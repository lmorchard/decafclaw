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

    # Wire telemetry subscribers (measurement only, fail-open) early so
    # they capture startup events (e.g. MCP connections).
    if config.audit_log.enabled:
        from .audit_log import make_audit_log_subscriber
        app_ctx.event_bus.subscribe(make_audit_log_subscriber(config))
        log.info("Audit log subscriber active (%s)",
                 config.audit_log.path)
    if config.telemetry.tool_usage_enabled:
        from .tool_telemetry import make_tool_telemetry_subscriber
        app_ctx.event_bus.subscribe(make_tool_telemetry_subscriber(config))
        log.info("Telemetry: tool-usage subscriber active (%s)",
                 config.telemetry.tool_usage_path)
    if config.telemetry.reflection_metrics_enabled:
        from .reflection_metrics import make_reflection_metrics_subscriber
        app_ctx.event_bus.subscribe(make_reflection_metrics_subscriber(config))
        log.info("Telemetry: reflection-metrics subscriber active (%s)",
                 config.telemetry.reflection_metrics_path)
    if config.telemetry.loop_breaker_enabled:
        from .loop_breaker_telemetry import make_loop_breaker_subscriber
        app_ctx.event_bus.subscribe(make_loop_breaker_subscriber(config))
        log.info("Telemetry: loop-breaker subscriber active (%s)",
                 config.telemetry.loop_breaker_path)
    if config.telemetry.retrieval_enabled:
        from .retrieval_telemetry import make_retrieval_telemetry_subscriber
        app_ctx.event_bus.subscribe(make_retrieval_telemetry_subscriber(config))
        log.info("Telemetry: retrieval subscriber active (%s)",
                 config.telemetry.retrieval_path)

    from .metrics import make_metrics_subscriber
    app_ctx.event_bus.subscribe(make_metrics_subscriber(config))
    log.info("Metrics: metrics subscriber active")

    # Init MCP servers (shared across all subsystems)
    await init_mcp(config, event_bus=app_ctx.event_bus)

    http_task = None
    mattermost_task = None
    heartbeat_task = None
    schedule_task = None

    try:
        # Create conversation manager (shared across web + future transports)
        from .conversation_manager import ConversationManager
        from .widget_input import register_widget_handler
        manager = ConversationManager(config, app_ctx.event_bus)
        register_widget_handler(manager.confirmation_registry)
        await manager.startup_scan()
        await manager.startup_scan_workflows()

        # Start workspace index background refresh loop (server startup refresh)
        from .workspace_index import start_workspace_index_loop
        start_workspace_index_loop(config)

        # Start HTTP server (button callbacks + web gateway)
        if config.http.enabled:
            from .http_server import run_http_server
            http_task = asyncio.create_task(
                run_http_server(config, app_ctx.event_bus, app_ctx=app_ctx,
                                manager=manager)
            )
            log.info(f"HTTP server enabled on {config.http.host}:{config.http.port}")

        # Start Mattermost client (skipped when disabled — lets the web gateway
        # run standalone without connecting to Mattermost, e.g. for the decafclaw client)
        mm_active = bool(
            config.mattermost.enabled
            and config.mattermost.url
            and config.mattermost.token
        )
        mm_client = None
        if mm_active:
            from .mattermost import MattermostClient
            mm_client = MattermostClient(config)
            mattermost_task = asyncio.create_task(
                mm_client.run(app_ctx, shutdown_event, manager=manager)
            )
            log.info("Mattermost client starting")

        # Wire notification channel adapters. Each adapter subscribes to
        # the event bus for `notification_created` events. Per-channel
        # guards + subscribe calls live in the notification_channels
        # package so adding a new channel doesn't touch this file.
        from .notification_channels import init_notification_channels
        init_notification_channels(
            config, app_ctx.event_bus,
            mm_client=mm_client,
        )

        # Wire the persistent backlink index (#197 Phase 4): incrementally
        # updates {workspace}/backlinks.json on every vault_changed event
        # instead of brute-force rescanning the vault on each query.
        # Fail-open — never propagates into the publishing turn.
        from .backlinks import make_backlinks_subscriber
        app_ctx.event_bus.subscribe(make_backlinks_subscriber(config))

        from .workspace_index import make_workspace_index_subscriber
        app_ctx.event_bus.subscribe(make_workspace_index_subscriber(config))

        # Start heartbeat timer
        if parse_interval(config.heartbeat.interval) is not None:
            # Use Mattermost heartbeat cycle if available, otherwise basic
            if mm_active:
                from .tools.heartbeat_tools import _guarded_heartbeat

                async def on_cycle():
                    await _guarded_heartbeat(config, app_ctx.event_bus, manager)

                heartbeat_task = asyncio.create_task(
                    run_heartbeat_timer(
                        config, app_ctx.event_bus, manager, shutdown_event,
                        on_cycle=on_cycle,
                    )
                )
            else:
                heartbeat_task = asyncio.create_task(
                    run_heartbeat_timer(
                        config, app_ctx.event_bus, manager, shutdown_event,
                    )
                )
            has_channel = config.heartbeat.channel or config.heartbeat.user
            log.info(f"Heartbeat timer started (reporting={'enabled' if has_channel else 'silent'})")
        else:
            log.info("Heartbeat disabled (interval not set)")

        # Start schedule timer
        from .schedules import run_schedule_timer
        schedule_task = asyncio.create_task(
            run_schedule_timer(config, app_ctx.event_bus, manager, shutdown_event)
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
