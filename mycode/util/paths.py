"""Global paths for data, config, state, and cache directories.

Follows XDG Base Directory specification on Linux,
uses platform-specific directories on macOS and Windows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "mycode"


def _xdg(env: str, fallback: str) -> Path:
    val = os.environ.get(env)
    if val:
        return Path(val) / APP_NAME
    return Path.home() / fallback / APP_NAME


class GlobalPaths:
    """Singleton holding all global directory paths."""

    @staticmethod
    def home() -> Path:
        return Path.home()

    @staticmethod
    def data() -> Path:
        """~/.local/share/mycode (Linux) or ~/Library/Application Support/mycode (macOS)."""
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / APP_NAME
        if sys.platform == "win32":
            return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
        return _xdg("XDG_DATA_HOME", ".local/share")

    @staticmethod
    def config() -> Path:
        """~/.config/mycode (Linux) or ~/Library/Application Support/mycode (macOS)."""
        if sys.platform == "darwin":
            return Path.home() / ".config" / APP_NAME
        if sys.platform == "win32":
            return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
        return _xdg("XDG_CONFIG_HOME", ".config")

    @staticmethod
    def state() -> Path:
        """~/.local/state/mycode."""
        if sys.platform == "darwin":
            return Path.home() / ".local" / "state" / APP_NAME
        if sys.platform == "win32":
            return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
        return _xdg("XDG_STATE_HOME", ".local/state")

    @staticmethod
    def cache() -> Path:
        """~/.cache/mycode."""
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Caches" / APP_NAME
        if sys.platform == "win32":
            return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME / "cache"
        return _xdg("XDG_CACHE_HOME", ".cache")

    @classmethod
    def ensure_all(cls) -> None:
        """Create all global directories if they don't exist."""
        for d in [cls.data(), cls.config(), cls.state(), cls.cache()]:
            d.mkdir(parents=True, exist_ok=True)
