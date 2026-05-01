"""
Configuration loader for Nexus Chat.
Reads settings.yaml and substitutes environment variables.
"""

import os
import re
import yaml
from pathlib import Path
from typing import Any

_config: dict | None = None

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
SERVER_CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.server.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Lists are replaced."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _substitute_env_vars(value: Any) -> Any:
    """Recursively substitute ${ENV_VAR} and ${ENV_VAR:-default} patterns."""
    if isinstance(value, str):
        pattern = r'\$\{([^}]+)\}'
        def replacer(match):
            expr = match.group(1)
            if ":-" in expr:
                var_name, default = expr.split(":-", 1)
                return os.environ.get(var_name, default)
            return os.environ.get(expr, match.group(0))
        return re.sub(pattern, replacer, value)
    elif isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_substitute_env_vars(item) for item in value]
    return value


def load_config(config_path: Path | None = None) -> dict:
    """Load and cache configuration from YAML file(s)."""
    global _config
    if _config is not None:
        return _config

    path = config_path or CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    # Merge server override if present
    if SERVER_CONFIG_PATH.exists():
        with open(SERVER_CONFIG_PATH) as f:
            override = yaml.safe_load(f)
        if override:
            raw = _deep_merge(raw or {}, override)

    _config = _substitute_env_vars(raw)
    return _config


def get_config() -> dict:
    """Get the cached configuration (loads if needed)."""
    if _config is None:
        return load_config()
    return _config


def get_enabled_providers() -> dict:
    """Return only enabled LLM provider configs."""
    cfg = get_config()
    providers = cfg.get("providers", {})
    return {
        name: prov for name, prov in providers.items()
        if prov.get("enabled", False)
    }


def get_enabled_tools() -> dict:
    """Return only enabled tool configs."""
    cfg = get_config()
    tools = cfg.get("tools", {})
    return {
        name: tool for name, tool in tools.items()
        if tool.get("enabled", False)
    }


def get_enabled_mcp_servers() -> dict:
    """Return only enabled MCP server configs."""
    cfg = get_config()
    servers = cfg.get("mcp_servers", {})
    return {
        name: srv for name, srv in servers.items()
        if srv.get("enabled", False)
    }
