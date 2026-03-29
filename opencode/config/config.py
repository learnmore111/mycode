"""Configuration loading and management.

Handles JSONC parsing, multi-layer config merging, and config file watching.
Equivalent to the core logic in src/config/config.ts.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import json5

from opencode.config.models import Config
from opencode.config.paths import (
    config_directories,
    global_config_file,
    project_files,
)
from opencode.util import log as logmod

logger = logmod.create(service="config")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dicts. override wins for scalar values; dicts are merged recursively.
    Lists from plugin and instructions are concatenated.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        elif key in ("plugin", "instructions") and isinstance(result.get(key), list) and isinstance(value, list):
            # Concatenate arrays for plugins and instructions
            combined = list(result[key])
            for item in value:
                if item not in combined:
                    combined.append(item)
            result[key] = combined
        else:
            result[key] = copy.deepcopy(value)
    return result


def parse_jsonc(text: str, filepath: str = "<unknown>") -> dict[str, Any]:
    """Parse JSONC (JSON with comments) text into a dict."""
    try:
        data = json5.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"Config must be a JSON object, got {type(data).__name__}")
        return data
    except Exception as e:
        raise ConfigParseError(filepath, str(e)) from e


def _load_file(filepath: str) -> dict[str, Any]:
    """Load and parse a single config file. Returns {} if file doesn't exist."""
    p = Path(filepath)
    if not p.exists():
        return {}
    try:
        text = p.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        return parse_jsonc(text, filepath)
    except ConfigParseError:
        raise
    except Exception as e:
        logger.warn("failed to load config file", path=filepath, error=str(e))
        return {}


def _validate(data: dict[str, Any], filepath: str = "<unknown>") -> Config:
    """Validate raw dict against the Config model."""
    try:
        return Config.model_validate(data)
    except Exception as e:
        raise ConfigValidationError(filepath, str(e)) from e


class ConfigParseError(Exception):
    def __init__(self, filepath: str, detail: str):
        self.filepath = filepath
        self.detail = detail
        super().__init__(f"Failed to parse config {filepath}: {detail}")


class ConfigValidationError(Exception):
    def __init__(self, filepath: str, detail: str):
        self.filepath = filepath
        self.detail = detail
        super().__init__(f"Invalid config {filepath}: {detail}")


# --- Cached config state ---

_cache: dict[str, Config] = {}  # key: directory path or "__global__"


def get(directory: str | None = None, worktree: str | None = None) -> Config:
    """Load and return the merged configuration.

    Merges configs in priority order:
    1. Global config
    2. OPENCODE_CONFIG env
    3. Project local configs
    4. .opencode directory configs
    5. OPENCODE_CONFIG_CONTENT env
    """
    cache_key = directory or "__global__"
    if cache_key in _cache:
        return _cache[cache_key]

    merged: dict[str, Any] = {}

    # 1. Global config
    global_file = str(global_config_file())
    merged = _deep_merge(merged, _load_file(global_file))

    # 2. OPENCODE_CONFIG env
    env_config = os.environ.get("OPENCODE_CONFIG")
    if env_config:
        merged = _deep_merge(merged, _load_file(env_config))

    # 3. Project local configs
    if directory:
        for filepath in project_files(directory, worktree):
            merged = _deep_merge(merged, _load_file(filepath))

    # 4. .opencode directory configs
    if directory:
        for d in config_directories(directory, worktree):
            for name in ["opencode.jsonc", "opencode.json"]:
                merged = _deep_merge(merged, _load_file(str(Path(d) / name)))

    # 5. OPENCODE_CONFIG_CONTENT env
    env_content = os.environ.get("OPENCODE_CONFIG_CONTENT")
    if env_content:
        try:
            merged = _deep_merge(merged, parse_jsonc(env_content, "OPENCODE_CONFIG_CONTENT"))
        except Exception as e:
            logger.warn("failed to parse OPENCODE_CONFIG_CONTENT", error=str(e))

    # Defaults
    merged.setdefault("agent", {})
    merged.setdefault("plugin", [])

    if not merged.get("username"):
        try:
            import getpass
            merged["username"] = getpass.getuser()
        except Exception:
            merged["username"] = "user"

    config = _validate(merged)
    _cache[cache_key] = config
    return config


def invalidate() -> None:
    """Clear the cached config so it will be reloaded on next access."""
    _cache.clear()


async def get_async(directory: str | None = None, worktree: str | None = None) -> Config:
    """Async wrapper for get(). Config loading is fast enough to be sync."""
    return get(directory, worktree)


def update_global(patch: dict[str, Any]) -> Config:
    """Update the global config file with the given patch."""
    filepath = str(global_config_file())
    existing = _load_file(filepath)
    merged = _deep_merge(existing, patch)

    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    import json
    Path(filepath).write_text(json.dumps(merged, indent=2, ensure_ascii=False))

    invalidate()
    return _validate(merged, filepath)
