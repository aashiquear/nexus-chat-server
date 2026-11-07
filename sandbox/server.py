"""
Nexus Sandbox - HTTP/WebSocket service for executing code in many languages.

Runs inside the ``nexus-sandbox`` container and is reachable from the
``nexus-chat`` backend over the ``nexus-net`` Docker network.

Languages
─────────
python · javascript (node) · typescript (node --experimental-strip-types)
bash/sh · java · c · cpp · go · rust · html (returned as a preview file) · r

Packages on the fly
───────────────────
Each run receives ``packages: [...]``. Python packages are installed with
``pip`` into a persistent per-container site directory (so a second run
is instant), JavaScript packages with ``npm`` into the run directory.
For Python, missing top-level imports are also auto-detected from the
source and installed before execution (``auto_install: true`` default).

Artifacts
─────────
Every run executes in its own working directory. Any file the program
creates there (plots, CSVs, HTML, PDFs…) is listed in the ``artifacts``
event and can be downloaded via ``GET /artifacts/{run_id}/{filename}``.
The backend copies them next to its own generated files so the chat UI
can preview / download them.

Endpoints
─────────
POST /exec                 — one-shot run, streams SSE events:
                              {"type": "stdout"|"stderr"|"status", "data": ...}
                              {"type": "artifacts", "run_id": ..., "files": [...]}
                              {"type": "exit", "code": int}
WebSocket /interact        — interactive session (stdin/stdout bridge)
GET  /artifacts/{run}/{f}  — download a produced file
GET  /languages            — supported languages + availability
GET  /health
"""

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Nexus Sandbox", version="0.2.0")

SANDBOX_ROOT = Path(os.environ.get("SANDBOX_ROOT", "/tmp/nexus-sandbox"))
SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
# Isolation between runs: every run gets a directory with an unguessable
# 128-bit name, mode 0700, and the parent is made non-listable (the image
# creates it root-owned with mode 1733 so the sandbox user cannot relax
# that). Code in one run therefore cannot enumerate or open another run's
# inputs/outputs even though all runs share one OS user. Run directories
# are deleted as soon as the backend has collected the artifacts (see
# DELETE /artifacts/{run_id}) and otherwise expire after RUN_TTL.
try:
    if os.access(SANDBOX_ROOT, os.W_OK) and SANDBOX_ROOT.stat().st_uid == os.getuid():
        os.chmod(SANDBOX_ROOT, 0o733)
except OSError:
    pass
# Persistent location for pip-installed packages so repeat runs are fast.
PIP_TARGET = Path(os.environ.get("SANDBOX_PIP_TARGET", "/sandbox/pylib"))
PIP_TARGET.mkdir(parents=True, exist_ok=True)
NPM_CACHE = Path(os.environ.get("SANDBOX_NPM_CACHE", "/sandbox/npm-cache"))
NPM_CACHE.mkdir(parents=True, exist_ok=True)

EXEC_TIMEOUT = int(os.environ.get("SANDBOX_TIMEOUT", "120"))
INSTALL_TIMEOUT = int(os.environ.get("SANDBOX_INSTALL_TIMEOUT", "300"))
MAX_ARTIFACT_BYTES = int(os.environ.get("SANDBOX_MAX_ARTIFACT_MB", "50")) * 1024 * 1024
MAX_OUTPUT_LINES = int(os.environ.get("SANDBOX_MAX_OUTPUT_LINES", "4000"))
# Runs older than this are purged (seconds). Kept short: the backend
# deletes a run right after fetching its artifacts, this is the fallback.
RUN_TTL = int(os.environ.get("SANDBOX_RUN_TTL", str(30 * 60)))
# Run directories created by this process (the root is not listable).
_RUNS: dict[str, float] = {}


class ExecRequest(BaseModel):
    code: str
    language: str = "python"
    packages: list[str] = []
    auto_install: bool = True
    timeout: int | None = None
    stdin: str | None = None
    filename: str | None = None
    # Optional input files: {name: base64} copied into the working dir.
    files: dict[str, str] | None = None


# ----------------------------------------------------------------------
# Language registry
# ----------------------------------------------------------------------
LANG_ALIASES = {
    "py": "python", "python3": "python", "python": "python",
    "js": "javascript", "node": "javascript", "javascript": "javascript",
    "ts": "typescript", "typescript": "typescript",
    "sh": "bash", "shell": "bash", "bash": "bash", "zsh": "bash",
    "java": "java",
    "c": "c",
    "cpp": "cpp", "c++": "cpp", "cxx": "cpp", "cc": "cpp",
    "go": "go", "golang": "go",
    "rust": "rust", "rs": "rust",
    "html": "html", "htm": "html",
    "r": "r",
}

LANG_SPECS = {
    "python": {"ext": "py", "bin": sys.executable},
    "javascript": {"ext": "js", "bin": "node"},
    "typescript": {"ext": "ts", "bin": "node"},
    "bash": {"ext": "sh", "bin": "bash"},
    "java": {"ext": "java", "bin": "java"},
    "c": {"ext": "c", "bin": "gcc"},
    "cpp": {"ext": "cpp", "bin": "g++"},
    "go": {"ext": "go", "bin": "go"},
    "rust": {"ext": "rs", "bin": "rustc"},
    "html": {"ext": "html", "bin": None},
    "r": {"ext": "R", "bin": "Rscript"},
}


def normalize_language(lang: str | None) -> str:
    key = (lang or "python").strip().lower()
    return LANG_ALIASES.get(key, key)


def language_available(lang: str) -> bool:
    spec = LANG_SPECS.get(lang)
    if not spec:
        return False
    if spec["bin"] is None:
        return True
    return shutil.which(spec["bin"]) is not None or Path(spec["bin"]).exists()


# Common import-name → pip-name mismatches.
PIP_NAME_MAP = {
    "cv2": "opencv-python-headless", "sklearn": "scikit-learn", "PIL": "pillow",
    "yaml": "pyyaml", "bs4": "beautifulsoup4", "Crypto": "pycryptodome",
    "dateutil": "python-dateutil", "docx": "python-docx", "pptx": "python-pptx",
    "fitz": "pymupdf", "attr": "attrs", "skimage": "scikit-image", "Bio": "biopython",
    "wx": "wxPython", "gi": "PyGObject", "OpenSSL": "pyopenssl", "jwt": "pyjwt",
    "serial": "pyserial", "usb": "pyusb", "lxml": "lxml", "mpl_toolkits": "matplotlib",
    "statsmodels": "statsmodels", "tensorflow": "tensorflow", "torch": "torch",
    "nx": "networkx", "sns": "seaborn", "px": "plotly", "sp": "scipy",
}

STDLIB = set(getattr(sys, "stdlib_module_names", set())) | {
    "__future__", "builtins", "typing_extensions",
}

_IMPORT_RE = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w]*)|import\s+([A-Za-z_][\w]*))", re.MULTILINE)


def detect_missing_python_imports(code: str) -> list[str]:
    """Return pip package names for top-level imports that are not installed."""
    import importlib.util

    missing: list[str] = []
    seen: set[str] = set()
    for m in _IMPORT_RE.finditer(code):
        mod = m.group(1) or m.group(2)
        if not mod or mod in seen or mod in STDLIB:
            continue
        seen.add(mod)
        if importlib.util.find_spec(mod) is not None:
            continue
        # Check the sandbox pip target too (not on sys.path of the server).
        if (PIP_TARGET / mod).exists() or list(PIP_TARGET.glob(f"{mod}.py")) or list(PIP_TARGET.glob(f"{mod}-*.dist-info")):
            continue
        missing.append(PIP_NAME_MAP.get(mod, mod))
    return missing


_NPM_REQUIRE_RE = re.compile(
    r"(?:require\(\s*['\"]([^'\"./][^'\"]*)['\"]\s*\)|from\s+['\"]([^'\"./][^'\"]*)['\"])"
)
NODE_BUILTINS = {
    "fs", "path", "os", "http", "https", "url", "util", "crypto", "events", "stream",
    "child_process", "readline", "assert", "buffer", "zlib", "net", "dns", "process",
    "querystring", "timers", "worker_threads", "perf_hooks", "string_decoder", "tty",
}


def detect_node_packages(code: str) -> list[str]:
    pkgs = []
    for m in _NPM_REQUIRE_RE.finditer(code):
        name = m.group(1) or m.group(2)
        if not name:
            continue
        name = name.replace("node:", "")
        base = "/".join(name.split("/")[:2]) if name.startswith("@") else name.split("/")[0]
        if base in NODE_BUILTINS or base in pkgs:
            continue
        pkgs.append(base)
    return pkgs


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _new_run_dir() -> tuple[str, Path]:
    import secrets
    import time

    _purge_old_runs()
    run_id = secrets.token_hex(16)
    d = SANDBOX_ROOT / run_id
    d.mkdir(parents=True, exist_ok=False, mode=0o700)
    _RUNS[run_id] = time.time()
    return run_id, d


def _remove_run(run_id: str) -> bool:
    _RUNS.pop(run_id, None)
    d = SANDBOX_ROOT / run_id
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


def _purge_old_runs() -> None:
    import time

    now = time.time()
    for run_id, created in list(_RUNS.items()):
        if now - created > RUN_TTL:
            _remove_run(run_id)


def _safe_name(name: str | None, default: str) -> str:
    if not name:
        return default
    name = Path(name).name
    return name if re.fullmatch(r"[\w.\-]+", name) else default


def _java_class_name(code: str) -> str:
    m = re.search(r"public\s+(?:final\s+)?class\s+([A-Za-z_]\w*)", code)
    if m:
        return m.group(1)
    m = re.search(r"\bclass\s+([A-Za-z_]\w*)", code)
    return m.group(1) if m else "Main"


def _env_for(run_dir: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PIP_TARGET) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUNBUFFERED"] = "1"
    env["MPLBACKEND"] = "Agg"
    env["HOME"] = str(run_dir)
    env["TMPDIR"] = str(run_dir)
    env["NODE_PATH"] = str(run_dir / "node_modules")
    env["npm_config_cache"] = str(NPM_CACHE)
    env["GOPATH"] = str(SANDBOX_ROOT / ".gopath")
    env["GOCACHE"] = str(SANDBOX_ROOT / ".gocache")
    env["GOFLAGS"] = "-mod=mod"
    env["CARGO_HOME"] = str(SANDBOX_ROOT / ".cargo")
    return env


def _write_input_files(run_dir: Path, files: dict[str, str] | None) -> None:
    import base64

    for name, b64 in (files or {}).items():
        safe = _safe_name(name, "input.bin")
        try:
            (run_dir / safe).write_bytes(base64.b64decode(b64))
        except Exception as e:
            logger.warning("Could not write input file %s: %s", name, e)


def _list_artifacts(run_dir: Path, before: set[str], exclude: set[str]) -> list[dict]:
    files = []
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(run_dir))
        if rel in before or rel in exclude or rel.startswith(("node_modules/", ".", "__pycache__/", "target/")):
            continue
        if p.suffix in (".class", ".o", ".pyc") or rel in ("a.out", "main", "main.exe"):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > MAX_ARTIFACT_BYTES:
            continue
        files.append({"name": rel, "size": size})
    return files


def _build_commands(lang: str, code: str, run_dir: Path, filename: str | None) -> tuple[list[list[str]], list[str], str]:
    """Return (compile_steps, run_cmd, main_file)."""
    spec = LANG_SPECS[lang]
    if lang == "java":
        cls = _java_class_name(code)
        main = run_dir / f"{cls}.java"
        main.write_text(code, encoding="utf-8")
        # Single-file source launch (Java 11+) — no javac step required.
        return [], ["java", str(main)], main.name
    if lang == "go":
        main = run_dir / "main.go"
        main.write_text(code, encoding="utf-8")
        return [], ["go", "run", str(main)], main.name
    if lang == "rust":
        main = run_dir / "main.rs"
        main.write_text(code, encoding="utf-8")
        return [["rustc", "-O", "-o", str(run_dir / "main"), str(main)]], [str(run_dir / "main")], main.name
    if lang == "c":
        main = run_dir / "main.c"
        main.write_text(code, encoding="utf-8")
        return [["gcc", "-O2", "-std=c11", "-o", str(run_dir / "a.out"), str(main), "-lm"]], [str(run_dir / "a.out")], main.name
    if lang == "cpp":
        main = run_dir / "main.cpp"
        main.write_text(code, encoding="utf-8")
        return [["g++", "-O2", "-std=c++17", "-o", str(run_dir / "a.out"), str(main)]], [str(run_dir / "a.out")], main.name
    if lang == "bash":
        main = run_dir / _safe_name(filename, "script.sh")
        main.write_text(code, encoding="utf-8")
        return [], ["bash", str(main)], main.name
    if lang == "javascript":
        main = run_dir / _safe_name(filename, "main.js")
        main.write_text(code, encoding="utf-8")
        return [], ["node", str(main)], main.name
    if lang == "typescript":
        main = run_dir / _safe_name(filename, "main.ts")
        main.write_text(code, encoding="utf-8")
        return [], ["node", "--experimental-strip-types", "--no-warnings", str(main)], main.name
    if lang == "r":
        main = run_dir / _safe_name(filename, "main.R")
        main.write_text(code, encoding="utf-8")
        return [], ["Rscript", str(main)], main.name
    # python
    main = run_dir / _safe_name(filename, "main.py")
    main.write_text(code, encoding="utf-8")
    return [], [sys.executable, "-u", str(main)], main.name


async def _run_streaming(cmd: list[str], run_dir: Path, env: dict, timeout: int, stdin_text: str | None, emit):
    """Run ``cmd`` and feed stdout/stderr lines to ``emit(kind, line)``.
    Returns the exit code (124 on timeout)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(run_dir),
        env=env,
    )
    if stdin_text is not None and proc.stdin:
        proc.stdin.write(stdin_text.encode())
        await proc.stdin.drain()
        proc.stdin.close()

    queue: asyncio.Queue = asyncio.Queue()

    async def pump(stream, kind):
        count = 0
        while True:
            line = await stream.readline()
            if not line:
                break
            count += 1
            if count <= MAX_OUTPUT_LINES:
                await queue.put((kind, line.decode("utf-8", errors="replace").rstrip("\n")))
            elif count == MAX_OUTPUT_LINES + 1:
                await queue.put(("stderr", f"[output truncated after {MAX_OUTPUT_LINES} lines]"))
        await queue.put((f"{kind}_eof", None))

    tasks = [asyncio.create_task(pump(proc.stdout, "stdout")), asyncio.create_task(pump(proc.stderr, "stderr"))]
    eofs = 0
    code = None
    try:
        while eofs < 2:
            try:
                kind, line = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await emit("stderr", f"Execution timed out after {timeout}s")
                code = 124
                break
            if kind.endswith("_eof"):
                eofs += 1
                continue
            await emit(kind, line)
        if code is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            code = proc.returncode or 0
    finally:
        for t in tasks:
            t.cancel()
        if proc.returncode is None:
            proc.kill()
    return code


async def _install_packages(lang: str, packages: list[str], run_dir: Path, env: dict, emit) -> bool:
    """Install packages for the run. Returns False if installation failed."""
    packages = [p for p in dict.fromkeys(packages) if re.fullmatch(r"[A-Za-z0-9_.\-\[\]@/<>=!~,+]+", p)]
    if not packages:
        return True
    if lang == "python":
        await emit("status", f"Installing Python packages: {' '.join(packages)}")
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
               "--no-input", "--target", str(PIP_TARGET), "--upgrade", *packages]
    elif lang in ("javascript", "typescript"):
        await emit("status", f"Installing npm packages: {' '.join(packages)}")
        if not (run_dir / "package.json").exists():
            (run_dir / "package.json").write_text('{"name":"nexus-run","private":true,"type":"commonjs"}')
        cmd = ["npm", "install", "--silent", "--no-audit", "--no-fund", "--prefix", str(run_dir), *packages]
    else:
        await emit("stderr", f"Package installation is not supported for {lang}; ignoring {packages}")
        return True

    code = await _run_streaming(cmd, run_dir, env, INSTALL_TIMEOUT, None, emit)
    if code != 0:
        await emit("stderr", f"Package installation failed (exit {code}).")
        return False
    await emit("status", "Packages installed.")
    return True


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "version": app.version}


@app.get("/languages")
async def languages():
    return {
        "languages": [
            {"id": lang, "available": language_available(lang), "ext": spec["ext"]}
            for lang, spec in LANG_SPECS.items()
        ]
    }


@app.delete("/artifacts/{run_id}")
async def delete_run(run_id: str):
    """Remove a run directory once the backend has collected its files."""
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise HTTPException(404, "Unknown run")
    return {"deleted": _remove_run(run_id)}


@app.get("/artifacts/{run_id}/{path:path}")
async def download_artifact(run_id: str, path: str):
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise HTTPException(404, "Unknown run")
    base = (SANDBOX_ROOT / run_id).resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(target)


@app.post("/exec")
async def exec_code(req: ExecRequest):
    """Stream output from a one-shot run as SSE."""
    lang = normalize_language(req.language)
    if lang not in LANG_SPECS:
        raise HTTPException(400, f"Unsupported language '{req.language}'. Supported: {sorted(LANG_SPECS)}")
    timeout = min(max(5, req.timeout or EXEC_TIMEOUT), 3600)
    run_id, run_dir = _new_run_dir()
    _write_input_files(run_dir, req.files)
    env = _env_for(run_dir)

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(kind, data):
            await queue.put({"type": kind, "data": data})

        async def worker():
            exit_code = 1
            try:
                before = {str(p.relative_to(run_dir)) for p in run_dir.rglob("*") if p.is_file()}
                if lang == "html":
                    main = run_dir / _safe_name(req.filename, "index.html")
                    main.write_text(req.code, encoding="utf-8")
                    await emit("status", f"Saved {main.name}; open it in the canvas preview.")
                    await queue.put({"type": "artifacts", "run_id": run_id,
                                     "files": [{"name": main.name, "size": main.stat().st_size, "preview": True}]})
                    exit_code = 0
                    return
                if not language_available(lang):
                    await emit("stderr", f"{lang} runtime is not installed in this sandbox image.")
                    return

                packages = list(req.packages or [])
                if req.auto_install and lang == "python":
                    packages += detect_missing_python_imports(req.code)
                if req.auto_install and lang in ("javascript", "typescript"):
                    packages += detect_node_packages(req.code)
                if packages and not await _install_packages(lang, packages, run_dir, env, emit):
                    return

                compile_steps, run_cmd, main_name = _build_commands(lang, req.code, run_dir, req.filename)
                for step in compile_steps:
                    await emit("status", f"Compiling ({step[0]})…")
                    code = await _run_streaming(step, run_dir, env, min(timeout, INSTALL_TIMEOUT), None, emit)
                    if code != 0:
                        await emit("stderr", f"Compilation failed (exit {code}).")
                        exit_code = code
                        return
                exit_code = await _run_streaming(run_cmd, run_dir, env, timeout, req.stdin, emit)
                files = _list_artifacts(run_dir, before, {main_name, "package.json", "package-lock.json"})
                if files:
                    await queue.put({"type": "artifacts", "run_id": run_id, "files": files})
            except Exception as e:
                logger.exception("Run failed")
                await emit("stderr", f"Sandbox error: {e}")
            finally:
                await queue.put({"type": "exit", "code": exit_code, "run_id": run_id})
                await queue.put(None)

        task = asyncio.create_task(worker())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.websocket("/interact")
async def interact(ws: WebSocket):
    """Bi-directional interactive session.

    Protocol:
      - First client → server message must be JSON
        {"code": "...", "language": "python", "packages": [...]}.
      - Subsequent client → server messages are stdin lines.
      - Server → client: JSON {"type": "stdout|stderr|status|exit|ready", ...}
    """
    await ws.accept()
    proc = None
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

        lang = normalize_language(payload.get("language"))
        if lang not in LANG_SPECS or lang == "html":
            await ws.send_text(json.dumps({"type": "stderr", "data": f"Unsupported interactive language '{lang}'"}))
            return
        if not language_available(lang):
            await ws.send_text(json.dumps({"type": "stderr", "data": f"{lang} runtime is not installed in this sandbox image."}))
            return

        timeout = min(max(5, int(payload.get("timeout") or EXEC_TIMEOUT)), 3600)
        run_id, run_dir = _new_run_dir()
        _write_input_files(run_dir, payload.get("files"))
        env = _env_for(run_dir)

        async def emit(kind, data):
            try:
                await ws.send_text(json.dumps({"type": kind, "data": data}))
            except Exception:
                pass

        packages = list(payload.get("packages") or [])
        if payload.get("auto_install", True) and lang == "python":
            packages += detect_missing_python_imports(code)
        if packages and not await _install_packages(lang, packages, run_dir, env, emit):
            await ws.send_text(json.dumps({"type": "exit", "code": 1}))
            return

        compile_steps, run_cmd, _main = _build_commands(lang, code, run_dir, payload.get("filename"))
        for step in compile_steps:
            await emit("status", f"Compiling ({step[0]})…")
            rc = await _run_streaming(step, run_dir, env, INSTALL_TIMEOUT, None, emit)
            if rc != 0:
                await ws.send_text(json.dumps({"type": "exit", "code": rc}))
                return

        proc = await asyncio.create_subprocess_exec(
            *run_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(run_dir),
            env=env,
        )

        await ws.send_text(json.dumps({"type": "ready", "run_id": run_id}))

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
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await ws.send_text(json.dumps({"type": "stderr", "data": f"Execution timed out after {timeout}s"}))

        await asyncio.gather(*pump_tasks, return_exceptions=True)
        stdin_task.cancel()

        inputs = {_safe_name(n, "input.bin") for n in (payload.get("files") or {})}
        files = _list_artifacts(run_dir, inputs, {_main})
        if files:
            await ws.send_text(json.dumps({"type": "artifacts", "run_id": run_id, "files": files}))
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
        try:
            await ws.close()
        except Exception:
            pass
