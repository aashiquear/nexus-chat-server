"""
Nexus Chat - FastAPI Application
Main entry point for the backend server.
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Load env vars before importing config
load_dotenv()

# Ensure backend is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

import backend.mcp  # noqa: F401 – MCP client module

# Import providers and tools to trigger registration
import backend.providers.anthropic_provider
import backend.providers.ollama_provider
import backend.providers.openai_provider
import backend.tools.builtin
import backend.tools.example_tool
import backend.tools.file_generator
import backend.tools.graph_plotter
import backend.tools.image_synthesizer
import backend.tools.svg_diagram
from backend import conversations
from backend.config import get_config, load_config
from backend.orchestrator import ChatOrchestrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load config
config = load_config()
app_config = config.get("app", {})

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    await orchestrator.init_mcp()
    if orchestrator.rag_engine:
        await orchestrator.rag_engine.sync_uploads(upload_dir)

    file_gen_cfg = config.get("tools", {}).get("file_generator", {})
    if file_gen_cfg.get("enabled", False):
        cache_dir = file_gen_cfg.get("config", {}).get("model_cache_dir")

        async def _warmup_diffusion():
            try:
                from backend.tools.file_generator import _ensure_diffusion_loaded

                logger.info("Pre-loading diffusion model in background …")
                await asyncio.to_thread(_ensure_diffusion_loaded, cache_dir)
                logger.info("Diffusion model pre-loaded successfully.")
            except Exception as e:
                logger.warning("Diffusion model pre-load failed (will retry on first use): %s", e)

        asyncio.create_task(_warmup_diffusion())

    yield


app = FastAPI(
    title=app_config.get("name", "Nexus Chat"),
    version=app_config.get("version", "0.1.0"),
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize orchestrator
orchestrator = ChatOrchestrator()

# Ensure upload, downloads, sandbox, and generated files directories exist
upload_dir = Path(config.get("uploads", {}).get("upload_directory", "./data/uploads"))
upload_dir.mkdir(parents=True, exist_ok=True)
download_dir = Path("./data/downloads")
download_dir.mkdir(parents=True, exist_ok=True)
sandbox_dir = Path("./data/sandbox")
sandbox_dir.mkdir(parents=True, exist_ok=True)
files_dir = Path("./data/files")
files_dir.mkdir(parents=True, exist_ok=True)



# ------ REST API Endpoints ------


@app.get("/api/health")
async def health():
    return {"status": "ok", "name": app_config.get("name")}


# ------ Auth Endpoints ------

@app.post("/api/auth/login")
async def login(request: Request):
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    users = auth._load_users()
    user = users.get(username)
    if not user or not auth.verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = auth.create_access_token(username)
    return {
        "token": token,
        "user": {
            "username": user["username"],
            "is_admin": user.get("is_admin", False),
        },
    }


@app.get("/api/auth/me")
async def me(current_user: dict = Depends(auth.get_current_user)):
    return {
        "username": current_user["username"],
        "is_admin": current_user.get("is_admin", False),
    }


@app.post("/api/auth/change-password")
async def change_password(request: Request, current_user: dict = Depends(auth.get_current_user)):
    body = await request.json()
    current_password = body.get("current_password", "")
    new_password = body.get("new_password", "")
    if not new_password or len(new_password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    await auth.change_password(current_user["username"], current_password, new_password)
    return {"status": "ok"}


@app.delete("/api/auth/account")
async def delete_account(request: Request, current_user: dict = Depends(auth.get_current_user)):
    body = await request.json()
    password = body.get("password", "")
    await auth.delete_user_account(current_user["username"], password)
    return {"deleted": current_user["username"]}


# ------ Admin Endpoints ------

@app.get("/api/admin/users")
async def admin_list_users(_admin: dict = Depends(auth.get_current_admin_user)):
    return {"users": await auth.list_users()}


@app.post("/api/admin/users")
async def admin_create_user(request: Request, _admin: dict = Depends(auth.get_current_admin_user)):
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    is_admin = bool(body.get("is_admin", False))
    result = await auth.create_user(username, password, is_admin)
    return result


@app.delete("/api/admin/users/{username}")
async def admin_delete_user(username: str, _admin: dict = Depends(auth.get_current_admin_user)):
    await auth.delete_user(username)
    return {"deleted": username}


@app.get("/api/models")
async def list_models():
    """Return all configured LLM models, annotated with whether each is
    currently available on its provider's remote server (Ollama tags,
    Anthropic ``/v1/models``, OpenAI ``/v1/models``).

    Probe results are cached briefly per-provider so this endpoint stays
    cheap to call from the frontend on demand.
    """
    return {"models": await orchestrator.get_available_models_async()}


@app.get("/api/tools")
async def list_tools():
    """Return all available tools."""
    return {"tools": orchestrator.get_available_tools()}


UPLOAD_STREAM_CHUNK = 1024 * 1024  # 1 MiB per read — bounded memory


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), current_user: dict = Depends(auth.get_current_user)):
    """Upload a file for RAG and tool use.

    The request body is streamed to disk in 1 MiB chunks so multi-GB
    uploads don't sit in memory. Once bytes are on disk the response
    returns immediately and embedding runs asynchronously; the client
    polls ``/api/upload/progress/{filename}`` to track the embedding
    stage separately from the byte-transfer stage.
    """
    upload_cfg = config.get("uploads", {})
    max_size = upload_cfg.get("max_file_size_mb", 50) * 1024 * 1024
    allowed = upload_cfg.get("allowed_extensions", [])

    # Check extension
    ext = Path(file.filename).suffix.lower()
    if allowed and ext not in allowed:
        raise HTTPException(400, f"File type '{ext}' not allowed. Allowed: {allowed}")

    # Stream to disk in chunks; abort early if the size limit is exceeded.
    filepath = upload_dir / file.filename
    written = 0
    try:
        with open(filepath, "wb") as out:
            while True:
                chunk = await file.read(UPLOAD_STREAM_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_size:
                    out.close()
                    try:
                        filepath.unlink()
                    except FileNotFoundError:
                        pass
                    raise HTTPException(
                        400,
                        f"File too large. Max: {max_size // (1024 * 1024)} MB",
                    )
                out.write(chunk)
    finally:
        await file.close()

    # Kick off embedding in the background so the upload response returns
    # immediately. The client polls /api/upload/progress to track it.
    embedding_status = "skipped"
    if orchestrator.rag_engine:
        rag = orchestrator.rag_engine
        rag._set_progress(file.filename, stage="queued", current=0, total=0, percent=0)

        async def _ingest_in_background():
            try:
                await rag.ingest_file(filepath)
            except Exception as e:
                logger.error("Background ingestion failed for %s: %s", file.filename, e)
                rag._set_progress(file.filename, stage="error", percent=0)

        import asyncio

        asyncio.create_task(_ingest_in_background())
        embedding_status = "started"

    return {
        "filename": file.filename,
        "size": written,
        "embedding_status": embedding_status,
    }


@app.get("/api/upload/progress/{filename}")
async def upload_progress(filename: str):
    """Poll the embedding progress for an uploaded file.

    Returns ``{stage, percent, current, total}`` where ``stage`` is one
    of: ``queued``, ``reading``, ``chunking``, ``embedding``,
    ``complete``, ``error``. Returns ``stage: "unknown"`` if the
    filename has no progress entry (e.g. completed long ago and cleared).
    """
    if not orchestrator.rag_engine:
        return {"stage": "unknown", "percent": 0}
    progress = orchestrator.rag_engine.get_progress(filename)
    if not progress:
        return {"stage": "unknown", "percent": 0}
    return progress


@app.get("/api/files")
async def list_files(current_user: dict = Depends(auth.get_current_user)):
    """List uploaded files and resync any new files into the RAG vector store."""
    # Re-ingest any files placed directly in the uploads directory (not via upload API)
    if orchestrator.rag_engine:
        await orchestrator.rag_engine.sync_uploads(upload_dir)

    files = []
    for f in upload_dir.iterdir():
        if f.is_file():
            files.append(
                {
                    "name": f.name,
                    "size": f.stat().st_size,
                    "extension": f.suffix,
                }
            )
    return {"files": files}


@app.delete("/api/files/{filename}")
async def delete_file(filename: str, current_user: dict = Depends(auth.get_current_user)):
    """Delete an uploaded file."""
    filepath = upload_dir / filename
    if not filepath.exists():
        raise HTTPException(404, "File not found")

    filepath.unlink()

    # Remove from RAG
    if orchestrator.rag_engine:
        await orchestrator.rag_engine.delete_file(filename)

    return {"deleted": filename}


# ------ Plot Image Endpoint ------


@app.get("/api/plots/{filename}")
async def serve_plot_file(filename: str):
    """Serve a generated plot image from the data directory."""
    data_dir = Path("./data")
    filepath = data_dir / filename
    if not filepath.exists():
        # Also check uploads directory
        filepath = upload_dir / filename
    if not filepath.exists():
        raise HTTPException(404, "Plot file not found")
    return FileResponse(filepath, media_type="image/png")


# ------ Download / Preview Endpoints ------


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    """Serve a downloadable file from the downloads or files directory."""
    filepath = download_dir / filename
    if not filepath.exists():
        filepath = files_dir / filename
    if not filepath.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(filepath, media_type="application/octet-stream", filename=filename)


_EXT_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
}


@app.get("/api/files/{filename}")
async def serve_generated_file(filename: str):
    """Serve a generated file from the files/downloads directory.

    Picks an inline-friendly media type for previewable formats (PDF,
    PNG, etc.) so the browser can render them in an iframe / <img>
    rather than forcing a download.
    """
    for base in (files_dir, download_dir, Path("./data")):
        filepath = base / filename
        if filepath.exists():
            media_type = _EXT_MEDIA_TYPES.get(filepath.suffix.lower(), "application/octet-stream")
            return FileResponse(filepath, media_type=media_type)
    raise HTTPException(404, "File not found")


@app.get("/api/preview/{filename}")
async def preview_file(filename: str):
    """Return file content for canvas preview.

    Looks in the downloads, files, and data directories. Binary formats
    (pdf, png, jpg) are returned as a reference URL instead of inline
    text, so the frontend can embed them via ``<iframe>`` or ``<img>``.
    """
    for base in (download_dir, files_dir, Path("./data")):
        candidate = base / filename
        if candidate.exists():
            filepath = candidate
            break
    else:
        raise HTTPException(404, "File not found")

    ext = filepath.suffix.lower()
    binary_types = {
        ".pdf": ("application/pdf", "pdf"),
        ".png": ("image/png", "image"),
        ".jpg": ("image/jpeg", "image"),
        ".jpeg": ("image/jpeg", "image"),
        ".gif": ("image/gif", "image"),
        ".webp": ("image/webp", "image"),
        ".docx": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "binary",
        ),
        ".pptx": (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "binary",
        ),
    }
    if ext in binary_types:
        mime, kind = binary_types[ext]
        # /api/files/{filename} now searches files_dir, download_dir,
        # and ./data, and serves inline with the correct MIME type —
        # perfect for canvas embeds (iframe for PDF, <img> for images).
        url = f"/api/files/{filename}"
        return {
            "filename": filename,
            "content_type": mime,
            "kind": kind,
            "url": url,
        }

    content_type = {
        ".md": "text/markdown",
        ".html": "text/html",
        ".htm": "text/html",
        ".py": "text/x-python",
        ".js": "text/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".txt": "text/plain",
        ".csv": "text/csv",
        ".c": "text/x-c",
        ".cpp": "text/x-c++",
        ".h": "text/x-c",
    }.get(ext, "text/plain")
    content = filepath.read_text(encoding="utf-8", errors="replace")
    return {
        "content": content,
        "content_type": content_type,
        "kind": "text",
        "filename": filename,
    }


# ------ Sandbox Interactive Session Proxy ------


@app.websocket("/ws/sandbox/interact/{session_id}")
async def sandbox_interact(websocket: WebSocket, session_id: str):
    """Bridge the browser to the nexus-sandbox WebSocket.

    The CodeExecutorTool registers the session via
    ``backend.sandbox_sessions.register_session`` when the tool is
    called with ``interactive=true``; the frontend then opens this
    endpoint to send stdin and receive live stdout/stderr.
    """
    import websockets

    from backend.sandbox_sessions import peek_session

    await websocket.accept()

    session = peek_session(session_id)
    if not session:
        await websocket.send_text(
            json.dumps({"type": "stderr", "data": "Unknown or expired session"})
        )
        await websocket.close()
        return

    # Convert http://host:port → ws://host:port/interact
    base = session.sandbox_url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
    target = f"{base}/interact"

    try:
        async with websockets.connect(target, ping_interval=None) as sandbox_ws:
            await sandbox_ws.send(json.dumps({"code": session.code}))

            async def browser_to_sandbox():
                try:
                    while True:
                        msg = await websocket.receive_text()
                        await sandbox_ws.send(msg)
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    logger.warning("browser→sandbox bridge ended: %s", e)

            async def sandbox_to_browser():
                try:
                    async for msg in sandbox_ws:
                        await websocket.send_text(msg if isinstance(msg, str) else msg.decode())
                except Exception as e:
                    logger.warning("sandbox→browser bridge ended: %s", e)

            await asyncio.gather(browser_to_sandbox(), sandbox_to_browser())
    except Exception as e:
        try:
            await websocket.send_text(
                json.dumps({"type": "stderr", "data": f"Sandbox unreachable: {e}"})
            )
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


MAX_PLOTLY_BODY_BYTES = 10 * 1024 * 1024  # 10 MB


@app.post("/api/plots/from-json")
async def plotly_json_to_png(request: Request):
    """Convert Plotly figure JSON to a PNG file and return the filename."""
    try:
        import plotly.io as pio

        raw_body = await request.body()
        if len(raw_body) > MAX_PLOTLY_BODY_BYTES:
            raise HTTPException(
                413, f"Request body exceeds {MAX_PLOTLY_BODY_BYTES // (1024 * 1024)} MB limit"
            )

        body = json.loads(raw_body)
        figure_json = body.get("figure_json")
        if not figure_json:
            raise HTTPException(400, "Missing figure_json in request body")

        fig = pio.from_json(
            json.dumps(figure_json) if isinstance(figure_json, dict) else figure_json
        )
        filename = f"plotly-{uuid.uuid4().hex[:8]}.png"
        data_dir = Path("./data")
        data_dir.mkdir(exist_ok=True)
        filepath = data_dir / filename
        fig.write_image(str(filepath), width=1200, height=700, scale=2)

        return JSONResponse({"filename": filename})
    except ImportError:
        raise HTTPException(500, "plotly or kaleido not installed")
    except Exception as e:
        raise HTTPException(500, f"Failed to render Plotly figure: {e}")


# ------ MCP Server Endpoints ------


@app.get("/api/mcp/servers")
async def list_mcp_servers():
    """Return all configured MCP servers and their status."""
    return {"servers": orchestrator.get_mcp_servers()}


@app.post("/api/mcp/servers/{server_id}/reconnect")
async def reconnect_mcp_server(server_id: str):
    """Attempt to reconnect to an MCP server."""
    ok = await orchestrator.reconnect_mcp(server_id)
    if not ok:
        raise HTTPException(404, f"MCP server '{server_id}' not found or unreachable")
    return {"status": "connected", "server": server_id}


@app.post("/api/mcp/servers/discover")
async def discover_mcp_server(payload: dict):
    """Probe an MCP server URL without persisting it."""
    url = payload.get("url", "").strip().rstrip("/")
    if not url:
        raise HTTPException(400, "URL is required")
    from backend.mcp.client import MCPClient
    temp_id = "discover_" + url.replace("://", "_").replace("/", "_").replace(":", "_")
    client = MCPClient(temp_id, {"url": url, "timeout": payload.get("timeout", 30)})
    connected = await client.connect()
    if not connected:
        raise HTTPException(502, f"Could not connect to MCP server at {url}")
    tools = client.tools
    return {
        "server": {
            "id": temp_id,
            "name": client.name,
            "description": client.description,
            "url": client.url,
            "icon": client.icon,
            "connected": True,
            "tools": [t["name"] for t in tools],
        },
        "tools": tools,
    }


@app.post("/api/mcp/servers")
async def add_mcp_server(payload: dict):
    """Add a new MCP server by URL and persist it."""
    url = payload.get("url", "").strip().rstrip("/")
    if not url:
        raise HTTPException(400, "URL is required")
    # Generate a stable server_id from the URL
    server_id = payload.get("id") or url.replace("://", "_").replace("/", "_").replace(":", "_")
    cfg = {
        "url": url,
        "name": payload.get("name", server_id),
        "description": payload.get("description", ""),
        "icon": payload.get("icon", "server"),
        "timeout": payload.get("timeout", 30),
        "enabled": True,
    }
    info = await orchestrator.add_mcp_server(server_id, cfg)
    if not info:
        raise HTTPException(502, f"Could not connect to MCP server at {url}")
    # Persist
    user_servers = _load_user_mcp_servers()
    user_servers[server_id] = cfg
    _save_user_mcp_servers(user_servers)
    return {"status": "added", "server": info}


@app.delete("/api/mcp/servers/{server_id}")
async def remove_mcp_server(server_id: str):
    """Remove a persisted MCP server."""
    ok = orchestrator.remove_mcp_server(server_id)
    if not ok:
        raise HTTPException(404, f"MCP server '{server_id}' not found")
    user_servers = _load_user_mcp_servers()
    user_servers.pop(server_id, None)
    _save_user_mcp_servers(user_servers)
    return {"status": "removed", "server": server_id}


# ------ Conversation Endpoints ------

from starlette.requests import Request as StarletteRequest


@app.get("/api/conversations")
async def list_conversations_endpoint(current_user: dict = Depends(auth.get_current_user)):
    """List all saved conversations for the current user."""
    return {"conversations": conversations.list_conversations(current_user["username"])}


@app.get("/api/conversations/{conversation_id}")
async def get_conversation_endpoint(conversation_id: str, current_user: dict = Depends(auth.get_current_user)):
    """Load a specific conversation for the current user."""
    data = conversations.get_conversation(conversation_id, current_user["username"])
    if not data:
        raise HTTPException(404, "Conversation not found")
    return data


@app.post("/api/conversations")
async def save_conversation_post(request: StarletteRequest, current_user: dict = Depends(auth.get_current_user)):
    """Create or update a conversation for the current user."""
    body = await request.json()
    result = conversations.save_conversation(
        conversation_id=body.get("id"),
        messages=body.get("messages", []),
        model=body.get("model", ""),
        token_usage=body.get("token_usage"),
        username=current_user["username"],
    )
    return result


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation_endpoint(conversation_id: str, current_user: dict = Depends(auth.get_current_user)):
    """Delete a conversation for the current user."""
    ok = conversations.delete_conversation(conversation_id, current_user["username"])
    if not ok:
        raise HTTPException(404, "Conversation not found")
    return {"deleted": conversation_id}


# ------ WebSocket Chat ------


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for streaming chat. Authenticates via ?token=..."""
    token = websocket.query_params.get("token")
    try:
        user = auth.get_ws_user(token)
    except Exception as e:
        await websocket.close(code=1008, reason=str(e))
        return

    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_text()
            request = json.loads(data)

            messages = request.get("messages", [])
            model = request.get("model", "")
            tools = request.get("tools", [])
            files = request.get("files", [])
            system = request.get("system_prompt", "")

            async for event in orchestrator.chat_stream(
                messages=messages,
                model_id=model,
                selected_tools=tools,
                selected_files=files,
                system_prompt=system,
            ):
                await websocket.send_text(json.dumps(event))

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_text(json.dumps({"type": "error", "content": str(e)}))
        except Exception:
            pass


# ------ Serve Frontend ------

frontend_dir = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        file_path = frontend_dir / path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(frontend_dir / "index.html")


def main():
    """Run the server."""
    import uvicorn

    host = app_config.get("host", "0.0.0.0")
    port = app_config.get("port", 8000)
    debug = app_config.get("debug", False)
    logger.info(f"Starting Nexus Chat on {host}:{port}")
    uvicorn.run("backend.main:app", host=host, port=port, reload=debug)


if __name__ == "__main__":
    main()
