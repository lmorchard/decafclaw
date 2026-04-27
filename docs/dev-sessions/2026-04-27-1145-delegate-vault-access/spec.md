# delegate_task: read-only vault access for children

Tracking issue: #396 (split out of #300).

## Problem

Today's `delegate_task` has a confused vault-access policy:

- `_run_child_turn` sets `child_ctx.skip_vault_retrieval = True`,
  so the proactive memory retrieval at turn start is suppressed.
- But `child_ctx.tools.allowed` includes every vault tool — read
  AND write — that the parent had access to. Children can
  `vault_write`, `vault_delete`, `vault_journal_append`, etc.,
  with no parental supervision.

The issue body framed current state as "child can't call
`vault_search` / `vault_read`," but the code shows children CAN
call those (along with the write tools). The actual gap is:

- **Children can write to the vault unsupervised.** Surprising
  blast radius for a sub-agent the parent invoked for an isolated
  task.
- **No way to opt children INTO the proactive retrieval** when
  the parent specifically wants them to benefit from memory.

## Goal

Replace today's all-or-nothing-implicit policy with an explicit
opt-in model:

- **Default**: child has no proactive retrieval, no vault read
  tools, no vault write tools — total isolation from the vault.
- **`allow_vault_retrieval: bool = False`**: opt the child INTO
  the proactive retrieval at turn start.
- **`allow_vault_read: bool = False`**: opt the child INTO
  read-side vault tools (`vault_read`, `vault_search`,
  `vault_list`, `vault_backlinks`, `vault_show_sections`).
- **Vault writes are NEVER allowed** for children, regardless of
  flags. If a parent needs the child's findings persisted to the
  vault, the parent does the write itself after delegation
  completes.

## Decisions (autonomous brainstorm)

1. **Two booleans, not a combined enum.** Easier to reason about
   in tool descriptions and call sites for v1. A future
   `vault_access: "none" | "read" | "retrieval+read"` enum is a
   refactor we can do once usage patterns surface.
2. **Writes are categorically blocked.** No "and also writes"
   flag. If the child writes, that's a category of action the
   parent should explicitly perform after the child returns —
   keeps the audit trail in the parent's conversation.
3. **This is a behavior tightening.** Children today can call
   any vault tool the parent has; this PR removes that. Calls
   that relied on the old behavior get a clear migration path:
   add `allow_vault_read=True`. Calls that rely on writes need
   to restructure (do the write in the parent).
4. **Read-set membership is hardcoded** rather than computed.
   The list of vault tools is small and stable; a hardcoded
   read/write split is auditable in one read of `delegate.py`.
   If new vault tools land, the author updates the set as part
   of adding the tool.
5. **Implementation via `ctx.tools.allowed` filtering**, not a
   bespoke runtime gate. `tools.allowed` is the existing
   mechanism; routing the new policy through it keeps tool
   restriction in one place and works for both bundled vault
   tools and any future vault-adjacent skills.

## Architecture

### Module-level tool sets

```python
_VAULT_READ_TOOLS = frozenset({
    "vault_read",
    "vault_search",
    "vault_list",
    "vault_backlinks",
    "vault_show_sections",
})

_VAULT_WRITE_TOOLS = frozenset({
    "vault_write",
    "vault_delete",
    "vault_rename",
    "vault_journal_append",
    "vault_move_lines",
    "vault_section",
})
```

### `_run_child_turn` change

Adds `allow_vault_retrieval: bool = False` and `allow_vault_read:
bool = False` parameters. Computes the excluded tool set:

```python
excluded = {"delegate_task", "activate_skill", "refresh_skills", "tool_search"}
excluded |= _VAULT_WRITE_TOOLS  # always
if not allow_vault_read:
    excluded |= _VAULT_READ_TOOLS
child_ctx.tools.allowed = (all_tools - excluded)
```

`skip_vault_retrieval` becomes `not allow_vault_retrieval` (was
hardcoded `True`).

### `tool_delegate_task` change

Adds the two parameters to its signature and threads them through
to `_run_child_turn`.

### Tool definition

Two new parameters with descriptions that explicitly describe the
default-deny posture:

```
"allow_vault_retrieval": {
    "type": "boolean",
    "description": (
        "When true, the child agent runs the proactive memory "
        "retrieval at turn start. Default false — the child has "
        "no auto-injected memory context unless you opt in. "
        "Use when the child needs to draw on past conversations "
        "or vault knowledge to do its task."
    ),
},
"allow_vault_read": {
    "type": "boolean",
    "description": (
        "When true, the child can call read-side vault tools "
        "(vault_read, vault_search, vault_list, vault_backlinks, "
        "vault_show_sections). Default false — the child can't "
        "read the vault unless you opt in. Vault WRITE tools "
        "(vault_write, vault_journal_append, vault_delete, etc.) "
        "are NEVER available to children regardless of this flag; "
        "if the child's work should land in the vault, do the "
        "write yourself after the child returns."
    ),
},
```

## Out of scope

- Read-write opt-in flag. Writes are always blocked.
- Per-tool-set granularity (e.g. let the child read but not
  search). Two flags is enough for v1.
- Combined `vault_access` enum.
- Auto-derived read/write split via tool metadata. Hardcoded
  sets are simpler for v1.

## Acceptance criteria

- Default `delegate_task(...)` (no flags): child cannot call
  `vault_read` / `vault_search` / `vault_list` /
  `vault_backlinks` / `vault_show_sections` / any vault write
  tool. No proactive retrieval.
- `allow_vault_read=True`: child can call the read-set; cannot
  call any write tool.
- `allow_vault_retrieval=True`: child sees proactive retrieval at
  turn start (`skip_vault_retrieval=False`).
- Both flags can be combined.
- The two boolean parameters appear in the tool definition with
  the descriptions above.

## Testing

- Existing test: `_run_child_turn` already verifies basic flow.
  Add new tests asserting `child_ctx.tools.allowed` after setup:
  - default → write tools excluded, read tools excluded
  - `allow_vault_read=True` → read tools included, write tools
    still excluded
  - `allow_vault_retrieval=True` → `skip_vault_retrieval=False`
    on the child ctx
- `tool_delegate_task` test: parameters thread through to
  `_run_child_turn`.
- No real-LLM CI test. Manual smoke after merge.

## Files touched

- `src/decafclaw/tools/delegate.py` — module-level sets + flag
  threading + tool definition.
- `tests/test_delegate.py` — new tests covering each flag.
- `docs/delegation.md` — describe the policy.
