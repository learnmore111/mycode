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
    """Run a single message in headless mode."""
    # Will be implemented in Phase 3
    click.echo(f"[headless] Processing: {message}")
    click.echo("[headless] Not yet implemented — completing Phase 3 first.")


@cli.command()
def providers() -> None:
    """List available AI providers and models."""
    click.echo("Provider listing not yet implemented.")


@cli.command()
def models() -> None:
    """List available models."""
    click.echo("Model listing not yet implemented.")
