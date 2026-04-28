"""File-backed mailbox for M6.5 — JSONL append-only, cross-process safe.

Design
======

Each swarm run owns a directory ``root_dir`` (by default a fresh
``tempfile.mkdtemp()`` created by :meth:`MailboxSystem.for_backend`).
Inside that directory every owner gets one append-only JSONL file
``{owner}.jsonl``; the files are written with an **exclusive file lock
per append** (POSIX ``fcntl.flock`` or Windows ``msvcrt.locking``)
so two processes sending to the same recipient never interleave a
half-serialised envelope.  A separate ``_seq`` file tracks the shared
``MailboxSystem`` counter so a cross-process swarm produces a strict
total order on :attr:`Envelope.seq`.

Reads are driven by ``drain()`` which remembers a byte offset into its
own inbox file and only returns envelopes appended since the previous
call.  Because appends are atomic per-line we can detect a torn write
(JSON parse failure) and wait for the next call — the next append will
finish the line.

The authoritative transcript for LLMs and tests is therefore the
JSONL files themselves; :class:`MailboxSystem.event_log` remains a
per-process mirror and is kept in sync by the calling process whenever
it sends / receives.

This backend has **no** dependency on tmux or iTerm; it is the base
for both of them (they subclass :class:`FileMailbox` and add a
best-effort visual side-effect).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mycode.orchestration.runtime.mailbox import Envelope

_IS_WINDOWS = sys.platform == "win32"

if not _IS_WINDOWS:
    import fcntl


# ---------------------------------------------------------------------------
# Low-level lock helpers
# ---------------------------------------------------------------------------


def _lock_exclusive(fd: int) -> None:
    """Acquire an exclusive advisory lock on ``fd``.  Blocks until held."""
    if sys.platform == "win32":  # pragma: no cover
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":  # pragma: no cover
        import msvcrt

        with contextlib.suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Envelope <-> JSONL
# ---------------------------------------------------------------------------


def _envelope_to_json(env: Envelope) -> str:
    """Serialise an envelope to a single JSON line (no trailing newline).

    We use :func:`dataclasses.asdict` rather than pydantic to avoid adding
    a schema dependency — the envelope is a plain dataclass and its
    fields are all JSON-native.
    """
    return json.dumps(asdict(env), ensure_ascii=False, separators=(",", ":"))


def _json_to_envelope(line: str) -> Envelope:
    from mycode.orchestration.runtime.mailbox import Envelope  # local to avoid cycle

    payload = json.loads(line)
    return Envelope(**payload)


# ---------------------------------------------------------------------------
# FileMailbox
# ---------------------------------------------------------------------------


class FileMailbox:
    """One JSONL file per owner; safe for concurrent multi-process writers.

    Parameters
    ----------
    owner:
        The agent name that owns this inbox.
    root_dir:
        Directory holding every inbox file for this swarm run.  The
        directory is created by :class:`MailboxSystem.for_backend`; the
        mailbox does **not** clean it up — ``close()`` only releases
        open file handles so callers (or tests) can inspect the files
        after a run.
    """

    def __init__(self, owner: str, root_dir: str | os.PathLike[str]) -> None:
        self.owner = owner
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.root_dir / f"{owner}.jsonl"
        # Touch the file so ``drain()`` never has to deal with missing.
        self.path.touch(exist_ok=True)
        # Offset we've consumed up to on our own inbox — readers only
        # pick up lines appended after this.
        self._read_offset: int = 0
        self._closed: bool = False

    # -- writer ------------------------------------------------------------

    async def put(self, env: Envelope) -> None:
        """Append one envelope line to the recipient's JSONL.  The actual
        file IO runs in the default executor to keep the event loop
        unblocked during (rare) fsync stalls."""
        if self._closed:
            return
        line = _envelope_to_json(env) + "\n"
        await asyncio.get_running_loop().run_in_executor(
            None, self._append_locked, line
        )

    def _append_locked(self, line: str) -> None:
        # Open in append-binary with line buffering disabled — each
        # append is one write() syscall and the flock keeps it atomic
        # vs other processes.
        with open(self.path, "ab") as fh:
            _lock_exclusive(fh.fileno())
            try:
                fh.write(line.encode("utf-8"))
                fh.flush()
                # Some filesystems (tmpfs on CI containers) reject
                # fsync; the flush above is enough for durability
                # within a single host.
                with contextlib.suppress(OSError):
                    os.fsync(fh.fileno())
            finally:
                _unlock(fh.fileno())

    # -- reader ------------------------------------------------------------

    async def drain(self) -> list[Envelope]:
        """Read every complete JSONL line appended since the last call."""
        if self._closed:
            return []
        return await asyncio.get_running_loop().run_in_executor(
            None, self._read_since_offset
        )

    def _read_since_offset(self) -> list[Envelope]:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return []
        if size <= self._read_offset:
            return []
        out: list[Envelope] = []
        with open(self.path, "rb") as fh:
            fh.seek(self._read_offset)
            raw = fh.read()
            new_offset = self._read_offset + len(raw)
        # Only promote offset past the last *complete* line, so a torn
        # append mid-newline is retried next drain().
        text = raw.decode("utf-8", errors="replace")
        lines = text.split("\n")
        if text.endswith("\n"):
            complete = lines[:-1]
            self._read_offset = new_offset
        else:
            complete = lines[:-1]
            # Back off the unfinished tail.
            self._read_offset = new_offset - len(lines[-1].encode("utf-8"))
        for ln in complete:
            if not ln.strip():
                continue
            try:
                out.append(_json_to_envelope(ln))
            except (json.JSONDecodeError, TypeError):
                # Defensive: ignore a line we can't parse (e.g. torn
                # write from a sibling that crashed mid-append).  A
                # later call will either succeed or skip again.
                continue
        return out

    async def close(self) -> None:
        self._closed = True


# ---------------------------------------------------------------------------
# Cross-process sequence counter
# ---------------------------------------------------------------------------


class FileSeqCounter:
    """Lock-protected integer stored in a single file.

    Mirrors :class:`itertools.count` semantics but coordinates across
    processes so every send gets a globally-unique ``seq`` even when
    two peers run in different Python interpreters.
    """

    def __init__(self, root_dir: str | os.PathLike[str]) -> None:
        self.path = Path(root_dir) / "_seq"
        if not self.path.exists():
            self.path.write_text("0", encoding="utf-8")

    def next(self) -> int:
        with open(self.path, "r+b") as fh:
            _lock_exclusive(fh.fileno())
            try:
                raw = fh.read().decode("utf-8").strip() or "0"
                try:
                    cur = int(raw)
                except ValueError:
                    cur = 0
                nxt = cur + 1
                fh.seek(0)
                fh.truncate()
                fh.write(str(nxt).encode("utf-8"))
                fh.flush()
                with contextlib.suppress(OSError):
                    os.fsync(fh.fileno())
                return nxt
            finally:
                _unlock(fh.fileno())


__all__ = [
    "FileMailbox",
    "FileSeqCounter",
    "_envelope_to_json",
    "_json_to_envelope",
]


# Keep a runtime-visible timestamp helper for subclasses that want to
# stamp their visual side-effects consistently with the envelope.
def _now_ts() -> float:
    return time.time()
