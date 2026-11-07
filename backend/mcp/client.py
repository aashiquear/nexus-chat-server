"""
MCP client – connects Nexus Chat to Model Context Protocol servers.

Transports
──────────
* ``streamable-http`` — the current MCP spec transport. JSON-RPC 2.0 is
  POSTed to the server URL; responses arrive either as JSON or as an SSE
  stream. The ``Mcp-Session-Id`` header is honoured.
* ``sse``             — legacy HTTP+SSE transport (``GET /sse`` yields an
  ``endpoint`` event; requests are POSTed there).
* ``stdio``           — spawn a local process (``npx -y @scope/server``,
  ``uvx some-mcp``, …) and speak newline-delimited JSON-RPC over its
  stdin/stdout. This is what GitHub/npm/PyPI marketplace servers and the
  ``mcpServers`` blocks from Claude Desktop / ChatGPT / Cursor configs use.
* ``legacy-rpc``      — Nexus's original simplified ``POST /rpc`` servers
  (see ``examples/mcp-database-server``). Kept for backwards compatibility.

``transport`` is auto-detected when not given: a ``command`` means stdio;
a URL ending in ``/sse`` means sse; otherwise streamable-http is tried
first and legacy ``/rpc`` as a fallback.

MCPClient wraps a single server; MCPManager owns all configured servers,
the user-added registry (``data/mcp_servers.json``) and runtime
add/remove. Resources exposed by servers can be read and ingested into
the RAG store (``MCPManager.read_resource``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "nexus-chat", "version": "0.3.0"}

USER_MCP_SERVERS_PATH = Path(os.environ.get("NEXUS_MCP_REGISTRY", "./data/mcp_servers.json"))


def _load_user_mcp_servers(path: Path | None = None) -> dict:
    """Load user-added MCP servers from persistent JSON."""
    path = path or USER_MCP_SERVERS_PATH
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("Failed to load user MCP servers: %s", e)
        return {}


def _save_user_mcp_servers(servers: dict, path: Path | None = None) -> None:
    """Persist user-added MCP servers to JSON."""
    path = path or USER_MCP_SERVERS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(servers, f, indent=2)
    tmp.replace(path)


def normalize_server_config(server_id: str, cfg: dict) -> dict:
    """Accept the many shapes an MCP server entry can take (Nexus YAML,
    Claude Desktop ``mcpServers``, registry packages) and return a
    canonical dict."""
    out = {
        "name": cfg.get("name") or server_id,
        "description": cfg.get("description", ""),
        "icon": cfg.get("icon", "server"),
        "timeout": int(cfg.get("timeout", 60) or 60),
        "enabled": cfg.get("enabled", True),
    }
    command = cfg.get("command")
    url = cfg.get("url") or cfg.get("serverUrl") or cfg.get("endpoint")
    transport = (cfg.get("transport") or cfg.get("type") or "").lower().replace("_", "-")
    if transport in ("http", "streamable_http", "streamablehttp"):
        transport = "streamable-http"
    if command:
        out["command"] = command
        out["args"] = list(cfg.get("args") or [])
        out["env"] = dict(cfg.get("env") or {})
        out["cwd"] = cfg.get("cwd")
        out["transport"] = "stdio"
    elif url:
        out["url"] = url.rstrip("/")
        out["headers"] = dict(cfg.get("headers") or {})
        out["transport"] = transport if transport in ("streamable-http", "sse", "legacy-rpc") else "auto"
    else:
        raise ValueError(f"MCP server '{server_id}' needs either a url or a command")
    for key in ("source", "registry_id", "package", "homepage", "owner", "scope"):
        if cfg.get(key) is not None:
            out[key] = cfg[key]
    return out


def _flatten_tool_result(result: Any) -> str:
    """Turn a ``tools/call`` result into text for the model/UI."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        parts = []
        for block in result["content"]:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "image":
                parts.append(json.dumps({"image": {"mimeType": block.get("mimeType"), "data": block.get("data")}}))
            elif btype == "resource":
                res = block.get("resource", {})
                parts.append(res.get("text") or json.dumps(res))
            else:
                parts.append(json.dumps(block))
        text = "\n".join(p for p in parts if p)
        if result.get("structuredContent") is not None and not text:
            text = json.dumps(result["structuredContent"])
        if result.get("isError"):
            return json.dumps({"error": text or "tool reported an error"})
        return text
    return json.dumps(result)


# ──────────────────────────────────────────────────────────────────────
# Transports
# ──────────────────────────────────────────────────────────────────────
class _RpcError(RuntimeError):
    pass


class _HttpTransport:
    """Streamable HTTP (and legacy /rpc) transport."""

    def __init__(self, url: str, headers: dict, timeout: int, legacy: bool = False):
        self.url = url
        self.headers = {"Content-Type": "application/json", **headers}
        self.timeout = timeout
        self.legacy = legacy
        self.session_id: str | None = None
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _endpoint(self) -> str:
        return f"{self.url}/rpc" if self.legacy else self.url

    async def request(self, method: str, params: dict | None = None) -> Any:
        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params or {}}
        headers = dict(self.headers)
        headers["Accept"] = "application/json, text/event-stream"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, read=None)) as client:
            async with client.stream("POST", self._endpoint(), json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise _RpcError(f"HTTP {resp.status_code} from {self._endpoint()}: {body[:300]}")
                sid = resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                ctype = resp.headers.get("content-type", "")
                if "text/event-stream" in ctype:
                    message = await self._read_sse_response(resp, payload["id"])
                else:
                    raw = await resp.aread()
                    if not raw.strip():
                        return None
                    message = json.loads(raw)
        return self._unwrap(message)

    async def notify(self, method: str, params: dict | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        headers = dict(self.headers)
        headers["Accept"] = "application/json, text/event-stream"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(self._endpoint(), json=payload, headers=headers)
        except Exception as e:  # notifications are best-effort
            logger.debug("MCP notify %s failed: %s", method, e)

    @staticmethod
    async def _read_sse_response(resp, want_id) -> dict:
        data_lines: list[str] = []
        async for line in resp.aiter_lines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif line == "" and data_lines:
                try:
                    msg = json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    data_lines = []
                    continue
                data_lines = []
                if isinstance(msg, dict) and msg.get("id") == want_id:
                    return msg
        if data_lines:
            msg = json.loads("\n".join(data_lines))
            if isinstance(msg, dict):
                return msg
        raise _RpcError("SSE stream ended without a response")

    @staticmethod
    def _unwrap(message: Any) -> Any:
        if isinstance(message, list):  # batch
            message = next((m for m in message if "result" in m or "error" in m), {})
        if isinstance(message, dict) and "error" in message:
            raise _RpcError(f"{message['error']}")
        return message.get("result") if isinstance(message, dict) else message

    async def close(self) -> None:
        if self.session_id and not self.legacy:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    await client.delete(self.url, headers={"Mcp-Session-Id": self.session_id})
            except Exception:
                pass


class _SseTransport:
    """Legacy HTTP+SSE transport: GET /sse gives an ``endpoint`` to POST to;
    responses stream back on the SSE channel."""

    def __init__(self, url: str, headers: dict, timeout: int):
        self.url = url
        self.headers = headers
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._endpoint: str | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._id = 0
        self._ready = asyncio.Event()

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, read=None), headers=self.headers)
        self._reader = asyncio.create_task(self._read_loop())
        await asyncio.wait_for(self._ready.wait(), timeout=self.timeout)

    async def _read_loop(self) -> None:
        assert self._client
        try:
            async with self._client.stream("GET", self.url, headers={"Accept": "text/event-stream"}) as resp:
                event = None
                data: list[str] = []
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif line == "":
                        if event == "endpoint" and data:
                            from urllib.parse import urljoin

                            self._endpoint = urljoin(self.url, "".join(data))
                            self._ready.set()
                        elif data:
                            try:
                                msg = json.loads("\n".join(data))
                            except json.JSONDecodeError:
                                msg = None
                            if isinstance(msg, dict) and msg.get("id") in self._pending:
                                fut = self._pending.pop(msg["id"])
                                if not fut.done():
                                    fut.set_result(msg)
                        event, data = None, []
        except Exception as e:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(_RpcError(f"SSE connection lost: {e}"))
            self._pending.clear()

    async def request(self, method: str, params: dict | None = None) -> Any:
        if not self._endpoint or not self._client:
            raise _RpcError("SSE transport not connected")
        self._id += 1
        rid = self._id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        resp = await self._client.post(self._endpoint, json=payload)
        if resp.status_code >= 400:
            self._pending.pop(rid, None)
            raise _RpcError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        msg = await asyncio.wait_for(fut, timeout=self.timeout)
        return _HttpTransport._unwrap(msg)

    async def notify(self, method: str, params: dict | None = None) -> None:
        if self._endpoint and self._client:
            try:
                await self._client.post(self._endpoint, json={"jsonrpc": "2.0", "method": method, "params": params or {}})
            except Exception:
                pass

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._client:
            await self._client.aclose()


class _StdioTransport:
    """Spawn a server process and exchange newline-delimited JSON-RPC."""

    def __init__(self, command: str, args: list[str], env: dict, cwd: str | None, timeout: int):
        self.command = command
        self.args = args
        self.env = env
        self.cwd = cwd
        self.timeout = timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._id = 0
        self._write_lock = asyncio.Lock()
        self.last_stderr: list[str] = []

    async def start(self) -> None:
        exe = shutil.which(self.command) or self.command
        if not (shutil.which(self.command) or Path(exe).exists()):
            raise _RpcError(
                f"Command '{self.command}' not found. Install it in the Nexus image "
                f"(node/npx, uv/uvx, docker) or use a remote URL."
            )
        env = dict(os.environ)
        env.update({k: str(v) for k, v in self.env.items()})
        self._proc = await asyncio.create_subprocess_exec(
            exe, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self.cwd or None,
        )
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())

    async def _stderr_loop(self) -> None:
        assert self._proc and self._proc.stderr
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            self.last_stderr.append(text)
            self.last_stderr = self.last_stderr[-50:]
            logger.debug("[mcp stdio %s] %s", self.command, text)

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("id") in self._pending and ("result" in msg or "error" in msg):
                fut = self._pending.pop(msg["id"])
                if not fut.done():
                    fut.set_result(msg)
            # Server-initiated requests (sampling, roots) are not supported;
            # answer with an error so the server doesn't hang.
            elif isinstance(msg, dict) and "method" in msg and "id" in msg:
                await self._write({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported"}})
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(_RpcError("MCP process exited: " + " | ".join(self.last_stderr[-3:])))
        self._pending.clear()

    async def _write(self, payload: dict) -> None:
        if not self._proc or not self._proc.stdin:
            raise _RpcError("stdio transport not running")
        async with self._write_lock:
            self._proc.stdin.write((json.dumps(payload) + "\n").encode())
            await self._proc.stdin.drain()

    async def request(self, method: str, params: dict | None = None) -> Any:
        if self._proc is None or self._proc.returncode is not None:
            raise _RpcError("MCP process is not running: " + " | ".join(self.last_stderr[-3:]))
        self._id += 1
        rid = self._id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        try:
            msg = await asyncio.wait_for(fut, timeout=self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise _RpcError(f"MCP '{method}' timed out after {self.timeout}s")
        return _HttpTransport._unwrap(msg)

    async def notify(self, method: str, params: dict | None = None) -> None:
        try:
            await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})
        except Exception:
            pass

    async def close(self) -> None:
        for t in (self._reader, self._stderr_task):
            if t:
                t.cancel()
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                self._proc.kill()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None


# ──────────────────────────────────────────────────────────────────────
# Client
# ──────────────────────────────────────────────────────────────────────
class MCPClient:
    """Client for a single MCP server."""

    def __init__(self, server_id: str, config: dict):
        self.server_id = server_id
        self.config = normalize_server_config(server_id, config)
        self.name: str = self.config["name"]
        self.description: str = self.config.get("description", "")
        self.icon: str = self.config.get("icon", "server")
        self.url: str = self.config.get("url", "")
        self.command: str | None = self.config.get("command")
        self.transport_name: str = self.config.get("transport", "auto")
        self._timeout = int(self.config.get("timeout", 60))
        self._tools: list[dict] = []
        self._resources: list[dict] = []
        self._server_info: dict = {}
        self._capabilities: dict = {}
        self._connected = False
        self._transport = None
        self.last_error: str | None = None
        # Which registry file this server is persisted in (set by the manager).
        self.registry_path: Path | None = None

    # ------------------------------------------------------------------
    async def _handshake(self, transport) -> dict:
        result = await transport.request("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        })
        await transport.notify("notifications/initialized")
        return result or {}

    async def connect(self) -> bool:
        """Initialize connection and discover tools."""
        await self._close_transport()
        attempts: list[tuple[str, Any]] = []
        if self.command:
            attempts.append(("stdio", _StdioTransport(
                self.command, self.config.get("args", []), self.config.get("env", {}),
                self.config.get("cwd"), self._timeout,
            )))
        else:
            headers = self.config.get("headers", {})
            t = self.transport_name
            if t == "sse" or (t == "auto" and self.url.rstrip("/").endswith("/sse")):
                attempts.append(("sse", _SseTransport(self.url, headers, self._timeout)))
            if t in ("streamable-http", "auto"):
                attempts.append(("streamable-http", _HttpTransport(self.url, headers, self._timeout)))
                if t == "auto" and not self.url.rstrip("/").endswith("/mcp"):
                    attempts.append(("streamable-http", _HttpTransport(self.url.rstrip("/") + "/mcp", headers, self._timeout)))
            if t in ("legacy-rpc", "auto"):
                attempts.append(("legacy-rpc", _HttpTransport(self.url, headers, self._timeout, legacy=True)))

        errors = []
        for name, transport in attempts:
            try:
                if hasattr(transport, "start"):
                    await transport.start()
                info = await self._handshake(transport)
                self._transport = transport
                self.transport_name = name
                self._server_info = info.get("serverInfo", {}) if isinstance(info, dict) else {}
                self._capabilities = info.get("capabilities", {}) if isinstance(info, dict) else {}
                if self._server_info.get("name") and self.config.get("name") == self.server_id:
                    self.name = self._server_info["name"]
                if name == "legacy-rpc" and isinstance(info, dict) and info.get("name") and self.config.get("name") == self.server_id:
                    self.name = info["name"]
                await self.refresh_tools()
                try:
                    await self.refresh_resources()
                except Exception:
                    self._resources = []
                self._connected = True
                self.last_error = None
                logger.info("MCP server '%s' connected via %s (%d tools)", self.server_id, name, len(self._tools))
                return True
            except Exception as e:
                errors.append(f"{name}: {e}")
                try:
                    await transport.close()
                except Exception:
                    pass
        self._connected = False
        self.last_error = "; ".join(errors)[:600]
        logger.warning("Failed to connect to MCP server '%s': %s", self.server_id, self.last_error)
        return False

    async def _close_transport(self) -> None:
        if self._transport is not None:
            try:
                await self._transport.close()
            except Exception:
                pass
            self._transport = None

    async def close(self) -> None:
        await self._close_transport()
        self._connected = False

    async def refresh_tools(self) -> list[dict]:
        """Fetch the tool list from the server (handles pagination)."""
        tools: list[dict] = []
        cursor = None
        for _ in range(20):
            params = {"cursor": cursor} if cursor else {}
            result = await self._transport.request("tools/list", params)
            batch = (result or {}).get("tools", []) if isinstance(result, dict) else []
            for t in batch:
                t = dict(t)
                # Standard MCP uses ``inputSchema``; Nexus legacy used ``parameters``.
                if "parameters" not in t:
                    t["parameters"] = t.get("inputSchema") or {"type": "object", "properties": {}}
                tools.append(t)
            cursor = (result or {}).get("nextCursor") if isinstance(result, dict) else None
            if not cursor:
                break
        self._tools = tools
        logger.info("MCP server '%s' provides %d tool(s)", self.server_id, len(self._tools))
        return self._tools

    async def refresh_resources(self) -> list[dict]:
        if "resources" not in self._capabilities and self.transport_name != "legacy-rpc":
            self._resources = []
            return []
        result = await self._transport.request("resources/list", {})
        self._resources = (result or {}).get("resources", []) if isinstance(result, dict) else []
        return self._resources

    async def read_resource(self, uri: str) -> dict:
        result = await self._transport.request("resources/read", {"uri": uri})
        contents = (result or {}).get("contents", []) if isinstance(result, dict) else []
        texts, blobs = [], []
        for c in contents:
            if c.get("text") is not None:
                texts.append(c["text"])
            elif c.get("blob"):
                blobs.append({"mimeType": c.get("mimeType"), "blob": c["blob"]})
        return {"uri": uri, "text": "\n".join(texts), "blobs": blobs, "raw": contents}

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool on the remote MCP server."""
        if self._transport is None:
            raise _RpcError("not connected")
        result = await self._transport.request("tools/call", {
            "name": tool_name,
            "arguments": arguments or {},
        })
        return _flatten_tool_result(result)

    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        if isinstance(self._transport, _StdioTransport) and not self._transport.alive:
            self._connected = False
        return self._connected

    @property
    def tools(self) -> list[dict]:
        return list(self._tools)

    @property
    def resources(self) -> list[dict]:
        return list(self._resources)

    def info(self) -> dict:
        return {
            "id": self.server_id,
            "name": self.name,
            "description": self.description or self._server_info.get("title", ""),
            "url": self.url or f"{self.command} {' '.join(self.config.get('args', []))}".strip(),
            "transport": self.transport_name,
            "icon": self.icon,
            "connected": self.is_connected,
            "tools": [t["name"] for t in self._tools],
            "resources": len(self._resources),
            "server_info": self._server_info,
            "source": self.config.get("source", "config"),
            "owner": self.config.get("owner"),
            "error": self.last_error,
        }

    async def health_check(self) -> bool:
        """Quick liveness check."""
        if self._transport is None:
            return False
        try:
            await self._transport.request("ping", {})
            return True
        except Exception:
            return self.is_connected


# ──────────────────────────────────────────────────────────────────────
# Manager
# ──────────────────────────────────────────────────────────────────────
class MCPManager:
    """Manages all configured MCP server connections."""

    def __init__(self, registry_path: Path | None = None):
        self._clients: dict[str, MCPClient] = {}
        self._registry_path = registry_path or USER_MCP_SERVERS_PATH

    async def init_from_config(self, mcp_config: dict):
        """Create clients for every enabled MCP server in the config and
        the user-added registry. Connections are attempted in parallel."""
        entries: list[tuple[str, dict]] = []
        for server_id, cfg in (mcp_config or {}).items():
            if cfg.get("enabled", False):
                entries.append((server_id, {**cfg, "source": "config"}))
        for server_id, cfg in _load_user_mcp_servers(self._registry_path).items():
            if cfg.get("enabled", True) and server_id not in dict(entries):
                entries.append((server_id, {**cfg, "source": cfg.get("source", "user")}))

        async def _connect(server_id, cfg):
            try:
                client = MCPClient(server_id, cfg)
            except ValueError as e:
                logger.warning("Skipping MCP server '%s': %s", server_id, e)
                return
            if cfg.get("source") != "config":
                client.registry_path = self._registry_path
            self._clients[server_id] = client
            ok = await client.connect()
            if ok:
                logger.info("MCP server '%s' ready (%d tools)", server_id, len(client.tools))
            else:
                logger.warning("MCP server '%s' not reachable – will retry on demand", server_id)

        await asyncio.gather(*[_connect(sid, cfg) for sid, cfg in entries], return_exceptions=True)

    async def load_registry(self, path: Path, defaults: dict | None = None) -> None:
        """Connect every server stored in an additional registry file
        (e.g. a per-user registry in the multi-user server edition).
        ``defaults`` are merged into each entry (owner, scope, …)."""
        entries = _load_user_mcp_servers(path)

        async def _connect(server_id, cfg):
            if server_id in self._clients:
                return
            try:
                client = MCPClient(server_id, {**(defaults or {}), **cfg})
            except ValueError as e:
                logger.warning("Skipping MCP server '%s': %s", server_id, e)
                return
            client.registry_path = path
            self._clients[server_id] = client
            await client.connect()

        await asyncio.gather(*[_connect(sid, cfg) for sid, cfg in entries.items() if cfg.get("enabled", True)], return_exceptions=True)

    async def add_server(
        self, server_id: str, cfg: dict, persist: bool = True, registry_path: Path | None = None
    ) -> dict | None:
        """Connect to a new MCP server and register it. Returns server info
        or None on failure. ``registry_path`` selects which JSON registry
        the entry is persisted in (default: the manager's)."""
        if server_id in self._clients:
            await self._clients[server_id].close()
        client = MCPClient(server_id, cfg)
        client.registry_path = registry_path or self._registry_path
        connected = await client.connect()
        self._clients[server_id] = client
        if persist:
            self._persist(server_id, client.config, client.registry_path)
        return client.info() if connected else None

    def _persist(self, server_id: str, cfg: dict, path: Path | None = None) -> None:
        path = path or self._registry_path
        servers = _load_user_mcp_servers(path)
        servers[server_id] = {k: v for k, v in cfg.items() if v not in (None, "", [], {})}
        _save_user_mcp_servers(servers, path)

    async def remove_server(self, server_id: str, registry_path: Path | None = None) -> bool:
        """Remove a registered MCP server."""
        client = self._clients.pop(server_id, None)
        path = registry_path or getattr(client, "registry_path", None) or self._registry_path
        servers = _load_user_mcp_servers(path)
        existed = servers.pop(server_id, None) is not None
        if existed:
            _save_user_mcp_servers(servers, path)
        if client is None:
            return existed
        await client.close()
        return True

    def get_client(self, server_id: str) -> MCPClient | None:
        return self._clients.get(server_id)

    # ------------------------------------------------------------------
    # Tool discovery
    # ------------------------------------------------------------------
    def get_all_tools(self, visible_servers: set[str] | None = None) -> list[dict]:
        """Return tool definitions from all connected MCP servers."""
        tools = []
        for server_id, client in self._clients.items():
            if visible_servers is not None and server_id not in visible_servers:
                continue
            if not client.is_connected:
                continue
            for tool in client.tools:
                tools.append({**tool, "_mcp_server": server_id})
        return tools

    def get_tool_info(self, visible_servers: set[str] | None = None) -> list[dict]:
        """Return metadata suitable for the /api/tools endpoint.

        Supports the toolkit grouping protocol: tools with a ``toolkit``
        field are hidden from the sidebar.  Their parent toolkit entry
        (which has *no* ``toolkit`` field) gets a ``children`` list with
        the IDs of all grouped sub-tools so the frontend can
        activate/deactivate them together.
        """
        items = []
        for server_id, client in self._clients.items():
            if visible_servers is not None and server_id not in visible_servers:
                continue
            toolkit_children: dict[str, list[str]] = defaultdict(list)
            for tool in client.tools:
                parent = tool.get("toolkit")
                if parent is not None:
                    toolkit_children[parent].append(f"mcp:{server_id}:{tool['name']}")

            for tool in client.tools:
                if tool.get("toolkit") is not None:
                    continue
                item = {
                    "id": f"mcp:{server_id}:{tool['name']}",
                    "name": tool.get("title") or tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "icon": client.icon,
                    "source": "mcp",
                    "server": server_id,
                    "server_name": client.name,
                    "connected": client.is_connected,
                }
                children = toolkit_children.get(tool["name"])
                if children:
                    item["children"] = children
                items.append(item)
        return items

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict) -> str:
        """Route a tool call to the correct MCP server."""
        client = self._clients.get(server_id)
        if not client:
            return json.dumps({"error": f"MCP server '{server_id}' not found"})
        if not client.is_connected:
            await client.connect()
            if not client.is_connected:
                return json.dumps({"error": f"MCP server '{server_id}' is not reachable: {client.last_error}"})
        try:
            return await client.call_tool(tool_name, arguments)
        except Exception as e:
            # One retry after reconnect (stdio servers may have died).
            if await client.connect():
                try:
                    return await client.call_tool(tool_name, arguments)
                except Exception as e2:
                    return json.dumps({"error": str(e2)})
            return json.dumps({"error": str(e)})

    async def list_resources(self, server_id: str) -> list[dict]:
        client = self._clients.get(server_id)
        if not client or not client.is_connected:
            return []
        try:
            return await client.refresh_resources()
        except Exception as e:
            logger.warning("resources/list failed for %s: %s", server_id, e)
            return client.resources

    async def read_resource(self, server_id: str, uri: str) -> dict:
        client = self._clients.get(server_id)
        if not client or not client.is_connected:
            raise RuntimeError(f"MCP server '{server_id}' is not connected")
        return await client.read_resource(uri)

    def get_servers_info(self, visible_servers: set[str] | None = None) -> list[dict]:
        """Return status info for all configured MCP servers."""
        return [
            c.info() for sid, c in self._clients.items()
            if visible_servers is None or sid in visible_servers
        ]

    async def reconnect(self, server_id: str) -> bool:
        """Attempt to reconnect a specific MCP server."""
        client = self._clients.get(server_id)
        if not client:
            return False
        return await client.connect()

    async def reconnect_all(self):
        """Attempt to reconnect all disconnected servers."""
        for client in self._clients.values():
            if not client.is_connected:
                await client.connect()

    async def shutdown(self):
        for client in self._clients.values():
            await client.close()


# ──────────────────────────────────────────────────────────────────────
# Registry / marketplace helpers
# ──────────────────────────────────────────────────────────────────────
OFFICIAL_REGISTRY_URL = os.environ.get(
    "MCP_REGISTRY_URL", "https://registry.modelcontextprotocol.io/v0.1/servers"
)


async def search_registry(query: str = "", limit: int = 30, cursor: str | None = None) -> dict:
    """Search the official MCP Registry (the same catalog behind the
    GitHub / Claude / ChatGPT MCP marketplaces) and return installable
    candidates with a ready-to-use Nexus config for each."""
    params: dict[str, Any] = {"limit": min(max(limit, 1), 100)}
    if query:
        params["search"] = query
    if cursor:
        params["cursor"] = cursor
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(OFFICIAL_REGISTRY_URL, params=params, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
    servers = data.get("servers", []) if isinstance(data, dict) else []
    results = [_registry_entry_to_candidate(s) for s in servers]
    meta = data.get("metadata", {}) if isinstance(data, dict) else {}
    return {"results": [r for r in results if r], "next_cursor": meta.get("nextCursor") or meta.get("next_cursor")}


def _registry_entry_to_candidate(entry: dict) -> dict | None:
    server = entry.get("server", entry)
    name = server.get("name") or ""
    if not name:
        return None
    options: list[dict] = []
    for remote in server.get("remotes", []) or []:
        rtype = (remote.get("type") or remote.get("transportType") or "").lower()
        url = remote.get("url")
        if not url:
            continue
        transport = "sse" if "sse" in rtype else "streamable-http"
        options.append({
            "kind": "remote",
            "label": f"Remote ({transport})",
            "config": {"url": url, "transport": transport,
                       "headers": {h.get("name"): h.get("value", "") for h in remote.get("headers", []) if h.get("name")}},
            "env_vars": [],
        })
    for pkg in server.get("packages", []) or []:
        reg = (pkg.get("registryType") or pkg.get("registry_name") or pkg.get("registryName") or "").lower()
        ident = pkg.get("identifier") or pkg.get("name")
        if not ident:
            continue
        version = pkg.get("version")
        env_vars = [
            {"name": e.get("name"), "description": e.get("description", ""), "required": e.get("isRequired", False), "secret": e.get("isSecret", False)}
            for e in (pkg.get("environmentVariables") or []) if e.get("name")
        ]
        runtime_args = [a.get("value") or a.get("default") for a in (pkg.get("packageArguments") or []) if (a.get("value") or a.get("default"))]
        if reg == "npm":
            spec = f"{ident}@{version}" if version else ident
            cfg = {"command": "npx", "args": ["-y", spec, *runtime_args]}
        elif reg in ("pypi", "pip"):
            spec = f"{ident}=={version}" if version else ident
            cfg = {"command": "uvx", "args": [spec, *runtime_args]}
        elif reg in ("oci", "docker"):
            cfg = {"command": "docker", "args": ["run", "-i", "--rm", ident, *runtime_args]}
        else:
            continue
        cfg["env"] = {e["name"]: "" for e in env_vars}
        options.append({"kind": reg, "label": f"{reg} package: {ident}", "config": cfg, "env_vars": env_vars})
    repo = server.get("repository", {}) or {}
    return {
        "id": name,
        "name": server.get("title") or name.split("/")[-1],
        "description": server.get("description", ""),
        "version": server.get("version"),
        "repository": repo.get("url"),
        "homepage": server.get("websiteUrl") or repo.get("url"),
        "options": options,
        "installable": bool(options),
    }


def parse_mcp_config_import(payload: Any) -> dict[str, dict]:
    """Parse an ``mcpServers`` config (Claude Desktop, ChatGPT/Cursor/VS
    Code style, or a Nexus YAML-ish dict) into {server_id: config}."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    servers = payload.get("mcpServers") or payload.get("mcp_servers") or payload.get("servers") or payload
    if not isinstance(servers, dict):
        raise ValueError("Expected an object keyed by server name")
    out: dict[str, dict] = {}
    for sid, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        cfg = dict(cfg)
        if isinstance(cfg.get("command"), str) and not cfg.get("args") and " " in cfg["command"]:
            parts = shlex.split(cfg["command"])
            cfg["command"], cfg["args"] = parts[0], parts[1:]
        try:
            out[_slug(sid)] = normalize_server_config(_slug(sid), {**cfg, "source": "import"})
        except ValueError as e:
            logger.warning("Skipping imported MCP server %s: %s", sid, e)
    return out


def _slug(text: str) -> str:
    import re

    slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", text).strip("-").lower()
    return slug or "server"
