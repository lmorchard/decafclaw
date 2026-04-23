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
    schedule_task = None

    try:
        # Create conversation manager (shared across web + future transports)
        from .conversation_manager import ConversationManager
        manager = ConversationManager(config, app_ctx.event_bus)
        await manager.startup_scan()

        # Start HTTP server (button callbacks + web gateway)
        if config.http.enabled:
            from .http_server import run_http_server
            http_task = asyncio.create_task(
                run_http_server(config, app_ctx.event_bus, app_ctx=app_ctx,
                                manager=manager)
            )
            log.info(f"HTTP server enabled on {config.http.host}:{config.http.port}")

        # Start Mattermost client
        mm_client = None
        if config.mattermost.url and config.mattermost.token:
            from .mattermost import MattermostClient
            mm_client = MattermostClient(config)
            mattermost_task = asyncio.create_task(
                mm_client.run(app_ctx, shutdown_event, manager=manager)
            )
            log.info("Mattermost client starting")

        # Wire notification channel adapters. Each adapter subscribes to
        # the event bus for `notification_created` events. Skip any
        # adapter whose config is incomplete or whose transport isn't
        # running — this is startup-time only; no errors at notify() time.
        mm_dm_cfg = config.notifications.channels.mattermost_dm
        if mm_dm_cfg.enabled and mm_dm_cfg.recipient_username and mm_client:
            from .notification_channels.mattermost_dm import (
                make_mattermost_dm_adapter,
            )
            adapter = make_mattermost_dm_adapter(config, mm_client)
            app_ctx.event_bus.subscribe(adapter)
            log.info(
                "Notifications: Mattermost DM adapter subscribed "
                "(recipient=%s, min_priority=%s)",
                mm_dm_cfg.recipient_username, mm_dm_cfg.min_priority,
            )

        # Start heartbeat timer
        if parse_interval(config.heartbeat.interval) is not None:
            # Use Mattermost heartbeat cycle if available, otherwise basic
            if config.mattermost.url and config.mattermost.token:
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
            has_channel = config.heartbeat.channel or config.heartbeat.user
            log.info(f"Heartbeat timer started (reporting={'enabled' if has_channel else 'silent'})")
        else:
            log.info("Heartbeat disabled (interval not set)")

        # Start schedule timer
        from .schedules import run_schedule_timer
        schedule_task = asyncio.create_task(
            run_schedule_timer(config, app_ctx.event_bus, shutdown_event)
        )
        log.info("Schedule timer started")

        # Wait for shutdown
        await shutdown_event.wait()

    finally:
        log.info("Shutting down...")

        # Stop subsystems in reverse order
        await _cancel_task(schedule_task, "schedule timer")
        await _cancel_task(heartbeat_task, "heartbeat")

        # Graceful HTTP server shutdown (avoids uvicorn CancelledError tracebacks)
        if http_task:
            from .http_server import shutdown_http_server
            await shutdown_http_server()
            try:
                await asyncio.wait_for(http_task, timeout=5)
            except asyncio.TimeoutError:
                log.warning("HTTP server shutdown timed out, cancelling")
                await _cancel_task(http_task, "HTTP server")
            except asyncio.CancelledError:
                pass

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

        # Wait for in-flight agent turns managed by the conversation manager
        await manager.shutdown()

        await shutdown_mcp()
        log.info("Shutdown complete")
