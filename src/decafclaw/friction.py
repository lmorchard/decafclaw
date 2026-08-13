import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from decafclaw.archive import read_archive
from decafclaw.conversation_paths import iter_conversation_archives
from decafclaw.workflow.llm import call_structured

log = logging.getLogger(__name__)

@dataclass
class FrictionTheme:
    theme: str
    proposed_addition: str
    occurrences: int


async def analyze_friction(config_or_ctx) -> list[FrictionTheme]:
    from decafclaw.context import Context
    from decafclaw.events import EventBus

    config = getattr(config_or_ctx, "config", config_or_ctx)
    ctx = config_or_ctx if hasattr(config_or_ctx, "config") else Context(config=config, event_bus=EventBus())

    # 1. Collect recent user messages across all archives
    messages = []

    # Heuristic words
    heuristics = ["no", "stop", "i told you", "don't", "please stick", "always use"]

    for conv_id, archive_path in iter_conversation_archives(config):
        msgs = read_archive(config, conv_id)
        for msg in msgs:
            if msg.get("role") == "user":
                content = msg.get("content", "").lower()
                if any(h in content for h in heuristics):
                    messages.append(msg.get("content", ""))

    if not messages:
        return []

    # 2. Ask LLM to extract themes
    schema = {
        "type": "object",
        "properties": {
            "themes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "theme": {"type": "string"},
                        "proposed_addition": {"type": "string"},
                        "occurrences": {"type": "integer"}
                    },
                    "required": ["theme", "proposed_addition", "occurrences"]
                }
            }
        },
        "required": ["themes"]
    }

    system = "You analyze user messages to extract recurring corrections or friction points."

    # Combine messages into a single prompt payload
    lines = [f"- {m}" for m in messages]
    prompt_text = "Here are user correction messages:\n" + "\n".join(lines) + "\n\nGroup them by theme and propose AGENT.md additions for each theme. Occurrences should count how many times this theme appeared."

    try:
        res = await call_structured(
            ctx,
            system=system,
            user_msg=prompt_text,
            schema=schema,
            tool_name="submit_themes",
            model="gemini-2.5-flash"  # Default fast model
        )

        extracted = res.get("themes", [])
        return [FrictionTheme(**t) for t in extracted]
    except Exception as e:
        log.error(f"Friction analysis failed: {e}")
        return []

