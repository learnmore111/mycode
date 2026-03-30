"""OpenCode MCP Server — wraps the opencode HTTP API as MCP tools.

This allows external AI agents (e.g., CodeBuddy, Claude Desktop) to interact with
opencode's AI coding agent through the MCP protocol, supporting multi-turn conversations.

Usage:
    # Start opencode HTTP server first:
    uv run opencode serve --port 4096

    # Then run this MCP server (stdio transport for IDE integration):
    uv run python -m opencode.mcp_server.server

    # Or via the CLI command:
    uv run opencode mcp-server
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPENCODE_BASE_URL = os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096")
OPENCODE_DIRECTORY = os.environ.get("OPENCODE_DIRECTORY", ".")

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "opencode",
    instructions=(
        "OpenCode AI Coding Agent — an MCP server that exposes opencode's capabilities "
        "as tools. You can create sessions, send messages for multi-turn conversations, "
        "list models/providers, read files, and manage the opencode agent. "
        "Start by creating a session, then send messages to it."
    ),
)


def _client() -> httpx.AsyncClient:
    """Create an async HTTP client pointing at the opencode server."""
    return httpx.AsyncClient(base_url=OPENCODE_BASE_URL, timeout=300.0)


async def _collect_sse(response: httpx.Response) -> dict[str, Any]:
    """Read an SSE stream and collect all events into a structured result."""
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    errors: list[str] = []
    done_data: dict[str, Any] = {}
    started_data: dict[str, Any] = {}

    current_event = ""
    current_data_lines: list[str] = []

    for line in response.text.split("\n"):
        if line.startswith("event:"):
            # Flush previous event
            if current_event and current_data_lines:
                data_str = "\n".join(current_data_lines)
                try:
                    data = json.loads(data_str)
                except (json.JSONDecodeError, ValueError):
                    data = {"raw": data_str}
                _process_sse_event(
                    current_event.strip(), data,
                    text_parts, tool_calls, errors, done_data, started_data,
                )
            current_event = line[len("event:"):].strip()
            current_data_lines = []
        elif line.startswith("data:"):
            current_data_lines.append(line[len("data:"):].strip())
        elif line.strip() == "" and current_event:
            # Empty line = end of event
            if current_data_lines:
                data_str = "\n".join(current_data_lines)
                try:
                    data = json.loads(data_str)
                except (json.JSONDecodeError, ValueError):
                    data = {"raw": data_str}
                _process_sse_event(
                    current_event.strip(), data,
                    text_parts, tool_calls, errors, done_data, started_data,
                )
            current_event = ""
            current_data_lines = []

    result: dict[str, Any] = {}
    if started_data:
        result["model"] = started_data.get("model", "unknown")
        result["agent"] = started_data.get("agent", "unknown")
    if text_parts:
        result["response"] = "".join(text_parts)
    if tool_calls:
        result["tool_calls"] = tool_calls
    if errors:
        result["errors"] = errors
    if done_data:
        result["tokens"] = done_data.get("tokens", {})
        result["cost"] = done_data.get("cost", 0.0)
        result["iterations"] = done_data.get("iterations", 0)
        result["context"] = done_data.get("context", {})
    return result


def _process_sse_event(
    event_type: str,
    data: dict[str, Any],
    text_parts: list[str],
    tool_calls: list[dict[str, Any]],
    errors: list[str],
    done_data: dict[str, Any],
    started_data: dict[str, Any],
) -> None:
    """Process a single SSE event."""
    if event_type == "text":
        text_parts.append(data.get("content", ""))
    elif event_type == "tool":
        tool_calls.append({
            "tool": data.get("tool", "?"),
            "status": data.get("status", "?"),
            "output": data.get("output", "")[:200],
        })
    elif event_type == "error":
        errors.append(data.get("message", "unknown error"))
    elif event_type == "done":
        done_data.update(data)
    elif event_type == "started":
        started_data.update(data)


# ===========================================================================
# MCP Tools — Session Management
# ===========================================================================

@mcp.tool()
async def create_session(title: str = "New Session") -> str:
    """Create a new opencode conversation session.

    Call this first before sending messages. Returns the session ID
    which you'll need for all subsequent interactions.

    Args:
        title: A short title for the session (e.g., "Fix login bug").
    """
    async with _client() as client:
        resp = await client.post(
            "/session",
            json={"title": title},
            params={"directory": OPENCODE_DIRECTORY},
        )
        resp.raise_for_status()
        data = resp.json()
        return json.dumps({
            "session_id": data["id"],
            "title": data["title"],
            "created": data["time"]["created"],
        }, ensure_ascii=False)


@mcp.tool()
async def send_message(
    session_id: str,
    message: str,
    model: str | None = None,
    agent: str | None = None,
) -> str:
    """Send a message to an opencode session and get the AI agent's response.

    This is the core interaction tool. The opencode agent will process your message,
    potentially using tools (file editing, code search, shell commands, etc.),
    and return a response.

    Supports multi-turn conversations — just keep sending messages to the same session_id.

    Args:
        session_id: The session ID from create_session.
        message: Your message/instruction to the AI coding agent.
        model: Optional model override (e.g., "openai/deepseek-v3.2"). Uses default if not set.
        agent: Optional agent override (e.g., "build", "plan"). Uses default if not set.
    """
    body: dict[str, Any] = {
        "parts": [{"type": "text", "content": message}],
    }
    if model:
        body["model"] = model
    if agent:
        body["agent"] = agent

    async with _client() as client:
        # Use streaming request to handle SSE
        resp = await client.post(
            f"/session/{session_id}/message",
            json=body,
            params={"directory": OPENCODE_DIRECTORY},
            headers={"Accept": "text/event-stream"},
        )
        resp.raise_for_status()
        result = await _collect_sse(resp)
        return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def abort_session(session_id: str) -> str:
    """Abort the current processing in a session.

    Use this to stop a long-running agent operation.

    Args:
        session_id: The session ID to abort.
    """
    async with _client() as client:
        resp = await client.post(f"/session/{session_id}/abort")
        resp.raise_for_status()
        return json.dumps(resp.json(), ensure_ascii=False)


@mcp.tool()
async def list_sessions(limit: int = 20) -> str:
    """List recent opencode sessions.

    Args:
        limit: Maximum number of sessions to return.
    """
    async with _client() as client:
        resp = await client.get(
            "/session",
            params={"directory": OPENCODE_DIRECTORY, "limit": limit},
        )
        resp.raise_for_status()
        sessions = resp.json()
        # Return a compact summary
        summary = [{
            "id": s["id"],
            "title": s["title"],
            "updated": s["time"]["updated"],
        } for s in sessions]
        return json.dumps(summary, ensure_ascii=False)


@mcp.tool()
async def delete_session(session_id: str) -> str:
    """Delete an opencode session.

    Args:
        session_id: The session ID to delete.
    """
    async with _client() as client:
        resp = await client.delete(
            f"/session/{session_id}",
            params={"directory": OPENCODE_DIRECTORY},
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), ensure_ascii=False)


# ===========================================================================
# MCP Tools — Model & Provider Info
# ===========================================================================

@mcp.tool()
async def list_models() -> str:
    """List all available AI models that opencode can use.

    Returns provider/model pairs that can be passed as the 'model' parameter
    to send_message.
    """
    async with _client() as client:
        resp = await client.get("/provider")
        resp.raise_for_status()
        providers = resp.json()
        models = []
        for pid, p in providers.items():
            for mid in p.get("models", {}):
                models.append(f"{pid}/{mid}")
        return json.dumps({"models": models}, ensure_ascii=False)


@mcp.tool()
async def get_config() -> str:
    """Get the current opencode configuration.

    Returns model, provider settings, and other configuration.
    """
    async with _client() as client:
        resp = await client.get(
            "/config",
            params={"directory": OPENCODE_DIRECTORY},
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), ensure_ascii=False)


# ===========================================================================
# MCP Tools — File Operations
# ===========================================================================

@mcp.tool()
async def read_file(path: str) -> str:
    """Read a file from the opencode project directory.

    Args:
        path: Path to the file (relative to the project directory or absolute).
    """
    async with _client() as client:
        resp = await client.get(
            "/file",
            params={"path": path, "directory": OPENCODE_DIRECTORY},
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), ensure_ascii=False)


@mcp.tool()
async def list_files(path: str | None = None) -> str:
    """List files in the opencode project directory.

    Args:
        path: Optional subdirectory path. Lists the root if not specified.
    """
    params: dict[str, Any] = {"directory": OPENCODE_DIRECTORY}
    if path:
        params["path"] = path

    async with _client() as client:
        resp = await client.get("/file/list", params=params)
        resp.raise_for_status()
        return json.dumps(resp.json(), ensure_ascii=False)


@mcp.tool()
async def search_files(query: str, limit: int = 30) -> str:
    """Search for files in the opencode project.

    Args:
        query: Search query string.
        limit: Maximum number of results.
    """
    async with _client() as client:
        resp = await client.get(
            "/file/search",
            params={"query": query, "limit": limit, "directory": OPENCODE_DIRECTORY},
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), ensure_ascii=False)


# ===========================================================================
# MCP Tools — Server Status
# ===========================================================================

@mcp.tool()
async def server_status() -> str:
    """Check if the opencode server is running and healthy.

    Returns server status and version information.
    Call this first to verify the connection before using other tools.
    """
    async with _client() as client:
        try:
            resp = await client.get("/health")
            resp.raise_for_status()
            return json.dumps(resp.json(), ensure_ascii=False)
        except httpx.ConnectError:
            return json.dumps({
                "status": "offline",
                "error": f"Cannot connect to opencode server at {OPENCODE_BASE_URL}. "
                         "Please start it with: uv run opencode serve",
            }, ensure_ascii=False)


# ===========================================================================
# Entry point
# ===========================================================================

def main() -> None:
    """Run the MCP server with stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
