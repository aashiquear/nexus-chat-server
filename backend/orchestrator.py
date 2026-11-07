"""
Chat Orchestrator - Core logic that ties LLM providers, tools, and RAG together.
Handles the agentic loop: LLM → tool call(s) → result(s) → LLM, plus
context-window management (compaction) and generation settings.
"""

import asyncio
import copy
import datetime as dt
import json
import logging
from typing import AsyncIterator

from backend.config import (
    get_config,
    get_enabled_mcp_servers,
    get_enabled_providers,
    get_enabled_tools,
)
from backend.mcp import MCPManager
from backend.providers import (
    GENERATION_KEYS,
    BaseLLMProvider,
    ChatRequest,
    Message,
    ToolDefinition,
    get_provider_class,
)
from backend.rag.engine import RAGEngine
from backend.tools import BaseTool, ToolContext, get_tool_class, reset_context, set_context
from backend.tools.catalog import CATALOG_BY_ID, CATEGORY_ORDER
from backend.tools.discovery import ToolIndex, build_docs, reset_resolver, set_resolver

logger = logging.getLogger(__name__)

# Rough token estimate used for compaction decisions when the provider
# cannot tell us ahead of time. 4 characters per token is a safe
# average for English + code across Llama/Qwen/Claude/GPT tokenizers.
CHARS_PER_TOKEN = 4
IMAGE_TOKEN_ESTIMATE = 1200
# Tool results fed back to the model are capped so a huge CSV dump or
# web page cannot blow the context window in a single round. The full
# result still reaches the UI.
MAX_TOOL_RESULT_CHARS = 16000

DEFAULT_GENERATION = {
    "local": {
        "num_ctx": 8192,
        "max_tokens": 4096,
        "temperature": 0.7,
        "top_p": 0.9,
        "top_k": 40,
        "repeat_penalty": 1.1,
        "keep_alive": "10m",
        "compaction": {"mode": "summarize", "threshold": 0.8, "keep_recent": 6},
    },
    "cloud": {
        "context_window": 200000,
        "max_tokens": 4096,
        "temperature": 0.7,
        "compaction": {"mode": "summarize", "threshold": 0.85, "keep_recent": 8},
    },
}

SUMMARIZE_PROMPT = (
    "You are compacting a long conversation so it fits in a limited context window. "
    "Write a dense, factual summary of the conversation below. Preserve: the user's "
    "goals and constraints, decisions made, important facts, numbers, file names, "
    "code identifiers, and any unresolved questions. Write in the third person, "
    "under 400 words, no preamble."
)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_message_tokens(msg: Message) -> int:
    total = estimate_tokens(msg.content or "") + 4
    if msg.images:
        total += IMAGE_TOKEN_ESTIMATE * len(msg.images)
    return total


class ChatOrchestrator:
    """Orchestrates chat between user, LLM, tools, and RAG."""

    def __init__(self):
        self.config = get_config()
        self._providers: dict[str, BaseLLMProvider] = {}
        self._tools: dict[str, BaseTool] = {}
        self._mcp: MCPManager = MCPManager()
        self._rag: RAGEngine | None = None
        self._init_providers()
        self._init_tools()
        self._init_rag()

    def _init_providers(self):
        for name, cfg in get_enabled_providers().items():
            cls = get_provider_class(name)
            if cls:
                provider = cls(cfg)
                self._providers[name] = provider
                logger.info(f"Provider '{name}' loaded")

    def _init_tools(self):
        """Register enabled tools *lazily*: only the config is kept here;
        the module is imported and the class instantiated on first use so
        startup stays fast however many tools are enabled."""
        self._tool_configs: dict[str, dict] = {}
        for name, cfg in get_enabled_tools().items():
            if name in CATALOG_BY_ID or get_tool_class(name) is not None:
                self._tool_configs[name] = cfg.get("config", {}) or {}
            else:
                logger.warning("Tool '%s' is enabled in settings.yaml but no implementation was found", name)
        logger.info("%d tool(s) enabled (loaded on demand)", len(self._tool_configs))

    def get_tool(self, name: str) -> BaseTool | None:
        """Instantiate (once) and return a built-in tool."""
        tool = self._tools.get(name)
        if tool is not None:
            return tool
        if name not in self._tool_configs:
            return None
        cls = get_tool_class(name)
        if cls is None:
            return None
        tool = cls(self._tool_configs[name])
        self._tools[name] = tool
        logger.info("Tool '%s' loaded", name)
        return tool

    @property
    def enabled_tool_ids(self) -> list[str]:
        return list(self._tool_configs)

    def tool_selection_config(self) -> dict:
        cfg = dict(self.config.get("tool_selection") or {})
        cfg.setdefault("mode", "auto")
        cfg.setdefault("max_tools", 8)
        cfg.setdefault("min_score", 0.25)
        cfg.setdefault("include_core", True)
        return cfg

    def builtin_tool_meta(self, name: str) -> dict:
        tool_cfg = get_enabled_tools().get(name, {})
        meta = CATALOG_BY_ID.get(name, {})
        tool = self._tools.get(name)
        description = tool_cfg.get("description") or (tool.description if tool else meta.get("summary", ""))
        return {
            "id": name,
            "name": tool_cfg.get("name", name.replace("_", " ").title()),
            "description": description,
            "summary": meta.get("summary", description),
            "icon": tool_cfg.get("icon", "wrench"),
            "source": "builtin",
            "category": meta.get("category", "Other"),
            "tags": meta.get("tags", []),
            "risk": meta.get("risk", "low"),
            "permission": meta.get("permission", "auto"),
            "offline": meta.get("offline", True),
            "core": bool(meta.get("core")),
            "hidden": bool(meta.get("hidden")),
            "loaded": name in self._tools,
        }

    def _tool_index(self, visible_mcp: set[str] | None = None) -> ToolIndex:
        builtin = [self.builtin_tool_meta(n) for n in self._tool_configs if not CATALOG_BY_ID.get(n, {}).get("hidden")]
        mcp = [t for t in self._mcp.get_tool_info(visible_mcp)]
        return ToolIndex(build_docs(builtin, mcp))

    def discover(self, query: str, limit: int = 8, visible_mcp: set[str] | None = None) -> list[dict]:
        cfg = self.tool_selection_config()
        idx = self._tool_index(visible_mcp)
        out = []
        for doc, score in idx.rank(query, limit=limit, min_score=float(cfg["min_score"])):
            if doc.permission != "auto":
                continue
            out.append({"id": doc.id, "name": doc.name, "summary": doc.summary, "category": doc.category, "score": score, "source": doc.source})
        return out

    def _init_rag(self):
        rag_config = self.config.get("rag", {})
        if rag_config.get("enabled", False):
            self._rag = RAGEngine(rag_config)
            logger.info("RAG engine initialized")

    # ------------------------------------------------------------------
    # Generation settings
    # ------------------------------------------------------------------
    def generation_defaults(self) -> dict:
        """Server-side defaults for the prompt-box settings control.

        ``settings.yaml`` → ``generation.defaults.{local,cloud}`` overrides
        the built-in :data:`DEFAULT_GENERATION`. The UI lets the user
        change these per browser; the server edition persists them per
        user."""
        cfg = copy.deepcopy(DEFAULT_GENERATION)
        user_cfg = (self.config.get("generation") or {}).get("defaults") or {}
        for kind in ("local", "cloud"):
            override = user_cfg.get(kind) or {}
            for k, v in override.items():
                if k == "compaction" and isinstance(v, dict):
                    cfg[kind]["compaction"].update(v)
                else:
                    cfg[kind][k] = v
        return {
            "defaults": cfg,
            "context_sizes": (self.config.get("generation") or {}).get(
                "context_sizes", [2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144]
            ),
        }

    def _resolve_generation(self, provider: BaseLLMProvider, model_cfg: dict, user_gen: dict | None) -> dict:
        """Merge built-in defaults ← model config ← user settings."""
        kind = getattr(provider, "kind", "cloud")
        gen = dict(self.generation_defaults()["defaults"].get(kind, {}))
        comp = dict(gen.pop("compaction", {}) or {})
        if model_cfg.get("max_tokens"):
            gen["max_tokens"] = model_cfg["max_tokens"]
        if model_cfg.get("num_ctx"):
            gen["num_ctx"] = model_cfg["num_ctx"]
        if model_cfg.get("context_length"):
            gen["context_window"] = model_cfg["context_length"]
        if user_gen:
            for k, v in user_gen.items():
                if k == "compaction" and isinstance(v, dict):
                    comp.update({kk: vv for kk, vv in v.items() if vv is not None})
                elif v is not None and v != "":
                    gen[k] = v
        gen["compaction"] = comp
        return gen

    # ------------------------------------------------------------------
    # Model catalog
    # ------------------------------------------------------------------
    def get_available_models(self) -> list[dict]:
        """Return all configured models across providers (sync, no probe)."""
        models = []
        for provider_name, provider in self._providers.items():
            for model in provider.list_models():
                models.append(self._format_model_entry(provider_name, provider, model, None))
        return models

    async def get_available_models_async(self) -> list[dict]:
        """Same as :meth:`get_available_models` but additionally probes
        each configured provider for its current remote model catalog
        and marks each entry with ``remote_available`` plus capability
        metadata (vision / tools / thinking / context length /
        quantization) when the backend can report it."""
        active = list(self._providers.items())
        remote_lists = await asyncio.gather(
            *[p.list_remote_models() for _, p in active],
            return_exceptions=True,
        )

        result = []
        cap_jobs = []
        for (provider_name, provider), remote in zip(active, remote_lists):
            if isinstance(remote, Exception):
                remote = None
            elif not (remote is None or isinstance(remote, set)):
                remote = None
            for model in provider.list_models():
                entry = self._format_model_entry(provider_name, provider, model, remote)
                result.append(entry)
                # Only probe models that are actually present remotely
                # (probing missing Ollama tags is a wasted round-trip).
                if entry["available"] and entry["remote_available"] in (True, None):
                    cap_jobs.append((entry, provider))

        sem = asyncio.Semaphore(8)

        async def _probe(entry, provider):
            async with sem:
                try:
                    caps = await provider.model_capabilities(entry["id"])
                except Exception:
                    caps = None
            if caps:
                entry["capabilities"] = {
                    "vision": bool(caps.get("vision")),
                    "tools": bool(caps.get("tools")),
                    "thinking": bool(caps.get("thinking")) or entry["thinking"],
                }
                if caps.get("context_length"):
                    entry["context_length"] = caps["context_length"]
                if caps.get("quantization"):
                    entry["quantization"] = caps["quantization"]
                if caps.get("parameter_size"):
                    entry["parameter_size"] = caps["parameter_size"]
                if caps.get("family"):
                    entry["family"] = caps["family"]

        await asyncio.gather(*[_probe(e, p) for e, p in cap_jobs], return_exceptions=True)
        return result

    @staticmethod
    def _format_model_entry(provider_name, provider, model, remote_models):
        """Build the dict shape returned by /api/models for one model."""
        mid = model["id"]
        entry = {
            "id": mid,
            "name": model.get("name", mid),
            "provider": provider_name,
            "kind": getattr(provider, "kind", "cloud"),
            "available": provider.is_available(),
            "thinking": bool(
                model.get("thinking")
                or model.get("thinking_level")
                or model.get("thinking_budget_tokens")
            ),
            "max_tokens": model.get("max_tokens"),
            "discovered": bool(model.get("discovered", False)),
        }
        for key in ("quantization", "family", "parameter_size", "context_length"):
            if model.get(key):
                entry[key] = model[key]
        if model.get("vision"):
            entry["capabilities"] = {"vision": True, "tools": True, "thinking": entry["thinking"]}
        if remote_models is None:
            entry["remote_available"] = None
        else:
            entry["remote_available"] = mid in remote_models or (
                ":" not in mid and f"{mid}:latest" in remote_models
            )
        return entry

    async def init_mcp(self):
        """Initialize MCP server connections (must be awaited)."""
        mcp_config = get_enabled_mcp_servers()
        await self._mcp.init_from_config(mcp_config)
        logger.info("MCP manager initialized")

    def get_available_tools(self, visible_mcp: set[str] | None = None) -> list[dict]:
        """Return all available tools (built-in + MCP) with catalog metadata
        (category, tags, risk, core) used by the sidebar and the discovery
        router. Built-in modules are *not* imported here."""
        result = []
        order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
        for name in self._tool_configs:
            meta = self.builtin_tool_meta(name)
            if meta["hidden"]:
                continue
            result.append(meta)
        result.sort(key=lambda m: (order.get(m["category"], 99), m["name"]))
        for t in self._mcp.get_tool_info(visible_mcp):
            result.append({**t, "category": f"MCP · {t.get('server_name', t.get('server'))}", "risk": "medium", "permission": "auto", "core": False})
        return result

    def get_mcp_servers(self) -> list[dict]:
        """Return status of all MCP servers."""
        return self._mcp.get_servers_info()

    async def reconnect_mcp(self, server_id: str) -> bool:
        """Reconnect a specific MCP server."""
        return await self._mcp.reconnect(server_id)

    async def add_mcp_server(self, server_id: str, cfg: dict) -> dict | None:
        """Add and connect a new MCP server at runtime."""
        return await self._mcp.add_server(server_id, cfg)

    async def remove_mcp_server(self, server_id: str) -> bool:
        """Remove an MCP server at runtime."""
        return await self._mcp.remove_server(server_id)

    @property
    def mcp(self) -> MCPManager:
        return self._mcp

    def _resolve_provider(self, model_id: str) -> tuple[str, BaseLLMProvider, dict] | None:
        """Find which provider hosts the given model."""
        for name, provider in self._providers.items():
            for model in provider.list_models():
                if model["id"] == model_id:
                    return name, provider, model
        # Ollama tags without ``:latest`` and vice versa.
        for name, provider in self._providers.items():
            for model in provider.list_models():
                if model["id"].split(":")[0] == model_id.split(":")[0] and getattr(provider, "kind", "") == "local":
                    return name, provider, {**model, "id": model_id}
        return None

    @staticmethod
    def _resolve_thinking_config(model: dict) -> dict | None:
        """Look up per-model thinking config so non-thinking models stay
        untouched while thinking-capable models get their required params.

        A model entry in settings.yaml may declare any of:
          thinking: true | false        — toggle thinking
          thinking_level: low|medium|high
          thinking_budget_tokens: N     — Anthropic extended-thinking budget
        """
        enabled = model.get("thinking")
        level = model.get("thinking_level")
        budget = model.get("thinking_budget_tokens")
        if not enabled and not level and not budget:
            return None
        cfg: dict = {"enabled": bool(enabled) or bool(level) or bool(budget)}
        if level:
            cfg["level"] = level
        if budget:
            cfg["budget_tokens"] = int(budget)
        return cfg

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------
    async def _execute_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool (built-in or MCP) and return its result."""
        if tool_name.startswith("mcp:"):
            parts = tool_name.split(":", 2)
            if len(parts) == 3:
                _, server_id, remote_name = parts
                try:
                    return await self._mcp.call_tool(server_id, remote_name, arguments)
                except Exception as e:
                    return json.dumps({"error": str(e)})

        tool = self.get_tool(tool_name)
        if not tool:
            return json.dumps({"error": f"Tool '{tool_name}' not found"})
        try:
            return await tool.execute(**arguments)
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _stream_tool(
        self, tool_name: str, arguments: dict
    ) -> AsyncIterator[dict]:
        """Execute a tool with optional live streaming support.

        Yields {"type": "tool_stream", "name": ..., "chunk": ...} for each
        line of live output, then finally {"type": "tool_result", ...}.
        """
        if tool_name.startswith("mcp:"):
            result = await self._execute_tool(tool_name, arguments)
            yield {"type": "tool_result", "name": tool_name, "result": result}
            return

        tool = self.get_tool(tool_name)
        if not tool:
            yield {
                "type": "tool_result",
                "name": tool_name,
                "result": json.dumps({"error": f"Tool '{tool_name}' not found"}),
            }
            return

        try:
            chunks: list[str] = []
            async for chunk in tool.stream_execute(**arguments):
                chunks.append(chunk)
                yield {"type": "tool_stream", "name": tool_name, "chunk": chunk}

            if hasattr(tool, "_last_output"):
                # code_executor stashes extra state on the instance —
                # surface it (return code, optional interactive session
                # id, produced artifacts) alongside the streamed output so
                # the frontend can auto-open the right canvas view.
                result_dict = {
                    "output": "\n".join(chunks).strip() or "(no output)",
                    "return_code": getattr(tool, "_last_return_code", 0),
                    "language": getattr(tool, "_last_language", "python"),
                }
                session_id = getattr(tool, "_last_session_id", None)
                if session_id:
                    result_dict["interactive_session"] = session_id
                artifacts = getattr(tool, "_last_artifacts", None)
                if artifacts:
                    result_dict["artifacts"] = artifacts
                preview = getattr(tool, "_last_preview", None)
                if preview:
                    result_dict["downloadable"] = preview
                result = json.dumps(result_dict)
            elif len(chunks) == 1:
                # Single-chunk tools yield their entire result as one
                # chunk — pass it through verbatim so structured JSON
                # reaches the frontend intact.
                result = chunks[0]
            else:
                result = json.dumps({"result": "\n".join(chunks)})

            yield {"type": "tool_result", "name": tool_name, "result": result}
        except Exception as e:
            yield {
                "type": "tool_result",
                "name": tool_name,
                "result": json.dumps({"error": str(e)}),
            }

    async def _get_rag_context(self, query: str, files: list[str] | None, where: dict | None = None) -> str:
        """Retrieve relevant context from RAG if enabled."""
        if not self._rag or not files:
            return ""
        results = await self._rag.query(query, filenames=files, where=where)
        if not results:
            return ""
        context_parts = []
        for r in results:
            source = r["metadata"].get("source", "unknown")
            context_parts.append(f"[From {source}]:\n{r['content']}")
        return "\n\n---\n\n".join(context_parts)

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------
    def _default_system_prompt(self, selected_tools: list[str] | None, model_entry: dict) -> str:
        today = dt.datetime.now().strftime("%A, %B %d, %Y")
        app_cfg = self.config.get("app", {})
        base = app_cfg.get("system_prompt") or "You are Nexus, a helpful, precise AI assistant."
        parts = [base, f"Today's date is {today}."]
        parts.append(
            "Your training data has a cutoff date, so anything that may have changed "
            "since then (news, prices, releases, versions, schedules, weather, sports, "
            "'latest'/'current' questions, or any date after your training) must be "
            "checked with tools rather than answered from memory."
        )
        tools = set(selected_tools or [])
        if "web_search" in tools or "web_browser" in tools:
            hint = []
            if "web_search" in tools:
                hint.append("call `web_search` to find current information")
            if "web_browser" in tools:
                hint.append("call `web_browser` to open a URL and read the page")
            parts.append(
                "When information could post-date your training or the user asks about "
                "something recent, " + " and ".join(hint) + " before answering, then cite the sources."
            )
        if "code_executor" in tools:
            parts.append(
                "Use `code_executor` to run code when a computation, data analysis, file "
                "conversion, or verification is needed. It supports python, javascript, "
                "typescript, bash, java, c, cpp, go, rust and html; missing packages can "
                "be installed on the fly via the `packages` argument. Files the program "
                "writes to its working directory are returned to the user as downloads."
            )
        if "csv_viewer" in tools:
            parts.append("Use `csv_viewer` to inspect uploaded CSV/TSV/Excel files before analysing or plotting them.")
        if "discover_tools" in tools:
            parts.append(
                "Only a few tools relevant to this request are attached. If the task needs a capability you "
                "do not see (unit conversion, symbolic math, SQL over uploaded data, academic papers, currency, "
                "date arithmetic, memory, to-do list, document search, decision matrix, change tracking…), call "
                "`discover_tools` with a short description and the matching tools will be attached for your next step."
            )
        if "memory" in tools:
            parts.append(
                "You have a `memory` tool. Save durable facts the user shares about themselves or their work "
                "(preferences, projects, decisions) and search it when a question depends on earlier context."
            )
        parts.append(
            "Format answers in Markdown. Put code in fenced blocks with a language tag."
        )
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Context compaction
    # ------------------------------------------------------------------
    def _context_budget(self, provider: BaseLLMProvider, gen: dict, model_entry: dict, caps: dict | None) -> int:
        """Token budget used for compaction decisions: the configured
        window (num_ctx locally, context_window for cloud), never above
        the limit the model actually reports."""
        model_limit = int(caps["context_length"]) if caps and caps.get("context_length") else 0
        if getattr(provider, "kind", "cloud") == "local":
            budget = int(gen.get("num_ctx") or 0) or model_limit or 8192
        else:
            budget = int(gen.get("context_window") or 0) or model_limit or 200000
        if model_limit:
            budget = min(budget, model_limit)
        return budget

    async def _compact(
        self,
        provider: BaseLLMProvider,
        request_template: ChatRequest,
        msgs: list[Message],
        gen: dict,
        budget: int,
        system_tokens: int,
        state: dict | None,
    ) -> tuple[list[Message], dict | None, dict | None]:
        """Shrink ``msgs`` so the prompt fits within the context budget.

        Returns ``(messages, new_state, event)`` where ``event`` is a
        ``compaction`` event for the UI (or None when nothing happened).
        ``state`` is ``{"summary": str, "covers": int}`` carried between
        turns by the frontend so earlier summaries are reused instead of
        re-summarising the same history every turn.
        """
        comp = gen.get("compaction") or {}
        mode = (comp.get("mode") or "off").lower()
        threshold = float(comp.get("threshold") or 0.8)
        keep_recent = max(2, int(comp.get("keep_recent") or 6))
        max_tokens = int(gen.get("max_tokens") or 4096)

        # Apply a prior summary first (cheap, no model call).
        working = list(msgs)
        summary_text = None
        if state and state.get("summary") and isinstance(state.get("covers"), int):
            covers = min(state["covers"], max(0, len(working) - 1))
            if covers > 0:
                summary_text = state["summary"]
                working = working[covers:]
        else:
            covers = 0

        def prompt_tokens(ms: list[Message], summary: str | None) -> int:
            total = system_tokens + sum(estimate_message_tokens(m) for m in ms)
            if summary:
                total += estimate_tokens(summary) + 40
            return total

        limit = int(budget * threshold) - max_tokens
        used = prompt_tokens(working, summary_text)
        event = None
        new_state = state if summary_text else None

        if mode != "off" and used > limit and len(working) > keep_recent:
            # Everything except the most recent ``keep_recent`` messages
            # gets folded away. Keep the boundary on a user turn so the
            # retained tail starts coherently.
            cut = len(working) - keep_recent
            while cut > 0 and working[cut].role != "user":
                cut -= 1
            if cut <= 0:
                cut = len(working) - keep_recent
            old, working = working[:cut], working[cut:]
            removed_tokens = sum(estimate_message_tokens(m) for m in old)

            if mode == "summarize":
                transcript = []
                if summary_text:
                    transcript.append(f"[Earlier summary]\n{summary_text}")
                for m in old:
                    label = "User" if m.role == "user" else "Assistant"
                    text = m.content or ""
                    if len(text) > 6000:
                        text = text[:6000] + " …[truncated]"
                    transcript.append(f"{label}: {text}")
                req = ChatRequest(
                    messages=[Message(role="user", content="\n\n".join(transcript))],
                    model=request_template.model,
                    system_prompt=SUMMARIZE_PROMPT,
                    max_tokens=min(1200, max_tokens),
                    temperature=0.2,
                    generation={
                        k: v for k, v in gen.items()
                        if k in ("num_ctx", "keep_alive") and v not in (None, "")
                    } | {"thinking": False, "max_tokens": min(1200, max_tokens)},
                )
                summary = await provider.chat_sync(req)
                if not summary or summary.startswith("Error"):
                    # Fall back to truncation if the summariser failed.
                    mode = "truncate"
                    summary_text = summary_text
                else:
                    summary_text = summary.strip()
            if mode == "truncate" and not summary_text:
                summary_text = None

            new_state = {"summary": summary_text, "covers": covers + len(old)} if summary_text else {
                "summary": "", "covers": covers + len(old)
            }
            event = {
                "type": "compaction",
                "mode": mode,
                "removed_messages": len(old),
                "removed_tokens": removed_tokens,
                "kept_messages": len(working),
                "budget": budget,
                "state": new_state,
            }

        if summary_text:
            working = [
                Message(
                    role="user",
                    content=(
                        "[Context summary of the earlier part of this conversation — "
                        "treat as prior knowledge, do not mention this note]\n" + summary_text
                    ),
                ),
                Message(role="assistant", content="Understood. I have the context and will continue from here."),
            ] + working
        return working, new_state, event

    # ------------------------------------------------------------------
    # Main chat loop
    # ------------------------------------------------------------------
    async def chat_stream(
        self,
        messages: list[dict],
        model_id: str,
        selected_tools: list[str] | None = None,
        selected_files: list[str] | None = None,
        system_prompt: str = "",
        generation: dict | None = None,
        compaction_state: dict | None = None,
        tool_context: ToolContext | None = None,
        rag_where: dict | None = None,
        tool_mode: str | None = None,
        visible_mcp: set[str] | None = None,
    ) -> AsyncIterator[dict]:
        """
        Stream a chat response, handling tool calls in an agentic loop.

        ``tool_mode`` is ``"auto"`` (default from settings) or ``"manual"``.
        In auto mode the discovery router attaches core tools plus the
        tools most relevant to the latest user message; ``selected_tools``
        act as user pins that are always attached. In manual mode only
        ``selected_tools`` are attached (legacy behaviour).

        Yields dicts with:
          {"type": "status", "stage": "initiated|compacting|..."}
          {"type": "text", "content": "..."}
          {"type": "notice", "content": "..."}
          {"type": "compaction", ...}
          {"type": "tool_call", "name": "...", "arguments": {...}}
          {"type": "tool_stream", "name": "...", "chunk": "..."}
          {"type": "tool_result", "name": "...", "result": "..."}
          {"type": "done", "usage": {...}, "generation": {...}}
          {"type": "error", "content": "..."}
        """
        yield {"type": "status", "stage": "initiated"}

        resolved = self._resolve_provider(model_id)
        if not resolved:
            yield {"type": "error", "content": f"Model '{model_id}' not found"}
            return

        provider_name, provider, model_cfg = resolved
        if not provider.is_available():
            yield {"type": "error", "content": f"Provider '{provider_name}' is not configured. Check your API key."}
            return

        ctx_token = set_context(tool_context) if tool_context is not None else None
        try:
            async for event in self._chat_stream_inner(
                messages, model_id, provider, model_cfg, selected_tools,
                selected_files, system_prompt, generation, compaction_state, rag_where,
                tool_mode, visible_mcp,
            ):
                yield event
        finally:
            if ctx_token is not None:
                reset_context(ctx_token)

    def _tool_definition(self, tool_id: str, visible_mcp: set[str] | None = None) -> ToolDefinition | None:
        if tool_id.startswith("mcp:"):
            parts = tool_id.split(":", 2)
            if len(parts) == 3:
                for mcp_tool in self._mcp.get_all_tools(visible_mcp):
                    if mcp_tool.get("_mcp_server") == parts[1] and mcp_tool["name"] == parts[2]:
                        return ToolDefinition(
                            name=tool_id,
                            description=mcp_tool.get("description", ""),
                            parameters=mcp_tool.get("parameters") or mcp_tool.get("inputSchema") or {"type": "object", "properties": {}},
                        )
            return None
        tool = self.get_tool(tool_id)
        if tool:
            return ToolDefinition(**tool.to_definition())
        return None

    def select_tools(self, messages: list[dict], pinned: list[str] | None, tool_mode: str | None,
                     visible_mcp: set[str] | None = None) -> tuple[list[str], dict]:
        """Decide which tool ids to attach for this turn.

        Returns ``(tool_ids, info)`` where ``info`` describes the choice for
        the UI (``mode``, ``core``, ``matched`` with scores, ``pinned``)."""
        cfg = self.tool_selection_config()
        mode = (tool_mode or cfg["mode"] or "auto").lower()
        pinned = [t for t in (pinned or []) if t in self._tool_configs or t.startswith("mcp:")]
        if mode != "auto":
            return pinned, {"mode": "manual", "pinned": pinned, "core": [], "matched": []}

        # Query = latest user message plus a little of the previous turn for context.
        user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str)]
        query = user_msgs[-1] if user_msgs else ""
        if len(user_msgs) > 1 and len(query) < 80:
            query = user_msgs[-2][-300:] + " " + query
        has_images = any(m.get("images") for m in messages[-1:])
        has_files = bool(self._rag)  # files selected are handled by RAG; csv tools matched by query

        core = [n for n in self._tool_configs if CATALOG_BY_ID.get(n, {}).get("core")] if cfg.get("include_core", True) else []
        matched = self.discover(query, limit=int(cfg["max_tools"]), visible_mcp=visible_mcp) if query.strip() else []
        selected: list[str] = []
        for tid in pinned + core + [m["id"] for m in matched]:
            if tid not in selected:
                selected.append(tid)
        # Hard cap keeps the schema payload small for local models.
        cap = int(cfg["max_tools"]) + len(core) + len(pinned)
        selected = selected[:cap]
        if has_images and "image_synthesizer" in self._tool_configs and "image_synthesizer" not in selected:
            pass  # vision models see images directly; the fallback tool stays discoverable
        return selected, {"mode": "auto", "pinned": pinned, "core": core, "matched": matched, "query": query[:200]}

    async def _chat_stream_inner(
        self, messages, model_id, provider, model_cfg, selected_tools,
        selected_files, system_prompt, generation, compaction_state, rag_where,
        tool_mode=None, visible_mcp=None,
    ) -> AsyncIterator[dict]:
        # Decide which tools to attach (auto discovery or manual selection)
        tool_ids, selection_info = self.select_tools(messages, selected_tools, tool_mode, visible_mcp)
        yield {"type": "tools_selected", **selection_info, "tools": tool_ids}

        tool_defs: list[ToolDefinition] = []
        attached: set[str] = set()
        for tool_id in tool_ids:
            td = self._tool_definition(tool_id, visible_mcp)
            if td:
                tool_defs.append(td)
                attached.add(tool_id)

        # Wire the discover_tools meta tool to this request so the model can
        # pull in more tools mid-conversation.
        newly_attached: list[dict] = []
        resolver_token = None
        if "discover_tools" in attached:
            def _resolve(query: str, limit: int) -> list[dict]:
                found = [m for m in self.discover(query, limit=limit, visible_mcp=visible_mcp) if m["id"] not in attached]
                for m in found:
                    td = self._tool_definition(m["id"], visible_mcp)
                    if td:
                        tool_defs.append(td)
                        attached.add(m["id"])
                        newly_attached.append(m)
                return found
            # Request-local: concurrent streams each get their own resolver.
            resolver_token = set_resolver(_resolve)
        selected_tools = list(attached)
        try:
            async for event in self._run_agent_loop(
                messages, model_id, provider, model_cfg, selected_tools, selected_files, system_prompt,
                generation, compaction_state, rag_where, tool_defs, attached, newly_attached, model_entry=None,
            ):
                yield event
        finally:
            if resolver_token is not None:
                reset_resolver(resolver_token)

    async def _run_agent_loop(
        self, messages, model_id, provider, model_cfg, selected_tools, selected_files, system_prompt,
        generation, compaction_state, rag_where, tool_defs, attached, newly_attached, model_entry=None,
    ) -> AsyncIterator[dict]:

        # Get RAG context
        last_user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "") or ""
                break

        rag_context = await self._get_rag_context(last_user_msg, selected_files, where=rag_where)

        model_entry = {"id": model_id, **model_cfg}
        full_system = system_prompt or self._default_system_prompt(selected_tools, model_entry)
        if rag_context:
            full_system += (
                "\n\n--- Relevant Context from Uploaded Files ---\n"
                + rag_context
                + "\n--- End Context ---\n"
                "Use the above context to help answer the user's question."
            )

        # Convert messages (images ride along for vision models)
        msg_objs = []
        for m in messages:
            role = m.get("role", "user")
            if role not in ("user", "assistant", "system"):
                continue
            content = m.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content)
            images = m.get("images") or None
            if images and not isinstance(images, list):
                images = None
            if not content and not images:
                continue
            msg_objs.append(Message(role=role, content=content, images=images))

        # Generation settings & context budget
        gen = self._resolve_generation(provider, model_cfg, generation)
        try:
            caps = await provider.model_capabilities(model_id)
        except Exception:
            caps = None
        budget = self._context_budget(provider, gen, model_entry, caps)

        thinking_cfg = self._resolve_thinking_config(model_cfg)
        request = ChatRequest(
            messages=msg_objs,
            model=model_id,
            tools=tool_defs,
            system_prompt=full_system,
            stream=True,
            thinking=thinking_cfg,
            max_tokens=int(gen.get("max_tokens") or model_cfg.get("max_tokens") or 4096),
            temperature=float(gen.get("temperature", 0.7)),
            generation={k: gen[k] for k in GENERATION_KEYS if k in gen and gen[k] not in (None, "")},
        )

        # Compaction
        system_tokens = estimate_tokens(full_system) + sum(
            estimate_tokens(json.dumps(td.parameters)) + estimate_tokens(td.description) + 20
            for td in tool_defs
        )
        comp_mode = (gen.get("compaction") or {}).get("mode", "off")
        if comp_mode != "off" or compaction_state:
            yield {"type": "status", "stage": "compacting"}
            try:
                msg_objs, new_state, comp_event = await self._compact(
                    provider, request, msg_objs, gen, budget, system_tokens, compaction_state
                )
                request.messages = msg_objs
                if comp_event:
                    yield comp_event
            except Exception as e:
                logger.warning("Compaction failed: %s", e)
                yield {"type": "notice", "content": f"Context compaction skipped: {e}"}

        prompt_estimate = system_tokens + sum(estimate_message_tokens(m) for m in msg_objs)
        yield {
            "type": "context",
            "budget": budget,
            "estimated_prompt_tokens": prompt_estimate,
            "generation": {k: v for k, v in gen.items() if k != "compaction"},
        }

        # Agentic loop — up to 15 rounds on success, stops after 5 consecutive failures
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        max_rounds = 15
        max_consecutive_failures = 5
        consecutive_failures = 0

        for _round in range(max_rounds):
            collected_text = ""
            tool_calls: list[dict] = []
            stream_error: str | None = None

            async for chunk in provider.chat(request):
                if chunk.notice:
                    yield {"type": "notice", "content": chunk.notice}
                if chunk.error:
                    stream_error = chunk.error
                if chunk.content:
                    collected_text += chunk.content
                    yield {"type": "text", "content": chunk.content}

                if chunk.tool_call:
                    tc = chunk.tool_call
                    if tc.get("name"):
                        tool_calls.append({
                            "id": tc.get("id"),
                            "name": tc["name"],
                            "arguments": tc.get("arguments") or "",
                        })
                    elif tool_calls:
                        if "arguments" in tc and tc["arguments"]:
                            tool_calls[-1]["arguments"] += tc["arguments"]
                        if "partial" in tc and tc["partial"]:
                            tool_calls[-1]["arguments"] += tc["partial"]

                if chunk.done:
                    if chunk.usage:
                        total_usage["prompt_tokens"] += chunk.usage.get("prompt_tokens", 0)
                        total_usage["completion_tokens"] += chunk.usage.get("completion_tokens", 0)
                        total_usage["total_tokens"] += chunk.usage.get("total_tokens", 0)
                    break

            if stream_error:
                yield {"type": "error", "content": stream_error}
                return

            if not tool_calls:
                done_event = {"type": "done"}
                if total_usage["total_tokens"] > 0:
                    done_event["usage"] = total_usage
                yield done_event
                return

            # Execute every tool call from this round, in order.
            round_results = []
            for call in tool_calls:
                raw_args = call["arguments"]
                try:
                    args = json.loads(raw_args) if raw_args else {}
                    if not isinstance(args, dict):
                        args = {"input": args}
                except json.JSONDecodeError:
                    args = {}
                    # Some models double-encode or trail garbage; try to
                    # salvage the first JSON object.
                    start, end = raw_args.find("{"), raw_args.rfind("}")
                    if start >= 0 and end > start:
                        try:
                            args = json.loads(raw_args[start:end + 1])
                        except json.JSONDecodeError:
                            args = {}

                yield {"type": "tool_call", "name": call["name"], "arguments": args}

                result = json.dumps({"error": "tool produced no result"})
                async for event in self._stream_tool(call["name"], args):
                    yield event
                    if event["type"] == "tool_result":
                        result = event["result"]

                try:
                    parsed_result = json.loads(result)
                    failed = isinstance(parsed_result, dict) and "error" in parsed_result
                except (json.JSONDecodeError, TypeError):
                    failed = False
                consecutive_failures = consecutive_failures + 1 if failed else 0
                round_results.append((call, result))

            if consecutive_failures >= max_consecutive_failures:
                yield {"type": "text", "content": "\n\n*Stopping: too many consecutive tool failures.*"}
                break

            if newly_attached:
                yield {"type": "tools_selected", "mode": "discover", "tools": list(attached),
                       "matched": list(newly_attached), "core": [], "pinned": []}
                newly_attached.clear()
                request.tools = tool_defs

            # Append assistant + tool results to messages for the next round.
            names = ", ".join(c["name"] for c, _ in round_results)
            msg_objs.append(Message(role="assistant", content=collected_text or f"Using tool(s): {names}"))
            feedback = []
            for call, result in round_results:
                text = result if isinstance(result, str) else json.dumps(result)
                if len(text) > MAX_TOOL_RESULT_CHARS:
                    text = text[:MAX_TOOL_RESULT_CHARS] + f"\n…[truncated {len(text) - MAX_TOOL_RESULT_CHARS} characters]"
                feedback.append(f"Tool '{call['name']}' returned:\n{text}")
            msg_objs.append(Message(role="user", content="\n\n".join(feedback)))
            request.messages = msg_objs

        done_event = {"type": "done"}
        if total_usage["total_tokens"] > 0:
            done_event["usage"] = total_usage
        yield done_event

    @property
    def rag_engine(self) -> RAGEngine | None:
        return self._rag
