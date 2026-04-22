"""File locking utilities for concurrent JSONL operations.

Cross-platform file locking to prevent race conditions in JSONL append and rewrite operations.

Architecture:
- Unix (POSIX): Use fcntl.lockf for advisory file locking
- Windows: Use msvcrt.locking for exclusive file access
- Fallback: In-memory asyncio.Lock per file path (for platforms without OS support)

The FileLock class provides a context manager for safe, atomic file operations.
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Any

from mycode.util import log as logmod

_IS_WINDOWS = sys.platform == "win32"

if not _IS_WINDOWS:
    import fcntl

logger = logmod.create(service="session.memory.filelock")

# Module-level fallback locks keyed by resolved file path.
# Ensures two FileLock instances for the same path share the same in-memory lock
# when OS-level locking is unavailable.
_fallback_locks: dict[str, asyncio.Lock] = {}
_fallback_locks_mutex = asyncio.Lock()


async def _get_fallback_lock(path: Path) -> asyncio.Lock:
    """Get or create a per-path in-memory fallback lock."""
    resolved = str(path.resolve())
    async with _fallback_locks_mutex:
        if resolved not in _fallback_locks:
            _fallback_locks[resolved] = asyncio.Lock()
        return _fallback_locks[resolved]


class FileLock:
    """Cross-platform file locking for atomic JSONL operations.

    Usage:
        async with FileLock(path) as lock:
            with open(path, "a") as f:
                f.write(json_record + "\n")

    On Unix: Uses fcntl advisory locking (POSIX)
    On Windows: Uses msvcrt file locking
    Fallback: In-memory asyncio.Lock for unsupported platforms
    """

    def __init__(self, path: Path, timeout_seconds: float = 10.0):
        """Initialize file lock.

        Args:
            path: Path to file to lock
            timeout_seconds: Timeout for acquiring lock
        """
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds
        self._file_handle: Any = None
        self._is_locked = False
        self._fallback_lock: asyncio.Lock | None = None  # Set lazily from module-level pool

    async def acquire(self) -> None:
        """Acquire exclusive lock on the file.

        Tries OS-level locking first (fcntl on Unix, msvcrt on Windows).
        Falls back to per-path in-memory asyncio.Lock if OS locking unavailable.

        Raises:
            TimeoutError: If lock cannot be acquired within timeout_seconds
            IOError: If file operations fail
        """
        try:
            if _IS_WINDOWS:  # pragma: no cover
                await self._acquire_windows()
            else:
                await self._acquire_unix()
        except Exception as e:
            logger.warn("OS-level file locking failed, using fallback", error=str(e))
            # Fallback to per-path in-memory lock (shared across all FileLock instances for same path)
            try:
                self._fallback_lock = await _get_fallback_lock(self.path)
                await asyncio.wait_for(self._fallback_lock.acquire(), timeout=self.timeout_seconds)
                self._is_locked = True
            except TimeoutError:
                raise TimeoutError(f"Could not acquire lock on {self.path} within {self.timeout_seconds}s") from e

    async def _acquire_unix(self) -> None:
        """Acquire lock using fcntl (POSIX systems)."""
        # Open file in append mode, which creates if doesn't exist
        loop = asyncio.get_event_loop()

        # Run blocking file operations in executor
        self._file_handle = await loop.run_in_executor(
            None, self._open_file_unix
        )

        # Now acquire the lock
        await loop.run_in_executor(
            None, fcntl.lockf, self._file_handle, fcntl.LOCK_EX, 0, 0, 0
        )
        self._is_locked = True
        logger.debug("acquired fcntl lock", path=self.path)

    def _open_file_unix(self) -> Any:
        """Open file for append on Unix."""
        # Ensure parent directories exist
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return open(self.path, "a", encoding="utf-8")

    async def _acquire_windows(self) -> None:
        """Acquire lock using msvcrt (Windows)."""
        import msvcrt

        loop = asyncio.get_event_loop()
        self._file_handle = await loop.run_in_executor(
            None, self._open_file_windows
        )

        # Try to lock file
        start_time = asyncio.get_event_loop().time()
        while True:
            try:
                await loop.run_in_executor(
                    None, msvcrt.locking, self._file_handle.fileno(), msvcrt.LK_NBLCK, 1  # type: ignore[attr-defined]
                )
                break
            except OSError:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > self.timeout_seconds:
                    raise TimeoutError(
                        f"Could not acquire lock on {self.path} within {self.timeout_seconds}s"
                    ) from None
                await asyncio.sleep(0.01)  # Retry after short delay

        self._is_locked = True
        logger.debug("acquired msvcrt lock", path=self.path)

    def _open_file_windows(self) -> Any:
        """Open file for append on Windows."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return open(self.path, "a", encoding="utf-8")

    async def release(self) -> None:
        """Release lock on the file."""
        if not self._is_locked:
            return

        try:
            if self._file_handle:
                loop = asyncio.get_event_loop()

                if _IS_WINDOWS:  # pragma: no cover
                    import msvcrt
                    # Unlock the file
                    with contextlib.suppress(OSError):
                        await loop.run_in_executor(
                            None, msvcrt.locking, self._file_handle.fileno(), msvcrt.LK_UNLCK, 1  # type: ignore[attr-defined]
                        )
                else:
                    # fcntl unlock
                    with contextlib.suppress(OSError):
                        await loop.run_in_executor(
                            None, fcntl.lockf, self._file_handle, fcntl.LOCK_UN, 0, 0, 0
                        )

                # Close file handle
                await loop.run_in_executor(None, self._file_handle.close)
                self._file_handle = None

            self._is_locked = False
            logger.debug("released file lock", path=self.path)
        except Exception as e:
            logger.warn("error releasing file lock", error=str(e))
            self._is_locked = False
        finally:
            # Always release fallback lock if held
            if self._fallback_lock is not None and self._fallback_lock.locked():
                self._fallback_lock.release()

    async def __aenter__(self) -> FileLock:
        """Async context manager entry."""
        await self.acquire()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.release()

    def __enter__(self) -> FileLock:
        """Sync context manager entry (for compatibility)."""
        # This is a synchronous context manager for use with concurrent.futures
        # The actual locking happens in acquire() which is async
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Sync context manager exit (stub for compatibility)."""
        pass


class FileLockManager:
    """Manager for file locks across multiple files.

    Maintains a cache of FileLock instances per file path to ensure
    consistent locking behavior across concurrent operations.
    """

    def __init__(self) -> None:
        """Initialize lock manager."""
        self._locks: dict[str, FileLock] = {}
        self._manager_lock = asyncio.Lock()

    async def acquire_lock(self, path: Path, timeout_seconds: float = 10.0) -> FileLock:
        """Get or create a FileLock for the given path.

        Args:
            path: Path to file to lock
            timeout_seconds: Timeout for acquiring lock

        Returns:
            FileLock instance (acquired and ready to use)
        """
        path_str = str(path.resolve())

        async with self._manager_lock:
            if path_str not in self._locks:
                self._locks[path_str] = FileLock(path, timeout_seconds)
            lock = self._locks[path_str]

        await lock.acquire()
        return lock

    def clear(self) -> None:
        """Clear all cached locks."""
        self._locks.clear()

    @property
    def lock_count(self) -> int:
        """Get number of cached locks."""
        return len(self._locks)


# Global lock manager instance
_lock_manager = FileLockManager()


async def get_file_lock(path: Path, timeout_seconds: float = 10.0) -> FileLock:
    """Convenience function to get a file lock.

    Args:
        path: Path to file to lock
        timeout_seconds: Timeout for acquiring lock

    Returns:
        FileLock instance (already acquired)
    """
    return await _lock_manager.acquire_lock(path, timeout_seconds)
