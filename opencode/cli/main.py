"""CLI entry point using Click.

Equivalent to the original src/index.ts yargs CLI.
"""

from __future__ import annotations

import click

from opencode import __version__


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="opencode")
@click.option("--print-logs", is_flag=True, help="Print logs to stderr")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARN", "ERROR"]), default=None)
@click.pass_context
def cli(ctx: click.Context, print_logs: bool, log_level: str | None) -> None:
    """OpenCode — The open source AI coding agent."""
    from opencode.util import log as logmod
    from opencode.util.paths import GlobalPaths

    GlobalPaths.ensure_all()

    logmod.init(
        print_logs=print_logs,
        dev=False,
        level=log_level or "INFO",
        log_dir=GlobalPaths.data(),
    )

    if ctx.invoked_subcommand is None:
        # Default: start interactive TUI / headless mode
        click.echo(f"OpenCode v{__version__}")
        click.echo("Use --help for available commands.")


@cli.command()
@click.option("--port", default=4096, help="Port to listen on")
@click.option("--host", default="127.0.0.1", help="Hostname to bind")
def serve(port: int, host: str) -> None:
    """Start the headless API server."""
    import uvicorn

    from opencode.util import log as logmod

    logger = logmod.create(service="cli.serve")
    logger.info("starting server", port=port, host=host)

    uvicorn.run(
        "opencode.server.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
    )


@cli.command()
@click.argument("directory", default=".")
@click.option("--model", "-m", default=None, help="Model to use (provider/model)")
@click.option("--agent", "-a", default=None, help="Agent to use")
@click.option("--message", "-p", default=None, help="Message to send (headless mode)")
def run(directory: str, model: str | None, agent: str | None, message: str | None) -> None:
    """Start OpenCode in a directory (default: interactive mode)."""
    import asyncio

    from opencode.util import log as logmod

    logger = logmod.create(service="cli.run")
    logger.info("starting", directory=directory, model=model, agent=agent)

    if message:
        asyncio.run(_headless(directory, model, agent, message))
    else:
        asyncio.run(_interactive(directory, model, agent))


async def _interactive(directory: str, model: str | None, agent: str | None) -> None:
    """Run the interactive CLI REPL."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.styles import Style

    from opencode.bus.bus import Bus
    from opencode.project.instance import provide
    from opencode.project.project import from_directory
    from opencode.session.prompt import PromptInput, prompt
    from opencode.session.session import create as create_session
    from opencode.tool.registry import register_builtins

    register_builtins()
    project = await from_directory(directory)

    style = Style.from_dict({
        "prompt": "#00aa00 bold",
        "info": "#888888",
        "tool": "#aa8800",
        "error": "#ff0000 bold",
    })

    click.echo(f"\n  OpenCode v{__version__}")
    click.echo(f"  Model: {model or 'default'} | Agent: {agent or 'build'}")
    click.echo(f"  Directory: {directory}")
    click.echo("  Type /help for commands, Ctrl+D to exit\n")

    history = InMemoryHistory()
    ps = PromptSession(history=history)

    session_info = None
    bus = Bus()
    conversation_history: list[dict] = []

    async def _run_loop() -> None:
        nonlocal session_info, conversation_history

        while True:
            try:
                user_input = await ps.prompt_async(
                    HTML("<prompt>❯ </prompt>"),
                    style=style,
                    multiline=False,
                )
            except (EOFError, KeyboardInterrupt):
                click.echo("\nBye!")
                break

            text = user_input.strip()
            if not text:
                continue

            # Handle slash commands
            if text.startswith("/"):
                handled = _handle_command(text, conversation_history)
                if handled == "quit":
                    click.echo("Bye!")
                    break
                if handled == "clear":
                    session_info = None
                    conversation_history = []
                continue

            # Create session on first message
            if session_info is None:
                session_info = create_session(title=text[:60])

            inp = PromptInput(
                session_id=session_info.id,
                parts=[{"type": "text", "content": text}],
                model=model,
                agent=agent,
            )

            click.echo()
            full_text = ""
            async for event in prompt(inp, bus, history=conversation_history):
                if event.type == "text":
                    content = event.data.get("content", "")
                    full_text += content
                    click.echo(content, nl=False)
                elif event.type == "tool":
                    tool_name = event.data.get("tool", "?")
                    status = event.data.get("status", "?")
                    output = event.data.get("output", "")
                    click.echo(f"\n  ⚙ [{tool_name}] {status}", err=True)
                    if output and status == "completed":
                        preview = output[:200].replace("\n", "\n    ")
                        click.echo(f"    {preview}", err=True)
                elif event.type == "error":
                    click.echo(f"\n  ✗ Error: {event.data.get('message', 'unknown')}", err=True)
                elif event.type == "compact":
                    click.echo("\n  ↻ Context compacted", err=True)
                elif event.type == "done":
                    tokens = event.data.get("tokens", {})
                    iters = event.data.get("iterations", 0)
                    click.echo(f"\n  ─ tokens in:{tokens.get('input',0)} out:{tokens.get('output',0)} | iterations:{iters}", err=True)

            click.echo()

            # Keep conversation history
            conversation_history.append({"role": "user", "content": text})
            if full_text:
                conversation_history.append({"role": "assistant", "content": full_text})

        await bus.close()

    await provide(directory, _run_loop, project)


def _handle_command(text: str, history: list) -> str | None:
    """Handle slash commands. Returns 'quit', 'clear', or None."""
    cmd = text.lower().split()[0]

    if cmd in ("/quit", "/exit", "/q"):
        return "quit"

    if cmd in ("/clear", "/reset"):
        click.echo("  ↻ Conversation cleared")
        return "clear"

    if cmd == "/help":
        click.echo("  Commands:")
        click.echo("    /help          Show this help")
        click.echo("    /clear         Clear conversation history")
        click.echo("    /history       Show conversation turns")
        click.echo("    /quit          Exit")
        return ""

    if cmd == "/history":
        if not history:
            click.echo("  (empty)")
        else:
            for i, msg in enumerate(history):
                role = msg["role"]
                content = msg.get("content", "")[:80]
                click.echo(f"  [{i}] {role}: {content}")
        return ""

    click.echo(f"  Unknown command: {cmd}. Type /help")
    return ""


async def _headless(directory: str, model: str | None, agent: str | None, message: str) -> None:
    """Run a single message in headless mode through the full agentic loop."""
    from opencode.bus.bus import Bus
    from opencode.project.instance import provide
    from opencode.project.project import from_directory
    from opencode.session.prompt import PromptInput, prompt
    from opencode.session.session import create as create_session
    from opencode.tool.registry import register_builtins

    register_builtins()
    project = await from_directory(directory)

    async def _run() -> None:
        session = create_session(title=message[:60])
        bus = Bus()
        inp = PromptInput(
            session_id=session.id,
            parts=[{"type": "text", "content": message}],
            model=model,
            agent=agent,
        )
        async for event in prompt(inp, bus):
            if event.type == "text":
                click.echo(event.data.get("content", ""), nl=False)
            elif event.type == "tool":
                tool_name = event.data.get("tool", "?")
                status = event.data.get("status", "?")
                click.echo(f"\n[tool:{tool_name}] {status}", err=True)
            elif event.type == "error":
                click.echo(f"\nError: {event.data.get('message', 'unknown')}", err=True)
            elif event.type == "done":
                tokens = event.data.get("tokens", {})
                click.echo(f"\n\n--- Done (in:{tokens.get('input',0)} out:{tokens.get('output',0)}) ---", err=True)
        await bus.close()

    await provide(directory, _run, project)


@cli.command()
def providers() -> None:
    """List available AI providers and models."""
    import asyncio

    from opencode.provider.provider import list_providers

    async def _list() -> None:
        provs = await list_providers()
        if not provs:
            click.echo("No providers found. Set an API key env var (e.g. ANTHROPIC_API_KEY).")
            return
        for pid, p in provs.items():
            click.echo(f"  {pid} ({p.source}) — {len(p.models)} models")
            for mid in list(p.models.keys())[:5]:
                click.echo(f"    • {mid}")
            if len(p.models) > 5:
                click.echo(f"    ... and {len(p.models) - 5} more")

    asyncio.run(_list())


@cli.command()
def models() -> None:
    """List available models."""
    import asyncio

    from opencode.provider.provider import list_providers

    async def _list() -> None:
        provs = await list_providers()
        for pid, p in provs.items():
            for mid, _m in p.models.items():
                click.echo(f"  {pid}/{mid}")

    asyncio.run(_list())


# --- Config commands ---

@cli.group()
def config() -> None:
    """Manage configuration."""


@config.command("show")
@click.argument("directory", default=".")
def config_show(directory: str) -> None:
    """Show the merged configuration for a directory."""
    from opencode.config.config import get as get_config
    cfg = get_config(directory)
    data = cfg.model_dump(exclude_none=True)
    import json
    click.echo(json.dumps(data, indent=2, ensure_ascii=False))


@config.command("path")
def config_path() -> None:
    """Show the global config file path."""
    from opencode.config.paths import global_config_file
    click.echo(str(global_config_file()))


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a global config value (e.g. 'model anthropic/claude-sonnet-4')."""
    import json as jsonmod

    from opencode.config.config import update_global
    try:
        parsed = jsonmod.loads(value)
    except (jsonmod.JSONDecodeError, ValueError):
        parsed = value
    update_global({key: parsed})
    click.echo(f"Set {key} = {parsed}")


# --- Session commands ---

@cli.group()
def session() -> None:
    """Manage sessions."""


@session.command("list")
@click.option("--limit", "-n", default=20, help="Max sessions to show")
def session_list(limit: int) -> None:
    """List recent sessions."""
    import asyncio

    from opencode.project.instance import provide

    async def _list() -> None:
        from opencode.session.session import list_sessions
        sessions = list_sessions(limit=limit)
        if not sessions:
            click.echo("No sessions found.")
            return
        for s in sessions:
            click.echo(f"  {s.id[:12]}  {s.title[:60]}")

    asyncio.run(provide(".", _list))


@session.command("delete")
@click.argument("session_id")
def session_delete(session_id: str) -> None:
    """Delete a session by ID."""
    import asyncio

    from opencode.project.instance import provide

    async def _del() -> None:
        from opencode.session.session import remove
        remove(session_id)
        click.echo(f"Deleted session {session_id}")

    asyncio.run(provide(".", _del))


# --- MCP commands ---

@cli.group()
def mcp() -> None:
    """Manage MCP servers."""


@mcp.command("list")
def mcp_list() -> None:
    """List configured MCP servers."""
    from opencode.config.config import get as get_config
    cfg = get_config()
    if not cfg.mcp:
        click.echo("No MCP servers configured.")
        return
    for name, mcfg in cfg.mcp.items():
        stype = mcfg.get("type", "?") if isinstance(mcfg, dict) else "?"
        enabled = mcfg.get("enabled", True) if isinstance(mcfg, dict) else True
        status = "enabled" if enabled else "disabled"
        click.echo(f"  {name} ({stype}) — {status}")


# --- Snapshot commands ---

@cli.group()
def snapshot() -> None:
    """Manage snapshots (undo/redo)."""


@snapshot.command("track")
@click.argument("directory", default=".")
def snapshot_track(directory: str) -> None:
    """Take a snapshot of the current working directory."""
    import asyncio

    from opencode.project.project import from_directory
    from opencode.snapshot.snapshot import Snapshot

    async def _track() -> None:
        project = await from_directory(directory)
        snap = Snapshot(project.id, project.worktree)
        tree_hash = await snap.track()
        if tree_hash:
            click.echo(f"Snapshot: {tree_hash}")
        else:
            click.echo("Failed to create snapshot.", err=True)

    asyncio.run(_track())


@snapshot.command("diff")
@click.argument("tree_hash")
@click.argument("directory", default=".")
def snapshot_diff(tree_hash: str, directory: str) -> None:
    """Show diff between current state and a snapshot."""
    import asyncio

    from opencode.project.project import from_directory
    from opencode.snapshot.snapshot import Snapshot

    async def _diff() -> None:
        project = await from_directory(directory)
        snap = Snapshot(project.id, project.worktree)
        diff = await snap.diff(tree_hash)
        if diff:
            click.echo(diff)
        else:
            click.echo("No differences.")

    asyncio.run(_diff())
