"""Model selection — user-only, not agent-callable.

Called directly by the websocket handler for user-initiated model changes.
Not registered in the agent's tool registry (cost control — see #245).
"""

import logging

from ..media import ToolResult

log = logging.getLogger(__name__)


async def tool_set_model(ctx, model: str) -> str | ToolResult:
    """Change the active model for this conversation."""
    log.info("[tool:set_model] model=%s", model)

    config = ctx.config
    if model not in config.model_configs:
        available = sorted(config.model_configs.keys())
        return ToolResult(
            text=f"[error: unknown model '{model}'. "
                 f"Available: {', '.join(available) or '(none configured)'}]"
        )

    mc = config.model_configs[model]
    if mc.provider not in config.providers:
        return ToolResult(
            text=f"[error: model '{model}' references unknown provider "
                 f"'{mc.provider}']"
        )

    ctx.active_model = model

    # Record model change in conversation archive
    from ..archive import append_message

    conv_id = ctx.conv_id or ctx.channel_id
    if conv_id:
        append_message(ctx.config, conv_id, {
            "role": "model", "content": model,
        })

    pc = config.providers[mc.provider]
    return (
        f"Model switched to **{model}** "
        f"(provider: {pc.type}, model: {mc.model}). "
        f"This applies for the rest of this conversation."
    )
