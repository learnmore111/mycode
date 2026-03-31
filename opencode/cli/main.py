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
    from prompt_toolkit.document import Document
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.styles import Style as PtStyle
    from rich.console import Console
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.spinner import Spinner
    from rich.text import Text

    from opencode.bus.bus import Bus
    from opencode.project.instance import provide
    from opencode.project.project import from_directory
    from opencode.provider import provider as providermod
    from opencode.session.memory import SessionMemory
    from opencode.session.prompt import PromptInput, prompt
    from opencode.session.session import create as create_session
    from opencode.tool.registry import register_builtins

    console = Console(highlight=False)
    register_builtins()
    abs_directory = os.path.abspath(directory)
    project = await from_directory(directory)

    # --- Mutable working directory for shell commands ---
    shell_cwd = [abs_directory]  # Use list to allow mutation in nested scope

    # --- Mutable model reference (allows runtime switching via /model) ---
    model_ref = [model]  # Use list to allow mutation in nested scope

    # --- Pre-fetch available models for /model completion ---
    _available_models: list[str] = []
    try:
        provs = await providermod.list_providers()
        for pid, p in provs.items():
            for mid in p.models:
                _available_models.append(f"{pid}/{mid}")
    except Exception:
        pass  # Will be populated lazily if needed

    # --- Welcome (Claude Code style: clean, minimal) ---
    console.print()
    console.print(Text.assemble(
        ("╭ ", "dim"),
        ("OpenCode", "bold"),
        (f" v{__version__}", "dim"),
    ))
    console.print(Text.assemble(
        ("│ ", "dim"),
        ("model: ", "dim"),
        (model_ref[0] or "default", ""),
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

    # --- Completer: slash commands, !shell commands, @file paths ---
    _slash_commands = {
        "/help": "Show available commands",
        "/clear": "Clear conversation history",
        "/reset": "Clear conversation history",
        "/model": "Switch model (/model <provider/model>)",
        "/history": "Show conversation turns",
        "/memory": "Show recent session notes",
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
                if len(parts) >= 2:
                    fragment = parts[1]
                else:
                    fragment = ""
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
                            if "/" in fragment:
                                rel = str(Path(fragment).parent / name)
                            else:
                                rel = name
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
                    if "/" in fragment:
                        rel_path = str(Path(fragment).parent / name)
                    else:
                        rel_path = name
                    if entry.is_dir():
                        rel_path += "/"
                    yield Completion(
                        rel_path, start_position=-len(fragment),
                        display=f"{name}/" if entry.is_dir() else name,
                    )
            except OSError:
                pass

    completer = merge_completers([_SlashCompleter(), _ShellCompleter(), _FileMentionCompleter()])

    # --- Prompt setup (Claude Code style: clean ❯ prompt) ---
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

        while True:
            # Claude Code style prompt: simple ❯
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

                handled = _handle_command(text, conversation_history, console, abs_directory, last_checkpoint=last_checkpoint)
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

            # --- Stream AI response (Claude Code style) ---
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
                """Generate Claude Code style tool label: verb + target."""
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
                async for event in prompt(inp, bus, history=conversation_history):
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

                        # Claude Code style: ✓/✗ + tool label
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

                        # Show brief output preview (Claude Code shows compact result)
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

            # Status line (Claude Code style: compact single line)
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

            # Context window bar
            if context_limit > 0:
                _print_context_bar(console, total_tokens_used, context_limit)
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
                async def _bg_record():
                    try:
                        await session_memory.record_turn(
                            user_query=text,
                            assistant_response=full_text,
                            messages=conversation_history,
                            start_time=session_start_time,
                        )
                    except Exception:
                        pass
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

        await bus.close()

    await provide(directory, _run_loop, project)


def _print_context_bar(console, used: int, limit: int, bar_width: int = 30) -> None:
    """Print a context window usage bar (Claude Code style)."""
    from rich.text import Text

    ratio = min(used / limit, 1.0) if limit > 0 else 0
    filled = int(bar_width * ratio)
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
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="bold")
        table.add_column(style="dim")
        table.add_row("/help", "Show this help")
        table.add_row("/clear", "Clear conversation history")
        table.add_row("/model", "List models or switch: /model <provider/model>")
        table.add_row("/history", "Show conversation turns")
        table.add_row("/history N", "Show full detail for message #N")
        table.add_row("/steps", "Show agentic loop step states from last turn")
        table.add_row("/memory", "Show recent session notes")
        table.add_row("/quit", "Exit")
        table.add_row("!<cmd>", "Execute a shell command")
        table.add_row("", "")
        table.add_row("Ctrl+J", "Insert newline")
        table.add_row("Ctrl+D", "Exit")
        console.print(table)
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
        from opencode.session.memory import SessionMemory
        if not project_path:
            console.print("  [dim](no project path)[/dim]")
            return ""
        memory = SessionMemory(project_path)
        if not memory.is_enabled:
            console.print("  [yellow]Session memory is disabled.[/yellow]")
            console.print("  [dim]Enable: sessionMemory.enabled = true[/dim]")
            return ""
        notes = memory.load_recent_sessions(limit=5)
        if not notes:
            console.print("  [dim](no session notes found)[/dim]")
        else:
            console.print(f"  [bold]Session notes ({len(notes)}):[/bold]")
            for note in notes:
                date = note.get("date", "?")
                duration = note.get("duration_min", 0)
                topics = ", ".join(note.get("topics", [])) or "general"
                console.print(f"    {date} ({duration}min) — {topics}")
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

    from opencode.bus.bus import Bus
    from opencode.project.instance import provide
    from opencode.project.project import from_directory
    from opencode.session.memory import SessionMemory
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
