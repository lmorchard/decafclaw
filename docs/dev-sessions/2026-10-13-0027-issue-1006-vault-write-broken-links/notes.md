# issue-1006: vault_write deterministic broken [[wiki-link]] check

## Resume here

Implementation complete, local gates green, published as a DRAFT PR (not ready, no
review requested). Worktree: `.claude/worktrees/issue-1006-vault-write-broken-links`,
branch `issue-1006-vault-write-broken-links`, base `origin/main @ 4eaef9ee`.
Next action: independent review of the published head (separate worker), then
`pr ready` + board "In review" + review request — all explicitly out of scope here.

## What changed

`tool_vault_write` (`src/decafclaw/skills/vault/tools.py`) now runs a deterministic,
LLM-free, fail-open broken-link check after `path.write_text(content)` and before the
final `ToolResult` return. It resolves every `[[link]]` in `content` with the existing
`backlinks._build_page_lookup` / `_resolve_link_target` helpers (the same rules as
`vault_backlinks`). Unresolved raw link texts are collected, deduped in first-occurrence
order. When any are found, the result text appends one line
`Unresolved links: [[Foo]], [[Bar]]` and `data` gains `unresolved_links` (list of raw
link texts, same order). When none are found, the text and data are unchanged (no
`unresolved_links` key). The whole check is wrapped in `try/except Exception`; on error it
logs at `log.debug` and returns the normal saved result (the write already succeeded).

Because the page is written to disk before the check, a `[[link]]` to the page being
written resolves (it is in the lookup) and is not reported — no special-casing.

Scope kept minimal: no other write tool touched (`vault_section`, `vault_journal_append`,
`vault_move_lines`, `vault_rename` out of scope), no auto-creation/correction of targets,
no LLM, no schema change.

### Result shape (before → after)

Before: `ToolResult(text="Vault page '<page>' saved.", data={path, created, bytes_written})`.

After (dangling present): text is
`"Vault page '<page>' saved.\nUnresolved links: [[Gone]], [[Missing]]"` and data is
`{path, created, bytes_written, unresolved_links: ["Gone", "Missing"]}`. After (all
resolve): identical to before.

## Tests (`tests/test_vault_tools.py`, new class `TestVaultWriteBrokenLinks`)

- `test_all_links_resolve_no_unresolved` — regression guard: all links resolve → no line, no key.
- `test_dangling_link_reported_deduped_in_order` — dangling reported in text + data, deduped, first-occurrence order.
- `test_display_form_and_subfolder_bare_resolve` — `[[Target|display]]` and bare subfolder `[[Alice]]` resolve (not reported).
- `test_dangling_subfolder_and_display_reported` — `[[people/Bob]]` and `[[Ghost]]` dangling reported; `[[people/Alice]]` resolves.
- `test_resolution_error_fails_open` — `_resolve_link_target` patched to raise → write succeeds, normal result, `log.debug` captured.

Test-specificity verification (check reverted to origin/main, new tests run, restored):
- FAIL when check removed: `test_dangling_link_reported_deduped_in_order`,
  `test_dangling_subfolder_and_display_reported`, `test_resolution_error_fails_open`.
- PASS in both cases (by design — they assert the ABSENCE of the line, identical pre/post):
  `test_all_links_resolve_no_unresolved`, `test_display_form_and_subfolder_bare_resolve`.
These two are no-false-positive regression guards; the positive-behavior tests are the
specific ones.

## Baseline

`make test` before any change: **4522 passed, 2 skipped** (clean, no pre-existing failures).

## Checks (command → result, only after run)

- `make test` (baseline) → 4522 passed, 2 skipped.
- `uv run pytest tests/test_vault_tools.py -k BrokenLinks -n 0 -v` (with check) → 5 passed.
- Revert check → run new tests → 3 failed (dangling×2, fail-open), 2 passed (regression guards). Restored.
- `uv run pytest tests/test_vault_tools.py -n 0` (restored) → 5 passed.
- `uv run pytest tests/test_vault_tools.py -n auto` → 153 passed.
- `make fmt` → 432 files left unchanged (exit 0).
- `make check` → exit 0 (check-message-types clean, fmt-check clean, ruff all passed, pyright 0 errors/0 warnings, tsc clean, module-graph 2 passed).
- `make test` (after change) → **4527 passed, 2 skipped** (exit 0). Baseline +5 new tests, all green.

## Board

Ready → In progress (via `ghflow board set-status`), readback confirmed.

## PR

Draft PR URL and published head recorded below at publication.
