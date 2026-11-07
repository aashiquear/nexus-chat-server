# Nexus Chat Server

Multi-user edition of [Nexus Chat](https://github.com/aashiquear/nexus-chat): the same agentic chatbot (Ollama / Claude / GPT, vision, multi-language sandbox, MCP marketplace, RAG, viewers, generation & context controls) wrapped in a server layer with an **admin account**, **per-user accounts** and **private per-user workspaces**.

This repository tracks `nexus-chat` as its upstream. Everything that is not server-specific lives upstream; the server layer is kept as a small, regenerable patch (`scripts/server-patches/`) so upstream updates can be pulled in with one script.

## What the server edition adds

| Area | Base `nexus-chat` | `nexus-chat-server` |
|---|---|---|
| Accounts | none (single user) | admin bootstrap from `NEXUS_ADMIN_PASSWORD`, users managed in the Admin Panel, bcrypt + JWT (bearer **and** HttpOnly cookie so previews work) |
| Files | `data/uploads`, `data/files`, … shared | `data/users/<user>/{uploads,files,downloads,plots,conversations}` — every tool, preview and download is scoped to the signed-in user |
| RAG | one shared index | one index, every chunk tagged with its `owner`; queries/deletes are filtered per user |
| Conversations | `data/conversations/` | per user, including compaction state and tool/file selection |
| Generation settings | per browser (localStorage) | per user on the server (`settings.json`), follow the account across devices |
| MCP servers | one registry | admin-managed **server-wide** servers + **personal** servers per user (`users_can_add_mcp`, `users_can_run_stdio_mcp` policies) |
| Sandbox | shared | runs with the user's workspace; interactive consoles are bound to their owner |
| Quotas | — | optional per-user storage quota (`auth.user_quota_mb`) |

## Quick Start

```bash
git clone https://github.com/aashiquear/nexus-chat-server.git
cd nexus-chat-server
cp .env.example .env            # set NEXUS_ADMIN_PASSWORD and NEXUS_JWT_SECRET
docker compose up --build       # http://localhost:8000 → sign in as "admin"
```

Then open the **Admin Panel** (sidebar) to create user accounts. Each user signs in with their own credentials and only sees their own conversations, uploads, generated files and personal MCP servers.

### Configuration

`config/settings.yaml` is the upstream file (do not edit it — it is replaced on updates). Server-specific settings live in `config/settings.server.yaml`, which is deep-merged on top:

```yaml
auth:
  jwt_secret: "${NEXUS_JWT_SECRET:-change-me-in-production}"
  token_expire_days: 7
  user_quota_mb: 0                # 0 = unlimited
  users_can_add_mcp: true         # personal (remote URL) MCP servers
  users_can_run_stdio_mcp: false  # stdio servers spawn processes → admins only
providers:
  anthropic: { enabled: ${NEXUS_ENABLE_ANTHROPIC:-false} }
  openai:    { enabled: ${NEXUS_ENABLE_OPENAI:-false} }
```

Cloud providers are off by default on a shared server; enable them in `.env` with `NEXUS_ENABLE_ANTHROPIC=true` / `NEXUS_ENABLE_OPENAI=true` plus the API keys.

### Data layout & host filesystem notes

```
data/
├── users.json                  # accounts (bcrypt hashes)
├── users/<username>/
│   ├── uploads/  files/  downloads/  plots/  sandbox/
│   ├── conversations/*.json
│   ├── mcp_servers.json        # personal MCP servers
│   └── settings.json           # generation defaults etc.
├── mcp_servers.json            # admin-added server-wide MCP servers
└── vector_store/               # ChromaDB (chunks tagged with owner)
```

- `docker-compose.yml` stores `data/` in a **named volume** (`nexus-data`). If you switch to a bind mount (`./data:/app/data`), the host directory must be writable by the container user; the server checks this at startup and fails with an explicit message instead of producing blank screens later.
- Usernames are limited to `[A-Za-z0-9._-]` (max 32) so they are always safe directory names.
- Workspaces are created on user creation **and** on every login, so a restored backup or recreated volume heals itself.
- Older layouts are migrated automatically on startup: `data/conversations/<user>/` moves into the user's workspace and files left in the shared `data/uploads/` are assigned to the first admin.
- Deleting a user (admin panel or self-service) removes their workspace, vectors and personal MCP servers.

### Updating from upstream

```bash
scripts/update-from-upstream.sh      # fetch upstream, merge, re-apply server patch
scripts/regenerate-server-patch.sh   # after editing server-only code: refresh the patch
docker compose build && docker compose up -d
```

The scripts default to `upstream/main`; set `UPSTREAM_REF=upstream/<branch>` to track another branch.

---

Below is the upstream feature documentation (shared with `nexus-chat`).

## Quick Start

### Option 1: Docker (Recommended)

```bash
# 1. Clone the repository
git clone https://github.com/aashiquear/nexus-chat.git
cd nexus-chat

# 2. Configure
cp .env.example .env
# Edit .env and add your API keys

# 3. Run
docker compose up --build

# Open http://localhost:8000
```

### Option 2: Local Development

**Prerequisites:** Python 3.11+, Node.js 18+

```bash
# Linux / macOS
bash start_dev.sh

# Windows
start_dev.bat
```

Or manually:

```bash
# Backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Frontend
cd frontend && npm install && npm run build && cd ..

# Run
python -m backend.main
```

Open [http://localhost:8000](http://localhost:8000).

## Configuration

All settings are in `config/settings.yaml`. API keys can be set either in the YAML file directly or via environment variables in `.env`.

### Adding an LLM Provider

Edit `config/settings.yaml` under `providers:`:

```yaml
providers:
  anthropic:
    enabled: true
    api_key: "${ANTHROPIC_API_KEY}"
    default_model: "claude-sonnet-4-20250514"
    models:
      - id: "claude-sonnet-4-20250514"
        name: "Claude Sonnet 4"
        max_tokens: 8192
```

Set the key in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

### Using Local Ollama

1. Install and run [Ollama](https://ollama.com) on your machine
2. Pull a model: `ollama pull llama3.2`
3. Ensure the config has:

```yaml
providers:
  ollama:
    enabled: true
    base_url: "http://localhost:11434"    # or host.docker.internal for Docker
    models:
      - id: "llama3.2"
        name: "Llama 3.2"
```

### Generation defaults (context size, sampling, compaction)

`config/settings.yaml` → `generation.defaults` holds separate defaults for **local** (Ollama) and **cloud** (Claude / GPT / Ollama Cloud) models:

```yaml
generation:
  defaults:
    local:
      num_ctx: 8192          # context window sent to Ollama
      max_tokens: 4096
      temperature: 0.7
      keep_alive: "10m"
      compaction: { mode: "summarize", threshold: 0.8, keep_recent: 6 }
    cloud:
      context_window: 200000
      max_tokens: 4096
      compaction: { mode: "summarize", threshold: 0.85, keep_recent: 8 }
```

Users can override every value from the sliders control (⚙) next to the prompt box and save their own defaults per kind. Compaction modes: `off`, `truncate` (drop oldest turns) or `summarize` (replace oldest turns with a model-written summary that is carried along with the conversation).

### Vision models

Click the image button (or paste / drop an image) to attach up to six images to a message. Images are downscaled client-side and sent as base64 to Ollama (`images`), Anthropic (image blocks) or OpenAI (`image_url`). Nexus checks the model's capabilities and warns when the selected model cannot see images. Mark models in `settings.yaml` with `vision: true` to badge them before the server has been probed.

## Sandbox (code execution)

The `nexus-sandbox` container executes code from the `code_executor` tool and from the **Run** button on code blocks.

| Language | Runtime | Package install |
|---|---|---|
| python | 3.12 + numpy/pandas/scipy/matplotlib/plotly/sklearn… | auto-detects missing imports, `pip install` into a persistent volume |
| javascript / typescript | Node 20 | `npm install` per run (auto-detected `require`/`import`) |
| bash, java, c, cpp, go, rust, r | bash, OpenJDK 17, gcc/g++, Go, rustc, Rscript | — |
| html | — | saved and opened in the preview panel |

Files written to the working directory are returned as artifacts and appear as preview/download cards. If the sandbox container is unreachable (e.g. `python -m backend.main` without Docker), Python/JS/Bash fall back to a local subprocess with a warning.

## Tool selection (Auto vs Manual)

`tool_selection` in `config/settings.yaml` controls how tools reach the model:

```yaml
tool_selection:
  mode: "auto"       # auto = discovery router + pins, manual = ticked tools only
  max_tools: 8       # matches attached per message on top of the core set
  min_score: 0.25
  include_core: true # calculator, date/time, web search, web browser, code executor, memory, discover_tools
```

Each user can flip the mode in the sidebar; ticked tools are *pinned* in Auto mode. `GET /api/tools/discover?q=…` exposes the ranker for debugging.

## Adding Custom Tools

Creating a new tool takes 4 steps (the extra one registers it in the catalog so it is discoverable and lazily loaded):

### 1. Create a Python file

Create `backend/tools/my_tool.py`:

```python
import json
from . import BaseTool, register_tool

@register_tool("my_tool")
class MyTool(BaseTool):
    name = "my_tool"
    description = "Describe what your tool does"
    parameters = {
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": "What this parameter does"
            }
        },
        "required": ["input"]
    }

    async def execute(self, **kwargs) -> str:
        result = do_something(kwargs["input"])
        return json.dumps({"result": result})
```

### 2. Add to config

In `config/settings.yaml`:

```yaml
tools:
  my_tool:
    enabled: true
    name: "My Tool"
    description: "Does something useful"
    icon: "wrench"     # Lucide icon name
    config:
      custom_key: "value"   # Accessible via self.config
```

### 3. Register in the catalog

Add an entry to `backend/tools/catalog.py` so the router can rank it and import its module lazily:

```python
{"id": "my_tool", "module": "backend.tools.my_tool", "category": "Utilities",
 "summary": "Does something useful", "tags": ["useful", "something"],
 "risk": "low", "permission": "auto", "offline": True},
```

Restart the server — the tool appears in the sidebar under its category and is attached automatically when a message matches its tags.

## MCP Server Integration

Nexus Chat can connect to external services via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). Tools from MCP servers appear in the sidebar alongside built-in tools — users can toggle them on/off per conversation.

### MCP Marketplace (no restart needed)

Open **MCP Servers → MCP Marketplace** in the sidebar:

- **Registry** — search the official MCP Registry and install a server with one click. npm packages run as `npx -y <pkg>`, PyPI packages as `uvx <pkg>`, OCI images via `docker run`, and remote servers connect by URL. Required environment variables (API tokens) are prompted for.
- **Import config** — paste a `mcpServers` JSON block from Claude Desktop, ChatGPT, Cursor or VS Code.
- **URL / command** — connect a server by URL (streamable HTTP, SSE or legacy `/rpc`, auto-detected) or by a stdio command.

User-added servers are stored in `data/mcp_servers.json` and reconnected on startup. Servers that expose **resources** show a book icon; resources can be indexed into the RAG store and used as context.

### Supported transports

| Config | Transport |
|---|---|
| `url: http://host:port/mcp` | Streamable HTTP (MCP 2025-06-18) |
| `url: http://host:port/sse` | HTTP + SSE (legacy) |
| `url: http://host:port` (no `/mcp`) | Streamable HTTP, then legacy Nexus `/rpc` fallback |
| `command: npx` / `uvx` / `docker` + `args` | stdio (process inside the Nexus container) |

### Quick Start with the Example Database Server

An example SQLite MCP server is included at `examples/mcp-database-server/`.

**Bundled (easiest):** Start everything together with the `mcp` profile:

```bash
docker compose --profile mcp up --build
```

**Standalone:** Run the MCP server independently and let it join the shared `nexus-net` network:

```bash
# 1. Start Nexus Chat (creates the nexus-net network)
docker compose up --build

# 2. In another terminal, start the MCP server
cd examples/mcp-database-server
docker compose up --build
```

Both approaches connect via the `nexus-net` Docker network, so the URL `http://mcp-database:8100` resolves in either case.

Then enable it in `config/settings.yaml`:

```yaml
mcp_servers:
  database:
    enabled: true
```

Restart Nexus Chat. The database server exposes four tools: `query`, `execute`, `list_tables`, and `describe_table`. Once connected, ask the chatbot things like *"List all tables in the database"* or *"Insert a new note titled 'Hello'"*.

### Connecting Your Own MCP Server

Any HTTP service that implements the MCP JSON-RPC interface can be connected:

**1. Implement the `/rpc` endpoint** with three methods:

| Method | Description |
|---|---|
| `initialize` | Handshake — return server name and version |
| `tools/list` | Return an array of tool definitions (name, description, JSON Schema parameters) |
| `tools/call` | Execute a tool by name with arguments, return the result |

Plus a `GET /health` endpoint for liveness checks.

**2. Add it to `config/settings.yaml`:**

```yaml
mcp_servers:
  my_server:
    enabled: true
    name: "My Service"
    description: "Does something useful"
    url: "http://my-mcp-server:9000"
    icon: "server"       # Lucide icon name
    timeout: 30          # seconds
```

**3. Connect via Docker network:**

*Option A — Bundled in docker-compose.yml:*

```yaml
services:
  my-mcp-server:
    build: ./path/to/server
    networks:
      - nexus-net
    profiles:
      - mcp
    ports:
      - "9000:9000"
```

*Option B — Standalone container on the shared network:*

```bash
docker run --name my-mcp-server --network nexus-net -p 9000:9000 my-mcp-image
```

Restart Nexus Chat — the MCP server's tools appear under the "MCP Servers" section in the sidebar.

### Docker Networking

All services (Nexus Chat + MCP servers) share a bridge network called `nexus-net`. The root `docker-compose.yml` creates it automatically. Standalone MCP servers can join it by referencing `nexus-net` as an external network — see `examples/mcp-database-server/docker-compose.yml` for a ready-made template.

### Turning MCP Servers On/Off

- **Config-level:** Set `enabled: false` in `settings.yaml` and restart.
- **Docker-level (bundled):** Run without the `mcp` profile: `docker compose up` (no `--profile mcp`).
- **Docker-level (standalone):** Stop the standalone container: `docker compose down` in its directory.
- **Per-conversation:** Toggle individual MCP tools on/off in the sidebar, just like built-in tools.

## Project Structure

```
nexus-chat/
├── backend/
│   ├── main.py              # FastAPI app, routes, WebSocket
│   ├── config.py             # YAML config loader
│   ├── orchestrator.py        # Chat orchestrator (LLM + tools + MCP + RAG)
│   ├── providers/
│   │   ├── __init__.py        # Base class + registry
│   │   ├── anthropic_provider.py
│   │   ├── openai_provider.py
│   │   └── ollama_provider.py
│   ├── tools/
│   │   ├── __init__.py        # Base class + registry
│   │   ├── builtin.py         # Calculator, code exec, search, etc.
│   │   └── example_tool.py    # Template for custom tools
│   ├── mcp/
│   │   ├── __init__.py        # MCP module entry
│   │   └── client.py          # MCPClient + MCPManager
│   └── rag/
│       ├── __init__.py
│       └── engine.py          # Document ingestion + retrieval
├── frontend/
│   ├── src/
│   │   ├── App.jsx            # Main app component
│   │   ├── main.jsx           # Entry point
│   │   ├── components/
│   │   │   ├── Sidebar.jsx    # Model/tool/MCP/file selection
│   │   │   ├── ChatMessage.jsx
│   │   │   └── ChatInput.jsx
│   │   ├── hooks/
│   │   │   ├── useChat.js     # WebSocket hook
│   │   │   └── api.js         # REST API helpers
│   │   └── styles/
│   │       └── global.css     # All styles
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── examples/
│   └── mcp-database-server/   # Example MCP server (SQLite)
│       ├── server.py
│       ├── Dockerfile
│       └── README.md
├── config/
│   └── settings.yaml          # Main configuration file
├── data/
│   ├── uploads/               # User-uploaded files
│   └── vector_store/          # ChromaDB persistence
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── pyproject.toml
├── start_dev.sh               # Linux/macOS dev script
├── start_dev.bat              # Windows dev script
├── .env.example
└── README.md
```

## Architecture

```
┌─────────────┐     WebSocket      ┌───────────────────────────────────┐
│   React UI  │◄───────────────────►│  FastAPI Backend                  │
│             │     REST API        │                                   │
│  • Sidebar  │◄───────────────────►│  ┌─────────────────────────────┐  │
│  • Chat     │                     │  │  Chat Orchestrator          │  │
│  • Input    │                     │  │                             │  │
└─────────────┘                     │  │  ┌─────────┐ ┌──────────┐  │  │
                                    │  │  │Providers│ │  Tools   │  │  │
                                    │  │  │• Claude │ │• Calc    │  │  │
                                    │  │  │• OpenAI │ │• Search  │  │  │
                                    │  │  │• Ollama │ │• Code    │  │  │
                                    │  │  └─────────┘ │• Custom  │  │  │
                                    │  │              └──────────┘  │  │
                                    │  │  ┌──────────────────────┐  │  │
                                    │  │  │  MCP Manager         │  │  │
                                    │  │  │  JSON-RPC clients    │──┼──┼──► MCP Servers
                                    │  │  └──────────────────────┘  │  │   (Docker / remote)
                                    │  │  ┌──────────────────────┐  │  │
                                    │  │  │  RAG Engine          │  │  │
                                    │  │  │  ChromaDB + Chunking │  │  │
                                    │  │  └──────────────────────┘  │  │
                                    │  └─────────────────────────────┘  │
                                    └───────────────────────────────────┘
```

![nexus_chat_architecture](https://github.com/user-attachments/assets/7381800e-dd69-40c2-81cd-6e70b41c592d)

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Health check (`edition: server`) |
| `/api/auth/login` · `logout` · `me` · `change-password` · `account` | POST / GET / DELETE | Session management (JWT + cookie) |
| `/api/admin/users[/{name}[/password]]` | GET / POST / DELETE | Admin: list, create, delete users, reset passwords |
| `/api/settings/user` | GET / PUT | Per-user settings (generation defaults) |
| `/api/models` | GET | List available LLM models |
| `/api/tools` | GET | List available tools |
| `/api/files` | GET | List uploaded files |
| `/api/upload` | POST | Upload a file (multipart) |
| `/api/files/{name}` | DELETE | Delete an uploaded file |
| `/api/mcp/servers` | GET / POST | List MCP servers / add one (URL or command) |
| `/api/mcp/servers/{id}` | DELETE | Remove a user-added MCP server |
| `/api/mcp/servers/{id}/reconnect` | POST | Reconnect an MCP server |
| `/api/mcp/servers/discover` | POST | Probe a server without saving it |
| `/api/mcp/registry/search?q=` | GET | Search the official MCP registry |
| `/api/mcp/import` | POST | Import an `mcpServers` JSON config |
| `/api/mcp/servers/{id}/resources[/ingest]` | GET / POST | List MCP resources / index one into RAG |
| `/api/settings/generation` | GET | Default generation / compaction settings |
| `/api/models/{id}/info` | GET | Model capabilities (vision, tools, context length, quantization) |
| `/api/models/pull` | POST | Pull an Ollama model tag (streams progress) |
| `/api/sandbox/run` | POST | Run a code block directly (SSE) |
| `/api/sandbox/languages` | GET | Languages available in the sandbox |
| `/api/preview/{name}` | GET | Preview data for generated / uploaded files |
| `/ws/chat` | WebSocket | Streaming chat |

### WebSocket Message Format

**Send:**
```json
{
  "messages": [{"role": "user", "content": "What is in this picture?", "images": ["data:image/png;base64,..."]}],
  "model": "gemma4:latest",
  "tools": ["calculator", "web_search", "code_executor"],
  "files": ["report.pdf"],
  "system_prompt": "",
  "generation": {"num_ctx": 16384, "temperature": 0.4, "compaction": {"mode": "summarize", "threshold": 0.8}},
  "compaction_state": {"summary": "…", "covers": 12}
}
```

Additional streamed events: `{"type": "notice", ...}` (e.g. tools disabled for a model), `{"type": "compaction", ...}` (history was compacted; carry `state` into the next request) and `{"type": "context", "budget": N, "estimated_prompt_tokens": N}`.

**Receive (streamed events):**
```json
{"type": "text", "content": "Hello! "}
{"type": "tool_call", "name": "calculator", "arguments": {"expression": "2+2"}}
{"type": "tool_result", "name": "calculator", "result": "{\"result\": 4}"}
{"type": "text", "content": "The answer is 4."}
{"type": "done"}
```

## Roadmap

- [x] Conversation history persistence
- [x] Multi-user authentication (nexus-chat-server)
- [x] MCP (Model Context Protocol) server integration
- [x] MCP marketplace / registry installer
- [x] Vision models & multi-language sandbox
- [ ] Mobile-friendly PWA
- [ ] Electron desktop app (Windows/macOS/Linux)
- [ ] iOS / Android via Capacitor or React Native wrapper
- [x] Streaming tool execution with progress

## License

MIT — see [LICENSE](LICENSE).
