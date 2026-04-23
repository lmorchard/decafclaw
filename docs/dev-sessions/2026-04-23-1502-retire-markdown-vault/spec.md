# Retire `markdown_vault`, fold section-aware tools into `vault`

Closes #264.

## Goal

Unify the two markdown trees (user's Obsidian vault at `workspace/obsidian/main/` and the agent's vault at `workspace/vault/`) into one configurable vault root, fold the three valuable section-aware tools from `markdown_vault` into `vault`, and retire `markdown_vault` entirely.

## Decisions locked during brainstorm

- **Vault root unification is the target.** The decafclaw vault should point at the user's full Obsidian vault. `agent/` becomes a peer subfolder alongside `journals/`, `pages/`, `templates/`, etc. Vault root remains configurable via `vault_path` in `config.json` (already supported — we're just setting it intentionally instead of relying on the default).
- **Three tools fold**, renamed to the `vault_*` convention and taking vault-relative paths:
  - `md_show` → `vault_show_sections`
  - `md_move_lines` → `vault_move_lines`
  - `md_section` → `vault_section`
- **Two tools drop entirely:** `md_create`, `vault_daily_path`. Their one caller (`daily-todo-migration`) is being deleted too; if a future reworked version needs them, they can be reintroduced.
- **`contrib/skills/daily-todo-migration/` is deleted.** A follow-up issue tracks recreating it on top of the unified vault.
- **Migration helper is a standalone script**, not a Makefile target or CLI subcommand.
- **Content migration is run by the user, not automatic.** The session delivers code + migration script; the user runs the script deliberately.

## Scope

### In scope

1. Rewrite three tools in `src/decafclaw/skills/vault/tools.py`:
   - `vault_show_sections(page, section=None)` — document outline or section content with absolute line numbers.
   - `vault_move_lines(from_page, to_page, lines, to_section=None, position="append")` — move numbered lines between vault pages.
   - `vault_section(page, action, section=None, title=None, level=1, after=None, before=None, parent=None)` — add/remove/rename/move sections.
   - Path semantics: all parameters vault-relative (e.g. `agent/pages/foo` or `journals/2026/2026-04-23`). The implementations resolve to disk via `_vault_root(ctx.config)` like existing vault tools.
   - Arg-name consistency: `page` / `from_page` / `to_page` (not `file` / `from_file` / `to_file`).
   - Same write guardrail as `vault_write`: writes outside `agent/` refuse unless the target is already a user-writable path per existing vault policy. (Moving lines INTO a user file is a write; moving lines OUT of a user file is also a write — both are restricted the same way existing `vault_write` handles agent-folder guardrails.)
2. Delete `src/decafclaw/skills/markdown_vault/` (SKILL.md, tools.py, tests/).
3. Delete `contrib/skills/daily-todo-migration/`.
4. Migrate the valuable test cases from `src/decafclaw/skills/markdown_vault/tests/test_vault.py` into `tests/test_vault_section_tools.py` (new file), rewritten against the folded tools and vault-relative paths.
5. Update test fixtures referencing the string `"markdown_vault"`:
   - `tests/test_commands.py` — swap to another existing skill name (`"tabstack"` works as a fork-dep fixture).
   - `tests/test_skills.py` — same.
   - `tests/test_context.py` — same.
6. Create `scripts/migrate_vault_root.py`:
   - Args: `--from <old_vault_root>`, `--to <new_vault_root>`, `--config <path-to-config.json>` (default: `data/decafclaw/config.json`).
   - Behavior: refuse if `<new>/agent/` already exists. Otherwise `shutil.move` old `agent/` into new root, update `config.json`'s `vault_path`, print a reminder to run `make reindex`.
   - Dry-run by default; `--apply` to execute.
7. `data/decafclaw/skill_permissions.json` is gitignored, so the PR doesn't touch it. User can manually delete the now-inert `"markdown_vault": "always"` entry post-merge if they want a tidy file.
8. Remove the stale reference in `docs/commands.md:14` (`vault_set_path, vault_daily_path, vault_move_items` — all three dead).
9. Update `docs/vault.md` to document the three new section-aware tools and the "vault root is the user's Obsidian root" setup story.
10. Update `CLAUDE.md` key-files list: remove the `markdown_vault` skill entry, note the new vault tools under the vault-skill description.
11. File follow-up issue: "Recreate `daily-todo-migration` skill on unified vault."

### Out of scope

- Rewriting `daily-todo-migration`.
- Changing what `vault_journal_append` does or where agent journals live.
- Making `daily_notes_path` configurable (dropped with `vault_daily_path`).
- Any changes to embedding schema; `make reindex` handles rebuild.

## Tool priority / budget

Skill tools inherit `critical` priority while their skill is active — the `priority` field only applies to core tools in `tools/`. Since `vault` is always-loaded, the three new tools are always active. Net impact is three extra tool definitions (~500-700 tokens in the always-active set), a negligible fraction of the context window and a fair trade for retiring ~1095 lines of `markdown_vault`. Tool descriptions should be kept tight but not exotic.

## Migration workflow (user-run, documented)

```
# Ensure no agent running
# 1. Back up just in case
cp -a data/decafclaw/workspace/vault data/decafclaw/workspace/vault.bak

# 2. Run the script (dry-run first)
python scripts/migrate_vault_root.py \
    --from data/decafclaw/workspace/vault \
    --to data/decafclaw/workspace/obsidian/main

python scripts/migrate_vault_root.py \
    --from data/decafclaw/workspace/vault \
    --to data/decafclaw/workspace/obsidian/main \
    --apply

# 3. Rebuild embeddings
make reindex

# 4. Restart agent
```

The script's `--apply` mode does exactly three things:
1. `mv <old>/agent <new>/agent`
2. Patch `config.json`: add/update `"vault_path": "<new>"`.
3. Print "run `make reindex` next".

It does NOT run `make reindex` itself (keeps the script pure filesystem + config; reindex has its own failure modes you want to see separately).

## Test plan

- Unit tests for `vault_show_sections`, `vault_move_lines`, `vault_section`, migrated from `markdown_vault/tests/test_vault.py`, rewritten to use the `tmp_path`-based vault fixture used by existing vault tool tests.
- Regression: `make lint && make test` clean.
- Manual smoke: after applying migration in your environment, `vault_search` finds known pages, `vault_show_sections` works on a daily note at `journals/2026/2026-04-23`, and the agent can activate the now-smaller bundled-skills catalog without errors.

## Risks

- **`markdown_vault` skill removal vs. active permissions.** You have `"markdown_vault": "always"` in skill_permissions.json. After removal the entry becomes inert (the skill no longer exists). The PR removes it to keep the file tidy.
- **Stale conversation context dumps.** Old `.context.json` sidecars reference `md_show` etc. in their tool lists; these are write-once artifacts of past turns. No action needed, they stay historical.
- **Pre-emptive search keyword matches.** `preempt_search.py` tokenizes user messages against tool names/descriptions. Old `md_*` names are gone; new `vault_*_sections` etc. names pick up "section"/"line" keywords naturally. No wiring change needed.
