"""OpenAI LLM provider (Chat Completions API).

Supports streaming text, reasoning models (``reasoning_effort``), native
tool calling (multiple parallel tool calls are tracked by index) and
image attachments (``image_url`` content parts with data URLs).
Generation settings from the prompt-box control override the per-model
defaults. ``base_url`` may be set to use any OpenAI-compatible endpoint.
"""

import logging
import time
from typing import AsyncIterator

from . import (
    BaseLLMProvider, ChatRequest, StreamChunk, register_provider
)

logger = logging.getLogger(__name__)

_REMOTE_MODEL_CACHE_TTL_SECONDS = 60


@register_provider("openai")
class OpenAIProvider(BaseLLMProvider):
    kind = "cloud"

    def __init__(self, config: dict):
        super().__init__(config)
        self._client = None
        self._remote_models_cache: set[str] | None = None
        self._remote_models_cache_ts: float = 0.0

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                api_key = self.config.get("api_key", "")
                if not api_key or api_key.startswith("${"):
                    return None
                kwargs = {"api_key": api_key}
                base_url = self.config.get("base_url")
                if base_url and not str(base_url).startswith("${"):
                    kwargs["base_url"] = base_url
                self._client = AsyncOpenAI(**kwargs)
            except ImportError:
                logger.warning("openai package not installed")
                return None
        return self._client

    def is_available(self) -> bool:
        return self._get_client() is not None

    def list_models(self) -> list[dict]:
        return self.config.get("models", [])

    async def list_remote_models(self) -> set[str] | None:
        """Probe the OpenAI ``/v1/models`` endpoint for the set of model
        IDs the configured API key has access to. Returns ``None`` when
        no probe is possible (no API key) or the call fails before any
        successful prior result was cached."""
        client = self._get_client()
        if client is None:
            return None
        now = time.monotonic()
        if (
            self._remote_models_cache is not None
            and now - self._remote_models_cache_ts < _REMOTE_MODEL_CACHE_TTL_SECONDS
        ):
            return self._remote_models_cache
        try:
            resp = await client.models.list()
            models = {m.id for m in resp.data}
            self._remote_models_cache = models
            self._remote_models_cache_ts = now
            return models
        except Exception as e:
            logger.debug("OpenAI models.list probe failed: %s", e)
            return self._remote_models_cache

    async def model_capabilities(self, model_id: str) -> dict | None:
        cfg = next((m for m in self.list_models() if m.get("id") == model_id), {})
        mid = model_id.lower()
        # o-series / gpt-5 / 4o / 4.1 all accept images; legacy 3.5 does not.
        vision = cfg.get("vision", not mid.startswith(("gpt-3.5", "o1-mini", "o3-mini")))
        return {
            "vision": bool(vision),
            "tools": True,
            "thinking": bool(cfg.get("thinking") or cfg.get("thinking_level")),
            "context_length": cfg.get("context_length", 128000),
            "quantization": None,
            "family": "openai",
            "parameter_size": None,
        }

    def _convert_tools(self, tools) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]

    def _build_messages(self, messages, system_prompt="") -> list[dict]:
        result = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        for msg in messages:
            if msg.images and msg.role == "user":
                parts = []
                if msg.content:
                    parts.append({"type": "text", "text": msg.content})
                for img in msg.images:
                    url = img if img.startswith("data:") else f"data:image/png;base64,{img}"
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                result.append({"role": msg.role, "content": parts})
            else:
                result.append({"role": msg.role, "content": msg.content or ""})
        return result

    def _reasoning_effort(self, request: ChatRequest) -> str | None:
        """Map the generic thinking config to OpenAI ``reasoning_effort``.

        Returns None for non-thinking models so the request stays unchanged.
        """
        override = request.gen("thinking")
        cfg = request.thinking or {}
        if override is False:
            return None
        if not (override or cfg.get("enabled")):
            return None
        level = request.gen("thinking_level") or cfg.get("level")
        if level in ("minimal", "low", "medium", "high"):
            return level
        # ``thinking: true`` with no explicit level → default to medium.
        return "medium"

    def _build_kwargs(self, request: ChatRequest, stream: bool) -> dict:
        kwargs = {
            "model": request.model,
            "messages": self._build_messages(request.messages, request.system_prompt),
        }
        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}

        max_tokens = int(request.gen("max_tokens", request.max_tokens))
        effort = self._reasoning_effort(request)
        mid = request.model.lower()
        reasoning_family = effort is not None or mid.startswith(("o1", "o3", "o4", "gpt-5"))
        if effort:
            kwargs["reasoning_effort"] = effort
        if reasoning_family:
            # Reasoning models reject ``temperature``/``top_p`` and use
            # ``max_completion_tokens`` instead of ``max_tokens``.
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = float(request.gen("temperature", request.temperature))
            top_p = request.gen("top_p")
            if top_p not in (None, ""):
                kwargs["top_p"] = float(top_p)
        seed = request.gen("seed")
        if seed not in (None, ""):
            kwargs["seed"] = int(seed)
        if request.tools:
            kwargs["tools"] = self._convert_tools(request.tools)
        return kwargs

    async def chat(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        client = self._get_client()
        if not client:
            yield StreamChunk(error="OpenAI provider not configured (missing API key).", done=True)
            return

        kwargs = self._build_kwargs(request, stream=True)

        try:
            usage_data = None
            # Parallel tool calls stream interleaved by ``index``; collect
            # them and flush at the end so each one is complete.
            pending_calls: dict[int, dict] = {}
            finished = False
            stream = await client.chat.completions.create(**kwargs)
            async for chunk in stream:
                # Capture usage from the final chunk
                if hasattr(chunk, "usage") and chunk.usage:
                    usage_data = {
                        "prompt_tokens": chunk.usage.prompt_tokens or 0,
                        "completion_tokens": chunk.usage.completion_tokens or 0,
                        "total_tokens": chunk.usage.total_tokens or 0,
                    }

                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield StreamChunk(content=delta.content)
                if delta and delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = getattr(tc, "index", 0) or 0
                        entry = pending_calls.setdefault(idx, {"id": None, "name": None, "arguments": ""})
                        if tc.id:
                            entry["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                entry["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                entry["arguments"] += fn.arguments
                if chunk.choices and chunk.choices[0].finish_reason:
                    finished = True
                    for idx in sorted(pending_calls):
                        call = pending_calls[idx]
                        if call["name"]:
                            yield StreamChunk(tool_call={**call, "complete": True})
                    pending_calls = {}
            if not finished:
                for idx in sorted(pending_calls):
                    call = pending_calls[idx]
                    if call["name"]:
                        yield StreamChunk(tool_call={**call, "complete": True})
            yield StreamChunk(done=True, usage=usage_data)
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            yield StreamChunk(error=f"OpenAI error: {e}", done=True)

    async def chat_sync(self, request: ChatRequest) -> str:
        client = self._get_client()
        if not client:
            return "OpenAI provider not configured."
        kwargs = self._build_kwargs(request, stream=False)
        kwargs.pop("tools", None)
        try:
            response = await client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"Error: {e}"
