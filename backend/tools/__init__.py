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
"""

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator


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
    return _tools.get(name)


def get_all_tool_classes() -> dict[str, type[BaseTool]]:
    return dict(_tools)
