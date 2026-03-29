"""Structured logging module.

Wraps structlog to provide a consistent logging interface across the project.
Equivalent to the original src/util/log.ts.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import structlog


class _Timer:
    """Context-manager that logs elapsed time on exit."""

    def __init__(self, logger: structlog.stdlib.BoundLogger, message: str, extra: dict[str, Any]):
        self._logger = logger
        self._message = message
        self._extra = extra
        self._start = time.monotonic()

    def stop(self) -> float:
        elapsed = time.monotonic() - self._start
        self._logger.debug(self._message, elapsed_ms=round(elapsed * 1000, 2), **self._extra)
        return elapsed


class Logger:
    """Structured logger wrapping structlog."""

    def __init__(self, *, service: str):
        self._logger: structlog.stdlib.BoundLogger = structlog.get_logger(service=service)
        self._tags: dict[str, str] = {}

    def clone(self) -> Logger:
        new = Logger.__new__(Logger)
        new._logger = self._logger
        new._tags = dict(self._tags)
        return new

    def tag(self, key: str, value: str) -> Logger:
        self._tags[key] = value
        return self

    def _bind(self, extra: dict[str, Any]) -> dict[str, Any]:
        merged = {**self._tags, **extra}
        return merged

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._logger.debug(msg, **self._bind(kwargs))

    def info(self, msg: str, **kwargs: Any) -> None:
        self._logger.info(msg, **self._bind(kwargs))

    def warn(self, msg: str, **kwargs: Any) -> None:
        self._logger.warning(msg, **self._bind(kwargs))

    def error(self, msg: str | BaseException, **kwargs: Any) -> None:
        if isinstance(msg, BaseException):
            self._logger.error(str(msg), exc_info=msg, **self._bind(kwargs))
        else:
            self._logger.error(msg, **self._bind(kwargs))

    def time(self, msg: str, **kwargs: Any) -> _Timer:
        return _Timer(self._logger, msg, self._bind(kwargs))


_log_file: Path | None = None
_initialized = False


def init(*, print_logs: bool = False, dev: bool = False, level: str = "INFO", log_dir: Path | None = None) -> None:
    """Initialize the logging system. Should be called once at startup."""
    global _log_file, _initialized
    if _initialized:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    handlers: list[logging.Handler] = []

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        _log_file = log_dir / "opencode.log"
        file_handler = logging.FileHandler(str(_log_file), encoding="utf-8")
        file_handler.setLevel(log_level)
        handlers.append(file_handler)

    if print_logs or dev:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(log_level)
        handlers.append(stderr_handler)
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())

    logging.basicConfig(format="%(message)s", handlers=handlers, level=log_level)

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _initialized = True


def file() -> Path | None:
    """Return the log file path, if any."""
    return _log_file


def create(*, service: str) -> Logger:
    """Create a new logger for a given service."""
    return Logger(service=service)
