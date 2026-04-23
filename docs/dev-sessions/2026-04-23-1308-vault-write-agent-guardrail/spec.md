# Vault Write Agent-Folder Guardrail + Ingest Skill Fix

## Context

During a `/ingest` run, the agent wrote vault pages at the vault root (outside `agent/pages/`) despite the ingest skill explicitly requiring writes under `agent/pages/`. A subsequent `/postmortem` surfaced the failure and proposed skill-wording changes, but the root cause is deeper: `vault_write` has no hard guardrail — it only *logs a notice* when writing outside the agent folder. The ingest skill's own Step 3 also has an ambiguity ("update in place" for an existing page) that conflicts with Step 4's "stay under `agent/pages/`" rule.

Separately, `vault_delete` already enforces agent-folder-only deletion — so once the stray pages landed, the agent could not clean them up, making the drift doubly expensive.

## Goals

1. Make `vault_write` refuse writes outside the agent folder, matching how `vault_delete` and `vault_rename` already behave. No override parameter.
2. Fix the instruction tension inside the ingest skill so the "update in place" rule cannot contradict the "stay under `agent/pages/`" rule.
3. Cover the new guardrail with a regression test.

## Non-goals (deferred)

- Introducing an alternate `vault_write`-with-approval tool for editing user notes. Defer until the "agent edits user notes" use case becomes concrete.
- Changes to AGENT.md. The existing language ("only write within `agent/` unless the user explicitly asks otherwise") already states the rule; tool-level enforcement makes reinforcing prose unnecessary.
- Cleaning up the stray root-level pages left behind by the original failure. Les will handle those manually.
- Any change to `vault_delete` / `vault_rename` behavior.
- Changes to the web UI's `/api/vault/*` endpoints (those are user-facing, not agent-facing, and are a separate code path).

## Design

### 1. `tool_vault_write` — hard guardrail

In `src/decafclaw/skills/vault/tools.py`:

- Replace the current soft behavior (lines 171–173: "Log notice if writing outside agent folder" + `log.info(...)`) with a hard error return, matching the shape already used by `tool_vault_delete`:

  ```python
  if not _is_in_agent_dir(ctx.config, path):
      return ToolResult(
          text=f"[error: refusing to write '{page}' — "
               f"only pages under the agent folder may be written]")
  ```

  Keep the existing `_safe_write_path` path-traversal validation intact — the new guardrail is an additional check after `_safe_write_path` succeeds.

- Update the `vault_write` tool description (TOOL_DEFINITIONS, ~line 572) to reflect the hard rule. Replace the current trailing sentence — *"Default to writing in agent/pages/ — only write outside the agent folder when the user explicitly asks."* — with language consistent with `vault_delete`'s description:

  > "Writes are restricted to the agent folder (`agent/pages/`, `agent/journal/`); admin and user pages are off-limits."

### 2. Ingest skill Step 3 clarification

In `src/decafclaw/skills/ingest/SKILL.md`, replace the current Step 3 closing sentence:

> **If a page on the exact source or its primary topic already exists, treat it as the primary page and update in place.** Do not create a duplicate at a different path.

with:

> **If a page on the exact source or its primary topic already exists under `agent/pages/`, treat it as the primary page and update in place.** If a strong match exists elsewhere in the vault (e.g. a user note), leave that page alone — Step 4 covers how to handle it.

Step 4 remains the authoritative rule and is unchanged.

### 3. Regression test

In `tests/test_vault_tools.py`, inside `TestVaultWrite`:

- Add `test_rejects_write_outside_agent_folder` — calls `tool_vault_write(ctx, "StrayRootPage", "# Stray")` and asserts:
  - Return value is a `ToolResult` with `[error:` prefix in its text.
  - No file is created at `vault_dir / "StrayRootPage.md"`.

Write this test **first**, confirm it fails against the current code, then apply the tool change to make it pass (per CLAUDE.md "bug fix = test first").

## Files changed

- `src/decafclaw/skills/vault/tools.py` — guardrail + description update
- `src/decafclaw/skills/ingest/SKILL.md` — Step 3 wording
- `tests/test_vault_tools.py` — new regression test

## Acceptance criteria

- `pytest tests/test_vault_tools.py` passes (including the new test).
- `make check` passes (lint + typecheck).
- `make test` passes (full suite; no regressions).
- Manual smoke check: re-reading the updated ingest skill's Steps 3 + 4 together reveals no conflict.
