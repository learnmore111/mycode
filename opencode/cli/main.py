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
        click.echo("Interactive TUI mode not yet implemented. Use --message for headless mode.")


async def _headless(directory: str, model: str | None, agent: str | None, message: str) -> None:
    """Run a single message in headless mode through the full agentic loop."""
    from opencode.bus.bus import Bus
    from opencode.project.instance import provide
    from opencode.project.project import from_directory
    from opencode.session.session import create as create_session
    from opencode.session.prompt import PromptInput, prompt
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
            for mid, m in p.models.items():
                click.echo(f"  {pid}/{mid}")

    asyncio.run(_list())
