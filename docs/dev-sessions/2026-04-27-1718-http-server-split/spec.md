# Split `http_server.create_app` (#383)

## Problem

`http_server.create_app` is one nested function spanning ~1500 lines (lines 85–1715). 40+ route handlers are defined inside as closures capturing `config`, `event_bus`, `manager`, and `app_ctx`. Plus inline constants (`_CONFIG_FILES`, folder names) and duplicated auth helpers (`_authenticated` decorator + `_require_auth`).

## Approach

Single PR, many small commits. Each commit is a coherent group (one or two domains' handlers) so reviewers can step through commit-by-commit.

**Dependency-passing pattern:** Use Starlette's `request.app.state` namespace. `create_app` populates `app.state.config`, `app.state.event_bus`, `app.state.manager`, `app.state.app_ctx` once at construction; module-level handlers read what they need via `request.app.state.<dep>`. No new dataclasses, no per-route lambda registrations, no signature plumbing.

**Auth helpers:** Unify `_authenticated` (decorator) and `_require_auth` (called manually in 3 places like the WebSocket and `serve_vault_page`). One module-level `_authenticated` decorator + a small `_get_username_or_401` helper for the manual-call cases.

## Commit plan

1. **Foundation** — hoist module-level constants (`_CONFIG_FILES`, conv-folder name constants), pure helpers (`_validate_folder_param`, `_workspace_file_entry`, `_can_write_as_text`, `_resolve_vault_page`, `_vault_root`, `_vault_source_type`, `_prune_empty_parents`, `_workspace_rename`, `_vault_rename`); populate `app.state.{config,event_bus,manager,app_ctx}` in `create_app`; introduce module-level `_authenticated` decorator + `_get_username_or_401` helper. Keep all closures temporarily wrapping the module helpers — non-invasive.
2. **Health + auth + confirm** — hoist `health`, `handle_confirm`, `auth_login`, `auth_logout`, `auth_me`. Smallest group; locks in the pattern.
3. **Conversations** — `list_conversations`, `list_archived_conversations`, `list_system_conversations`, `create_conversation`, `get_conversation`, `rename_conversation`, `get_conversation_history`, `get_context_diagnostics`, `delete_conversation`, `archive_conversation`, `unarchive_conversation`.
4. **Conversation folders** — `create_conv_folder`, `delete_conv_folder`, `rename_conv_folder` plus the `_validate_folder_param` already-hoisted helper.
5. **Notifications** — `list_notifications`, `notifications_unread_count`, `notifications_mark_read`, `notifications_mark_all_read`.
6. **Workspace** — `serve_workspace_file`, `workspace_list`, `workspace_read_json`, `workspace_write`, `workspace_delete`, `workspace_create`, `workspace_recent`.
7. **Config files** — `config_list_files`, `config_read_file`, `config_write_file`.
8. **Vault** — `vault_list`, `vault_recent`, `vault_read`, `vault_write`, `vault_create`, `vault_create_folder`, `vault_delete`, `serve_vault_page`, `_vault_rename`. Plus the `/api/wiki/*` aliases.
9. **Upload + canvas + widgets + WebSocket adapter** — `handle_upload`, `get_canvas_state`, `post_canvas_set`, `get_canvas_page`, `list_widgets`, `serve_widget_js`, `ws_chat`. WebSocket already calls into `web/websocket.py` so it's mostly unchanged.
10. **Cleanup** — verify `create_app` is now just app construction + `app.state` setup + route table + static mounts. Update CLAUDE.md key-files line if needed.

## Test plan

- After each commit: `make lint`, `make test` clean.
- `tests/web/test_workspace_http.py` and friends already cover the routes; they must keep passing.
- No new test files needed — the structural change shouldn't change behavior.

## Risk

The HTTP server is the only path for the web UI and Mattermost button callbacks. Behavior changes should be zero — every commit must preserve the existing route signatures and responses. Pytest covers the API routes thoroughly. A live smoke test is desirable before merge — note in PR.

## Out of scope

- Centralizing WebSocket message types (separate issue).
- Reorganizing handlers across files / packages (this PR keeps everything in `http_server.py`; future work could split into `http_server/{auth,conversations,vault,workspace}.py` if useful).
- Tightening the `app_ctx` interface (it's still passed through to the WebSocket).
