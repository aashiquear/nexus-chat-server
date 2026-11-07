"""
Dynamic tool discovery.

Ranks the available tools (built-in catalog + connected MCP tools) against
the user's request so only a small, relevant subset is attached to the
model. A hybrid scorer is used:

* keyword / tag overlap (fast, deterministic, works offline), and
* optional embedding similarity using the same ONNX MiniLM embedder the
  RAG engine uses (lazily loaded, cached per tool description).

It also provides the ``discover_tools`` meta-tool the model can call when
the attached set lacks a capability: the orchestrator intercepts its
result and attaches the matched tools for the following rounds.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from dataclasses import dataclass, field

from . import BaseTool, register_tool

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#.'-]*")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are", "be", "it", "this", "that",
    "i", "me", "my", "we", "you", "your", "please", "can", "could", "would", "should", "do", "does", "how", "what",
    "from", "by", "as", "at", "if", "then", "than", "so", "not", "no", "yes", "into", "about", "some", "any",
    "give", "show", "tell", "make", "get", "use", "using", "need", "want", "like", "help", "let", "us",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall((text or "").lower()) if t not in _STOP and len(t) > 1]


@dataclass
class ToolDoc:
    id: str
    name: str
    summary: str
    description: str
    tags: list[str]
    category: str
    source: str  # builtin | mcp
    core: bool = False
    permission: str = "auto"
    risk: str = "low"
    tokens: set[str] = field(default_factory=set)
    tag_tokens: set[str] = field(default_factory=set)
    name_tokens: set[str] = field(default_factory=set)
    phrases: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.name_tokens = set(tokenize(self.name.replace("_", " ")))
        self.tag_tokens = set()
        for tag in self.tags:
            self.tag_tokens.update(tokenize(tag))
        self.phrases = [t.lower() for t in self.tags if " " in t]
        self.tokens = set(tokenize(f"{self.summary} {self.description[:600]}")) | self.tag_tokens | self.name_tokens

    def text(self) -> str:
        return f"{self.name.replace('_', ' ')}: {self.summary}. {', '.join(self.tags)}"


class _Embedder:
    """Lazy, shared sentence embedder (ChromaDB's ONNX MiniLM)."""

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._fn = None
        self._tried = False
        self._cache: dict[str, list[float]] = {}

    @classmethod
    def get(cls) -> "_Embedder":
        with cls._lock:
            if cls._instance is None:
                cls._instance = _Embedder()
            return cls._instance

    def _load(self):
        if self._tried:
            return self._fn
        self._tried = True
        try:
            from chromadb.utils import embedding_functions

            self._fn = embedding_functions.ONNXMiniLM_L6_V2()
        except Exception as e:  # chromadb optional
            logger.info("Tool-discovery embeddings unavailable (%s); using keyword ranking only", e)
            self._fn = None
        return self._fn

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        fn = self._load()
        if fn is None:
            return None
        missing = [t for t in texts if t not in self._cache]
        if missing:
            try:
                vecs = fn(missing)
            except Exception as e:
                logger.debug("embedding failed: %s", e)
                return None
            for t, v in zip(missing, vecs):
                self._cache[t] = list(v)
        return [self._cache[t] for t in texts]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class ToolIndex:
    """Ranks ToolDocs against a query."""

    def __init__(self, docs: list[ToolDoc], use_embeddings: bool = True):
        self.docs = docs
        self.by_id = {d.id: d for d in docs}
        self.use_embeddings = use_embeddings

    def keyword_scores(self, query: str) -> dict[str, float]:
        q_tokens = tokenize(query)
        q_set = set(q_tokens)
        q_lower = (query or "").lower()
        scores: dict[str, float] = {}
        for d in self.docs:
            s = 0.0
            s += 4.0 * len(q_set & d.name_tokens)
            s += 2.5 * len(q_set & d.tag_tokens)
            s += 0.8 * len(q_set & (d.tokens - d.tag_tokens - d.name_tokens))
            for ph in d.phrases:
                if ph in q_lower:
                    s += 4.0
            # prefix matches (plot → plotting, convert → conversion)
            for qt in q_set:
                if len(qt) >= 5 and any(t.startswith(qt[:5]) for t in d.tag_tokens | d.name_tokens):
                    s += 0.6
            scores[d.id] = s
        return scores

    def embedding_scores(self, query: str) -> dict[str, float] | None:
        if not self.use_embeddings or not query.strip():
            return None
        emb = _Embedder.get()
        texts = [d.text() for d in self.docs]
        vecs = emb.embed([query] + texts)
        if vecs is None:
            return None
        qv, dvs = vecs[0], vecs[1:]
        return {d.id: _cosine(qv, dv) for d, dv in zip(self.docs, dvs)}

    def rank(self, query: str, limit: int = 8, min_score: float = 0.0) -> list[tuple[ToolDoc, float]]:
        kw = self.keyword_scores(query)
        max_kw = max(kw.values()) if kw else 0.0
        emb = self.embedding_scores(query)
        results = []
        for d in self.docs:
            k = kw.get(d.id, 0.0) / max_kw if max_kw > 0 else 0.0
            e = emb.get(d.id, 0.0) if emb else 0.0
            # cosine similarity for unrelated texts sits around 0.0-0.15;
            # rescale 0.15..0.6 → 0..1
            e_n = max(0.0, min(1.0, (e - 0.15) / 0.45)) if emb else 0.0
            score = 0.6 * k + 0.4 * e_n if emb else k
            if k == 0 and (not emb or e_n < 0.35):
                score = 0.0
            if score > min_score:
                results.append((d, round(score, 4)))
        results.sort(key=lambda x: -x[1])
        return results[:limit]


def build_docs(builtin_meta: list[dict], mcp_tools: list[dict]) -> list[ToolDoc]:
    docs = []
    for m in builtin_meta:
        docs.append(ToolDoc(
            id=m["id"], name=m.get("name", m["id"]), summary=m.get("summary") or m.get("description", ""),
            description=m.get("description", ""), tags=list(m.get("tags", [])), category=m.get("category", "Other"),
            source="builtin", core=bool(m.get("core")), permission=m.get("permission", "auto"), risk=m.get("risk", "low"),
        ))
    for t in mcp_tools:
        desc = t.get("description", "") or ""
        docs.append(ToolDoc(
            id=t["id"], name=t.get("name", t["id"]), summary=desc[:200], description=desc,
            tags=tokenize(t.get("name", ""))[:6] + [t.get("server_name", "")], category=f"MCP · {t.get('server_name', t.get('server', ''))}",
            source="mcp", core=False, permission="auto", risk="medium",
        ))
    return docs


# ----------------------------------------------------------------------
# Meta tool
# ----------------------------------------------------------------------
@register_tool("discover_tools")
class DiscoverToolsTool(BaseTool):
    name = "discover_tools"
    description = (
        "Search the catalog of available tools and attach the ones you need. Call this "
        "when the task needs a capability that is not in your current tool list — e.g. "
        "unit conversion, symbolic math, SQL over an uploaded dataset, academic paper "
        "search, currency rates, date arithmetic, long-term memory, to-do lists, document "
        "search, decision matrices, change tracking. Matched tools become available on "
        "your next turn; then call them directly."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What you need to do, in a few words (e.g. 'convert 5 miles to km', 'solve quadratic equation', 'sql group by over sales.csv')."},
            "limit": {"type": "integer", "description": "Max tools to attach (default 4).", "default": 4},
        },
        "required": ["query"],
    }

    # The orchestrator injects a callable that performs the ranking over the
    # live tool set (built-in + MCP) and attaches the results.
    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.resolver = None

    async def execute(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        limit = max(1, min(int(kwargs.get("limit") or 4), 8))
        if self.resolver is None:
            return json.dumps({"error": "tool discovery is not available in this context"})
        matches = self.resolver(query, limit)
        if not matches:
            return json.dumps({"query": query, "attached": [], "message": "No matching tools. Answer without tools or use code_executor."})
        return json.dumps({
            "query": query,
            "attached": [{"tool": m["id"], "summary": m["summary"]} for m in matches],
            "message": "These tools are now available — call them directly in your next step.",
        })
