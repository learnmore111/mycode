"""Tests for the agent system."""
import pytest
from mycode.agent.agent import AgentInfo, get, list_agents, default_agent, invalidate


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate()
    yield
    invalidate()


@pytest.mark.asyncio
async def test_get_build():
    agent = await get("build")
    assert agent is not None
    assert agent.name == "build"
    assert agent.mode == "primary"
    assert agent.native is True


@pytest.mark.asyncio
async def test_get_plan():
    agent = await get("plan")
    assert agent is not None
    assert agent.name == "plan"


@pytest.mark.asyncio
async def test_get_nonexistent():
    agent = await get("nonexistent_agent_xyz")
    assert agent is None


@pytest.mark.asyncio
async def test_list_agents():
    agents = await list_agents()
    assert len(agents) >= 2  # build, plan (hidden and subagent agents are filtered)
    names = {a.name for a in agents}
    assert "build" in names
    assert "plan" in names
    # Hidden and subagent agents should not appear
    assert "compaction" not in names
    assert "title" not in names
    assert "summary" not in names
    assert "explore" not in names
    assert "general" not in names


@pytest.mark.asyncio
async def test_default_agent():
    name = await default_agent()
    assert name == "build"


@pytest.mark.asyncio
async def test_list_agents_sorted():
    agents = await list_agents()
    # Default agent should be first
    assert agents[0].name == "build"


def test_agent_info_fields():
    a = AgentInfo(name="test", description="desc", mode="subagent")
    assert a.name == "test"
    assert a.temperature is None
    assert a.steps is None
    assert a.permission == []
