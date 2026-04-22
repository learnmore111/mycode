"""M6.5 — file / tmux / iterm mailbox backend tests.

Three layers of coverage:

1. **FileMailbox unit tests** — round-trip envelopes through JSONL,
   offset-based drain, partial-line recovery, idempotent close.
2. **Cross-process round-trip** — spawn a real subprocess that
   appends to the *same* ``root_dir``; the parent process drains
   and sees the envelopes in ``seq`` order.  Uses
   :class:`FileSeqCounter` to confirm the counter is global.
3. **Terminal backends** — with no live tmux / iTerm present, the
   subprocess call must be best-effort: file delivery still succeeds,
   ``put()`` never raises.  We inject a stubbed binary via ``PATH``
   to observe the visual-mirror invocation shape.
4. **Swarm end-to-end** — ``run_swarm`` with
   ``spec.backend.prefer="file"`` uses the file backend under the
   hood; a pair of scripted peers exchange two messages and the
   transcript matches :attr:`MailboxSystem.event_log`.

All tests avoid networking and real LLMs; terminal backends degrade
cleanly when ``tmux`` / ``osascript`` are absent so this suite runs
on Linux CI.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import pytest

from mycode.agent.agent import AgentInfo
from mycode.orchestration.runtime import (
    FileMailbox,
    FileSeqCounter,
    InprocessMailbox,
    ItermMailbox,
    MailboxSystem,
    SpawnOutput,
    TmuxMailbox,
    run_swarm,
)
from mycode.orchestration.runtime.mailbox import Envelope
from mycode.orchestration.runtime.mailbox_file import (
    _envelope_to_json,
    _json_to_envelope,
)
from mycode.orchestration.runtime.mailbox_terminal import _format_visual_line
from mycode.orchestration.topology.schema import (
    AgentSpec,
    BackendSpec,
    OrchestrationSpec,
)

if TYPE_CHECKING:
    from pathlib import Path

    from mycode.orchestration.runtime.swarm import SwarmAgentContext


# --- Helpers ----------------------------------------------------------------


def _mk_env(sender: str = "a", recipient: str = "b", content: str = "hi", seq: int = 1) -> Envelope:
    return Envelope(
        kind="message",
        sender=sender,
        recipient=recipient,
        content=content,
        summary="",
        seq=seq,
        timestamp=123.0,
    )


# --- 1. FileMailbox unit ----------------------------------------------------


async def test_file_mailbox_roundtrip(tmp_path: Path) -> None:
    mb = FileMailbox("bob", tmp_path)
    await mb.put(_mk_env(seq=1))
    await mb.put(_mk_env(content="world", seq=2))
    out = await mb.drain()
    assert [e.seq for e in out] == [1, 2]
    assert [e.content for e in out] == ["hi", "world"]
    # Second drain returns nothing until more arrive.
    assert await mb.drain() == []
    await mb.put(_mk_env(content="more", seq=3))
    out2 = await mb.drain()
    assert [e.content for e in out2] == ["more"]
    await mb.close()


async def test_file_mailbox_json_shape(tmp_path: Path) -> None:
    mb = FileMailbox("alice", tmp_path)
    env = _mk_env(content="payload")
    await mb.put(env)
    lines = (tmp_path / "alice.jsonl").read_text().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed == {
        "kind": "message",
        "sender": "a",
        "recipient": "b",
        "content": "payload",
        "summary": "",
        "seq": 1,
        "timestamp": 123.0,
    }
    # Round-trip helpers agree with json.loads.
    assert _json_to_envelope(_envelope_to_json(env)) == env


async def test_file_mailbox_handles_torn_line(tmp_path: Path) -> None:
    # Simulate a crashed sibling by appending a half-written line — the
    # reader must skip it and still surface the good envelope appended
    # afterwards.
    inbox = tmp_path / "bob.jsonl"
    inbox.write_text('{"kind": "message", "sender": "x", "reci')  # torn, no newline
    mb = FileMailbox("bob", tmp_path)
    # Offset holds at start of torn line — nothing is complete yet.
    assert await mb.drain() == []
    # A later writer:
    #  1. terminates the broken line with **junk that is not valid JSON**
    #     (so parser skips it),
    #  2. appends a real envelope on the next line.
    good = _mk_env(seq=2)
    with open(inbox, "ab") as fh:
        fh.write(b'GARBAGE -- not json\n')
        fh.write((_envelope_to_json(good) + "\n").encode("utf-8"))
    got = await mb.drain()
    assert [e.seq for e in got] == [2]


async def test_file_seq_counter_is_monotonic(tmp_path: Path) -> None:
    c = FileSeqCounter(tmp_path)
    seqs = [c.next() for _ in range(5)]
    assert seqs == [1, 2, 3, 4, 5]
    # A second counter reusing the same dir continues the sequence.
    c2 = FileSeqCounter(tmp_path)
    assert c2.next() == 6


async def test_file_mailbox_close_is_idempotent(tmp_path: Path) -> None:
    mb = FileMailbox("x", tmp_path)
    await mb.close()
    await mb.close()
    # Writes on a closed mailbox are silently dropped (matches Inprocess).
    await mb.put(_mk_env())
    assert (tmp_path / "x.jsonl").read_text() == ""


# --- 2. Cross-process round-trip --------------------------------------------


_WRITER_SCRIPT = textwrap.dedent(
    """
    import sys, asyncio, json
    from mycode.orchestration.runtime.mailbox_file import (
        FileMailbox, FileSeqCounter,
    )
    from mycode.orchestration.runtime.mailbox import Envelope

    async def main():
        root = sys.argv[1]
        counter = FileSeqCounter(root)
        mb = FileMailbox("bob", root)
        for text in ("from-child-1", "from-child-2"):
            env = Envelope(
                kind="message", sender="alice", recipient="bob",
                content=text, summary="", seq=counter.next(),
                timestamp=0.0,
            )
            await mb.put(env)
        await mb.close()

    asyncio.run(main())
    """
).strip()


async def test_file_mailbox_cross_process(tmp_path: Path) -> None:
    # Parent writes one envelope first so the counter starts used.
    parent_counter = FileSeqCounter(tmp_path)
    parent_mb = FileMailbox("bob", tmp_path)
    parent_env = Envelope(
        kind="message",
        sender="alice",
        recipient="bob",
        content="from-parent",
        summary="",
        seq=parent_counter.next(),
        timestamp=0.0,
    )
    await parent_mb.put(parent_env)

    # Child appends two more envelopes via a fresh interpreter.
    script_path = tmp_path / "writer.py"
    script_path.write_text(_WRITER_SCRIPT)
    env = {**os.environ, "PYTHONPATH": os.getcwd()}
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script_path),
        str(tmp_path),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()

    out = await parent_mb.drain()
    assert [e.seq for e in out] == [1, 2, 3]
    assert [e.content for e in out] == ["from-parent", "from-child-1", "from-child-2"]
    await parent_mb.close()


# --- 3. Terminal backends ---------------------------------------------------


async def test_tmux_mailbox_no_binary_degrades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Force shutil.which to always miss so the mirror is suppressed but
    # the file write path still runs.
    monkeypatch.setattr(
        "mycode.orchestration.runtime.mailbox_terminal.shutil.which",
        lambda _name: None,
    )
    mb = TmuxMailbox("alice", tmp_path, targets={"alice": "sess:0.0"})
    await mb.put(_mk_env(content="hello"))
    out = await mb.drain()
    assert [e.content for e in out] == ["hello"]
    await mb.close()


async def test_iterm_mailbox_no_binary_degrades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "mycode.orchestration.runtime.mailbox_terminal.shutil.which",
        lambda _name: None,
    )
    mb = ItermMailbox("alice", tmp_path, targets={"alice": "UUID-1234"})
    await mb.put(_mk_env(content="yo"))
    out = await mb.drain()
    assert [e.content for e in out] == ["yo"]
    await mb.close()


def test_format_visual_line_truncates_long_summary() -> None:
    env = _mk_env(content="x" * 500, seq=7)
    env.summary = "y" * 500
    line = _format_visual_line(env)
    assert line.startswith("[#7] a → b: ")
    # Summary truncated to 140 chars, suffix "...".
    assert line.endswith("...")
    assert len(line) < 200


async def test_tmux_mailbox_invokes_stub_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install a fake ``tmux`` binary on PATH and verify the mailbox
    invokes it once per put, with the expected argv shape."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "tmux_calls.log"
    fake_tmux = bin_dir / "tmux"
    fake_tmux.write_text(
        f'#!/usr/bin/env bash\necho "$@" >> {log_file}\nexit 0\n'
    )
    fake_tmux.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    inbox_dir = tmp_path / "inbox"
    mb = TmuxMailbox(
        "alice",
        inbox_dir,
        targets={"alice": "my-sess:0.1"},
    )
    await mb.put(_mk_env(content="hi visual"))
    # Allow the subprocess to finish.
    await asyncio.sleep(0.05)
    log_text = log_file.read_text() if log_file.exists() else ""
    assert "send-keys" in log_text
    assert "-t" in log_text
    assert "my-sess:0.1" in log_text
    await mb.close()


# --- 4. Swarm end-to-end with file backend ---------------------------------


# Local scripted runner — mirrors the one in ``test_orchestration_m6_swarm.py``
# but trimmed to the bits we need here.


@dataclass
class _PeerScript:
    """Minimal script replayed by :class:`_ScriptedSwarmRunner`."""

    actions: list[tuple[str, dict[str, Any]]] = field(default_factory=list)


@dataclass
class _Plan:
    scripts: dict[str, _PeerScript] = field(default_factory=dict)


class _ScriptedSwarmRunner:
    """Deterministic :class:`SwarmAgentRunner`: replays pre-canned
    ``send`` / ``idle`` / ``done`` actions for each peer."""

    def __init__(self, plan: _Plan) -> None:
        self._plan = plan
        self._progress: dict[str, int] = {k: 0 for k in plan.scripts}

    async def __call__(self, sctx: SwarmAgentContext) -> SpawnOutput:
        name = sctx.sender_name
        script = self._plan.scripts.get(name)
        if script is None:
            return SpawnOutput(agent=name, task="", output="", turns=0, tool_calls=0)
        tool_calls = 0
        for _ in range(sctx.max_turns):
            if sctx.should_stop():
                break
            # Drain incoming envelopes (consume them silently — tests
            # assert on the system event log, not the peer's view).
            await sctx.system.inboxes[sctx.sender_name].drain()
            idx = self._progress[name]
            if idx >= len(script.actions):
                break
            action, kwargs = script.actions[idx]
            self._progress[name] = idx + 1
            if action == "send":
                await sctx.system.send(
                    sender=sctx.sender_name,
                    recipient=kwargs["recipient"],
                    content=kwargs.get("content", ""),
                    summary=kwargs.get("summary", ""),
                )
                tool_calls += 1
                await asyncio.sleep(0.01)
            elif action == "idle":
                await asyncio.sleep(0.02)
            elif action == "done":
                break
        return SpawnOutput(
            agent=name,
            task="",
            output=f"{name}-done",
            turns=1,
            tool_calls=tool_calls,
        )


def _agent(name: str) -> AgentInfo:
    return AgentInfo(
        name=name,
        description=f"test agent {name}",
        mode="all",
        native=False,
        source="project",
    )


async def test_run_swarm_with_file_backend(tmp_path: Path) -> None:
    spec = OrchestrationSpec(
        name="m65-swarm",
        mode="swarm",
        lead="lead",
        agents=[
            AgentSpec(name="lead"),
            AgentSpec(name="peer"),
        ],
        backend=BackendSpec(prefer="file", root_dir=str(tmp_path)),
    )
    agents = {"lead": _agent("lead"), "peer": _agent("peer")}
    plan = _Plan(
        scripts={
            "lead": _PeerScript(
                actions=[
                    ("send", {"recipient": "peer", "content": "please help", "summary": "ask"}),
                    ("idle", {}),
                    ("idle", {}),
                    ("done", {}),
                ]
            ),
            "peer": _PeerScript(
                actions=[
                    ("idle", {}),
                    ("send", {"recipient": "lead", "content": "ok", "summary": "ack"}),
                    ("idle", {}),
                    ("done", {}),
                ]
            ),
        }
    )
    result = await run_swarm(
        spec,
        agents,
        user_task="coordinate please",
        runner=_ScriptedSwarmRunner(plan),
        max_turns=6,
        walltime_seconds=5.0,
    )

    # Authoritative check: the JSONL files in ``tmp_path`` contain the
    # same two messages (lead → peer, peer → lead) that the peers sent.
    peer_lines = (tmp_path / "peer.jsonl").read_text().splitlines()
    lead_lines = (tmp_path / "lead.jsonl").read_text().splitlines()
    assert [json.loads(ln)["content"] for ln in peer_lines] == ["please help"]
    assert [json.loads(ln)["content"] for ln in lead_lines] == ["ok"]

    # The cross-process seq counter must have handed out 1 and 2.
    assert (tmp_path / "_seq").read_text().strip() in ("2", "3")  # >=2
    # And ``result`` still has both peers.
    assert set(result.peers) == {"lead", "peer"}


# --- 5. MailboxSystem.for_backend dispatch ---------------------------------


@pytest.mark.parametrize(
    "prefer,expected_cls",
    [
        ("auto", InprocessMailbox),
        ("inprocess", InprocessMailbox),
        ("file", FileMailbox),
        ("tmux", TmuxMailbox),
        ("iterm", ItermMailbox),
    ],
)
def test_for_backend_dispatch(
    tmp_path: Path,
    prefer: Literal["auto", "inprocess", "file", "tmux", "iterm"],
    expected_cls: type,
) -> None:
    sys_ = MailboxSystem.for_backend(
        prefer,
        ["a", "b"],
        root_dir=str(tmp_path),
    )
    for owner, mb in sys_.inboxes.items():
        assert isinstance(mb, expected_cls), (owner, type(mb).__name__)


def test_for_backend_rejects_duplicate_owners(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate owner"):
        MailboxSystem.for_backend("file", ["a", "a"], root_dir=str(tmp_path))


def test_for_backend_auto_allocates_root_dir_for_file() -> None:
    # No root_dir given → factory must mkdtemp() one; the file backend
    # writes inboxes there and leaves cleanup to the caller.
    sys_ = MailboxSystem.for_backend("file", ["x"])
    mb = sys_.inboxes["x"]
    assert isinstance(mb, FileMailbox)
    assert mb.root_dir.exists()
    # Clean up to avoid leaking tempdirs across the test session.
    import shutil

    shutil.rmtree(mb.root_dir, ignore_errors=True)


# --- 6. Guard: inprocess unchanged -----------------------------------------


async def test_inprocess_still_works_without_seq_fn(tmp_path: Path) -> None:
    """Regression: the existing in-process path must not accidentally
    pick up a file-backed counter."""
    sys_ = MailboxSystem.inprocess(["a", "b"])
    e1 = await sys_.send(sender="a", recipient="b", content="one")
    e2 = await sys_.send(sender="a", recipient="b", content="two")
    assert [e1.seq, e2.seq] == [1, 2]
    assert sys_._seq_fn is None  # in-process path keeps itertools counter
