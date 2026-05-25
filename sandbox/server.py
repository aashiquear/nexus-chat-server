"""
Nexus Sandbox - HTTP/WebSocket server for executing Python code.

This service runs inside the `nexus-sandbox` container and is reachable
from the `nexus-chat` backend over the `nexus-net` Docker network. It
replaces the previous `docker exec` flow (which required the backend
container to have access to the Docker socket — it does not).

Endpoints
─────────
POST /exec           — One-shot execution, streams stdout/stderr lines
                       as Server-Sent Events. Body: {"code": "..."}.
WebSocket /interact  — Interactive REPL session. Client → server: text
                       payloads to send as stdin. Server → client:
                       {"type": "stdout|stderr|exit", "data": "..."}.
                       Initial client message must be {"code": "..."}.
"""

import asyncio
import json
import logging
import os
import shlex
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Nexus Sandbox", version="0.1.0")

SANDBOX_ROOT = Path("/tmp/nexus-sandbox")
SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)

EXEC_TIMEOUT = int(os.environ.get("SANDBOX_TIMEOUT", "60"))


class ExecRequest(BaseModel):
    code: str


@app.get("/health")
async def health():
    return {"status": "ok"}


def _write_code_file(code: str) -> Path:
    session_id = uuid.uuid4().hex[:12]
    code_path = SANDBOX_ROOT / f"{session_id}.py"
    code_path.write_text(code, encoding="utf-8")
    return code_path


@app.post("/exec")
async def exec_code(req: ExecRequest):
    """Stream output from a one-shot Python run as SSE."""
    code_path = _write_code_file(req.code)

    async def event_stream():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", str(code_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(SANDBOX_ROOT),
        )

        queue: asyncio.Queue = asyncio.Queue()

        async def pump(stream, kind):
            while True:
                line = await stream.readline()
                if not line:
                    break
                await queue.put((kind, line.decode("utf-8", errors="replace").rstrip("\n")))
            await queue.put((f"{kind}_eof", None))

        stdout_task = asyncio.create_task(pump(proc.stdout, "stdout"))
        stderr_task = asyncio.create_task(pump(proc.stderr, "stderr"))

        eofs = 0
        try:
            while eofs < 2:
                try:
                    kind, line = await asyncio.wait_for(queue.get(), timeout=EXEC_TIMEOUT)
                except asyncio.TimeoutError:
                    proc.kill()
                    yield f"data: {json.dumps({'type': 'stderr', 'data': 'Execution timed out'})}\n\n"
                    break

                if kind.endswith("_eof"):
                    eofs += 1
                    continue
                yield f"data: {json.dumps({'type': kind, 'data': line})}\n\n"

            await proc.wait()
            yield f"data: {json.dumps({'type': 'exit', 'code': proc.returncode or 0})}\n\n"
        finally:
            stdout_task.cancel()
            stderr_task.cancel()
            try:
                code_path.unlink()
            except OSError:
                pass

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.websocket("/interact")
async def interact(ws: WebSocket):
    """Bi-directional interactive Python session.

    Protocol:
      - First client → server message must be JSON {"code": "..."}.
      - Subsequent client → server messages are stdin lines.
      - Server → client: JSON {"type": "stdout|stderr|exit|ready", ...}
    """
    await ws.accept()
    proc = None
    code_path = None
    pump_tasks: list[asyncio.Task] = []
    try:
        first = await ws.receive_text()
        try:
            payload = json.loads(first)
            code = payload.get("code", "")
        except json.JSONDecodeError:
            await ws.send_text(json.dumps({"type": "stderr", "data": "Expected JSON {code: ...}"}))
            return

        if not code:
            await ws.send_text(json.dumps({"type": "stderr", "data": "Empty code"}))
            return

        code_path = _write_code_file(code)

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", str(code_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(SANDBOX_ROOT),
        )

        await ws.send_text(json.dumps({"type": "ready"}))

        async def pump(stream, kind):
            while True:
                line = await stream.readline()
                if not line:
                    break
                try:
                    await ws.send_text(json.dumps({
                        "type": kind,
                        "data": line.decode("utf-8", errors="replace").rstrip("\n"),
                    }))
                except Exception:
                    break

        pump_tasks = [
            asyncio.create_task(pump(proc.stdout, "stdout")),
            asyncio.create_task(pump(proc.stderr, "stderr")),
        ]

        async def stdin_pump():
            while True:
                try:
                    data = await ws.receive_text()
                except WebSocketDisconnect:
                    break
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.write((data + "\n").encode())
                    try:
                        await proc.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        break

        stdin_task = asyncio.create_task(stdin_pump())

        try:
            await asyncio.wait_for(proc.wait(), timeout=EXEC_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await ws.send_text(json.dumps({"type": "stderr", "data": "Execution timed out"}))

        # Drain any remaining output
        await asyncio.gather(*pump_tasks, return_exceptions=True)
        stdin_task.cancel()

        await ws.send_text(json.dumps({"type": "exit", "code": proc.returncode or 0}))
    except WebSocketDisconnect:
        logger.info("Client disconnected from /interact")
    except Exception as e:
        logger.exception("Interactive session failed")
        try:
            await ws.send_text(json.dumps({"type": "stderr", "data": f"Sandbox error: {e}"}))
        except Exception:
            pass
    finally:
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        for t in pump_tasks:
            t.cancel()
        if code_path:
            try:
                code_path.unlink()
            except OSError:
                pass
        try:
            await ws.close()
        except Exception:
            pass
