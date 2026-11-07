"""
LLM Provider abstraction layer.
Add new providers by subclassing BaseLLMProvider and registering them.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class Message:
    role: str  # "user", "assistant", "system", "tool"
    content: str
    tool_call_id: str | None = None
    tool_calls: list | None = None
    # Optional image attachments for vision-capable models. Each entry is
    # either a ``data:<mime>;base64,<payload>`` URL or a bare base64
    # string (assumed PNG). Providers translate these into their native
    # multimodal content format.
    images: list[str] | None = None


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass
class StreamChunk:
    content: str = ""
    tool_call: dict | None = None
    done: bool = False
    usage: dict | None = None
    # Non-fatal provider notice surfaced to the UI (e.g. "tools disabled
    # because the model does not support them").
    notice: str | None = None
    # Fatal provider error. The orchestrator converts this into an
    # ``error`` event instead of rendering it as assistant text.
    error: str | None = None


# Keys accepted in ``ChatRequest.generation``. Each provider applies the
# subset it understands and ignores the rest.
GENERATION_KEYS = (
    "num_ctx",          # Ollama context window (tokens)
    "max_tokens",       # response budget (Ollama num_predict)
    "temperature",
    "top_p",
    "top_k",            # Ollama / Anthropic
    "repeat_penalty",   # Ollama
    "seed",             # Ollama / OpenAI
    "keep_alive",       # Ollama model residency ("5m", "-1", 0)
    "thinking",         # bool | None — override per-model thinking flag
    "thinking_level",   # "minimal" | "low" | "medium" | "high"
    "thinking_budget_tokens",  # Anthropic extended-thinking budget
)


@dataclass
class ChatRequest:
    messages: list[Message]
    model: str
    tools: list[ToolDefinition] = field(default_factory=list)
    max_tokens: int = 4096
    temperature: float = 0.7
    stream: bool = True
    system_prompt: str = ""
    # Per-model "thinking" / "reasoning" configuration. None means the
    # model is non-thinking and providers must not send any thinking
    # parameters. Recognized fields (provider-specific subset applies):
    #   enabled: bool       — turn thinking on
    #   level:   str        — "low" | "medium" | "high" (Ollama, OpenAI)
    #   budget_tokens: int  — Anthropic extended-thinking budget
    thinking: dict | None = None
    # User-adjustable generation settings (see GENERATION_KEYS). These
    # come from the prompt-box settings control and override the
    # per-model defaults above when present.
    generation: dict = field(default_factory=dict)

    def gen(self, key: str, default=None):
        """Read a generation setting, falling back to ``default``."""
        val = self.generation.get(key) if self.generation else None
        return default if val is None else val


def parse_data_url(image: str) -> tuple[str, str]:
    """Split a data URL (or bare base64 string) into ``(media_type, b64)``."""
    if image.startswith("data:"):
        header, _, payload = image.partition(",")
        media_type = header[5:].split(";", 1)[0] or "image/png"
        return media_type, payload
    return "image/png", image


class BaseLLMProvider(ABC):
    """Base class for all LLM providers."""

    name: str = "base"
    # Whether this provider talks to a local/self-hosted runtime (Ollama)
    # or a hosted cloud API. The UI uses this to pick the matching set of
    # default generation settings ("local" vs "cloud").
    kind: str = "cloud"

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def chat(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        """Stream a chat completion response."""
        ...

    @abstractmethod
    async def chat_sync(self, request: ChatRequest) -> str:
        """Non-streaming chat completion."""
        ...

    @abstractmethod
    def list_models(self) -> list[dict]:
        """Return available models for this provider."""
        ...

    def is_available(self) -> bool:
        """Check if the provider is properly configured."""
        return True

    async def list_remote_models(self) -> set[str] | None:
        """Return the set of model IDs the remote server actually offers.

        Distinguish three cases:
          - ``None``     — no probe possible (provider not configured,
                           network failed, no implementation). The UI
                           must treat every configured model as
                           "unknown" availability and not grey them out.
          - ``set()``    — probe succeeded but the server has no models.
          - ``{...}``    — probe succeeded; only IDs in this set are
                           confirmed available.

        Providers that can probe their backend (Ollama, OpenAI,
        Anthropic) override this with a cached lookup.
        """
        return None

    async def model_capabilities(self, model_id: str) -> dict | None:
        """Return capability metadata for a model, when the backend can
        report it. Shape::

            {"vision": bool, "tools": bool, "thinking": bool,
             "context_length": int | None, "quantization": str | None,
             "family": str | None, "parameter_size": str | None}

        ``None`` means "unknown" — callers must not assume anything.
        """
        return None


# --- Provider Registry ---
_providers: dict[str, type[BaseLLMProvider]] = {}


def register_provider(name: str):
    """Decorator to register an LLM provider class."""
    def decorator(cls):
        _providers[name] = cls
        cls.name = name
        return cls
    return decorator


def get_provider_class(name: str) -> type[BaseLLMProvider] | None:
    return _providers.get(name)


def get_all_provider_classes() -> dict[str, type[BaseLLMProvider]]:
    return dict(_providers)
