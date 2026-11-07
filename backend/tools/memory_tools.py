"""Personal memory and tasks — persistent across conversations (per user in the server edition)."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from . import BaseTool, register_tool
from .discovery import _Embedder, _cosine, tokenize


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def _save(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1, ensure_ascii=False))
    tmp.replace(path)


@register_tool("memory")
class MemoryTool(BaseTool):
    name = "memory"
    description = (
        "Long-term memory that persists across conversations. Use operation=save to store durable facts the user tells "
        "you about themselves, their preferences, projects, decisions or instructions ('remember that…'); "
        "operation=search to recall relevant memories before answering questions about the user or earlier work; "
        "operation=list / delete / update to manage them. Never store secrets (passwords, keys). Keep each memory a "
        "single concise sentence."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["save", "search", "list", "delete", "update"]},
            "content": {"type": "string", "description": "Memory text (save/update) or search query (search)."},
            "category": {"type": "string", "description": "preference | fact | project | decision | instruction | other"},
            "id": {"type": "string", "description": "Memory id (delete/update)."},
            "limit": {"type": "integer", "default": 8},
        },
        "required": ["operation"],
    }

    def _path(self) -> Path:
        return Path(self.ctx.state_dir) / "memory.json"

    async def execute(self, **kwargs) -> str:
        op = kwargs.get("operation")
        path = self._path()
        items = _load(path)
        try:
            if op == "save":
                content = (kwargs.get("content") or "").strip()
                if not content:
                    return json.dumps({"error": "content is required"})
                if re.search(r"(password|api[_ -]?key|secret|token)\s*[:=]", content, re.I):
                    return json.dumps({"error": "refusing to store what looks like a credential"})
                for it in items:
                    if it["content"].lower() == content.lower():
                        return json.dumps({"saved": False, "message": "already remembered", "id": it["id"]})
                item = {"id": uuid.uuid4().hex[:8], "content": content[:500], "category": (kwargs.get("category") or "other")[:30],
                        "created_at": time.strftime("%Y-%m-%d %H:%M"), "used": 0}
                items.append(item)
                _save(path, items)
                return json.dumps({"saved": True, "id": item["id"], "total_memories": len(items)})
            if op == "list":
                return json.dumps({"count": len(items), "memories": [{k: it[k] for k in ("id", "content", "category", "created_at")} for it in items[-100:]]})
            if op == "delete":
                before = len(items)
                items = [it for it in items if it["id"] != kwargs.get("id")]
                _save(path, items)
                return json.dumps({"deleted": before - len(items)})
            if op == "update":
                for it in items:
                    if it["id"] == kwargs.get("id"):
                        it["content"] = (kwargs.get("content") or it["content"])[:500]
                        if kwargs.get("category"):
                            it["category"] = kwargs["category"]
                        _save(path, items)
                        return json.dumps({"updated": it})
                return json.dumps({"error": "memory not found"})
            if op == "search":
                query = (kwargs.get("content") or "").strip()
                limit = max(1, min(int(kwargs.get("limit") or 8), 30))
                if not items:
                    return json.dumps({"count": 0, "memories": [], "message": "no memories stored yet"})
                if not query:
                    sel = items[-limit:]
                else:
                    q = set(tokenize(query))
                    scored = []
                    emb = _Embedder.get().embed([query] + [it["content"] for it in items])
                    for i, it in enumerate(items):
                        kw = len(q & set(tokenize(it["content"]))) / (len(q) or 1)
                        es = _cosine(emb[0], emb[i + 1]) if emb else 0.0
                        score = 0.5 * kw + 0.5 * max(0.0, es)
                        scored.append((score, it))
                    scored.sort(key=lambda x: -x[0])
                    sel = [it for s, it in scored[:limit] if s > 0.12]
                for it in sel:
                    it["used"] = int(it.get("used", 0)) + 1
                _save(path, items)
                return json.dumps({"count": len(sel), "memories": [{k: it[k] for k in ("id", "content", "category", "created_at")} for it in sel]})
            return json.dumps({"error": f"unknown operation {op}"})
        except Exception as e:
            return json.dumps({"error": str(e)})


@register_tool("tasks_manage")
class TasksTool(BaseTool):
    name = "tasks_manage"
    description = (
        "Personal to-do list that persists across conversations. Operations: add (title, optional due date "
        "YYYY-MM-DD, priority high/medium/low, notes), list (open, done or all; overdue flagged), complete, "
        "update, delete. Use it when the user asks to track, remind or plan tasks."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["add", "list", "complete", "update", "delete"]},
            "title": {"type": "string"},
            "due": {"type": "string", "description": "Due date YYYY-MM-DD (optional)."},
            "priority": {"type": "string", "enum": ["high", "medium", "low"]},
            "notes": {"type": "string"},
            "id": {"type": "string"},
            "status": {"type": "string", "enum": ["open", "done", "all"], "description": "Filter for list (default open)."},
        },
        "required": ["operation"],
    }

    def _path(self) -> Path:
        return Path(self.ctx.state_dir) / "tasks.json"

    async def execute(self, **kwargs) -> str:
        op = kwargs.get("operation")
        path = self._path()
        items = _load(path)
        today = time.strftime("%Y-%m-%d")
        try:
            if op == "add":
                title = (kwargs.get("title") or "").strip()
                if not title:
                    return json.dumps({"error": "title is required"})
                item = {"id": uuid.uuid4().hex[:6], "title": title[:200], "due": kwargs.get("due"), "priority": kwargs.get("priority") or "medium",
                        "notes": (kwargs.get("notes") or "")[:1000], "done": False, "created_at": today}
                items.append(item)
                _save(path, items)
                return json.dumps({"added": item, "open_tasks": sum(1 for t in items if not t["done"])})
            if op == "list":
                status = kwargs.get("status") or "open"
                sel = [t for t in items if status == "all" or (t["done"] == (status == "done"))]
                order = {"high": 0, "medium": 1, "low": 2}
                sel.sort(key=lambda t: (t["done"], t.get("due") or "9999", order.get(t.get("priority"), 1)))
                for t in sel:
                    t["overdue"] = bool(t.get("due") and not t["done"] and t["due"] < today)
                return json.dumps({"today": today, "count": len(sel), "tasks": sel})
            target = next((t for t in items if t["id"] == kwargs.get("id") or (kwargs.get("title") and t["title"].lower() == kwargs["title"].lower())), None)
            if target is None:
                return json.dumps({"error": "task not found (give id or exact title)"})
            if op == "complete":
                target["done"] = True
                target["completed_at"] = today
            elif op == "update":
                for key in ("title", "due", "priority", "notes"):
                    if kwargs.get(key) is not None:
                        target[key] = kwargs[key]
            elif op == "delete":
                items = [t for t in items if t is not target]
            _save(path, items)
            return json.dumps({op: target})
        except Exception as e:
            return json.dumps({"error": str(e)})
