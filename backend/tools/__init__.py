"""
Tool system for Nexus Chat.

To create a new tool:
1. Create a new file in backend/tools/
2. Subclass BaseTool
3. Use the @register_tool decorator
4. Add the tool entry to config/settings.yaml

Example:
    @register_tool("my_tool")
    class MyTool(BaseTool):
        name = "my_tool"
        description = "Does something useful"
        parameters = {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "The input"}
            },
            "required": ["input"]
        }

        async def execute(self, **kwargs) -> str:
            return f"Result: {kwargs['input']}"

Per-request context
-------------------
Tools that touch the filesystem (file reader, csv viewer, graph plotter,
code executor artifacts, …) should resolve directories through
:func:`get_context` instead of hard-coding ``./data/uploads``. The
orchestrator populates the context for every chat turn; the multi-user
server edition points it at the current user's private directories.
"""

from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator


@dataclass
class ToolContext:
    """Where a tool may read uploads from / write generated files to."""

    upload_dir: Path = Path("./data/uploads")
    files_dir: Path = Path("./data/files")
    downloads_dir: Path = Path("./data/downloads")
    plots_dir: Path = Path("./data")
    # Persistent per-user state (memory, tasks, snapshots). The server
    # edition points this at data/users/<u>/state.
    state_dir: Path = Path("./data/state")
    username: str | None = None
    extra: dict = field(default_factory=dict)

    def url_prefix(self) -> str:
        """Query-string suffix that scopes file URLs to the owner (server
        edition). Empty for the single-user build."""
        return self.extra.get("url_suffix", "")


_current_context: ContextVar[ToolContext | None] = ContextVar("nexus_tool_context", default=None)
_default_context = ToolContext()


def get_context() -> ToolContext:
    ctx = _current_context.get()
    return ctx if ctx is not None else _default_context


def set_context(ctx: ToolContext | None):
    """Set the active tool context; returns a token for :func:`reset_context`."""
    return _current_context.set(ctx)


def reset_context(token) -> None:
    _current_context.reset(token)


def set_default_context(ctx: ToolContext) -> None:
    global _default_context
    _default_context = ctx


class BaseTool(ABC):
    """Base class for all tools."""

    name: str = ""
    description: str = ""
    parameters: dict = {}  # JSON Schema for tool parameters

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """Execute the tool with the given parameters."""
        ...

    async def stream_execute(self, **kwargs) -> AsyncIterator[str]:
        """Optional streaming execution. Tools that support live output
        can override this to yield chunks in real-time. The default
        delegates to execute() and yields the full result as one chunk.
        """
        yield await self.execute(**kwargs)

    def to_definition(self) -> dict:
        """Convert to a tool definition for LLM providers."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    # Convenience accessors -------------------------------------------
    @property
    def ctx(self) -> ToolContext:
        return get_context()

    def upload_dir(self) -> Path:
        cfg = self.config.get("upload_dir")
        ctx = get_context()
        if ctx.username is not None or not cfg:
            return Path(ctx.upload_dir)
        return Path(cfg)


# --- Tool Registry ---
_tools: dict[str, type[BaseTool]] = {}


def register_tool(name: str):
    """Decorator to register a tool class."""
    def decorator(cls):
        _tools[name] = cls
        if not cls.name:
            cls.name = name
        return cls
    return decorator


def get_tool_class(name: str) -> type[BaseTool] | None:
    """Return the tool class, importing its module lazily on first use.

    Modules are looked up in :mod:`backend.tools.catalog`, so heavy
    dependencies (SymPy, DuckDB, pandas, torch…) are only imported when a
    tool is actually needed.
    """
    cls = _tools.get(name)
    if cls is not None:
        return cls
    try:
        from .catalog import CATALOG_BY_ID
    except Exception:  # pragma: no cover
        return None
    meta = CATALOG_BY_ID.get(name)
    if meta and meta.get("module"):
        import importlib

        try:
            importlib.import_module(meta["module"])
        except Exception as e:  # missing optional dependency etc.
            import logging

            logging.getLogger(__name__).warning("Could not load tool '%s' from %s: %s", name, meta["module"], e)
            return None
        cls = _tools.get(name)
    return cls


def get_all_tool_classes() -> dict[str, type[BaseTool]]:
    return dict(_tools)
