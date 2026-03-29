"""Agent system — defines built-in and custom agents."""
from opencode.agent.agent import AgentInfo, default_agent, get, invalidate, list_agents

__all__ = ["AgentInfo", "get", "list_agents", "default_agent", "invalidate"]
