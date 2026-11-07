"""
In-memory registry for interactive sandbox sessions.

When a tool call requests ``interactive: true``, the CodeExecutorTool
generates a session id, stashes the code + sandbox URL here, and
returns. The frontend then opens
``ws://<host>/ws/sandbox/interact/{session_id}`` which the main app
proxies to the sandbox container's ``/interact`` WebSocket.

Sessions expire after a fixed TTL (default 5 minutes) so abandoned
codes don't accumulate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class SandboxSession:
    code: str
    sandbox_url: str
    created_at: float
    language: str = "python"
    packages: list[str] = field(default_factory=list)
    timeout: int = 120
    owner: str | None = None
    # Uploaded input files to materialise in the run directory: {name: base64}
    files: dict = field(default_factory=dict)

    def initial_message(self) -> dict:
        msg = {
            "code": self.code,
            "language": self.language,
            "packages": self.packages,
            "timeout": self.timeout,
        }
        if self.files:
            msg["files"] = self.files
        return msg


_SESSIONS: Dict[str, SandboxSession] = {}
_TTL_SECONDS = 300


def _purge_expired() -> None:
    now = time.time()
    expired = [sid for sid, s in _SESSIONS.items() if now - s.created_at > _TTL_SECONDS]
    for sid in expired:
        _SESSIONS.pop(sid, None)


def register_session(
    session_id: str,
    code: str,
    sandbox_url: str,
    language: str = "python",
    packages: list[str] | None = None,
    timeout: int = 120,
    owner: str | None = None,
    files: dict | None = None,
) -> None:
    _purge_expired()
    _SESSIONS[session_id] = SandboxSession(
        code=code,
        sandbox_url=sandbox_url,
        created_at=time.time(),
        language=language,
        packages=list(packages or []),
        timeout=timeout,
        owner=owner,
        files=dict(files or {}),
    )


def consume_session(session_id: str) -> SandboxSession | None:
    """Pop a session — used when the WebSocket proxy starts."""
    _purge_expired()
    return _SESSIONS.pop(session_id, None)


def peek_session(session_id: str) -> SandboxSession | None:
    _purge_expired()
    return _SESSIONS.get(session_id)
