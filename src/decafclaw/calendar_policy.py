"""Tool policy and model pinning for calendar wake turns (#1971, #1972).

A schedule declares its policy in SCHEDULE.md frontmatter and
``schedules.setup_schedule_ctx`` applies it. Calendar events have no
frontmatter, so the same policy travels on the ``CalendarEvent`` itself and is
applied here by the wake's ``context_setup``.

The trust rule is the load-bearing part. ``schedules.py`` states it:

    Frontmatter always RESTRICTS which tools are visible. It only GRANTS
    pre-approval at a human-controlled tier — an agent-writable schedule must
    not be able to approve its own shell commands (#731).

Calendar has no ``task.source`` to consult, so provenance stands in for it.
The authenticated web route is the human control surface and may set
``policy_granted``; the agent tool may not. Without the flag an event can take
capabilities away and nothing more.

Skill bodies arrive by a different route than tools. ``required_skills``
activates here, but the *body* is delivered by
``context_composer.render_activated_skills`` as the transient
``<activated_skills>`` block — not by this module. See the note in
``activate_required_skills`` for why this differs from the schedule path, which
builds its own ``<loaded_skills>`` block into the prompt instead (#2006).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .calendar_events import CalendarEvent
    from .context import Context

log = logging.getLogger(__name__)

#: Keep the deferred-tool escape hatches reachable even under a tight
#: ``allowed_tools`` list, so an under-specified event can still recover via
#: tool_search / activate_skill. Neither grants a capability on its own.
#: Matches ``schedules._SCHEDULE_ESCAPE_HATCH_TOOLS``.
_ESCAPE_HATCH_TOOLS = frozenset({"tool_search", "activate_skill"})

#: ``shell(pattern)`` entries are the only preapproval form an event may carry.
_SHELL_ENTRY_RE = re.compile(r"^shell\((.+)\)$")


def split_allowed_tools(entries: list[str]) -> tuple[list[str], list[str]]:
    """Split ``["shell(git *)", "vault_read"]`` into tool names and shell patterns.

    ``shell(...)`` is only meaningful with preapproval, so it is validated and
    stripped separately rather than left in the visible-tool list.
    """
    tools: list[str] = []
    patterns: list[str] = []
    for raw in entries:
        entry = str(raw or "").strip()
        if not entry:
            continue
        match = _SHELL_ENTRY_RE.match(entry)
        if match:
            pattern = match.group(1).strip()
            if pattern:
                patterns.append(pattern)
            continue
        tools.append(entry)
    return tools, patterns


def validate_tool_names(entries: list[str], *, field_name: str) -> list[str]:
    """Strip, drop blanks, and reject ``shell(...)`` in a plain tool-name list."""
    cleaned: list[str] = []
    for raw in entries or []:
        entry = str(raw or "").strip()
        if not entry:
            continue
        if _SHELL_ENTRY_RE.match(entry):
            raise ValueError(
                f"{field_name} takes tool names, not shell(...) patterns; use allowed_tools for shell preapproval"
            )
        cleaned.append(entry)
    return cleaned


async def activate_required_skills(ctx: "Context", event: "CalendarEvent") -> None:
    """Pre-activate an event's ``required_skills`` behind the capability gate.

    Activation imports and execs a skill's ``tools.py``, so ``required_skills``
    is a code-execution grant (#731). ``workspace/skills/`` is agent-writable
    *and* the highest-precedence scan entry, so an agent-authored skill can
    shadow a bundled one of the same name. ``activate_skill`` already refuses
    workspace tier on an unattended turn (#649); this is the same rule for the
    path that never goes through it.

    Routes through ``skills.grants_capability`` rather than comparing tiers by
    hand: that allowlist fails closed on an unrecognized tier, where a
    ``!= "workspace"`` check would fail open (#741).
    """
    names = [str(n or "").strip() for n in (event.required_skills or []) if str(n or "").strip()]
    if not names:
        return

    from .skills import grants_capability
    from .tools.skill_tools import activate_skill_internal

    skill_map = {s.name: s for s in ctx.config.discovered_skills}
    for skill_name in names:
        skill_info = skill_map.get(skill_name)
        if skill_info is None:
            log.warning(
                "Calendar event %s requires unknown skill '%s'; skipping",
                event.event_id,
                skill_name,
            )
            ctx.skills.activation_denials.append(skill_name)
            continue
        if not grants_capability(skill_info):
            log.warning(
                "Skipping %r-tier skill '%s' required by calendar event %s: "
                "only trusted placements are activated on unattended turns",
                getattr(skill_info, "trust_tier", "?"),
                skill_name,
                event.event_id,
            )
            ctx.skills.activation_denials.append(skill_name)
            continue
        try:
            await activate_skill_internal(ctx, skill_info)
        except Exception as exc:  # noqa: BLE001 - one skill must not kill the wake
            log.error(
                "Failed to activate skill '%s' for calendar event %s: %s",
                skill_name,
                event.event_id,
                exc,
            )
            ctx.skills.activation_denials.append(skill_name)
            continue
        # Deliberately do NOT add to ctx.skills.body_in_prompt.
        #
        # That set means "this body is already part of this turn's prompt", and
        # render_activated_skills skips anything in it. A schedule can set it
        # because schedules.py builds a <loaded_skills> block into the prompt
        # itself. A calendar wake has no equivalent: calendar_manager enqueues
        # _build_prompt(event), which is the event's `reason` and nothing else.
        # Marking it here therefore suppressed the body while delivering it
        # nowhere, so `required_skills` did the opposite of what it names (#2006).
        #
        # Leaving it unset lets the composer render the body as the transient
        # <activated_skills> block, which re-renders from ctx.skills.activated
        # each turn and is never archived (#1823) — the right shape for a
        # multi-turn composition, where an archived body would accumulate.


def apply_event_policy(ctx: "Context", event: "CalendarEvent") -> None:
    """Apply an event's tool policy and model pin to a wake context.

    Call this **after** any inherited base setup. ``compose_wake_context_setup``
    replays the conversation's ``last_context_setup``, which restores the
    previous turn's tool policy; applying ours first would let a stale policy
    win.

    Restriction fields apply unconditionally. Preapproval — bare tool names in
    ``ctx.tools.preapproved`` and scoped shell patterns — applies only when
    ``policy_granted`` is set by a human-controlled creation path.
    """
    if event.model:
        ctx.active_model = event.model

    tools = ctx.tools
    allowed = list(event.allowed_tools or [])
    disallowed = list(event.disallowed_tools or [])
    patterns = list(event.shell_patterns or [])

    if allowed or patterns:
        # ``allowed_tools`` may carry scoped ``shell(...)`` entries; they are
        # preapproval, not visible-tool names. A pattern implies the shell tool
        # must stay visible.
        tool_names, inline_patterns = split_allowed_tools(allowed)
        all_patterns = patterns + inline_patterns
        if all_patterns and "shell" not in tool_names:
            tool_names.append("shell")
        tools.allowed = set(tool_names) | _ESCAPE_HATCH_TOOLS
        if event.policy_granted:
            # Never blanket-preapprove the `shell` tool itself: shell_tools
            # treats `"shell" in preapproved` as "every command is allowed",
            # which would make the scoped patterns below meaningless. Shell
            # preapproval is exclusively the pattern list.
            tools.preapproved = (set(tool_names) - _ESCAPE_HATCH_TOOLS) - {"shell"}
            tools.preapproved_shell_patterns = all_patterns
        else:
            # Restriction without grant. Drop anything a previous turn may have
            # preapproved, or the inherited setup would hand this event a
            # standing grant it never earned.
            tools.preapproved = set()
            tools.preapproved_shell_patterns = []

    if disallowed:
        tools.disallowed = set(disallowed)

    if not event.policy_granted and (patterns or allowed):
        log.info(
            "Calendar event %s restricts tools without a human-controlled creation path; no preapproval granted",
            event.event_id,
        )
