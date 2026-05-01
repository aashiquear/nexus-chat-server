"""
MCP client – connects to remote MCP servers via HTTP JSON-RPC 2.0.

Each MCP server exposes tools through:
  POST /rpc  — JSON-RPC 2.0 endpoint
    • initialize        → handshake
    • tools/list         → discover available tools
    • tools/call         → execute a tool

MCPClient wraps a single server; MCPManager owns all configured servers.
"""

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

USER_MCP_SERVERS_PATH = Path("./data/user_mcp_servers.json")


def _load_user_mcp_servers() -> dict:
    """Load user-added MCP servers from persistent JSON."""
    if not USER_MCP_SERVERS_PATH.exists():
        return {}
    try:
        with open(USER_MCP_SERVERS_PATH) as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to load user MCP servers: %s", e)
        return {}


def _save_user_mcp_servers(servers: dict) -> None:
    """Persist user-added MCP servers to JSON."""
    USER_MCP_SERVERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(USER_MCP_SERVERS_PATH, "w") as f:
        json.dump(servers, f, indent=2)


class MCPClient:
    """Client for a single MCP server."""

    def __init__(self, server_id: str, config: dict):
        self.server_id = server_id
        self.url: str = config["url"].rstrip("/")
        self.name: str = config.get("name", server_id)
        self.description: str = config.get("description", "")
        self.icon: str = config.get("icon", "server")
        self._tools: list[dict] = []
        self._connected = False
        self._timeout = config.get("timeout", 30)

    # ------------------------------------------------------------------
    # JSON-RPC helper
    # ------------------------------------------------------------------
    async def _rpc(self, method: str, params: dict | None = None) -> Any:
        """Send a JSON-RPC 2.0 request to the MCP server."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self.url}/rpc",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                raise RuntimeError(
                    f"MCP error from {self.server_id}: {body['error']}"
                )
            return body.get("result")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def connect(self) -> bool:
        """Initialize connection and discover tools."""
        try:
            result = await self._rpc("initialize", {
                "client": "nexus-chat",
                "version": "0.1.0",
            })
            logger.info(
                "MCP server '%s' initialized: %s",
                self.server_id,
                result,
            )
            await self.refresh_tools()
            self._connected = True
            return True
        except Exception as e:
            logger.warning("Failed to connect to MCP server '%s': %s", self.server_id, e)
            self._connected = False
            return False

    async def refresh_tools(self) -> list[dict]:
        """Fetch the tool list from the server."""
        result = await self._rpc("tools/list")
        self._tools = result.get("tools", []) if result else []
        logger.info(
            "MCP server '%s' provides %d tool(s)", self.server_id, len(self._tools)
        )
        return self._tools

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool on the remote MCP server."""
        result = await self._rpc("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        if isinstance(result, str):
            return result
        return json.dumps(result)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def tools(self) -> list[dict]:
        return list(self._tools)

    async def health_check(self) -> bool:
        """Quick liveness check."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.url}/health")
                return resp.status_code == 200
        except Exception:
            return False


class MCPManager:
    """Manages all configured MCP server connections."""

    def __init__(self):
        self._clients: dict[str, MCPClient] = {}

    async def init_from_config(self, mcp_config: dict):
        """Create clients for every enabled MCP server in the config and user registry."""
        for server_id, cfg in mcp_config.items():
            if not cfg.get("enabled", False):
                continue
            client = MCPClient(server_id, cfg)
            connected = await client.connect()
            self._clients[server_id] = client
            if connected:
                logger.info("MCP server '%s' ready (%d tools)", server_id, len(client.tools))
            else:
                logger.warning("MCP server '%s' not reachable – will retry on demand", server_id)
        # Also load user-added servers
        user_servers = _load_user_mcp_servers()
        for server_id, cfg in user_servers.items():
            if not cfg.get("enabled", True):
                continue
            if server_id in self._clients:
                continue
            client = MCPClient(server_id, cfg)
            connected = await client.connect()
            self._clients[server_id] = client
            if connected:
                logger.info("User MCP server '%s' ready (%d tools)", server_id, len(client.tools))
            else:
                logger.warning("User MCP server '%s' not reachable – will retry on demand", server_id)

    async def add_server(self, server_id: str, cfg: dict) -> dict | None:
        """Connect to a new MCP server and register it. Returns server info or None on failure."""
        if server_id in self._clients:
            # reconnect / refresh existing
            client = self._clients[server_id]
            await client.connect()
        else:
            client = MCPClient(server_id, cfg)
            connected = await client.connect()
            if not connected:
                return None
            self._clients[server_id] = client
        return {
            "id": server_id,
            "name": client.name,
            "description": client.description,
            "url": client.url,
            "icon": client.icon,
            "connected": client.is_connected,
            "tools": [t["name"] for t in client.tools],
        }

    def remove_server(self, server_id: str) -> bool:
        """Remove a registered MCP server."""
        client = self._clients.pop(server_id, None)
        if client is None:
            return False
        # No persistent connection to close; just drop the reference
        return True

    # ------------------------------------------------------------------
    # Tool discovery
    # ------------------------------------------------------------------
    def get_all_tools(self) -> list[dict]:
        """Return tool definitions from all connected MCP servers.

        Each tool dict has:
          name, description, parameters (JSON Schema),
          plus _mcp_server to track provenance.
        """
        tools = []
        for server_id, client in self._clients.items():
            if not client.is_connected:
                continue
            for tool in client.tools:
                tools.append({
                    **tool,
                    "_mcp_server": server_id,
                })
        return tools

    def get_tool_info(self) -> list[dict]:
        """Return metadata suitable for the /api/tools endpoint.

        Supports the toolkit grouping protocol: tools with a ``toolkit``
        field are hidden from the sidebar.  Their parent toolkit entry
        (which has *no* ``toolkit`` field) gets a ``children`` list with
        the IDs of all grouped sub-tools so the frontend can
        activate/deactivate them together.

        MCP servers that do **not** use the ``toolkit`` field continue to
        work unchanged — every tool appears individually.
        """
        items = []
        for server_id, client in self._clients.items():
            # Build a mapping: toolkit_name -> [child tool IDs]
            toolkit_children: dict[str, list[str]] = defaultdict(list)
            for tool in client.tools:
                parent = tool.get("toolkit")
                if parent is not None:
                    toolkit_children[parent].append(
                        f"mcp:{server_id}:{tool['name']}"
                    )

            # Only expose sidebar-visible entries (those without a
            # ``toolkit`` field).
            for tool in client.tools:
                if tool.get("toolkit") is not None:
                    continue
                item = {
                    "id": f"mcp:{server_id}:{tool['name']}",
                    "name": tool.get("name", ""),
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
            # Try reconnecting once
            await client.connect()
            if not client.is_connected:
                return json.dumps({"error": f"MCP server '{server_id}' is not reachable"})
        return await client.call_tool(tool_name, arguments)

    def get_servers_info(self) -> list[dict]:
        """Return status info for all configured MCP servers."""
        servers = []
        for server_id, client in self._clients.items():
            servers.append({
                "id": server_id,
                "name": client.name,
                "description": client.description,
                "url": client.url,
                "icon": client.icon,
                "connected": client.is_connected,
                "tools": [t["name"] for t in client.tools],
            })
        return servers

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
