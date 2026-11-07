"""Anthropic Claude LLM provider.

Supports streaming text, extended thinking, native tool use (the tool
name/id arrive on ``content_block_start`` and the JSON input is streamed
as ``input_json_delta`` fragments) and image attachments (base64 image
content blocks). Generation settings from the prompt-box control
(``temperature``, ``top_p``, ``top_k``, ``max_tokens``, thinking budget)
override the per-model defaults.
"""

import logging
import time
from typing import AsyncIterator

from . import (
    BaseLLMProvider, ChatRequest, StreamChunk, parse_data_url, register_provider
)

logger = logging.getLogger(__name__)

_REMOTE_MODEL_CACHE_TTL_SECONDS = 60


@register_provider("anthropic")
class AnthropicProvider(BaseLLMProvider):
    kind = "cloud"

    def __init__(self, config: dict):
        super().__init__(config)
        self._client = None
        self._remote_models_cache: set[str] | None = None
        self._remote_models_cache_ts: float = 0.0

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                api_key = self.config.get("api_key", "")
                if not api_key or api_key.startswith("${"):
                    return None
                kwargs = {"api_key": api_key}
                base_url = self.config.get("base_url")
                if base_url and not str(base_url).startswith("${"):
                    kwargs["base_url"] = base_url
                self._client = anthropic.AsyncAnthropic(**kwargs)
            except ImportError:
                logger.warning("anthropic package not installed")
                return None
        return self._client

    def is_available(self) -> bool:
        return self._get_client() is not None

    def list_models(self) -> list[dict]:
        return self.config.get("models", [])

    async def list_remote_models(self) -> set[str] | None:
        """Probe Anthropic's ``/v1/models`` endpoint via the SDK. Returns
        ``None`` when the API key isn't set or the request fails with no
        prior cached result."""
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
            models: set[str] = set()
            resp = await client.models.list(limit=1000)
            data = getattr(resp, "data", None) or []
            models = {m.id for m in data if getattr(m, "id", None)}
            self._remote_models_cache = models
            self._remote_models_cache_ts = now
            return models
        except Exception as e:
            logger.debug("Anthropic models.list probe failed: %s", e)
            return self._remote_models_cache

    async def model_capabilities(self, model_id: str) -> dict | None:
        # All current Claude models accept images and tools.
        cfg = next((m for m in self.list_models() if m.get("id") == model_id), {})
        return {
            "vision": True,
            "tools": True,
            "thinking": bool(cfg.get("thinking") or cfg.get("thinking_budget_tokens")),
            "context_length": cfg.get("context_length", 200000),
            "quantization": None,
            "family": "claude",
            "parameter_size": None,
        }

    def _convert_tools(self, tools) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters or {"type": "object", "properties": {}},
            }
            for t in tools
        ]

    def _build_messages(self, messages) -> list[dict]:
        """Convert to Anthropic format, honouring the API constraints:
        no system messages inline, no empty content, alternating roles."""
        result: list[dict] = []
        for msg in messages:
            if msg.role == "system":
                continue  # Handled separately
            role = "assistant" if msg.role == "assistant" else "user"
            blocks: list[dict] = []
            if msg.images and role == "user":
                for img in msg.images:
                    media_type, data = parse_data_url(img)
                    blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": data},
                    })
            text = msg.content or ""
            if text.strip():
                blocks.append({"type": "text", "text": text})
            if not blocks:
                if role == "assistant":
                    continue
                blocks.append({"type": "text", "text": "(empty)"})
            if result and result[-1]["role"] == role:
                # Merge consecutive same-role turns.
                result[-1]["content"].extend(blocks)
            else:
                result.append({"role": role, "content": blocks})
        # Conversation must start with a user turn.
        while result and result[0]["role"] != "user":
            result.pop(0)
        return result

    def _build_thinking_kwarg(self, request: ChatRequest, max_tokens: int) -> dict | None:
        """Translate the generic thinking config into Anthropic's
        ``thinking`` parameter, or return None for non-thinking models."""
        override = request.gen("thinking")
        cfg = request.thinking or {}
        if override is False:
            return None
        if not (override or cfg.get("enabled")):
            return None
        # Anthropic requires budget_tokens < max_tokens (min 1024).
        budget = (
            request.gen("thinking_budget_tokens")
            or cfg.get("budget_tokens")
            or max(1024, max_tokens // 2)
        )
        budget = max(1024, min(int(budget), max(1024, max_tokens - 512)))
        return {"type": "enabled", "budget_tokens": int(budget)}

    def _build_kwargs(self, request: ChatRequest) -> dict:
        max_tokens = int(request.gen("max_tokens", request.max_tokens))
        kwargs = {
            "model": request.model,
            "max_tokens": max_tokens,
            "messages": self._build_messages(request.messages),
        }
        if request.system_prompt:
            kwargs["system"] = request.system_prompt
        if request.tools:
            kwargs["tools"] = self._convert_tools(request.tools)

        thinking_kwarg = self._build_thinking_kwarg(request, max_tokens)
        if thinking_kwarg:
            kwargs["thinking"] = thinking_kwarg
            # Extended thinking must have a budget strictly below max_tokens.
            if kwargs["thinking"]["budget_tokens"] >= max_tokens:
                kwargs["max_tokens"] = kwargs["thinking"]["budget_tokens"] + 1024
            # API constraint: extended thinking forces temperature=1 and
            # forbids top_k / top_p tweaks.
            kwargs["temperature"] = 1.0
        else:
            kwargs["temperature"] = float(request.gen("temperature", request.temperature))
            top_p = request.gen("top_p")
            top_k = request.gen("top_k")
            # Anthropic rejects temperature+top_p together on newer models;
            # prefer temperature unless the user pinned only top_p.
            if top_p not in (None, "") and request.gen("temperature") is None:
                kwargs.pop("temperature", None)
                kwargs["top_p"] = float(top_p)
            if top_k not in (None, ""):
                kwargs["top_k"] = int(top_k)
        return kwargs

    async def chat(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        client = self._get_client()
        if not client:
            yield StreamChunk(error="Anthropic provider not configured (missing API key).", done=True)
            return

        kwargs = self._build_kwargs(request)

        # Track whether the active content block is a "thinking" block so
        # we can bracket those deltas with <think>…</think> for the UI.
        in_thinking_block = False

        try:
            usage_data = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            async with client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    etype = getattr(event, "type", None)
                    if not etype:
                        continue
                    if etype == "message_start" and hasattr(event, "message"):
                        msg_usage = getattr(event.message, "usage", None)
                        if msg_usage:
                            usage_data["prompt_tokens"] = (
                                (getattr(msg_usage, "input_tokens", 0) or 0)
                                + (getattr(msg_usage, "cache_read_input_tokens", 0) or 0)
                                + (getattr(msg_usage, "cache_creation_input_tokens", 0) or 0)
                            )
                    elif etype == "message_delta":
                        delta_usage = getattr(event, "usage", None)
                        if delta_usage:
                            usage_data["completion_tokens"] = getattr(delta_usage, "output_tokens", 0) or 0
                        usage_data["total_tokens"] = usage_data["prompt_tokens"] + usage_data["completion_tokens"]
                    elif etype == "content_block_start":
                        block = getattr(event, "content_block", None)
                        block_type = getattr(block, "type", None) if block else None
                        if block_type == "thinking":
                            in_thinking_block = True
                            yield StreamChunk(content="<think>")
                        elif block_type == "tool_use":
                            yield StreamChunk(tool_call={
                                "id": getattr(block, "id", None),
                                "name": getattr(block, "name", None),
                                "arguments": "",
                            })
                    elif etype == "content_block_delta":
                        delta = event.delta
                        dtype = getattr(delta, "type", "")
                        if dtype == "thinking_delta" or getattr(delta, "thinking", None):
                            if not in_thinking_block:
                                in_thinking_block = True
                                yield StreamChunk(content="<think>")
                            yield StreamChunk(content=getattr(delta, "thinking", "") or "")
                        elif dtype == "text_delta" or getattr(delta, "text", None):
                            yield StreamChunk(content=getattr(delta, "text", "") or "")
                        elif dtype == "input_json_delta" or hasattr(delta, "partial_json"):
                            yield StreamChunk(
                                tool_call={"partial": getattr(delta, "partial_json", "") or ""}
                            )
                    elif etype == "content_block_stop":
                        if in_thinking_block:
                            in_thinking_block = False
                            yield StreamChunk(content="</think>")
                    elif etype == "message_stop":
                        if in_thinking_block:
                            in_thinking_block = False
                            yield StreamChunk(content="</think>")
                        yield StreamChunk(done=True, usage=usage_data if usage_data["total_tokens"] > 0 else None)
                        return
            if in_thinking_block:
                yield StreamChunk(content="</think>")
            yield StreamChunk(done=True, usage=usage_data if usage_data["total_tokens"] > 0 else None)
        except Exception as e:
            logger.error(f"Anthropic error: {e}")
            if in_thinking_block:
                yield StreamChunk(content="</think>")
            yield StreamChunk(error=f"Anthropic error: {e}", done=True)

    async def chat_sync(self, request: ChatRequest) -> str:
        client = self._get_client()
        if not client:
            return "Anthropic provider not configured."
        kwargs = self._build_kwargs(request)
        kwargs.pop("thinking", None)
        kwargs.pop("tools", None)
        try:
            response = await client.messages.create(**kwargs)
            return "".join(
                block.text for block in response.content
                if hasattr(block, "text")
            )
        except Exception as e:
            return f"Error: {e}"
