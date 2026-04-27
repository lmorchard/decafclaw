# Plan

See `spec.md` for decisions.

## Phase 1 — code + tests

- `src/decafclaw/tools/delegate.py`:
  - Module-level `_VAULT_READ_TOOLS` and `_VAULT_WRITE_TOOLS`
    `frozenset`s.
  - `_run_child_turn(..., allow_vault_retrieval=False,
    allow_vault_read=False)` — wires both into the `setup`
    closure.
  - `setup` builds the excluded set: existing four + writes
    (always) + reads (when not opted in). `skip_vault_retrieval`
    becomes `not allow_vault_retrieval`.
  - `tool_delegate_task(..., allow_vault_retrieval=False,
    allow_vault_read=False)` — passes through.
  - Tool-definition update: two new boolean parameters with
    explicit-opt-in language in the descriptions.
- `tests/test_delegate.py` extensions:
  - Default flags → child's `allowed` set excludes all vault
    tools; `skip_vault_retrieval=True`.
  - `allow_vault_read=True` → read set in `allowed`, write set
    not in `allowed`.
  - `allow_vault_retrieval=True` → `skip_vault_retrieval=False`.
  - Wrapper: parameters thread through to `_run_child_turn`.

## Phase 2 — docs

- `docs/delegation.md`: new "Vault access" section describing
  the default-deny posture and when to opt in.

## Phase 3 — squash, push, PR, request Copilot

`Closes #396`. Note the behavior change in the PR description.

## Risk register

- **Behavior tightening.** Existing code paths that delegate
  vault-using subtasks without opt-in flags will see those
  subtasks lose vault access. The migration is a one-line param
  add; documenting clearly in the PR description and in
  `docs/delegation.md` covers the discovery path.
- **Children writing to vault as part of legitimate flows.** I
  haven't found any in the bundled skills — the dream/garden
  paths run as scheduled tasks (not via `delegate_task`). If
  anyone has a workspace skill that delegates "write a journal
  entry" to a child, that breaks until they restructure to write
  from the parent. Documented in the PR.
