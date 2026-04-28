# PersistedTurnState — single source of truth for conversation persistence (#378)

## Problem

`ConversationManager._save_conversation_state` and `_restore_per_conv_state` are a save/restore pair (`conversation_manager.py:765-790`). They copy a deliberate subset of `Context` state onto `ConversationState` and back. The bug shape: **two parallel hand-maintained field lists** that drift out of sync silently — adding a persistent field requires both a save edit and a restore edit, with symmetry enforced only by code review.

CLAUDE.md already bans the "hand-list fields when copying" pattern, but `_save`/`_restore` carved out an exception because not every ctx field should persist (per-call counters, per-call IDs, transient flags must NOT survive). The right fix isn't `vars()` iteration; it's a typed dataclass that names "what persists" plus a single binding table that walks fields symmetrically.

## Why this isn't the same fix as #377

The audit fixes that #377 addressed (`fork_for_tool_call`, notification round-trip, confirmation round-trip) all wanted *every* field to propagate — `vars()` / `dataclasses.fields()` iteration was the right idiom there.

This pair wants only a *deliberate subset*. So `vars()` would be wrong; we need a structurally-enforced subset.

## Design

### `PersistedTurnState` dataclass

```python
@dataclass
class PersistedTurnState:
    """Per-conversation state that persists across turns.

    Two field categories live here:
    - Ctx-driven: written by `_save_conversation_state` from ctx,
      read by `_restore_per_conv_state` onto ctx.
    - Externally-driven: set by `manager.set_flag()` from web /
      transport handlers, read by restore onto ctx; save never
      overwrites these.
    """
    extra_tools: dict = field(default_factory=dict)
    extra_tool_definitions: list = field(default_factory=list)
    activated_skills: set = field(default_factory=set)
    skip_vault_retrieval: bool = False
    active_model: str = ""
```

Replaces the existing trio on `ConversationState`:
- `skill_state: dict | None` (the dict shape that today bundles `extra_tools` / `extra_tool_definitions` / `activated_skills`)
- `skip_vault_retrieval: bool`
- `active_model: str`

…with a single field `persisted: PersistedTurnState = field(default_factory=PersistedTurnState)`.

### Binding table — single source of truth

```python
# Per-field reader/writer pair, exhaustive over PersistedTurnState
# fields. Adding a field to PersistedTurnState requires adding an
# entry here (the regression test in test_conversation_manager.py
# enforces this).
_PERSISTED_BINDINGS: dict[str, tuple[Callable, Callable]] = {
    "extra_tools": (
        lambda ctx: ctx.tools.extra,
        lambda ctx, v: setattr(ctx.tools, "extra", v),
    ),
    "extra_tool_definitions": (
        lambda ctx: ctx.tools.extra_definitions,
        lambda ctx, v: setattr(ctx.tools, "extra_definitions", v),
    ),
    "activated_skills": (
        lambda ctx: ctx.skills.activated,
        lambda ctx, v: setattr(ctx.skills, "activated", v),
    ),
    "skip_vault_retrieval": (
        lambda ctx: ctx.skip_vault_retrieval,
        lambda ctx, v: setattr(ctx, "skip_vault_retrieval", v),
    ),
    "active_model": (
        lambda ctx: ctx.active_model,
        lambda ctx, v: setattr(ctx, "active_model", v),
    ),
}

# Fields whose value flows ctx -> state on save. Others are
# externally-driven and only flow state -> ctx on restore.
_CTX_DRIVEN_FIELDS = frozenset({
    "extra_tools",
    "extra_tool_definitions",
    "activated_skills",
    "skip_vault_retrieval",
})
```

### Save/restore become symmetric loops

```python
def _save_conversation_state(self, state, ctx):
    for f in dc_fields(PersistedTurnState):
        if f.name not in _CTX_DRIVEN_FIELDS:
            continue
        reader, _ = _PERSISTED_BINDINGS[f.name]
        value = reader(ctx)
        if value:  # truthy — preserves sticky-once-set semantics
            setattr(state.persisted, f.name, value)

def _restore_per_conv_state(self, state, ctx):
    for f in dc_fields(PersistedTurnState):
        _, writer = _PERSISTED_BINDINGS[f.name]
        value = getattr(state.persisted, f.name)
        if not value:
            continue
        writer(ctx, value)
```

The `if value:` truthiness guards preserve the existing "once-True/once-set, never clobber back to default" semantics that the inline conditionals have today (e.g. ctx.skip_vault_retrieval set via `set_flag` survives even if a turn's ctx is born False).

### `set_flag` writes through

Currently `set_flag(conv_id, key, value)` does `setattr(state, key, value)`. After the refactor, the persisted fields live on `state.persisted`. Update:

```python
def set_flag(self, conv_id, key, value):
    state = self._get_or_create(conv_id)
    if key in {f.name for f in dc_fields(PersistedTurnState)}:
        setattr(state.persisted, key, value)
        return
    if hasattr(state, key):
        setattr(state, key, value)
        return
    log.warning("Unknown conversation flag: %s", key)
```

### Read sites

`mattermost.py:450` reads `conv_state.active_model` directly. Update to `conv_state.persisted.active_model`. Self-contained — only one external read site. Tests have one or two pokes at the old fields, also updated.

### Regression test

```python
def test_persisted_field_bindings_exhaustive():
    """Every PersistedTurnState field must have a binding entry —
    so adding a future field can't silently drop out of save/restore."""
    pf = {f.name for f in dc_fields(PersistedTurnState)}
    bf = set(_PERSISTED_BINDINGS.keys())
    assert pf == bf
```

Plus an end-to-end save→restore round-trip test with every field set to a non-default sentinel, verifying restore reproduces them all on a fresh ctx.

## Files touched

- `src/decafclaw/conversation_manager.py` — new dataclass, new bindings, rewritten save/restore + set_flag, `ConversationState` field swap.
- `src/decafclaw/mattermost.py` — one read site.
- `tests/test_conversation_manager.py` — update `test_enqueue_turn_wake_kind_restores_skill_state` to construct `PersistedTurnState`; add the binding-coverage test and a round-trip test.

## Out of scope

- Persisting any *new* fields. This is purely a structural refactor of what's there.
- Changing the public API of `set_flag`. The transport-side callers (`web/websocket.py`) keep working.

## Test plan

1. `test_persisted_field_bindings_exhaustive` — fields set matches bindings set.
2. `test_save_restore_round_trip` — set every PersistedTurnState field on ctx + state via `set_flag`/save, restore on a fresh ctx, assert all values flow through.
3. Existing `test_enqueue_turn_wake_kind_restores_skill_state` updated to use `PersistedTurnState` directly — should pass without behavior change.
4. Full pytest run — no regressions.
