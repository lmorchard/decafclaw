# Notes — Deduplicate Vault Instructions

Issue: #1050

## Context & Background
Issue #1050 identified three overlapping layers delivering vault instructions:
1. `<agent_role>` (`src/decafclaw/prompts/AGENT.md`)
2. `<loaded_skills><skill name="vault">` (`src/decafclaw/skills/vault/SKILL.md`)
3. `<vault_guide>` (`<vault_root>/AGENTS.md`)

## Key Decisions & Division of Responsibilities
- **User's `AGENTS.md` (`<vault_guide>`)**: Defines user-specific vault conventions, structure, habits, and permissions.
- **`vault` skill (`skills/vault/SKILL.md`)**: Documents specific tool contracts and workflows (`vault_read`, `vault_write`, `vault_delete`, `vault_rename`, `vault_update_frontmatter`, `vault_grant_folder`, section-aware tools, folder tools, when to consult and update). Removed the 20-line "Vault Gardening Rules" and "tl;dr summaries" block which belongs in user `AGENTS.md` and dedicated maintenance skills (`garden`, `dream`).
- **`AGENT.md` (`prompts/AGENT.md`)**: Condensed the 39-line "Vault — Your Persistent Memory" section into a minimal high-level overview (~20 lines). Kept core behavioral guardrails (search before saying "I don't know", vault pages are not skills, journal errors later). Pointed to the `vault` skill for tool contracts and workflows, and `<vault_guide>` for user conventions.

## Verification
- Added `tests/test_vault_prompts_dedup.py` guarding against prompt bloat and duplication.
- Updated `docs/vault.md` to reflect instruction division.
- `make check` passed with zero errors/warnings.
- `make test` passed: 4492 passed, 2 skipped.
- `make eval-tools` passed: all 16 vault-related tool choice cases passed (0% swapped).
