"""
Nexus Chat - FastAPI Application
Main entry point for the backend server.
"""

import asyncio
import json
import logging
import os
import re
import sys
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Load env vars before importing config
load_dotenv()

# Ensure backend is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

import backend.mcp  # noqa: F401 – MCP client module

# Import providers to trigger registration. Tools are *not* imported
# here: backend/tools/catalog.py maps every tool to its module and the
# orchestrator imports a module the first time one of its tools is used,
# so startup cost does not grow with the number of tools.
import backend.providers.anthropic_provider
import backend.providers.ollama_provider
import backend.providers.openai_provider
import backend.tools.catalog  # noqa: F401
import backend.tools.example_tool  # tiny template tool, kept eager
from backend import conversations
from backend.config import get_config, load_config
from backend.mcp.client import parse_mcp_config_import, search_registry
from backend.orchestrator import ChatOrchestrator
from backend.tools import ToolContext, set_default_context

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load config
config = load_config()
app_config = config.get("app", {})


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Initialize MCP connections, sync uploads, and pre-load heavy models."""
    await orchestrator.init_mcp()
    # Re-ingest any uploaded files missing from the vector store (e.g. after restart)
    if orchestrator.rag_engine:
        await orchestrator.rag_engine.sync_uploads(upload_dir)

    # ── Pre-load diffusion model in background so first PNG generation is fast ──
    file_gen_cfg = config.get("tools", {}).get("file_generator", {})
    if file_gen_cfg.get("enabled", False) and file_gen_cfg.get("config", {}).get("preload_diffusion", False):
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
    await orchestrator.mcp.shutdown()


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
plots_dir = Path("./data")
state_dir = Path("./data/state")
state_dir.mkdir(parents=True, exist_ok=True)

set_default_context(
    ToolContext(upload_dir=upload_dir, files_dir=files_dir, downloads_dir=download_dir, plots_dir=plots_dir, state_dir=state_dir)
)


def safe_filename(name: str) -> str:
    """Reduce a client-supplied file name to a single safe path component.

    Rejects traversal attempts (``../etc/passwd``), hidden files and
    control characters so no endpoint can read or write outside its
    directory."""
    base = Path(name or "").name
    base = base.replace("\x00", "").strip()
    if not base or base in (".", "..") or base.startswith("."):
        raise HTTPException(400, "Invalid file name")
    return base


def _find_file(name: str, *dirs: Path) -> Path | None:
    for base in dirs:
        candidate = Path(base) / name
        if candidate.is_file():
            return candidate
    return None


# ------ REST API Endpoints ------


@app.get("/api/health")
async def health():
    return {"status": "ok", "name": app_config.get("name"), "version": app_config.get("version")}


@app.get("/api/models")
async def list_models():
    """Return all configured LLM models, annotated with whether each is
    currently available on its provider's remote server plus capability
    metadata (vision / tools / thinking / context length / quantization)."""
    return {"models": await orchestrator.get_available_models_async()}


@app.get("/api/models/{model_id:path}/info")
async def model_info(model_id: str):
    """Capability details for one model (Ollama ``/api/show`` etc.)."""
    resolved = orchestrator._resolve_provider(model_id)
    if not resolved:
        raise HTTPException(404, "Model not found")
    _name, provider, cfg = resolved
    caps = await provider.model_capabilities(model_id)
    return {"id": model_id, "provider": _name, "config": cfg, "capabilities": caps}


@app.post("/api/models/pull")
async def pull_model(request: Request):
    """Pull a model tag (e.g. a different quantization such as
    ``llama3.2:3b-instruct-q8_0``) onto the connected Ollama server.
    Streams Ollama's progress events as SSE."""
    body = await request.json()
    name = (body.get("model") or "").strip()
    if not name or not re.fullmatch(r"[\w.\-/:]+", name):
        raise HTTPException(400, "Invalid model name")
    provider = orchestrator._providers.get("ollama")
    if provider is None:
        raise HTTPException(400, "Ollama provider is not enabled")

    import httpx

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=None)) as client:
                async with client.stream(
                    "POST", f"{provider.base_url}/api/pull",
                    json={"model": name, "stream": True}, headers=provider._headers,
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.strip():
                            yield f"data: {line}\n\n"
            # Invalidate the tag cache so the new model shows up immediately.
            provider._remote_models_cache_ts = 0.0
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/settings/generation")
async def generation_settings():
    """Default generation / context settings for the prompt-box control."""
    return orchestrator.generation_defaults()


@app.get("/api/tools")
async def list_tools():
    """Return all available tools with catalog metadata plus the tool
    selection policy (auto discovery vs manual)."""
    return {"tools": orchestrator.get_available_tools(), "selection": orchestrator.tool_selection_config()}


@app.get("/api/tools/discover")
async def discover_tools_endpoint(q: str = "", limit: int = 8):
    """Rank tools against a query — the same router the chat uses in auto mode."""
    return {"query": q, "matches": orchestrator.discover(q, limit=max(1, min(limit, 20)))}


UPLOAD_STREAM_CHUNK = 1024 * 1024  # 1 MiB per read — bounded memory


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
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

    filename = safe_filename(file.filename)
    ext = Path(filename).suffix.lower()
    if allowed and ext not in allowed:
        raise HTTPException(400, f"File type '{ext}' not allowed. Allowed: {allowed}")

    # Stream to disk in chunks; abort early if the size limit is exceeded.
    filepath = upload_dir / filename
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
        rag._set_progress(filename, stage="queued", current=0, total=0, percent=0)

        async def _ingest_in_background():
            try:
                await rag.ingest_file(filepath)
            except Exception as e:
                logger.error("Background ingestion failed for %s: %s", filename, e)
                rag._set_progress(filename, stage="error", percent=0)

        asyncio.create_task(_ingest_in_background())
        embedding_status = "started"

    return {
        "filename": filename,
        "size": written,
        "embedding_status": embedding_status,
    }


@app.get("/api/upload/progress/{filename}")
async def upload_progress(filename: str):
    """Poll the embedding progress for an uploaded file."""
    if not orchestrator.rag_engine:
        return {"stage": "unknown", "percent": 0}
    progress = orchestrator.rag_engine.get_progress(filename)
    if not progress:
        return {"stage": "unknown", "percent": 0}
    return progress


@app.get("/api/files")
async def list_files():
    """List uploaded files and resync any new files into the RAG vector store."""
    if orchestrator.rag_engine:
        await orchestrator.rag_engine.sync_uploads(upload_dir)

    files = []
    for f in sorted(upload_dir.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            files.append(
                {
                    "name": f.name,
                    "size": f.stat().st_size,
                    "extension": f.suffix,
                }
            )
    return {"files": files}


@app.delete("/api/files/{filename}")
async def delete_file(filename: str):
    """Delete an uploaded file."""
    filename = safe_filename(filename)
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
    filename = safe_filename(filename)
    filepath = _find_file(filename, plots_dir, upload_dir, files_dir)
    if filepath is None:
        raise HTTPException(404, "Plot file not found")
    return FileResponse(filepath, media_type="image/png")


# ------ Download / Preview Endpoints ------


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    """Serve a downloadable file from the downloads, files or uploads directory."""
    filename = safe_filename(filename)
    filepath = _find_file(filename, download_dir, files_dir, upload_dir, plots_dir)
    if filepath is None:
        raise HTTPException(404, "File not found")
    return FileResponse(filepath, media_type="application/octet-stream", filename=filename)


_EXT_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}

_TEXT_CONTENT_TYPES = {
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".jsx": "text/javascript",
    ".tsx": "text/typescript",
    ".css": "text/css",
    ".json": "application/json",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".c": "text/x-c",
    ".cpp": "text/x-c++",
    ".h": "text/x-c",
    ".java": "text/x-java",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".sh": "text/x-shellscript",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".xml": "text/xml",
    ".svg": "image/svg+xml",
    ".log": "text/plain",
    ".sql": "text/x-sql",
}

MAX_PREVIEW_TEXT_BYTES = 4 * 1024 * 1024


@app.get("/api/files/{filename}")
async def serve_generated_file(filename: str):
    """Serve a generated or uploaded file inline with a previewable media type."""
    filename = safe_filename(filename)
    filepath = _find_file(filename, files_dir, download_dir, plots_dir, upload_dir)
    if filepath is None:
        raise HTTPException(404, "File not found")
    media_type = _EXT_MEDIA_TYPES.get(filepath.suffix.lower(), "application/octet-stream")
    headers = {"Content-Disposition": f'inline; filename="{filename}"'}
    return FileResponse(filepath, media_type=media_type, headers=headers)


@app.get("/api/preview/{filename}")
async def preview_file(filename: str):
    """Return file content for canvas preview.

    Looks in the downloads, files, data and uploads directories. Binary
    formats (pdf, images, office docs, media) are returned as a reference
    URL instead of inline text so the frontend can embed them. Text-like
    formats (code, markdown, csv, json, svg …) are returned inline.
    """
    filename = safe_filename(filename)
    filepath = _find_file(filename, download_dir, files_dir, plots_dir, upload_dir)
    if filepath is None:
        raise HTTPException(404, "File not found")

    ext = filepath.suffix.lower()
    binary_types = {
        ".pdf": ("application/pdf", "pdf"),
        ".png": ("image/png", "image"),
        ".jpg": ("image/jpeg", "image"),
        ".jpeg": ("image/jpeg", "image"),
        ".gif": ("image/gif", "image"),
        ".webp": ("image/webp", "image"),
        ".bmp": ("image/bmp", "image"),
        ".mp4": ("video/mp4", "video"),
        ".webm": ("video/webm", "video"),
        ".mp3": ("audio/mpeg", "audio"),
        ".wav": ("audio/wav", "audio"),
        ".docx": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "binary",
        ),
        ".pptx": (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "binary",
        ),
        ".xlsx": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "binary",
        ),
    }
    size = filepath.stat().st_size
    if ext in binary_types:
        mime, kind = binary_types[ext]
        return {
            "filename": filename,
            "content_type": mime,
            "kind": kind,
            "size": size,
            "url": f"/api/files/{filename}",
        }

    content_type = _TEXT_CONTENT_TYPES.get(ext, "text/plain")
    truncated = size > MAX_PREVIEW_TEXT_BYTES
    with open(filepath, "rb") as fh:
        raw = fh.read(MAX_PREVIEW_TEXT_BYTES)
    content = raw.decode("utf-8", errors="replace")
    return {
        "content": content,
        "content_type": content_type,
        "kind": "text",
        "filename": filename,
        "size": size,
        "truncated": truncated,
        "url": f"/api/files/{filename}",
    }


# ------ Sandbox: direct run (code-block "Run" button) ------


@app.get("/api/sandbox/languages")
async def sandbox_languages():
    """Which languages the sandbox container can execute right now."""
    tool = orchestrator.get_tool("code_executor")
    if tool is None:
        return {"languages": [], "sandbox": False}
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(tool.sandbox_url.rstrip("/") + "/languages")
            if resp.status_code == 200:
                return {**resp.json(), "sandbox": True}
    except Exception:
        pass
    # Local fallback availability.
    import shutil

    langs = [{"id": "python", "available": True, "ext": "py"}]
    for lid, binary in (("javascript", "node"), ("typescript", "node"), ("bash", "bash")):
        langs.append({"id": lid, "available": shutil.which(binary) is not None, "ext": lid[:2]})
    langs.append({"id": "html", "available": True, "ext": "html"})
    return {"languages": langs, "sandbox": False}


@app.post("/api/sandbox/run")
async def sandbox_run(request: Request):
    """Run a code block directly (without the model) and stream the
    output as SSE. Used by the Run button on code blocks."""
    body = await request.json()
    code = body.get("code") or ""
    if not code.strip():
        raise HTTPException(400, "code is required")
    tool = orchestrator.get_tool("code_executor")
    if tool is None:
        raise HTTPException(400, "code_executor tool is disabled")
    kwargs = {
        "code": code,
        "language": body.get("language") or "python",
        "packages": body.get("packages") or [],
        "stdin": body.get("stdin"),
        "timeout": body.get("timeout"),
        "filename": body.get("filename"),
        "input_files": body.get("input_files") or [],
    }

    async def stream():
        chunks = []
        try:
            async for chunk in tool.stream_execute(**kwargs):
                chunks.append(chunk)
                yield f"data: {json.dumps({'type': 'chunk', 'data': chunk})}\n\n"
            result = {
                "type": "result",
                "output": "\n".join(chunks).strip(),
                "return_code": tool._last_return_code,
                "language": tool._last_language,
                "artifacts": tool._last_artifacts or [],
            }
            if tool._last_preview:
                result["downloadable"] = tool._last_preview
            yield f"data: {json.dumps(result)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


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
        async with websockets.connect(target, ping_interval=None, max_size=None) as sandbox_ws:
            await sandbox_ws.send(json.dumps(session.initial_message()))

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
        plots_dir.mkdir(exist_ok=True)
        filepath = plots_dir / filename
        await asyncio.to_thread(fig.write_image, str(filepath), width=1200, height=700, scale=2)

        return JSONResponse({"filename": filename})
    except HTTPException:
        raise
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
        client = orchestrator.mcp.get_client(server_id)
        detail = client.last_error if client else "not found"
        raise HTTPException(404, f"MCP server '{server_id}' not reachable: {detail}")
    return {"status": "connected", "server": server_id}


def _server_id_from_payload(payload: dict) -> str:
    sid = payload.get("id") or payload.get("name") or payload.get("url") or payload.get("command") or "server"
    sid = re.sub(r"[^a-zA-Z0-9_\-]+", "-", str(sid)).strip("-").lower()
    return sid[:60] or f"server-{uuid.uuid4().hex[:6]}"


@app.post("/api/mcp/servers/discover")
async def discover_mcp_server(payload: dict):
    """Probe an MCP server (URL or command) without persisting it."""
    from backend.mcp.client import MCPClient

    if not (payload.get("url") or payload.get("command")):
        raise HTTPException(400, "url or command is required")
    temp_id = "discover-" + uuid.uuid4().hex[:6]
    try:
        client = MCPClient(temp_id, {**payload, "timeout": payload.get("timeout", 60)})
    except ValueError as e:
        raise HTTPException(400, str(e))
    connected = await client.connect()
    info = client.info()
    tools = client.tools
    await client.close()
    if not connected:
        raise HTTPException(502, f"Could not connect to MCP server: {client.last_error}")
    return {"server": info, "tools": tools, "resources": client.resources}


@app.post("/api/mcp/servers")
async def add_mcp_server(payload: dict):
    """Add a new MCP server (URL or stdio command) and persist it."""
    if not (payload.get("url") or payload.get("command")):
        raise HTTPException(400, "url or command is required")
    server_id = _server_id_from_payload(payload)
    cfg = {k: v for k, v in payload.items() if k != "id"}
    cfg.setdefault("source", "user")
    try:
        info = await orchestrator.add_mcp_server(server_id, cfg)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not info:
        client = orchestrator.mcp.get_client(server_id)
        raise HTTPException(502, f"Could not connect to MCP server: {client.last_error if client else 'unknown error'}")
    return {"status": "added", "server": info}


@app.delete("/api/mcp/servers/{server_id}")
async def remove_mcp_server(server_id: str):
    """Remove a persisted MCP server."""
    ok = await orchestrator.remove_mcp_server(server_id)
    if not ok:
        raise HTTPException(404, f"MCP server '{server_id}' not found")
    return {"status": "removed", "server": server_id}


@app.get("/api/mcp/registry/search")
async def mcp_registry_search(q: str = "", limit: int = 30, cursor: str | None = None):
    """Search the official MCP Registry (GitHub / Claude / ChatGPT
    marketplace catalog) for installable servers."""
    try:
        return await search_registry(q, limit=limit, cursor=cursor)
    except Exception as e:
        raise HTTPException(502, f"Registry search failed: {e}")


@app.post("/api/mcp/import")
async def mcp_import(payload: dict):
    """Import an ``mcpServers`` JSON block (Claude Desktop / ChatGPT /
    Cursor / VS Code format) and connect every server in it."""
    raw = payload.get("config") if "config" in payload else payload
    try:
        servers = parse_mcp_config_import(raw)
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(400, f"Invalid config: {e}")
    if not servers:
        raise HTTPException(400, "No MCP servers found in config")
    results = []
    for sid, cfg in servers.items():
        info = await orchestrator.add_mcp_server(sid, cfg)
        client = orchestrator.mcp.get_client(sid)
        results.append({
            "id": sid,
            "connected": bool(info),
            "tools": info["tools"] if info else [],
            "error": None if info else (client.last_error if client else "unknown"),
        })
    return {"imported": results}


@app.get("/api/mcp/servers/{server_id}/resources")
async def mcp_resources(server_id: str):
    return {"resources": await orchestrator.mcp.list_resources(server_id)}


@app.post("/api/mcp/servers/{server_id}/resources/ingest")
async def mcp_ingest_resource(server_id: str, payload: dict):
    """Read an MCP resource and index it into the RAG vector store so it
    can be used as context like an uploaded file."""
    uri = payload.get("uri")
    if not uri:
        raise HTTPException(400, "uri is required")
    try:
        data = await orchestrator.mcp.read_resource(server_id, uri)
    except Exception as e:
        raise HTTPException(502, f"Could not read resource: {e}")
    text = data.get("text") or ""
    if not text.strip():
        raise HTTPException(400, "Resource has no text content to index")
    name = payload.get("filename") or re.sub(r"[^\w.\-]+", "_", uri.split("/")[-1] or "resource")[:80]
    if "." not in name:
        name += ".md"
    name = safe_filename(f"mcp-{server_id}-{name}")
    target = upload_dir / name
    target.write_text(text, encoding="utf-8")
    result = {"filename": name, "size": len(text)}
    if orchestrator.rag_engine:
        result["ingest"] = await orchestrator.rag_engine.ingest_file(
            target, metadata={"mcp_server": server_id, "mcp_uri": uri}
        )
    return result


# ------ Conversation Endpoints ------

from starlette.requests import Request as StarletteRequest


@app.get("/api/conversations")
async def list_conversations_endpoint():
    """List all saved conversations."""
    return {"conversations": conversations.list_conversations()}


@app.get("/api/conversations/{conversation_id}")
async def get_conversation_endpoint(conversation_id: str):
    """Load a specific conversation."""
    data = conversations.get_conversation(conversation_id)
    if not data:
        raise HTTPException(404, "Conversation not found")
    return data


@app.post("/api/conversations")
async def save_conversation_post(request: StarletteRequest):
    """Create or update a conversation."""
    body = await request.json()
    result = conversations.save_conversation(
        conversation_id=body.get("id"),
        messages=body.get("messages", []),
        model=body.get("model", ""),
        token_usage=body.get("token_usage"),
        extra={
            k: body[k] for k in ("compaction_state", "generation", "selected_tools", "selected_files")
            if k in body
        },
    )
    return result


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation_endpoint(conversation_id: str):
    """Delete a conversation."""
    ok = conversations.delete_conversation(conversation_id)
    if not ok:
        raise HTTPException(404, "Conversation not found")
    return {"deleted": conversation_id}


# ------ WebSocket Chat ------


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket endpoint for streaming chat."""
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
            generation = request.get("generation") or None
            compaction_state = request.get("compaction_state") or None
            tool_mode = request.get("tool_mode") or None

            async for event in orchestrator.chat_stream(
                messages=messages,
                model_id=model,
                selected_tools=tools,
                selected_files=files,
                system_prompt=system,
                generation=generation,
                compaction_state=compaction_state,
                tool_mode=tool_mode,
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

    _frontend_root = frontend_dir.resolve()

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        try:
            file_path = (_frontend_root / path).resolve()
        except (OSError, ValueError):
            file_path = _frontend_root / "index.html"
        # Never serve anything outside the built frontend directory.
        if _frontend_root in file_path.parents and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_frontend_root / "index.html")


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
