# Workflow Engine — Phase-Turn Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the conv-scoped workflow engine's "LLM drives flow / tool calls handle transitions" model with an "engine drives flow / LLM drives routing only" model where each phase fires as its own scheduled turn via the existing `ConversationManager`. Resolves the narrate-stall failure mode the cheap experiment confirmed is general.

**Architecture:** New `TurnKind.WORKFLOW_PHASE` + new `ComposerMode.WORKFLOW_PHASE`. `tool_workflow_start` / `tool_phase_advance` end the turn and enqueue the next phase as a manager-scheduled turn instead of dispatching subagents synchronously. Phase prompt becomes the **system prompt** (full replace) for that turn — no more general decafclaw preamble in worker mode. Engine handles the mechanics; LLM picks `phase_advance(target)`. Phase-internal nudge loop in `TurnRunner` catches LLM-stops-without-advancing.

**Tech Stack:** Python 3.13, asyncio, dataclasses, pytest, pytest-asyncio. Existing `ConversationManager` / `TurnRunner` / `ContextComposer`. No new dependencies.

**Parent spec:** [`spec.md`](spec.md). **Settled open questions:** see [`notes.md`](notes.md) "All settled (2026-05-30)" section.

**Branch:** `feat/255-workflow-engine` (PR #557). All commits land on this branch — same PR.

---

## Scope Check

This is one cohesive subsystem rework — turn scheduling, composer mode, tool surface, and subagent dispatch are tightly coupled around the phase-turn model. **Not** broken into separate plans.

## File Structure

### Modified

| File | Responsibility |
|---|---|
| `src/decafclaw/config_types.py` | Add `WorkflowConfig` dataclass; nest under `Config.workflow`. |
| `src/decafclaw/conversation_manager.py` | Add `TurnKind.WORKFLOW_PHASE`. Dispatch USER-style ctx with `task_mode="workflow_phase"`. Persist conv state on WORKFLOW_PHASE turns. |
| `src/decafclaw/context.py` | Add `parent_conv_id`, `workflow_advanced_this_turn`, `phase_continuations` fields. |
| `src/decafclaw/context_composer.py` | Add `ComposerMode.WORKFLOW_PHASE`. New `_compose_workflow_phase_system_prompt` builder. USER-turn-with-active-workflow auto-promotes to WORKFLOW_PHASE composition. |
| `src/decafclaw/agent.py` | `TurnRunner` phase-internal nudge loop (inject synthetic user-role nudge, bounded by `max_phase_continuations`). Recognize `task_mode="workflow_phase"` to select composer mode + nudge behavior. |
| `src/decafclaw/tools/workflow_tools.py` | `tool_workflow_start` accepts `params` arg, enqueues phase turn, `end_turn=True`. `tool_phase_advance` enqueues next phase turn, `end_turn=True`. Sets `ctx.workflow_advanced_this_turn`. Drop `_render_phase_handoff` (superseded by system-prompt-from-phase). |
| `src/decafclaw/workflow/types.py` | `WorkflowState.params: dict`. `PhaseDef.max_continuations: int \| None`. |
| `src/decafclaw/workflow/loader.py` | Parse `max-continuations:` (frontmatter, kebab-case). |
| `src/decafclaw/workflow/engine.py` | `_enqueue_phase_turn` helper. Reframe transitions to use it. Delete sync subagent dispatcher; remove `dispatch_subagent_if_needed` and `dispatch_and_finalize_subagent`. |
| `src/decafclaw/workflow/subagent.py` | Replace `_run_child` synchronous dispatcher with `_setup_child_for_phase` callable used during CHILD_AGENT enqueue from the engine. Delete the `child_ctx.conv_id = parent.conv_id` override (Bug 2); use `parent_conv_id` field. Drop the strong-framing wrapper added in `50a528b` — phase-as-system-prompt replaces it. Keep `_latest_parent_user_message` only as fallback when no `params.topic` is set. |
| `src/decafclaw/workflow/conv_state.py` | Path helpers resolve to `parent_conv_id or conv_id`. |
| `src/decafclaw/skills/workflow_demo/SKILL.md` | Body instructs parent to pass topic via `workflow_start(params={"topic": ...})`. |
| `src/decafclaw/skills/workflow_demo/phases/gather.md` | Replace "research the topic given by the parent agent" with `{{params.topic}}`. |
| `docs/workflows.md` | Document the phase-turn model. |
| `CLAUDE.md` | One-line update to workflow conventions if needed. |
| `tests/test_workflow_*.py` | Migrate suite for engine-enqueued dispatch. Add WORKFLOW_PHASE mode tests, params-interpolation tests, nudge-loop tests. |

### New

None. All logic fits in existing modules.

---

## Task ordering

Tasks are dependency-ordered. Each task is small and committable independently. Per `make check` discipline, run `make lint && make typecheck && make test` before every commit.

---

### Task 1: Add `WorkflowConfig` to `config_types.py`

**Files:**
- Modify: `src/decafclaw/config_types.py`
- Modify: `src/decafclaw/config.py` (wire into `Config`)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_workflow_config_defaults():
    from decafclaw.config import load_config
    cfg = load_config()
    assert cfg.workflow.max_phase_continuations == 2
```

- [ ] **Step 2: Run test to verify it fails**

`uv run pytest tests/test_config.py::test_workflow_config_defaults -v` → AttributeError on `cfg.workflow`.

- [ ] **Step 3: Add the dataclass**

In `src/decafclaw/config_types.py`, add (after `AgentConfig` or similar nested config):

```python
@dataclass
class WorkflowConfig:
    """Workflow-engine-specific runtime tunables."""
    max_phase_continuations: int = 2  # 2 nudges = 3 total attempts per phase
```

- [ ] **Step 4: Wire into `Config`**

In `src/decafclaw/config.py`, add `workflow: WorkflowConfig = field(default_factory=WorkflowConfig)` to the `Config` dataclass (near other nested config fields). Add import for `WorkflowConfig`.

- [ ] **Step 5: Run test to verify it passes**

`uv run pytest tests/test_config.py::test_workflow_config_defaults -v` → PASS.

- [ ] **Step 6: Run full check**

`make check && make test` → all green.

- [ ] **Step 7: Commit**

```bash
git add src/decafclaw/config_types.py src/decafclaw/config.py tests/test_config.py
git commit -m "feat(workflow): WorkflowConfig with max_phase_continuations default"
```

---

### Task 2: Add `WorkflowState.params` + `PhaseDef.max_continuations`

**Files:**
- Modify: `src/decafclaw/workflow/types.py:55-118`
- Test: `tests/test_workflow_types.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workflow_types.py`:

```python
def test_workflow_state_params_roundtrip():
    from decafclaw.workflow.types import WorkflowState, RunStatus
    s = WorkflowState(
        workflow="demo", status=RunStatus.RUNNING,
        current_phase="a", created_at="2026-05-31T00:00:00",
        updated_at="2026-05-31T00:00:00", params={"topic": "movable type"},
    )
    raw = s.to_json()
    back = WorkflowState.from_json(raw)
    assert back.params == {"topic": "movable type"}


def test_phase_def_max_continuations_default_none():
    from decafclaw.workflow.types import PhaseDef, PhaseKind
    p = PhaseDef(
        id="x", kind=PhaseKind.INLINE, prompt="", tools=[],
        next_phases=[], gate=None, outputs=(),
        subagent_skill=None, context_profile={},
    )
    assert p.max_continuations is None
```

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/test_workflow_types.py -v` → AttributeError / TypeError on missing fields.

- [ ] **Step 3: Add the fields**

In `src/decafclaw/workflow/types.py`:

```python
@dataclass(frozen=True)
class PhaseDef:
    """A single phase in a workflow definition."""

    id: str
    kind: PhaseKind
    prompt: str
    tools: list[str]
    next_phases: list[EdgeDef]
    gate: None
    outputs: tuple[str, ...]
    subagent_skill: str | None
    context_profile: dict
    max_continuations: int | None = None  # None = use config default

    @property
    def is_terminal(self) -> bool:
        return not self.next_phases
```

```python
@dataclass
class WorkflowState:
    workflow: str
    status: RunStatus
    current_phase: str
    created_at: str
    updated_at: str
    history: list[dict] = field(default_factory=list)
    pending_gate: dict | None = None
    pending_subagent: dict | None = None
    error: str | None = None
    params: dict = field(default_factory=dict)
    # ... to_json / from_json already use asdict / **d so the new field
    # is round-trip-safe automatically — no method changes needed.
```

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/test_workflow_types.py -v` → PASS.

- [ ] **Step 5: Run full workflow suite**

`uv run pytest tests/ -k workflow` → all green (loader/engine tests use the existing `PhaseDef`/`WorkflowState` constructors; the new fields have defaults so they shouldn't break anything).

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/workflow/types.py tests/test_workflow_types.py
git commit -m "feat(workflow): WorkflowState.params + PhaseDef.max_continuations"
```

---

### Task 3: Loader parses `max-continuations:` from phase frontmatter

**Files:**
- Modify: `src/decafclaw/workflow/loader.py`
- Test: `tests/test_workflow_loader.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workflow_loader.py`:

```python
def test_loader_parses_max_continuations(tmp_path: Path):
    skill_dir = tmp_path / "demo"
    (skill_dir / "phases").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\nkind: workflow\nworkflow:\n  initial-phase: a\n---\n"
    )
    (skill_dir / "phases" / "a.md").write_text(
        "---\nkind: inline\ntools: []\nmax-continuations: 4\n"
        "next-phases: []\n---\nbody\n"
    )
    from decafclaw.workflow.loader import load_workflow
    wf = load_workflow(skill_dir)
    assert wf.phase("a").max_continuations == 4


def test_loader_max_continuations_defaults_to_none(tmp_path: Path):
    skill_dir = tmp_path / "demo"
    (skill_dir / "phases").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\nkind: workflow\nworkflow:\n  initial-phase: a\n---\n"
    )
    (skill_dir / "phases" / "a.md").write_text(
        "---\nkind: inline\ntools: []\nnext-phases: []\n---\nbody\n"
    )
    from decafclaw.workflow.loader import load_workflow
    wf = load_workflow(skill_dir)
    assert wf.phase("a").max_continuations is None
```

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/test_workflow_loader.py::test_loader_parses_max_continuations -v` → AssertionError or attribute error.

- [ ] **Step 3: Parse the field**

In `src/decafclaw/workflow/loader.py`, find the `PhaseDef(...)` construction inside the phase-loading function (likely `_parse_phase` or `load_workflow`). Add:

```python
max_cont = meta.get("max-continuations")
if max_cont is not None and not isinstance(max_cont, int):
    raise LoaderError(
        f"phase '{phase_id}': max-continuations must be int, got "
        f"{type(max_cont).__name__}")
```

Pass `max_continuations=max_cont` to the `PhaseDef(...)` constructor.

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/test_workflow_loader.py -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/workflow/loader.py tests/test_workflow_loader.py
git commit -m "feat(workflow): loader parses max-continuations: from phase frontmatter"
```

---

### Task 4: Add `TurnKind.WORKFLOW_PHASE` enum value

**Files:**
- Modify: `src/decafclaw/conversation_manager.py:73-106`
- Test: `tests/test_conversation_manager.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_conversation_manager.py`:

```python
def test_workflow_phase_turnkind_state_persists():
    from decafclaw.conversation_manager import (
        TurnKind, STATE_PERSIST_KINDS, TASK_KINDS, KIND_TASK_MODE,
    )
    assert TurnKind.WORKFLOW_PHASE in STATE_PERSIST_KINDS, (
        "WORKFLOW_PHASE fires on a persistent conv — its skills/flags "
        "must survive across phase turns just like USER turns do.")
    # WORKFLOW_PHASE is NOT a background TASK_KIND — it uses a USER-style
    # ctx so the manager wires transports / history / state restoration.
    assert TurnKind.WORKFLOW_PHASE not in TASK_KINDS
    assert TurnKind.WORKFLOW_PHASE not in KIND_TASK_MODE
```

- [ ] **Step 2: Run test to verify it fails**

`uv run pytest tests/test_conversation_manager.py::test_workflow_phase_turnkind_state_persists -v` → AttributeError on `TurnKind.WORKFLOW_PHASE`.

- [ ] **Step 3: Add the enum value + state-persist entry**

In `src/decafclaw/conversation_manager.py`:

```python
class TurnKind(Enum):
    """Classification of agent turn origins."""

    USER = "user"
    HEARTBEAT_SECTION = "heartbeat_section"
    SCHEDULED_TASK = "scheduled_task"
    CHILD_AGENT = "child_agent"
    WAKE = "wake"
    WORKFLOW_PHASE = "workflow_phase"
```

```python
STATE_PERSIST_KINDS = {TurnKind.USER, TurnKind.WAKE, TurnKind.WORKFLOW_PHASE}
```

`TASK_KINDS` and `KIND_TASK_MODE` deliberately do **not** include WORKFLOW_PHASE — they're for one-shot background contexts (heartbeat / scheduled / child / wake). WORKFLOW_PHASE fires on a real user conv and needs USER-style context construction.

- [ ] **Step 4: Run test to verify it passes**

`uv run pytest tests/test_conversation_manager.py::test_workflow_phase_turnkind_state_persists -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/conversation_manager.py tests/test_conversation_manager.py
git commit -m "feat(workflow): TurnKind.WORKFLOW_PHASE enum + STATE_PERSIST entry"
```

---

### Task 5: Add `Context.parent_conv_id` + workflow-loop fields

**Files:**
- Modify: `src/decafclaw/context.py:70-110`
- Test: `tests/test_context.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_context.py`:

```python
def test_context_workflow_fields_default():
    from decafclaw.context import Context
    from decafclaw.events import EventBus
    ctx = Context(config=None, event_bus=EventBus())
    assert ctx.parent_conv_id == ""
    assert ctx.workflow_advanced_this_turn is False
    assert ctx.phase_continuations == 0


def test_context_workflow_fields_carry_through_tool_call_fork():
    """fork_for_tool_call uses copy.copy(self), so new fields must
    propagate automatically — verifies the documented convention."""
    from decafclaw.context import Context
    from decafclaw.events import EventBus
    parent = Context(config=None, event_bus=EventBus())
    parent.parent_conv_id = "parent-123"
    parent.phase_continuations = 1
    child = parent.fork_for_tool_call("call-abc")
    assert child.parent_conv_id == "parent-123"
    assert child.phase_continuations == 1
```

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/test_context.py -k workflow -v` → AttributeError on missing fields.

- [ ] **Step 3: Add the fields**

In `src/decafclaw/context.py`, inside `Context.__init__`, after `self.task_mode = ""`:

```python
# Workflow-engine state (see docs/workflows.md). All three are
# touched by tool_workflow_start / tool_phase_advance and the
# TurnRunner phase-internal nudge loop.
self.parent_conv_id: str = ""  # set on child turns to parent's conv_id; conv_state path helpers resolve via this
self.workflow_advanced_this_turn: bool = False  # set by tool_phase_advance; reset at turn start
self.phase_continuations: int = 0  # incremented by TurnRunner nudge loop
```

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/test_context.py -k workflow -v` → all PASS.

- [ ] **Step 5: Verify no other tests broke**

`make test` → all green. (Context's `__init__` is touched on every turn — must not regress.)

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/context.py tests/test_context.py
git commit -m "feat(workflow): Context.parent_conv_id + workflow-loop fields"
```

---

### Task 6: `conv_state` path helpers consult `parent_conv_id`

**Files:**
- Modify: `src/decafclaw/workflow/conv_state.py`
- Test: `tests/test_workflow_conv_state.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workflow_conv_state.py`:

```python
def test_artifacts_dir_resolves_via_parent_conv_id(tmp_path: Path):
    """A child agent's ctx with parent_conv_id set should resolve
    workflow paths to the parent's conversation dir, not its own."""
    from decafclaw.workflow.conv_state import artifacts_dir
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(workspace_path=workspace)
    ctx = SimpleNamespace(
        config=config,
        conv_id="child-conv",
        parent_conv_id="parent-conv",
    )
    art = artifacts_dir(ctx)
    assert "parent-conv" in str(art)
    assert "child-conv" not in str(art)


def test_artifacts_dir_falls_back_to_conv_id_when_parent_empty(tmp_path: Path):
    from decafclaw.workflow.conv_state import artifacts_dir
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(workspace_path=workspace)
    ctx = SimpleNamespace(
        config=config,
        conv_id="main-conv",
        parent_conv_id="",
    )
    art = artifacts_dir(ctx)
    assert "main-conv" in str(art)
```

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/test_workflow_conv_state.py -k parent_conv_id -v` → tests reference behavior not yet implemented.

- [ ] **Step 3: Update path helpers**

In `src/decafclaw/workflow/conv_state.py`, find the helper that computes the conv root (likely a small `_conv_root(ctx)` or inline expression in `artifacts_dir`, `_workflow_state_path`, `conv_lock`). Add:

```python
def _resolve_conv_id(ctx) -> str:
    """Return the conv_id workflow paths should resolve against.

    Child agents set ``parent_conv_id`` so their tools' file I/O
    lands in the parent's directory (where the engine looks for
    declared outputs). Main agents have ``parent_conv_id=""`` and
    fall through to their own ``conv_id``.
    """
    return getattr(ctx, "parent_conv_id", "") or ctx.conv_id
```

Replace every direct `ctx.conv_id` lookup inside `conv_state.py` that's used to build a path with `_resolve_conv_id(ctx)`.

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/test_workflow_conv_state.py -v` → all PASS, including the existing tests (which don't set `parent_conv_id` and thus fall through to the same behavior as before).

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/workflow/conv_state.py tests/test_workflow_conv_state.py
git commit -m "feat(workflow): conv_state paths resolve via parent_conv_id when set"
```

---

### Task 7: Add `ComposerMode.WORKFLOW_PHASE` enum value

**Files:**
- Modify: `src/decafclaw/context_composer.py:36-55`
- Test: `tests/test_context_composer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_context_composer.py`:

```python
def test_workflow_phase_composer_mode_exists():
    from decafclaw.context_composer import ComposerMode
    assert ComposerMode.WORKFLOW_PHASE.value == "workflow_phase"
```

- [ ] **Step 2: Run test to verify it fails**

`uv run pytest tests/test_context_composer.py::test_workflow_phase_composer_mode_exists -v` → AttributeError.

- [ ] **Step 3: Add enum value**

In `src/decafclaw/context_composer.py`, in the `ComposerMode` enum:

```python
class ComposerMode(enum.Enum):
    INTERACTIVE = "interactive"
    HEARTBEAT = "heartbeat"
    SCHEDULED = "scheduled"
    CHILD_AGENT = "child_agent"
    WORKFLOW_PHASE = "workflow_phase"
```

- [ ] **Step 4: Run test to verify it passes**

`uv run pytest tests/test_context_composer.py::test_workflow_phase_composer_mode_exists -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/context_composer.py tests/test_context_composer.py
git commit -m "feat(workflow): ComposerMode.WORKFLOW_PHASE enum value"
```

---

### Task 8: Workflow-phase system prompt builder + `{{params.X}}` interpolation

**Files:**
- Modify: `src/decafclaw/context_composer.py` (add helper + dispatch in `compose()`)
- Test: `tests/test_context_composer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_context_composer.py`:

```python
def test_workflow_phase_system_prompt_replaces_general(tmp_path: Path):
    """In WORKFLOW_PHASE mode, the system prompt is the phase body
    wrapped in a <workflow_phase> block, with NO general decafclaw
    preamble (no SOUL.md / AGENT.md). Phase body must be present;
    'You are decafclaw' frame must be absent."""
    from decafclaw.context_composer import (
        ContextComposer, ComposerMode,
    )
    # Set up a minimal active workflow on a real-ish ctx fixture.
    # (Reuse whichever helper test_context_composer.py uses to
    # construct a ctx; add parent_conv_id="" and a workflow.json
    # with current_phase pointing at an inline phase.)
    ctx = _composer_ctx(tmp_path, mode=ComposerMode.WORKFLOW_PHASE)
    # Seed workflow state pointing at phase "draft" of a test workflow
    # whose draft.md body says "DRAFT PHASE BODY MARKER".
    _seed_workflow(ctx, phase="draft", body="DRAFT PHASE BODY MARKER")

    composer = ContextComposer()
    composed = await composer.compose(ctx, [], mode=ComposerMode.WORKFLOW_PHASE)
    sys_msg = composed.messages[0]
    assert "DRAFT PHASE BODY MARKER" in sys_msg["content"]
    assert "<workflow_phase>" in sys_msg["content"]
    assert "You are decafclaw" not in sys_msg["content"]


def test_workflow_phase_system_prompt_interpolates_params(tmp_path: Path):
    """{{params.topic}} in a phase body resolves from WorkflowState.params."""
    from decafclaw.context_composer import (
        ContextComposer, ComposerMode,
    )
    ctx = _composer_ctx(tmp_path, mode=ComposerMode.WORKFLOW_PHASE)
    _seed_workflow(
        ctx, phase="draft",
        body="Draft a brief on {{params.topic}}.",
        params={"topic": "movable type"},
    )
    composer = ContextComposer()
    composed = await composer.compose(ctx, [], mode=ComposerMode.WORKFLOW_PHASE)
    assert "Draft a brief on movable type." in composed.messages[0]["content"]
    assert "{{params.topic}}" not in composed.messages[0]["content"]
```

(`_composer_ctx` and `_seed_workflow` are test helpers — add them as fixtures alongside the new tests. They set up a `Context` with `manager=None`, real `Config`, a workspace dir, and write a `workflow.json` + register a `WorkflowDef` whose phase's body matches the test's input. Pattern matches existing fixtures in `test_workflow_conv_state.py`.)

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/test_context_composer.py -k workflow_phase -v` → fails because composer doesn't handle WORKFLOW_PHASE yet.

- [ ] **Step 3: Add the helper**

In `src/decafclaw/context_composer.py`, add a module-level helper (near `_compose_system_prompt`):

```python
def _interpolate_phase_params(body: str, params: dict) -> str:
    """Replace `{{params.<key>}}` tokens with values from ``params``.

    Missing keys render as the literal placeholder so the LLM can see
    something's wrong (rather than silently dropping). Non-string
    values are stringified via ``str()``.
    """
    import re
    def _sub(match):
        key = match.group(1)
        if key in params:
            return str(params[key])
        return match.group(0)  # leave the placeholder visible
    return re.sub(r"\{\{params\.(\w+)\}\}", _sub, body)


def _build_workflow_phase_system_prompt(ctx) -> str | None:
    """Return the system-prompt content for a WORKFLOW_PHASE turn,
    or None if no workflow is active.

    Structure: a single ``<workflow_phase>`` XML block wrapping the
    phase body (with ``{{params.X}}`` interpolation), an annotated
    transitions table, and a one-line worker-mode frame. No general
    decafclaw preamble — the phase IS the system prompt.
    """
    from .workflow import registry
    from .workflow.conv_state import load_workflow_state

    state = load_workflow_state(ctx)
    if state is None:
        return None
    wf = registry.get(state.workflow)
    if wf is None:
        return None
    phase = wf.phase(state.current_phase)
    if phase is None:
        return None

    body = _interpolate_phase_params(phase.prompt or "", state.params or {})

    parts = [
        "<workflow_phase>",
        f"You are operating in workflow mode. Workflow: '{wf.name}'. "
        f"Active phase: '{phase.id}'.",
        "",
        "## Phase body",
        "",
        body.strip(),
        "",
    ]
    if phase.next_phases:
        parts.append("## Available transitions (call `phase_advance` with one):")
        for edge in phase.next_phases:
            when = (edge.when or "(only option)").strip()
            gated = " [REQUIRES USER REVIEW]" if edge.gate else ""
            parts.append(f"  - `phase_advance(target_phase_id=\"{edge.id}\")`{gated}")
            for ln in when.splitlines():
                parts.append(f"      {ln}")
        parts.append("")
    elif phase.is_terminal:
        parts.append("## Terminal phase")
        parts.append("This is the final phase. Complete the work; no `phase_advance` needed.")
        parts.append("")

    parts.append("</workflow_phase>")
    return "\n".join(parts)
```

In `ContextComposer.compose()`, near the start (before the existing system-prompt assembly), add:

```python
if mode == ComposerMode.WORKFLOW_PHASE:
    wp_sys = _build_workflow_phase_system_prompt(ctx)
    if wp_sys is not None:
        messages = [{"role": "system", "content": wp_sys}]
        # Skip the general system-prompt builder, skip workflow overlay
        # (it IS the system prompt now), skip memory retrieval below by
        # treating WORKFLOW_PHASE the same as CHILD_AGENT/HEARTBEAT in
        # the existing skip_modes sets.
        # Append history + return composed result.
        ...
```

(The exact integration depends on `compose()`'s current shape — the implementer should read the function and place the early-return / branch where it fits. The key invariants: WORKFLOW_PHASE mode skips general preamble, skips memory retrieval, skips workflow overlay, and the system message is the `_build_workflow_phase_system_prompt` output.)

Also add `ComposerMode.WORKFLOW_PHASE` to each existing `skip_modes` set in `context_composer.py` (lines ~697, ~849, ~915 — there are three skip_modes definitions). It behaves like `CHILD_AGENT`/`HEARTBEAT`/`SCHEDULED` for memory retrieval, vault overlay, and similar inhibitions.

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/test_context_composer.py -k workflow_phase -v` → all PASS.

- [ ] **Step 5: Run full suite**

`make check && make test` → all green.

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/context_composer.py tests/test_context_composer.py
git commit -m "feat(workflow): WORKFLOW_PHASE composer mode with phase-as-system-prompt + params interpolation"
```

---

### Task 9: USER turns mid-workflow apply WORKFLOW_PHASE composition

**Files:**
- Modify: `src/decafclaw/context_composer.py` (in `compose()` mode dispatch)
- Test: `tests/test_context_composer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_context_composer.py`:

```python
def test_user_turn_mid_workflow_uses_phase_system_prompt(tmp_path: Path):
    """When mode=INTERACTIVE but workflow.json shows an active workflow,
    compose() promotes to WORKFLOW_PHASE-style composition. The agent
    in a user turn during an active workflow stays in worker mode."""
    from decafclaw.context_composer import (
        ContextComposer, ComposerMode,
    )
    ctx = _composer_ctx(tmp_path, mode=ComposerMode.INTERACTIVE)
    _seed_workflow(ctx, phase="draft", body="DRAFT MARKER")

    composer = ContextComposer()
    composed = await composer.compose(ctx, [], mode=ComposerMode.INTERACTIVE)
    sys = composed.messages[0]["content"]
    assert "DRAFT MARKER" in sys
    assert "<workflow_phase>" in sys


def test_user_turn_without_workflow_uses_general_preamble(tmp_path: Path):
    """When no workflow is active, INTERACTIVE mode behaves as before
    (general decafclaw preamble)."""
    from decafclaw.context_composer import (
        ContextComposer, ComposerMode,
    )
    ctx = _composer_ctx(tmp_path, mode=ComposerMode.INTERACTIVE)
    # No _seed_workflow call.
    composer = ContextComposer()
    composed = await composer.compose(ctx, [], mode=ComposerMode.INTERACTIVE)
    sys = composed.messages[0]["content"]
    assert "<workflow_phase>" not in sys
    # General preamble should be back.
```

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/test_context_composer.py -k user_turn_mid_workflow -v` → fails.

- [ ] **Step 3: Add the promotion**

In `compose()`, replace the earlier (Task 8) branch:

```python
# If a workflow is active in this conv, treat the turn as a phase turn
# regardless of mode — applies to INTERACTIVE (user typing mid-workflow)
# and explicit WORKFLOW_PHASE turns alike. CHILD_AGENT for a subagent
# phase ALSO uses this — it's the only mode that gets the workflow
# system prompt instead of inheriting general preamble.
should_use_workflow_prompt = mode in (
    ComposerMode.WORKFLOW_PHASE,
    ComposerMode.INTERACTIVE,
    ComposerMode.CHILD_AGENT,
)
if should_use_workflow_prompt:
    wp_sys = _build_workflow_phase_system_prompt(ctx)
    if wp_sys is not None:
        # ... (build composed result with wp_sys as the system message,
        # skipping general preamble + workflow overlay).
```

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/test_context_composer.py -k workflow -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/context_composer.py tests/test_context_composer.py
git commit -m "feat(workflow): USER/CHILD turns mid-workflow auto-promote to phase composition"
```

---

### Task 10: `ConversationManager` dispatches `WORKFLOW_PHASE` with USER-style ctx + task_mode

**Files:**
- Modify: `src/decafclaw/conversation_manager.py:1290-1395`
- Test: `tests/test_conversation_manager.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_conversation_manager.py`:

```python
@pytest.mark.asyncio
async def test_enqueue_workflow_phase_builds_user_style_ctx(tmp_path: Path):
    """Enqueuing a WORKFLOW_PHASE turn must build a USER-style ctx
    (not Context.for_task) so transports/history/state-restore work,
    but with task_mode='workflow_phase' so the composer mode dispatch
    selects WORKFLOW_PHASE composition."""
    # Use the existing test_conversation_manager helper to spin up a
    # manager + mock LLM + capture the ctx passed to run_agent_turn.
    captured_ctx = []
    async def fake_run_turn(ctx, **kw):
        captured_ctx.append(ctx)
        return None
    # ... wire fake_run_turn in (existing tests do this via monkeypatch).
    # Then:
    fut = await manager.enqueue_turn(
        "conv-test",
        kind=TurnKind.WORKFLOW_PHASE,
        prompt="",
        user_id="u",
    )
    await fut
    assert captured_ctx, "run_agent_turn should have been called"
    ctx = captured_ctx[0]
    assert ctx.conv_id == "conv-test"
    assert ctx.task_mode == "workflow_phase"
    assert ctx.user_id == "u"
```

- [ ] **Step 2: Run test to verify it fails**

`uv run pytest tests/test_conversation_manager.py::test_enqueue_workflow_phase_builds_user_style_ctx -v` → fails.

- [ ] **Step 3: Add the dispatch branch**

In `conversation_manager.py`'s dispatch (around line 1335), modify:

```python
# -- Build context based on turn kind -----------------------------------
if kind in TASK_KINDS:
    # ... existing Context.for_task path ...
elif kind is TurnKind.WORKFLOW_PHASE:
    # USER-style ctx (persistent conv, history, state-restore), but
    # task_mode marks the turn for the composer's WORKFLOW_PHASE
    # path. workflow_advanced_this_turn resets to False at turn
    # start; tool_phase_advance flips it during execution.
    ctx = Context(config=self.config, event_bus=self.event_bus)
    ctx.user_id = user_id
    ctx.channel_id = conv_id
    ctx.conv_id = conv_id
    ctx.cancelled = state.cancel_event
    ctx.wiki_page = wiki_page
    ctx.task_mode = "workflow_phase"
    self._restore_per_conv_state(state, ctx)
else:
    # USER turn — existing behavior.
    ...
```

- [ ] **Step 4: Run test to verify it passes**

`uv run pytest tests/test_conversation_manager.py::test_enqueue_workflow_phase_builds_user_style_ctx -v` → PASS.

- [ ] **Step 5: Run full suite**

`make check && make test` → all green.

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/conversation_manager.py tests/test_conversation_manager.py
git commit -m "feat(workflow): ConversationManager dispatches WORKFLOW_PHASE as USER-style ctx with task_mode"
```

---

### Task 11: `run_agent_turn` selects `ComposerMode.WORKFLOW_PHASE` from `task_mode`

**Files:**
- Modify: `src/decafclaw/agent.py` (look for the existing `task_mode → ComposerMode` mapping)
- Test: `tests/test_agent.py`

- [ ] **Step 1: Read existing mapping**

Find the block in `agent.py` that picks `ComposerMode` based on `ctx.task_mode`. It likely looks like:

```python
mode = ComposerMode.INTERACTIVE
if ctx.task_mode == "heartbeat":
    mode = ComposerMode.HEARTBEAT
elif ctx.task_mode == "scheduled":
    mode = ComposerMode.SCHEDULED
elif ctx.task_mode == "child_agent":
    mode = ComposerMode.CHILD_AGENT
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_agent.py`:

```python
def test_agent_selects_workflow_phase_composer_mode():
    """A ctx with task_mode='workflow_phase' should drive
    ContextComposer with ComposerMode.WORKFLOW_PHASE."""
    # Use whatever helper test_agent.py has for inspecting the
    # composer mode passed to compose(); pattern-match on existing
    # heartbeat/scheduled test if present.
    ...
```

(If `test_agent.py` doesn't have a clean hook for this, instead add a unit test in `test_context_composer.py` that asserts the string `"workflow_phase"` → `ComposerMode.WORKFLOW_PHASE` mapping happens. The simplest is a small helper `_task_mode_to_composer_mode(s: str) -> ComposerMode` in `agent.py` that we can test directly.)

- [ ] **Step 3: Wire the mapping**

```python
elif ctx.task_mode == "workflow_phase":
    mode = ComposerMode.WORKFLOW_PHASE
```

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/ -k task_mode -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/agent.py tests/
git commit -m "feat(workflow): run_agent_turn selects WORKFLOW_PHASE composer mode from task_mode"
```

---

### Task 12: `_enqueue_phase_turn` engine helper

**Files:**
- Modify: `src/decafclaw/workflow/engine.py`
- Test: `tests/test_workflow_engine.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workflow_engine.py`:

```python
@pytest.mark.asyncio
async def test_enqueue_phase_turn_inline_phase_dispatches_workflow_phase_kind(tmp_path: Path):
    """For an inline phase, _enqueue_phase_turn calls
    manager.enqueue_turn(kind=WORKFLOW_PHASE, conv_id=parent's)."""
    captured = []
    class FakeMgr:
        async def enqueue_turn(self, conv_id, *, kind, **kw):
            captured.append({"conv_id": conv_id, "kind": kind, **kw})
            return asyncio.Future()
    from decafclaw.workflow import engine
    parent_ctx = SimpleNamespace(
        conv_id="parent-c", parent_conv_id="",
        manager=FakeMgr(),
        user_id="u", config=None,
    )
    wf = _two_phase_wf()  # existing helper
    registry.register(wf)
    state = WorkflowState(workflow="demo", status=RunStatus.RUNNING,
                          current_phase="a", created_at="", updated_at="")
    phase = wf.phase("a")  # INLINE
    await engine._enqueue_phase_turn(parent_ctx, state, phase)
    assert len(captured) == 1
    assert captured[0]["conv_id"] == "parent-c"
    assert captured[0]["kind"].name == "WORKFLOW_PHASE"


@pytest.mark.asyncio
async def test_enqueue_phase_turn_subagent_phase_dispatches_child_agent_with_parent_conv_id(tmp_path: Path):
    """For a SUBAGENT phase, _enqueue_phase_turn dispatches a
    CHILD_AGENT turn with a unique child conv_id, and the setup
    callback configures the child ctx with parent_conv_id set to
    the parent's conv_id."""
    captured = []
    class FakeMgr:
        async def enqueue_turn(self, conv_id, *, kind, context_setup, **kw):
            captured.append({"conv_id": conv_id, "kind": kind,
                             "context_setup": context_setup, **kw})
            return asyncio.Future()
    from decafclaw.workflow import engine
    parent_ctx = SimpleNamespace(
        conv_id="parent-c", parent_conv_id="",
        manager=FakeMgr(),
        user_id="u", config=None,
        tools=SimpleNamespace(extra={}, extra_definitions=[]),
        skills=SimpleNamespace(activated=set(), data={}),
    )
    wf = _subagent_workflow()  # existing helper
    registry.register(wf)
    state = WorkflowState(workflow="sub", status=RunStatus.PAUSED_SUBAGENT,
                          current_phase="g", created_at="", updated_at="")
    phase = wf.phase("g")  # SUBAGENT
    await engine._enqueue_phase_turn(parent_ctx, state, phase)
    assert len(captured) == 1
    assert captured[0]["kind"].name == "CHILD_AGENT"
    assert captured[0]["conv_id"].startswith("parent-c--wf-sub-g-")
    # Verify setup callback sets parent_conv_id on the child ctx.
    child_ctx = SimpleNamespace(
        config=parent_ctx.config,
        tools=SimpleNamespace(extra={}, extra_definitions=[], allowed=None),
        skills=SimpleNamespace(activated=set(), data={}),
    )
    captured[0]["context_setup"](child_ctx)
    assert child_ctx.parent_conv_id == "parent-c"
    assert child_ctx.task_mode == "workflow_phase"
```

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/test_workflow_engine.py -k _enqueue_phase_turn -v` → fails (helper doesn't exist).

- [ ] **Step 3: Add the helper**

In `src/decafclaw/workflow/engine.py`:

```python
import secrets
from .types import PhaseKind, PhaseDef, WorkflowState


async def _enqueue_phase_turn(parent_ctx, state: WorkflowState,
                               phase: PhaseDef):
    """Enqueue the next phase turn on the parent's ConversationManager.

    INLINE phase → WORKFLOW_PHASE turn on parent's conv. The main
    agent runs the phase, calls phase_advance.

    SUBAGENT phase → CHILD_AGENT turn on a fresh child conv_id, with
    a setup callback that wires parent_conv_id (for workflow path
    resolution), task_mode='workflow_phase' (so composer dispatch
    picks WORKFLOW_PHASE mode), phase's tools.allowed, and the
    parent's tools.extra (skill-attached tools).
    """
    from ..conversation_manager import TurnKind

    manager = getattr(parent_ctx, "manager", None)
    if manager is None:
        raise RuntimeError(
            "workflow engine requires parent ctx to have a "
            "ConversationManager (no manager on parent_ctx)")

    if phase.kind == PhaseKind.INLINE:
        await manager.enqueue_turn(
            parent_ctx.conv_id,
            kind=TurnKind.WORKFLOW_PHASE,
            prompt="",  # system prompt is built from phase, no user msg
            user_id=getattr(parent_ctx, "user_id", ""),
        )
        return

    # SUBAGENT phase
    parent_conv = parent_ctx.conv_id
    child_conv_id = (
        f"{parent_conv}--wf-{state.workflow}-{phase.id}-"
        f"{secrets.token_hex(4)}"
    )

    parent_extras_tools = dict(getattr(parent_ctx.tools, "extra", {}))
    parent_extras_defs = list(getattr(parent_ctx.tools, "extra_definitions", []))
    parent_activated = set(getattr(parent_ctx.skills, "activated", set()))
    parent_skill_data = dict(getattr(parent_ctx.skills, "data", {}))

    def setup(child_ctx):
        # Workflow path resolution: child writes land in parent's dir.
        child_ctx.parent_conv_id = parent_conv
        # Compose-mode selector: forces WORKFLOW_PHASE composition
        # (which auto-resolves to the active workflow's current phase).
        child_ctx.task_mode = "workflow_phase"
        # Inherit parent's skill-attached tools so tabstack_research etc
        # work in the subagent.
        child_ctx.tools.extra = parent_extras_tools
        child_ctx.tools.extra_definitions = parent_extras_defs
        child_ctx.skills.activated = parent_activated
        child_ctx.skills.data = parent_skill_data
        # Subagents skip reflection + memory; phase prompt is the whole
        # context they need.
        child_ctx.is_child = True
        child_ctx.skip_reflection = True
        child_ctx.skip_vault_retrieval = True

    await manager.enqueue_turn(
        child_conv_id,
        kind=TurnKind.CHILD_AGENT,
        prompt="",
        history=[],
        context_setup=setup,
        user_id=getattr(parent_ctx, "user_id", ""),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/test_workflow_engine.py -k _enqueue_phase_turn -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/workflow/engine.py tests/test_workflow_engine.py
git commit -m "feat(workflow): _enqueue_phase_turn helper for engine-driven phase scheduling"
```

---

### Task 13: Rewrite `tool_workflow_start` to accept `params` and enqueue

**Files:**
- Modify: `src/decafclaw/tools/workflow_tools.py`
- Modify: `src/decafclaw/workflow/conv_state.py:init_workflow_state` (accept `params`)
- Test: `tests/test_workflow_tools.py`

- [ ] **Step 1: Update `init_workflow_state` to accept params**

In `src/decafclaw/workflow/conv_state.py`, change `init_workflow_state`'s signature to accept `params: dict | None = None`. Store it on `WorkflowState.params`.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_workflow_tools.py`:

```python
@pytest.mark.asyncio
async def test_workflow_start_accepts_params_and_stores_them(tmp_path: Path):
    """workflow_start(name='x', params={'topic': 'Y'}) → WorkflowState
    has .params populated."""
    registry.register(_two_phase_wf())
    ctx = _ctx_for_with_manager(tmp_path)  # ctx with FakeManager
    result = await tool_workflow_start(
        ctx, name="demo", params={"topic": "movable type"})
    state = load_workflow_state(ctx)
    assert state.params == {"topic": "movable type"}


@pytest.mark.asyncio
async def test_workflow_start_enqueues_phase_turn_and_ends_turn(tmp_path: Path):
    """workflow_start enqueues the initial phase turn on the manager
    and returns ToolResult(end_turn=True)."""
    enqueued = []
    class FakeMgr:
        async def enqueue_turn(self, conv_id, **kw):
            enqueued.append({"conv_id": conv_id, **kw})
            return asyncio.Future()
    registry.register(_two_phase_wf())
    ctx = SimpleNamespace(
        config=_minimal_config(tmp_path),
        conv_id="c1", parent_conv_id="",
        manager=FakeMgr(),
        tools=SimpleNamespace(extra={}, extra_definitions=[]),
        skills=SimpleNamespace(activated=set(), data={}),
        user_id="u",
    )
    result = await tool_workflow_start(ctx, name="demo", params={})
    assert isinstance(result, ToolResult)
    assert result.end_turn is True
    assert len(enqueued) == 1
    assert enqueued[0]["conv_id"] == "c1"


@pytest.mark.asyncio
async def test_workflow_start_params_defaults_to_empty_dict(tmp_path: Path):
    """If caller omits params, state.params defaults to {}."""
    registry.register(_two_phase_wf())
    ctx = _ctx_for_with_manager(tmp_path)
    await tool_workflow_start(ctx, name="demo")  # no params arg
    state = load_workflow_state(ctx)
    assert state.params == {}
```

- [ ] **Step 3: Run tests to verify they fail**

`uv run pytest tests/test_workflow_tools.py -k workflow_start -v` → fails.

- [ ] **Step 4: Rewrite `tool_workflow_start`**

In `src/decafclaw/tools/workflow_tools.py`:

```python
async def tool_workflow_start(ctx, name: str,
                               params: dict | None = None
                               ) -> str | ToolResult:
    """Start a fresh workflow for the current conversation.

    Activates each skill in ``wf.required_skills`` first; then
    initializes conversation-scoped state with the caller's
    ``params`` dict (exposed to phase prompts via
    ``{{params.X}}`` interpolation). Enqueues the initial phase
    turn on the parent's ConversationManager — does NOT run the
    subagent synchronously.

    Returns ToolResult(end_turn=True) so the parent's current turn
    ends; the engine-scheduled phase turn picks up from there.
    """
    wf = registry.get(name)
    if wf is None:
        return ToolResult(text=f"[error: workflow '{name}' not found]")

    existing = load_workflow_state(ctx)
    if existing is not None and existing.status not in (
            RunStatus.DONE, RunStatus.ERROR, RunStatus.ABORTED):
        return ToolResult(text=(
            f"[error: workflow '{existing.workflow}' is already "
            f"active in this conversation (status: "
            f"{existing.status.value}). Call workflow_abort first, or "
            f"wait for it to finish.]"))

    if existing is not None:
        archive_workflow_state(ctx)

    for skill_name in wf.required_skills:
        result = await _activate_skill_for_workflow(ctx, skill_name)
        text = result.text if isinstance(result, ToolResult) else result
        if isinstance(text, str) and text.startswith("[error"):
            return ToolResult(text=(
                f"[error: required skill '{skill_name}' failed to "
                f"activate: {text}. Cannot start workflow '{name}'.]"))

    state = init_workflow_state(
        ctx, workflow=name, initial_phase=wf.initial_phase,
        params=params or {})

    initial_phase = wf.phase(state.current_phase)
    if initial_phase is None:
        return ToolResult(text=(
            f"[error: workflow '{name}' initial_phase "
            f"'{wf.initial_phase}' not defined]"))

    await engine._enqueue_phase_turn(ctx, state, initial_phase)
    return ToolResult(
        text=(f"Started workflow '{name}' on phase "
              f"'{state.current_phase}'. The engine will run that phase "
              "as the next turn — your current turn ends here."),
        end_turn=True,
    )
```

Update the tool schema (`WORKFLOW_TOOL_DEFINITIONS` list) to add `params` (object, optional):

```python
{
    "type": "function",
    "priority": "normal",
    "function": {
        "name": "workflow_start",
        "description": (
            "Start a fresh workflow in the current conversation. "
            "Activates the workflow's required-skills first, then "
            "initializes per-conversation state. The optional "
            "`params` dict is exposed to phase prompts via "
            "{{params.X}} interpolation — pass topic / target / "
            "input values here."),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "params": {
                    "type": "object",
                    "description": (
                        "Optional key-value dict made available to "
                        "phase prompts as {{params.X}}.")
                },
            },
            "required": ["name"],
        },
    },
},
```

Remove `_render_phase_handoff` (no longer used — system prompt builds in the composer now). Remove the `RunStatus.DONE` / `RunStatus.ERROR` / `RunStatus.PAUSED_SUBAGENT` post-dispatch branches (handled by the enqueued turn / engine).

- [ ] **Step 5: Run tests**

`uv run pytest tests/test_workflow_tools.py -k workflow_start -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/tools/workflow_tools.py src/decafclaw/workflow/conv_state.py tests/test_workflow_tools.py
git commit -m "feat(workflow): workflow_start accepts params, enqueues phase turn, end_turn=True"
```

---

### Task 14: Rewrite `tool_phase_advance` to enqueue

**Files:**
- Modify: `src/decafclaw/tools/workflow_tools.py`
- Test: `tests/test_workflow_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workflow_tools.py`:

```python
@pytest.mark.asyncio
async def test_phase_advance_sets_workflow_advanced_this_turn(tmp_path: Path):
    """tool_phase_advance must set ctx.workflow_advanced_this_turn so
    TurnRunner's phase-internal loop knows to exit cleanly."""
    registry.register(_two_phase_wf())
    ctx = _ctx_for_with_manager(tmp_path)
    ctx.workflow_advanced_this_turn = False
    await tool_workflow_start(ctx, name="demo")
    ctx.workflow_advanced_this_turn = False  # reset between calls
    await tool_phase_advance(ctx, target_phase_id="b", reason="r")
    assert ctx.workflow_advanced_this_turn is True


@pytest.mark.asyncio
async def test_phase_advance_enqueues_next_phase_and_ends_turn(tmp_path: Path):
    """phase_advance for an inline transition enqueues a
    WORKFLOW_PHASE turn for the new phase and returns end_turn=True."""
    enqueued = []
    class FakeMgr:
        async def enqueue_turn(self, conv_id, *, kind, **kw):
            enqueued.append({"conv_id": conv_id, "kind": kind})
            return asyncio.Future()
    registry.register(_three_phase_wf())  # a → b → c, inline only
    ctx = SimpleNamespace(
        config=_minimal_config(tmp_path),
        conv_id="c1", parent_conv_id="",
        manager=FakeMgr(),
        tools=SimpleNamespace(extra={}, extra_definitions=[]),
        skills=SimpleNamespace(activated=set(), data={}),
        user_id="u",
        workflow_advanced_this_turn=False,
    )
    await tool_workflow_start(ctx, name="three")
    enqueued.clear()
    result = await tool_phase_advance(ctx, target_phase_id="b", reason="ready")
    assert isinstance(result, ToolResult)
    assert result.end_turn is True
    assert len(enqueued) == 1
    assert enqueued[0]["kind"].name == "WORKFLOW_PHASE"
```

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/test_workflow_tools.py -k phase_advance -v` → fails (current `phase_advance` returns `end_turn=False` and uses sync subagent dispatch).

- [ ] **Step 3: Rewrite `tool_phase_advance`**

```python
async def tool_phase_advance(ctx, target_phase_id: str,
                              reason: str = "") -> str | ToolResult:
    """Canonical workflow transition.

    Applies the transition, sets `ctx.workflow_advanced_this_turn` so
    the TurnRunner phase-internal loop exits cleanly, and enqueues
    the next phase turn (or surfaces a gate). Always ends the
    current turn.
    """
    state, wf = _get_workflow(ctx)
    if state is None or wf is None:
        return ToolResult(text="[error: no active workflow]")
    try:
        result = await engine.advance(
            ctx, state, target=target_phase_id, reason=reason)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")

    ctx.workflow_advanced_this_turn = True

    if result.end_turn_signal is not None:
        confirm = result.end_turn_signal
        if not isinstance(confirm, EndTurnConfirm):
            log.warning(
                "[workflow] unexpected end_turn_signal type %r from "
                "engine.advance — expected EndTurnConfirm",
                type(confirm).__name__)
            return ToolResult(
                text="[error: unexpected gate signal type from engine]")

        async def _on_approve():
            s = load_workflow_state(ctx)
            if s is None:
                return
            adv = await engine.finalize_gate_response(
                ctx, s, approved=True)
            wf = registry.get(s.workflow)
            if wf is None:
                return
            next_phase = wf.phase(adv.new_phase)
            if next_phase is not None:
                await engine._enqueue_phase_turn(ctx, s, next_phase)

        async def _on_deny():
            s = load_workflow_state(ctx)
            if s is None:
                return
            adv = await engine.finalize_gate_response(
                ctx, s, approved=False)
            wf = registry.get(s.workflow)
            if wf is None:
                return
            next_phase = wf.phase(adv.new_phase)
            if next_phase is not None:
                await engine._enqueue_phase_turn(ctx, s, next_phase)

        confirm.on_approve = _on_approve
        confirm.on_deny = _on_deny
        return ToolResult(text="Submitted for review.",
                          end_turn=confirm)

    fresh = load_workflow_state(ctx)
    if fresh is None:
        return ToolResult(
            text="[error: workflow state lost after advance]",
            end_turn=True)

    if fresh.status == RunStatus.DONE:
        return ToolResult(
            text=(f"Workflow '{fresh.workflow}' is complete "
                  f"(terminal phase '{fresh.current_phase}')."),
            end_turn=True)

    next_phase = wf.phase(fresh.current_phase)
    if next_phase is None:
        return ToolResult(
            text=f"[error: phase '{fresh.current_phase}' not in workflow]",
            end_turn=True)

    await engine._enqueue_phase_turn(ctx, fresh, next_phase)
    return ToolResult(
        text=(f"Advanced to phase '{fresh.current_phase}'. Engine will "
              "run that phase as the next turn."),
        end_turn=True,
    )
```

- [ ] **Step 4: Run tests**

`uv run pytest tests/test_workflow_tools.py -k phase_advance -v` → PASS.

- [ ] **Step 5: Run full workflow suite**

`uv run pytest tests/ -k workflow` → check what breaks. Several existing tests will likely fail because they expect synchronous dispatch and `end_turn=False`. Update them to expect `end_turn=True` and enqueue-on-manager semantics. Use mocked manager (`FakeMgr`) for those.

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/tools/workflow_tools.py tests/test_workflow_tools.py
git commit -m "feat(workflow): phase_advance enqueues next-phase turn, sets workflow_advanced flag, end_turn=True"
```

---

### Task 15: Replace synchronous subagent dispatch with engine-enqueued CHILD_AGENT

**Files:**
- Modify: `src/decafclaw/workflow/engine.py` — delete `dispatch_subagent_if_needed`, `dispatch_and_finalize_subagent`, related helpers. Subagent dispatch happens via `_enqueue_phase_turn` (Task 12).
- Modify: `src/decafclaw/workflow/subagent.py` — delete `_run_child`, `_resolve_phase_tools`, `_BLOCKED_FOR_CHILDREN`, `_latest_parent_user_message`, `_render_subagent_user_prompt`. All replaced by `_enqueue_phase_turn`'s setup callback + phase-as-system-prompt.
- Modify: `src/decafclaw/tools/workflow_tools.py` — drop the `dispatch_subagent_if_needed` call from `tool_workflow_start` (it's now `_enqueue_phase_turn`).
- Modify: `src/decafclaw/workflow/engine.py` — `_apply_transition` no longer needs the `PAUSED_SUBAGENT` status for subagent phases (engine just enqueues; transitions are observable through state.current_phase).
- Test: `tests/test_workflow_engine.py`, `tests/test_workflow_tools.py`

- [ ] **Step 1: Audit usage of deleted helpers**

```bash
grep -rn "dispatch_subagent_if_needed\|dispatch_and_finalize_subagent\|_run_child\|_resolve_phase_tools\|_BLOCKED_FOR_CHILDREN\|_latest_parent_user_message\|_render_subagent_user_prompt" src/ tests/
```

Make a list. Every callsite needs to migrate or get removed.

- [ ] **Step 2: Delete + migrate**

Remove the helpers in the files listed above. Update callsites:

- `tool_workflow_start` (Task 13 already removed the sync call — verify).
- `engine.advance` returns the `AdvanceResult` without dispatching anything; caller (now `tool_phase_advance`) handles enqueue via `_enqueue_phase_turn`.
- `subagent.py` shrinks to ~empty (delete the file? — keep the file as a placeholder for any future subagent-only helpers, OR remove it entirely and update imports). Recommendation: **delete `src/decafclaw/workflow/subagent.py` entirely**; the engine module owns subagent dispatch now via `_enqueue_phase_turn`.

- [ ] **Step 3: Migrate tests**

Existing tests for `_run_child` / `dispatch_subagent_if_needed` need to either:
- Test the new `_enqueue_phase_turn` path instead (already done in Task 12).
- Test that subagent failure modes (timeout, missing output) are still handled (now they happen inside the enqueued CHILD_AGENT turn — see Task 17).
- Be deleted if their behavior is no longer relevant.

Walk every workflow test file:

```bash
ls tests/test_workflow_*.py
```

Read each. Remove/update tests that assert on the deleted sync dispatch. Confirm `make test` passes.

- [ ] **Step 4: Run tests**

`make test` → all green. Expect to spend ~30-60 min on test migration; this is the densest task.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(workflow): drop sync subagent dispatch; engine enqueues CHILD_AGENT turns"
```

---

### Task 16: Auto-advance subagent phase from CHILD_AGENT turn completion

**Files:**
- Modify: `src/decafclaw/workflow/engine.py` — add `_finalize_subagent_phase` to verify outputs after child turn ends, auto-fire `phase_advance` against the single `next-phases` edge.
- Modify: `src/decafclaw/conversation_manager.py` — after a CHILD_AGENT turn completes, if its ctx was a workflow-phase child, call `_finalize_subagent_phase`.

The cleanest hook: when the CHILD_AGENT turn ends, the manager already runs its finally block. Add a post-turn check: if `child_ctx.task_mode == "workflow_phase"` and `child_ctx.parent_conv_id`, load the parent's workflow state, verify the phase outputs, auto-`phase_advance`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workflow_engine.py`:

```python
@pytest.mark.asyncio
async def test_subagent_finalize_advances_on_outputs_present(tmp_path: Path):
    """After a CHILD_AGENT turn for a subagent phase ends, if the
    declared outputs are present, the parent's workflow auto-advances
    to the single next-phases edge."""
    # Construct a workflow with subagent phase 'g' → inline phase 'd'.
    # Set up parent ctx + state at 'g'. Write the declared output file.
    # Call _finalize_subagent_phase(parent_ctx, state, phase=g).
    # Assert state.current_phase == 'd' afterward.
    ...


@pytest.mark.asyncio
async def test_subagent_finalize_errors_on_missing_outputs(tmp_path: Path):
    """If declared outputs are missing after child turn, state goes
    to RunStatus.ERROR and current_phase is unchanged."""
    ...
```

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/test_workflow_engine.py -k subagent_finalize -v` → fails.

- [ ] **Step 3: Add `_finalize_subagent_phase`**

```python
async def _finalize_subagent_phase(parent_ctx, state: WorkflowState,
                                    phase: PhaseDef) -> None:
    """Run after a CHILD_AGENT phase turn ends. Verifies the phase's
    declared outputs, then either auto-advances along the single
    next-phases edge (no LLM involved) or sets RunStatus.ERROR.
    """
    missing = verify_subagent_outputs(parent_ctx, state, phase.id)
    if missing:
        state.status = RunStatus.ERROR
        state.error = (
            "subagent did not produce required outputs: "
            + ", ".join(missing))
        save_workflow_state(parent_ctx, state)
        return

    if len(phase.next_phases) != 1:
        state.status = RunStatus.ERROR
        state.error = (
            f"subagent phase '{phase.id}' must have exactly one "
            f"next-phases edge; got {len(phase.next_phases)}")
        save_workflow_state(parent_ctx, state)
        return

    edge = phase.next_phases[0]
    adv = await advance(parent_ctx, state, target=edge.id,
                         reason="subagent outputs verified")
    # _apply_transition has already saved state with new current_phase.
    fresh = load_workflow_state(parent_ctx)
    if fresh is None:
        return
    wf = registry.get(fresh.workflow)
    if wf is None:
        return
    next_phase = wf.phase(fresh.current_phase)
    if next_phase is not None and fresh.status == RunStatus.RUNNING:
        await _enqueue_phase_turn(parent_ctx, fresh, next_phase)
```

- [ ] **Step 4: Wire into ConversationManager**

In `conversation_manager.py`, in the run() function's finally block (or wherever CHILD_AGENT turns complete), add:

```python
if (kind is TurnKind.CHILD_AGENT
        and getattr(ctx, "task_mode", "") == "workflow_phase"
        and getattr(ctx, "parent_conv_id", "")):
    # Need parent's ctx (or at least parent's manager + conv_id) to
    # finalize. Simplest: construct a minimal parent_ctx shim with the
    # needed fields and call the engine's finalize helper.
    from .workflow.engine import _finalize_subagent_phase
    from .workflow.conv_state import load_workflow_state
    from .workflow import registry
    parent_shim = Context(config=self.config, event_bus=self.event_bus)
    parent_shim.conv_id = ctx.parent_conv_id
    parent_shim.manager = self
    parent_state = load_workflow_state(parent_shim)
    if parent_state is not None:
        wf = registry.get(parent_state.workflow)
        if wf is not None:
            phase = wf.phase(parent_state.current_phase)
            if phase is not None:
                await _finalize_subagent_phase(
                    parent_shim, parent_state, phase)
```

- [ ] **Step 5: Run tests**

`uv run pytest tests/test_workflow_engine.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/workflow/engine.py src/decafclaw/conversation_manager.py tests/test_workflow_engine.py
git commit -m "feat(workflow): auto-finalize subagent phase from CHILD_AGENT turn end"
```

---

### Task 17: `TurnRunner` phase-internal nudge loop

**Files:**
- Modify: `src/decafclaw/agent.py` (the `TurnRunner.run` iteration loop)
- Test: `tests/test_agent.py`

- [ ] **Step 1: Reset workflow flags at turn start**

At the top of `TurnRunner.run()` (or wherever per-turn init happens), reset:

```python
self.ctx.workflow_advanced_this_turn = False
self.ctx.phase_continuations = 0
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_workflow_phase_nudge_loop_extends_turn_when_no_advance(tmp_path: Path):
    """If a WORKFLOW_PHASE turn ends an LLM iteration with no tool
    calls and workflow_advanced_this_turn is False, the loop should
    inject a synthetic user-role nudge and iterate again."""
    # Mock the LLM client: first call returns text with no tool calls;
    # second call calls phase_advance.
    # Assert: phase_advance got called (loop didn't bail), and one
    # synthetic user-role 'You stopped without phase_advance' message
    # was appended to messages.
    ...


@pytest.mark.asyncio
async def test_workflow_phase_nudge_loop_bails_after_max_continuations(tmp_path: Path):
    """After max_phase_continuations nudges, the loop bails and the
    phase goes to RunStatus.ERROR."""
    # Mock the LLM client: ALWAYS returns text with no tool calls.
    # Set max_phase_continuations=2.
    # Run TurnRunner. Expect 3 LLM calls total (initial + 2 nudges),
    # then state.status == ERROR.
    ...
```

- [ ] **Step 3: Run tests to verify they fail**

`uv run pytest tests/test_agent.py -k workflow_phase_nudge -v` → fails.

- [ ] **Step 4: Extend TurnRunner**

In `agent.py`'s `TurnRunner.run()`, find the loop exit condition (where it currently exits on "no tool calls returned"). Replace:

```python
# Existing exit: if no tool calls, end turn.
if not response_has_tool_calls:
    break
```

With:

```python
if not response_has_tool_calls:
    # Phase-internal loop: in a WORKFLOW_PHASE turn, if the LLM
    # ended without calling phase_advance, give it one more chance
    # before bailing.
    is_phase_turn = self.ctx.task_mode == "workflow_phase"
    advanced = getattr(self.ctx, "workflow_advanced_this_turn", False)

    if is_phase_turn and not advanced:
        # Determine the per-phase / config cap.
        from .workflow.conv_state import load_workflow_state
        from .workflow import registry
        state = load_workflow_state(self.ctx)
        wf = registry.get(state.workflow) if state else None
        phase = wf.phase(state.current_phase) if (wf and state) else None
        cap = (phase.max_continuations if phase and phase.max_continuations is not None
               else self.ctx.config.workflow.max_phase_continuations)
        cur = getattr(self.ctx, "phase_continuations", 0)

        if cur < cap:
            nudge = {
                "role": "user",
                "content": (
                    "You've stopped working on this phase, but "
                    "haven't called `phase_advance` yet. If the "
                    "phase is complete, call `phase_advance` with "
                    "the right target. If not, finish the remaining "
                    "work."),
            }
            self.messages.append(nudge)
            # Archive the nudge as a visible user-role message (per
            # Q7 decision in spec notes). If the agent already
            # archives messages on each iteration, this happens
            # automatically; otherwise add an explicit append.
            self.ctx.phase_continuations = cur + 1
            continue  # back to top of loop

        # Cap exhausted — mark phase error.
        if state is not None:
            state.status = RunStatus.ERROR
            state.error = (
                f"phase '{state.current_phase}' ended without "
                f"phase_advance after {cap} continuations")
            from .workflow.conv_state import save_workflow_state
            save_workflow_state(self.ctx, state)

    break
```

- [ ] **Step 5: Run tests**

`uv run pytest tests/test_agent.py -k workflow_phase_nudge -v` → PASS.

- [ ] **Step 6: Run full suite**

`make check && make test` → all green.

- [ ] **Step 7: Commit**

```bash
git add src/decafclaw/agent.py tests/test_agent.py
git commit -m "feat(workflow): TurnRunner phase-internal nudge loop"
```

---

### Task 18: Update workflow_demo to use `{{params.topic}}`

**Files:**
- Modify: `src/decafclaw/skills/workflow_demo/SKILL.md`
- Modify: `src/decafclaw/skills/workflow_demo/phases/gather.md`
- Modify: `src/decafclaw/skills/workflow_demo/phases/draft.md` (if it references topic)

- [ ] **Step 1: Update SKILL.md body**

Change the body to instruct the parent LLM to pass the topic via `params`:

```markdown
Research a topic and produce a short written brief.

When invoked as `!research_brief start <topic>` or `/research_brief start <topic>`,
call `workflow_start` with `name="research_brief"` and `params={"topic": "<topic>"}`.
The engine activates the `tabstack` skill (declared in required-skills above) before
any phase runs, then enqueues the gather phase as a CHILD_AGENT turn that fetches
sources.

The engine drives phase transitions automatically. You don't need to manage
the workflow yourself — just kick it off with the right params and let it run.

User said: $ARGUMENTS
```

- [ ] **Step 2: Update gather.md to use `{{params.topic}}`**

```markdown
---
kind: subagent
tools: [tabstack_research, tabstack_extract_markdown, vault_read, workflow_artifact_write]
outputs: [sources.md]
next-phases:
  - id: draft
---

You are researching the topic: **{{params.topic}}**

Procedure:
1. Use `tabstack_research` to gather 4-8 high-quality sources on this topic.
2. For each source, capture: title, URL, 1-2 sentence summary in your own words,
   and any key facts/quotes (with attribution).
3. Call `workflow_artifact_write` with `relative_path="gather/sources.md"` and
   `content` set to the markdown text below. (Do not put the markdown in your
   text response — it must be the `content` parameter of the tool call.)

The file content should have:
- A top-level heading naming the topic
- One `## Source: <title>` section per source
- A final `## Key themes` section listing 3-5 themes that emerged
```

- [ ] **Step 3: Update draft.md if it references the topic**

If `draft.md` mentions "the topic" or similar, change to `{{params.topic}}`. Check it.

- [ ] **Step 4: Verify the workflow still loads**

`uv run python -c "from decafclaw.workflow.loader import load_workflow; from pathlib import Path; wf = load_workflow(Path('src/decafclaw/skills/workflow_demo')); print(wf.name, wf.initial_phase)"` → `research_brief gather`.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/skills/workflow_demo/
git commit -m "feat(workflow): research_brief demo uses {{params.topic}} interpolation"
```

---

### Task 19: Live smoke against the demo

**Files:**
- No code changes. Pure validation.

- [ ] **Step 1: Restart make dev**

If `make dev` is already running on this branch, just verify it picked up changes (watch the log). Otherwise:

```bash
pkill -f "watchfiles.*decafclaw.main" ; sleep 2
make dev > /tmp/decafclaw-smoke.log 2>&1 &
```

Wait for `MCP: ` line in log.

- [ ] **Step 2: Drive smoke via Playwright MCP**

In a fresh chat, send: `/research_brief start the history of movable type`

Expected sequence (each is a distinct visible turn in the chat):

1. **Iteration 1:** agent calls `workflow_start(name="research_brief", params={"topic": "the history of movable type"})` — turn ends.
2. **Child turn (gather):** subagent runs with phase-as-system-prompt. Calls `tabstack_research` → `workflow_artifact_write(relative_path="gather/sources.md", ...)`. Turn ends. Engine auto-finalizes, advances to draft, enqueues draft phase turn.
3. **Draft turn:** main agent runs draft phase with phase-as-system-prompt. Reads sources, writes brief, calls `phase_advance(target_phase_id="review")`. Turn ends.
4. **Review turn:** agent presents the brief in text, calls `phase_advance(target_phase_id="publish")`. Gate fires → EndTurnConfirm with Approve/Deny buttons.
5. User clicks Approve.
6. **Publish turn:** agent writes vault page, workflow status = `done`.

- [ ] **Step 3: Verify the conv archive matches expected shape**

```bash
cat data/decafclaw/workspace/conversations/web-lmorchard-<NEW>.jsonl | python3 -c '...' (pretty-print)
```

Confirm: workflow_start → gather subagent → draft → review → publish, with auto-transitions and end_turn=True between phases.

- [ ] **Step 4: If it fails**

The most likely failure modes:
- Composer mode misdispatch → check `task_mode` is set on the child ctx and recognized by the agent.
- Engine doesn't fire auto-advance after subagent → check `_finalize_subagent_phase` wiring in conversation_manager.
- Phase prompt missing `{{params.topic}}` substitution → check `_interpolate_phase_params` is called in `_build_workflow_phase_system_prompt`.

Debug, fix, re-smoke. Commit fixes incrementally.

- [ ] **Step 5: Once smoke is clean, capture transcript**

Save the successful conv's JSONL to `docs/dev-sessions/2026-05-29-1729-workflow-engine-phase-turn-model/smoke-success.jsonl` for the dev-session record.

- [ ] **Step 6: Commit**

```bash
git add docs/dev-sessions/2026-05-29-1729-workflow-engine-phase-turn-model/
git commit -m "test(workflow): live smoke transcript confirms phase-turn walk"
```

---

### Task 20: Update docs + retro

**Files:**
- Modify: `docs/workflows.md`
- Modify: `CLAUDE.md` (skills section)
- Create: `docs/dev-sessions/2026-05-29-1729-workflow-engine-phase-turn-model/retro.md`

- [ ] **Step 1: Update docs/workflows.md**

Sections to rewrite:
- "Engine tools" — `workflow_start(name, params)` now ends the turn and engine enqueues phases. `phase_advance` enqueues next phase, ends turn.
- New section "Turn model" — describe WORKFLOW_PHASE turn kind, phase-as-system-prompt, the engine-drives-flow / LLM-drives-routing split.
- New section "Phase-internal nudge loop" — explain the `max_continuations:` field + global config default.
- Update the "Run state" section: `WorkflowState.params` is new.

- [ ] **Step 2: Update CLAUDE.md**

In the "Skills > Workflow skills" bullet, mention:
- Each phase fires as its own `WORKFLOW_PHASE` (or `CHILD_AGENT`) turn.
- Phase prompt becomes the system prompt for that turn (full replace; no general decafclaw preamble in worker mode).
- `phase_advance(target)` is the LLM's seam; everything else is engine-driven.

- [ ] **Step 3: Write retro.md**

What worked, what didn't, what to remember next time. Include:
- The cheap experiment finding (prompt-only isn't enough) was useful — saved us from building MORE prompt machinery before pivoting.
- Phase-as-system-prompt is the structural lever; everything else is plumbing.
- The `{{params.X}}` interpolation is small but solved Bug 1 cleanly.

- [ ] **Step 4: Run final check**

`make check && make test` → all green.

- [ ] **Step 5: Push final commit**

```bash
git add docs/workflows.md CLAUDE.md docs/dev-sessions/2026-05-29-1729-workflow-engine-phase-turn-model/retro.md
git commit -m "docs(workflow): document phase-turn model + dev-session retro"
git push origin feat/255-workflow-engine
```

- [ ] **Step 6: Update PR description**

`gh pr edit 557 --body "$(cat <<'EOF'
[Updated description summarizing the phase-turn model]
EOF
)"`

Notes for description: This PR's history spans three iterations (cross-conv → conv-scoped → phase-turn). The current end state is the phase-turn model. Reference [`docs/dev-sessions/2026-05-29-1729-workflow-engine-phase-turn-model/spec.md`](docs/dev-sessions/2026-05-29-1729-workflow-engine-phase-turn-model/spec.md) for full design.

---

## Self-Review checklist (done before handoff)

**Spec coverage:**
- ✓ `TurnKind.WORKFLOW_PHASE` — Task 4
- ✓ `Context.parent_conv_id` — Task 5
- ✓ `ComposerMode.WORKFLOW_PHASE` + phase-as-system-prompt + params interpolation — Tasks 7, 8
- ✓ USER turn mid-workflow phase composition — Task 9
- ✓ Manager dispatch — Task 10
- ✓ Engine `_enqueue_phase_turn` — Task 12
- ✓ `tool_workflow_start` rewrite + params arg — Task 13
- ✓ `tool_phase_advance` rewrite — Task 14
- ✓ Subagent sync dispatch deleted — Task 15
- ✓ Subagent auto-finalize on CHILD_AGENT turn end — Task 16
- ✓ TurnRunner phase-internal nudge loop — Task 17
- ✓ Demo workflow update — Task 18
- ✓ `WorkflowState.params` + `PhaseDef.max_continuations` — Task 2
- ✓ Loader `max-continuations:` — Task 3
- ✓ `WorkflowConfig.max_phase_continuations` — Task 1
- ✓ Live smoke — Task 19
- ✓ Docs — Task 20

**Placeholder scan:** No "TBD" / "implement later" / "fill in details" in any task body. A few tasks point at "the existing test_X.py pattern" for fixture setup — these are real existing patterns the implementer should mirror, not unfilled work.

**Type consistency:** `params` is `dict` throughout. `max_continuations` is `int | None` in `PhaseDef`, `int` in `WorkflowConfig`. `parent_conv_id` is `str` everywhere (empty string = unset). `workflow_advanced_this_turn` is `bool`. `phase_continuations` is `int`. `task_mode == "workflow_phase"` is the string sentinel — used consistently in agent.py / manager.py / setup callbacks.

---

## Execution

Plan saved to `docs/dev-sessions/2026-05-29-1729-workflow-engine-phase-turn-model/plan.md`.

**Recommended execution path:** superpowers:subagent-driven-development. Fresh implementer subagent per task + two-stage review (spec compliance, then code quality). Estimated 6-10 hours for the whole run depending on test-migration depth in Task 15.

**Alternative:** superpowers:executing-plans for batched in-session execution.
