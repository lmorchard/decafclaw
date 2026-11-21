"""Aux shell-approval judge prompt: bundled template + per-agent override.

The prompt is policy, not plumbing, so it lives in markdown instead of a Python
string. The bundled ``prompts/SHELL_APPROVAL.md`` is the default; an agent can
override it at ``data/{agent_id}/SHELL_APPROVAL.md`` (admin-managed, not
workspace-writable) and the override is read on each judge call, so edits apply
without a restart.

The renderer is single-pass: a command or purpose that happens to contain a
placeholder name is injected literally and never expanded again.
"""

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decafclaw.activity import Activity

log = logging.getLogger(__name__)

TEMPLATE_FILENAME = "SHELL_APPROVAL.md"
_BUNDLED_TEMPLATE_PATH = Path(__file__).parent / "prompts" / TEMPLATE_FILENAME
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_]+)\}\}")

# A judge decline needs one of these named risks (#1641). A style preference
# such as "a native tool would fit better" is advisory, never a decline.
AUX_RISK_CATEGORIES = frozenset(
    {"data_loss", "exfiltration", "privilege_escalation", "outside_workspace", "irreversible"}
)


def template_path(config) -> Path:
    """Per-agent prompt override path."""
    return config.agent_path / TEMPLATE_FILENAME


def load_template(config) -> str:
    """Read the agent override, falling back to the bundled template.

    A broken override does not disable the judge: the bundled prompt is used
    and the failure is logged.
    """
    override = template_path(config)
    if override.exists():
        try:
            return override.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("shell approval prompt override unreadable (%s): %s", override, exc)
    return _BUNDLED_TEMPLATE_PATH.read_text(encoding="utf-8")


def activity_direction(activity: "Activity | None") -> str:
    """One line telling the judge what direction the conversation is in."""
    if activity is None:
        return "Conversation Activity: (not derived)"
    state = "established earlier in this conversation" if activity.established else "new to this conversation"
    return f"Conversation Activity: {activity.domain} ({state})"


def policy_block(guidelines: list[str]) -> str:
    """The default policy, widened by active guidance when present."""
    if not guidelines:
        return (
            "Policy: auto-approve unless one of the five risk categories above applies. "
            "Do not decline merely because the command changes state, uses the network, or repeats work a "
            "native tool could do."
        )
    formatted = "\n".join(f"- {guideline}" for guideline in guidelines)
    return (
        "Policy: auto-approve unless one of the five risk categories above applies. "
        "The additional guidelines below narrow or widen ordinary work inside this activity; they do not "
        "override a concrete risk category.\n\n"
        "Additional Approval Guidelines:\n"
        f"{formatted}\n\n"
        "You may auto-approve commands that match these guidelines, provided they remain within the stated bounds "
        "and do not pose unexpected risk or escape the workspace."
    )


def render_aux_approval_prompt(
    config,
    *,
    command: str,
    working_dir: str,
    stated_purpose: str,
    activity: "Activity | None",
    native_tool_index: str,
    guidelines: list[str],
) -> str:
    """Fill the judge template in one pass."""
    values = {
        "COMMAND": command,
        "WORKING_DIR": working_dir,
        "STATED_PURPOSE": stated_purpose,
        "DIRECTION": activity_direction(activity),
        "NATIVE_TOOL_INDEX": native_tool_index,
        "POLICY_BLOCK": policy_block(guidelines),
    }
    template = load_template(config)
    return _PLACEHOLDER_RE.sub(lambda match: values.get(match.group(1), match.group(0)), template)
