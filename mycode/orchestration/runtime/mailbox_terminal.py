"""Terminal-backed mailboxes for M6.5.

These backends extend :class:`FileMailbox` — the JSONL file remains the
**authoritative** transport (the LLM-visible transcript is always
reconstructed from it) — and overlay a best-effort **visual mirror**
onto a live terminal:

- :class:`TmuxMailbox` calls ``tmux send-keys`` on a target pane so a
  human watching the tmux session sees the same conversation.
- :class:`ItermMailbox` uses ``osascript`` to feed text into an iTerm2
  session on macOS.

Design rules
============

1. **Visual side-effects are best-effort.**  If ``tmux`` or
   ``osascript`` is missing, returns non-zero, or the target pane /
   session does not exist, the mailbox **must not raise** — it logs
   a warning via :mod:`mycode.util.log` and falls back to file-only
   delivery.  That way a Linux CI box never fails a pair-review test
   just because iTerm isn't present.
2. **No polling of the terminal.**  We never try to *read* from the
   terminal (too fragile); reading always goes through the JSONL file
   inherited from :class:`FileMailbox`.
3. **Mapping target → owner is explicit.**  Each subclass receives a
   ``targets`` dict: ``{owner → pane_id_or_session_id}``.  An owner
   missing from the dict simply gets file-only delivery — this lets
   partial mirroring (e.g. only the *lead* is mirrored into a tmux
   pane while teammates stay headless).
"""

from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING

from mycode.orchestration.runtime.mailbox_file import FileMailbox
from mycode.util.log import create as create_logger

if TYPE_CHECKING:
    import os

    from mycode.orchestration.runtime.mailbox import Envelope

_log = create_logger(service="orchestration.mailbox.terminal")


def _format_visual_line(env: Envelope) -> str:
    """Render a one-line visual summary suitable for tmux / iterm.

    We keep it short so it does not blow up the operator's terminal:
    ``[seq] sender → recipient : summary_or_first_line``.
    """
    first_line = env.content.splitlines()[0] if env.content else ""
    tag = env.summary or first_line
    if len(tag) > 140:
        tag = tag[:137] + "..."
    return f"[#{env.seq}] {env.sender} → {env.recipient}: {tag}"


# ---------------------------------------------------------------------------
# Tmux
# ---------------------------------------------------------------------------


class TmuxMailbox(FileMailbox):
    """:class:`FileMailbox` + mirror every ``put()`` into a tmux pane.

    ``targets`` maps ``owner → tmux target`` (a string accepted by
    ``tmux send-keys -t <target>`` — typically ``session:window.pane``
    or a pane id like ``%7``).  If the owner of *this* mailbox is not
    in ``targets`` the mirror is a no-op; we still file-persist every
    envelope.
    """

    def __init__(
        self,
        owner: str,
        root_dir: str | os.PathLike[str],
        *,
        targets: dict[str, str] | None = None,
    ) -> None:
        super().__init__(owner, root_dir)
        self._target: str | None = (targets or {}).get(owner)
        self._tmux: str | None = shutil.which("tmux")
        if self._target and not self._tmux:
            _log.warn(
                "TmuxMailbox: tmux binary not found; falling back to file-only",
                owner=owner,
            )

    async def put(self, env: Envelope) -> None:
        # File first — this is the authoritative path.  Only mirror if
        # the file write succeeded (i.e. the envelope is persisted).
        await super().put(env)
        if self._closed or not self._target or not self._tmux:
            return
        line = _format_visual_line(env)
        try:
            # -l sends the literal string (no key interpretation), then
            # a separate call sends Enter so the pane's shell/app sees
            # it as a full line.  We *don't* assume the target is a
            # shell — a custom TUI will ignore Enter gracefully.
            proc = await asyncio.create_subprocess_exec(
                self._tmux,
                "send-keys",
                "-t",
                self._target,
                "-l",
                line + "\n",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                _log.warn(
                    "TmuxMailbox: send-keys failed",
                    owner=self.owner,
                    exit=proc.returncode,
                    stderr=stderr.decode("utf-8", "replace").strip(),
                )
        except (OSError, asyncio.CancelledError) as exc:
            # CancelledError is re-raised by the caller if the run is
            # being torn down; we only swallow OSError here.
            if isinstance(exc, asyncio.CancelledError):
                raise
            _log.warn("TmuxMailbox: mirror failed", owner=self.owner, error=str(exc))


# ---------------------------------------------------------------------------
# iTerm2
# ---------------------------------------------------------------------------


_ITERM_APPLESCRIPT = """
tell application "iTerm2"
    try
        tell session id "{session}" of window 1
            write text "{text}"
        end tell
    on error errMsg
        -- Session may have closed; ignore so we stay best-effort.
    end try
end tell
""".strip()


def _applescript_escape(s: str) -> str:
    """Escape a string for embedding into an AppleScript double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


class ItermMailbox(FileMailbox):
    """File-backed mailbox + mirror to iTerm2 via ``osascript``.

    ``targets`` maps ``owner → iTerm2 session id`` (the UUID-looking
    string returned by ``iTerm2`` Python API / AppleScript
    ``unique id`` of a session).  macOS only — on every other OS we
    log once and degrade to file-only.
    """

    def __init__(
        self,
        owner: str,
        root_dir: str | os.PathLike[str],
        *,
        targets: dict[str, str] | None = None,
    ) -> None:
        super().__init__(owner, root_dir)
        self._target: str | None = (targets or {}).get(owner)
        self._osascript: str | None = shutil.which("osascript")
        if self._target and not self._osascript:
            _log.warn(
                "ItermMailbox: osascript not found (non-macOS?); "
                "falling back to file-only",
                owner=owner,
            )

    async def put(self, env: Envelope) -> None:
        await super().put(env)
        if self._closed or not self._target or not self._osascript:
            return
        text = _applescript_escape(_format_visual_line(env))
        script = _ITERM_APPLESCRIPT.format(
            session=_applescript_escape(self._target),
            text=text,
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                self._osascript,
                "-e",
                script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                _log.warn(
                    "ItermMailbox: osascript failed",
                    owner=self.owner,
                    exit=proc.returncode,
                    stderr=stderr.decode("utf-8", "replace").strip(),
                )
        except (OSError, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            _log.warn("ItermMailbox: mirror failed", owner=self.owner, error=str(exc))


__all__ = [
    "ItermMailbox",
    "TmuxMailbox",
    "_format_visual_line",
]
