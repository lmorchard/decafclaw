"""DecafClaw — a minimal AI agent for learning."""

import asyncio
import logging
import sys

from .config import load_config


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    config = load_config()

    # If Mattermost is configured, run as a bot. Otherwise, interactive mode.
    if config.mattermost_url and config.mattermost_token:
        from .mattermost import MattermostClient
        from .agent import run_agent_turn

        asyncio.run(_run_mattermost(config))
    else:
        from .agent import run_interactive

        run_interactive(config)


async def _run_mattermost(config):
    from .mattermost import MattermostClient
    from .agent import run_agent_turn

    client = MattermostClient(config)
    await client.connect()

    # Per-channel conversation history (simple dict, no persistence)
    histories = {}

    def on_message(msg):
        channel_id = msg["channel_id"]
        text = msg["text"]
        root_id = msg["root_id"] or msg["post_id"]

        logging.info(f"Message from {msg['sender_name']}: {text[:50]}")

        # Get or create history for this channel
        if channel_id not in histories:
            histories[channel_id] = []
        history = histories[channel_id]

        # Run the agent
        response = run_agent_turn(config, text, history)

        # Send response (fire-and-forget via event loop)
        asyncio.get_event_loop().create_task(
            client.send(channel_id, response, root_id=root_id)
        )

    await client.listen(on_message)
