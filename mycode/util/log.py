"""Structured logging module.

Wraps structlog to provide a consistent logging interface across the project.

Includes automatic redaction of common sensitive values (API keys, bearer
tokens, user home directory prefixes) so debug-level logs cannot leak
credentials onto disk. Redaction runs as a structlog processor and covers
both the top-level ``event`` string and any bound kwargs recursively.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from pathlib import Path


# --- Redaction ---------------------------------------------------------------

# Secret-bearing key names (case-insensitive). Values for these keys in log
# kwargs are replaced wholesale with ``***``.
_SECRET_KEY_NAMES = frozenset({
    "api_key", "apikey", "x-api-key", "authorization", "auth", "auth_token",
    "bearer", "token", "access_token", "refresh_token", "secret",
    "client_secret", "password", "passphrase", "cookie", "set-cookie",
})

# Pattern-based redaction for strings. Order matters: more specific patterns
# first. We keep patterns conservative to avoid false positives in legitimate
# log content.
_REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # OpenAI / Anthropic / Claude / generic sk- prefixed keys
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "sk-***"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b"), "sk-ant-***"),
    # AWS access key IDs
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA***"),
    # Generic long hex/base64 credential-looking blobs after "Bearer"
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"), "Bearer ***"),
    # `Authorization: <scheme> <value>` headers embedded in strings
    (re.compile(r"(?i)(authorization[\"'\s:=]+)([A-Za-z0-9._\-+/=]{12,})"), r"\1***"),
    # GitHub tokens
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "ghp_***"),
    (re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"), "gho_***"),
]

# Replace the caller's real HOME with a placeholder in log strings.
_HOME_DIR = os.path.expanduser("~")


def _scrub_string(value: str) -> str:
    for pat, repl in _REDACTION_PATTERNS:
        value = pat.sub(repl, value)
    if _HOME_DIR and _HOME_DIR != "/" and _HOME_DIR in value:
        value = value.replace(_HOME_DIR, "~")
    return value


def _scrub_value(key: str | None, value: Any) -> Any:
    """Recursively scrub one logged value.

    Whole-value replacement fires when the *key* matches a known secret
    name; otherwise we just sanitize strings using pattern matching.
    """
    if key is not None and key.lower() in _SECRET_KEY_NAMES:
        if value in (None, "", b""):
            return value
        return "***"
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, dict):
        return {k: _scrub_value(k, v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        scrubbed = [_scrub_value(None, v) for v in value]
        return type(value)(scrubbed) if isinstance(value, tuple) else scrubbed
    return value


def _redact_processor(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor that redacts sensitive values on every log call."""
    return {k: _scrub_value(k, v) for k, v in event_dict.items()}


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
        # Redaction runs LATE so it sees the final event_dict (after
        # contextvars merge & unicode decode) but BEFORE the renderer
        # serialises it to JSON / console output.
        _redact_processor,
    ]

    handlers: list[logging.Handler] = []

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        _log_file = log_dir / "mycode.log"
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
