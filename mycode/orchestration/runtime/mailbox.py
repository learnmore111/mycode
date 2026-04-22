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
from typing import Literal, Protocol

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
        self._closed: bool = False

    async def put(self, env: Envelope) -> None:
        if self._closed:
            # Dropping on a closed mailbox matches the semantics the
            # runtime wants during teardown: the agent is gone, so a
            # laggy send should not raise.
            return
        await self._queue.put(env)

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
    """

    owners: list[str] = field(default_factory=list)
    inboxes: dict[str, Mailbox] = field(default_factory=dict)
    event_log: list[Envelope] = field(default_factory=list)
    _counter: itertools.count = field(default_factory=lambda: itertools.count(1))

    @classmethod
    def inprocess(cls, owners: list[str]) -> MailboxSystem:
        """Build a :class:`MailboxSystem` with one :class:`InprocessMailbox`
        per owner.  Owner order is preserved (used for broadcast
        iteration stability)."""
        seen: set[str] = set()
        unique: list[str] = []
        for o in owners:
            if o in seen:
                raise ValueError(f"duplicate owner: {o!r}")
            seen.add(o)
            unique.append(o)
        return cls(
            owners=unique,
            inboxes={o: InprocessMailbox(o) for o in unique},
            event_log=[],
        )

    def next_seq(self) -> int:
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
    "Envelope",
    "EnvelopeKind",
    "InprocessMailbox",
    "Mailbox",
    "MailboxSystem",
]
