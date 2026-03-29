"""Configuration file path discovery.

Discovers config files in the following priority order (low to high):
1. Global config (~/.config/opencode/)
2. OPENCODE_CONFIG env var
3. Project local config (opencode.json / .opencode/)
4. OPENCODE_CONFIG_CONTENT env var

Equivalent to src/config/paths.ts.
"""

from __future__ import annotations

import os
from pathlib import Path

from opencode.util.paths import GlobalPaths

CONFIG_FILENAMES = ["opencode.jsonc", "opencode.json"]
LEGACY_FILENAMES = ["config.json"]
DOTDIR = ".opencode"


def global_config_file() -> Path:
    """Return the global config file path (first existing, or default)."""
    config_dir = GlobalPaths.config()
    candidates = [config_dir / name for name in CONFIG_FILENAMES + LEGACY_FILENAMES]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def project_files(directory: str, worktree: str | None = None) -> list[str]:
    """Find all project-level config files, in priority order.

    Searches for opencode.json / opencode.jsonc in the project directory
    and its .opencode subdirectory.
    """
    result: list[str] = []
    dirs = [directory]
    if worktree and worktree != directory:
        dirs.insert(0, worktree)

    for d in dirs:
        for name in CONFIG_FILENAMES:
            p = Path(d) / name
            if p.exists():
                result.append(str(p))

    return result


def config_directories(directory: str, worktree: str | None = None) -> list[str]:
    """Return all .opencode directories to search for commands/agents/plugins.

    Priority order (low to high):
    1. Global config dir
    2. OPENCODE_CONFIG_DIR env
    3. Worktree .opencode
    4. Directory .opencode
    """
    result: list[str] = []

    # Global
    global_dir = str(GlobalPaths.config())
    if Path(global_dir).exists():
        result.append(global_dir)

    # OPENCODE_CONFIG_DIR
    env_dir = os.environ.get("OPENCODE_CONFIG_DIR")
    if env_dir and Path(env_dir).exists():
        result.append(env_dir)

    # Worktree .opencode
    if worktree:
        wt_dot = str(Path(worktree) / DOTDIR)
        if Path(wt_dot).exists():
            result.append(wt_dot)

    # Directory .opencode
    dir_dot = str(Path(directory) / DOTDIR)
    if Path(dir_dot).exists() and dir_dot not in result:
        result.append(dir_dot)

    return result
