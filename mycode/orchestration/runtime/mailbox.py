"""Swarm mailbox: peer-to-peer message routing for M6.

A swarm is a set of *peer* agents (a lead plus teammates) that coordinate
not through a DAG but through **messages**.  Each agent owns an inbox;
sending a message to another agent places an envelope in that agent's
inbox.  The swarm runtime (``swarm.py``) polls each inbox between LLM
turns and feeds received messages back into the conversation.

Design
======

- **`Envelope`** is a plain dataclass.  It carries ``kind`` (message /
  broadcast / shutdown_request / shutdown_response), sender / recipient
  names, textual ``content``, an optional ``summary`` (5–10 word gist
  used by UIs), an epoch-seconds ``timestamp`` (for ordering /
  transcripts), and a monotonically-increasing ``seq`` that lets the
  runtime deduplicate and reason about causality even if multiple
  backends are stitched together later.

- **`Mailbox`** is a ``Protocol`` so we can plug non-inprocess backends
  later (file, tmux, iterm) without touching the runtime.  The M6
  shipping implementation is :class:`InprocessMailbox`, a thin wrapper
  around ``asyncio.Queue`` with a non-blocking ``drain()`` used by the
  runtime on every turn.

- **`MailboxSystem`** owns one mailbox per agent plus a shared event
  log.  It exposes ``send()`` (route to recipient's inbox + record
  event) and ``broadcast()`` (fan-out to every other peer).  The
  event log is the swarm's authoritative transcript — tests assert on
  it to verify routing and ordering without needing any LLM.

This module deliberately contains **no** LLM, tool, or async-loop code;
all actual conversation lives in ``swarm.py``.  Keeping the two layered
means the mailbox can be unit-tested on its own and future backends
(file, tmux) can swap in with a single ``register_mailbox`` call.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    import os
    from collections.abc import Awaitable, Callable

# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------

#: Message kinds recognised by the runtime.  ``message`` is point-to-point,
#: ``broadcast`` fans out to every peer but the sender, and the two
#: ``shutdown_*`` kinds are how the lead negotiates graceful teardown.
EnvelopeKind = Literal[
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
]

#: Backend preference accepted by :meth:`MailboxSystem.for_backend`.  Kept
#: here (not in the topology schema module) so runtime code can import a
#: single source of truth without pulling pydantic into the mailbox layer.
BackendKind = Literal["auto", "inprocess", "file", "tmux", "iterm"]


def _unique_owners(owners: list[str]) -> list[str]:
    """Return ``owners`` with order preserved, raising on duplicates.

    Mailbox routing is keyed on the owner name, so an accidental duplicate
    would silently shadow a peer.  We fail loudly instead.
    """
    seen: set[str] = set()
    unique: list[str] = []
    for o in owners:
        if o in seen:
            raise ValueError(f"duplicate owner: {o!r}")
        seen.add(o)
        unique.append(o)
    return unique


@dataclass
class Envelope:
    """One routed message between swarm peers.

    ``seq`` is assigned by :class:`MailboxSystem` at send time and is
    globally monotonic across *all* mailboxes in the system — it gives
    the transcript a total order even though individual inboxes are
    drained independently.  Tests rely on this.
    """

    kind: EnvelopeKind
    sender: str
    recipient: str  # "*" for broadcast
    content: str = ""
    summary: str = ""
    seq: int = 0
    timestamp: float = 0.0

    def is_shutdown(self) -> bool:
        return self.kind in ("shutdown_request", "shutdown_response")

    def format_for_llm(self) -> str:
        """Render the envelope as a markdown-flavoured block suitable
        for injection into a user-role message of the recipient."""
        prefix = {
            "message": f"Message from `{self.sender}`",
            "broadcast": f"Broadcast from `{self.sender}`",
            "shutdown_request": f"Shutdown requested by `{self.sender}`",
            "shutdown_response": f"Shutdown response from `{self.sender}`",
        }[self.kind]
        head = f"### {prefix}"
        if self.summary:
            head += f" — *{self.summary}*"
        body = self.content.strip() or "_(no body)_"
        return f"{head}\n\n{body}"


# ---------------------------------------------------------------------------
# Mailbox protocol
# ---------------------------------------------------------------------------


class Mailbox(Protocol):
    """Per-agent inbox contract.

    Implementations must be **async-safe** — the runtime spawns one
    task per agent that reads from its own mailbox concurrently with
    senders writing to it.
    """

    owner: str

    async def put(self, env: Envelope) -> None: ...

    async def drain(self) -> list[Envelope]:
        """Return every envelope queued since the last call, in FIFO
        order.  Must be non-blocking (returns ``[]`` immediately if
        empty)."""
        ...

    async def wait_for_message(self, timeout: float | None = None) -> bool:
        """Wait until at least one envelope is available.

        Returns ``True`` when a subsequent ``drain()`` should see work,
        or ``False`` when the optional timeout elapsed. Implementations
        must not consume the envelope while waiting.
        """
        ...

    async def close(self) -> None:
        """Release any resources (e.g. file handles).  Idempotent."""
        ...


class InprocessMailbox:
    """The M6 default backend: a plain ``asyncio.Queue``.

    No persistence, no cross-process visibility — exactly the semantics
    the coordinator-less swarm needs when all peers run inside a single
    Python event loop (the overwhelmingly common case for now).
    """

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self._queue: asyncio.Queue[Envelope] = asyncio.Queue()
        self._condition = asyncio.Condition()
        self._closed: bool = False

    async def put(self, env: Envelope) -> None:
        if self._closed:
            # Dropping on a closed mailbox matches the semantics the
            # runtime wants during teardown: the agent is gone, so a
            # laggy send should not raise.
            return
        async with self._condition:
            await self._queue.put(env)
            self._condition.notify_all()

    async def wait_for_message(self, timeout: float | None = None) -> bool:
        if not self._queue.empty():
            return True
        if self._closed:
            return False

        async def _wait_until_ready() -> bool:
            async with self._condition:
                while self._queue.empty() and not self._closed:
                    await self._condition.wait()
                return not self._queue.empty()

        try:
            return await asyncio.wait_for(_wait_until_ready(), timeout=timeout)
        except TimeoutError:
            return False

    async def drain(self) -> list[Envelope]:
        """Grab whatever is queued right now and return it.  Drains in
        a tight loop to avoid missing messages that arrive mid-drain —
        senders are awaited so any pending ``put`` must finish before
        the returning task gets the event loop back."""
        out: list[Envelope] = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                return out

    async def close(self) -> None:
        self._closed = True
        async with self._condition:
            self._condition.notify_all()


# ---------------------------------------------------------------------------
# MailboxSystem — routing + event log
# ---------------------------------------------------------------------------


@dataclass
class MailboxSystem:
    """All mailboxes for one swarm run, plus a shared sequential log.

    ``event_log`` is an append-only list of every envelope the system
    has ever routed, *including* broadcasts (stored once with
    ``recipient="*"`` plus one per-recipient copy so the transcript
    reflects actual delivery).  This is what the CLI / tests consume
    to reconstruct a conversation.

    ``on_send`` is an optional async callback invoked once per routed
    envelope (including the per-recipient copies fan-out emits).  It
    exists purely so the orchestration event emitter can bridge to
    :class:`mycode.bus.bus.Bus` without the mailbox importing the bus.
    When ``None`` it's a zero-cost no-op.
    """

    owners: list[str] = field(default_factory=list)
    inboxes: dict[str, Mailbox] = field(default_factory=dict)
    event_log: list[Envelope] = field(default_factory=list)
    _counter: itertools.count[int] = field(default_factory=lambda: itertools.count(1))
    # Optional cross-process seq counter.  When set, ``next_seq`` reads
    # from this instead of the per-process ``_counter`` so file- /
    # terminal-backed swarms spanning multiple processes still produce
    # a globally unique ordering on :attr:`Envelope.seq`.
    _seq_fn: Callable[[], int] | None = None
    # Async callback invoked after every envelope the system routes.
    # Used by the orchestration event emitter to publish
    # ``orchestration.message.sent`` onto the global bus without making
    # the mailbox depend on the bus module directly.  ``None`` (default)
    # is a zero-cost no-op.
    on_send: Callable[[Envelope], Awaitable[None]] | None = None

    @classmethod
    def inprocess(cls, owners: list[str]) -> MailboxSystem:
        """Build a :class:`MailboxSystem` with one :class:`InprocessMailbox`
        per owner.  Owner order is preserved (used for broadcast
        iteration stability)."""
        unique = _unique_owners(owners)
        return cls(
            owners=unique,
            inboxes={o: InprocessMailbox(o) for o in unique},
            event_log=[],
        )

    @classmethod
    def for_backend(
        cls,
        prefer: BackendKind,
        owners: list[str],
        *,
        root_dir: str | os.PathLike[str] | None = None,
        tmux_targets: dict[str, str] | None = None,
        iterm_targets: dict[str, str] | None = None,
    ) -> MailboxSystem:
        """Build a :class:`MailboxSystem` using the requested backend.

        The ``auto`` choice always maps to ``inprocess`` — it exists
        as a spec-level hint that the runtime is free to pick whatever
        is cheapest.  ``file`` / ``tmux`` / ``iterm`` all require a
        ``root_dir`` (a fresh directory is created if one is not
        supplied); they also share a cross-process :class:`FileSeqCounter`
        so envelopes from separate interpreters still get a strict
        total order on :attr:`Envelope.seq`.

        ``tmux_targets`` / ``iterm_targets`` map ``owner → target id``
        and are only consulted by the matching backend; missing entries
        simply fall back to file-only for that owner.
        """
        unique = _unique_owners(owners)

        if prefer in ("auto", "inprocess"):
            return cls(
                owners=unique,
                inboxes={o: InprocessMailbox(o) for o in unique},
                event_log=[],
            )

        # All remaining backends share a filesystem root + seq counter.
        from mycode.orchestration.runtime.mailbox_file import (
            FileMailbox,
            FileSeqCounter,
        )

        if root_dir is None:
            import tempfile

            root_dir = tempfile.mkdtemp(prefix="mycode-swarm-")

        counter = FileSeqCounter(root_dir)

        inboxes: dict[str, Mailbox]
        if prefer == "file":
            inboxes = {o: FileMailbox(o, root_dir) for o in unique}
        elif prefer == "tmux":
            from mycode.orchestration.runtime.mailbox_terminal import TmuxMailbox

            inboxes = {
                o: TmuxMailbox(o, root_dir, targets=tmux_targets) for o in unique
            }
        elif prefer == "iterm":
            from mycode.orchestration.runtime.mailbox_terminal import ItermMailbox

            inboxes = {
                o: ItermMailbox(o, root_dir, targets=iterm_targets) for o in unique
            }
        else:  # pragma: no cover - exhausted by Literal
            raise ValueError(f"unknown mailbox backend: {prefer!r}")

        return cls(
            owners=unique,
            inboxes=inboxes,
            event_log=[],
            _seq_fn=counter.next,
        )

    def next_seq(self) -> int:
        if self._seq_fn is not None:
            return self._seq_fn()
        return next(self._counter)

    def has(self, owner: str) -> bool:
        return owner in self.inboxes

    async def send(
        self,
        *,
        sender: str,
        recipient: str,
        content: str = "",
        summary: str = "",
        kind: EnvelopeKind = "message",
    ) -> Envelope:
        """Deliver a point-to-point envelope.  Raises ``KeyError`` if
        the recipient is unknown — callers (the send_message tool
        factory) are expected to turn that into a readable tool error
        so the LLM can retry with a correct name."""
        if recipient not in self.inboxes:
            raise KeyError(
                f"unknown swarm recipient {recipient!r}; "
                f"known: {sorted(self.inboxes)}"
            )
        env = Envelope(
            kind=kind,
            sender=sender,
            recipient=recipient,
            content=content,
            summary=summary,
            seq=self.next_seq(),
            timestamp=time.time(),
        )
        self.event_log.append(env)
        await self.inboxes[recipient].put(env)
        if self.on_send is not None:
            await self.on_send(env)
        return env

    async def broadcast(
        self,
        *,
        sender: str,
        content: str = "",
        summary: str = "",
    ) -> list[Envelope]:
        """Fan-out to every peer except ``sender``.  A single envelope
        with ``recipient="*"`` is logged first for transcript clarity,
        then one per-recipient delivery envelope is logged and placed
        into each inbox (they share ``seq`` grouping via consecutive
        counter values, which keeps ordering stable without needing a
        secondary index)."""
        summary_env = Envelope(
            kind="broadcast",
            sender=sender,
            recipient="*",
            content=content,
            summary=summary,
            seq=self.next_seq(),
            timestamp=time.time(),
        )
        self.event_log.append(summary_env)
        if self.on_send is not None:
            await self.on_send(summary_env)

        delivered: list[Envelope] = []
        for owner in self.owners:
            if owner == sender:
                continue
            env = Envelope(
                kind="broadcast",
                sender=sender,
                recipient=owner,
                content=content,
                summary=summary,
                seq=self.next_seq(),
                timestamp=time.time(),
            )
            self.event_log.append(env)
            await self.inboxes[owner].put(env)
            if self.on_send is not None:
                await self.on_send(env)
            delivered.append(env)
        return delivered

    async def shutdown_request(self, *, sender: str, recipient: str, reason: str = "") -> Envelope:
        return await self.send(
            sender=sender,
            recipient=recipient,
            content=reason,
            summary="shutdown requested",
            kind="shutdown_request",
        )

    async def shutdown_response(
        self,
        *,
        sender: str,
        recipient: str,
        approve: bool,
        note: str = "",
    ) -> Envelope:
        return await self.send(
            sender=sender,
            recipient=recipient,
            content=f"approve={approve}" + (f"\n\n{note}" if note else ""),
            summary="shutdown approved" if approve else "shutdown declined",
            kind="shutdown_response",
        )

    async def close(self) -> None:
        """Close every mailbox (idempotent)."""
        for mb in self.inboxes.values():
            await mb.close()


__all__ = [
    "BackendKind",
    "Envelope",
    "EnvelopeKind",
    "InprocessMailbox",
    "Mailbox",
    "MailboxSystem",
]
