"""M3 integration tests.

Exercises the wiring added in milestone M3:

1. ``mycode.agent.agent._build_all_agents`` overlays project/global Markdown
   agents discovered by :class:`AgentRegistry` so ``agentmod.get(name)``
   returns them.
2. ``SubAgentTool._filter_tools_for_agent`` respects ``agent.tools`` and
   still excludes recursion-unsafe tools.
3. ``SubAgentTool._apply_agent_max_turns`` applies the documented
   precedence (user arg → agent.max_turns → mode default) with cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mycode.agent import agent as agentmod
from mycode.agent.agent import AgentInfo
from mycode.tool.subagent import _TURNS_CONFIG, SubAgentTool

if TYPE_CHECKING:
    from pathlib import Path


# --- _build_all_agents overlay --------------------------------------------


def _write_project_agent(project_dir: Path, name: str, body: str = "Hi.") -> None:
    agents_dir = project_dir / ".mycode" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{name}.md").write_text(
        "---\n"
        "description: m3 project agent\n"
        "mode: subagent\n"
        "role: reviewer\n"
        "tools: [read, grep]\n"
        "max_turns: 7\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_build_all_agents_overlays_project_md(tmp_path: Path) -> None:
    _write_project_agent(tmp_path, "m3-project-agent")

    from mycode.project import provide

    async def _inner() -> None:
        agentmod.invalidate()
        info = await agentmod.get("m3-project-agent")
        assert info is not None
        assert info.source == "project"
        assert info.role == "reviewer"
        assert info.tools == ["read", "grep"]
        assert info.max_turns == 7

    try:
        await provide(str(tmp_path), _inner)
    finally:
        agentmod.invalidate()


@pytest.mark.asyncio
async def test_build_all_agents_overlay_ignored_without_project_ctx() -> None:
    """Without an active project context, project-level .md files must not leak."""
    agentmod.invalidate()
    try:
        # Built-ins still work.
        build = await agentmod.get("build")
        assert build is not None
        # Fictional project-only name is absent.
        assert await agentmod.get("m3-definitely-not-builtin") is None
    finally:
        agentmod.invalidate()


@pytest.mark.asyncio
async def test_build_all_agents_registry_error_is_non_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken registry discovery must not block builtin agents."""

    def _explode(*_args: object, **_kwargs: object) -> None:  # pragma: no cover - stub
        raise RuntimeError("boom")

    # Patch AgentRegistry.list_entries to raise after the overlay begins.
    import mycode.orchestration.registry.agent_registry as reg_mod
    monkeypatch.setattr(reg_mod.AgentRegistry, "list_entries", _explode)

    from mycode.project import provide

    async def _inner() -> None:
        agentmod.invalidate()
        build = await agentmod.get("build")
        assert build is not None, "registry failure must not mask builtin agents"

    try:
        await provide(str(tmp_path), _inner)
    finally:
        agentmod.invalidate()


# --- SubAgentTool._filter_tools_for_agent ---------------------------------


def _fake_tool(name: str) -> dict[str, object]:
    return {"type": "function", "function": {"name": name, "description": ""}}


def test_filter_tools_drops_excluded_always() -> None:
    tool = SubAgentTool()
    tools = [_fake_tool(n) for n in ("read", "grep", "subagent", "task", "todo")]
    # No allow-list → excluded set drops recursion tools.
    agent = AgentInfo(name="x", mode="subagent")
    filtered = tool._filter_tools_for_agent(agent, tools)
    names = [t["function"]["name"] for t in filtered]
    assert "read" in names and "grep" in names
    for forbidden in ("subagent", "task", "todo"):
        assert forbidden not in names


def test_filter_tools_allow_list_restricts_further() -> None:
    tool = SubAgentTool()
    tools = [_fake_tool(n) for n in ("read", "grep", "edit", "bash", "subagent")]
    agent = AgentInfo(name="x", mode="subagent", tools=["read", "grep"])
    filtered = tool._filter_tools_for_agent(agent, tools)
    names = {t["function"]["name"] for t in filtered}
    assert names == {"read", "grep"}


def test_filter_tools_empty_allow_list_treated_as_no_filter() -> None:
    """An explicit empty list means "no tools allowed" — LLM would be toolless."""
    tool = SubAgentTool()
    tools = [_fake_tool(n) for n in ("read", "grep")]
    # Empty list is falsy in our helper → treated as "no allow-list" (inherit).
    # Keep this behavior documented so authors use ``tools: null`` for "inherit"
    # and avoid ``tools: []``.
    agent_none = AgentInfo(name="a", mode="subagent", tools=None)
    agent_empty = AgentInfo(name="b", mode="subagent", tools=[])
    assert len(tool._filter_tools_for_agent(agent_none, tools)) == 2
    assert len(tool._filter_tools_for_agent(agent_empty, tools)) == 2


def test_filter_tools_allow_list_with_excluded_tool_still_drops_it() -> None:
    """Even if an author adds 'subagent' to tools, the exclusion list wins."""
    tool = SubAgentTool()
    tools = [_fake_tool(n) for n in ("subagent", "read")]
    agent = AgentInfo(name="x", mode="subagent", tools=["subagent", "read"])
    filtered = tool._filter_tools_for_agent(agent, tools)
    names = {t["function"]["name"] for t in filtered}
    assert names == {"read"}


# --- SubAgentTool._apply_agent_max_turns ----------------------------------


def test_max_turns_user_value_wins_over_agent_default() -> None:
    tool = SubAgentTool()
    agent = AgentInfo(name="x", mode="subagent", max_turns=20)
    # User supplied 5 → 5 wins (capped at mode max).
    assert tool._apply_agent_max_turns(agent, 5, "delegate") == 5


def test_max_turns_agent_fills_when_user_none() -> None:
    tool = SubAgentTool()
    agent = AgentInfo(name="x", mode="subagent", max_turns=10)
    assert tool._apply_agent_max_turns(agent, None, "delegate") == 10


def test_max_turns_falls_back_to_mode_default() -> None:
    tool = SubAgentTool()
    agent = AgentInfo(name="x", mode="subagent")  # no max_turns
    assert tool._apply_agent_max_turns(agent, None, "delegate") == _TURNS_CONFIG["delegate"][0]
    assert tool._apply_agent_max_turns(agent, None, "parallel") == _TURNS_CONFIG["parallel"][0]
    assert tool._apply_agent_max_turns(agent, None, "isolated") == _TURNS_CONFIG["isolated"][0]


def test_max_turns_capped_at_mode_max() -> None:
    tool = SubAgentTool()
    agent = AgentInfo(name="x", mode="subagent", max_turns=999)
    cap = _TURNS_CONFIG["delegate"][1]
    assert tool._apply_agent_max_turns(agent, None, "delegate") == cap
    # User-supplied value also capped.
    assert tool._apply_agent_max_turns(agent, 999, "delegate") == cap


def test_max_turns_never_below_one() -> None:
    tool = SubAgentTool()
    agent = AgentInfo(name="x", mode="subagent", max_turns=0)
    assert tool._apply_agent_max_turns(agent, 0, "delegate") == 1
