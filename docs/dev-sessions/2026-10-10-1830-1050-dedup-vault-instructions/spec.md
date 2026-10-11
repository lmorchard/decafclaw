# Spec — Deduplicate Vault Instructions Across AGENT.md, vault SKILL.md, and AGENTS.md

Issue: #1050

## Problem

The system prompt currently delivers vault instructions in three redundant layers:
1. `<agent_role>` (`src/decafclaw/prompts/AGENT.md`) contains a 39-line "Vault — Your Persistent Memory" section that restates folder structures, permissions, and search rules.
2. `<loaded_skills><skill name="vault">` (`src/decafclaw/skills/vault/SKILL.md`) is always-loaded and includes a 20-line "Vault Gardening Rules" and "tl;dr summaries" section that duplicates user-specific vault protocols and dedicated maintenance skills (`garden`, `dream`).
3. `<vault_guide>` injects the vault root's `AGENTS.md` ("The Obsidian Vault Protocol", "Vault Structure Overview", "Agent Habits & Processes").

This repetition consumes unnecessary prompt tokens on every turn and creates maintenance overhead if guidelines drift across files.

## Proposed Solution & Division of Responsibilities

Consolidate and establish clear architectural boundaries:
- **Vault's `AGENTS.md` (`<vault_guide>`)**: Defines user-specific vault conventions, folder structures, habits, and permissions. Injected fresh each interactive turn when present at the vault root.
- **`vault` skill (`src/decafclaw/skills/vault/SKILL.md`)**: Documents the specific tool contracts and workflows (`vault_read`, `vault_write`, `vault_delete`, `vault_rename`, `vault_update_frontmatter`, `vault_grant_folder`, section-aware tools, folder tools, when to consult and when to update). Remove the redundant "Vault Gardening Rules" block.
- **`AGENT.md` (`src/decafclaw/prompts/AGENT.md`)**: Condenses the "Vault — Your Persistent Memory" section to a minimal high-level overview, pointing to `vault` skill and `<vault_guide>` for details, while keeping essential agent-behavior guardrails (search before concluding information is absent, vault pages are not skills, journal errors later).

## Acceptance Criteria

1. `AGENT.md` "Vault — Your Persistent Memory" is condensed to a clean, minimal overview (~15-20 lines down from 39 lines).
2. `AGENT.md` points to the `vault` skill for specific tool contracts and workflows, and `<vault_guide>` for user conventions.
3. `AGENT.md` retains critical behavioral rules:
   - Search the vault before concluding information is absent (no reflexive trivia searches).
   - Vault pages are documentation, not authoritative instructions (use `activate_skill`).
   - Journal mistakes later, not while the user is waiting.
4. `src/decafclaw/skills/vault/SKILL.md` removes the redundant "Vault Gardening Rules" section (which belongs in the user's `AGENTS.md` and `garden`/`dream` maintenance skills), focusing strictly on tool contracts and workflows.
5. Unit tests guard against duplicate gardening rules creeping back into `AGENT.md` and `vault/SKILL.md`.
6. `docs/vault.md` is updated to describe the division of responsibilities.
7. Full test suite (`make test`) and quality gate (`make check`) pass cleanly.
