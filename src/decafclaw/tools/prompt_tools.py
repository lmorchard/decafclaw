"""System prompt section updates — confirmation-gated rewrites of the
agent's mutable prompt sections (issue #24).

The system prompt is assembled from markdown files under
``config.agent_path`` (see decafclaw.prompts). Most of them are
admin-owned and read-only to the agent; two are the agent's own
"living document" sections:

  - ``USER.md``  — who the user is (wrapped in ``<user_context>``)
  - ``CUSTOM.md`` — custom standing instructions (wrapped in
    ``<custom_context>``)

``update_system_prompt`` rewrites one of those two files, with the
same protections as the admin file tools: interactive user
confirmation with a unified diff preview before the write, categorical
blocking on unattended turns and child agents, the shared secret-file
rule (``secret_policy.py``), the redaction-marker guard, and
snapshot revalidation so a file changed while the confirmation is
pending is not clobbered.

``SOUL.md`` is the core identity file: it is guarded against agent
modification by name, and the guard holds whether or not the file
exists on disk.

The private helpers imported from ``.admin_tools`` are the project's
shared confirmation/revalidation machinery — reusing them (rather than
copying) is what keeps the recovery path below inheriting exactly the
guards the interactive path has (see #1013).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ..media import ToolResult
from ..secret_policy import is_refused, marker_in, marker_message, refusal_message
from .admin_tools import (
    _AdminFileSnapshot,
    _capture_snapshot,
    _confirm_admin_mutation,
    _file_error,
    _is_cancelled,
    _mini_diff,
    _resolve_admin_path,
    _revalidate_snapshot,
)
from .file_locks import file_lock

if TYPE_CHECKING:
    from decafclaw.confirmations import ConfirmationRequest, ConfirmationResponse
    from decafclaw.context import Context

log = logging.getLogger(__name__)

# The only sections the agent may update, keyed by upper-cased name to the
# canonical on-disk file name. A section is a *name*, not a path: anything
# with a separator or traversal is rejected outright.
_ALLOWED_SECTIONS: dict[str, str] = {
    "USER.MD": "USER.md",
    "CUSTOM.MD": "CUSTOM.md",
}

# Core identity files the agent must never modify. Matched by name
# (upper-cased), independent of on-disk state.
_GUARDED_SECTIONS: frozenset[str] = frozenset({"SOUL.MD"})


def _allowed_sections_list() -> str:
    """The allowed sections for user-facing messages (canonical names)."""
    return ", ".join(_ALLOWED_SECTIONS.values())


def _validate_section(section: str) -> tuple[str, None] | tuple[None, ToolResult]:
    """Validate a section name. Returns a (canonical_name, error) pair where
    exactly one element is None:

    - allowed section → (canonical_name, None)
    - guarded section → (None, guarded-error)
    - anything else   → (None, descriptive-error)

    Callers pair the `err is not None` early return with
    `assert canonical is not None` (the same idiom the admin tools use
    for `_resolve_admin_path`).
    """
    cleaned = (section or "").strip()
    if not cleaned or "/" in cleaned or "\\" in cleaned or ".." in cleaned:
        return None, ToolResult(
            text=(
                f"[error: section '{section}' is not a valid prompt section name. "
                f"Allowed sections: {_allowed_sections_list()} (plain file names, no paths). "
                f"SOUL.md is guarded and cannot be updated. For other files under "
                f"data/{{agent_id}}/ use admin_write.]"
            )
        )
    upper = cleaned.upper()
    if upper in _GUARDED_SECTIONS:
        return None, ToolResult(
            text=(
                f"[error: '{cleaned}' is a guarded core identity file and cannot be modified with "
                f"update_system_prompt. Ask the user to edit it directly (data/{{agent_id}}/{cleaned}).]"
            )
        )
    canonical = _ALLOWED_SECTIONS.get(upper)
    if canonical is None:
        return None, ToolResult(
            text=(
                f"[error: '{cleaned}' is not an updatable prompt section. "
                f"Allowed sections: {_allowed_sections_list()}. SOUL.md is guarded and "
                f"cannot be updated. For other files under data/{{agent_id}}/ use admin_write.]"
            )
        )
    return canonical, None


async def tool_update_system_prompt(ctx: "Context", section: str, content: str) -> str | ToolResult:
    """Update a mutable system prompt section (USER.md or CUSTOM.md) in the agent admin directory."""
    log.info(f"[tool:update_system_prompt] section={section!r}")

    canonical, err = _validate_section(section)
    if err is not None:
        return err
    assert canonical is not None

    config = ctx.config
    # Containment + workspace exclusion, same as the admin tools. The
    # canonical name comes from the allowlist, so this is defense in depth
    # against a future allowlist entry that isn't a plain file name.
    resolved, res_err = _resolve_admin_path(config, canonical)
    if res_err:
        return ToolResult(text=res_err)
    assert resolved is not None

    # The one shared secret rule (secret_policy.py): an admin-widened
    # refuse_paths entry covering this file blocks the write. USER.md /
    # CUSTOM.md are not in the default refuse set and are not redactable
    # (not JSON config), so the redaction tier does not apply to them.
    if is_refused(canonical, config):
        return ToolResult(text=refusal_message(canonical))
    # Marker guard: a payload holding the <redacted> placeholder would
    # clobber a real secret value if the agent round-tripped a redacted
    # read back through this tool.
    if marker_in(content):
        return ToolResult(text=marker_message(canonical))

    if resolved.is_dir():
        return ToolResult(text=f"[error: '{canonical}' is a directory, not a file]")

    snapshot, snap_err = _capture_snapshot(resolved)
    if snap_err:
        return snap_err
    assert snapshot is not None

    if snapshot.exists:
        existing = snapshot.content or ""
        diff = _mini_diff(existing, content, canonical)
        preview = diff if diff else "(no changes)"
    else:
        diff = _mini_diff("", content, canonical)
        preview = f"(new file, {len(content)} characters)\n\n" + (diff if diff else content)

    gate = await _confirm_admin_mutation(
        ctx,
        "update_system_prompt",
        canonical,
        preview,
        payload={"content": content},
        snapshot=snapshot,
    )
    if gate is not None:
        return gate

    async with file_lock(resolved):
        if _is_cancelled(ctx):
            return ToolResult(text="[tool interrupted: agent turn cancelled]")

        re_resolved, reval_err = await _revalidate_snapshot(config, canonical, snapshot)
        if reval_err:
            return reval_err
        assert re_resolved is not None

        try:
            await asyncio.to_thread(re_resolved.write_text, content, encoding="utf-8")
            summary = f"Updated '{canonical}' in the agent admin directory ({len(content)} characters)"
            if snapshot.exists and diff:
                return f"{summary}\n\n{diff}"
            return summary
        except PermissionError as e:
            return _file_error(e, canonical)


class UpdateSystemPromptHandler:
    """Confirmation handler for ``UPDATE_SYSTEM_PROMPT`` actions.

    Invoked only by the recovery path (when a pending confirmation is
    resolved after server restart or when no agent loop is running).
    MUST inherit every guard the interactive tool has — section
    allowlist, guarded sections, secret-path refusal, marker guard, and
    snapshot revalidation — because a pending approval is re-applied here
    after a restart, and stored action_data is not trusted input
    (see #1013, AdminMutationHandler._apply_mutation).
    """

    async def on_approve(self, ctx: Any, request: "ConfirmationRequest", response: "ConfirmationResponse") -> dict:
        action_data = request.action_data or {}
        section = action_data.get("section") or ""
        payload = action_data.get("payload") or {}
        content = payload.get("content")
        snapshot_data = action_data.get("snapshot") or {}

        if content is None:
            return await self._cancel(ctx, section or "unknown", "missing content payload")

        # Re-validate against the current allowlist — the stored
        # action_data is not trusted input.
        canonical, err = _validate_section(section)
        if err is not None:
            return await self._cancel(ctx, section or "unknown", err.text)
        assert canonical is not None

        config = ctx.config
        target = config.agent_path / canonical

        # Secret-path refusal: the same rule every other admin writer consults.
        if is_refused(canonical, config):
            return await self._cancel(ctx, canonical, refusal_message(canonical))

        # Marker guard: the write payload is the only content to guard.
        if marker_in(content):
            return await self._cancel(ctx, canonical, marker_message(canonical))

        snapshot = _AdminFileSnapshot.from_dict(snapshot_data, default_path=target)

        try:
            async with file_lock(target):
                re_resolved, reval_err = await _revalidate_snapshot(config, canonical, snapshot)
                if reval_err:
                    return await self._cancel(ctx, canonical, reval_err.text)
                assert re_resolved is not None

                await asyncio.to_thread(re_resolved.write_text, content, encoding="utf-8")
                summary = f"Updated '{canonical}' in the agent admin directory ({len(content)} characters)"
        except Exception as exc:
            log.exception("Error executing recovered prompt update for %s: %s", canonical, exc)
            return await self._cancel(ctx, canonical, str(exc))

        # Success: write synthetic assistant message documenting the recovery mutation
        if ctx is not None and getattr(ctx, "conv_id", None):
            from ..archive import append_message

            append_message(
                config,
                ctx.conv_id,
                {
                    "role": "assistant",
                    "content": f"[Prompt update completed on recovery: {summary}]",
                },
            )
        return {"inject_message": summary, "continue_loop": False}

    async def on_deny(self, ctx: Any, request: "ConfirmationRequest", response: "ConfirmationResponse") -> dict:
        action_data = request.action_data or {}
        section = action_data.get("section", "section")
        msg = f"[update_system_prompt for '{section}' was denied by user]"
        if ctx is not None and getattr(ctx, "conv_id", None):
            from ..archive import append_message

            append_message(
                ctx.config,
                ctx.conv_id,
                {
                    "role": "assistant",
                    "content": msg,
                },
            )
        return {"inject_message": msg, "continue_loop": False}

    async def _cancel(self, ctx: Any, section: str, reason: str) -> dict:
        msg = f"[Prompt update cancelled: {reason}. The requested update was not applied. Please retry.]"
        if ctx is not None and getattr(ctx, "conv_id", None):
            from ..archive import append_message

            append_message(
                ctx.config,
                ctx.conv_id,
                {
                    "role": "assistant",
                    "content": msg,
                },
            )
        return {"error": reason, "continue_loop": False}


PROMPT_TOOLS = {
    "update_system_prompt": tool_update_system_prompt,
}

PROMPT_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "low",
        "timeout": None,
        "function": {
            "name": "update_system_prompt",
            "description": (
                "Update a mutable section of the agent's own system prompt — USER.md (who the user is) "
                "or CUSTOM.md (custom standing instructions) in data/{agent_id}/. Use when the user "
                "explicitly asks to update your user context or standing instructions; the new text is "
                "picked up by load_system_prompt on the next prompt assembly. "
                "SOUL.md is guarded and CANNOT be updated through this tool. "
                "REQUIRES INTERACTIVE USER CONFIRMATION showing a unified diff preview. "
                "Blocked on unattended turns and child agents. "
                "NOT for workspace files (use workspace_write), vault pages (use vault_write), "
                "or other admin files (use admin_write)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": "Prompt section to update: 'USER.md' or 'CUSTOM.md'",
                        "enum": ["USER.md", "CUSTOM.md"],
                    },
                    "content": {
                        "type": "string",
                        "description": "Full replacement text for the section (an empty string clears it)",
                    },
                },
                "required": ["section", "content"],
            },
        },
    },
]
