"""Code executor tool — runs code in the nexus-sandbox container.

Supports many languages (python, javascript, typescript, bash, java, c,
cpp, go, rust, r, html), installs missing packages on the fly, streams
stdout/stderr live into the canvas terminal, and collects files the
program writes (plots, CSVs, HTML, PDFs …) as downloadable artifacts.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
import shutil
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from . import BaseTool, register_tool

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = [
    "python", "javascript", "typescript", "bash", "java", "c", "cpp", "go", "rust", "r", "html",
]

LOCAL_RUNNERS = {
    "python": lambda path: [sys.executable, "-u", str(path)],
    "javascript": lambda path: ["node", str(path)],
    "typescript": lambda path: ["node", "--experimental-strip-types", "--no-warnings", str(path)],
    "bash": lambda path: ["bash", str(path)],
}
LOCAL_EXT = {"python": "py", "javascript": "js", "typescript": "ts", "bash": "sh", "html": "html"}


@register_tool("code_executor")
class CodeExecutorTool(BaseTool):
    name = "code_executor"
    description = (
        "Execute code in an isolated sandbox and return its output. Supported "
        "languages: python (default), javascript, typescript, bash, java, c, cpp, "
        "go, rust, r, and html (saved and shown in the preview panel). Missing "
        "Python/npm packages are installed automatically; list extra ones in "
        "`packages`. Live output streams to the canvas terminal. Files written to "
        "the working directory (e.g. plot.png, result.csv, report.pdf, page.html) "
        "are returned to the user as downloadable/previewable artifacts — save "
        "plots with plt.savefig('name.png') instead of plt.show(). Uploaded files "
        "listed in `input_files` are copied into the working directory. Set "
        "`interactive=true` only when the program reads from stdin."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Source code to execute."},
            "language": {
                "type": "string",
                "enum": SUPPORTED_LANGUAGES,
                "description": "Programming language of `code` (default python).",
            },
            "packages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Extra packages to install before running (pip names for python, npm names for javascript/typescript).",
            },
            "input_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of uploaded files to copy into the sandbox working directory.",
            },
            "stdin": {"type": "string", "description": "Text to feed to the program's standard input."},
            "timeout": {"type": "integer", "description": "Max run time in seconds (default 120)."},
            "filename": {"type": "string", "description": "Optional file name for the main source file (e.g. app.js, index.html)."},
            "interactive": {
                "type": "boolean",
                "description": (
                    "If true, expose a stdin channel via the canvas terminal so "
                    "users can type input while the program runs. Default false."
                ),
            },
        },
        "required": ["code"],
    }

    DEFAULT_SANDBOX_URL = "http://nexus-sandbox:8500"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self._last_output = ""
        self._last_return_code = 0
        self._last_language = "python"
        self._last_session_id: str | None = None
        self._last_artifacts: list[dict] | None = None
        self._last_preview: dict | None = None

    @property
    def sandbox_url(self) -> str:
        return self.config.get("sandbox_url") or os.environ.get(
            "NEXUS_SANDBOX_URL",
            self.DEFAULT_SANDBOX_URL,
        )

    @staticmethod
    def normalize_language(lang: str | None) -> str:
        key = (lang or "python").strip().lower()
        aliases = {
            "py": "python", "python3": "python", "js": "javascript", "node": "javascript",
            "ts": "typescript", "sh": "bash", "shell": "bash", "zsh": "bash", "c++": "cpp",
            "cxx": "cpp", "cc": "cpp", "golang": "go", "rs": "rust", "htm": "html",
        }
        return aliases.get(key, key)

    async def execute(self, **kwargs) -> str:
        """Non-streaming fallback: collect all chunks and return as JSON."""
        chunks = []
        async for chunk in self.stream_execute(**kwargs):
            chunks.append(chunk)
        result = {
            "output": "\n".join(chunks).strip() or "(no output)",
            "return_code": self._last_return_code,
            "language": self._last_language,
        }
        if self._last_session_id:
            result["interactive_session"] = self._last_session_id
        if self._last_artifacts:
            result["artifacts"] = self._last_artifacts
        if self._last_preview:
            result["downloadable"] = self._last_preview
        return json.dumps(result)

    # ------------------------------------------------------------------
    def _collect_input_files(self, names: list[str] | None) -> dict[str, str]:
        files: dict[str, str] = {}
        for name in names or []:
            safe = Path(name).name
            for base in (self.upload_dir(), Path(self.ctx.files_dir), Path(self.ctx.downloads_dir)):
                p = Path(base) / safe
                if p.is_file():
                    try:
                        if p.stat().st_size <= 25 * 1024 * 1024:
                            files[safe] = base64.b64encode(p.read_bytes()).decode()
                    except OSError:
                        pass
                    break
        return files

    def _store_artifact(self, name: str, data: bytes) -> dict:
        """Copy a sandbox artifact into the generated-files directory and
        describe it in the ``downloadable`` shape the UI already renders."""
        files_dir = Path(self.ctx.files_dir)
        files_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(name).stem
        suffix = Path(name).suffix
        safe_stem = re.sub(r"[^\w.\-]+", "_", stem)[:60] or "artifact"
        target = files_dir / f"{safe_stem}-{uuid.uuid4().hex[:6]}{suffix}"
        target.write_bytes(data)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        suffix_q = self.ctx.url_prefix()
        return {
            "filename": target.name,
            "original_name": name,
            "content_type": mime,
            "size": len(data),
            "download_url": f"/api/download/{target.name}{suffix_q}",
            "preview_url": f"/api/files/{target.name}{suffix_q}",
        }

    async def stream_execute(self, **kwargs) -> AsyncIterator[str]:
        """Stream stdout/stderr lines from the sandbox HTTP service in
        real time. Falls back to a local subprocess if the sandbox
        endpoint is unreachable — useful for dev runs without docker."""
        import httpx

        code = kwargs.get("code", "")
        language = self.normalize_language(kwargs.get("language"))
        interactive = bool(kwargs.get("interactive", False))
        timeout = int(kwargs.get("timeout") or self.config.get("timeout", 120))
        packages = [str(p) for p in (kwargs.get("packages") or [])]

        self._last_session_id = None
        self._last_return_code = 0
        self._last_language = language
        self._last_artifacts = None
        self._last_preview = None

        if language not in SUPPORTED_LANGUAGES:
            yield f"[stderr]: unsupported language '{language}'. Supported: {', '.join(SUPPORTED_LANGUAGES)}"
            self._last_return_code = 1
            return

        if language == "html":
            # No execution needed: persist and hand back a preview card.
            name = Path(kwargs.get("filename") or "page.html").name
            if not name.lower().endswith((".html", ".htm")):
                name += ".html"
            self._last_preview = self._store_artifact(name, code.encode("utf-8"))
            self._last_artifacts = [self._last_preview]
            yield f"Saved {self._last_preview['filename']} — open it in the preview panel."
            return

        if interactive:
            self._last_session_id = uuid.uuid4().hex[:16]
            from backend.sandbox_sessions import register_session

            register_session(
                self._last_session_id, code, self.sandbox_url,
                language=language, packages=packages, timeout=timeout,
                owner=self.ctx.username,
                files=self._collect_input_files(kwargs.get("input_files")),
            )
            yield f"[interactive session ready: {self._last_session_id}]"
            yield "Open the canvas terminal to send input and view live output."
            return

        url = self.sandbox_url.rstrip("/") + "/exec"
        payload = {
            "code": code,
            "language": language,
            "packages": packages,
            "auto_install": True,
            "timeout": timeout,
            "stdin": kwargs.get("stdin"),
            "filename": kwargs.get("filename"),
        }
        input_files = self._collect_input_files(kwargs.get("input_files"))
        if input_files:
            payload["files"] = input_files

        artifacts_meta: list[dict] = []
        run_id = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout + 330, read=None)) as client:
                async with client.stream("POST", url, json=payload) as resp:
                    if resp.status_code >= 400:
                        body = await resp.aread()
                        msg = body.decode("utf-8", errors="replace")
                        yield f"[stderr]: sandbox HTTP {resp.status_code}: {msg}"
                        self._last_return_code = 1
                        return

                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload_raw = line[len("data:"):].strip()
                        try:
                            ev = json.loads(payload_raw)
                        except json.JSONDecodeError:
                            yield payload_raw
                            continue
                        kind = ev.get("type")
                        if kind == "stdout":
                            yield ev.get("data", "")
                        elif kind == "stderr":
                            yield "[stderr]: " + ev.get("data", "")
                        elif kind == "status":
                            yield "[status]: " + ev.get("data", "")
                        elif kind == "artifacts":
                            run_id = ev.get("run_id")
                            artifacts_meta = ev.get("files") or []
                        elif kind == "exit":
                            self._last_return_code = int(ev.get("code", 0))

                # Pull produced files back from the sandbox.
                if run_id and artifacts_meta:
                    stored = []
                    for meta in artifacts_meta[:25]:
                        name = meta.get("name")
                        if not name:
                            continue
                        try:
                            r = await client.get(
                                f"{self.sandbox_url.rstrip('/')}/artifacts/{run_id}/{name}"
                            )
                            if r.status_code == 200:
                                stored.append(self._store_artifact(name, r.content))
                        except Exception as e:  # pragma: no cover - network
                            logger.warning("Artifact fetch failed for %s: %s", name, e)
                    if stored:
                        self._last_artifacts = stored
                        yield "[status]: produced " + ", ".join(a["original_name"] for a in stored)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            logger.warning("Sandbox unreachable (%s); falling back to local subprocess.", e)
            async for chunk in self._local_fallback(code, language, timeout, kwargs.get("stdin")):
                yield chunk

    # ------------------------------------------------------------------
    async def _local_fallback(self, code: str, language: str, timeout: int, stdin_text: str | None) -> AsyncIterator[str]:
        """Last-resort local execution when the sandbox is unreachable.

        Runs a one-shot subprocess in a temp directory with no shell. This
        is clearly less safe than the docker sandbox; we emit a warning
        line so the user understands the difference.
        """
        yield "[stderr]: sandbox container not reachable; running locally"

        runner = LOCAL_RUNNERS.get(language)
        if runner is None:
            yield f"[stderr]: local fallback cannot run {language}; start the nexus-sandbox container."
            self._last_return_code = 1
            return
        if language != "python" and shutil.which(runner(Path("x"))[0]) is None:
            yield f"[stderr]: {runner(Path('x'))[0]} is not installed on this host."
            self._last_return_code = 1
            return

        run_dir = (Path("./data/sandbox") / f"local-{uuid.uuid4().hex[:8]}").resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        code_file = run_dir / f"main.{LOCAL_EXT.get(language, 'txt')}"
        code_file.write_text(code, encoding="utf-8")

        env = dict(os.environ)
        env["MPLBACKEND"] = "Agg"
        proc = await asyncio.create_subprocess_exec(
            *runner(code_file),
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

        async def pump(stream, prefix):
            while True:
                line = await stream.readline()
                if not line:
                    break
                await queue.put(prefix + line.decode("utf-8", errors="replace").rstrip("\n"))
            await queue.put(None)

        stdout_task = asyncio.create_task(pump(proc.stdout, ""))
        stderr_task = asyncio.create_task(pump(proc.stderr, "[stderr]: "))

        eofs = 0
        try:
            while eofs < 2:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    proc.kill()
                    yield "[stderr]: local execution timed out"
                    break
                if item is None:
                    eofs += 1
                    continue
                yield item
            await proc.wait()
            self._last_return_code = proc.returncode or 0
            # Collect artifacts written next to the script.
            stored = []
            for p in sorted(run_dir.iterdir()):
                if p.is_file() and p != code_file and p.stat().st_size <= 50 * 1024 * 1024:
                    stored.append(self._store_artifact(p.name, p.read_bytes()))
            if stored:
                self._last_artifacts = stored
                yield "[status]: produced " + ", ".join(a["original_name"] for a in stored)
        finally:
            stdout_task.cancel()
            stderr_task.cancel()
            shutil.rmtree(run_dir, ignore_errors=True)
