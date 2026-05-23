"""
Chat Orchestrator - Core logic that ties LLM providers, tools, and RAG together.
Handles the agentic loop: LLM → tool call → result → LLM.
"""

import json
import logging
from typing import AsyncIterator

from backend.config import get_config, get_enabled_providers, get_enabled_tools, get_enabled_mcp_servers
from backend.providers import (
    BaseLLMProvider, ChatRequest, Message, StreamChunk, ToolDefinition,
    get_provider_class,
)
from backend.tools import BaseTool, get_tool_class
from backend.mcp import MCPManager
from backend.rag.engine import RAGEngine

logger = logging.getLogger(__name__)


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
        for name, cfg in get_enabled_tools().items():
            cls = get_tool_class(name)
            if cls:
                tool = cls(cfg.get("config", {}))
                self._tools[name] = tool
                logger.info(f"Tool '{name}' loaded")

    def _init_rag(self):
        rag_config = self.config.get("rag", {})
        if rag_config.get("enabled", False):
            self._rag = RAGEngine(rag_config)
            logger.info("RAG engine initialized")

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
        and marks each entry with ``remote_available``. Probes run in
        parallel and any failure falls back to "no info" gracefully."""
        import asyncio

        active = list(self._providers.items())
        # Probe every provider in parallel; failures are normalized to
        # None ("no info"), in which case the UI shows the model
        # without a remote-availability flag.
        remote_lists = await asyncio.gather(
            *[p.list_remote_models() for _, p in active],
            return_exceptions=True,
        )

        result = []
        for (provider_name, provider), remote in zip(active, remote_lists):
            # Probe failures and "no implementation" both surface as
            # None so the UI shows those models as available (not
            # spuriously greyed out). An empty set, by contrast, means
            # the probe succeeded but reported no models.
            if isinstance(remote, Exception):
                remote = None
            elif not (remote is None or isinstance(remote, set)):
                remote = None
            for model in provider.list_models():
                result.append(
                    self._format_model_entry(provider_name, provider, model, remote)
                )
        return result

    @staticmethod
    def _format_model_entry(provider_name, provider, model, remote_models):
        """Build the dict shape returned by /api/models for one model."""
        mid = model["id"]
        entry = {
            "id": mid,
            "name": model.get("name", mid),
            "provider": provider_name,
            "available": provider.is_available(),
            "thinking": bool(
                model.get("thinking")
                or model.get("thinking_level")
                or model.get("thinking_budget_tokens")
            ),
        }
        # ``remote_available`` is None ("unknown") when the provider
        # didn't report a catalog. The UI treats unknown as "available"
        # so providers without a probe don't get falsely greyed out.
        if remote_models is None:
            entry["remote_available"] = None
        else:
            entry["remote_available"] = mid in remote_models
        return entry

    async def init_mcp(self):
        """Initialize MCP server connections (must be awaited)."""
        mcp_config = get_enabled_mcp_servers()
        if mcp_config:
            await self._mcp.init_from_config(mcp_config)
            logger.info("MCP manager initialized with %d server(s)", len(mcp_config))

    def get_available_tools(self) -> list[dict]:
        """Return all available tools (built-in + MCP)."""
        tool_configs = get_enabled_tools()
        result = []
        for name, tool in self._tools.items():
            cfg = tool_configs.get(name, {})
            result.append({
                "id": name,
                "name": cfg.get("name", name),
                "description": cfg.get("description", tool.description),
                "icon": cfg.get("icon", "wrench"),
                "source": "builtin",
            })
        # Append MCP tools
        result.extend(self._mcp.get_tool_info())
        return result

    def get_mcp_servers(self) -> list[dict]:
        """Return status of all MCP servers."""
        return self._mcp.get_servers_info()

    async def reconnect_mcp(self, server_id: str) -> bool:
        """Reconnect a specific MCP server."""
        return await self._mcp.reconnect(server_id)

    def _resolve_provider(self, model_id: str) -> tuple[str, BaseLLMProvider] | None:
        """Find which provider hosts the given model."""
        for name, provider in self._providers.items():
            for model in provider.list_models():
                if model["id"] == model_id:
                    return name, provider
        return None

    def _resolve_thinking_config(self, provider: BaseLLMProvider, model_id: str) -> dict | None:
        """Look up per-model thinking config so non-thinking models stay
        untouched while thinking-capable models get their required params.

        A model entry in settings.yaml may declare any of:
          thinking: true | false        — toggle thinking
          thinking_level: low|medium|high
          thinking_budget_tokens: N     — Anthropic extended-thinking budget
        """
        for model in provider.list_models():
            if model.get("id") != model_id:
                continue
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
        return None

    async def _execute_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool (built-in or MCP) and return its result."""
        # Check if it's an MCP tool (format: mcp:<server>:<name>)
        if tool_name.startswith("mcp:"):
            parts = tool_name.split(":", 2)
            if len(parts) == 3:
                _, server_id, remote_name = parts
                try:
                    return await self._mcp.call_tool(server_id, remote_name, arguments)
                except Exception as e:
                    return json.dumps({"error": str(e)})

        tool = self._tools.get(tool_name)
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
            # MCP tools do not support streaming yet
            result = await self._execute_tool(tool_name, arguments)
            yield {"type": "tool_result", "name": tool_name, "result": result}
            return

        tool = self._tools.get(tool_name)
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

            # Build final result.
            if hasattr(tool, "_last_output"):
                # code_executor stashes extra state on the instance —
                # surface it (return code, optional interactive session
                # id) alongside the streamed output so the frontend can
                # auto-open the right canvas view.
                result_dict = {
                    "output": "\n".join(chunks).strip() or "(no output)",
                    "return_code": getattr(tool, "_last_return_code", 0),
                }
                session_id = getattr(tool, "_last_session_id", None)
                if session_id:
                    result_dict["interactive_session"] = session_id
                result = json.dumps(result_dict)
            elif len(chunks) == 1:
                # Single-chunk tools (the default BaseTool.stream_execute
                # path) yield their entire result as one chunk — pass it
                # through verbatim so structured JSON (figure_json,
                # plot_image, downloadable, svg, ...) reaches the frontend
                # intact instead of being wrapped in ``{"result": "<json
                # string>"}`` and double-encoded.
                result = chunks[0]
            else:
                # True multi-chunk streamer with no ``_last_output`` —
                # concatenate the streamed lines into one text payload.
                result = json.dumps({"result": "\n".join(chunks)})

            yield {"type": "tool_result", "name": tool_name, "result": result}
        except Exception as e:
            yield {
                "type": "tool_result",
                "name": tool_name,
                "result": json.dumps({"error": str(e)}),
            }

    async def _get_rag_context(self, query: str, files: list[str] | None) -> str:
        """Retrieve relevant context from RAG if enabled."""
        if not self._rag or not files:
            return ""
        results = await self._rag.query(query, filenames=files)
        if not results:
            return ""
        context_parts = []
        for r in results:
            source = r["metadata"].get("source", "unknown")
            context_parts.append(f"[From {source}]:\n{r['content']}")
        return "\n\n---\n\n".join(context_parts)

    async def chat_stream(
        self,
        messages: list[dict],
        model_id: str,
        selected_tools: list[str] | None = None,
        selected_files: list[str] | None = None,
        system_prompt: str = "",
    ) -> AsyncIterator[dict]:
        """
        Stream a chat response, handling tool calls in an agentic loop.

        Yields dicts with:
          {"type": "status", "stage": "initiated|thinking|responding|tool_calling|tool_executing"}
          {"type": "text", "content": "..."}
          {"type": "tool_call", "name": "...", "arguments": {...}}
          {"type": "tool_result", "name": "...", "result": "..."}
          {"type": "done"}
          {"type": "error", "content": "..."}
        """
        # Surface "initiated" immediately so the UI can flip from idle to
        # active before the provider's first token arrives (some models
        # spend several seconds in queue/warmup).
        yield {"type": "status", "stage": "initiated"}

        # Resolve provider
        resolved = self._resolve_provider(model_id)
        if not resolved:
            yield {"type": "error", "content": f"Model '{model_id}' not found"}
            return

        provider_name, provider = resolved
        if not provider.is_available():
            yield {"type": "error", "content": f"Provider '{provider_name}' is not configured. Check your API key."}
            return

        # Build tool definitions (built-in + MCP)
        tool_defs = []
        if selected_tools:
            for tool_id in selected_tools:
                if tool_id.startswith("mcp:"):
                    # MCP tool — find its definition from the manager
                    parts = tool_id.split(":", 2)
                    if len(parts) == 3:
                        for mcp_tool in self._mcp.get_all_tools():
                            if mcp_tool.get("_mcp_server") == parts[1] and mcp_tool["name"] == parts[2]:
                                tool_defs.append(ToolDefinition(
                                    name=tool_id,
                                    description=mcp_tool.get("description", ""),
                                    parameters=mcp_tool.get("parameters", {"type": "object", "properties": {}}),
                                ))
                                break
                else:
                    tool = self._tools.get(tool_id)
                    if tool:
                        td = tool.to_definition()
                        tool_defs.append(ToolDefinition(**td))

        # Get RAG context
        last_user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break

        rag_context = await self._get_rag_context(last_user_msg, selected_files)

        # Build system prompt with RAG context
        full_system = system_prompt or "You are a helpful AI assistant."
        if rag_context:
            full_system += (
                "\n\n--- Relevant Context from Uploaded Files ---\n"
                + rag_context
                + "\n--- End Context ---\n"
                "Use the above context to help answer the user's question."
            )

        # Convert messages
        msg_objs = [Message(role=m["role"], content=m["content"]) for m in messages]

        # Build request
        thinking_cfg = self._resolve_thinking_config(provider, model_id)
        request = ChatRequest(
            messages=msg_objs,
            model=model_id,
            tools=tool_defs,
            system_prompt=full_system,
            stream=True,
            thinking=thinking_cfg,
        )

        # Agentic loop — up to 15 rounds on success, stops after 5 consecutive failures
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        max_rounds = 15
        max_consecutive_failures = 5
        consecutive_failures = 0

        for _round in range(max_rounds):
            collected_text = ""
            collected_tool_call = None
            tool_call_buffer = ""

            async for chunk in provider.chat(request):
                if chunk.content:
                    collected_text += chunk.content
                    yield {"type": "text", "content": chunk.content}

                if chunk.tool_call:
                    tc = chunk.tool_call
                    if "name" in tc and tc["name"]:
                        collected_tool_call = {"name": tc["name"], "arguments": ""}
                    if "arguments" in tc:
                        tool_call_buffer += tc["arguments"]
                    if "partial" in tc:
                        tool_call_buffer += tc["partial"]

                if chunk.done:
                    if chunk.usage:
                        total_usage["prompt_tokens"] += chunk.usage.get("prompt_tokens", 0)
                        total_usage["completion_tokens"] += chunk.usage.get("completion_tokens", 0)
                        total_usage["total_tokens"] += chunk.usage.get("total_tokens", 0)
                    break

            # If no tool call, we're done
            if not collected_tool_call:
                done_event = {"type": "done"}
                if total_usage["total_tokens"] > 0:
                    done_event["usage"] = total_usage
                yield done_event
                return

            # Execute tool
            collected_tool_call["arguments"] = tool_call_buffer
            try:
                args = json.loads(tool_call_buffer) if tool_call_buffer else {}
            except json.JSONDecodeError:
                args = {}

            yield {
                "type": "tool_call",
                "name": collected_tool_call["name"],
                "arguments": args,
            }

            async for event in self._stream_tool(collected_tool_call["name"], args):
                yield event
                if event["type"] == "tool_result":
                    result = event["result"]

            # Track consecutive failures
            try:
                parsed_result = json.loads(result)
                if isinstance(parsed_result, dict) and "error" in parsed_result:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0
            except (json.JSONDecodeError, TypeError):
                consecutive_failures = 0

            if consecutive_failures >= max_consecutive_failures:
                yield {"type": "text", "content": "\n\n*Stopping: too many consecutive tool failures.*"}
                break

            # Append assistant + tool result to messages for next round
            msg_objs.append(Message(role="assistant", content=collected_text or f"Using tool: {collected_tool_call['name']}"))
            msg_objs.append(Message(role="user", content=f"Tool '{collected_tool_call['name']}' returned:\n{result}"))
            request.messages = msg_objs

        done_event = {"type": "done"}
        if total_usage["total_tokens"] > 0:
            done_event["usage"] = total_usage
        yield done_event

    @property
    def rag_engine(self) -> RAGEngine | None:
        return self._rag
