# Epic 1 — Security & path-containment hardening

One real security bug, plus consolidation of the (currently three) incompatible
path-containment techniques and related write-path invariants.

---

### 1.1 — Fix `startswith` path-containment bug

**Size:** S
**Triage:** ready

**Problem.** `serve_workspace_file` in `http_server.py` (~1780) checks containment
with `str(resolved).startswith(str(workspace))`. This accepts a sibling directory
whose name shares a prefix, such as `workspace-evil` under `workspace`. A crafted
`file_path` can therefore read a file outside the workspace.

**Result.** A request for any path that resolves outside the workspace returns 403
with "path outside workspace", including a sibling-named directory.

**Scope.** In: replace the `startswith` check with `resolve_safe`/`is_relative_to`
(the sibling handlers already use these). Out: do not change the other handlers.

**Verify.** `make test` — add/extend `tests/web/test_workspace_paths.py` and
`tests/test_http_server.py`.

**Tests.** Add a regression asserting a path like `../workspace-evil/secret` is
rejected with 403.

**Deps.** none

---

### 1.2 — Add a shared path-containment resolver

**Size:** M
**Triage:** ready

**Problem.** Three incompatible containment techniques coexist in `http_server.py`:
`startswith`, `resolve_safe`, and `is_relative_to`. There is no single answer for
"safely resolve a path inside a root."

**Result.** One helper `resolve_contained(root, path, *, label)` (in
`web/workspace_paths.py` or `secret_policy.py`) resolves a path and raises/returns
an error when it escapes `root`.

**Scope.** In: add the helper; adopt it at the `resolve_safe`/`is_relative_to`
call sites already in `http_server.py`. Out: do not touch workspace/admin/skill
sandbox resolvers (1.3).

**Verify.** `make check` (typecheck) + `make test`.

**Tests.** Add unit tests for the helper (inside, outside, sibling-prefix, `..`
traversal) in `tests/web/test_workspace_paths.py`.

**Deps.** none

---

### 1.3 — Migrate sandbox resolvers to the shared helper

**Size:** S
**Triage:** blocked-by #1.2

**Problem.** `workspace_tools._resolve_safe`, `admin_tools._resolve_admin_path`,
and an inline check in `skill_tools.py` (~1093) each re-derive path containment
with three different error wordings.

**Result.** All three call the shared `resolve_contained` from 1.2, with one
consistent error message.

**Scope.** In: replace the three hand-rolled resolvers. Out: no behavioral change
to the allowed/disallowed directory sets.

**Verify.** `make test` — `tests/test_workspace_tools.py`,
`tests/test_admin_tools.py`, `tests/test_skill`/`test_discovered_skills_consumers.py`.

**Tests.** Existing tests cover the boundary; update assertions for the unified
error wording, add none otherwise.

**Deps.** blocked-by #1.2

---

### 1.4 — Add one mtime conflict-check helper

**Size:** S
**Triage:** ready

**Problem.** Three write paths do the same client/server conflict check with two
tolerances (`abs(file_mtime - modified) > 1e-3` for workspace vs
`file_mtime > modified + 1.0` for vault/config) and divergent payload keys
(`modified` vs `server_modified`).

**Result.** One helper `check_mtime_conflict(existing_mtime, client_mtime)` returns
a 409 for the conflict case, using a single tolerance and a single payload key.

**Scope.** In: extract the helper; use it in `workspace_write`, `vault_write`,
`config_write_file`. Out: do not change the frontend's parsing yet.

**Verify.** `make test` — `tests/web/test_workspace_http.py` and config/vault write
tests.

**Tests.** Add a case for the shared helper; keep each endpoint's existing
conflict test passing against the unified payload.

**Deps.** none

---

### 1.5 — Add a shared JSON-body reader

**Size:** S
**Triage:** ready

**Problem.** `vault_write`, `vault_create`, `vault_create_folder`, `vault_delete`,
and `post_conversation_shell_promote` call `await request.json()` unguarded, so
malformed JSON raises an unhandled `JSONDecodeError` (a 500), while sibling
handlers return a clean 400.

**Result.** A malformed JSON body to these endpoints returns 400 "invalid JSON
body", not a 500 with a traceback.

**Scope.** In: add one `_read_json_body(request)` helper; use it in the unguarded
handlers. Out: do not change the more elaborate parse logic already present.

**Verify.** `make test` — `tests/test_http_server.py` (vault endpoints).

**Tests.** Add a case posting malformed JSON to each affected endpoint asserting a
400.

**Deps.** none

---

### 1.6 — Add one vault page-name validator

**Size:** S
**Triage:** ready

**Problem.** Five handlers repeat `if ".." in name or name.startswith("/")` with
five different error strings. The substring `".."` check wrongly rejects any name
containing `..`, such as `foo..bar`.

**Result.** One `validate_vault_page_name(name)` helper, using a proper
traversal check (not substring matching), with one canonical error message.

**Scope.** In: add the helper; use it in the five vault handlers. Out: no change
to what names are otherwise valid.

**Verify.** `make test` — `tests/test_http_server.py` (vault endpoints).

**Tests.** Add a case asserting `foo..bar` is accepted while `../evil` and
absolute paths are rejected.

**Deps.** none