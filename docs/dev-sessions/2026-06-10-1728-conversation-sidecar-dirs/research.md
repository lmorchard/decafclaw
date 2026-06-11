# Research: conversation sidecar paths

Branch `conversation-sidecar-dirs`. All file:line refs against that worktree.

## 1. All per-conversation sidecars (the issue undercounted: lists 4, there are 9 flat + 2 dir-based)

**Flat (`workspace/conversations/{conv_id}.SUFFIX`):**

| Type | Pattern | Helper | file:line |
|---|---|---|---|
| JSONL archive | `{conv_id}.jsonl` | `archive_path()` | archive.py:16-18 |
| Compacted archive | `{conv_id}.compacted.jsonl` | `_compacted_path()` | archive.py:32-33 |
| Notes | `{conv_id}.notes.md` | `notes_path()` | notes.py:35-47 |
| Decisions slice | `{conv_id}.decisions.json` | `_slice_path()` | compaction_decisions.py:81-98 |
| Context diagnostics | `{conv_id}.context.json` | `_context_sidecar_path()` | context_composer.py:109-118 |
| Canvas state | `{conv_id}.canvas.json` | `_canvas_sidecar_path()` | canvas.py:41-50 |
| Activated skills | `{conv_id}.skills.json` | `_skills_path()` | persistence.py:10-11 |
| Skill data | `{conv_id}.skill_data.json` | `_skill_data_path()` | persistence.py:32-33 |
| Vault grants | `{conv_id}.vault_grants.json` | `_grants_sidecar_path()` | skills/vault/_grants.py:24-35 |

**Directory (`workspace/conversations/{conv_id}/...`):**

| Type | Path | Helper | file:line |
|---|---|---|---|
| Workflow journal | `{conv_id}/workflow.json` | `workflow_path()` | workflow/paths.py:26-27 |
| Conversation dir | `{conv_id}/` | `workflow_dir()` | workflow/paths.py:16-23 |
| Uploads | `{conv_id}/uploads/*` | `uploads_dir()` | attachments.py:12-14 |

## 2. Existing directory convention (PR #573)

- `workflow/paths.py:11-13` `_safe_conv_id(conv_id)` → strips `/`, `\`, `..`; falls back to `"_invalid"`.
- `workflow/paths.py:16-23` `workflow_dir(config, conv_id, *, create=False)` → `conversations/{safe}/`, mkdir on demand.
- `workflow/paths.py:26-27` `workflow_path()` → `{dir}/workflow.json`.
- journal save/load: workflow/journal.py:64-77.
- **No shared per-conversation dir helper** outside workflow; uploads (attachments.py:12-14) also build the dir but with NO sanitization.

## 3. conv_id shape & safety

- Mattermost: `conv_id = msg["root_id"] or msg["channel_id"]` (mattermost.py:697-698) — UUIDs, safe.
- Web UI: from REST route param `{conv_id}` — untrusted user input.
- Defense-in-depth already standard: each flat helper strips `/`,`\`,`..`, sandboxes via `.is_relative_to(base)`, returns `_invalid.*` sentinel on escape. (notes.py:35-47, compaction_decisions.py:81-98, context_composer.py:109-118, canvas.py:41-50.)
- `attachments.py:12-14` (uploads) and `persistence.py:10-11,32-33` (skills) do NOT sanitize.
- Test of sandboxing: test_notes.py:43-57 (`../../etc/passwd` → `etcpasswd.notes.md`; `../..` → `_invalid.notes.md`).

## 4. Globbing / discovery of sidecars

| file:line | Op | Pattern | Purpose |
|---|---|---|---|
| conversation_manager.py:1813 | `conversations_dir.glob("*.jsonl")` | flat | startup_scan: recover pending confirmations (skips `.compacted.jsonl` via stem check, :1815) |
| conversation_tools.py:23 | `conv_dir.glob("*.jsonl")` | flat | `conversation_search` tool |
| conversation_manager.py:1805 | base path | — | `workspace_path / "conversations"` |
| http_server.py:668, web/conversations.py:70 | base path | — | export / web endpoints |

No `iterdir`/`listdir` on conversations/. Workflow/uploads are key-lookups, not globbed.

## 5. Workspace root & migration pattern

- `config.workspace_path` (config.py:213-215) = `{agent_path}/workspace`; `agent_path` (config.py:209-210) = `{agent.data_home}/{agent.id}`.
- **No unified `conversations_dir` helper.** Each module builds independently.
- **Migration template:** `scripts/migrate_to_vault.py` — `--dry-run`, `src.rglob("*")` + `shutil.move()`, cleans empty dirs, `load_config()`. Makefile targets `make migrate-vault` / `make migrate-vault-dry`. No sidecar-migration target yet.

## 6. Tests hand-constructing sidecar paths

- test_archive.py:8-9 (`conv-123.jsonl`)
- test_notes.py:38-57 (`notes_path()` + sandbox)
- test_compaction_decisions.py:104-121 (`_slice_path()` sandbox)
- test_context_composer.py:1275-1291 (`write/read_context_sidecar`)
- test_web_conversations.py (`conv_dir / f"{conv_id}.jsonl"`, `.context.json`)
- test_restore_history.py (`_conv_dir()` → `.jsonl` + `.compacted.jsonl`)
- test_workflow_paths.py (`workflow_dir`/`workflow_path`)
