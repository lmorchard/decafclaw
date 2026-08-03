# Web UI: autocomplete for commands (#139)

**Source:** https://github.com/lmorchard/decafclaw/issues/139

Captured verbatim from the issue body (marker line stripped) at session start,
2026-08-02. The issue is authoritative; this is a snapshot.

---

## Summary

Add tab-completion support to the web UI for:

- `!` / `/` commands — skill commands and MCP prompt commands
- `@` mentions — workspace files and MCP resources

Currently users must type full command and resource names manually. With MCP prompts exposed as commands (e.g., `!mcp__server__promptname`), autocomplete becomes much more important for usability.

## Context

MCP prompt commands were added in the MCP resources/prompts/notifications PR. The double-underscore naming convention is functional but hard to type without assistance.

## Scope

- Command autocomplete in the web chat input
- Resource autocomplete (workspace files + MCP resources)
- Both Mattermost (`!` prefix) and web UI (`/` prefix)

Deferred from the MCP resources/prompts session.

---

<!-- Appended by agent-session:triage on 2026-07-29. Author's text above is unchanged. -->

## Design decisions (settled by Les, 2026-07-29)

These resolve every question that was blocking this issue. They are recorded here, in the body, because
a decision in a comment is invisible to every downstream mode.

- **Decision: this issue is the autocomplete *base*, commands only.** Resource autocomplete is split out
  to **#726**.
  - **Why:** the `@`-reference surface for workspace files and MCP resources **does not exist** (see
    Verified-false claims), so that half is feature design, not completion. #139 has an oracle today;
    #726 does not.
  - **Rejected:** keeping both in one issue — it would have forced the whole thing to `needs-review`.
- **Decision: the menu opens live when `/` or `!` is typed at the start of the line.** Not Tab-to-open.
- **Decision: fuzzy matching**, not prefix-only.
  - **Why:** command names include `mcp__<server>__<prompt>`, where prefix matching is nearly useless.
- **Decision: cursor keys (↑/↓) navigate the menu once it is open; Tab commits the highlighted
  suggestion.**
- **Decision: the command list travels over the existing WebSocket**, as a new message type — not a new
  `GET /api/commands`.
  - **Why:** responsiveness, and the TUI will want the same list and also speaks WebSocket, so one
    transport serves both clients.
  - **Rejected:** an HTTP endpoint — it would need a second implementation for the TUI.
- **Decision: Mattermost is out of scope.** The title says Web UI.
  - **Why:** autocomplete in Mattermost's own composer requires registering a Mattermost slash-command
    integration — a different transport and a different product. `rg -i 'slash_command|/api/v4/commands|create_command|autocomplete' src/decafclaw/mattermost*.py` returns nothing today.

**Stated defaults** (not decided above; assumed so planning can proceed): `Esc` dismisses the menu, and
`Enter` keeps its current send behaviour rather than committing a suggestion — consistent with Tab being
the commit key. Change either and the first criterion's assertions change with it.

## Verified-false claims

- **FALSE — "`@` mentions — workspace files and MCP resources" is presented as an existing reference
  surface. It is not.** The only `@` syntax in the codebase is `@[[PageName]]`, resolving against the
  **vault**, not the workspace tree (`memory_context.py:310,313,340-345`). `rg` over `src/` finds no
  `@`-syntax for workspace files and none for MCP resources; resources are reachable only via the
  deferred *agent* tools `tool_mcp_list_resources` / `tool_mcp_read_resource`
  (`skills/mcp/tools.py:97,144`) — **the user cannot reference a resource in a message at all.** This is
  what drove the split to #726.
- **FALSE — the prefix mapping is backwards.** "Both Mattermost (`!` prefix) and web UI (`/` prefix)".
  `dispatch_command` defaults to `prefixes = ["!", "/"]` (`commands.py:307-308`) and the web WebSocket
  path calls it with **no** `prefixes` argument (`web/websocket.py:388`), so **the web UI accepts both
  `!` and `/`.** Mattermost is the restricted one: `prefixes=["!"]` (`mattermost.py:345`). A criterion
  written from the issue's mapping would have tested the wrong prefix set — which is why the decision
  above says "`/` or `!`".
- **No data source exists for either side.** `rg '/api/commands' src/` → exit 1 (no such route; full
  route table at `http_server.py:2220-2276`). No `commands_available`-style entry in
  `web/message_types.json`. And **there is no structured command-enumeration API at all**: `format_help`
  (`commands.py:84`) builds *help text*, and `_get_mcp_prompt_commands` (`:115`) returns tuples — neither
  is a transportable list.
- Verified TRUE: MCP prompt commands exist and use the `mcp__<server>__<prompt>` convention
  (`commands.py:115-148`, name built at `:132`, parsed `:150-160`, executed `:197-280`). "Users must type
  names manually" is true — the only `autocomplete` token in web static is `autocomplete="off"` at
  `components/login-view.js:48`. Provenance is real and this issue was explicitly deferred from
  `docs/dev-sessions/2026-03-25-2030-mcp-resources-prompts-notifications/{spec.md:120,plan.md:247}`.
- Pleasing detail: **`docs/commands.md:52` documents `argument-hint` as "(future: UI autocomplete)"** —
  this issue is that future, and `SkillInfo.argument_hint` already exists
  (`skills/__init__.py:55`, parsed from `argument-hint` at `:185`). That field is the hint text for a
  suggestion row.

## Verifiable acceptance criteria

- CRITERION: GIVEN a mounted `chat-input` whose command source lists `help` and `mcp__demo__summarize`,
  WHEN the user types `/mc` at the start of the line, THEN the component SHALL render a suggestion list
  containing `mcp__demo__summarize` (fuzzy subsequence match), AND WHEN `ArrowDown`/`ArrowUp` are
  pressed the highlighted entry SHALL change, AND WHEN `Tab` is pressed the textarea value SHALL become
  `/mcp__demo__summarize ` and the menu SHALL close.
  CHECK: `cd src/decafclaw/web/static && npx vitest run components/chat-input.test.js`
  VERIFIED DISCRIMINATING — **oracle proven by running it at triage, not assumed.** A probe mounted the
  real component in vitest+jsdom and drove synthetic keyboard events:
  ```
  PROBE light-DOM textarea found: true
  PROBE Enter produced send event: {"text":"hello","attachments":[]}
  PROBE Tab defaultPrevented today: false
  PROBE any suggestion list rendered today: false
  ```
  So keyboard interaction is genuinely drivable (a synthetic `Enter` routes through the component's
  private `#handleKeydown` and emits `send`), and **today `Tab` does nothing and no menu renders** — the
  criterion fails now and cannot pass without the feature. `createRenderRoot() { return this; }`
  (`components/chat-input.js:14`) renders into light DOM, so a test can `querySelector('textarea')` with
  no shadow-root piercing and no browser. The probe was deleted; the tree is clean.
  **Check-form constraint:** do NOT use `-t <name>`. Measured on this repo: a `-t` selection matching
  nothing exits **0** with everything skipped. A missing test *file*, by contrast, exits **1**
  (`No test files found`), so file-level absence cannot pass vacuously.

- CRITERION: WHEN a web client requests the invokable-command list over the WebSocket, the server SHALL
  respond with a structured list that includes each skill command and **every MCP prompt as
  `mcp__<server>__<prompt>`**, each carrying its description and argument hint.
  CHECK: a pytest node over the new handler, **with `get_registry` monkeypatched to a fake registry
  exposing one prompt with one required and one optional argument**, asserting the response contains the
  skill command and an entry `mcp__demo__summarize` whose hint is `<text> [language]`.
  VERIFIED DISCRIMINATING: the transport does not exist — `rg '/api/commands' src/` → exit 1;
  no `commands_available` entry in `web/message_types.json`; no enumeration API in `commands.py`.
  **Freezability constraint, verified and load-bearing:** `_get_mcp_prompt_commands`
  (`commands.py:115-124`) calls `get_registry()` and **returns `[]` when it is falsy.** A check that
  relies on the developer's live MCP config answers differently on another machine and in CI — the #625
  failure mode exactly. **The fake registry must be injected inside the test.**

## Regression guards

- GUARD: `cd src/decafclaw/web/static && npx vitest run` — the JS suite. Invariant: no test lost, newly
  skipped, or newly failing. Observed at triage: `Test Files 6 passed (6) / Tests 42 passed (42)`.
- GUARD: **existing composer behaviour is unchanged when the menu is closed** — `Enter` sends
  (`chat-input.js:38-41`), `Shift+Enter` newlines, `Escape` stops while busy (`:41-43`). Verified at
  triage that `Enter` → `send` with `{"text":"hello","attachments":[]}`. The autocomplete keydown hook
  must not regress these; this is the guard most at risk, since Tab/arrows/Esc all have to route
  conditionally.
- GUARD: `uv run pytest tests/test_web_websocket_commands.py tests/test_commands.py` — command dispatch
  through the socket keeps working, **including that the web path still accepts both `!` and `/`** and
  Mattermost stays `!`-only. Observed at triage: `test_web_websocket_commands.py` → `1 passed in 1.54s`
  (note: that file has exactly one test — thin coverage for the path this issue extends).
- GUARD: `make check-message-types` — a new WebSocket message type means a `web/message_types.json`
  entry plus regenerated `message_types.py`, `lib/message-types.js`, `docs/websocket-messages.md` and
  `tui/src/types.generated.ts`, all in the same commit. **Do not hand-edit the generated files.**
- GUARD (invariant): full Python suite green. **UNRUN (needs a serial run)** — not verified.

## Tier: `auto-ok`

Neither trigger fires, **now that the four decisions above are settled.**

**Trigger 1:** both criteria pair with concrete tests whose harnesses exist today — the component one
**proven by running a probe against the real component**, the transport one using ordinary pytest with
an injected fake registry. Both fail today. Neither is satisfiable without the work: the first asserts
the filtered suggestion *and* the resulting textarea value after Tab, not the presence of a name; the
second asserts a specific hint string built from argument metadata. No criterion rests on human
judgment — the previously-subjective parts (trigger key, match semantics, nav keys) are now pinned to
specific keys and specific input→output pairs.

**Trigger 2:** no risk-gated path — client-side JS, an additive WebSocket message type, and a
server-side enumeration helper. No auth, secrets, data migration/deletion, deploy/infra/CI config.

**One scope bound is load-bearing for this tier, and it is deliberate: implement fuzzy matching without
adding a new direct npm dependency.** A hand-rolled subsequence scorer is ~20 lines. Promoting
`@codemirror/autocomplete` (currently only a *transitive* entry in `package-lock.json`) to a direct
dependency **would fire trigger 2** and this issue must be re-tiered if that is judged necessary. This
is the "mechanism choice and tier are coupled" case: how you propose to do it changes how it must be
reviewed.

## Patterns to follow

- `components/wiki-editor.test.js` is the component-test pattern (mount via `document.createElement`,
  `await el.updateComplete`, then query light DOM).
- `SkillInfo.argument_hint` (`skills/__init__.py:55`) is the hint text for a suggestion row;
  `_get_mcp_prompt_commands` (`commands.py:115`) already builds `"<text> [language]"`-style hints and is
  the natural source to reuse rather than reimplement.
- `web/message_types.json` + `make gen-message-types` (`Makefile:80`) is the only correct way to add a
  message type.

## What we're NOT doing

- **Resource / workspace-file `@` autocomplete** — split to **#726**, which must first invent the
  reference syntax and injection semantics.
- **Mattermost autocomplete** — out of scope per the decision above.
- **A recursive/prefix workspace search endpoint** — only needed by #726; `/api/workspace`
  (`http_server.py:866-886`) stays one-folder-per-request.
- Adding a direct npm dependency (see the tier's scope bound).

## Note on sizing

Board size **M** was about right for the body as written and is now generous: command-only autocomplete
in `chat-input.js` (208 lines) plus one WebSocket message type plus `components/chat-input.test.js` is a
clean **S**. The L-shaped work moved to #726.
