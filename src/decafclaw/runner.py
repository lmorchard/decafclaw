"""Top-level orchestrator — manages all subsystems as parallel asyncio tasks."""

import asyncio
import logging
import signal

log = logging.getLogger(__name__)


async def _cancel_task(task, name="task"):
    """Cancel a task and wait for it to finish."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    log.debug(f"Stopped {name}")


async def run_all(app_ctx):
    """Run all subsystems: MCP, HTTP server, Mattermost, heartbeat.

    This is the main entry point for server mode (Mattermost and/or HTTP).
    Subsystems are started as parallel asyncio tasks and shut down gracefully
    on SIGTERM/SIGINT.
    """
    from .heartbeat import parse_interval, run_heartbeat_timer
    from .mcp_client import init_mcp, shutdown_mcp

    config = app_ctx.config

    # Graceful shutdown support
    shutdown_event = asyncio.Event()

    def _signal_handler():
        log.info("Shutdown signal received, finishing in-flight turns...")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    # Init MCP servers (shared across all subsystems)
    await init_mcp(config)

    http_task = None
    mattermost_task = None
    heartbeat_task = None

    try:
        # Start HTTP server (button callbacks + web gateway)
        if config.http_enabled:
            from .http_server import run_http_server
            http_task = asyncio.create_task(
                run_http_server(config, app_ctx.event_bus, app_ctx=app_ctx)
            )
            log.info(f"HTTP server enabled on {config.http_host}:{config.http_port}")

        # Start Mattermost client
        if config.mattermost_url and config.mattermost_token:
            from .mattermost import MattermostClient
            client = MattermostClient(config)
            mattermost_task = asyncio.create_task(
                client.run(app_ctx, shutdown_event)
            )
            log.info("Mattermost client starting")

        # Start heartbeat timer
        if parse_interval(config.heartbeat_interval) is not None:
            # Use Mattermost heartbeat cycle if available, otherwise basic
            if config.mattermost_url and config.mattermost_token:
                from .tools.heartbeat_tools import _guarded_heartbeat

                async def on_cycle():
                    await _guarded_heartbeat(config, app_ctx.event_bus)

                heartbeat_task = asyncio.create_task(
                    run_heartbeat_timer(
                        config, app_ctx.event_bus, shutdown_event,
                        on_cycle=on_cycle,
                    )
                )
            else:
                heartbeat_task = asyncio.create_task(
                    run_heartbeat_timer(
                        config, app_ctx.event_bus, shutdown_event,
                    )
                )
            has_channel = config.heartbeat_channel or config.heartbeat_user
            log.info(f"Heartbeat timer started (reporting={'enabled' if has_channel else 'silent'})")
        else:
            log.info("Heartbeat disabled (interval not set)")

        # Wait for shutdown
        await shutdown_event.wait()

    finally:
        log.info("Shutting down...")

        # Stop subsystems in reverse order
        await _cancel_task(heartbeat_task, "heartbeat")
        await _cancel_task(http_task, "HTTP server")

        # Mattermost handles its own in-flight task cleanup
        if mattermost_task:
            # Signal shutdown and wait for it to finish
            shutdown_event.set()
            try:
                await asyncio.wait_for(mattermost_task, timeout=15)
            except asyncio.TimeoutError:
                log.warning("Mattermost shutdown timed out, cancelling")
                await _cancel_task(mattermost_task, "Mattermost")
            except asyncio.CancelledError:
                pass

        await shutdown_mcp()
        log.info("Shutdown complete")
