"""CLI entry point using Click.

"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

import click

from mycode import __version__

if TYPE_CHECKING:
    from prompt_toolkit.document import Document


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="mycode")
@click.option("--print-logs", is_flag=True, help="Print logs to stderr")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARN", "ERROR"]), default=None)
@click.pass_context
def cli(ctx: click.Context, print_logs: bool, log_level: str | None) -> None:
    """MyCode — AI coding agent."""
    from mycode.util import log as logmod
    from mycode.util.paths import GlobalPaths

    GlobalPaths.ensure_all()

    logmod.init(
        print_logs=print_logs,
        dev=False,
        level=log_level or "INFO",
        log_dir=GlobalPaths.data(),
    )

    if ctx.invoked_subcommand is None:
        # Default: start interactive TUI / headless mode
        click.echo(f"MyCode v{__version__}")
        click.echo("Use --help for available commands.")


@cli.command()
@click.option("--port", default=4096, help="Port to listen on")
@click.option("--host", default="127.0.0.1", help="Hostname to bind")
def serve(port: int, host: str) -> None:
    """Start the headless API server."""
    import uvicorn

    from mycode.util import log as logmod

    logger = logmod.create(service="cli.serve")
    logger.info("starting server", port=port, host=host)

    uvicorn.run(
        "mycode.server.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
    )


@cli.command()
@click.option("--port", default=4096, help="Backend API port")
@click.option("--host", default="127.0.0.1", help="Backend hostname to bind")
@click.option("--frontend-port", default=3000, help="Frontend dev server port")
def dev(port: int, host: str, frontend_port: int) -> None:
    """Start both backend API server and frontend dev server for development."""
    import signal
    import subprocess
    import sys
    import time

    from mycode.util import log as logmod

    logger = logmod.create(service="cli.dev")

    # Resolve the web/ directory relative to this project
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    web_dir = os.path.join(project_root, "web")

    if not os.path.isdir(web_dir):
        click.echo(f"Error: web directory not found at {web_dir}", err=True)
        sys.exit(1)

    click.echo(f"Starting backend API server on http://{host}:{port}")
    click.echo(f"Starting frontend dev server on http://localhost:{frontend_port}")
    click.echo("Press Ctrl+C to stop both servers.\n")

    procs: list[subprocess.Popen[bytes]] = []

    def cleanup(signum: int | None = None, frame: object = None) -> None:
        for p in procs:
            with contextlib.suppress(OSError):
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        # Start backend API server
        backend_cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "mycode.server.app:create_app",
            "--factory",
            f"--host={host}",
            f"--port={port}",
            "--log-level=info",
        ]
        backend_proc = subprocess.Popen(backend_cmd, cwd=project_root)
        procs.append(backend_proc)
        logger.info("backend started", pid=backend_proc.pid, port=port)

        # Start frontend Vite dev server
        # Try npx first, fall back to npm run dev
        frontend_cmd = ["npx", "vite", "--port", str(frontend_port)]
        frontend_proc = subprocess.Popen(frontend_cmd, cwd=web_dir)
        procs.append(frontend_proc)
        logger.info("frontend started", pid=frontend_proc.pid, port=frontend_port)

        click.echo(f"Backend API:  http://{host}:{port}")
        click.echo(f"Frontend UI:  http://localhost:{frontend_port}")
        click.echo()

        # Wait for either process to exit
        while True:
            for p in procs:
                ret = p.poll()
                if ret is not None:
                    click.echo(f"Process (pid={p.pid}) exited with code {ret}, shutting down...")
                    cleanup()
            time.sleep(0.5)

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        cleanup()


@cli.command()
@click.argument("directory", default=".")
@click.option("--model", "-m", default=None, help="Model to use (provider/model)")
@click.option("--agent", "-a", default=None, help="Agent to use")
@click.option("--message", "-p", default=None, help="Message to send (headless mode)")
def run(directory: str, model: str | None, agent: str | None, message: str | None) -> None:
    """Start MyCode in a directory (default: interactive mode)."""
    import asyncio

    from mycode.util import log as logmod

    logger = logmod.create(service="cli.run")
    logger.info("starting", directory=directory, model=model, agent=agent)

    if message:
        asyncio.run(_headless(directory, model, agent, message))
    else:
        asyncio.run(_interactive(directory, model, agent))


async def _interactive(directory: str, model: str | None, agent: str | None) -> None:
    """Run the interactive CLI REPL with Rich-powered UI."""
    import shlex
    import time
    from datetime import datetime
    from pathlib import Path

    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import (
        Completer,
        Completion,
        merge_completers,
    )
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.styles import Style as PtStyle
    from rich.console import Console
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.spinner import Spinner
    from rich.text import Text

    from mycode.bus.bus import Bus
    from mycode.project.instance import provide
    from mycode.project.project import from_directory
    from mycode.provider import provider as providermod
    from mycode.session.memory import SessionMemory
    from mycode.session.prompt import PromptInput, prompt
    from mycode.session.session import create as create_session
    from mycode.tool.registry import register_builtins

    console = Console(highlight=False)
    register_builtins()
    abs_directory = os.path.abspath(directory)
    project = await from_directory(directory)

    # --- Mutable working directory for shell commands ---
    shell_cwd = [abs_directory]  # Use list to allow mutation in nested scope

    # --- Mutable model reference (allows runtime switching via /model) ---
    model_ref = [model]  # Use list to allow mutation in nested scope

    # --- Pre-fetch available models (will be populated inside provide() context) ---
    _available_models: list[str] = []
    debug_mode: list[bool] = [False]  # Use list for mutation in nested scope

    # --- Welcome will be printed inside _run_loop (after provide() sets project context) ---

    # --- Completer: slash commands, !shell commands, @file paths ---
    _slash_commands = {
        "/help": "Show available commands",
        "/clear": "Clear conversation history",
        "/reset": "Clear conversation history",
        "/model": "Switch model (/model <provider/model>)",
        "/history": "Show conversation turns",
        "/debug": "Toggle debug mode (dump LLM input/output to file)",
        "/memory": "Show recent session notes",
        "/reload-plugin": "Reload a plugin: /reload-plugin <name>",
        "/quit": "Exit",
        "/exit": "Exit",
        "/q": "Exit",
    }

    class _SlashCompleter(Completer):
        """Fuzzy-match slash commands like /help, /clear. Also complete model names after /model."""
        def get_completions(self, document: Document, complete_event):
            text = document.text_before_cursor.lstrip()
            if not text.startswith("/"):
                return

            # Check if user is typing /model <model_name>
            if text.lower().startswith("/model "):
                fragment = text[7:]  # after "/model "
                for m in _available_models:
                    if fragment.lower() in m.lower() or not fragment:
                        yield Completion(
                            f"/model {m}",
                            start_position=-len(text),
                            display=m,
                            display_meta="switch model",
                        )
                return

            typed = text[1:]
            for cmd, desc in _slash_commands.items():
                name = cmd[1:]  # strip leading /
                if typed.lower() in name.lower() or not typed:
                    yield Completion(cmd, start_position=-len(text), display=cmd, display_meta=desc)

    class _ShellCompleter(Completer):
        """Complete commands after ! prefix with common shell commands and path completion."""
        _common_cmds = [
            "ls", "cd", "pwd", "cat", "head", "tail", "grep", "find", "echo",
            "mkdir", "rm", "cp", "mv", "touch", "git", "python", "pip", "uv",
            "which", "env", "export", "source", "make", "curl", "wget", "tree",
        ]
        def get_completions(self, document: Document, complete_event):
            text = document.text_before_cursor.lstrip()
            if not text.startswith("!"):
                return
            shell_text = text[1:]
            # If no space yet, complete the command name
            if " " not in shell_text:
                for cmd in self._common_cmds:
                    if shell_text.lower() in cmd.lower() or not shell_text:
                        yield Completion(f"!{cmd}", start_position=-len(text), display=f"!{cmd}")
            else:
                # After the command, offer path completion
                parts = shell_text.split(None, 1)
                fragment = parts[1] if len(parts) >= 2 else ""
                base_dir = shell_cwd[0]
                try:
                    p = Path(base_dir)
                    if fragment:
                        search_dir = p / Path(fragment).parent if "/" in fragment else p
                        prefix = Path(fragment).name if "/" in fragment else fragment
                    else:
                        search_dir = p
                        prefix = ""
                    if search_dir.is_dir():
                        for entry in sorted(search_dir.iterdir()):
                            name = entry.name
                            if name.startswith("."):
                                continue
                            if prefix and not name.lower().startswith(prefix.lower()):
                                continue
                            display_name = f"{name}/" if entry.is_dir() else name
                            # Build the full replacement text
                            rel = str(Path(fragment).parent / name) if "/" in fragment else name
                            if entry.is_dir():
                                rel += "/"
                            full_text = f"!{parts[0]} {rel}"
                            yield Completion(full_text, start_position=-len(text), display=display_name)
                except OSError:
                    pass

    class _FileMentionCompleter(Completer):
        """Complete file paths after @ mention."""
        def get_completions(self, document: Document, complete_event):
            text = document.text_before_cursor
            idx = text.rfind("@")
            if idx == -1:
                return
            # Don't trigger in the middle of a word (e.g. email@)
            if idx > 0 and text[idx - 1].isalnum():
                return
            fragment = text[idx + 1:]
            if " " in fragment:
                return
            base_dir = shell_cwd[0]
            try:
                p = Path(base_dir)
                if fragment and "/" in fragment:
                    search_dir = p / Path(fragment).parent
                    prefix = Path(fragment).name
                else:
                    search_dir = p
                    prefix = fragment
                if not search_dir.is_dir():
                    return
                for entry in sorted(search_dir.iterdir()):
                    name = entry.name
                    if name.startswith("."):
                        continue
                    if prefix and prefix.lower() not in name.lower():
                        continue
                    rel_path = str(Path(fragment).parent / name) if "/" in fragment else name
                    if entry.is_dir():
                        rel_path += "/"
                    yield Completion(
                        rel_path, start_position=-len(fragment),
                        display=f"{name}/" if entry.is_dir() else name,
                    )
            except OSError:
                pass

    completer = merge_completers([_SlashCompleter(), _ShellCompleter(), _FileMentionCompleter()])

    # --- Prompt setup ---
    def _bottom_toolbar():
        """Subtle bottom status bar."""
        cwd_display = shell_cwd[0]
        home = os.path.expanduser("~")
        if cwd_display.startswith(home):
            cwd_display = "~" + cwd_display[len(home):]
        return HTML(
            f'  <style fg="#555555">{cwd_display}</style>'
            f'  <style fg="#444444">Ctrl+D: exit</style>'
        )

    pt_style = PtStyle.from_dict({
        "bottom-toolbar": "noreverse",
        "bottom-toolbar.text": "",
        "prompt": "",
    })
    history = InMemoryHistory()
    ps = PromptSession(
        history=history,
        style=pt_style,
        completer=completer,
        complete_while_typing=True,
        reserve_space_for_menu=4,
        placeholder=HTML('<style fg="#555555">Send a message...</style>'),
        bottom_toolbar=_bottom_toolbar,
    )


    # --- Token formatting helper ---
    def _fmt_tokens(n: int) -> str:
        if n >= 100_000:
            return f"{n // 1000}K"
        if n >= 1000:
            return f"{n / 1000:.1f}K"
        return str(n)


    session_info = None
    bus = Bus()
    conversation_history: list[dict] = []
    total_tokens_used = 0
    context_limit = 0
    last_checkpoint: dict = {}
    session_start_time = datetime.now()  # Track when session started

    # Initialize session memory
    session_memory = SessionMemory(abs_directory)
    if session_memory.is_enabled:
        # Load recent notes for context (optional: can be used for context injection)
        recent_notes = session_memory.load_recent_sessions()
        if recent_notes:
            console.print(Text(f"  ℹ {len(recent_notes)} recent session notes", style="dim"))

    async def _run_loop() -> None:
        nonlocal session_info, conversation_history, total_tokens_used, context_limit, last_checkpoint, session_start_time

        # Pre-fetch models (now inside provide() context, project config is available)
        try:
            providermod.invalidate()  # Clear stale provider state
            provs = await providermod.list_providers()
            for pid, p in provs.items():
                for mid in p.models:
                    _available_models.append(f"{pid}/{mid}")
        except Exception:
            pass

        # Resolve display model name
        display_model = model_ref[0]
        if not display_model:
            try:
                from mycode.config import config as configmod
                cfg = configmod.get()
                display_model = cfg.model or "default"
            except Exception:
                display_model = "default"

        # Welcome
        console.print()
        console.print(Text.assemble(
            ("╭ ", "dim"),
            ("MyCode", "bold"),
            (f" v{__version__}", "dim"),
        ))
        console.print(Text.assemble(
            ("│ ", "dim"),
            ("model: ", "dim"),
            (display_model, ""),
            ("  cwd: ", "dim"),
            (abs_directory, ""),
        ))
        console.print(Text.assemble(
            ("╰ ", "dim"),
            ("Type ", "dim"),
            ("/help", "bold"),
            (" for commands · ", "dim"),
            ("Ctrl+D", "bold"),
            (" to exit", "dim"),
        ))
        console.print()

        while True:
            # simple ❯ prompt
            prompt_symbol = "❯ "

            prompt_msg = HTML(
                f'<style fg="#6366f1"><b>{prompt_symbol}</b></style>'
            )

            try:
                with patch_stdout(raw=True):
                    user_input = await ps.prompt_async(
                        prompt_msg,
                        multiline=False,
                    )
            except (EOFError, KeyboardInterrupt):
                console.print("\n[grey50]Bye![/grey50]")
                break

            text = user_input.strip()
            if not text:
                continue

            if text.startswith("/"):
                if text.lower().startswith("/model"):
                    # Handle /model command inline (needs access to model_ref and _available_models)
                    parts_cmd = text.split(None, 1)
                    if len(parts_cmd) < 2:
                        # No argument — list available models
                        if _available_models:
                            console.print(f"  [bold]Current:[/bold] {model_ref[0] or 'default'}")
                            console.print(f"  [bold]Available ({len(_available_models)}):[/bold]")
                            for m in _available_models:
                                marker = " ←" if m == model_ref[0] else ""
                                console.print(f"    {m}[green]{marker}[/green]")
                        else:
                            console.print("  [dim]No models found. Set an API key env var.[/dim]")
                        console.print("  [dim]Usage: /model <provider/model>[/dim]")
                    else:
                        new_model = parts_cmd[1].strip()
                        if new_model in _available_models:
                            old_model = model_ref[0] or "default"
                            model_ref[0] = new_model
                            console.print(f"  [green]✓ Model: {old_model} → {new_model}[/green]")
                        else:
                            # Try fuzzy match
                            matches = [m for m in _available_models if new_model.lower() in m.lower()]
                            if len(matches) == 1:
                                old_model = model_ref[0] or "default"
                                model_ref[0] = matches[0]
                                console.print(f"  [green]✓ Model: {old_model} → {matches[0]}[/green]")
                            elif matches:
                                console.print(f"  [yellow]Ambiguous: '{new_model}'. Matches:[/yellow]")
                                for m in matches:
                                    console.print(f"    {m}")
                            else:
                                console.print(f"  [red]✗ Unknown model: {new_model}[/red]")
                                console.print("  [dim]Use /model to list available models.[/dim]")
                    continue

                handled = _handle_command(text, conversation_history, console, abs_directory, last_checkpoint=last_checkpoint, debug_ref=debug_mode)
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
                    # Handle cd command specially — update cwd in main process
                    try:
                        parts_cmd = shlex.split(shell_cmd)
                    except ValueError:
                        parts_cmd = shell_cmd.split()
                    if parts_cmd and parts_cmd[0] == "cd":
                        target = parts_cmd[1] if len(parts_cmd) > 1 else os.path.expanduser("~")
                        # Resolve relative to current shell_cwd
                        new_dir = os.path.normpath(os.path.join(shell_cwd[0], os.path.expanduser(target)))
                        if os.path.isdir(new_dir):
                            shell_cwd[0] = new_dir
                            console.print(Text(f"  → {new_dir}", style="cyan"))
                        else:
                            console.print(Text(f"  ✗ cd: no such directory: {target}", style="red"))
                        console.print()
                    else:
                        await _run_shell(console, shell_cmd, shell_cwd[0])
                continue

            if session_info is None:
                session_info = create_session(title=text[:60])
                # Initialize session memory with session ID
                if session_memory.is_enabled:
                    session_memory.session_id = session_info.id

            inp = PromptInput(
                session_id=session_info.id,
                parts=[{"type": "text", "content": text}],
                model=model_ref[0],
                agent=agent,
            )

            # --- Stream AI response ---
            console.print()
            full_text = ""
            done_data: dict = {}
            start_time = time.monotonic()
            _text_buf = ""
            _in_text = False
            _started = False
            _tool_count = 0  # Track tool calls in this turn

            def _flush_text() -> None:
                """Render accumulated text as Markdown and reset buffer."""
                nonlocal _text_buf, _in_text
                if _text_buf.strip():
                    console.print(Markdown(_text_buf.strip()))
                _text_buf = ""
                _in_text = False

            def _tool_label(tool_name: str, tool_input: dict | None = None) -> str:
                """Generate Generate tool label: verb + target."""
                ti = tool_input or {}
                # Map tool IDs to human-friendly verbs + extract key arg
                name_map = {
                    "read": ("Read", ("file_path", "filePath", "path")),
                    "read_file": ("Read", ("file_path", "filePath", "path")),
                    "write": ("Write", ("file_path", "filePath", "path")),
                    "write_file": ("Write", ("file_path", "filePath", "path")),
                    "write_to_file": ("Write", ("file_path", "filePath", "path")),
                    "edit": ("Edit", ("file_path", "filePath", "path")),
                    "replace_in_file": ("Edit", ("file_path", "filePath", "path")),
                    "bash": ("Bash", ("command",)),
                    "glob": ("Glob", ("pattern",)),
                    "grep": ("Grep", ("pattern",)),
                    "search_content": ("Search", ("pattern",)),
                    "codebase_search": ("Search", ("query",)),
                    "list": ("List", ("path",)),
                    "list_dir": ("List", ("path", "target_directory")),
                    "webfetch": ("Fetch", ("url",)),
                    "websearch": ("Search", ("query",)),
                    "task": ("Task", ("description",)),
                    "todowrite": ("Todo", ()),
                }
                verb, keys = name_map.get(tool_name, (tool_name, ()))
                target = ""
                for k in keys:
                    if k in ti:
                        val = str(ti[k])
                        # Shorten file paths: show just filename or last 2 segments
                        if "/" in val and len(val) > 50:
                            parts = val.rstrip("/").split("/")
                            val = "/".join(parts[-2:]) if len(parts) > 2 else val
                        target = val[:60]
                        break
                return f"{verb} {target}" if target else verb

            # Initial "thinking" spinner
            spinner = Spinner("dots", "")
            live = Live(spinner, console=console, refresh_per_second=10, transient=True)
            live.start()

            try:
                async for event in prompt(inp, bus, history=conversation_history, debug=debug_mode[0]):
                    if event.type == "started":
                        _started = True
                        model_name = event.data.get("model", "?")
                        spinner.text = Text.assemble(
                            ("Thinking", "dim italic"),
                            (f"  ({model_name})", "dim"),
                        )

                    elif event.type == "text_delta":
                        if _started and live.is_started:
                            live.stop()
                            _started = False
                        content = event.data.get("content", "")
                        full_text += content
                        _text_buf += content
                        _in_text = True

                    elif event.type == "tool_start":
                        if live.is_started:
                            live.stop()
                            _started = False
                        _flush_text()
                        _tool_count += 1
                        tool_name = event.data.get("tool", "?")
                        label = _tool_label(tool_name)
                        # Show spinner with tool label
                        tool_spinner = Spinner("dots", "")
                        tool_spinner.text = Text.assemble(
                            ("  ", ""),
                            (label, "dim"),
                        )
                        live = Live(tool_spinner, console=console, refresh_per_second=10, transient=True)
                        live.start()

                    elif event.type == "tool_running":
                        tool_name = event.data.get("tool", "?")
                        tool_input = event.data.get("input", {})
                        if live.is_started:
                            label = _tool_label(tool_name, tool_input)
                            tool_spinner = Spinner("dots", "")
                            tool_spinner.text = Text.assemble(
                                ("  ", ""),
                                (label, "dim"),
                            )
                            live.update(tool_spinner)

                    elif event.type == "tool_done":
                        if live.is_started:
                            live.stop()
                        tool_name = event.data.get("tool", "?")
                        status = event.data.get("status", "?")
                        output = event.data.get("output", "")
                        tool_input = event.data.get("input", {})

                        # Record tool call to session memory
                        if session_memory.is_enabled:
                            session_memory.record_tool_call(
                                tool_name=tool_name,
                                tool_input=tool_input if isinstance(tool_input, dict) else {},
                                tool_output=output,
                                status=status,
                            )

                        label = _tool_label(tool_name, tool_input)

                        # ✓/✗ + tool label
                        if status == "completed":
                            console.print(Text.assemble(
                                ("  ✓ ", "green"),
                                (label, ""),
                            ))
                        elif status == "error":
                            console.print(Text.assemble(
                                ("  ✗ ", "red"),
                                (label, ""),
                            ))
                        else:
                            console.print(Text.assemble(
                                ("  • ", "yellow"),
                                (label, ""),
                            ))

                        # Show brief output preview (show compact result)
                        if output and status == "completed":
                            preview = output[:200].strip()
                            if preview:
                                # Show first meaningful line only
                                first_line = preview.split("\n")[0][:80]
                                if first_line:
                                    console.print(Text(f"    {first_line}", style="dim"))
                        elif output and status == "error":
                            err_line = output[:120].strip().split("\n")[0]
                            if err_line:
                                console.print(Text(f"    {err_line}", style="red dim"))

                        # Prepare fresh live for next iteration (auto-start thinking spinner)
                        live = Live(Spinner("dots", ""), console=console, refresh_per_second=10, transient=True)
                        live.start()
                        spinner = Spinner("dots", "")
                        spinner.text = Text("Thinking...", style="dim italic")
                        live.update(spinner)

                    elif event.type == "error":
                        if live.is_started:
                            live.stop()
                        _flush_text()
                        console.print(Text.assemble(
                            ("\n✗ Error: ", "red bold"),
                            (event.data.get("message", "unknown"), "red"),
                        ))

                    elif event.type == "guard_warn":
                        reason = event.data.get("reason", "")
                        if live.is_started:
                            live.stop()
                        console.print(Text(f"  ⚠ {reason}", style="yellow dim"))
                        # Re-start spinner for continued execution
                        live = Live(Spinner("dots", ""), console=console, refresh_per_second=10, transient=True)
                        live.start()
                        spinner = Spinner("dots", "")
                        spinner.text = Text("Thinking...", style="dim italic")
                        live.update(spinner)

                    elif event.type == "guard_stop":
                        reason = event.data.get("reason", "")
                        if live.is_started:
                            live.stop()
                        _flush_text()
                        console.print(Text(f"  ■ Loop stopped: {reason}", style="dark_orange"))

                    elif event.type == "compact":
                        if not live.is_started:
                            live.start()
                        spinner = Spinner("dots", "")
                        spinner.text = Text("  Compacting context...", style="yellow dim")
                        live.update(spinner)

                    elif event.type == "debug_iter":
                        # Debug mode: write iteration data to file
                        debug_file = event.data.get("file", "")
                        iteration = event.data.get("iteration", "?")
                        phase = event.data.get("phase", "?")
                        msg_count = event.data.get("message_count", 0)
                        if live.is_started:
                            live.stop()
                        console.print(Text(
                            f"  🔍 [debug] iter={iteration} phase={phase} msgs={msg_count} → {debug_file}",
                            style="magenta dim",
                        ))
                        if not live.is_started:
                            live.start()
                            spinner = Spinner("dots", "")
                            spinner.text = Text("Thinking...", style="dim italic")
                            live.update(spinner)

                    elif event.type == "done":
                        done_data = event.data
                        # Save checkpoint for /steps command
                        if done_data.get("checkpoint"):
                            last_checkpoint = done_data["checkpoint"]
                        if live.is_started:
                            live.stop()

            finally:
                if live.is_started:
                    live.stop()

            # Flush any remaining text
            _flush_text()

            # If no text was produced but tools were called, show a notice
            if not full_text and _tool_count > 0:
                console.print(Text(
                    "  (No text response — model returned only tool calls. Use /history to view tool results.)",
                    style="yellow dim",
                ))

            # Status line
            elapsed = time.monotonic() - start_time
            tokens = done_data.get("tokens", {}) if done_data else {}
            cost = done_data.get("cost", 0.0) if done_data else 0.0
            ctx_info = done_data.get("context", {}) if done_data else {}
            t_in = tokens.get("input", 0)
            t_out = tokens.get("output", 0)
            t_reason = tokens.get("reasoning", 0)
            t_cache_r = tokens.get("cache_read", 0)

            # Context usage for this turn (not cumulative across turns)
            # ctx_info["used"] is the actual context used in this agentic loop
            # iteration (input + output tokens for the final LLM call)
            current_context_used = ctx_info.get("used", t_in + t_out)
            if ctx_info.get("limit", 0) > 0:
                context_limit = ctx_info["limit"]

            # Also track cumulative for stats (but don't use for bar)
            total_tokens_used += t_in + t_out

            # Format: ─ 3.2s · in:1234 out:567 · $0.0012
            stat_parts = [f"{elapsed:.1f}s"]
            if t_in or t_out:
                stat_parts.append(f"in:{_fmt_tokens(t_in)} out:{_fmt_tokens(t_out)}")
            if t_reason:
                stat_parts.append(f"reasoning:{_fmt_tokens(t_reason)}")
            if t_cache_r:
                stat_parts.append(f"cached:{_fmt_tokens(t_cache_r)}")
            if cost > 0:
                stat_parts.append(f"${cost:.4f}")

            console.print(Text(
                f"  ─ {' · '.join(stat_parts)}",
                style="dim",
            ))

            # Context window bar — shows THIS turn's usage, not cumulative
            if context_limit > 0:
                _print_context_bar(console, current_context_used, context_limit)
            console.print()

            # Keep conversation history (use full messages from prompt if available)
            if done_data.get("messages"):
                # Use the complete messages from the agentic loop (includes tool calls/results)
                conversation_history.clear()
                conversation_history.extend(done_data["messages"])
            else:
                # Fallback: simple text-only history
                conversation_history.append({"role": "user", "content": text})
                if full_text:
                    conversation_history.append({"role": "assistant", "content": full_text})

            # --- Per-turn memory updates (non-blocking) ---
            if session_memory.is_enabled:
                import asyncio as _mem_aio
                _text = text
                _full_text = full_text
                _history = list(conversation_history)
                async def _bg_record(
                    _q: str = _text,
                    _a: str = _full_text,
                    _msgs: list = _history,
                ) -> None:
                    with contextlib.suppress(Exception):
                        await session_memory.record_turn(
                            user_query=_q,
                            assistant_response=_a,
                            messages=_msgs,
                            start_time=session_start_time,
                        )
                _mem_aio.ensure_future(_bg_record())

        # --- Session end: save memory note if enabled (with timeout) ---
        if session_memory.is_enabled and conversation_history:
            console.print(Text("  Saving session...", style="dim"))
            try:
                import asyncio as _fin_aio
                note_path = await _fin_aio.wait_for(
                    session_memory.finalize(
                        messages=conversation_history,
                        start_time=session_start_time,
                    ),
                    timeout=5.0,
                )
                if note_path:
                    console.print(Text(f"  ✓ Saved: {note_path.name}", style="green"))
            except TimeoutError:
                console.print(Text("  ⚠ Save timed out (skipped LLM summary)", style="yellow dim"))
            except Exception as e:
                console.print(Text(f"  ✗ Save failed: {e}", style="red dim"))

        # --- Session end: extract long-term memories (best-effort) ---
        if conversation_history and len(conversation_history) >= 4:
            try:
                import asyncio as _ext_aio

                from mycode.session.memory.extractor import extract_memories, save_extracted_memories

                result = await _ext_aio.wait_for(
                    extract_memories(abs_directory, conversation_history),
                    timeout=3.0,
                )
                if result.extracted:
                    saved = save_extracted_memories(abs_directory, result)
                    if saved:
                        console.print(Text(f"  ✓ Extracted {len(saved)} memories", style="green dim"))
            except TimeoutError:
                pass  # Don't block exit
            except Exception:
                pass  # Best effort

        await bus.close()

    await provide(directory, _run_loop, project)


def _print_context_bar(console, used: int, limit: int, bar_width: int = 30) -> None:
    """Print a context window usage bar."""
    from rich.text import Text

    ratio = used / limit if limit > 0 else 0
    # Cap the visual fill at bar_width, but show real percentage
    filled = min(int(bar_width * ratio), bar_width)
    empty = bar_width - filled
    pct = ratio * 100

    # Color gradient based on usage
    if pct < 50:
        bar_color = "green"
    elif pct < 75:
        bar_color = "yellow"
    elif pct < 85:
        bar_color = "dark_orange"
    else:
        bar_color = "red"

    def _fmt(n: int) -> str:
        if n >= 100_000:
            return f"{n // 1000}K"
        if n >= 1000:
            return f"{n / 1000:.1f}K"
        return str(n)

    bar = Text.assemble(
        ("  Context ", "dim"),
        ("▐", "dim"),
        ("█" * filled, bar_color),
        ("░" * empty, "grey23"),
        ("▌", "dim"),
        (f" {_fmt(used)}/{_fmt(limit)} ", "dim"),
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

    console.print(Text(f"  $ {command}", style="dim"))
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
            console.print(Text(f"  exit code: {proc.returncode}", style="red dim"))
    except TimeoutError:
        console.print(Text("  ✗ Command timed out (30s)", style="red"))
    except Exception as e:
        console.print(Text(f"  ✗ {e}", style="red"))
    console.print()


def _handle_command(text: str, history: list, console=None, project_path: str | None = None, **extra) -> str | None:
    """Handle slash commands. Returns 'quit', 'clear', or None.

    extra may contain:
      - last_checkpoint: dict from loop guard checkpoint
    """
    if console is None:
        from rich.console import Console
        console = Console(highlight=False)

    parts_cmd = text.split()
    cmd = parts_cmd[0].lower()

    if cmd in ("/quit", "/exit", "/q"):
        return "quit"

    if cmd in ("/clear", "/reset"):
        console.print("  [yellow]↻ Conversation cleared[/yellow]")
        return "clear"

    if cmd == "/help":
        from rich.table import Table

        def _make_table(title: str, rows: list[tuple[str, str]]) -> Table:
            t = Table(title=title, title_style="bold cyan", title_justify="left",
                      show_header=False, box=None, padding=(0, 2))
            t.add_column(style="bold")
            t.add_column(style="dim")
            for name, desc in rows:
                t.add_row(name, desc)
            return t

        console.print(_make_table("Chat", [
            ("/help", "Show this help"),
            ("/clear", "Clear conversation history"),
            ("/model", "List models or switch: /model <provider/model>"),
            ("/memory", "Show recent session notes"),
            ("/quit", "Exit"),
            ("!<cmd>", "Execute a shell command (shell escape)"),
        ]))
        console.print()
        console.print(_make_table("Inspect", [
            ("/history", "Show conversation turns"),
            ("/history N", "Show full detail for message #N"),
            ("/steps", "Show agentic loop step states from last turn"),
        ]))
        console.print()
        console.print(_make_table("Debug", [
            ("/debug", "Toggle LLM I/O dump to .mycode/debug/<session>/"),
            ("/reload-plugin NAME", "Reload a named plugin without restarting"),
        ]))
        console.print()
        console.print(_make_table("Keyboard", [
            ("Ctrl+J", "Insert newline"),
            ("Ctrl+D", "Exit"),
            ("Ctrl+C", "Abort current response"),
        ]))
        return ""

    if cmd == "/history":
        if not history:
            console.print("  [dim](empty)[/dim]")
            return ""

        # /history N → show detail for message N
        if len(parts_cmd) >= 2:
            try:
                idx = int(parts_cmd[1])
            except ValueError:
                console.print(f"  [red]Invalid index: {parts_cmd[1]}[/red]")
                return ""
            if idx < 0 or idx >= len(history):
                console.print(f"  [red]Index {idx} out of range (0-{len(history)-1})[/red]")
                return ""

            msg = history[idx]
            role = msg.get("role", "?")
            _print_message_detail(console, idx, msg, role)
            return ""

        # /history → overview of all turns
        turn_num = 0
        for i, msg in enumerate(history):
            role = msg.get("role", "?")
            if role == "user":
                turn_num += 1
                content = msg.get("content", "") or ""
                console.print(f"\n  [bold green]Turn {turn_num}[/bold green]")
                console.print(f"  [green][{i}] user:[/green] {content[:100]}")
            elif role == "assistant":
                content = msg.get("content", "") or ""
                tool_calls = msg.get("tool_calls", [])
                preview = content[:100] if content else "(no text)"
                if tool_calls:
                    tool_names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
                    console.print(f"  [dim][{i}] assistant:[/dim] {preview}")
                    console.print(f"       [dim]tools: {', '.join(tool_names)}[/dim]")
                else:
                    console.print(f"  [dim][{i}] assistant:[/dim] {preview}")
            elif role == "tool":
                tool_id = msg.get("tool_call_id", "?")[:12]
                content = msg.get("content", "") or ""
                first_line = content.split("\n")[0][:80] if content else "(empty)"
                console.print(f"  [cyan][{i}] tool ({tool_id}):[/cyan] {first_line}")

        console.print(f"\n  [dim]{len(history)} messages total. Use /history N for detail.[/dim]")
        return ""

    if cmd == "/debug":
        # Toggle debug mode — requires access to debug_mode from outer scope
        debug_ref = extra.get("debug_ref")
        if debug_ref is not None:
            debug_ref[0] = not debug_ref[0]
            state = "ON" if debug_ref[0] else "OFF"
            color = "green" if debug_ref[0] else "dim"
            console.print(f"  [{color}]🔍 Debug mode: {state}[/{color}]")
            if debug_ref[0]:
                console.print("  [dim]Each LLM iteration will dump full messages to .mycode/debug/[/dim]")
        else:
            console.print("  [red]Debug mode not available[/red]")
        return ""

    if cmd == "/steps":
        checkpoint = extra.get("last_checkpoint")
        if not checkpoint or not checkpoint.get("steps"):
            console.print("  [dim](no step data — run a query first)[/dim]")
            return ""

        steps = checkpoint["steps"]
        console.print(f"\n  [bold]Agentic Loop Steps[/bold] ({len(steps)} iterations)")
        console.print(f"  [dim]Cache: {checkpoint.get('cache_stats', {}).get('size', 0)} entries[/dim]")
        console.print()

        for s in steps:
            it = s.get("iteration", "?")
            status = s.get("status", "?")
            duration = s.get("duration", 0)
            text_len = s.get("text_length", 0)
            tools = s.get("tool_calls", [])
            cached = s.get("cached_calls", 0)
            retry = s.get("retry_count", 0)

            # Status icon
            icon = {"completed": "✅", "failed": "❌", "running": "🔶", "pending": "⬜"}.get(status, "•")

            console.print(f"  {icon} Step {it}  [dim]{duration:.1f}s[/dim]", end="")
            if text_len > 0:
                console.print(f"  [green]text:{text_len}[/green]", end="")
            if tools:
                tool_summary = ", ".join(
                    f"{t['tool']}{'✓' if t.get('status') == 'completed' else '✗'}"
                    + (" 📦" if t.get("cached") else "")
                    for t in tools
                )
                console.print(f"  tools:[{tool_summary}]", end="")
            if cached:
                console.print(f"  [cyan]cached:{cached}[/cyan]", end="")
            if retry:
                console.print(f"  [yellow]retries:{retry}[/yellow]", end="")
            if s.get("error"):
                console.print(f"  [red]{s['error'][:60]}[/red]", end="")
            console.print()  # newline

        return ""

    if cmd == "/memory":
        from mycode.session.memory import SessionMemory
        from mycode.session.memory.memdir import scan_memory_files
        from mycode.session.memory.memory import memory_age_text
        if not project_path:
            console.print("  [dim](no project path)[/dim]")
            return ""

        # Section 1: Structured memories (memdir)
        memories = scan_memory_files(project_path)
        if memories:
            console.print(f"  [bold]Structured memories ({len(memories)}):[/bold]")
            for mem in memories:
                age = memory_age_text(mem.mtime_ms) if mem.mtime_ms > 0 else "?"
                desc = mem.description[:60] if mem.description else "(no description)"
                console.print(f"    [{mem.memory_type}] {mem.name} — {desc} [dim]({age})[/dim]")
            console.print()

        # Section 2: Session notes
        memory = SessionMemory(project_path)
        if memory.is_enabled:
            notes = memory.load_recent_sessions(limit=5)
            if notes:
                console.print(f"  [bold]Session notes ({len(notes)}):[/bold]")
                for note in notes:
                    date = note.get("date", "?")
                    duration = note.get("duration_min", 0)
                    topics = ", ".join(note.get("topics", [])) or "general"
                    console.print(f"    {date} ({duration}min) — {topics}")
            else:
                console.print("  [dim](no session notes)[/dim]")
        else:
            console.print("  [dim]Session notes: disabled (enable: sessionMemory.enabled = true)[/dim]")

        if not memories and not (memory.is_enabled and memory.load_recent_sessions(limit=1)):
            console.print("  [dim](no memories found)[/dim]")
        return ""

    if cmd == "/reload-plugin":
        if len(parts_cmd) < 2:
            console.print("  [yellow]Usage: /reload-plugin <module>[/yellow]")
            return ""
        plugin_name = parts_cmd[1]
        try:
            from mycode.plugin.plugin import PluginManager
            mgr = PluginManager._default_instance() if hasattr(PluginManager, "_default_instance") else None
            if mgr is None:
                # Fall back to direct import-reload for projects that do
                # not route plugins through a singleton PluginManager.
                import importlib as _importlib
                _importlib.reload(_importlib.import_module(plugin_name))
                console.print(f"  [green]Reloaded module {plugin_name}[/green]")
                return ""
            # Schedule the coroutine if we are inside the REPL's event loop.
            import asyncio as _asyncio
            info = _asyncio.get_event_loop().run_until_complete(mgr.reload(plugin_name))
            console.print(
                f"  [green]Reloaded plugin {plugin_name}: {info.status}"
                + (f" ({info.error})" if info.error else "") + "[/green]"
            )
        except Exception as exc:
            console.print(f"  [red]Reload failed: {exc}[/red]")
        return ""

    console.print(f"  [red]Unknown command: {cmd}. Type /help[/red]")
    return ""


def _print_message_detail(console, idx: int, msg: dict, role: str) -> None:
    """Print full detail for a single conversation message."""
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.text import Text

    if role == "user":
        content = msg.get("content", "") or ""
        console.print(Panel(
            Markdown(content) if content else Text("(empty)", style="dim"),
            title=f"[green][{idx}] user[/green]",
            border_style="green",
            expand=False,
        ))

    elif role == "assistant":
        content = msg.get("content", "") or ""
        tool_calls = msg.get("tool_calls", [])

        parts = []
        if content:
            parts.append(Markdown(content))

        if tool_calls:
            tc_lines = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "?")
                args = fn.get("arguments", "{}")
                # Pretty-print arguments
                try:
                    import json as _json
                    args_pretty = _json.dumps(_json.loads(args), indent=2, ensure_ascii=False)
                except Exception:
                    args_pretty = args
                tc_lines.append(f"  🔧 {name}({args_pretty[:200]})")
            tool_text = "\n".join(tc_lines)
            console.print(Panel(
                Text.assemble(
                    (content[:500] + "\n\n" if content else ""),
                    ("Tool calls:\n", "bold"),
                    (tool_text, "cyan"),
                ),
                title=f"[dim][{idx}] assistant[/dim]",
                border_style="blue",
                expand=False,
            ))
        else:
            console.print(Panel(
                Markdown(content) if content else Text("(no text output)", style="dim"),
                title=f"[dim][{idx}] assistant[/dim]",
                border_style="blue",
                expand=False,
            ))

    elif role == "tool":
        tool_id = msg.get("tool_call_id", "?")
        content = msg.get("content", "") or ""
        # Show up to 2000 chars of tool output
        display = content[:2000]
        if len(content) > 2000:
            display += f"\n\n... ({len(content)} chars total)"
        console.print(Panel(
            Text(display) if display else Text("(empty)", style="dim"),
            title=f"[cyan][{idx}] tool result ({tool_id[:16]})[/cyan]",
            border_style="cyan",
            expand=False,
        ))

    elif role == "system":
        content = msg.get("content", "") or ""
        console.print(Panel(
            Text(content[:500], style="dim"),
            title=f"[yellow][{idx}] system[/yellow]",
            border_style="yellow",
            expand=False,
        ))


async def _headless(directory: str, model: str | None, agent: str | None, message: str) -> None:
    """Run a single message in headless mode through the full agentic loop."""
    from datetime import datetime

    from mycode.bus.bus import Bus
    from mycode.project.instance import provide
    from mycode.project.project import from_directory
    from mycode.session.memory import SessionMemory
    from mycode.session.prompt import PromptInput, prompt
    from mycode.session.session import create as create_session
    from mycode.tool.registry import register_builtins

    register_builtins()
    project = await from_directory(directory)
    abs_directory = os.path.abspath(directory)
    session_memory = SessionMemory(abs_directory)

    async def _run() -> None:
        session = create_session(title=message[:60])
        session_start_time = datetime.now()
        conversation_history: list[dict] = []

        # Initialize session memory with session ID
        if session_memory.is_enabled:
            session_memory.session_id = session.id

        bus = Bus()
        inp = PromptInput(
            session_id=session.id,
            parts=[{"type": "text", "content": message}],
            model=model,
            agent=agent,
        )
        full_response = ""
        async for event in prompt(inp, bus):
            if event.type == "text_delta":
                content = event.data.get("content", "")
                full_response += content
                click.echo(content, nl=False)
            elif event.type == "tool_start":
                tool_name = event.data.get("tool", "?")
                click.echo(f"\n[tool:{tool_name}] starting", err=True)
            elif event.type == "tool_running":
                tool_name = event.data.get("tool", "?")
                click.echo(f"[tool:{tool_name}] running", err=True)
            elif event.type == "tool_done":
                tool_name = event.data.get("tool", "?")
                status = event.data.get("status", "?")
                tool_input = event.data.get("input", {})
                output = event.data.get("output", "")
                click.echo(f"[tool:{tool_name}] {status}", err=True)
                # Record tool call to session memory
                if session_memory.is_enabled and status == "completed":
                    session_memory.record_tool_call(
                        tool_name=tool_name,
                        tool_input=tool_input if isinstance(tool_input, dict) else {},
                        tool_output=output,
                        status=status,
                    )
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

        # Record turn and save session memory
        if session_memory.is_enabled and conversation_history:
            try:
                await session_memory.record_turn(
                    user_query=message,
                    assistant_response=full_response,
                    messages=conversation_history,
                    start_time=session_start_time,
                )
                note_path = await session_memory.finalize(
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

    from mycode.provider.provider import list_providers

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

    from mycode.provider.provider import list_providers

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
    from mycode.config.config import get as get_config
    cfg = get_config(directory)
    data = cfg.model_dump(exclude_none=True)
    import json
    click.echo(json.dumps(data, indent=2, ensure_ascii=False))


@config.command("path")
def config_path() -> None:
    """Show the global config file path."""
    from mycode.config.paths import global_config_file
    click.echo(str(global_config_file()))


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a global config value (e.g. 'model anthropic/claude-sonnet-4')."""
    import json as jsonmod

    from mycode.config.config import update_global
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

    from mycode.project.instance import provide

    async def _list() -> None:
        from mycode.session.session import list_sessions
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

    from mycode.project.instance import provide

    async def _del() -> None:
        from mycode.session.session import remove
        remove(session_id)
        click.echo(f"Deleted session {session_id}")

    asyncio.run(provide(".", _del))


@session.command("export")
@click.argument("session_id")
@click.option("--output", "-o", type=click.Path(dir_okay=False, writable=True), default=None,
              help="Write to file instead of stdout.")
def session_export_cmd(session_id: str, output: str | None) -> None:
    """Export a session to a JSON archive.

    The archive is plain JSON with ``format: mycode-session-archive``;
    pipe to a file (or pass ``-o path``) and import with
    ``mycode session import``.
    """
    import asyncio

    from mycode.project.instance import provide

    async def _run() -> None:
        from mycode.session.archive import export_session_json
        try:
            payload = export_session_json(session_id)
        except KeyError as exc:
            raise click.ClickException(str(exc)) from exc
        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(payload)
            click.echo(f"Wrote archive → {output}")
        else:
            click.echo(payload)

    asyncio.run(provide(".", _run))


@session.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--keep-id", is_flag=True, help="Preserve original session ID (dangerous if already present).")
@click.option("--title-prefix", default="", help="Prefix to prepend to restored title.")
def session_import_cmd(path: str, keep_id: bool, title_prefix: str) -> None:
    """Import a session archive produced by ``mycode session export``."""
    import asyncio

    from mycode.project.instance import provide

    async def _run() -> None:
        from mycode.session.archive import import_session_json

        with open(path, encoding="utf-8") as f:
            payload = f.read()
        try:
            info = import_session_json(payload, new_id=not keep_id, title_prefix=title_prefix)
        except (ValueError, KeyError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Imported session {info.id[:12]}  ({info.title})")

    asyncio.run(provide(".", _run))


@session.command("fork")
@click.argument("session_id")
@click.option("--turn", "-t", type=int, required=True, help="Assistant turn to fork after (inclusive).")
@click.option("--title", default=None, help="Title for the new forked session.")
def session_fork_cmd(session_id: str, turn: int, title: str | None) -> None:
    """Fork a session at a given assistant turn into a new session."""
    import asyncio

    from mycode.project.instance import provide

    async def _run() -> None:
        from mycode.session.archive import fork_session
        try:
            info = fork_session(session_id, turn, title=title)
        except (KeyError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Forked → {info.id[:12]}  ({info.title})")

    asyncio.run(provide(".", _run))


# --- MCP commands ---

@cli.group()
def mcp() -> None:
    """Manage MCP servers."""


@mcp.command("list")
def mcp_list() -> None:
    """List configured MCP servers."""
    from mycode.config.config import get as get_config
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
@click.option("--mycode-url", default="http://127.0.0.1:4096", help="MyCode HTTP server URL")
@click.option("--directory", "-d", default=".", help="Project directory")
def mcp_serve(mycode_url: str, directory: str) -> None:
    """Start the mycode MCP server (stdio transport).

    This exposes mycode as MCP tools for external AI agents.
    Make sure the mycode HTTP server is running first:

        mycode serve --port 4096
    """
    os.environ["OPENCODE_URL"] = mycode_url
    os.environ["OPENCODE_DIRECTORY"] = os.path.abspath(directory)

    from mycode.mcp_server.server import main
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

    from mycode.project.project import from_directory
    from mycode.snapshot.snapshot import Snapshot

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

    from mycode.project.project import from_directory
    from mycode.snapshot.snapshot import Snapshot

    async def _diff() -> None:
        project = await from_directory(directory)
        snap = Snapshot(project.id, project.worktree)
        diff = await snap.diff(tree_hash)
        if diff:
            click.echo(diff)
        else:
            click.echo("No differences.")

    asyncio.run(_diff())


# --- Orchestration commands ---

@cli.group()
def orchestrate() -> None:
    """Manage multi-agent orchestration flows."""


@orchestrate.command("list")
@click.option("--directory", "-d", default=".", help="Project directory (for project-level flows)")
def orchestrate_list(directory: str) -> None:
    """List available orchestration flows (built-in + global + project)."""
    from mycode.orchestration.registry import get_default_registry

    registry = get_default_registry(project_dir=os.path.abspath(directory), refresh=True)
    flows = registry.list_flows()
    if not flows:
        click.echo("No orchestration flows found.")
        click.echo("  Built-in:  mycode/orchestration/flows/")
        click.echo("  Global:    ~/.mycode/orchestrations/")
        click.echo(f"  Project:   {os.path.abspath(directory)}/.mycode/orchestrations/")
        return

    # Group by source
    by_source: dict[str, list] = {"builtin": [], "global": [], "project": []}
    for f in flows:
        by_source.setdefault(f.source, []).append(f)

    for source in ("builtin", "global", "project"):
        entries = by_source.get(source) or []
        if not entries:
            continue
        click.echo(f"\n[{source}]")
        for f in entries:
            click.echo(f"  {f.name:<30} {f.path}")


@orchestrate.command("inspect")
@click.argument("flow_name")
@click.option("--directory", "-d", default=".", help="Project directory")
@click.option(
    "--vars", "-v", "vars_", multiple=True,
    help="Variable override: key=value (repeatable)",
)
@click.option("--json", "as_json", is_flag=True, help="Emit resolved spec as JSON")
def orchestrate_inspect(flow_name: str, directory: str, vars_: tuple[str, ...], as_json: bool) -> None:
    """Load + validate + pretty-print an orchestration flow."""
    from mycode.orchestration.registry import get_default_registry
    from mycode.orchestration.topology.loader import OrchestrationLoadError
    from mycode.orchestration.topology.validator import OrchestrationValidationError

    overrides: dict[str, str] = {}
    for kv in vars_:
        if "=" not in kv:
            raise click.ClickException(f"--vars expects key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        overrides[k.strip()] = v

    registry = get_default_registry(project_dir=os.path.abspath(directory), refresh=True)
    try:
        spec = registry.load(flow_name, vars_override=overrides)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    except OrchestrationLoadError as exc:
        raise click.ClickException(f"Load error: {exc}") from exc
    except OrchestrationValidationError as exc:
        msg = "Validation failed:\n  - " + "\n  - ".join(exc.issues)
        raise click.ClickException(msg) from exc

    if as_json:
        import json as _json
        click.echo(_json.dumps(spec.model_dump(exclude_none=True), indent=2, ensure_ascii=False))
        return

    _print_spec_tree(spec)


def _print_spec_tree(spec) -> None:
    """Pretty-print an OrchestrationSpec as a tree."""
    from rich.console import Console
    from rich.tree import Tree

    console = Console(highlight=False)
    root = Tree(f"[bold cyan]{spec.name}[/bold cyan] [dim]({spec.mode})[/dim]")
    if spec.description:
        root.add(f"[dim]{spec.description}[/dim]")
    if spec.source_path:
        root.add(f"[dim]source: {spec.source_path}[/dim]")
    if spec.vars:
        vars_node = root.add("[bold]vars[/bold]")
        for k, v in spec.vars.items():
            vars_node.add(f"{k} = {v!r}")

    agents_node = root.add(f"[bold]agents[/bold] ({len(spec.agents)})")
    for a in spec.agents:
        line = f"[green]{a.name}[/green]"
        if a.role:
            line += f" [dim]role={a.role}[/dim]"
        if a.extends:
            line += f" [dim]extends={a.extends}[/dim]"
        node = agents_node.add(line)
        if a.tools:
            node.add(f"tools: {', '.join(a.tools)}")
        if a.isolation != "none":
            node.add(f"isolation: {a.isolation}")
        if a.max_turns:
            node.add(f"max_turns: {a.max_turns}")

    if spec.mode in ("coordinator", "hybrid") and spec.stages:
        stages_node = root.add(f"[bold]stages[/bold] ({len(spec.stages)})")
        for s in spec.stages:
            line = f"[yellow]{s.id}[/yellow]"
            flags = []
            if s.parallel:
                flags.append(f"parallel[{s.max_concurrency}]")
            if s.runs_on:
                flags.append(f"runs_on={s.runs_on}")
            if s.fan_out_from:
                flags.append(f"fan_out_from={s.fan_out_from}")
            if s.depends_on:
                flags.append(f"depends_on={','.join(s.depends_on)}")
            if flags:
                line += " [dim](" + ", ".join(flags) + ")[/dim]"
            node = stages_node.add(line)
            for spawn in s.spawn:
                node.add(f"[blue]→[/blue] {spawn.agent}: {spawn.task[:80]}")

    if spec.mode in ("swarm", "hybrid") and spec.lead:
        root.add(f"[bold]lead[/bold]: [magenta]{spec.lead}[/magenta]")
    if spec.backend:
        root.add(f"[bold]backend[/bold]: prefer={spec.backend.prefer}")

    console.print(root)
