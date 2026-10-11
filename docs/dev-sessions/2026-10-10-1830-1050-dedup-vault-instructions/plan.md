# Implementation Plan — Deduplicate Vault Instructions

## Phase 1: Test Baseline & Guards
- Write unit tests in `tests/test_vault_prompts_dedup.py`:
  - Assert `AGENT.md` Vault section is condensed and no longer contains redundant folder bullets or gardening rules.
  - Assert `AGENT.md` retains key guardrails: "Search before saying 'I don't know'", "Vault pages are NOT skills", "Journal mistakes later".
  - Assert `src/decafclaw/skills/vault/SKILL.md` no longer contains the "Vault Gardening Rules" block.
  - Verify test fails against current codebase.

## Phase 2: Condense `AGENT.md`
- Edit `src/decafclaw/prompts/AGENT.md` under `## Vault — Your Persistent Memory`:
  - Replace the verbose 39-line section with the concise high-level overview.
  - Point to `vault` skill for tool contracts/workflows and `<vault_guide>` for user conventions.

## Phase 3: Update `vault/SKILL.md`
- Edit `src/decafclaw/skills/vault/SKILL.md`:
  - Remove the redundant `## Vault Gardening Rules` section (lines 20-39).
  - Ensure tool contracts (`## Your Home Folder`, `## Journal vs Pages`, `## Boundaries`, `## Editing Sections`, `## Organizing with Folders`, `## Navigating the Knowledge Graph`, `## When to Consult the Vault`, `## When to Update the Vault`) remain cohesive.

## Phase 4: Documentation & Quality Gates
- Update `docs/vault.md` to reflect the clean division of instructions and update references to gardening rules.
- Run `make check` and `make test`.
- Verify all tests pass.
