"""
Authentication, user management and per-user workspaces for Nexus Chat Server.

Users live in ``data/users.json`` (bcrypt password hashes, no extra DB).
Sessions are JWTs delivered both as a bearer token (for fetch / XHR /
WebSocket query) and as an HttpOnly cookie (so ``<img>``, ``<iframe>`` and
``<embed>`` previews work without leaking the token into URLs).

Per-user filesystem layout (``data/users/<username>/``)::

    uploads/         RAG uploads & vision/tool inputs
    files/           tool-generated files (sandbox artifacts, SVGs, …)
    downloads/       file_generator output (PDF/DOCX/PPTX/PNG)
    plots/           graph_plotter PNGs
    conversations/   saved chat threads
    state/           memory, tasks, change-tracker snapshots
    mcp_servers.json user-added MCP servers
    settings.json    user preferences (generation defaults, …)

Usernames are restricted to ``[A-Za-z0-9._-]`` (max 32 chars) so they can
be used as directory names without any path-traversal risk. Directories
are created on user creation *and* lazily on every login, so a recreated
Docker volume or a restored backup never leaves a user without a
workspace.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, WebSocketException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

DATA_ROOT = Path(os.environ.get("NEXUS_DATA_DIR", "./data"))
USERS_FILE = DATA_ROOT / "users.json"
USERS_ROOT = DATA_ROOT / "users"
# Legacy layouts from earlier server versions (migrated on startup).
LEGACY_CONVERSATIONS_ROOT = DATA_ROOT / "conversations"
LEGACY_UPLOADS = DATA_ROOT / "uploads"

USER_DIRS = ("uploads", "files", "downloads", "plots", "conversations", "sandbox", "state")
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
COOKIE_NAME = "nexus_token"

# Lock to prevent concurrent writes to users.json
_users_lock = asyncio.Lock()

security = HTTPBearer(auto_error=False)


# ----------------------------------------------------------------------
# Users file
# ----------------------------------------------------------------------
def _load_users() -> dict[str, dict]:
    if not USERS_FILE.exists():
        return {}
    try:
        data = json.loads(USERS_FILE.read_text())
        return {u["username"]: u for u in data.get("users", [])}
    except Exception as e:
        logger.error("Failed to load users: %s", e)
        return {}


async def _save_users(users: dict[str, dict]):
    async with _users_lock:
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = USERS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"users": list(users.values())}, indent=2))
        tmp.replace(USERS_FILE)  # atomic on POSIX — a crash never corrupts users.json


def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(
            status_code=400,
            detail="Username must be 1-32 characters: letters, digits, '.', '_' or '-' (must start with a letter or digit)",
        )
    return username


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


# ----------------------------------------------------------------------
# Per-user workspace
# ----------------------------------------------------------------------
def user_root(username: str) -> Path:
    if not USERNAME_RE.match(username or ""):
        raise HTTPException(status_code=400, detail="Invalid username")
    return USERS_ROOT / username


def ensure_user_dirs(username: str) -> Path:
    """Create the user's workspace (idempotent). Raises a clear error if the
    data volume is not writable, which is the usual cause of "blank screen
    after login" reports on hosts with restrictive bind mounts."""
    root = user_root(username)
    try:
        for d in USER_DIRS:
            (root / d).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error("Cannot create workspace for '%s' at %s: %s", username, root, e)
        raise HTTPException(
            status_code=500,
            detail=(
                f"Server data directory is not writable ({root}): {e}. "
                "Check the permissions/ownership of the mounted data volume."
            ),
        )
    return root


def user_dir(username: str, kind: str) -> Path:
    if kind not in USER_DIRS:
        raise ValueError(f"unknown user dir '{kind}'")
    return ensure_user_dirs(username) / kind


def user_usage_bytes(username: str) -> int:
    root = USERS_ROOT / username
    if not root.exists():
        return 0
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def user_settings_path(username: str) -> Path:
    return ensure_user_dirs(username) / "settings.json"


def load_user_settings(username: str) -> dict:
    p = user_settings_path(username)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def save_user_settings(username: str, settings: dict) -> None:
    p = user_settings_path(username)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2))
    tmp.replace(p)


def check_data_root_writable() -> None:
    """Fail fast at startup with an actionable message instead of 500s later."""
    try:
        USERS_ROOT.mkdir(parents=True, exist_ok=True)
        probe = USERS_ROOT / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        raise RuntimeError(
            f"Data directory {DATA_ROOT.resolve()} is not writable ({e}). "
            "When using a bind mount, make sure the host directory is owned by the "
            "UID the container runs as (root by default), or use the named volume "
            "from docker-compose.yml."
        )


def migrate_legacy_layout(users: dict[str, dict]) -> None:
    """Move data from earlier server layouts into per-user workspaces.

    * ``data/conversations/<user>/*.json`` → ``data/users/<user>/conversations/``
    * shared ``data/uploads/*`` (pre-per-user versions) → the first admin's
      uploads (they were visible to everyone before, so an admin keeps them).
    """
    for username in users:
        legacy = LEGACY_CONVERSATIONS_ROOT / username
        if legacy.is_dir():
            target = user_dir(username, "conversations")
            moved = 0
            for f in legacy.glob("*.json"):
                dest = target / f.name
                if not dest.exists():
                    shutil.move(str(f), str(dest))
                    moved += 1
            try:
                legacy.rmdir()
            except OSError:
                pass
            if moved:
                logger.info("Migrated %d conversation(s) for '%s' to the per-user layout", moved, username)

    admins = [u for u, d in users.items() if d.get("is_admin")]
    if admins and LEGACY_UPLOADS.is_dir():
        files = [f for f in LEGACY_UPLOADS.iterdir() if f.is_file() and not f.name.startswith(".")]
        if files:
            target = user_dir(admins[0], "uploads")
            for f in files:
                dest = target / f.name
                if not dest.exists():
                    shutil.move(str(f), str(dest))
            logger.info("Moved %d legacy shared upload(s) to admin '%s'", len(files), admins[0])


# ----------------------------------------------------------------------
# Tokens
# ----------------------------------------------------------------------
def _auth_cfg() -> dict:
    from backend.config import get_config

    return get_config().get("auth", {}) or {}


def _jwt_secret() -> str:
    from backend.config import get_config

    cfg = get_config()
    secret = cfg.get("auth", {}).get("jwt_secret") or cfg.get("app", {}).get("secret_key", "change-me")
    if secret in ("change-me", "change-me-in-production"):
        logger.warning("NEXUS_JWT_SECRET is not set — using an insecure default. Set it in .env for production.")
    return secret


def _token_expire_days() -> int:
    return int(_auth_cfg().get("token_expire_days", 7))


def create_access_token(username: str) -> str:
    secret = _jwt_secret()
    expire = datetime.now(timezone.utc) + timedelta(days=_token_expire_days())
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    secret = _jwt_secret()
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def _user_from_token(token: str | None) -> dict:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    payload = decode_access_token(token)
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    users = _load_users()
    user = users.get(username)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """Resolve the user from ``Authorization: Bearer`` or the session cookie."""
    token = credentials.credentials if credentials else None
    if not token:
        token = request.cookies.get(COOKIE_NAME) or request.query_params.get("token")
    user = _user_from_token(token)
    ensure_user_dirs(user["username"])
    return user


async def get_current_admin_user(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def set_session_cookie(response, token: str, request: Request | None = None) -> None:
    secure = bool(request and request.url.scheme == "https")
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=_token_expire_days() * 86400,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


# ----------------------------------------------------------------------
# User lifecycle
# ----------------------------------------------------------------------
async def init_admin_user():
    """Bootstrap the admin user from env var on first startup, verify the
    data directory and migrate legacy layouts."""
    check_data_root_writable()
    users = _load_users()
    if not users:
        admin_password = os.environ.get("NEXUS_ADMIN_PASSWORD")
        if not admin_password:
            logger.error("=" * 60)
            logger.error("NEXUS_ADMIN_PASSWORD is not set!")
            logger.error("Set it in your .env and restart the container.")
            logger.error("=" * 60)
            raise RuntimeError("NEXUS_ADMIN_PASSWORD must be set to bootstrap the admin account")
        users["admin"] = {
            "username": "admin",
            "password_hash": hash_password(admin_password),
            "is_admin": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await _save_users(users)
        logger.info("Admin user 'admin' created from NEXUS_ADMIN_PASSWORD")
    for username in users:
        if USERNAME_RE.match(username):
            ensure_user_dirs(username)
        else:
            logger.error("User '%s' has an invalid name for the per-user layout; rename it in data/users.json", username)
    migrate_legacy_layout(users)


async def create_user(username: str, password: str, is_admin: bool = False) -> dict:
    username = validate_username(username)
    if not password or len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    users = _load_users()
    if username in users:
        raise HTTPException(status_code=409, detail="Username already exists")
    users[username] = {
        "username": username,
        "password_hash": hash_password(password),
        "is_admin": is_admin,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await _save_users(users)
    ensure_user_dirs(username)
    return {"username": username, "is_admin": is_admin, "created_at": users[username]["created_at"]}


async def delete_user(username: str) -> bool:
    users = _load_users()
    if username not in users:
        raise HTTPException(status_code=404, detail="User not found")
    if users[username].get("is_admin") and sum(1 for u in users.values() if u.get("is_admin")) == 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last admin account")
    del users[username]
    await _save_users(users)
    _remove_workspace(username)
    return True


def _remove_workspace(username: str) -> None:
    if not USERNAME_RE.match(username or ""):
        return
    for root in (USERS_ROOT / username, LEGACY_CONVERSATIONS_ROOT / username):
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)


async def list_users() -> list[dict]:
    users = _load_users()
    return [
        {
            "username": u["username"],
            "is_admin": u.get("is_admin", False),
            "created_at": u.get("created_at", ""),
            "usage_bytes": user_usage_bytes(u["username"]),
            "conversations": len(list((USERS_ROOT / u["username"] / "conversations").glob("*.json")))
            if (USERS_ROOT / u["username"] / "conversations").exists() else 0,
        }
        for u in users.values()
    ]


async def change_password(username: str, current_password: str, new_password: str):
    users = _load_users()
    user = users.get(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(current_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    user["password_hash"] = hash_password(new_password)
    await _save_users(users)


async def admin_reset_password(username: str, new_password: str):
    users = _load_users()
    user = users.get(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not new_password or len(new_password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    user["password_hash"] = hash_password(new_password)
    await _save_users(users)


async def delete_user_account(username: str, password: str):
    users = _load_users()
    user = users.get(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Password is incorrect")
    if user.get("is_admin") and sum(1 for u in users.values() if u.get("is_admin")) == 1:
        raise HTTPException(status_code=400, detail="The last admin account cannot delete itself")
    del users[username]
    await _save_users(users)
    _remove_workspace(username)


def get_ws_user(token: str | None, cookie_header: str | None = None) -> dict:
    """Authenticate a WebSocket from ``?token=`` or the session cookie."""
    if not token and cookie_header:
        for part in cookie_header.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME:
                token = v
                break
    if not token:
        raise WebSocketException(code=1008, reason="Missing token")
    try:
        user = _user_from_token(token)
    except HTTPException as e:
        raise WebSocketException(code=1008, reason=str(e.detail))
    ensure_user_dirs(user["username"])
    return user
