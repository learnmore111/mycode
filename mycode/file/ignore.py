"""Unified ignore patterns for file search and listing.

All search/glob/list tools should use these patterns to avoid
returning results from virtual environments, caches, build artifacts, etc.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import PurePosixPath

# Directories that should always be excluded from search results.
# These are common build artifacts, caches, and virtual environments.
IGNORED_DIRS: frozenset[str] = frozenset({
    ".venv",
    "venv",
    ".env",
    "env",
    "__pycache__",
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".tox",
    ".nox",
    ".eggs",
    "dist",
    "build",
    ".DS_Store",
    ".idea",
    ".vscode",
    "*.egg-info",
    ".next",
    ".nuxt",
    ".cache",
    ".parcel-cache",
    ".turbo",
    "coverage",
    ".nyc_output",
    ".gradle",
    "target",
    "__pypackages__",
})

# File patterns that should be excluded (binary/cache artifacts)
IGNORED_FILE_PATTERNS: frozenset[str] = frozenset({
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.class",
    "*.o",
    "*.a",
    "*.wasm",
})

# Glob patterns for ripgrep --glob exclusion (prefixed with !)
RG_EXCLUDE_GLOBS: tuple[str, ...] = tuple(
    f"!{d}" for d in sorted(IGNORED_DIRS)
)


def should_ignore_path(path: str) -> bool:
    """Check if a file path should be ignored.

    Tests each path segment against IGNORED_DIRS (using fnmatch for
    wildcard entries like ``*.egg-info``) and tests the filename against
    IGNORED_FILE_PATTERNS.
    """
    parts = PurePosixPath(path.replace(os.sep, "/")).parts
    for part in parts:
        for pattern in IGNORED_DIRS:
            if fnmatch.fnmatch(part, pattern):
                return True
    # Check file patterns on the last segment (filename)
    if parts:
        filename = parts[-1]
        for pattern in IGNORED_FILE_PATTERNS:
            if fnmatch.fnmatch(filename, pattern):
                return True
    return False


def should_ignore_entry(name: str) -> bool:
    """Check if a directory entry name should be ignored (for list_dir)."""
    return any(fnmatch.fnmatch(name, pattern) for pattern in IGNORED_DIRS)
