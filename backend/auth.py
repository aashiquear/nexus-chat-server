"""
Authentication and user management for Nexus Chat Server.
Stores users in data/users.json (lightweight, no extra DB dependency).
Uses bcrypt for passwords and JWT for session tokens.
"""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, WebSocketException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

USERS_FILE = Path("./data/users.json")
CONVERSATIONS_ROOT = Path("./data/conversations")

# Lock to prevent concurrent writes to users.json
_users_lock = asyncio.Lock()

security = HTTPBearer(auto_error=False)


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
        USERS_FILE.write_text(json.dumps({"users": list(users.values())}, indent=2))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def _jwt_secret() -> str:
    from backend.config import get_config
    cfg = get_config()
    return cfg.get("auth", {}).get("jwt_secret", cfg.get("app", {}).get("secret_key", "change-me"))


def _token_expire_days() -> int:
    from backend.config import get_config
    cfg = get_config()
    return cfg.get("auth", {}).get("token_expire_days", 7)


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


async def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    payload = decode_access_token(credentials.credentials)
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    users = _load_users()
    user = users.get(username)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_current_admin_user(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


async def init_admin_user():
    """Bootstrap the admin user from env var on first startup."""
    users = _load_users()
    if users:
        return

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


async def create_user(username: str, password: str, is_admin: bool = False) -> dict:
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")
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
    # Create private conversation directory
    user_dir = CONVERSATIONS_ROOT / username
    user_dir.mkdir(parents=True, exist_ok=True)
    return {"username": username, "is_admin": is_admin, "created_at": users[username]["created_at"]}


async def delete_user(username: str) -> bool:
    users = _load_users()
    if username not in users:
        raise HTTPException(status_code=404, detail="User not found")
    del users[username]
    await _save_users(users)
    # Clean up conversation directory
    user_dir = CONVERSATIONS_ROOT / username
    if user_dir.exists():
        shutil.rmtree(user_dir)
    return True


async def list_users() -> list[dict]:
    users = _load_users()
    return [
        {
            "username": u["username"],
            "is_admin": u.get("is_admin", False),
            "created_at": u.get("created_at", ""),
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


async def delete_user_account(username: str, password: str):
    users = _load_users()
    user = users.get(username)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Password is incorrect")
    del users[username]
    await _save_users(users)
    # Clean up conversation directory
    user_dir = CONVERSATIONS_ROOT / username
    if user_dir.exists():
        shutil.rmtree(user_dir)


def get_ws_user(token: str | None) -> dict:
    if not token:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
    try:
        payload = decode_access_token(token)
    except HTTPException:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
    username = payload.get("sub")
    if not username:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
    users = _load_users()
    user = users.get(username)
    if not user:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="User not found")
    return user
