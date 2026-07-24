"""Eval runner — execute test cases against the agent."""

import dataclasses
import logging
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from ..agent import run_agent_turn
from ..commands import dispatch_command
from ..config import Config
from ..context import Context
from ..conversation_manager import ConversationManager
from ..events import EventBus
from ..skills import discover_skills as _discover_skills_fn

log = logging.getLogger(__name__)

# Preserves the concrete dataclass type through _apply_overrides, so a
# Config in stays a Config out rather than degrading to DataclassInstance.
_T = TypeVar("_T")


class _EvalConversationManager(ConversationManager):
    """Eval-mode manager: auto-resolves confirmations routed through the
    manager path.

    In production, transports (mattermost, web UI) subscribe to per-conv
    event streams and dispatch confirmation UIs. Eval mode has no UI, and
    child conv_ids created by ``delegate_task`` never get a transport
    subscriber. Without something in the loop, ``manager.request_confirmation``
    would emit the request and then block on ``confirmation_event.wait()``
    indefinitely.

    We install an auto-resolver on every new conversation the manager
    tracks (parent + all children, including nested delegates). The
    resolver approves or denies per ``setup.auto_confirm``, matching the
    existing legacy event-bus shim's behavior for parent tools.

    Note the parent conversation ("eval") reaches this manager only if
    ``run_test`` sets ``ctx.manager = self`` AND some parent tool routes
    through the manager path. Today ``run_test`` calls ``run_agent_turn``
    directly with ``ctx.request_confirmation = None``, so parent tools
    take the event-bus fallback; child tools go through
    ``manager._start_turn`` which overwrites ``child_ctx.request_confirmation``
    to a manager-based closure (see ``conversation_manager.py::_start_turn``).
    """

    def __init__(self, config, event_bus, *, auto_confirm: bool):
        super().__init__(config, event_bus)
        self._eval_auto_confirm = auto_confirm

    def _get_or_create(self, conv_id):
        is_new = conv_id not in self._conversations
        state = super()._get_or_create(conv_id)
        if is_new:
            self._install_auto_confirm(conv_id)
        return state

    def _install_auto_confirm(self, conv_id: str) -> None:
        """Subscribe an auto-resolver to ``conv_id``'s event stream.

        Fires on ``confirmation_request`` emits and awaits
        ``respond_to_confirmation`` inline. Safe against deadlock because
        ``ConversationManager.request_confirmation`` releases ``state.lock``
        before it emits the request, so the resolver's own lock
        acquisition inside ``respond_to_confirmation`` doesn't contend
        with the caller. The manager runs subscribers concurrently via
        ``asyncio.gather`` in ``emit`` — this resolver just needs to be
        the one that lands ``state.confirmation_response`` before
        ``request_confirmation`` returns to its ``event.wait()``.
        """
        approved = self._eval_auto_confirm

        async def _resolver(event):
            if event.get("type") != "confirmation_request":
                return
            cid = event.get("confirmation_id", "")
            if not cid:
                return
            try:
                await self.respond_to_confirmation(
                    conv_id, cid, approved=approved,
                )
            except Exception:
                log.exception(
                    "Eval auto-confirm resolver failed for conv %s / cid %s",
                    conv_id, cid,
                )

        self.subscribe(conv_id, _resolver)


async def _setup_skills(ctx, test_case: dict):
    """Pre-activate skills specified in setup.skills."""
    setup = _setup_of(test_case)
    skill_names = setup.get("skills", [])
    if not skill_names:
        return

    from ..skills import discover_skills
    from ..tools.skill_tools import activate_skill_internal

    all_skills = discover_skills(ctx.config)
    skill_map = {s.name: s for s in all_skills}

    for name in skill_names:
        info = skill_map.get(name)
        if info:
            await activate_skill_internal(ctx, info)
            log.info(f"Pre-activated skill '{name}' for eval")
        else:
            log.warning(f"Skill '{name}' not found for eval pre-activation")


def _seed_conversation_history(config, test_case: dict) -> list[dict]:
    """Seed `{workspace}/conversations/eval/archive.jsonl` from `setup.conversation_history`.

    Returns the same list of messages (with timestamps filled in) so the
    caller can pre-populate the in-memory history passed to ``run_agent_turn``.
    This way both the running agent loop AND tools that read the archive
    (e.g. ``conversation_search``) see the seeded data.

    Validates that each entry has a ``role``; everything else is passed
    through. Returns ``[]`` when the setup field is absent.
    """
    setup = _setup_of(test_case)
    seed = setup.get("conversation_history") or []
    if not seed:
        return []

    from ..archive import append_message  # local to avoid top-level dep cycle

    now_iso = datetime.now().isoformat()
    out: list[dict] = []
    for i, msg in enumerate(seed):
        if not isinstance(msg, dict) or "role" not in msg:
            raise ValueError(
                f"setup.conversation_history[{i}] must be a dict with a 'role' key"
            )
        # Stamp upfront so the returned list and on-disk archive agree.
        stamped = {**msg, "timestamp": msg.get("timestamp") or now_iso}
        append_message(config, "eval", stamped)
        out.append(stamped)
    return out


async def _setup_workspace(config, test_case: dict):
    """Create fixture data in the temp workspace."""
    import shutil
    setup = _setup_of(test_case)

    # Copy pre-built embeddings fixture if specified
    fixture_db = setup.get("embeddings_fixture")
    if fixture_db:
        fixture_path = Path(fixture_db)
        if fixture_path.exists():
            dest = config.workspace_path / "embeddings.db"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fixture_path, dest)
            log.info(f"Copied embeddings fixture from {fixture_path}")

    # Seed arbitrary workspace files (path → content). Parent dirs created.
    # Sandbox-check each path — absolute paths and `..` escapes would otherwise
    # let a test fixture clobber files outside the temp workspace on the runner.
    workspace_files = setup.get("workspace_files", {})
    workspace_root = config.workspace_path.resolve()
    for rel_path, content in workspace_files.items():
        rel = Path(rel_path)
        if rel.is_absolute():
            raise ValueError(f"workspace_files path must be relative: {rel_path}")
        dest = (config.workspace_path / rel).resolve()
        if not dest.is_relative_to(workspace_root):
            raise ValueError(f"workspace_files path escapes workspace: {rel_path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    # Save journal entries (replaces memories)
    memories = setup.get("memories", [])
    if memories:
        now = datetime.now()
        journal_dir = config.vault_agent_journal_dir / str(now.year)
        journal_dir.mkdir(parents=True, exist_ok=True)
        filepath = journal_dir / f"{now:%Y-%m-%d}.md"
        with open(filepath, "a", encoding="utf-8") as f:
            for mem in memories:
                tag_str = ", ".join(mem.get("tags", []))
                entry = (
                    f"\n## {now:%Y-%m-%d %H:%M}\n\n"
                    f"- **channel:** eval (eval)\n"
                    f"- **tags:** {tag_str}\n"
                    f"\n{mem['content']}\n"
                )
                f.write(entry)

    # Index journal entries for semantic search if strategy is semantic
    if config.embedding.search_strategy == "semantic" and memories:
        from ..embeddings import index_entry
        for mem in memories:
            tag_str = ", ".join(mem.get("tags", []))
            entry_text = (
                f"## 2026-01-01 00:00\n\n"
                f"- **channel:** eval (eval)\n"
                f"- **tags:** {tag_str}\n\n"
                f"{mem['content']}"
            )
            await index_entry(config, "eval-fixture", entry_text,
                              source_type="journal")


def _count_tool_calls(history: list) -> int:
    """Count tool result messages in history."""
    return sum(1 for msg in history if msg.get("role") == "tool")


def _collect_tool_names(history: list) -> list[str]:
    """List tool names called in the (slice of) history, in call order, including duplicates."""
    names = []
    for msg in history:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            name = (call.get("function") or {}).get("name")
            if name:
                names.append(name)
    return names


def _collect_tool_calls(history: list) -> list[tuple[str, dict]]:
    """List (tool_name, parsed_args) for each tool call, in call order.

    Tool-call ``arguments`` are JSON strings on the wire; unparseable args
    degrade to an empty dict rather than raising, so assertion checks stay
    robust against malformed model output.
    """
    import json
    calls: list[tuple[str, dict]] = []
    for msg in history:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            raw = fn.get("arguments")
            args: dict = {}
            if isinstance(raw, dict):
                args = raw
            elif isinstance(raw, str) and raw.strip():
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        args = parsed
                except (ValueError, TypeError):
                    args = {}
            calls.append((name, args))
    return calls


def _count_tool_errors(history: list) -> int:
    """Count tool results that contain error indicators."""
    count = 0
    for msg in history:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", "")
        if isinstance(content, str) and "[error" in content.lower():
            count += 1
    return count


def _collect_tool_errors(history: list) -> list[str]:
    """Extract tool error messages from history."""
    errors = []
    for msg in history:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", "")
        if isinstance(content, str) and "[error" in content.lower():
            # Truncate long error messages
            errors.append(content[:200])
    return errors


def _check_workspace_assertions(test_case: dict,
                                workspace_path: Path) -> tuple[bool, str]:
    """Check post-turn workspace-state assertions.

    Reads ``test_case["expect_workspace"]`` (top-level, parallel to ``setup:``)
    so the timing is unambiguous: these run once at the end of the test, not
    per-turn. Three fields supported:

    - ``workspace_files: {rel_path: content_or_re_pattern}`` — both existence
      AND content. Strings prefixed with ``re:`` match as regex; otherwise
      bare-substring (case-insensitive) match. Use the regex form for exact
      content (anchor with ``^`` / ``$`` if needed).
    - ``workspace_file_exists: [rel_path, ...]`` — existence only.
    - ``workspace_file_absent: [rel_path, ...]`` — must NOT exist.

    All paths must be relative to ``workspace_path`` and must not escape via
    ``..`` — symmetric with ``_setup_workspace``'s ``workspace_files``.
    """
    import re

    expect_ws = test_case.get("expect_workspace")
    if not expect_ws:
        return True, ""

    workspace_root = workspace_path.resolve()

    def _resolve(rel_path: str) -> Path:
        rel = Path(rel_path)
        if rel.is_absolute():
            raise ValueError(f"expect_workspace path must be relative: {rel_path}")
        dest = (workspace_path / rel).resolve()
        if not dest.is_relative_to(workspace_root):
            raise ValueError(f"expect_workspace path escapes workspace: {rel_path}")
        return dest

    for rel_path, expected in (expect_ws.get("workspace_files") or {}).items():
        dest = _resolve(rel_path)
        if not dest.exists():
            return False, f"workspace_files: expected file {rel_path!r} to exist"
        content = dest.read_text(encoding="utf-8")
        if expected.startswith("re:"):
            if not re.search(expected[3:], content, re.IGNORECASE | re.DOTALL):
                return False, (
                    f"workspace_files[{rel_path!r}]: content did not match "
                    f"pattern {expected!r}"
                )
        elif expected.lower() not in content.lower():
            return False, (
                f"workspace_files[{rel_path!r}]: content did not contain "
                f"expected substring {expected!r}"
            )

    for rel_path in expect_ws.get("workspace_file_exists") or []:
        dest = _resolve(rel_path)
        if not dest.exists():
            return False, f"workspace_file_exists: {rel_path!r} does not exist"

    for rel_path in expect_ws.get("workspace_file_absent") or []:
        dest = _resolve(rel_path)
        if dest.exists():
            return False, f"workspace_file_absent: {rel_path!r} unexpectedly exists"

    return True, ""


def _check_assertions(test_case: dict, response: str, tool_calls: int,
                      tool_errors: int = 0,
                      tool_names: list[str] | None = None,
                      tool_calls_detail: list[tuple[str, dict]] | None = None,
                      ) -> tuple[bool, str]:
    """Check test assertions. Returns (passed, failure_reason)."""
    import re

    expect = test_case.get("expect", {})
    response_lower = response.lower()

    # response_contains: string, list (any match), or regex (prefix with "re:")
    contains = expect.get("response_contains")
    if contains:
        if isinstance(contains, str):
            contains = [contains]
        matched = False
        for c in contains:
            if c.startswith("re:"):
                if re.search(c[3:], response, re.IGNORECASE):
                    matched = True
                    break
            elif c.lower() in response_lower:
                matched = True
                break
        if not matched:
            return False, f"Expected one of {contains} in response"

    # response_contains_all: string or list (AND semantics — all must match).
    # Mirror response_contains item handling: `re:` prefix opts into regex,
    # bare strings use case-insensitive substring.
    contains_all = expect.get("response_contains_all")
    if contains_all:
        if isinstance(contains_all, str):
            contains_all = [contains_all]
        for c in contains_all:
            if c.startswith("re:"):
                if not re.search(c[3:], response, re.IGNORECASE):
                    return False, f"Expected all of {contains_all} in response, missing pattern {c!r}"
            elif c.lower() not in response_lower:
                return False, f"Expected all of {contains_all} in response, missing {c!r}"

    # response_not_contains: string or list (all must be absent)
    not_contains = expect.get("response_not_contains")
    if not_contains:
        if isinstance(not_contains, str):
            not_contains = [not_contains]
        for nc in not_contains:
            if nc.lower() in response_lower:
                return False, f"Response should not contain '{nc}'"

    max_tools = expect.get("max_tool_calls")
    if max_tools is not None and tool_calls > max_tools:
        return False, f"Too many tool calls: {tool_calls} > {max_tools}"

    max_errors = expect.get("max_tool_errors")
    if max_errors is not None and tool_errors > max_errors:
        return False, f"Too many tool errors: {tool_errors} > {max_errors}"

    names = tool_names or []
    called_list = f"[{', '.join(names)}]"

    expect_tool = expect.get("expect_tool")
    if expect_tool is not None:
        wanted = [expect_tool] if isinstance(expect_tool, str) else list(expect_tool)
        if not any(w in names for w in wanted):
            tail = f"tools called were {called_list}" if names else "no tools were called"
            return False, f"Expected one of {wanted} to be called, but {tail}"

    expect_no_tool = expect.get("expect_no_tool")
    if expect_no_tool is not None:
        forbidden = [expect_no_tool] if isinstance(expect_no_tool, str) else list(expect_no_tool)
        for f in forbidden:
            if f in names:
                return False, f"Unexpected tool called: '{f}' (called tools: {called_list})"

    count_by_name = expect.get("expect_tool_count_by_name")
    if count_by_name is not None:
        for name, want in count_by_name.items():
            got = sum(1 for n in names if n == name)
            if got != want:
                paren = f"called tools: {called_list}" if names else "no tools were called"
                return False, (
                    f"Tool count mismatch for '{name}': expected {want}, got {got} ({paren})"
                )

    # expect_tool_args: assert a tool was called with specific argument
    # values. Each spec is {tool: <name>, args: {k: v, ...}}; it passes if at
    # least one call to that tool has matching values for every listed key
    # (subset match — other args are ignored). This is the only assertion
    # that inspects call arguments rather than just names; needed to
    # disambiguate same-tool variants (e.g. canvas_new_tab widget_type).
    expect_args = expect.get("expect_tool_args")
    if expect_args is not None:
        detail = tool_calls_detail or []
        specs = [expect_args] if isinstance(expect_args, dict) else list(expect_args)
        for spec in specs:
            want_tool = spec.get("tool")
            want_args = spec.get("args") or {}
            matched = any(
                name == want_tool
                and all(args.get(k) == v for k, v in want_args.items())
                for name, args in detail
            )
            if not matched:
                got = [
                    {k: a.get(k) for k in want_args}
                    for n, a in detail if n == want_tool
                ]
                if got:
                    return False, (
                        f"Expected {want_tool} called with {want_args}, but "
                        f"matching-key args were {got}"
                    )
                return False, (
                    f"Expected {want_tool} called with {want_args}, but "
                    f"{want_tool} was not called (called: {called_list})"
                )

    return True, ""


# Accepted keys in a test case's ``setup`` block.
#
# Hand-maintained on purpose: these are consumed by five separate functions
# (``_setup_skills``, ``_seed_conversation_history``, ``_setup_workspace``,
# ``_build_test_config``, and the ``auto_confirm`` lookup in ``run_test``),
# so there is no single structure to introspect. Deriving the set by
# scraping ``setup.get(...)`` call sites would also happily accept a key
# that is read but undocumented.
#
# ``test_known_setup_keys_match_docs`` is the keeper: it asserts this set
# matches the ``setup.*`` rows in the docs/eval-loop.md table, so adding a
# key means touching both and forgetting either one fails.
_KNOWN_SETUP_KEYS = frozenset({
    "skills",
    "memories",
    "workspace_files",
    "conversation_history",
    "embeddings_fixture",
    "auto_confirm",
    "config_overrides",
})

# Bespoke setup keys folded into the generic config_overrides mechanism.
# Kept only to fail loudly — a silently-ignored key would look like a
# passing test of the wrong config, which is the failure mode the generic
# path exists to prevent.
_REMOVED_SETUP_KEYS = {
    "max_tool_iterations": "agent.max_tool_iterations",
    "reflection_enabled": "reflection.enabled",
}


def _setup_of(test_case: dict) -> dict:
    """Return a test case's ``setup`` block, normalized and validated.

    A bare ``setup:`` in YAML parses to ``None``. An empty setup block is a
    natural authoring state, so treat it as absent — but reject any other
    non-mapping instead of letting it surface later as an ``AttributeError``
    from a ``.get()`` on the wrong type.

    Unknown keys raise. Every reader goes through here, so a typo like
    ``workspace_file`` (missing the ``s``) fails the case outright instead
    of returning the ``.get()`` default and quietly skipping its fixture.
    """
    setup = test_case.get("setup")
    if setup is None:
        return {}
    if not isinstance(setup, dict):
        raise ValueError(f"setup must be a mapping, got {type(setup).__name__}")

    # Removed keys first — the migration hint is more useful than the
    # generic "unknown key" message they would otherwise fall through to.
    for old, new in _REMOVED_SETUP_KEYS.items():
        if old in setup:
            raise ValueError(
                f"setup.{old} was replaced by setup.config_overrides. "
                f"Use:\n  setup:\n    config_overrides:\n      {new}: <value>"
            )

    unknown = set(setup) - _KNOWN_SETUP_KEYS
    if unknown:
        # Stringify before sorting/joining: YAML resolves `on:` / `no:` to
        # booleans, so a plausible typo yields a non-string key. Sorting a
        # mixed-type set raises TypeError, and joining a non-str does too —
        # either would mask the validation error we're trying to report.
        names = ", ".join(sorted(str(k) for k in unknown))
        raise ValueError(
            f"unknown setup key(s): {names}. "
            f"Valid keys: {', '.join(sorted(_KNOWN_SETUP_KEYS))}"
        )
    return setup


class _Leaf:
    """Marks a resolved override value inside the nested override tree.

    Without this, a dict on the right-hand side would be ambiguous: is
    ``{"skills": {"demo": {}}}`` setting ``config.skills`` to a dict, or
    descending into a ``skills`` section? Nesting is expressed by dots in
    the *key*, so anything on the value side is always a literal.
    """

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


def _nest_overrides(flat: dict) -> dict:
    """Expand ``{"a.b": 1}`` into ``{"a": {"b": _Leaf(1)}}``."""
    nested: dict = {}
    for path, value in flat.items():
        parts = str(path).split(".")
        cursor = nested
        for i, part in enumerate(parts[:-1]):
            existing = cursor.setdefault(part, {})
            if isinstance(existing, _Leaf):
                prefix = ".".join(parts[:i + 1])
                raise ValueError(
                    f"config_overrides: path conflict — '{path}' descends into "
                    f"'{prefix}', which another override sets as a value"
                )
            cursor = existing
        last = parts[-1]
        if isinstance(cursor.get(last), dict):
            raise ValueError(
                f"config_overrides: path conflict — '{path}' is set as a value "
                f"but other overrides descend into it"
            )
        cursor[last] = _Leaf(value)
    return nested


def _apply_overrides(obj: _T, overrides: dict, path: str = "") -> _T:
    """Recursively ``dataclasses.replace`` ``obj`` from a nested override tree.

    Unknown fields raise rather than silently no-op: a typo'd path in an
    eval YAML would otherwise look like a passing test of the wrong config.
    """
    # `isinstance(obj, type)` rules out a dataclass *class* (as opposed to an
    # instance), which `replace` cannot take.
    if not dataclasses.is_dataclass(obj) or isinstance(obj, type):
        raise ValueError(
            f"config_overrides: '{path}' is not a config section, so it has "
            f"no fields to descend into"
        )
    valid = {f.name for f in dataclasses.fields(obj)}
    kwargs = {}
    for key, node in overrides.items():
        full = f"{path}.{key}" if path else key
        if key not in valid:
            raise ValueError(
                f"config_overrides: unknown config field '{full}'. "
                f"Available here: {', '.join(sorted(valid))}"
            )
        if isinstance(node, _Leaf):
            kwargs[key] = node.value
        else:
            kwargs[key] = _apply_overrides(getattr(obj, key), node, full)
    return replace(obj, **kwargs)


def _build_test_config(config: Config, test_case: dict, tmp: str) -> Config:
    """Apply per-test ``setup`` overrides on top of the base ``config``.

    ``setup.config_overrides`` maps dotted config paths to values and is
    applied via recursive ``dataclasses.replace``, so any field on ``Config``
    or any of its nested sections is reachable without touching this runner
    when new config lands:

    .. code-block:: yaml

        setup:
          config_overrides:
            vault_retrieval.mode: headlines
            agent.max_tool_iterations: 3

    The sandbox fields (``agent.data_home`` / ``agent.id``) are applied
    *last* so a case cannot redirect itself out of its temp directory.
    """
    # `_setup_of` has already rejected non-mappings, removed keys, and
    # unknown keys, so only the config_overrides shape is left to check.
    setup = _setup_of(test_case)

    # Validate on *presence*, not truthiness. A bare `config_overrides:`
    # parses to None, and `[]` / `0` / `""` are falsy too — gating on
    # truthiness would let all of them silently no-op, which is the exact
    # failure this mechanism exists to prevent. `{}` is the unambiguous way
    # to say "no overrides".
    if "config_overrides" in setup:
        raw = setup["config_overrides"]
        if not isinstance(raw, dict):
            raise ValueError(
                f"config_overrides must be a mapping of dotted paths to "
                f"values, got {type(raw).__name__}. Use `{{}}` for none."
            )
        config = _apply_overrides(config, _nest_overrides(raw))

    return replace(config, agent=replace(config.agent, data_home=tmp, id="eval"))


async def run_test(config: Config, test_case: dict) -> dict:
    """Run a single eval test case. Returns a result dict.

    Supports two formats:
    - Single turn: { input: "...", expect: {...} }
    - Multi turn: { turns: [ {input: "...", expect: {...}}, ... ] }

    Multi-turn tests share history across turns (same conversation).
    All turns must pass for the test to pass.
    """
    # Populate discovered_skills so dispatch_command can resolve `/foo` triggers.
    if not config.discovered_skills:
        from ..skills import build_skill_tool_owners
        config.discovered_skills = _discover_skills_fn(config)
        config.skill_tool_owners = build_skill_tool_owners(config.discovered_skills)

    # setup.auto_confirm: true (default) = auto-approve, false = auto-deny.
    # Resolved early so both the manager (child confirmations via typed
    # path) and the event-bus shim (parent confirmations via legacy path)
    # see the same verdict.
    auto_confirm = _setup_of(test_case).get("auto_confirm", True)

    bus = EventBus()
    manager = _EvalConversationManager(config, bus, auto_confirm=auto_confirm)
    ctx = Context(config=config, event_bus=bus)
    ctx.conv_id = "eval"
    ctx.channel_id = "eval"
    ctx.channel_name = "eval"
    ctx.thread_id = ""
    ctx.user_id = config.agent.user_id
    # Wire the manager onto the parent context so ``delegate_task`` can
    # ``enqueue_turn`` a child agent (#536). We deliberately leave
    # ``ctx.request_confirmation`` as None so parent tools take the
    # event-bus fallback (handled by ``_handle_confirm`` below); the
    # manager's ``_start_turn`` will set the child's own
    # ``request_confirmation`` to the manager-based closure.
    ctx.manager = manager

    # Set allowed tools if specified (disallowed tools return an error)
    allowed_tools = test_case.get("allowed_tools")
    if allowed_tools:
        ctx.tools.allowed = set(allowed_tools)

    # Setup fixtures
    await _setup_workspace(config, test_case)

    # Seed the conversation archive AND in-memory history if requested. Both
    # views need to agree so conversation_search reads the same data the
    # agent loop is processing.
    seeded_history = _seed_conversation_history(config, test_case)

    # Pre-activate skills
    await _setup_skills(ctx, test_case)

    import asyncio

    def _handle_confirm(event):
        if event.get("type") == "tool_confirm_request":
            asyncio.get_running_loop().create_task(bus.publish({
                "type": "tool_confirm_response",
                "context_id": event.get("context_id", ""),
                "tool": event.get("tool", ""),
                "tool_call_id": event.get("tool_call_id", ""),
                "approved": auto_confirm,
            }))
    bus.subscribe(_handle_confirm)

    # Determine turns
    if "turns" in test_case:
        turns = test_case["turns"]
    else:
        turns = [{"input": test_case["input"], "expect": test_case.get("expect", {})}]

    # Start with whatever was seeded — agent loop appends to this list.
    history = list(seeded_history)
    total_duration = 0
    all_responses = []
    overall_passed = True
    failure_reason = None

    for turn_idx, turn in enumerate(turns):
        # Reset per-turn token counters
        ctx.tokens.total_prompt = 0
        ctx.tokens.total_completion = 0

        # Snapshot history length before this turn for per-turn counting
        pre_turn_history_len = len(history)
        pre_turn_tool_calls = _count_tool_calls(history)
        pre_turn_tool_errors = _count_tool_errors(history)

        # Dispatch user-invokable commands (/foo, !foo) just like real transports
        # do — this gives us end-to-end coverage for skills with user-invocable: true.
        turn_input = turn["input"]
        cmd = await dispatch_command(ctx, turn_input)
        if cmd.mode == "inline":
            turn_input = cmd.text
        elif cmd.mode in ("help", "fork"):
            # Help/fork responses don't run the agent loop; treat cmd.text as the response.
            # This keeps assertions working for commands that don't need a turn.
            start = time.monotonic()
            response = cmd.text
            duration = time.monotonic() - start
            all_responses.append({
                "turn": turn_idx + 1,
                "input": turn["input"],
                "response": response,
                "duration_sec": round(duration, 1),
                "tool_calls": 0,
            })
            expect = turn.get("expect", {})
            if expect:
                passed, reason = _check_assertions(turn, response, 0, 0, tool_names=[])
                if not passed:
                    overall_passed = False
                    failure_reason = f"Turn {turn_idx + 1}: {reason}"
                    break
            continue
        elif cmd.mode in ("unknown", "error"):
            overall_passed = False
            failure_reason = f"Turn {turn_idx + 1}: command dispatch {cmd.mode}: {cmd.text}"
            break
        # mode == "not_command" or "inline": fall through to run_agent_turn with turn_input

        start = time.monotonic()
        result = await run_agent_turn(ctx, turn_input, history)
        response = result.text
        duration = time.monotonic() - start
        total_duration += duration

        # Per-turn counts (delta from pre-turn snapshot)
        tool_calls = _count_tool_calls(history) - pre_turn_tool_calls
        all_responses.append({
            "turn": turn_idx + 1,
            "input": turn["input"],
            "response": response,
            "duration_sec": round(duration, 1),
            "tool_calls": tool_calls,
        })

        # Check assertions for this turn
        expect = turn.get("expect", {})
        if expect:
            tool_errors = _count_tool_errors(history) - pre_turn_tool_errors
            turn_slice = history[pre_turn_history_len:]
            tool_names = _collect_tool_names(turn_slice)
            passed, reason = _check_assertions(turn, response, tool_calls, tool_errors,
                                               tool_names=tool_names,
                                               tool_calls_detail=_collect_tool_calls(turn_slice))
            if not passed:
                overall_passed = False
                # Collect errors from this turn's messages only
                error_details = _collect_tool_errors(history[pre_turn_history_len:])
                detail_str = "; ".join(error_details) if error_details else ""
                failure_reason = f"Turn {turn_idx + 1}: {reason}"
                if detail_str:
                    failure_reason += f"\n         Errors: {detail_str}"
                break

    # Post-turn workspace-state checks (#352). Run once at end-of-test so
    # the timing matches the field name's promise. Only check if everything
    # before this passed — first failure still wins.
    if overall_passed:
        ws_passed, ws_reason = _check_workspace_assertions(
            test_case, config.workspace_path,
        )
        if not ws_passed:
            overall_passed = False
            failure_reason = ws_reason

    # Gather cumulative metrics
    prompt_tokens = ctx.tokens.total_prompt
    completion_tokens = ctx.tokens.total_completion
    total_tool_calls = _count_tool_calls(history)

    # For single-turn, keep flat response; for multi-turn, show last response
    final_response = all_responses[-1]["response"] if all_responses else ""

    result = {
        "name": test_case["name"],
        "status": "pass" if overall_passed else "fail",
        "duration_sec": round(total_duration, 1),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "tool_calls": total_tool_calls,
        "response": final_response,
        "failure_reason": failure_reason,
    }

    # Include turn details for multi-turn tests
    if len(turns) > 1:
        result["turns"] = all_responses

    # Include full conversation history for debugging failed tests
    if not overall_passed:
        result["history"] = history

    return result


async def run_eval(yaml_data: list[dict], config: Config,
                   model: str | None = None,
                   verbose: bool = False,
                   concurrency: int = 4) -> tuple[dict, str, str]:
    """Run all test cases and return (results, timestamp, model_name).

    Tests run concurrently (up to `concurrency` at a time) but results
    are printed in test order after all complete.
    """
    import asyncio
    import tempfile

    if model:
        # If model matches a model config, set it as the default
        if model in config.model_configs:
            config = replace(config, default_model=model)
        else:
            config = replace(config, llm=replace(config.llm, model=model))

    effective_model = config.default_model or config.llm.model
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")

    total = len(yaml_data)
    semaphore = asyncio.Semaphore(concurrency)

    completed = 0

    async def _run_one(i: int, test_case: dict) -> dict:
        nonlocal completed
        async with semaphore:
            with tempfile.TemporaryDirectory() as tmp:
                test_config = _build_test_config(config, test_case, tmp)
                result = await run_test(test_config, test_case)
                completed += 1
                status = "✓" if result["status"] == "pass" else "✗"
                print(f"  {status} {completed}/{total} {test_case['name']}", flush=True)
                return result

    # Run all tests concurrently
    tasks = [_run_one(i, tc) for i, tc in enumerate(yaml_data)]
    test_results = await asyncio.gather(*tasks, return_exceptions=True)

    print()  # blank line before summary

    # Print results in order
    results = {
        "timestamp": datetime.now().isoformat(),
        "model": effective_model,
        "tests": [],
        "summary": {"total": 0, "passed": 0, "failed": 0,
                     "duration_sec": 0, "total_tokens": 0},
    }

    for i, result in enumerate(test_results):
        name = yaml_data[i]["name"]

        if isinstance(result, BaseException):
            result = {
                "name": name,
                "status": "fail",
                "duration_sec": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "tool_calls": 0,
                "response": "",
                "failure_reason": f"Exception: {result}",
            }

        status = "PASS" if result["status"] == "pass" else "FAIL"
        tokens = result["prompt_tokens"] + result["completion_tokens"]
        tools = result["tool_calls"]
        dur = result["duration_sec"]
        pad = "." * max(1, 50 - len(name))
        print(f"[{i+1}/{total}] {name} {pad} {status}  ({dur}s, {tokens} tokens, {tools} tools)")

        if verbose and result.get("response"):
            print(f"         Response: {result['response'][:200]}")

        if result["status"] == "fail":
            print(f"         {result.get('failure_reason', '')}")

        results["tests"].append(result)
        results["summary"]["total"] += 1
        results["summary"]["passed"] += 1 if result["status"] == "pass" else 0
        results["summary"]["failed"] += 1 if result["status"] == "fail" else 0
        results["summary"]["duration_sec"] += dur
        results["summary"]["total_tokens"] += tokens

    results["summary"]["duration_sec"] = round(results["summary"]["duration_sec"], 1)
    return results, timestamp, effective_model
