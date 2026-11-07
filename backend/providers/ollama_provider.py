"""Ollama LLM provider - supports local and remote (Ollama Cloud) instances.

Features
────────
* Vision: ``Message.images`` are forwarded as Ollama ``images`` (base64).
* Generation controls: ``num_ctx``, ``num_predict``, ``temperature``,
  ``top_p``, ``top_k``, ``repeat_penalty``, ``seed`` and ``keep_alive`` are
  read from ``ChatRequest.generation`` (the prompt-box settings control)
  and fall back to the per-model defaults from ``settings.yaml``.
* Capability probing: ``POST /api/show`` tells us whether a model supports
  ``vision`` / ``tools`` / ``thinking`` and reports its context length and
  quantization. Tools are automatically dropped (with a notice) for
  models that reject them, which otherwise produces a hard 400 error.
* Robust streaming: ``{"error": ...}`` lines and non-200 responses are
  surfaced as proper errors instead of silently producing no output.
"""

import json
import logging
import time
from typing import AsyncIterator

import httpx

from . import (
    BaseLLMProvider, ChatRequest, StreamChunk, parse_data_url, register_provider
)

logger = logging.getLogger(__name__)

# How long to trust a cached /api/tags response before refetching.
_REMOTE_MODEL_CACHE_TTL_SECONDS = 60
# /api/show results are stable for a given tag; cache them for 10 minutes.
_CAPABILITY_CACHE_TTL_SECONDS = 600


@register_provider("ollama")
class OllamaProvider(BaseLLMProvider):
    kind = "local"

    def __init__(self, config: dict):
        super().__init__(config)
        base = config.get("base_url", "http://localhost:11434").rstrip("/")
        # Normalize: strip trailing /api so endpoint paths (/api/chat, /api/tags)
        # work for both local (http://localhost:11434) and cloud (https://ollama.com/api)
        if base.endswith("/api"):
            base = base[:-4]
        self.base_url = base
        if "ollama.com" in base:
            self.kind = "cloud"
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
        # Raw /api/tags entries keyed by model name (for quantization etc.)
        self._remote_details: dict[str, dict] = {}
        # Per-model /api/show capability cache: model -> (ts, caps)
        self._caps_cache: dict[str, tuple[float, dict]] = {}
        self._available_cache: bool | None = None
        self._available_cache_ts: float = 0.0
        # Request timeout (seconds). Large local models can take a while
        # to load; ``None`` read timeout keeps long generations alive.
        self._timeout = float(config.get("timeout", 600))

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        """Check if Ollama server is reachable (cached for 30s so listing
        40 configured models does not issue 40 probes)."""
        now = time.monotonic()
        if self._available_cache is not None and now - self._available_cache_ts < 30:
            return self._available_cache
        try:
            with httpx.Client(timeout=3) as client:
                resp = client.get(f"{self.base_url}/api/tags", headers=self._headers)
                ok = resp.status_code == 200
                if ok:
                    self._ingest_tags(resp.json())
        except Exception:
            ok = False
        self._available_cache = ok
        self._available_cache_ts = now
        return ok

    def list_models(self) -> list[dict]:
        """Return configured models plus any models discovered on the
        server that are not in the config (so freshly pulled models show
        up without editing settings.yaml)."""
        configured = list(self.config.get("models", []) or [])
        known = {m.get("id") for m in configured}
        discovered: list[dict] = []
        remote = self._remote_models_cache
        if remote is None:
            # No async probe yet — do a quick sync one so the first
            # /api/models call is complete.
            try:
                with httpx.Client(timeout=5) as client:
                    resp = client.get(f"{self.base_url}/api/tags", headers=self._headers)
                    if resp.status_code == 200:
                        self._ingest_tags(resp.json())
                        remote = self._remote_models_cache
            except Exception:
                remote = None
        for name in sorted(remote or []):
            if name in known:
                continue
            details = self._remote_details.get(name, {}).get("details", {}) or {}
            discovered.append({
                "id": name,
                "name": name,
                "max_tokens": 8192,
                "discovered": True,
                "quantization": details.get("quantization_level"),
                "family": details.get("family"),
                "parameter_size": details.get("parameter_size"),
            })
        # Annotate configured entries with quantization info when known.
        out = []
        for m in configured:
            entry = dict(m)
            details = self._remote_details.get(m.get("id"), {}).get("details", {}) or {}
            if details:
                entry.setdefault("quantization", details.get("quantization_level"))
                entry.setdefault("family", details.get("family"))
                entry.setdefault("parameter_size", details.get("parameter_size"))
            out.append(entry)
        return out + discovered

    def _ingest_tags(self, data: dict) -> set[str]:
        models = set()
        details: dict[str, dict] = {}
        for m in data.get("models", []) or []:
            name = m.get("name") or m.get("model")
            if not name:
                continue
            models.add(name)
            details[name] = m
            # ``llama3.2:latest`` is also addressable as ``llama3.2``.
            if name.endswith(":latest"):
                short = name[: -len(":latest")]
                models.add(short)
                details[short] = m
        self._remote_models_cache = models
        self._remote_models_cache_ts = time.monotonic()
        self._remote_details = details
        return models

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
                    return self._ingest_tags(resp.json())
        except Exception as e:
            logger.debug("Ollama /api/tags probe failed: %s", e)
        return self._remote_models_cache  # may be None if never succeeded

    async def model_capabilities(self, model_id: str) -> dict | None:
        """``POST /api/show`` → vision / tools / thinking flags, context
        length and quantization. Cached per model."""
        now = time.monotonic()
        cached = self._caps_cache.get(model_id)
        if cached and now - cached[0] < _CAPABILITY_CACHE_TTL_SECONDS:
            return cached[1]
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.base_url}/api/show",
                    json={"model": model_id},
                    headers=self._headers,
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
        except Exception as e:
            logger.debug("Ollama /api/show failed for %s: %s", model_id, e)
            return None

        caps_list = [c.lower() for c in (data.get("capabilities") or [])]
        details = data.get("details") or {}
        model_info = data.get("model_info") or {}
        context_length = None
        for key, val in model_info.items():
            if key.endswith(".context_length") and isinstance(val, (int, float)):
                context_length = int(val)
                break
        caps = {
            "vision": "vision" in caps_list,
            "tools": "tools" in caps_list,
            "thinking": "thinking" in caps_list,
            "embedding": "embedding" in caps_list,
            "context_length": context_length,
            "quantization": details.get("quantization_level"),
            "family": details.get("family"),
            "parameter_size": details.get("parameter_size"),
            "capabilities": caps_list,
        }
        self._caps_cache[model_id] = (now, caps)
        return caps

    # ------------------------------------------------------------------
    # Request building
    # ------------------------------------------------------------------
    def _build_messages(self, messages, system_prompt="") -> list[dict]:
        result = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        for msg in messages:
            entry = {"role": msg.role, "content": msg.content or ""}
            if msg.images:
                entry["images"] = [parse_data_url(img)[1] for img in msg.images]
            result.append(entry)
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

    def _think_param(self, request: ChatRequest, caps: dict | None):
        """Translate the thinking config into Ollama's ``think`` field.

        Precedence: user override (generation.thinking) → per-model config
        → capability probe (thinking-capable models default to on).

        Returns one of:
          None   — omit the field entirely
          False  — explicitly disable thinking
          True   — boolean thinking (e.g. deepseek-r1, qwen3)
          "low" / "medium" / "high" — level-based (e.g. gpt-oss)
        """
        override = request.gen("thinking")
        level = request.gen("thinking_level")
        cfg = request.thinking or {}
        if override is False:
            return False if (cfg.get("enabled") or (caps and caps.get("thinking"))) else None
        enabled = bool(override) or bool(cfg.get("enabled"))
        if not enabled and caps and caps.get("thinking") and override is None:
            # Model advertises thinking; leave Ollama's default behaviour
            # (on) alone but capture the <think> stream.
            return None
        if not enabled:
            return None
        level = level or cfg.get("level")
        if level in ("low", "medium", "high"):
            return level
        return True

    def _options(self, request: ChatRequest) -> dict:
        opts = {
            "temperature": request.gen("temperature", request.temperature),
            "num_predict": int(request.gen("max_tokens", request.max_tokens)),
        }
        for key in ("num_ctx", "top_p", "top_k", "repeat_penalty", "seed"):
            val = request.gen(key)
            if val is not None and val != "":
                opts[key] = val
        if "num_ctx" in opts:
            opts["num_ctx"] = int(opts["num_ctx"])
        return opts

    async def _prepare_payload(self, request: ChatRequest, stream: bool):
        caps = await self.model_capabilities(request.model)
        payload = {
            "model": request.model,
            "messages": self._build_messages(request.messages, request.system_prompt),
            "stream": stream,
            "options": self._options(request),
        }
        keep_alive = request.gen("keep_alive")
        if keep_alive not in (None, ""):
            payload["keep_alive"] = keep_alive

        notice = None
        if request.tools:
            if caps is not None and not caps.get("tools"):
                notice = (
                    f"Model '{request.model}' does not support tool calling; "
                    "tools were disabled for this request."
                )
            else:
                payload["tools"] = self._convert_tools(request.tools)

        has_images = any(m.images for m in request.messages)
        if has_images and caps is not None and not caps.get("vision"):
            extra = (
                f"Model '{request.model}' is not a vision model; attached images "
                "will be ignored. Pick a vision-capable model (e.g. gemma4, "
                "llama4, qwen3.5-vl, llava) to analyse images."
            )
            notice = f"{notice} {extra}" if notice else extra
            for m in payload["messages"]:
                m.pop("images", None)

        think = self._think_param(request, caps)
        if think is not None:
            payload["think"] = think
        return payload, notice

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    async def chat(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        payload, notice = await self._prepare_payload(request, stream=True)
        if notice:
            yield StreamChunk(notice=notice)

        # Track whether we're currently inside a streamed thinking section
        # so we can wrap it in <think>…</think> tags for the frontend.
        in_thinking = False
        timeout = httpx.Timeout(self._timeout, read=None)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=payload,
                    headers=self._headers,
                ) as resp:
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        try:
                            detail = json.loads(body).get("error", body)
                        except Exception:
                            detail = body
                        yield StreamChunk(
                            error=f"Ollama HTTP {resp.status_code}: {detail}", done=True
                        )
                        return

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if "error" in data and not data.get("message"):
                            if in_thinking:
                                yield StreamChunk(content="</think>")
                            yield StreamChunk(error=f"Ollama: {data['error']}", done=True)
                            return
                        msg = data.get("message", {}) or {}

                        if msg.get("tool_calls"):
                            if in_thinking:
                                yield StreamChunk(content="</think>")
                                in_thinking = False
                            for tc in msg["tool_calls"]:
                                fn = tc.get("function", {})
                                yield StreamChunk(tool_call={
                                    "id": tc.get("id"),
                                    "name": fn.get("name"),
                                    "arguments": json.dumps(fn.get("arguments", {})),
                                    "complete": True,
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
                            return
                    # Stream ended without a done marker.
                    if in_thinking:
                        yield StreamChunk(content="</think>")
                    yield StreamChunk(done=True)
        except httpx.ConnectError:
            if in_thinking:
                yield StreamChunk(content="</think>")
            yield StreamChunk(
                error=f"Cannot connect to Ollama at {self.base_url}. Ensure it is running.",
                done=True,
            )
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            if in_thinking:
                yield StreamChunk(content="</think>")
            yield StreamChunk(error=f"Ollama error: {e}", done=True)

    async def chat_sync(self, request: ChatRequest) -> str:
        payload, _notice = await self._prepare_payload(request, stream=False)
        payload.pop("think", None)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout, read=None)) as client:
                resp = await client.post(
                    f"{self.base_url}/api/chat", json=payload,
                    headers=self._headers,
                )
                data = resp.json()
                if "error" in data:
                    return f"Error: {data['error']}"
                return data.get("message", {}).get("content", "")
        except Exception as e:
            return f"Error: {e}"
