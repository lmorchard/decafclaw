"""Scheduled tasks — cron-style task files with per-task scheduling."""

import asyncio
import html
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from croniter import croniter

from .skills import (
    _BUNDLED_SKILLS_DIR,
    _iter_skill_dirs,
    _parse_allowed_tools,
    _resolve_extra_skill_paths,
    _split_frontmatter,
)

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

# Every source tier `discover_schedules` can assign. Declared in one place
# so the trust classification below can be checked for completeness.
SCHEDULE_TIERS = ("admin", "workspace", "bundled", "extra")

# Tiers whose frontmatter may GRANT capability — pre-approve tools, shell
# patterns, or email recipients. An allowlist, not a denylist: an
# unrecognized tier gets no pre-approval, because a security gate must
# fail closed.
#
# `workspace` is excluded because `workspace/schedules/*.md` is
# agent-writable by design, so an agent could otherwise approve its own
# shell commands (#731). `extra` (contrib) is excluded as defense in
# depth — it is force-disabled at discovery today, and a user opts in by
# copying the file to the admin dir, which makes its source `admin`.
_PREAPPROVAL_TIERS = frozenset({"admin", "bundled"})
_UNTRUSTED_TIERS = frozenset({"workspace", "extra"})


@dataclass
class ScheduleTask:
    """A parsed schedule file."""
    name: str
    schedule: str  # 5-field cron expression
    body: str
    source: str  # "admin" | "workspace" | "bundled" | "extra"
    path: Path
    channel: str = ""
    enabled: bool = True
    model: str = ""  # named model config, empty = default
    allowed_tools: list[str] = field(default_factory=list)
    shell_patterns: list[str] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)
    # Per-task overlay for `send_email` — exact addresses or
    # `@domain.com` suffix patterns that bypass confirmation. Merged
    # with `config.email.allowed_recipients` at tool-call time.
    email_recipients: list[str] = field(default_factory=list)
    # Path to a Python script run before the turn; its stdout is injected into
    # the prompt. Relative to workspace/ or data/{agent_id}/ (#450).
    pre_script: str = ""
    # Frontmatter keys the parser does not recognize. Diagnostic only —
    # never serialized back, never patchable. Exists so a typo'd key is
    # visible in the UI instead of being silently dropped (#729).
    unknown_keys: list[str] = field(default_factory=list)


# Frontmatter keys `parse_schedule_file` understands. Anything else lands
# in `ScheduleTask.unknown_keys`. `effort` is the legacy alias for `model`.
_KNOWN_FRONTMATTER_KEYS = frozenset({
    "schedule", "enabled", "channel", "model", "effort",
    "allowed-tools", "pre_script", "required-skills", "email-recipients",
})


def parse_schedule_file(path: Path) -> ScheduleTask | None:
    """Parse a schedule markdown file. Returns None if invalid."""
    try:
        text = path.read_text()
    except OSError as e:
        log.warning(f"Cannot read schedule file {path}: {e}")
        return None

    meta, body = _split_frontmatter(text)
    if meta is None:
        log.debug(f"No frontmatter in {path.name}, skipping")
        return None

    schedule = meta.get("schedule", "")
    if not schedule:
        log.warning(f"No schedule field in {path.name}, skipping")
        return None

    if not croniter.is_valid(schedule):
        log.warning(f"Invalid cron expression in {path.name}: {schedule!r}")
        return None

    # Type coercion — YAML can produce unexpected types (e.g. 'false' as string)
    enabled = meta.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = str(enabled).lower() not in ("false", "0", "no", "off")

    allowed_tools_raw = meta.get("allowed-tools", "")
    if allowed_tools_raw is None:
        allowed_tools_raw = ""
    elif isinstance(allowed_tools_raw, list):
        allowed_tools_raw = ", ".join(str(t) for t in allowed_tools_raw)
    allowed_tools, shell_patterns = _parse_allowed_tools(str(allowed_tools_raw))

    required_skills = meta.get("required-skills", [])
    if not isinstance(required_skills, list):
        required_skills = [str(required_skills)] if required_skills else []

    email_recipients = meta.get("email-recipients", [])
    if not isinstance(email_recipients, list):
        email_recipients = [str(email_recipients)] if email_recipients else []

    unknown_keys = sorted(set(meta) - _KNOWN_FRONTMATTER_KEYS)
    if unknown_keys:
        log.warning("Unrecognized frontmatter in %s: %s (ignored)",
                    path.name, ", ".join(unknown_keys))

    return ScheduleTask(
        name=path.stem,
        schedule=schedule,
        body=body.strip(),
        source="",  # set by caller
        path=path,
        channel=str(meta.get("channel", "")),
        pre_script=str(meta.get("pre_script", "")),
        enabled=enabled,
        model=str(meta.get("model", meta.get("effort", ""))),
        allowed_tools=allowed_tools,
        shell_patterns=shell_patterns,
        required_skills=[str(s) for s in required_skills],
        email_recipients=[str(r) for r in email_recipients],
        unknown_keys=unknown_keys,
    )


# -- Discovery ----------------------------------------------------------------


def _discover_skill_schedule_files(config) -> dict[str, ScheduleTask]:
    """Discover SCHEDULE.md sidecars in skill directories.

    Scans: admin > extra > bundled (no workspace — workspace skills
    cannot self-schedule, matching the long-standing rule).

    Contrib (extra-path) SCHEDULE.md is forced to enabled=False so
    third-party skills don't silently activate cron jobs on install.

    Returns a {name -> ScheduleTask} dict. Caller decides how to
    merge with file-based schedules (currently: file-based wins).
    """
    bundled_dir = _BUNDLED_SKILLS_DIR.resolve()
    admin_skills_dir = (config.agent_path / "skills").resolve()
    extra_paths = _resolve_extra_skill_paths(config)

    sources: list[tuple[str, Path]] = [
        ("admin", admin_skills_dir),
        *(("extra", p) for p in extra_paths),
        ("bundled", bundled_dir),
    ]

    result: dict[str, ScheduleTask] = {}
    for tier, base_dir in sources:
        for skill_dir in _iter_skill_dirs(base_dir):
            sched_md = skill_dir / "SCHEDULE.md"
            if not sched_md.exists():
                continue
            task = parse_schedule_file(sched_md)
            if task is None:
                continue
            task.source = tier
            # Use the skill's directory name as the task name so the
            # overlay file at data/{agent_id}/schedules/{name}.md can
            # shadow it by simple name match.
            task.name = skill_dir.name
            if tier == "extra":
                task.enabled = False  # contrib opts-in via overlay
            # First-found wins (admin > extra > bundled)
            result.setdefault(task.name, task)
    return result


def discover_schedules(config) -> list[ScheduleTask]:
    """Discover schedule files from admin/workspace dirs and skill
    SCHEDULE.md sidecars.

    Precedence (highest wins on name collision):
      1. data/{agent_id}/schedules/{name}.md  (admin standalone; also
         acts as the overlay for skill SCHEDULE.md of the same name)
      2. workspace/schedules/{name}.md        (workspace standalone)
      3. Skill SCHEDULE.md (admin > extra > bundled)
    """
    tasks_by_name: dict[str, ScheduleTask] = {}

    # Skill SCHEDULE.md sidecars (lowest precedence — populated first
    # so that standalone files can shadow them).
    skill_tasks = _discover_skill_schedule_files(config)
    tasks_by_name.update(skill_tasks)

    # File-based standalone schedules: workspace then admin. Admin
    # wins over workspace, both win over skill SCHEDULE.md.
    for source, base_dir in [
        ("workspace", config.workspace_path / "schedules"),
        ("admin", config.agent_path / "schedules"),
    ]:
        if not base_dir.is_dir():
            continue
        for path in sorted(base_dir.glob("*.md")):
            task = parse_schedule_file(path)
            if task is None:
                continue
            task.source = source
            tasks_by_name[task.name] = task  # later sources override

    return list(tasks_by_name.values())


# -- Overlay helpers ----------------------------------------------------------


def serialize_to_markdown(task: ScheduleTask) -> str:
    """Render a ScheduleTask as a SCHEDULE.md-format markdown string.

    Frontmatter includes only fields with values. Field order:
    schedule, enabled (only if false), channel, model, allowed-tools,
    pre_script, required-skills, email-recipients.
    """
    fm: dict = {"schedule": task.schedule}
    if not task.enabled:
        fm["enabled"] = False
    if task.channel:
        fm["channel"] = task.channel
    if task.model:
        fm["model"] = task.model
    if task.allowed_tools or task.shell_patterns:
        entries = list(task.allowed_tools)
        entries.extend(f"shell({p})" for p in task.shell_patterns)
        fm["allowed-tools"] = ", ".join(entries)
    if task.pre_script:
        fm["pre_script"] = task.pre_script
    if task.required_skills:
        fm["required-skills"] = list(task.required_skills)
    if task.email_recipients:
        fm["email-recipients"] = list(task.email_recipients)
    fm_text = yaml.safe_dump(fm, sort_keys=False).rstrip()
    return f"---\n{fm_text}\n---\n\n{task.body}\n"


def _overlay_path(config, name: str) -> Path:
    """Path where an overlay would live; validated against safe name."""
    safe = _safe_task_name(name)
    if safe != name:
        raise ValueError(f"unsafe schedule name: {name!r}")
    return config.agent_path / "schedules" / f"{name}.md"


def write_overlay(config, name: str, patch: dict) -> ScheduleTask:
    """Apply patch to current effective state and write full resolved
    task to disk. Returns the newly resolved task.

    Patch keys (all optional): enabled (bool), schedule (str), body (str),
    channel (str), allowed_tools (list[str]), required_skills (list[str]),
    shell_patterns (list[str]), email_recipients (list[str]), model (str),
    pre_script (str).

    Write targets:
    - workspace source → workspace/schedules/{name}.md (in-place edit)
    - admin source (standalone) → data/{agent_id}/schedules/{name}.md (in-place edit)
    - skill SCHEDULE.md source (bundled/admin/extra) → data/{agent_id}/schedules/{name}.md (creates overlay)

    Raises KeyError if the schedule name is not found.
    Raises ValueError if the name is unsafe or the cron expression is invalid.
    """
    tasks = {t.name: t for t in discover_schedules(config)}
    base = tasks.get(name)
    if base is None:
        raise KeyError(name)

    # Treat null (None) values as "leave unchanged" rather than overwriting.
    patch = {k: v for k, v in patch.items() if v is not None}

    # Validate cron expression before touching disk.
    if "schedule" in patch:
        if not isinstance(patch["schedule"], str):
            raise ValueError(
                f"schedule must be a string, got {type(patch['schedule']).__name__}"
            )
        if not croniter.is_valid(patch["schedule"]):
            raise ValueError(f"invalid cron expression: {patch['schedule']!r}")

    # Validate list fields — reject non-list values (e.g. comma-separated strings)
    # rather than silently iterating characters.
    for list_field in ("allowed_tools", "required_skills",
                       "shell_patterns", "email_recipients"):
        if list_field in patch and not isinstance(patch[list_field], list):
            raise ValueError(f"{list_field} must be a list of strings")

    updated = replace(
        base,
        enabled=patch.get("enabled", base.enabled),
        schedule=patch.get("schedule", base.schedule),
        body=patch.get("body", base.body),
        channel=patch.get("channel", base.channel),
        allowed_tools=list(patch.get("allowed_tools", base.allowed_tools)),
        required_skills=list(patch.get("required_skills", base.required_skills)),
        shell_patterns=list(patch.get("shell_patterns", base.shell_patterns)),
        email_recipients=list(patch.get("email_recipients", base.email_recipients)),
        model=patch.get("model", base.model),
        pre_script=patch.get("pre_script", base.pre_script),
    )

    if base.source == "workspace":
        safe = _safe_task_name(name)
        if safe != name:
            raise ValueError(f"unsafe schedule name: {name!r}")
        path = config.workspace_path / "schedules" / f"{name}.md"
    else:
        path = _overlay_path(config, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_to_markdown(updated))

    return {t.name: t for t in discover_schedules(config)}[name]


def delete_overlay(config, name: str) -> ScheduleTask:
    """Delete the admin standalone file for `name`. Returns the
    post-delete resolved task (which must still exist via a SCHEDULE.md
    fallback — caller validates).

    Raises FileNotFoundError if no overlay file exists.
    Raises KeyError if after delete no SCHEDULE.md remains.
    """
    path = _overlay_path(config, name)
    if not path.exists():
        raise FileNotFoundError(name)
    path.unlink()
    tasks = {t.name: t for t in discover_schedules(config)}
    if name not in tasks:
        raise KeyError(name)
    return tasks[name]


# -- Last-run tracking --------------------------------------------------------


def _safe_task_name(task_name: str) -> str:
    """Sanitize task name for use in filesystem paths."""
    # Strip path separators and dots, keep alphanumeric + hyphens + underscores
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", task_name)
    if not safe or safe in (".", ".."):
        safe = "_invalid_"
    return safe


def _last_run_path(config, task_name: str) -> Path:
    base = config.workspace_path / ".schedule_last_run"
    path = (base / _safe_task_name(task_name)).resolve()
    # Verify path stays under the base directory
    if not str(path).startswith(str(base.resolve())):
        raise ValueError(f"Task name resolves outside last-run directory: {task_name!r}")
    return path


def read_last_run(config, task_name: str) -> float:
    """Read last-run timestamp. Returns 0 if never run."""
    path = _last_run_path(config, task_name)
    try:
        if path.exists():
            return float(path.read_text().strip())
    except (ValueError, OSError):
        pass
    return 0


def write_last_run(config, task_name: str, timestamp: float | None = None) -> None:
    """Write last-run timestamp (defaults to now)."""
    path = _last_run_path(config, task_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time() if timestamp is None else timestamp))


def is_due(config, task: ScheduleTask) -> bool:
    """Check if a scheduled task is due to run."""
    last_run = read_last_run(config, task.name)
    if last_run == 0:
        return True  # never run before

    cron = croniter(task.schedule, datetime.fromtimestamp(last_run, tz=timezone.utc))
    next_fire = cron.get_next(float)
    return time.time() >= next_fire


# -- Task execution -----------------------------------------------------------


# tool_search lets the model recover deferred tools; activate_skill lets it
# load additional skill bodies. Scheduled tasks pre-activate required-skills
# and ship a tight allowed-tools list, so without this exemption the model
# has no escape hatch if the task is under-spec'd or the body fails to land.
_SCHEDULE_ESCAPE_HATCH_TOOLS = frozenset({"tool_search", "activate_skill"})

# A scheduled response starting with this suppresses notification delivery — no
# inbox entry, so no channel delivers either (every adapter subscribes to the
# notification). Start-anchored like every other sentinel; see
# heartbeat.response_starts_with_sentinel.
#
# Inert unless a task's own body asks for it. Deliberately NOT added to
# build_task_preamble: telling every scheduled task to consider suppressing
# itself is a policy change, and this is a mechanism (#450).
SILENT_SENTINEL = "[SILENT]"

# Cap on injected pre_script stdout. A module constant, not config: there is no
# reason to tune it per agent, and every knob is surface area.
_PRE_SCRIPT_MAX_CHARS = 8000


def _resolve_pre_script_path(config, pre_script: str) -> Path | None:
    """Resolve `pre_script` against workspace/ then data/{agent_id}/.

    Returns None when the path escapes both roots. Containment is checked with
    `is_relative_to` after resolution, not by string prefix, so `..` segments
    and symlinks can't walk out — same approach as
    `tools/workspace_tools.py:_resolve_safe`.
    """
    for root in (config.workspace_path, config.agent_path):
        root = root.resolve()
        candidate = (root / pre_script).resolve()
        if candidate.is_relative_to(root):
            return candidate
    return None


# Inherited env vars a script plausibly needs to run at all. Everything else is
# withheld: provider credentials live in this process's environment, and a
# pre_script's stdout goes straight into the model's context, so `print(os.environ)`
# in an otherwise innocent script would exfiltrate them (#450 review).
_PRE_SCRIPT_ENV_PASSTHROUGH = (
    "PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
)


def _pre_script_env(config, task: ScheduleTask) -> dict[str, str]:
    """The explicit environment a pre_script runs with.

    An allowlist, not `os.environ` — see _PRE_SCRIPT_ENV_PASSTHROUGH. A script
    that genuinely needs a secret should read it from a file it is pointed at,
    which leaves a reviewable trail.
    """
    env = {
        k: os.environ[k]
        for k in _PRE_SCRIPT_ENV_PASSTHROUGH
        if k in os.environ
    }
    env.update({
        "DECAFCLAW_AGENT_ID": config.agent.id,
        "DECAFCLAW_ROUTINE_NAME": task.name,
        "DECAFCLAW_WORKSPACE": str(config.workspace_path),
    })
    return env


def _neutralize_delimiter(text: str) -> str:
    """Defuse a closing `</pre_script_output>` inside the payload.

    Otherwise a script that printed the closing tag would end the block early and
    everything after it would read as prompt text rather than data. The tag is
    replaced rather than escaped so the result stays plain and obvious to a model.
    """
    return text.replace("</pre_script_output>", "<\\/pre_script_output>")


async def _run_pre_script(config, task: ScheduleTask) -> str:
    """Run a task's pre_script and return the text to inject, or "".

    Fail-open with disclosure. A missing file, a non-zero exit, or a timeout
    returns an error string rather than raising, so the agent gets to say "the
    fetch failed" instead of the run disappearing. Aborting the turn would
    produce silence — which is what `[SILENT]` exists to make a *deliberate*
    choice about, not something a transient network blip should cause.
    """
    if not task.pre_script or not config.pre_script.enabled:
        return ""
    script = _resolve_pre_script_path(config, task.pre_script)
    if script is None:
        log.warning("Scheduled task %r: pre_script %r escapes the allowed roots",
                    task.name, task.pre_script)
        return f"[pre_script error: {task.pre_script!r} is outside the allowed roots]"
    if not script.is_file():
        return f"[pre_script error: {task.pre_script!r} not found]"

    timeout = config.pre_script.timeout_sec
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(config.workspace_path),
            env=_pre_script_env(config, task),
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc is not None:
            proc.kill()
            # Reap it. kill() only signals; without the wait the child stays a
            # zombie until GC and asyncio complains about the transport on
            # shutdown — the kind of "harmless" noise CLAUDE.md rules out.
            await proc.wait()
        log.warning("Scheduled task %r: pre_script timed out after %ss",
                    task.name, timeout)
        return f"[pre_script error: timed out after {timeout}s]"
    except OSError as exc:
        log.warning("Scheduled task %r: pre_script failed to start: %s",
                    task.name, exc)
        return f"[pre_script error: {type(exc).__name__}: {exc}]"

    if err:
        # stderr is diagnostics, not interface — logged, never injected, so a
        # script's warnings don't end up in the model's context.
        log.warning("Scheduled task %r pre_script stderr: %s",
                    task.name, err.decode(errors="replace")[:500])
    if proc.returncode != 0:
        return f"[pre_script error: exited {proc.returncode}]"

    text = _neutralize_delimiter(out.decode(errors="replace"))
    if len(text) > _PRE_SCRIPT_MAX_CHARS:
        # Say so rather than cutting silently. A truncated JSON array or item
        # list reads as a complete one, and the agent would summarize a partial
        # payload as if it were everything.
        log.warning("Scheduled task %r: pre_script output truncated from %d chars",
                    task.name, len(text))
        text = (text[:_PRE_SCRIPT_MAX_CHARS]
                + f"\n[pre_script output truncated at {_PRE_SCRIPT_MAX_CHARS} "
                  f"characters — {len(text)} were produced]")
    return text


def _resolve_skill_dir(config, task: "ScheduleTask") -> str:
    """Resolve `$SKILL_DIR` substitutions for a scheduled task.

    Overlay schedules at `data/{agent_id}/schedules/{name}.md` shadow a
    skill's SCHEDULE.md but no longer sit next to the skill's scripts,
    so `task.path.parent` is the wrong anchor for `$SKILL_DIR` — the
    pattern would whitelist the schedules dir while the SKILL.md body
    (expanded via `info.location` in `_render_required_skill_bodies`)
    points the agent at the original skill dir, and the preapproval
    check fails to match.

    Resolution order: discovered SkillInfo matching `task.name` (which
    equals the original skill dir name for skill-derived schedules,
    including overlays) > first `required-skills` entry that resolves
    against `discovered_skills` > task file's parent dir as fallback for
    genuinely skill-independent schedules.

    Iterating all `required-skills` (not just the first) matches
    `_render_required_skill_bodies`, which silently skips unresolvable
    names and injects whatever else resolves. Stopping at index 0 here
    would let an unresolvable primary entry quietly desync the shell
    pattern from a body that still got injected from a later entry.
    """
    skill_map = {s.name: s for s in (config.discovered_skills or [])}
    info = skill_map.get(task.name)
    if info is None:
        for name in task.required_skills:
            info = skill_map.get(name)
            if info is not None:
                break
    if info is not None:
        return str(info.location.resolve())
    return str(task.path.parent.resolve())


def _render_required_skill_bodies(config, skill_names: list[str]) -> str:
    """Render `<loaded_skills>` block for pre-activated required-skills.

    Scheduled tasks ship a thin trigger in SCHEDULE.md and rely on
    `required-skills` to bring the SKILL.md body into context. Always-loaded
    skill bodies make it into the prompt via `prompts/__init__.py`, but
    per-conversation activated bodies normally arrive as `activate_skill`
    tool-result messages — a path that doesn't fire for scheduled tasks
    (no prior turn). This helper builds the equivalent block so a thin
    trigger has something to act on. Returns "" if nothing resolves.
    """
    if not skill_names:
        return ""
    skill_map = {s.name: s for s in (config.discovered_skills or [])}
    blocks: list[str] = []
    for name in skill_names:
        info = skill_map.get(name)
        if info is None:
            log.warning(f"Schedule references unknown required-skill {name!r}; "
                        f"skipping body injection")
            continue
        if not info.body:
            continue
        body = info.body.replace("$SKILL_DIR", str(info.location.resolve()))
        safe_name = html.escape(info.name, quote=True)
        blocks.append(f'<skill name="{safe_name}">\n{body}\n</skill>')
    if not blocks:
        return ""
    return "<loaded_skills>\n" + "\n".join(blocks) + "\n</loaded_skills>"


async def run_schedule_task(config, event_bus, manager, task: ScheduleTask,
                             conv_id: str | None = None) -> dict:
    """Run a single scheduled task as an agent turn via ConversationManager.

    If conv_id is provided, use it; otherwise generate from task.name + now.
    Returns {"task_name", "channel", "response", "is_ok", "context_id"}.
    """
    from .conversation_manager import TurnKind

    if conv_id is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        conv_id = f"schedule-{task.name}-{timestamp}"
    channel = task.channel or f"schedule:{task.name}"

    # Frontmatter always RESTRICTS which tools are visible. It only GRANTS
    # pre-approval at a human-controlled tier — an agent-writable schedule
    # must not be able to approve its own shell commands (#731).
    trusted = task.source in _PREAPPROVAL_TIERS

    allowed_tools_set = None
    preapproved = set()
    if task.allowed_tools or task.shell_patterns:
        allowed_tools_set = set(task.allowed_tools)
        if task.shell_patterns:
            allowed_tools_set.add("shell")  # ensure shell tool is visible
        # Keep tool_search / activate_skill reachable so the model has
        # an escape hatch if the task is under-spec'd. They don't grant
        # capabilities on their own.
        allowed_tools_set |= _SCHEDULE_ESCAPE_HATCH_TOOLS
        if trusted:
            preapproved = set(task.allowed_tools)

    skill_dir = _resolve_skill_dir(config, task)
    shell_patterns = None
    if trusted and task.shell_patterns:
        shell_patterns = [
            p.replace("$SKILL_DIR", skill_dir) for p in task.shell_patterns
        ]

    # Per-task settings applied after the manager creates the context
    required_skills = list(task.required_skills)
    task_model = task.model
    email_recipients = (task.email_recipients or None) if trusted else None

    async def setup_schedule_ctx(ctx: "Context") -> None:
        """Apply per-task settings (model, tools, skills) to the context."""
        if task_model:
            ctx.active_model = task_model
        if allowed_tools_set is not None:
            ctx.tools.allowed = allowed_tools_set
            ctx.tools.preapproved = preapproved
        if shell_patterns:
            ctx.tools.preapproved_shell_patterns = shell_patterns
        if email_recipients is not None:
            ctx.tools.preapproved_email_recipients = email_recipients
        # Override channel info so events land in the right place
        ctx.channel_id = channel
        ctx.channel_name = channel
        # Pre-activate required skills
        if required_skills:
            discovered = config.discovered_skills
            skill_map = {s.name: s for s in discovered}
            from .tools.skill_tools import activate_skill_internal
            for skill_name in required_skills:
                skill_info = skill_map.get(skill_name)
                if skill_info:
                    try:
                        await activate_skill_internal(ctx, skill_info)
                    except Exception as e:
                        log.error(f"Failed to activate skill '{skill_name}' "
                                  f"for task '{task.name}': {e}")

    from .commands import substitute_body
    from .polling import build_task_preamble

    preamble = build_task_preamble("scheduled task", task.name)
    body = substitute_body(task.body, skill_dir=skill_dir)
    loaded_skills = _render_required_skill_bodies(config, required_skills)
    # Data goes between the instructions and the ask, so the agent reads what
    # it's doing, then what it has, then what to do with it.
    if task.pre_script and not trusted:
        # Same tier gate as the shell/email preapprovals above: pre_script
        # runs arbitrary Python with no approval path at all (#731 fix
        # round 1), so an agent-writable schedule must not be able to make
        # itself one. Disclose rather than silently drop — matches the
        # fail-open-with-disclosure convention in `_run_pre_script`.
        log.warning("pre_script ignored at %s tier: %s", task.source, task.name)
        pre_output = "[pre_script error: ignored — not permitted at this tier]"
    else:
        pre_output = await _run_pre_script(config, task)
    pre_block = (
        f"<pre_script_output>\n{pre_output.rstrip()}\n</pre_script_output>\n\n"
        if pre_output else ""
    )
    prompt = (preamble
              + (f"{loaded_skills}\n\n" if loaded_skills else "")
              + pre_block
              + body)

    try:
        future = await manager.enqueue_turn(
            conv_id=conv_id,
            kind=TurnKind.SCHEDULED_TASK,
            prompt=prompt,
            history=[],
            task_mode="scheduled",
            user_id=f"schedule-{task.source}",
            context_setup=setup_schedule_ctx,
            metadata={"task_name": task.name, "channel": channel},
        )
        result_text = (await future) or "(no response)"
        from .heartbeat import is_heartbeat_ok, response_starts_with_sentinel
        ok = is_heartbeat_ok(result_text)
        if response_starts_with_sentinel(result_text, SILENT_SENTINEL):
            # Suppressed cycles must stay distinguishable from crashed ones in
            # the log — the notification that didn't happen is otherwise the
            # only trace, and an absence isn't diagnosable.
            log.info("Scheduled task %r returned %s — suppressing notification",
                     task.name, SILENT_SENTINEL)
        else:
            await _notify_task_complete(
                config, event_bus, task.name, result_text, ok, conv_id,
            )
        return {
            "task_name": task.name,
            "channel": channel,
            "response": result_text,
            "is_ok": ok,
            "context_id": None,
        }
    except Exception as e:
        log.error(f"Scheduled task '{task.name}' failed: {e}", exc_info=True)
        await _notify_task_complete(
            config, event_bus, task.name, f"[error: {e}]",
            ok=False, conv_id=conv_id,
        )
        return {
            "task_name": task.name,
            "channel": channel,
            "response": f"[error: scheduled task failed: {e}]",
            "is_ok": False,
            "context_id": None,
        }


async def _notify_task_complete(
    config, event_bus, task_name: str, response: str, ok: bool,
    conv_id: str = "",
) -> None:
    """Append an inbox notification for a scheduled-task run."""
    from . import notifications
    title = f"Scheduled: {task_name}" if ok else f"Scheduled task alert: {task_name}"
    body = response.strip().splitlines()[0] if response.strip() else ""
    if len(body) > 160:
        body = body[:157] + "..."
    try:
        await notifications.notify(
            config, event_bus,
            category="schedule", title=title, body=body,
            priority="high" if not ok else "normal",
            conv_id=conv_id or None,
        )
    except Exception as e:
        log.warning(f"Failed to emit schedule notification: {e}")


# -- Timer loop ---------------------------------------------------------------


_SCHEDULE_POLL_INTERVAL = 60


async def run_schedule_timer(config, event_bus, manager, shutdown_event,
                              on_result=None, poll_interval=None):
    """Run the schedule timer loop.

    Discovers schedule files, checks if tasks are due, and runs them.
    """
    from .polling import run_polling_loop

    interval = poll_interval or _SCHEDULE_POLL_INTERVAL
    running_tasks: set[str] = set()
    in_flight: set[asyncio.Task] = set()

    log.info("Schedule timer starting")

    async def _tick():
        tasks = discover_schedules(config)
        if not tasks:
            return

        for task in tasks:
            if not task.enabled:
                continue
            if task.name in running_tasks:
                log.debug(f"Schedule '{task.name}' still running, skipping")
                continue
            if not is_due(config, task):
                continue

            log.info(f"Schedule '{task.name}' is due, executing")
            running_tasks.add(task.name)

            async def _run(t=task):
                try:
                    write_last_run(config, t.name)
                    result = await run_schedule_task(config, event_bus, manager, t)
                    if on_result:
                        try:
                            await on_result(result)
                        except Exception as e:
                            log.error(f"Schedule result callback failed: {e}")
                    elif result["is_ok"]:
                        log.info(f"Schedule '{t.name}': HEARTBEAT_OK")
                    else:
                        log.info(f"Schedule '{t.name}' response: "
                                 f"{result['response'][:200]}")
                except Exception as e:
                    log.error(f"Schedule '{t.name}' execution failed: {e}")
                finally:
                    running_tasks.discard(t.name)

            t = asyncio.create_task(_run())
            in_flight.add(t)
            t.add_done_callback(in_flight.discard)

    try:
        await run_polling_loop(
            interval=interval,
            shutdown_event=shutdown_event,
            on_tick=_tick,
            label="Schedule",
        )
    finally:
        # Await in-flight tasks on shutdown
        if in_flight:
            log.info(f"Waiting for {len(in_flight)} in-flight scheduled task(s)")
            await asyncio.gather(*in_flight, return_exceptions=True)

    log.info("Schedule timer stopped")
