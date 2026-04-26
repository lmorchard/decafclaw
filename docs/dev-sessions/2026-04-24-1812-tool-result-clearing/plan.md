# Plan: Tool-result clearing

Refer to `spec.md` for architecture + decisions.

Phases:

1. **`CleanupConfig` + `clear_old_tool_results` core** — pure module + unit tests.
2. **Wire into agent loop** — call before `_maybe_compact`; thread stats to `ComposerState`.
3. **Sidecar diagnostics** — surface cleared count + bytes in the context sidecar.
4. **Docs** — `docs/context-composer.md`, `docs/config.md`, `CLAUDE.md`.
5. **PR + Copilot review.**

Phase 1 is the heaviest; phases 2–3 are small wiring; phase 4 is docs.

## Phase 1 — core clearing logic

- `src/decafclaw/config_types.py`: `CleanupConfig` dataclass.
- `src/decafclaw/config.py`: register `cleanup` sub-config alongside the others.
- `src/decafclaw/context_cleanup.py` (new):
  - `@dataclass ClearStats(cleared_count: int = 0, cleared_bytes: int = 0)` with `merge(other)` for accumulation.
  - `clear_old_tool_results(history: list[dict], config) -> ClearStats` — pure function, mutates `history` in place, returns the delta from this call.
  - Helper: `_tool_name_for_call_id(history, call_id)` — walks back through assistant messages to find the originating `tool_calls[].function.name`.
  - Helper: `_user_turn_boundaries(history)` — list of indices where `role == "user"`.
- `tests/test_context_cleanup.py`: unit tests per spec.

## Phase 2 — agent loop wiring

- `src/decafclaw/context.py`: extend `ComposerState` with `cleanup_stats: ClearStats`.
- `src/decafclaw/agent.py`: in `_maybe_compact` (or a new sibling `_clear_and_compact`), call `clear_old_tool_results` first, accumulate to `ctx.composer.cleanup_stats`.

## Phase 3 — sidecar diagnostics

- `src/decafclaw/context_composer.py`: where the sidecar dict is built, include `cleanup: {cleared_count, cleared_bytes}` from `ctx.composer.cleanup_stats`.
- Update existing sidecar test if any (and write one if not).

## Phase 4 — docs

- `docs/context-composer.md`: new "Tool-result clearing" subsection.
- `docs/config.md`: `cleanup.*` reference table.
- `CLAUDE.md`: extend the context-engineering bullet to mention the clear tier.

## Phase 5 — PR

Squash, rebase if main moved, push, open PR (`Closes #298`), request Copilot, move to In review.

## Risk register

- **Resolving tool name from `tool_call_id`** requires walking history. If the assistant message has been compacted away (replaced by a summary), the name resolution returns `None` and we fall back to "treat as eligible" — that's the right policy since a compacted history's tool messages are also old and outsized. Document the fallback.
- **Idempotency check**: stub strings start with a recognizable prefix; re-runs detect and skip. Accounted for in eligibility rule 2.
- **Eval impact**: clearing changes what the model sees on subsequent iterations. Existing memory evals could regress if the agent was re-reading old `vault_read` output. Run `make eval` post-merge to spot-check; if regressions appear, raise `min_size_bytes` or shrink `min_turn_age`.
