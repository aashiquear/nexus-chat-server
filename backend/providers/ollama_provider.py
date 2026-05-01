"""Ollama LLM provider - supports local and remote instances."""

import json
import logging
import time
from typing import AsyncIterator

import httpx

from . import (
    BaseLLMProvider, ChatRequest, StreamChunk, register_provider
)

logger = logging.getLogger(__name__)

# How long to trust a cached /api/tags response before refetching.
_REMOTE_MODEL_CACHE_TTL_SECONDS = 60


@register_provider("ollama")
class OllamaProvider(BaseLLMProvider):

    def __init__(self, config: dict):
        super().__init__(config)
        base = config.get("base_url", "http://localhost:11434").rstrip("/")
        # Normalize: strip trailing /api so endpoint paths (/api/chat, /api/tags)
        # work for both local (http://localhost:11434) and cloud (https://ollama.com/api)
        if base.endswith("/api"):
            base = base[:-4]
        self.base_url = base
        api_key = config.get("api_key", "")
        # Build auth header for Ollama Cloud (Bearer token)
        self._headers = {}
        if api_key and not api_key.startswith("${"):
            self._headers["Authorization"] = f"Bearer {api_key}"
        # Remote-model probe cache. ``None`` means "no successful probe
        # yet" — distinct from an empty set, which means "the server
        # genuinely has no models".
        self._remote_models_cache: set[str] | None = None
        self._remote_models_cache_ts: float = 0.0

    def is_available(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            import httpx
            with httpx.Client(timeout=3) as client:
                resp = client.get(f"{self.base_url}/api/tags", headers=self._headers)
                return resp.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[dict]:
        """Query Ollama for installed models dynamically. Always live."""
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.base_url}/api/tags", headers=self._headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return [
                        {"id": m["name"], "name": m["name"]}
                        for m in data.get("models", [])
                    ]
        except Exception:
            pass
        return []

    async def list_remote_models(self) -> set[str] | None:
        """Probe ``GET /api/tags`` for the set of models the Ollama server
        actually has available. Cached for ~60s to avoid hammering the
        endpoint on every model-list refresh. Returns ``None`` if the
        probe fails and no prior result is cached."""
        now = time.monotonic()
        if (
            self._remote_models_cache is not None
            and now - self._remote_models_cache_ts < _REMOTE_MODEL_CACHE_TTL_SECONDS
        ):
            return self._remote_models_cache
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{self.base_url}/api/tags", headers=self._headers
                )
                if resp.status_code == 200:
                    data = resp.json()
                    models = {m.get("name") for m in data.get("models", []) if m.get("name")}
                    self._remote_models_cache = models
                    self._remote_models_cache_ts = now
                    return models
        except Exception as e:
            logger.debug("Ollama /api/tags probe failed: %s", e)
        return self._remote_models_cache  # may be None if never succeeded

    def _build_messages(self, messages, system_prompt="") -> list[dict]:
        result = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        for msg in messages:
            result.append({"role": msg.role, "content": msg.content})
        return result

    def _convert_tools(self, tools) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    def _think_param(self, request: ChatRequest):
        """Translate the generic thinking config into Ollama's ``think`` field.

        Returns one of:
          None   — model is non-thinking; field is omitted entirely
          True   — boolean thinking (e.g. deepseek-r1, qwen3-thinking)
          "low" / "medium" / "high" — level-based (e.g. gpt-oss)
        """
        cfg = request.thinking
        if not cfg or not cfg.get("enabled"):
            return None
        level = cfg.get("level")
        if level in ("low", "medium", "high"):
            return level
        return True

    async def chat(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        payload = {
            "model": request.model,
            "messages": self._build_messages(request.messages, request.system_prompt),
            "stream": True,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }
        if request.tools:
            payload["tools"] = self._convert_tools(request.tools)

        # Only set ``think`` when the model is configured as a thinking
        # model — otherwise non-thinking models are unaffected.
        think = self._think_param(request)
        if think is not None:
            payload["think"] = think

        # Track whether we're currently inside a streamed thinking section
        # so we can wrap it in <think>…</think> tags for the frontend.
        in_thinking = False

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=payload,
                    headers=self._headers,
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        msg = data.get("message", {})

                        if msg.get("tool_calls"):
                            for tc in msg["tool_calls"]:
                                yield StreamChunk(tool_call={
                                    "name": tc["function"]["name"],
                                    "arguments": json.dumps(
                                        tc["function"].get("arguments", {})
                                    ),
                                })

                        # Ollama emits thinking output in ``message.thinking``
                        # separately from ``message.content``. Bracket it
                        # with the same <think> tags the frontend already
                        # parses so the UI doesn't need provider-specific
                        # logic.
                        thinking_delta = msg.get("thinking", "")
                        if thinking_delta:
                            if not in_thinking:
                                yield StreamChunk(content="<think>")
                                in_thinking = True
                            yield StreamChunk(content=thinking_delta)

                        content = msg.get("content", "")
                        if content:
                            if in_thinking:
                                yield StreamChunk(content="</think>")
                                in_thinking = False
                            yield StreamChunk(content=content)

                        if data.get("done", False):
                            if in_thinking:
                                yield StreamChunk(content="</think>")
                                in_thinking = False
                            usage_data = None
                            prompt_tokens = data.get("prompt_eval_count", 0)
                            completion_tokens = data.get("eval_count", 0)
                            if prompt_tokens or completion_tokens:
                                usage_data = {
                                    "prompt_tokens": prompt_tokens,
                                    "completion_tokens": completion_tokens,
                                    "total_tokens": prompt_tokens + completion_tokens,
                                }
                            yield StreamChunk(done=True, usage=usage_data)
        except httpx.ConnectError:
            if in_thinking:
                yield StreamChunk(content="</think>")
            yield StreamChunk(
                content="Cannot connect to Ollama. Ensure it is running.",
                done=True,
            )
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            if in_thinking:
                yield StreamChunk(content="</think>")
            yield StreamChunk(content=f"Error: {e}", done=True)

    async def chat_sync(self, request: ChatRequest) -> str:
        payload = {
            "model": request.model,
            "messages": self._build_messages(request.messages, request.system_prompt),
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }
        if request.tools:
            payload["tools"] = self._convert_tools(request.tools)

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self.base_url}/api/chat", json=payload,
                    headers=self._headers,
                )
                data = resp.json()
                return data.get("message", {}).get("content", "")
        except Exception as e:
            return f"Error: {e}"
