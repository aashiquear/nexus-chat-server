"""
Conversation persistence — stores chat threads as JSON files locally.

Each conversation is a JSON file in data/conversations/:
  {
    "id": "abc123",
    "title": "First user message (truncated)",
    "model": "claude-sonnet-4-20250514",
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:05:00Z",
    "messages": [...]
  }
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CONVERSATIONS_DIR = Path("./data/conversations")
CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

# Common English stopwords filtered out when summarising the first user
# prompt down to a 3-word sidebar title. Kept short on purpose — we want
# a snappy heuristic, not a full NLP pipeline.
_TITLE_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "could", "did",
    "do", "does", "for", "from", "have", "has", "had", "he", "her", "him",
    "his", "how", "i", "if", "in", "is", "it", "its", "just", "let", "me",
    "my", "of", "on", "or", "our", "please", "she", "should", "so", "some",
    "than", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "those", "to", "us", "was", "we", "were", "what", "when", "where",
    "which", "who", "why", "will", "with", "would", "you", "your",
}


def _path(conversation_id: str) -> Path:
    return CONVERSATIONS_DIR / f"{conversation_id}.json"


def _derive_title(messages: list[dict]) -> str:
    """Summarise the first user message into ~3 words for the sidebar.

    The heuristic strips punctuation, drops common stopwords, and keeps
    the first three remaining tokens. If filtering leaves fewer than
    three words (e.g. very short prompt or all stopwords) we fall back
    to the first three raw words so the title always has signal.
    """
    for msg in messages:
        if msg.get("role") != "user":
            continue
        text = (msg.get("content") or "").strip()
        if not text:
            continue
        first_line = text.split("\n", 1)[0]
        # Tokenise to alphanum-only words; keeps things like "GPT4" together.
        raw_words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", first_line)
        if not raw_words:
            continue
        meaningful = [w for w in raw_words if w.lower() not in _TITLE_STOPWORDS]
        chosen = meaningful[:3] if len(meaningful) >= 3 else raw_words[:3]
        if not chosen:
            continue
        # Title-case the result, but preserve mixed-case identifiers
        # (URLs, model names like "GPT5") that are already capitalised.
        formatted = [w if any(c.isupper() for c in w[1:]) else w.capitalize() for w in chosen]
        return " ".join(formatted)
    return "New conversation"


def list_conversations() -> list[dict]:
    """Return all conversations sorted by updated_at descending."""
    convos = []
    for f in CONVERSATIONS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            convos.append({
                "id": data["id"],
                "title": data.get("title", "Untitled"),
                "model": data.get("model", ""),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "message_count": len(data.get("messages", [])),
            })
        except Exception as e:
            logger.warning("Failed to read conversation %s: %s", f.name, e)
    convos.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
    return convos


def get_conversation(conversation_id: str) -> dict | None:
    """Load a full conversation by ID."""
    path = _path(conversation_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.error("Failed to load conversation %s: %s", conversation_id, e)
        return None


def save_conversation(
    conversation_id: str | None,
    messages: list[dict],
    model: str = "",
    token_usage: list[dict] | None = None,
    extra: dict | None = None,
) -> dict:
    """Create or update a conversation. Returns the saved metadata.

    ``extra`` carries optional per-conversation state such as the
    compaction summary, generation settings and tool/file selection so a
    reloaded thread resumes exactly where it left off."""
    now = datetime.now(timezone.utc).isoformat()

    if conversation_id:
        existing = get_conversation(conversation_id)
    else:
        existing = None

    if existing:
        existing["messages"] = messages
        existing["model"] = model or existing.get("model", "")
        existing["updated_at"] = now
        existing["title"] = _derive_title(messages)
        if token_usage is not None:
            existing["token_usage"] = token_usage
        for k, v in (extra or {}).items():
            existing[k] = v
        data = existing
    else:
        cid = conversation_id or uuid.uuid4().hex[:12]
        data = {
            "id": cid,
            "title": _derive_title(messages),
            "model": model,
            "created_at": now,
            "updated_at": now,
            "messages": messages,
        }
        if token_usage is not None:
            data["token_usage"] = token_usage
        for k, v in (extra or {}).items():
            data[k] = v

    path = _path(data["id"])
    path.write_text(json.dumps(data, indent=2))

    return {
        "id": data["id"],
        "title": data["title"],
        "model": data["model"],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
        "message_count": len(messages),
    }


def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation file. Returns True if deleted."""
    path = _path(conversation_id)
    if path.exists():
        path.unlink()
        return True
    return False
