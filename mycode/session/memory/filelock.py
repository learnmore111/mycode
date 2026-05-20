"""并发 JSONL 操作的文件锁定工具。

跨平台文件锁定，防止 JSONL 追加和重写操作中的竞态条件。

架构：
- Unix (POSIX)：使用 fcntl.lockf 进行建议性文件锁定
- Windows：使用 msvcrt.locking 进行独占文件访问
- 回退：每个文件路径的内存 asyncio.Lock（用于不支持 OS 锁定的平台）

FileLock 类提供上下文管理器，用于安全、原子的文件操作。
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

# 按解析后的文件路径键控的模块级回退锁。
# 确保同一路径的两个 FileLock 实例在 OS 级锁定不可用时共享相同的内存锁。
_fallback_locks: dict[str, asyncio.Lock] = {}
_fallback_locks_mutex = asyncio.Lock()


async def _get_fallback_lock(path: Path) -> asyncio.Lock:
    """获取或创建每个路径的内存回退锁。"""
    resolved = str(path.resolve())
    async with _fallback_locks_mutex:
        if resolved not in _fallback_locks:
            _fallback_locks[resolved] = asyncio.Lock()
        return _fallback_locks[resolved]


class FileLock:
    """用于原子 JSONL 操作的跨平台文件锁定。

    用法：
        async with FileLock(path) as lock:
            with open(path, "a") as f:
                f.write(json_record + "\n")

    在 Unix 上：使用 fcntl 建议性锁定（POSIX）
    在 Windows 上：使用 msvcrt 文件锁定
    回退：为不支持的平台使用内存 asyncio.Lock
    """

    def __init__(self, path: Path, timeout_seconds: float = 10.0):
        """初始化文件锁。

        参数：
            path: 要锁定的文件路径
            timeout_seconds: 获取锁定的超时时间
        """
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds
        self._file_handle: Any = None
        self._is_locked = False
        self._fallback_lock: asyncio.Lock | None = None  # Set lazily from module-level pool

    async def acquire(self) -> None:
        """获取文件的独占锁。

        首先尝试 OS 级锁定（Unix 上使用 fcntl，Windows 上使用 msvcrt）。
        如果 OS 锁定不可用，则回退到每个路径的内存 asyncio.Lock。

        抛出：
            TimeoutError：如果无法在 timeout_seconds 内获取锁定
            IOError：如果文件操作失败
        """
        try:
            if _IS_WINDOWS:  # pragma: no cover
                await self._acquire_windows()
            else:
                await self._acquire_unix()
        except Exception as e:
            logger.warn("OS-level file locking failed, using fallback", error=str(e))
            # 回退到每个路径的内存锁（同一路径的所有 FileLock 实例共享）
            try:
                self._fallback_lock = await _get_fallback_lock(self.path)
                await asyncio.wait_for(self._fallback_lock.acquire(), timeout=self.timeout_seconds)
                self._is_locked = True
            except TimeoutError:
                raise TimeoutError(f"Could not acquire lock on {self.path} within {self.timeout_seconds}s") from e

    async def _acquire_unix(self) -> None:
        """使用 fcntl 获取锁（POSIX 系统）。"""
        # 以追加模式打开文件，如果不存在则创建
        loop = asyncio.get_event_loop()

        # 在执行器中运行阻塞文件操作
        self._file_handle = await loop.run_in_executor(
            None, self._open_file_unix
        )

        # 现在获取锁
        await loop.run_in_executor(
            None, fcntl.lockf, self._file_handle, fcntl.LOCK_EX, 0, 0, 0
        )
        self._is_locked = True
        logger.debug("acquired fcntl lock", path=self.path)

    def _open_file_unix(self) -> Any:
        """在 Unix 上以追加模式打开文件。"""
        # 确保父目录存在
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return open(self.path, "a", encoding="utf-8")

    async def _acquire_windows(self) -> None:
        """使用 msvcrt 获取锁（Windows）。"""
        import msvcrt

        loop = asyncio.get_event_loop()
        self._file_handle = await loop.run_in_executor(
            None, self._open_file_windows
        )

        # 尝试锁定文件
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
                await asyncio.sleep(0.01)  # 短暂延迟后重试

        self._is_locked = True
        logger.debug("acquired msvcrt lock", path=self.path)

    def _open_file_windows(self) -> Any:
        """Open file for append on Windows."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return open(self.path, "a", encoding="utf-8")

    async def release(self) -> None:
        """释放文件上的锁。"""
        if not self._is_locked:
            return

        try:
            if self._file_handle:
                loop = asyncio.get_event_loop()

                if _IS_WINDOWS:  # pragma: no cover
                    import msvcrt
                    # 解锁文件
                    with contextlib.suppress(OSError):
                        await loop.run_in_executor(
                            None, msvcrt.locking, self._file_handle.fileno(), msvcrt.LK_UNLCK, 1  # type: ignore[attr-defined]
                        )
                else:
                    # fcntl 解锁
                    with contextlib.suppress(OSError):
                        await loop.run_in_executor(
                            None, fcntl.lockf, self._file_handle, fcntl.LOCK_UN, 0, 0, 0
                        )

                # 关闭文件句柄
                await loop.run_in_executor(None, self._file_handle.close)
                self._file_handle = None

            self._is_locked = False
            logger.debug("released file lock", path=self.path)
        except Exception as e:
            logger.warn("error releasing file lock", error=str(e))
            self._is_locked = False
        finally:
            # 如果持有回退锁，始终释放它
            if self._fallback_lock is not None and self._fallback_lock.locked():
                self._fallback_lock.release()

    async def __aenter__(self) -> FileLock:
        """异步上下文管理器入口。"""
        await self.acquire()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """异步上下文管理器退出。"""
        await self.release()

    def __enter__(self) -> FileLock:
        """同步上下文管理器入口（用于兼容）。"""
        # 这是用于 concurrent.futures 的同步上下文管理器
        # 实际锁定发生在异步的 acquire() 中
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """同步上下文管理器退出（兼容存根）。"""
        pass


class FileLockManager:
    """跨多个文件的文件锁管理器。

    为每个文件路径维护 FileLock 实例缓存，以确保跨并发操作的一致锁定行为。
    """

    def __init__(self) -> None:
        """初始化锁管理器。"""
        self._locks: dict[str, FileLock] = {}
        self._manager_lock = asyncio.Lock()

    async def acquire_lock(self, path: Path, timeout_seconds: float = 10.0) -> FileLock:
        """获取或创建给定路径的 FileLock。

        参数：
            path: 要锁定的文件路径
            timeout_seconds: 获取锁定的超时时间

        返回：
            FileLock 实例（已获取并准备使用）
        """
        path_str = str(path.resolve())

        async with self._manager_lock:
            if path_str not in self._locks:
                self._locks[path_str] = FileLock(path, timeout_seconds)
            lock = self._locks[path_str]

        await lock.acquire()
        return lock

    def clear(self) -> None:
        """清除所有缓存的锁。"""
        self._locks.clear()

    @property
    def lock_count(self) -> int:
        """获取缓存锁的数量。"""
        return len(self._locks)


# 全局锁管理器实例
_lock_manager = FileLockManager()


async def get_file_lock(path: Path, timeout_seconds: float = 10.0) -> FileLock:
    """获取文件锁的便捷函数。

    参数：
        path: 要锁定的文件路径
        timeout_seconds: 获取锁定的超时时间

    返回：
        FileLock 实例（已获取）
    """
    return await _lock_manager.acquire_lock(path, timeout_seconds)
