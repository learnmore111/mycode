"""CLI entry point using Click.

Equivalent to the original src/index.ts yargs CLI.
"""

from __future__ import annotations

import os

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
    """Run the interactive CLI REPL with Rich-powered UI."""
    import time
    from datetime import datetime

    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.styles import Style as PtStyle
    from rich.console import Console
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.spinner import Spinner
    from rich.table import Table
    from rich.text import Text

    from opencode.bus.bus import Bus
    from opencode.project.instance import provide
    from opencode.project.project import from_directory
    from opencode.session.memory import SessionMemory, save_session_note
    from opencode.session.prompt import PromptInput, prompt
    from opencode.session.session import create as create_session
    from opencode.tool.registry import register_builtins

    console = Console(highlight=False)
    register_builtins()
    abs_directory = os.path.abspath(directory)
    project = await from_directory(directory)

    # --- Welcome Panel ---
    blue = "dodger_blue1"
    logo = Text.from_markup(f"[{blue} bold]▐█▛█▛█▌\n▐█████▌[/{blue} bold]")
    head = Text.from_markup(f"[bold]Welcome to OpenCode v{__version__}![/bold]")
    help_text = Text.from_markup("[grey50]Type /help for commands, Ctrl+D to exit.[/grey50]")
    header_table = Table(show_header=False, show_edge=False, box=None, padding=(0, 1), expand=False)
    header_table.add_column(justify="left")
    header_table.add_column(justify="left")
    header_table.add_row(logo, Text.assemble(head, "\n", help_text))

    info_items = [
        ("Directory", abs_directory, "grey50"),
        ("Model", model or "default", "grey50"),
        ("Agent", agent or "build", "grey50"),
    ]
    info_lines = [header_table, Text("")]
    for name, value, color in info_items:
        info_lines.append(Text(f"{name}: {value}", style=color))
    info_lines.append(Text(""))
    tips = [
        ("Ctrl+J", "newline"),
        ("Ctrl+D", "exit"),
        ("/clear", "reset"),
        ("!cmd", "shell"),
    ]
    tip_text = "  ".join(f"{k}: {v}" for k, v in tips)
    info_lines.append(Text(tip_text, style="grey50"))

    from rich.console import Group
    console.print(Panel(
        Group(*info_lines),
        border_style=blue,
        expand=False,
        padding=(1, 2),
    ))
    console.print()

    # --- Prompt setup ---
    pt_style = PtStyle.from_dict({
        "bottom-toolbar": "noreverse",
    })
    history = InMemoryHistory()
    ps = PromptSession(
        history=history,
        style=pt_style,
    )

    # Input area border
    _border_color = "grey50"
    _border_char = "─"
    _border_width = 60

    session_info = None
    bus = Bus()
    conversation_history: list[dict] = []
    total_tokens_used = 0
    context_limit = 0
    session_start_time = datetime.now()  # Track when session started

    # Initialize session memory
    session_memory = SessionMemory(abs_directory)
    if session_memory.is_enabled:
        # Load recent notes for context (optional: can be used for context injection)
        recent_notes = session_memory.load_recent_notes()
        if recent_notes:
            console.print(Text(f"  📝 {len(recent_notes)} recent session notes available", style="grey50"))

    async def _run_loop() -> None:
        nonlocal session_info, conversation_history, total_tokens_used, context_limit, session_start_time

        while True:
            # Prompt symbol
            prompt_symbol = "✨ " if not (agent and agent == "plan") else "📋 "

            # Upper border of input area
            console.print(Text(
                f"┌{'─' * _border_width}",
                style=_border_color,
            ))

            try:
                with patch_stdout(raw=True):
                    user_input = await ps.prompt_async(
                        HTML(f"<b>│ {prompt_symbol}</b>"),
                        multiline=False,
                    )
            except (EOFError, KeyboardInterrupt):
                console.print(Text(
                    f"└{'─' * _border_width}",
                    style=_border_color,
                ))
                console.print("\n[grey50]Bye![/grey50]")
                break

            # Lower border of input area
            console.print(Text(
                f"└{'─' * _border_width}",
                style=_border_color,
            ))

            text = user_input.strip()
            if not text:
                continue

            if text.startswith("/"):
                handled = _handle_command(text, conversation_history, console, abs_directory)
                if handled == "quit":
                    console.print("[grey50]Bye![/grey50]")
                    break
                if handled == "clear":
                    session_info = None
                    conversation_history = []
                    total_tokens_used = 0
                continue

            # Shell escape: !command runs directly in shell
            if text.startswith("!"):
                shell_cmd = text[1:].strip()
                if shell_cmd:
                    await _run_shell(console, shell_cmd, abs_directory)
                continue

            if session_info is None:
                session_info = create_session(title=text[:60])

            inp = PromptInput(
                session_id=session_info.id,
                parts=[{"type": "text", "content": text}],
                model=model,
                agent=agent,
            )

            # --- Stream AI response with Rich Live ---
            console.print()
            full_text = ""
            done_data: dict = {}
            start_time = time.monotonic()
            spinner = Spinner("dots", "")

            with Live(spinner, console=console, refresh_per_second=10, transient=True) as live:
                def elapsed_fn(_st=start_time):  # noqa: B023
                    e = time.monotonic() - _st
                    return f"{e:.0f}s" if e >= 1 else "<1s"

                async for event in prompt(inp, bus, history=conversation_history):
                    if event.type == "started":
                        model_name = event.data.get("model", "?")
                        spinner.text = Text.assemble(
                            ("Composing... ", ""),
                            ("<1s", "grey50"),
                            (f" · {model_name}", "grey50"),
                        )

                    elif event.type == "text":
                        content = event.data.get("content", "")
                        full_text += content
                        spinner.text = Text.assemble(
                            ("Composing... ", ""),
                            (f"{elapsed_fn()}", "grey50"),
                        )

                    elif event.type == "tool":
                        tool_name = event.data.get("tool", "?")
                        status = event.data.get("status", "?")
                        output = event.data.get("output", "")
                        if status == "completed":
                            # Print tool result permanently
                            live.update(Text(""))
                            console.print(Text.assemble(
                                ("• ", "green"),
                                ("Used ", ""),
                                (tool_name, "blue"),
                            ))
                            if output:
                                preview = output[:300].strip()
                                if preview:
                                    console.print(Text(f"  {preview[:150]}", style="grey50"))
                        else:
                            tool_spinner = Spinner("dots", "")
                            tool_spinner.text = Text.assemble(
                                ("Using ", ""),
                                (tool_name, "blue"),
                            )
                            live.update(tool_spinner)

                    elif event.type == "error":
                        live.update(Text(""))
                        console.print(Text(f"✗ Error: {event.data.get('message', 'unknown')}", style="red bold"))

                    elif event.type == "compact":
                        spinner.text = Text("↻ Compacting context...", style="yellow")

                    elif event.type == "done":
                        done_data = event.data
                        live.update(Text(""))

            # Render the full AI response as Markdown
            if full_text.strip():
                console.print(Markdown(full_text.strip()))

            # Status line with tokens, cost, and context progress bar
            elapsed = time.monotonic() - start_time
            tokens = done_data.get("tokens", {}) if done_data else {}
            cost = done_data.get("cost", 0.0) if done_data else 0.0
            ctx_info = done_data.get("context", {}) if done_data else {}
            t_in = tokens.get("input", 0)
            t_out = tokens.get("output", 0)
            t_reason = tokens.get("reasoning", 0)
            t_cache_r = tokens.get("cache_read", 0)

            # Accumulate total tokens for context bar
            total_tokens_used += t_in + t_out
            if ctx_info.get("limit", 0) > 0:
                context_limit = ctx_info["limit"]

            parts_list = [f"{elapsed:.1f}s"]
            if t_in or t_out:
                parts_list.append(f"in:{t_in} out:{t_out}")
            if t_reason:
                parts_list.append(f"reasoning:{t_reason}")
            if t_cache_r:
                parts_list.append(f"cached:{t_cache_r}")
            if cost > 0:
                parts_list.append(f"${cost:.4f}")

            console.print(Text(
                f"  ─ {' · '.join(parts_list)}",
                style="grey50",
            ))

            # Context window progress bar
            if context_limit > 0:
                _print_context_bar(console, total_tokens_used, context_limit)
            console.print()

            # Keep conversation history
            conversation_history.append({"role": "user", "content": text})
            if full_text:
                conversation_history.append({"role": "assistant", "content": full_text})

        # --- Session end: save memory note if enabled ---
        if session_memory.is_enabled and conversation_history:
            console.print(Text("  💾 Saving session memory...", style="grey50"))
            try:
                note_path = await save_session_note(
                    project_path=abs_directory,
                    session_id=session_info.id if session_info else "unknown",
                    messages=conversation_history,
                    start_time=session_start_time,
                )
                if note_path:
                    console.print(Text(f"  ✓ Session note saved: {note_path.name}", style="green"))
            except Exception as e:
                console.print(Text(f"  ✗ Failed to save session note: {e}", style="red"))

        await bus.close()

    await provide(directory, _run_loop, project)


def _print_context_bar(console, used: int, limit: int, bar_width: int = 30) -> None:
    """Print a context window usage progress bar."""
    from rich.text import Text

    ratio = min(used / limit, 1.0) if limit > 0 else 0
    filled = int(bar_width * ratio)
    empty = bar_width - filled
    pct = ratio * 100

    # Color based on usage level
    if pct < 50:
        bar_color = "green"
    elif pct < 75:
        bar_color = "yellow"
    elif pct < 85:
        bar_color = "dark_orange"
    else:
        bar_color = "red"

    # Format token counts: 1234 → 1.2K, 123456 → 123K
    def _fmt(n: int) -> str:
        if n >= 100_000:
            return f"{n // 1000}K"
        if n >= 1000:
            return f"{n / 1000:.1f}K"
        return str(n)

    bar = Text.assemble(
        ("  Context ", "grey50"),
        ("▐", "grey30"),
        ("█" * filled, bar_color),
        ("░" * empty, "grey23"),
        ("▌", "grey30"),
        (f" {_fmt(used)}/{_fmt(limit)} ", "grey50"),
        (f"({pct:.0f}%)", bar_color),
    )
    console.print(bar)


async def _run_shell(console, command: str, cwd: str) -> None:
    """Execute a shell command directly and print output."""
    import asyncio as _aio
    import shutil

    from rich.text import Text

    shell = os.environ.get("SHELL", "/bin/sh")
    if os.path.basename(shell) in ("fish", "nu"):
        shell = shutil.which("bash") or shutil.which("zsh") or "/bin/sh"

    console.print(Text(f"  $ {command}", style="cyan"))
    try:
        proc = await _aio.create_subprocess_exec(
            shell, "-c", command,
            stdout=_aio.subprocess.PIPE,
            stderr=_aio.subprocess.STDOUT,
            cwd=cwd,
        )
        stdout, _ = await _aio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        if output:
            console.print(output.rstrip())
        if proc.returncode and proc.returncode != 0:
            console.print(Text(f"  exit code: {proc.returncode}", style="red"))
    except TimeoutError:
        console.print(Text("  ⏱ Command timed out (30s)", style="red"))
    except Exception as e:
        console.print(Text(f"  ✗ {e}", style="red"))
    console.print()


def _handle_command(text: str, history: list, console=None, project_path: str | None = None) -> str | None:
    """Handle slash commands. Returns 'quit', 'clear', or None."""
    if console is None:
        from rich.console import Console
        console = Console(highlight=False)

    parts = text.lower().split()
    cmd = parts[0]

    if cmd in ("/quit", "/exit", "/q"):
        return "quit"

    if cmd in ("/clear", "/reset"):
        console.print("  [yellow]↻ Conversation cleared[/yellow]")
        return "clear"

    if cmd == "/help":
        from rich.table import Table
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="cyan")
        table.add_column(style="grey50")
        table.add_row("/help", "Show this help")
        table.add_row("/clear", "Clear conversation history")
        table.add_row("/history", "Show conversation turns")
        table.add_row("/memory", "Show recent session notes")
        table.add_row("/quit", "Exit")
        table.add_row("!<cmd>", "Execute a shell command directly")
        table.add_row("", "")
        table.add_row("Ctrl+J", "Insert newline")
        table.add_row("Ctrl+D", "Exit")
        console.print(table)
        return ""

    if cmd == "/history":
        if not history:
            console.print("  [grey50](empty)[/grey50]")
        else:
            for i, msg in enumerate(history):
                role = msg["role"]
                content = msg.get("content", "")[:80]
                style = "green" if role == "user" else "blue"
                console.print(f"  [{style}][{i}] {role}:[/{style}] {content}")
        return ""

    if cmd == "/memory":
        from opencode.session.memory import SessionMemory
        if not project_path:
            console.print("  [grey50](no project path)[/grey50]")
            return ""
        memory = SessionMemory(project_path)
        if not memory.is_enabled:
            console.print("  [yellow]Session memory is disabled.[/yellow]")
            console.print("  [grey50]Enable it in config: sessionMemory.enabled = true[/grey50]")
            return ""
        notes = memory.load_recent_notes(limit=5)
        if not notes:
            console.print("  [grey50](no session notes found)[/grey50]")
        else:
            console.print(f"  [cyan]Recent session notes ({len(notes)}):[/cyan]")
            for note in notes:
                date = note.get("date", "?")
                duration = note.get("duration_minutes", 0)
                topics = ", ".join(note.get("topics", [])) or "general"
                console.print(f"    • {date} ({duration}min) - {topics}")
        return ""

    console.print(f"  [red]Unknown command: {cmd}. Type /help[/red]")
    return ""


async def _headless(directory: str, model: str | None, agent: str | None, message: str) -> None:
    """Run a single message in headless mode through the full agentic loop."""
    from datetime import datetime

    from opencode.bus.bus import Bus
    from opencode.project.instance import provide
    from opencode.project.project import from_directory
    from opencode.session.memory import SessionMemory, save_session_note
    from opencode.session.prompt import PromptInput, prompt
    from opencode.session.session import create as create_session
    from opencode.tool.registry import register_builtins

    register_builtins()
    project = await from_directory(directory)
    abs_directory = os.path.abspath(directory)
    session_memory = SessionMemory(abs_directory)

    async def _run() -> None:
        session = create_session(title=message[:60])
        session_start_time = datetime.now()
        conversation_history: list[dict] = []
        bus = Bus()
        inp = PromptInput(
            session_id=session.id,
            parts=[{"type": "text", "content": message}],
            model=model,
            agent=agent,
        )
        full_response = ""
        async for event in prompt(inp, bus):
            if event.type == "text":
                content = event.data.get("content", "")
                full_response += content
                click.echo(content, nl=False)
            elif event.type == "tool":
                tool_name = event.data.get("tool", "?")
                status = event.data.get("status", "?")
                click.echo(f"\n[tool:{tool_name}] {status}", err=True)
            elif event.type == "error":
                click.echo(f"\nError: {event.data.get('message', 'unknown')}", err=True)
            elif event.type == "done":
                tokens = event.data.get("tokens", {})
                cost = event.data.get("cost", 0.0)
                ctx_info = event.data.get("context", {})
                t_in = tokens.get("input", 0)
                t_out = tokens.get("output", 0)
                parts_list = [f"in:{t_in}", f"out:{t_out}"]
                if tokens.get("reasoning", 0):
                    parts_list.append(f"reasoning:{tokens['reasoning']}")
                if cost > 0:
                    parts_list.append(f"${cost:.4f}")
                ctx_limit = ctx_info.get("limit", 0)
                ctx_used = ctx_info.get("used", 0)
                if ctx_limit > 0:
                    pct = ctx_used / ctx_limit * 100
                    parts_list.append(f"ctx:{ctx_used}/{ctx_limit}({pct:.0f}%)")
                click.echo(f"\n\n--- Done ({' · '.join(parts_list)}) ---", err=True)

        # Build conversation history
        conversation_history.append({"role": "user", "content": message})
        if full_response:
            conversation_history.append({"role": "assistant", "content": full_response})

        # Save session memory if enabled
        if session_memory.is_enabled and conversation_history:
            try:
                note_path = await save_session_note(
                    project_path=abs_directory,
                    session_id=session.id,
                    messages=conversation_history,
                    start_time=session_start_time,
                )
                if note_path:
                    click.echo(f"Session note saved: {note_path.name}", err=True)
            except Exception as e:
                click.echo(f"Failed to save session note: {e}", err=True)

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


@mcp.command("serve")
@click.option("--opencode-url", default="http://127.0.0.1:4096", help="OpenCode HTTP server URL")
@click.option("--directory", "-d", default=".", help="Project directory")
def mcp_serve(opencode_url: str, directory: str) -> None:
    """Start the opencode MCP server (stdio transport).

    This exposes opencode as MCP tools for external AI agents.
    Make sure the opencode HTTP server is running first:

        opencode serve --port 4096
    """
    os.environ["OPENCODE_URL"] = opencode_url
    os.environ["OPENCODE_DIRECTORY"] = os.path.abspath(directory)

    from opencode.mcp_server.server import main
    main()


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
