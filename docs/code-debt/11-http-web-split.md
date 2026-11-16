# Epic 11 — HTTP/web layer split

`http_server.py` (5,293 lines) is the single biggest onboarding obstacle. Split it
per-resource, establishing the pattern with one exemplar first, then extract each
resource, then the WebSocket dispatcher and handler dedup.

---

### 11.0 — Establish the per-resource module pattern (workspace exemplar)

**Size:** S
**Triage:** ready

**Problem.** `create_app()` is a ~950-line route table; no submodule convention
exists for adding resources cleanly.

**Result.** A documented pattern: one `web/<resource>_routes.py` module (models +
handlers + router), demonstrated on the workspace resource, with `http_server.py`
aggregating routers.

**Scope.** In: extract workspace resource as the exemplar; document the convention.
Out: other resources (11.1-11.8).

**Verify.** `make check` + `make test` — `tests/web/test_workspace_http.py`,
`tests/test_http_server.py`.

**Tests.** Existing workspace HTTP tests pass unchanged.

**Deps.** none

---

### 11.1 — Extract workspace routes

**Size:** M
**Triage:** ready

**Result.** Workspace models/handlers/routes live in `web/workspace_routes.py`.

**Verify.** `make test` — `tests/web/test_workspace_http.py`.

**Tests.** Existing tests pass; no new tests (pure move).

**Deps.** none (follows 11.0 pattern)

---

### 11.2 — Extract vault routes

**Size:** M
**Triage:** ready

**Result.** Vault endpoints move to `web/vault_routes.py`.

**Verify.** `make test` — `tests/test_http_server.py` (vault) +
`tests/web/test_workspace_http.py` if shared.

**Tests.** Existing vault HTTP tests pass.

**Deps.** none

---

### 11.3 — Extract config-file + models-catalog routes

**Size:** M
**Triage:** ready

**Result.** Config-file and models-catalog endpoints move to
`web/config_routes.py` and `web/models_routes.py`.

**Verify.** `make test` — `tests/web/test_models_catalog_http.py`,
`tests/test_web_config_files_api.py`.

**Tests.** Existing tests pass.

**Deps.** none

---

### 11.4 — Extract schedule routes

**Size:** S
**Triage:** ready

**Result.** Schedule endpoints move to `web/schedule_routes.py`.

**Verify.** `make test` — `tests/web/test_schedules_api` if present, else
`tests/test_web_schedules_api.py`.

**Tests.** Existing schedule API tests pass.

**Deps.** none

---

### 11.5 — Extract widgets/canvas/sticky routes

**Size:** S
**Triage:** ready

**Result.** Widget/canvas/sticky endpoints move to `web/widget_routes.py`.

**Verify.** `make test` — `tests/test_web_widgets.py`, `tests/test_http_sticky.py`,
`tests/test_web_canvas.py`.

**Tests.** Existing tests pass.

**Deps.** none

---

### 11.6 — Extract shell-approval routes

**Size:** M
**Triage:** ready

**Result.** Shell-approval endpoints move to `web/shell_approval_routes.py`.

**Verify.** `make test` — `tests/web/test_shell_approval_panel_http.py`,
`tests/test_websocket_shell_details.py`.

**Tests.** Existing tests pass.

**Deps.** none

---

### 11.7 — Extract conversation routes

**Size:** M
**Triage:** ready

**Result.** Conversation endpoints (list/archive/session) move to
`web/conversation_routes.py` (alongside the existing `web/conversations.py`).

**Verify.** `make test` — `tests/test_web_conversations.py`,
`tests/test_conversation_folders.py`.

**Tests.** Existing tests pass.

**Deps.** none

---

### 11.8 — Extract notification + terminal routes

**Size:** S
**Triage:** ready

**Result.** Notification and terminal endpoints move to their own route modules.

**Verify.** `make test` — `tests/test_web_notifications.py`,
`tests/web/test_terminal_ws.py`.

**Tests.** Existing tests pass.

**Deps.** none

---

### 11.9 — Split the WebSocket `on_conv_event` dispatcher

**Size:** M
**Triage:** ready

**Problem.** `on_conv_event` (`websocket.py` ~957-1307) is a ~350-line `if/elif`
chain mixing projection with side effects, with the `streaming_buffer` flush in
multiple places.

**Result.** A per-event-type projector registry; one shared flush helper; each
branch a small testable function.

**Verify.** `make test` — `tests/test_web_websocket_*.py`.

**Tests.** Existing websocket event tests pass; add a flush-helper unit case.

**Deps.** none

---

### 11.10 — Dedupe confirmation projection (`_confirmation_to_dict`)

**Size:** S
**Triage:** ready

**Problem.** Three code paths hand-assemble the same confirm-request shape
(`websocket.py` ~147-170, ~1125-1147, and the load-history path).

**Result.** The event branch and load-history call `_confirmation_to_dict`.

**Verify.** `make test` — `tests/test_web_confirm.py`,
`tests/test_web_websocket_confirm_response.py`.

**Tests.** Existing confirmation-projection tests pass.

**Deps.** none

---

### 11.11 — Unify the auth preamble

**Size:** M
**Triage:** ready

**Problem.** A `@_authenticated` decorator coexists with inline username-or-401
checks in ~10 handlers; `_get_username_or_401` never returns 401.

**Result.** One auth mechanism everywhere; the helper is renamed to reflect it
returns `str | None`.

**Verify.** `make test` — `tests/test_web_auth.py`.

**Tests.** Existing auth tests pass; add a case asserting unauthenticated handlers
return 401 uniformly.

**Deps.** none

---

### 11.12 — Make `response_model=` actually validate (slice 1)

**Size:** M
**Triage:** needs-definition (compat surface)

**Problem.** Routes declare `response_model=` but return raw `JSONResponse(dict)`,
so validation never runs and the OpenAPI schema drifts.

**Design note.** Full conversion could change wire shapes (e.g. an extra `"status"`
key). Scope slice 1 to the conversation-listing endpoints; validate the output
shape is stable before widening.

**Result.** Slice 1: conversation-listing handlers return the Pydantic response
model, with validation running.

**Verify.** `make test` — `tests/test_web_conversations.py`,
`tests/test_api_client_contracts.py`.

**Tests.** Add a case asserting the serialized response matches the model; keep the
existing client-contract tests green.

**Deps.** none (spec boundaries before implementation)

---

### 11.13 — Dedupe the `models_catalog_set_*` handlers

**Size:** S
**Triage:** ready

**Problem.** Four near-identical handlers (`set_default`, `set_aux_approval`,
`set_reflection`, `set_verifier`) copy the JSON-parse preamble and log lines.

**Result.** A factory `_make_set_model_pointer_handler(action_name, setter)`.

**Verify.** `make test` — `tests/web/test_models_catalog_http.py`.

**Tests.** Existing models-catalog tests pass.

**Deps.** none

---

### 11.14 — Dedupe the shell-approval handlers' preamble

**Size:** S
**Triage:** ready

**Problem.** The ~15-line auth/ownership/status/parse block repeats 4× (and the
promote route omits the status/safety checks the others have).

**Result.** A shared `_resolve_owned_conv_idle(request, id)` dependency plus a
`_read_aux_approval_state` accessor; the promote route gains the missing checks.

**Verify.** `make test` — `tests/web/test_shell_approval_panel_http.py`.

**Tests.** Add a case asserting promote applies the same ownership/status gating.

**Deps.** none

---

### 11.15 — Hoist magic numbers to named constants

**Size:** S
**Triage:** ready

**Problem.** Unnamed literals (autocomplete caps `20`/`50`, title-preview `100`/`50`,
mtime tolerance `1e-3`/`1.0`) are scattered with inconsistent meanings.

**Result.** Named module-level constants (`_MAX_AUTOCOMPLETE_RESULTS`,
`_TITLE_PREVIEW_LEN`, `_CONFLICT_MTIME_TOLERANCE`, ...).

**Verify.** `make check` + `make test` — `tests/test_autocomplete.py`,
`tests/test_web_auto_titling.py`.

**Tests.** Existing tests pass; no new tests (rename only).

**Deps.** none