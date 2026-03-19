"""Heartbeat management tools — trigger heartbeat manually."""

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

# Prevent concurrent heartbeat runs
_heartbeat_lock = asyncio.Lock()


async def tool_heartbeat_trigger(ctx) -> str:
    """Manually trigger a heartbeat cycle, posting results to the configured channel."""
    log.info("[tool:heartbeat_trigger]")

    if _heartbeat_lock.locked():
        return "Heartbeat is already running."

    from ..heartbeat import load_heartbeat_sections

    sections = load_heartbeat_sections(ctx.config)
    if not sections:
        return "No HEARTBEAT.md sections found. Nothing to run."

    # Fire and forget — run the cycle in the background
    asyncio.create_task(_guarded_heartbeat(ctx.config, ctx.event_bus))

    has_channel = ctx.config.heartbeat.channel or ctx.config.heartbeat.user
    if has_channel:
        return f"Heartbeat triggered: {len(sections)} section(s) queued. Results will be posted to the heartbeat channel."
    return f"Heartbeat triggered: {len(sections)} section(s) queued. No reporting channel configured — running silently."


async def _guarded_heartbeat(config, event_bus) -> None:
    """Run heartbeat with concurrency guard. Lock auto-releases on crash."""
    if _heartbeat_lock.locked():
        log.warning("Heartbeat already running, skipping")
        return
    async with _heartbeat_lock:
        await _run_heartbeat_to_channel(config, event_bus)


async def _run_heartbeat_to_channel(config, event_bus) -> None:
    """Run heartbeat sections, optionally posting results to a channel."""
    from datetime import datetime

    from ..heartbeat import (
        load_heartbeat_sections,
        run_section_turn,
    )

    sections = load_heartbeat_sections(config)
    if not sections:
        return

    # Set up reporting (optional — heartbeat runs even without a channel)
    http = _make_http_client(config)
    channel_id = None
    marker_id = None
    section_post_ids = {}

    if http:
        try:
            channel_id = await _resolve_channel(http, config)
        except Exception as e:
            log.error(f"Failed to resolve heartbeat channel: {e}")

    suppress_ok = config.heartbeat.suppress_ok
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Post marker and placeholders if we have a channel
    if http and channel_id:
        try:
            marker_resp = await http.post("/posts", json={
                "channel_id": channel_id,
                "message": f"\u2764\ufe0f Heartbeat \u2014 {timestamp_str} ({len(sections)} section(s))",
            })
            marker_resp.raise_for_status()
            marker_id = marker_resp.json().get("id")

            for i, section in enumerate(sections):
                title = section["title"]
                try:
                    resp = await http.post("/posts", json={
                        "channel_id": channel_id,
                        "message": f"\u23f3 **{title}** \u2014 running...",
                        "root_id": marker_id,
                    })
                    resp.raise_for_status()
                    section_post_ids[i] = resp.json().get("id")
                except Exception as e:
                    log.error(f"Failed to pre-post heartbeat section '{title}': {e}")
        except Exception as e:
            log.error(f"Failed to post heartbeat marker: {e}")

    # Run all sections concurrently
    section_results = []

    async def run_section(i, section):
        title = section["title"]
        post_id = section_post_ids.get(i)

        turn_result = await run_section_turn(
            config, event_bus, section, timestamp, i,
        )
        response = turn_result["response"]
        ok = turn_result["is_ok"]

        section_results.append(ok)

        # Update placeholder if we have one
        if http and post_id:
            try:
                if ok and suppress_ok:
                    await http.put(f"/posts/{post_id}/patch", json={
                        "id": post_id,
                        "message": f"\u2705 **{title}** \u2014 OK",
                    })
                else:
                    await http.put(f"/posts/{post_id}/patch", json={
                        "id": post_id,
                        "message": f"**{title}**\n\n{response}",
                    })
            except Exception as e:
                log.debug(f"Failed to update heartbeat section post: {e}")

    try:
        await asyncio.gather(*[
            run_section(i, section) for i, section in enumerate(sections)
        ])

        all_ok = all(section_results) if section_results else True

        # Update marker to show completion status
        if http and marker_id:
            status = "all OK" if all_ok else "done"
            try:
                await http.put(f"/posts/{marker_id}/patch", json={
                    "id": marker_id,
                    "message": f"\u2764\ufe0f Heartbeat \u2014 {timestamp_str} \u2014 {status}",
                })
            except Exception as e:
                log.debug(f"Failed to update heartbeat marker: {e}")

    except Exception as e:
        log.error(f"Heartbeat cycle failed: {e}", exc_info=True)
    finally:
        if http:
            await http.aclose()


def _make_http_client(config) -> httpx.AsyncClient | None:
    """Create an HTTP client for Mattermost posting. Returns None if not configured."""
    if not config.mattermost.url or not config.mattermost.token:
        return None

    base_url = config.mattermost.url.rstrip("/") + "/api/v4"
    headers = {
        "Authorization": f"Bearer {config.mattermost.token}",
        "Content-Type": "application/json",
    }
    return httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30)


async def _resolve_channel(http, config) -> str | None:
    """Resolve the heartbeat channel ID from config. Returns None if not configured."""
    if config.heartbeat.channel:
        return config.heartbeat.channel

    if config.heartbeat.user:
        me_resp = await http.get("/users/me")
        me_resp.raise_for_status()
        bot_user_id = me_resp.json()["id"]

        dm_resp = await http.post("/channels/direct",
                                 json=[bot_user_id, config.heartbeat.user])
        dm_resp.raise_for_status()
        return dm_resp.json()["id"]

    return None


HEARTBEAT_TOOLS = {
    "heartbeat_trigger": tool_heartbeat_trigger,
}

HEARTBEAT_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "heartbeat_trigger",
            "description": (
                "Manually trigger a heartbeat cycle right now, without waiting for "
                "the next scheduled tick. Reads HEARTBEAT.md and runs all sections. "
                "Posts results to the configured heartbeat channel if one is set. "
                "Use when asked to 'run heartbeat', 'check heartbeat tasks', or "
                "'trigger heartbeat'."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
