"""Skill activation tool — lazy-loads skills with permission checking."""

import ast
import asyncio
import hashlib
import importlib.util
import inspect
import json
import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..media import ToolResult
from ..skills import (
    CheckResult,
    find_misplaced_skills,
    is_discoverable_skill_dir,
    validate_skill_md,
)
from .confirmation import request_confirmation

if TYPE_CHECKING:
    from decafclaw.context import Context

    from ..skills import SkillInfo

log = logging.getLogger(__name__)


class SkillContractError(Exception):
    """A skill's tools.py exports don't match the native-tool contract.

    Raised at load time so the failure names the contract instead of
    surfacing downstream as an opaque `dict.update()` TypeError (#675).
    """


def check_tools_contract(module) -> list[str]:
    """Return contract violations in a skill's imported tools.py module.

    The loader requires `TOOLS` to be a dict mapping tool name -> callable
    and `TOOL_DEFINITIONS` to be a list of OpenAI function schemas; a skill
    exporting only `get_tools(ctx)` has neither and is fine. Both an empty
    list of problems and the absence of the exports mean "no violations".

    Shared by `skill_validate` (reports them as a check) and
    `_load_native_tools` (raises), so the validator can never green-light a
    skill the loader will reject.
    """
    problems: list[str] = []

    tools = getattr(module, "TOOLS", None)
    if tools is not None:
        if not isinstance(tools, dict):
            problems.append(
                "TOOLS must be a dict mapping tool name -> function "
                f"(e.g. {{'my_tool': my_tool}}), got {type(tools).__name__}"
            )
        else:
            for key, value in tools.items():
                if not isinstance(key, str):
                    problems.append(
                        f"TOOLS keys must be str tool names, got {type(key).__name__}"
                    )
                elif not callable(value):
                    problems.append(
                        f"TOOLS[{key!r}] must be callable, got {type(value).__name__}"
                    )

    tool_defs = getattr(module, "TOOL_DEFINITIONS", None)
    if tool_defs is not None:
        if not isinstance(tool_defs, list | tuple):
            problems.append(
                "TOOL_DEFINITIONS must be a list of function schemas "
                "(e.g. [{'type': 'function', 'function': {'name': 'my_tool'}}]), "
                f"got {type(tool_defs).__name__}"
            )
        else:
            for i, td in enumerate(tool_defs):
                if not isinstance(td, dict):
                    problems.append(
                        f"TOOL_DEFINITIONS[{i}] must be a dict, got "
                        f"{type(td).__name__}"
                    )
                    continue
                fn = td.get("function")
                if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) \
                        or not fn["name"]:
                    problems.append(
                        f"TOOL_DEFINITIONS[{i}] needs a non-empty function.name string"
                    )

    return problems


def _import_tools_module(module_name: str, tools_path: Path):
    """Import a skill's tools.py from source, bypassing the bytecode cache.

    Skill modules are edited live and re-imported in-process, which is
    exactly the workload CPython's bytecode cache handles badly: a cached
    .pyc is validated against the source's size and its mtime *truncated to
    whole seconds*, so an edit that keeps the file the same size and lands
    in the same second as the previous import re-executes the STALE
    bytecode. The edit then appears to have had no effect at all — a
    symptom indistinguishable from a reload that never ran, and one that
    sends the author looking for the bug in code that isn't executing.

    Compiling the source ourselves takes the cache out of the picture. It
    also stops littering the agent-writable workspace with `__pycache__`
    directories, which the agent cannot remove with `workspace_delete`.
    """
    spec = importlib.util.spec_from_file_location(module_name, tools_path)
    if spec is None:
        raise ImportError(f"Could not load module spec for {tools_path}")
    module = importlib.util.module_from_spec(spec)
    had_module = module_name in sys.modules
    prev_module = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        code = compile(tools_path.read_text(), str(tools_path), "exec")
        exec(code, module.__dict__)  # noqa: S102 — skill tools are trusted code by placement
    except Exception:
        if had_module and prev_module is not None:
            sys.modules[module_name] = prev_module
        else:
            sys.modules.pop(module_name, None)
        raise
    return module


_CTX_RECEIVERS = frozenset({"ctx", "context"})


def _is_tool_namespace_receiver(func: ast.expr) -> bool:
    """True when the call's receiver is `ctx` / `context`, or `<that>.tools`.

    Used only to gate tool names that collide with ordinary Python methods.
    Deliberately shallow: `ctx.cancelled.wait()` is a real idiom in this
    codebase and must not be flagged, while `ctx.wait()` and
    `ctx.tools.shell()` should be.
    """
    value = func.value if isinstance(func, ast.Attribute | ast.Subscript) else None
    if isinstance(value, ast.Attribute):  # ctx.tools.X
        return (value.attr == "tools"
                and isinstance(value.value, ast.Name)
                and value.value.id in _CTX_RECEIVERS)
    return isinstance(value, ast.Name) and value.id in _CTX_RECEIVERS


def _called_tool_name(func: ast.expr) -> str | None:
    """The decaf tool name a call target names, however it was reached.

    Handles the shapes that actually turn up:

        ctx.shell_background_start(...)          Attribute
        ctx.tools.shell_background_start(...)    Attribute chain
        context.shell_background_start(...)      receiver renamed
        self.shell_background_start(...)         inside a helper class
        context['shell_background_start'](...)   Subscript

    Keyed on the *name being called*, not on the receiver. The receiver was the
    original mistake: a tool's first parameter is `ctx` by convention only, and
    an eval produced `context['shell_background_start'](...)` specifically while
    working around earlier validation failures. The tool name is the signal —
    these are specific identifiers like `shell_background_start`, not words that
    show up by chance.
    """
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Subscript):
        key = func.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value
    return None


def _phantom_tool_calls(source: str, tool_names: set[str]) -> list[str]:
    """Find calls to decaf tools from inside a skill's tools.py.

    A skill tool is a plain Python function with no channel back into the tool
    layer, so `default_api.shell_background_start(...)`,
    `ctx.shell_background_start(...)` and `ctx.tools.shell_background_start(...)`
    are all impossible. They are also *popular*: all three variants appeared in
    a single session, and evals confirmed that documenting the constraint in
    skill-creator does not suppress it (0/6 with the guidance loaded).

    They cannot be caught at import — the call sits in a function body, so the
    module imports cleanly and the skill activates. It fails only when the user
    invokes the tool. Detecting it statically is the difference between a
    validator that says PASS on a broken skill and one that names the problem,
    which is the same lesson as the #675 export-shape contract.

    Deliberately narrow to stay free of false positives: only a `default_api`
    reference, or a call whose attribute chain roots at `ctx` AND whose final
    attribute is a real decaf tool name. `ctx.publish(...)` and
    `ctx.config.workspace_path` are untouched.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # tools_import reports this; don't double-report

    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "default_api":
            problems.append(
                "references `default_api`, which does not exist in decafclaw — "
                "there is no way to call a decaf tool from inside a skill tool"
            )
        elif isinstance(node, ast.Call):
            called = _called_tool_name(node.func)
            # Two of ~107 tool names (`shell`, `wait`) are bare words that
            # collide with everyday Python methods — `process.wait()`,
            # `event.wait()`. For those, require a ctx-ish receiver; the
            # underscored names are distinctive enough to flag anywhere.
            # Without this the check fires on the bundled background and
            # claude_code skills, both of which call `.wait()` legitimately.
            if called in tool_names and (
                    "_" in called or _is_tool_namespace_receiver(node.func)):
                problems.append(
                    f"tries to call the decaf tool `{called}` — a skill tool "
                    f"cannot call another tool, and `ctx` is the runtime "
                    f"context, not a tool namespace. Use a library directly "
                    f"(e.g. `subprocess` / `pathlib`), or drop tools.py and "
                    f"document `{called}` in SKILL.md so the agent calls it"
                )
    # Same wrong call in five places is one problem, not five.
    return list(dict.fromkeys(problems))


def _compute_skill_hash(skill_info: "SkillInfo") -> str:
    """Hash the tools.py content. Empty string if no native tools."""
    if not skill_info.has_native_tools:
        return ""
    tools_path = skill_info.location / "tools.py"
    if not tools_path.exists():
        return ""
    try:
        content = tools_path.read_bytes()
        return hashlib.sha256(content).hexdigest()
    except OSError:
        return ""


def _permissions_path(config) -> Path:
    """Path to the skill permissions file (outside workspace, read-only to agent)."""
    return config.agent_path / "skill_permissions.json"


def _load_permissions(config) -> dict:
    """Load skill permissions from disk. Returns {} if missing or corrupt."""
    path = _permissions_path(config)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Could not read skill permissions: {e}")
        return {}


def _save_permission(config, skill_name: str, value: dict | str) -> None:
    """Save a skill permission. Called by the host-side confirmation handler."""
    path = _permissions_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    perms = _load_permissions(config)
    perms[skill_name] = value
    path.write_text(json.dumps(perms, indent=2) + "\n")
    log.info(f"Saved skill permission: {skill_name}={value}")


def _rejection_display_path(config, path: Path) -> str:
    """Show a rejected SKILL.md path relative to a meaningful root.

    For skills outside workspace/agent roots (e.g. absolute
    extra_skill_paths entries) we redact to the trailing
    <skill-dir>/SKILL.md segments rather than echo the full host path
    into refresh_skills output.
    """
    for root in (config.workspace_path, config.agent_path):
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return str(Path(*path.parts[-2:])) if len(path.parts) >= 2 else str(path)


def _skill_phantom_calls(skill_info, config) -> list[str]:
    """Phantom decaf-tool calls in a skill's tools.py, for the activation path.

    `skill_validate` reports the same thing, but only when the author chooses
    to run it — evals measured that choice as a coin flip, which capped the
    rate of correct outcomes at roughly 1/3 (#701). Activation cannot be
    skipped: a skill's tools do not exist until it is activated. Checking here
    closes the gap.

    Returns [] when the source can't be read; the import that follows will
    report that far better than a guess from here.
    """
    tools_path = skill_info.location / "tools.py"
    try:
        source = tools_path.read_text()
    except OSError:
        return []
    return _phantom_tool_calls(source, _known_tool_names(config))


def _known_tool_names(config) -> set[str]:
    """Every decaf tool name a skill might wrongly try to call.

    Core tools plus every discovered skill's tools (which is where
    `shell_background_start` — the most-wrapped tool — actually lives).
    """
    return _core_tool_names() | set(config.skill_tool_owners)


def _lint_tools_py(skill_dir: Path, tool_names: set[str]) -> list[CheckResult]:
    """tools.py-specific checks for skill_validate.

    Returns [] for a text-only skill (no tools.py and no stray entrypoint).
    Imports tools.py to surface SyntaxError / NameError / ImportError —
    the same exec_module path activation uses — and introspects (does NOT
    call) get_tools' signature.
    """
    checks: list[CheckResult] = []
    tools_py = skill_dir / "tools.py"

    if not tools_py.exists():
        stray = skill_dir / "main.py"
        if stray.exists():
            checks.append(CheckResult(
                "tools_filename", False,
                "found main.py — native tools must live in 'tools.py'; rename it",
            ))
        return checks

    checks.append(CheckResult("tools_filename", True, "tools.py present"))

    # Source-level check, before the import: these calls import cleanly and
    # only fail when the tool is invoked, so nothing downstream will catch them.
    try:
        source = tools_py.read_text()
    except OSError as exc:
        source = ""
        checks.append(CheckResult(
            "tools_readable", False, f"cannot read tools.py: {exc}",
        ))
    if source:
        phantom = _phantom_tool_calls(source, tool_names)
        if phantom:
            checks.append(CheckResult(
                "no_phantom_tool_calls", False, "; ".join(phantom),
            ))
        else:
            checks.append(CheckResult(
                "no_phantom_tool_calls", True,
                "no attempts to call decaf tools from inside a tool",
            ))

    try:
        # Same source-compiled import the loader uses — a validator that
        # checked stale bytecode could report a SyntaxError the author has
        # already fixed, or pass source the loader will reject.
        module = _import_tools_module(
            f"decafclaw_skill_validate_{skill_dir.name}", tools_py
        )
    except Exception as exc:
        checks.append(CheckResult(
            "tools_import", False,
            f"tools.py failed to import: {type(exc).__name__}: {exc}",
        ))
        return checks

    checks.append(CheckResult("tools_import", True, "tools.py imports cleanly"))

    get_tools = getattr(module, "get_tools", None)
    has_static = hasattr(module, "TOOLS") or hasattr(module, "TOOL_DEFINITIONS")
    if get_tools is not None:
        try:
            params = list(inspect.signature(get_tools).parameters.values())
            accepts_ctx = any(
                p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)
                for p in params
            )
        except (TypeError, ValueError) as exc:
            checks.append(CheckResult(
                "get_tools_signature", False,
                f"could not inspect get_tools signature: {exc}",
            ))
            return checks
        if accepts_ctx:
            checks.append(CheckResult(
                "get_tools_signature", True, "get_tools(ctx) accepts a ctx parameter",
            ))
        else:
            checks.append(CheckResult(
                "get_tools_signature", False,
                "get_tools must accept ctx as its first parameter: "
                "def get_tools(ctx) -> (dict, list)",
            ))
    elif has_static:
        checks.append(CheckResult(
            "tools_exports", True, "exports TOOLS / TOOL_DEFINITIONS",
        ))
    else:
        checks.append(CheckResult(
            "tools_exports", False,
            "tools.py exports neither get_tools(ctx) nor TOOLS / TOOL_DEFINITIONS",
        ))

    # Shape, not just presence: exports of the wrong type import cleanly
    # and pass every check above, then fail at activation with an opaque
    # error. A validator that green-lights an unloadable skill is worse
    # than none — it makes the failure unresolvable (#675).
    if has_static:
        problems = check_tools_contract(module)
        if problems:
            checks.append(CheckResult(
                "tools_shape", False, "; ".join(problems),
            ))
        else:
            checks.append(CheckResult(
                "tools_shape", True,
                "TOOLS / TOOL_DEFINITIONS match the native-tool contract",
            ))
    return checks


def _name_advisories(meta: dict | None, skill_dir: Path) -> list[str]:
    """Non-blocking notes about a skill's `name` field.

    Neither of these prevents loading — a skill activates under its
    frontmatter `name` whatever the directory is called, and the bundled
    catalog contains names with spaces, capitals, and underscores. So they
    must NOT fail validation: reporting FAIL on a skill the loader accepts
    is the validator-contradicts-loader trap in reverse, and it trains the
    author to ignore the validator.
    """
    if not meta:
        return []
    name = meta.get("name")
    if not isinstance(name, str) or not name:
        return []

    advisories: list[str] = []
    if name != skill_dir.name:
        advisories.append(
            f"frontmatter name '{name}' differs from the directory "
            f"'{skill_dir.name}' — this loads fine, but you activate it as "
            f"'{name}' while its files live under '{skill_dir.name}'. "
            f"Matching them avoids the confusion."
        )
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
        advisories.append(
            f"name '{name}' isn't the conventional format (lowercase letters, "
            f"numbers, and single hyphens). This loads fine; the convention "
            f"comes from the Agent Skills standard."
        )
    return advisories


def _render_validation(path: str, checks: list[CheckResult],
                       advisories: list[str] | None = None) -> ToolResult:
    """Render a checklist of CheckResults as a ToolResult (text + data).

    `ok` reflects the checks only. Advisories are things that will load but
    may surprise the author later, so they never flip the verdict.
    """
    advisories = advisories or []
    ok = all(c.passed for c in checks)
    header = "PASS" if ok else "FAIL"
    lines = [f"skill_validate '{path}': {header}", ""]
    for c in checks:
        lines.append(f"  {'[x]' if c.passed else '[ ]'} {c.name}: {c.message}")
    if advisories:
        lines.append("")
        lines.append("Advisories (will load, but worth a look):")
        lines.extend(f"  ! {a}" for a in advisories)
    if not ok:
        lines.append("")
        lines.append(
            "Fix the unchecked items, then run skill_validate again "
            "(or refresh_skills to load it)."
        )
    return ToolResult(
        text="\n".join(lines),
        data={
            "path": path,
            "ok": ok,
            "checks": [
                {"name": c.name, "passed": c.passed, "message": c.message}
                for c in checks
            ],
            "advisories": advisories,
        },
    )


def _load_native_tools(skill_info) -> tuple[dict, list, object]:
    """Import tools.py from a skill directory and return (TOOLS, TOOL_DEFINITIONS, module).

    If the module exports a get_tools(ctx) function, it can be retrieved
    via getattr(module, "get_tools", None) by the caller.
    """
    tools_path = skill_info.location / "tools.py"
    module = _import_tools_module(
        f"decafclaw_skill_{skill_info.name}", tools_path
    )

    # Reject wrong-shaped exports here rather than letting them surface
    # downstream as `cannot convert dictionary update sequence element #0`
    # from ctx.tools.extra.update() or `'str' object has no attribute 'get'`
    # from a consumer iterating TOOL_DEFINITIONS (#675). Callers that
    # already guard _load_native_tools (activation, build_skill_tool_owners)
    # get an actionable message and skip the skill instead of crashing.
    problems = check_tools_contract(module)
    if problems:
        raise SkillContractError(
            f"{tools_path} does not match the native-tool contract "
            f"(TOOLS: dict[str, callable], TOOL_DEFINITIONS: list of function "
            f"schemas) — {'; '.join(problems)}"
        )

    tools = getattr(module, "TOOLS", {})
    tool_defs = getattr(module, "TOOL_DEFINITIONS", [])
    return tools, tool_defs, module


async def _call_init(module, config, skill_name: str = "") -> None:
    """Call module.init() with config and optional skill config.

    If the module exports a SkillConfig dataclass, resolve it from
    config.skills[skill_name] + env vars, then call init(config, skill_config).
    Otherwise call init(config) for backward compat.
    """
    init_fn = getattr(module, "init", None)
    if init_fn is None:
        return

    skill_config_cls = getattr(module, "SkillConfig", None)
    if skill_config_cls is not None and skill_name:
        from ..config import load_sub_config

        raw = config.skills.get(skill_name, {})
        prefix = f"SKILLS_{skill_name.upper().replace('-', '_')}"
        skill_config = load_sub_config(skill_config_cls, raw, prefix)
        if asyncio.iscoroutinefunction(init_fn):
            await init_fn(config, skill_config)
        else:
            await asyncio.to_thread(init_fn, config, skill_config)
    else:
        if asyncio.iscoroutinefunction(init_fn):
            await init_fn(config)
        else:
            await asyncio.to_thread(init_fn, config)


async def restore_skills(ctx: "Context") -> None:
    """Re-activate skills recorded in ctx.skills.activated, without permission checks.

    Called at the start of each web gateway turn to restore skills that were
    active in a previous turn or server session.
    """
    skill_names = set(ctx.skills.activated)
    if not skill_names:
        return
    discovered = ctx.config.discovered_skills
    skill_map = {s.name: s for s in discovered}
    existing_tools = set(ctx.tools.extra.keys())
    for name in list(skill_names):
        skill_info = skill_map.get(name)
        if not skill_info or not skill_info.has_native_tools:
            continue

        current_hash = _compute_skill_hash(skill_info)
        if skill_info.trust_tier == "workspace":
            recorded_hash = ctx.skills.activated.get(name)
            if recorded_hash != current_hash:
                log.warning(f"Skill '{name}' code changed since activation. Skipping restore.")
                del ctx.skills.activated[name]
                continue

        # Skip if these tools are already loaded (e.g. from persisted skill state)
        stale = ctx.tools.skill_tool_names.get(name)
        if stale and all(t in existing_tools for t in stale):
            log.debug(f"Skill '{name}' tools already loaded, skipping restore")
            continue

        try:
            tools, tool_defs, module = _load_native_tools(skill_info)
            # Skip if these tools are already loaded (e.g. from persisted skill state
            # but skill_tool_names was lost across restarts)
            if all(t in existing_tools for t in tools):
                log.debug(f"Skill '{name}' tools already loaded, skipping restore")
                continue
            await _call_init(module, ctx.config, name)
            _register_skill_tools(ctx, name, tools, tool_defs)

            # Register dynamic tool provider if available
            get_tools_fn = getattr(module, "get_tools", None)
            if get_tools_fn:
                ctx.tools.dynamic_providers[name] = get_tools_fn
                ctx.tools.dynamic_provider_names[name] = set(tools.keys())

            log.info(f"Restored skill '{name}' with tools: {list(tools.keys())}")
        except Exception as e:
            log.error(f"Failed to restore skill '{name}': {e}")


def _core_tool_names() -> set[str]:
    """Names of the always-registered core tools.

    Function-level import: `tools/__init__` imports this module, so a
    module-level import would close the cycle.
    """
    from . import TOOL_DEFINITIONS  # noqa: PLC0415 — breaks an import cycle

    return {
        td.get("function", {}).get("name", "")
        for td in TOOL_DEFINITIONS
    }


def _find_skill(discovered, name: str):
    """Return the discovered SkillInfo named `name`, or None."""
    for s in discovered:
        if s.name == name:
            return s
    return None


async def tool_activate_skill(ctx: "Context", name: str) -> str | ToolResult:
    """Activate a skill to make its capabilities available in this conversation."""
    log.info(f"[tool:activate_skill] name={name}")

    # Find the skill in discovered skills
    skill_info = _find_skill(ctx.config.discovered_skills, name)

    if skill_info is None:
        # Catalog miss. The skill may have been written earlier in this same
        # turn, or a `refresh_skills` call in the same batch may still be
        # running — tool calls in a batch run concurrently under
        # asyncio.gather, so this read can race the catalog replacement and
        # report "not found" for a skill refresh_skills just listed (#675).
        # Re-scan once before giving up, which also repairs the catalog so
        # restore_skills finds the skill on later turns.
        await asyncio.to_thread(rediscover_skills, ctx.config)
        skill_info = _find_skill(ctx.config.discovered_skills, name)

    if skill_info is None:
        return ToolResult(text=f"[error: skill '{name}' not found. Check Available Skills in your instructions.]")

    # Already active. For a text-only skill there is nothing to do, but for a
    # native skill this is the author's edit-and-reload path: re-import
    # tools.py so a fix to the file actually takes effect. Returning a bare
    # "already active" here made editing an active skill's tools impossible
    # — refresh_skills only rebuilds the catalog on
    # `config`, never the live callables in ctx.tools.extra, so the loop
    # could not converge and the only escape was restarting the process.
    activated = ctx.skills.activated
    if name in activated:
        if not skill_info.has_native_tools:
            return f"Skill '{name}' is already active."
        return await activate_skill_internal(ctx, skill_info, reloading=True)

    # Permission resolution, highest precedence first:
    # 1. User's explicit "deny" in skill_permissions.json — always wins
    # 2. Trusted tier (bundled / admin / extra) — placement is trust
    # 3. User's explicit "always" permission
    # 4. Skill with `auto-approve: true` frontmatter
    # 5. Unattended turn (heartbeat / scheduled) — denied, see #649
    # 6. Interactive confirmation
    # Trust by placement: bundled/admin/extra skills are pre-trusted
    # because the user opted them in by editing source, placing files,
    # or editing config. Workspace skills could be agent-authored, so
    # they still require explicit confirmation — and an unattended turn
    # cannot give it, so it gets a denial rather than an unanswerable prompt.
    is_trusted_tier = skill_info.trust_tier != "workspace"
    perms = _load_permissions(ctx.config)
    perm_val = perms.get(name)
    perm_status = perm_val.get("status") if isinstance(perm_val, dict) else perm_val
    perm_hash = perm_val.get("hash") if isinstance(perm_val, dict) else ""

    if perm_status == "deny":
        return ToolResult(text=f"[error: activation of skill '{name}' was denied by user]")

    current_hash = _compute_skill_hash(skill_info)

    if (not is_trusted_tier
            and not (perm_status == "always" and perm_hash == current_hash)
            and not skill_info.auto_approve):
        # Need confirmation (workspace tier only at this point)
        if ctx.is_unattended:
            # Nobody can answer a prompt on this turn, so it would block for the
            # 60s timeout and end in this same denial. An unattended turn gets a
            # workspace skill only via a standing "always" grant, handled above.
            log.warning(
                f"[tool:activate_skill] denied on unattended turn "
                f"(task_mode={ctx.task_mode!r}): workspace-tier skill "
                f"'{name}' has no standing grant or its code was modified")
            return ToolResult(
                text=f"[error: activation of skill '{name}' was denied by user]")
        approved, always = await _request_skill_confirmation(ctx, name)
        if not approved:
            return ToolResult(text=f"[error: activation of skill '{name}' was denied by user]")
        if always:
            _save_permission(ctx.config, name, {"status": "always", "hash": current_hash})

    # Activate the skill (shared logic)
    result = await activate_skill_internal(ctx, skill_info)
    if isinstance(result, ToolResult):
        return result
    return result


def _retract_skill_tools(ctx: "Context", name: str) -> None:
    """Remove the tools a previous activation of `name` registered.

    Called after a successful re-import and before registering the new
    generation, so a failed reload leaves the working tools untouched.

    Shadowing makes this more than a delete. `execute_tool` checks
    `ctx.tools.extra` before the global registry, so a later-activated skill's
    tool of the same name genuinely wins — and if the reloaded skill was the
    shadower and drops that name, the shadowed skill is still active and its
    tool must stay callable. So after removing this skill's names, any name
    another active skill also provides is rebound from that skill's recorded
    contribution. A core tool needs no rebinding: removing the shadow from
    `extra` already lets the global registry answer for it again.
    """
    stale = ctx.tools.skill_tool_names.get(name, set())
    if not stale:
        return
    for tool_name in stale:
        ctx.tools.extra.pop(tool_name, None)
    ctx.tools.extra_definitions[:] = [
        td for td in ctx.tools.extra_definitions
        if td.get("function", {}).get("name") not in stale
    ]
    ctx.config.always_loaded_skill_tools = (
        ctx.config.always_loaded_skill_tools - stale
    )

    # Later activation wins, so walk providers in reverse activation order and
    # let the first match claim each name. Exactly one definition per name —
    # two would be a duplicate function declaration, which providers reject
    # outright (#684).
    unclaimed = set(stale)
    for other, (tools, tool_defs) in reversed(list(ctx.tools.skill_contributions.items())):
        if not unclaimed:
            break
        if other == name or other not in ctx.skills.activated:
            continue
        for tool_name in sorted(unclaimed & set(tools)):
            ctx.tools.extra[tool_name] = tools[tool_name]
            unclaimed.discard(tool_name)
            log.debug(
                "Rebound %r to skill %r after %r stopped providing it",
                tool_name, other, name,
            )
        recovered = {
            td.get("function", {}).get("name") for td in tool_defs
        } & (set(stale) - unclaimed)
        ctx.tools.extra_definitions.extend(
            td for td in tool_defs
            if td.get("function", {}).get("name") in recovered
        )


def _register_skill_tools(ctx: "Context", name: str, tools: dict, tool_defs: list) -> None:
    """Retract a skill's previous generation, then register this one.

    The single registration path for both activation and post-restart restore.
    Two call sites that each half-updated this bookkeeping would drift, and the
    symptom is invisible until the next reload: `restore_skills` originally
    recorded nothing, so the first reload after a server restart had no idea
    what to retract and left the pre-edit tool bound.
    """
    _retract_skill_tools(ctx, name)
    ctx.tools.extra.update(tools)
    ctx.tools.extra_definitions.extend(tool_defs)
    # Record BOTH the TOOLS keys and the declared function names. They need not
    # match, and retracting by keys alone leaves an orphaned declaration that
    # the next reload duplicates — which providers reject outright (Vertex:
    # `400 Duplicate function declaration found`) at the provider call, before
    # any tool runs (#684).
    declared = {
        td.get("function", {}).get("name") for td in tool_defs
    } - {None, ""}
    ctx.tools.skill_tool_names[name] = set(tools.keys()) | declared
    # Re-inserted so insertion order stays activation order — the reload of an
    # existing skill makes it the most recent provider again.
    ctx.tools.skill_contributions.pop(name, None)
    ctx.tools.skill_contributions[name] = (dict(tools), list(tool_defs))


async def activate_skill_internal(ctx: "Context", skill_info, reloading: bool = False) -> str | ToolResult:
    """Activate a skill: load tools, register on ctx, mark active.

    Shared by tool_activate_skill (with permission checks) and
    command execution (without permission checks). Returns the
    skill body text on success.

    `reloading=True` is the re-activation path for a skill whose tools.py
    changed on disk: the previous generation's tool names are retracted
    before the new ones register, and the result says "reloaded" so the
    caller can tell an edit took effect from a no-op.
    """
    name = skill_info.name
    # Substitute $SKILL_DIR in the body so the LLM sees usable paths.
    # The command and schedule paths do the same via commands.substitute_body
    # (commands.py:417 / schedules.py:235), both using .resolve() so the LLM
    # always gets an absolute path regardless of how data_home was configured.
    # activate_skill needs to match so skills loaded via extra_skill_paths
    # (where the location isn't a conventional guess) work consistently.
    body = skill_info.body.replace("$SKILL_DIR", str(skill_info.location.resolve()))
    result_parts = [body]

    if skill_info.has_native_tools:
        # Phantom decaf-tool calls, before the import. They import cleanly and
        # fail only when the tool is invoked, so this is the last point at
        # which the author can be told without the user hitting it first.
        #
        # Workspace tier is agent-authored: refuse to load, which is the
        # forcing function #701 is about. Trusted tiers are the user's own
        # code and may hold many working tools beside one broken one, so warn
        # instead of breaking their agent — and `activate_always_loaded` skips
        # workspace tier entirely, so startup is never gated on this.
        phantom = _skill_phantom_calls(skill_info, ctx.config)
        if phantom:
            detail = "; ".join(phantom)
            if skill_info.trust_tier == "workspace":
                log.warning(
                    "Refusing to activate skill %r — phantom tool call: %s",
                    name, detail,
                )
                still_active = (
                    " The previously loaded tools are still active."
                    if reloading else ""
                )
                return ToolResult(text=(
                    f"[error: skill '{name}' cannot be activated: {detail}."
                    f"{still_active} Fix tools.py and activate the skill "
                    f"again.]"
                ))
            log.warning(
                "Skill %r (%s tier) has a phantom tool call: %s",
                name, skill_info.trust_tier, detail,
            )
            result_parts.append(
                f"\n\nWarning: this skill's tools.py {detail}. It loaded, but "
                f"that call will raise when the tool runs."
            )

        try:
            tools, tool_defs, module = _load_native_tools(skill_info)
            await _call_init(module, ctx.config, skill_info.name)

            # Import succeeded, so it's safe to drop the previous generation.
            # A no-op on first activation (nothing recorded yet).
            _register_skill_tools(ctx, name, tools, tool_defs)

            # Register dynamic tool provider if the skill exports get_tools()
            get_tools_fn = getattr(module, "get_tools", None)
            if get_tools_fn:
                ctx.tools.dynamic_providers[name] = get_tools_fn
                # Track the initial static tool names so the first refresh
                # can remove tools that aren't in the dynamic subset
                ctx.tools.dynamic_provider_names[name] = set(tools.keys())
                log.info(f"Registered dynamic tool provider for skill '{name}'")

            tool_names = list(tools.keys())
            if reloading:
                result_parts.append(
                    f"\n\nReloaded tools.py. The following tools are now "
                    f"available (previous versions discarded): "
                    f"{', '.join(tool_names)}"
                )
                log.info(f"Reloaded native skill '{name}' with tools: {tool_names}")
            else:
                result_parts.append(
                    f"\n\nThe following tools are now available: {', '.join(tool_names)}"
                )
                log.info(f"Activated native skill '{name}' with tools: {tool_names}")

            # Shadowing a core tool name is legal — execute_tool checks
            # ctx.tools.extra before the global registry, so the skill's
            # version really does win — but it used to happen in total
            # silence, and the agent authoring the skill has no way to know
            # which names are taken. Say so, in the result the LLM reads
            # (#684).
            # sorted(): set iteration order for strings varies between
            # processes (hash randomization), and this text goes into the
            # activation result the LLM reads. Stable ordering keeps the
            # output reproducible when a skill shadows more than one name.
            for shadowed in sorted(_core_tool_names() & set(tool_names)):
                result_parts.append(
                    f"\n\nNote: this skill's '{shadowed}' shadows the "
                    f"built-in tool of the same name. The skill's version "
                    f"will be used for the rest of this conversation. Rename "
                    f"it if that wasn't intended."
                )
                log.warning(
                    "Skill %r tool %r shadows a core tool of the same name.",
                    name, shadowed,
                )

            # Cache tool names for always-loaded skills so tool_registry
            # can exempt them from deferral
            if skill_info.always_loaded:
                ctx.config.always_loaded_skill_tools = (
                    ctx.config.always_loaded_skill_tools | set(tool_names)
                )

        except Exception as e:
            log.error(f"Failed to load skill '{name}' tools: {e}")
            # Name the exception type, as skill_validate does — a bare
            # str(SyntaxError) is just "invalid syntax (tools.py, line 1)",
            # which reads like a description rather than a Python error.
            detail = f"{type(e).__name__}: {e}"
            if reloading:
                # Nothing was retracted (the retract happens only after a
                # successful import), so say so explicitly rather than leave
                # the caller guessing whether the skill is now half-loaded.
                return ToolResult(text=(
                    f"[error: failed to reload skill '{name}': {detail}. "
                    f"The previously loaded tools are still active — fix "
                    f"tools.py and activate the skill again.]"
                ))
            return ToolResult(text=f"[error: failed to load skill '{name}': {detail}]")
    else:
        log.info(f"Activated shell-based skill '{name}'")

    ctx.skills.activated[name] = _compute_skill_hash(skill_info)
    
    try:
        await ctx.publish("skill_activated", skill=name)
    except Exception as e:
        log.debug("failed to publish skill_activated: %s", e)
        
    return "\n".join(result_parts)


async def _request_skill_confirmation(ctx: "Context", skill_name: str) -> tuple[bool, bool]:
    """Request user confirmation for skill activation.

    Returns (approved, always) tuple.
    """
    result = await request_confirmation(
        ctx, tool_name="activate_skill",
        command=f"Activate skill: {skill_name}",
        message=f"Activate skill: **{skill_name}**",
        skill_name=skill_name,
    )
    return result.get("approved", False), result.get("always", False)


def tool_skill_validate(ctx: "Context", path: str) -> ToolResult:
    """Pre-flight validate a single workspace skill directory."""
    log.info(f"[tool:skill_validate] {path}")
    workspace = ctx.config.workspace_path.resolve()
    target = (workspace / path).resolve()
    if not target.is_relative_to(workspace):
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")

    skill_dir = target.parent if target.name == "SKILL.md" else target
    if not skill_dir.is_dir():
        return ToolResult(
            text=f"[error: '{path}' is not a directory in the workspace]"
        )

    checks: list[CheckResult] = []

    # Location first: a skill in the wrong directory is invisible to
    # discovery, so every other check below is beside the point. Reporting
    # PASS here while refresh_skills silently found nothing gives the author
    # two tools that contradict each other and no way to reconcile them.
    if is_discoverable_skill_dir(ctx.config, skill_dir):
        checks.append(CheckResult(
            "discoverable", True, "location is scanned by skill discovery",
        ))
    else:
        checks.append(CheckResult(
            "discoverable", False,
            f"nothing scans '{path}' — a workspace skill must be an immediate "
            f"child of the workspace 'skills/' directory. Move it to "
            f"'skills/{skill_dir.name}' (workspace_write paths are already "
            f"workspace-relative, so do NOT prefix them with 'workspace/').",
        ))

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        checks.append(CheckResult(
            "skill_md_present", False,
            "no SKILL.md here — a skill needs skills/<name>/SKILL.md",
        ))
        return _render_validation(path, checks)
    checks.append(CheckResult("skill_md_present", True, "SKILL.md present"))

    # Discovery-level checks — shared source of truth with refresh_skills.
    validation = validate_skill_md(skill_md)
    checks.extend(validation.checks)
    # tools.py checks run regardless of frontmatter validity (filesystem-based).
    checks.extend(_lint_tools_py(skill_dir, _known_tool_names(ctx.config)))

    return _render_validation(
        path, checks, _name_advisories(validation.meta, skill_dir)
    )


def rediscover_skills(config) -> list:
    """Re-scan skill directories and update `config` in place.

    The single mutation path for the runtime skill catalog — used by
    `refresh_skills` and by `activate_skill`'s catalog-miss path. Returns
    the list of SkillRejections so callers can surface them.

    Intentional mutation: runtime fields need to update the shared config
    object that the agent loop holds. dataclasses.replace() would create
    a disconnected copy.
    """
    from ..prompts import load_system_prompt
    from ..skills import build_skill_tool_owners, discover_skills
    from ..tool_definitions import invalidate_skill_cache  # deferred: circular dep

    rejections: list = []
    if not config.system_prompt or "<skill_catalog>" in config.system_prompt:
        config.system_prompt, config.discovered_skills = load_system_prompt(
            config, rejections=rejections
        )
    else:
        config.discovered_skills = discover_skills(config, rejections=rejections)
    config.skill_tool_owners = build_skill_tool_owners(config.discovered_skills)
    invalidate_skill_cache(config)
    return rejections


def tool_refresh_skills(ctx: "Context") -> str | ToolResult:
    """Re-discover skills and update the system prompt catalog."""
    log.info("[tool:refresh_skills]")
    config = ctx.config
    # Snapshot before rediscovery replaces the catalog, so the result can say
    # what actually changed. The full list runs to dozens of names, and
    # "did the skill I just wrote show up?" is the only question the caller
    # usually has — answering it directly beats making them diff the list.
    before = {s.name for s in config.discovered_skills}
    rejections = rediscover_skills(config)
    # List all discovered skills — text-only, native-tools, and user-invocable
    # are all valid activatable skills
    names = [s.name for s in config.discovered_skills]
    text = f"Skills refreshed. Available skills: {', '.join(names) or '(none)'}"

    after = set(names)
    # An empty baseline means the catalog hadn't been populated yet (a fresh
    # Context), not that every skill is new. Reporting a diff against nothing
    # would print the whole list a second time under a "New:" heading.
    if before:
        added = sorted(after - before)
        removed = sorted(before - after)
        if added:
            text += f"\nNew: {', '.join(added)}"
        if removed:
            text += f"\nNo longer found: {', '.join(removed)}"
        if not added and not removed:
            text += "\nNo change since the last refresh."

    if rejections:
        text += "\nRejected (found but not loaded):\n" + "\n".join(
            f"  - {_rejection_display_path(config, r.path)} — {r.reason}"
            for r in rejections
        )

    # A skill in an unscanned directory produces no rejection — discovery
    # never walks there — so without this the author sees only an absence.
    misplaced = find_misplaced_skills(config)
    if misplaced:
        text += "\nPossibly misplaced (found but not scanned):\n" + "\n".join(
            f"  - {found} — nothing scans this path; did you mean "
            f"'{suggested}'?"
            for found, suggested in misplaced
        )
    return text


SKILL_TOOLS = {
    "activate_skill": tool_activate_skill,
    "refresh_skills": tool_refresh_skills,
    "skill_validate": tool_skill_validate,
}

SKILL_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "activate_skill",
            "description": (
                "Activate a skill to make its tools available in this conversation. "
                "You MUST call this before using any skill tools — skill tools do not "
                "exist until activated. Check the Available Skills section in your "
                "instructions for what's available. REQUIRES USER CONFIRMATION unless "
                "previously approved. Once activated, the skill's tools become available "
                "for the rest of this conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the skill to activate",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "priority": "low",
        "function": {
            "name": "refresh_skills",
            "description": "Re-scan skill directories and update the available skills catalog. Use when new skills have been added or removed without restarting the agent.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "low",
        "function": {
            "name": "skill_validate",
            "description": (
                "Validate a workspace skill directory BEFORE it loads, and get the "
                "specific reasons it would be rejected. Checks SKILL.md frontmatter "
                "(must have name + description), that native tools live in tools.py "
                "(NOT main.py), that tools.py imports without error, and that its "
                "exports match the loader's contract: TOOLS must be a dict mapping "
                "tool name -> function, TOOL_DEFINITIONS must be a list of "
                "OpenAI-style function schemas, and/or get_tools(ctx) -> (dict, list). "
                "Use this when a skill you authored isn't appearing, or before "
                "refresh_skills, instead of guessing. Takes a workspace-relative path "
                "like 'skills/my-skill'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Workspace-relative path to the skill directory "
                            "(or its SKILL.md), e.g. 'skills/my-skill'."
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
]
