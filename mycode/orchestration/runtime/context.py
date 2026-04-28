"""Runtime-side data structures for orchestration execution.

This module is **pure data** — no I/O, no LLM, no tool calls.  The
:class:`Coordinator` (in ``coordinator.py``) drives the DAG and writes
:class:`StageOutput` / :class:`SpawnOutput` records here.

Design notes:

- ``SpawnOutput`` captures the result of *one* ``SpawnSpec`` execution
  (one agent, one task).  Even when a stage has ``parallel=True`` or
  ``fan_out_from=...``, each produced spawn gets its own record.
- ``StageOutput`` aggregates the spawns of a stage plus, for
  ``runs_on=<agent>`` stages, a single coordinator-authored text body
  in ``coordinator_output``.
- ``RunContext`` accumulates everything produced so far so later stages
  can reference earlier ones through ``inputs: [stage_id.*]`` globs.
  It also carries the ``vars`` map (already rendered into the spec at
  load time, but kept here so custom runners can substitute ``$item`` /
  ``$index`` for fan-outs without re-running the full renderer).

The distinction between "per-spawn output text" and "per-stage synthesis"
matters because the coordinator synthesis stage in ``research.yaml``
must see each explorer's output **distinctly** (not concatenated), so
``RunContext.collect_inputs`` returns a list, never a joined string.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SpawnOutput:
    """Result of one ``SpawnSpec`` (agent + task) execution."""

    agent: str
    task: str
    output: str = ""
    is_error: bool = False
    title: str = ""
    turns: int = 0
    tool_calls: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        """One-line status for tree printing."""
        state = "ERR" if self.is_error else "OK"
        return f"[{state}] {self.agent}: {self.task[:60]}"


@dataclass
class StageOutput:
    """Aggregated result of one stage (may contain N spawns)."""

    stage_id: str
    spawns: list[SpawnOutput] = field(default_factory=list)
    # For ``runs_on=<agent>`` stages: the coordinator's synthesized body.
    coordinator_output: str | None = None
    coordinator_agent: str | None = None
    is_error: bool = False

    def ok_spawns(self) -> list[SpawnOutput]:
        return [s for s in self.spawns if not s.is_error]

    def first_error(self) -> SpawnOutput | None:
        for s in self.spawns:
            if s.is_error:
                return s
        return None


@dataclass
class RunContext:
    """Mutable state threaded through a single orchestration run."""

    flow_name: str
    vars: dict[str, Any] = field(default_factory=dict)
    stages: dict[str, StageOutput] = field(default_factory=dict)
    # Ordered history for reproducibility / debug tooling.
    stage_order: list[str] = field(default_factory=list)

    def record(self, stage: StageOutput) -> None:
        """Save a stage result; later records for the same id overwrite
        (should not happen in a valid DAG but we keep it defensive)."""
        if stage.stage_id not in self.stages:
            self.stage_order.append(stage.stage_id)
        self.stages[stage.stage_id] = stage

    # --- input resolution ---------------------------------------------------

    def collect_inputs(self, patterns: list[str]) -> list[SpawnOutput]:
        """Collect spawn outputs matching ``inputs: [stage.*]`` globs.

        Matching rules:

        - ``"<stage_id>"`` or ``"<stage_id>.*"`` → every spawn of that stage.
        - ``"<glob>"`` where glob contains '*' / '?' → match stage ids via
          ``fnmatch`` (useful for sibling-stage fan-ins like ``"research-*"``).

        Only successful spawns are returned (error spawns are skipped so
        a downstream synthesis stage does not see partial/error text as
        if it were a real finding).  Callers that need errors can read
        ``self.stages[...].first_error()`` directly.
        """
        out: list[SpawnOutput] = []
        for pattern in patterns:
            # Split off optional ".<sub>" (we currently only support ".*")
            if "." in pattern:
                stage_glob, _, _sub = pattern.partition(".")
            else:
                stage_glob = pattern
            for sid, stage in self.stages.items():
                if fnmatch.fnmatchcase(sid, stage_glob):
                    out.extend(stage.ok_spawns())
        return out

    def collect_inputs_text(self, patterns: list[str]) -> str:
        """Render inputs as a markdown-flavoured list suitable for direct
        injection into a coordinator prompt."""
        items = self.collect_inputs(patterns)
        if not items:
            return ""
        lines: list[str] = []
        for i, s in enumerate(items, start=1):
            lines.append(f"### Input {i} — agent=`{s.agent}`")
            lines.append(f"**task:** {s.task}")
            lines.append("")
            lines.append(s.output.strip() or "_(no output)_")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
