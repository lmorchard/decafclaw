# Web terminal: agent can orphan a PTY / create dead terminal tab via canvas tools

**Goal:** Prevent the agent from orphaning terminal PTYs or creating dead terminal tabs through canvas tools.

**Source:** https://github.com/lmorchard/decafclaw/issues/625

## Current state

Follow-up from #442 whole-branch review (non-blocking).

Two bounded, no-access-escalation gaps in how the agent-facing canvas tools interact with human-only terminal tabs:

1. **Orphaned PTY.** `tools/canvas_tools.py` `canvas_close_tab` calls `canvas.close_tab(...)` with no `registry`, and `canvas_clear` → `clear_canvas` removes tabs with no kill path. So the agent can remove a human-opened terminal tab (or clear the canvas) leaving the shell running until conversation-delete/server-shutdown. Not an access escalation (the agent still cannot read/write/attach the PTY), but orphaned shells count against `max_sessions_per_conv` (default 8), so repeated human-open + agent-close could eventually block new terminals with "Max sessions reached."

2. **Dead terminal tab.** The terminal `widget.json` declares `modes: [\"canvas\"]`, so `canvas_new_tab(widget_type=\"terminal\", ...)` succeeds for the agent. No PTY is spawned (only the server-side `/terminal` command spawns), so the client just shows a `[session ended]` banner — cosmetic litter, no shell.

### Fix ideas
- Thread the `TerminalRegistry` into the `canvas_close_tab` / `canvas_clear` tool call sites so dropping a terminal tab kills its PTY; or have canvas mutations that drop terminal tabs emit a kill.
- Reject `widget_type == \"terminal\"` in the agent-facing `canvas_new_tab` (or add an `agent-createable: false` descriptor flag).

See docs/web-terminal.md and docs/dev-sessions/2026-05-06-2042-web-terminal-canvas/.

---

## Design decisions (resolved at intake, 2026-07-26/27)

Triage left three architecture questions open, which is *why* this issue was `needs-review`. All
three are now decided; the criteria below depend on them and are unreadable without them.

**D1 — the kill capability reaches agent tools as a narrowed façade on `Context`, not the registry.**
`Context` gains `terminal_registry: Any = None  # set by ConversationManager`, mirroring the existing
`request_confirmation` pattern (declared on `Context`, assigned in `conversation_manager.py` near
line 1471, sourced from `app.state.terminal_registry` — which already sits beside `state.manager` at
`http_server.py:1800`). The object assigned is **not** the `TerminalRegistry`: it is a small façade
forwarding only `.get(conv_id, tab_id)` and `.kill(session)`.

*Why not the registry itself* (the first answer, corrected during intake): `TerminalRegistry` also
exposes `spawn`, `attach`, `write_input`, `detach` and `shutdown_all`. Handing it to agent-side code
would grant exactly the capabilities the terminal widget's own `widget.json` promises are absent —
*"the agent cannot spawn, attach to, or read terminals."* And **G3 would not have caught it**: G3
forbids *imports* of `terminals.py` from `tools/`, but a capability arriving as an object imports
nothing. Hence C4, which checks the thing G3 structurally cannot see.

*Why a façade and not a bare callable:* `canvas.close_tab` uses exactly `.get` and `.kill`, so its
already-tested `registry=` kwarg contract is unchanged and
`tests/test_canvas.py::test_close_tab_kills_terminal_pty` keeps passing untouched. A callback shape
would force a change to the one path already tested for this behaviour.

**D2 — C1 is enforced by an `agent_createable` descriptor flag, not by rejecting the name
`\"terminal\"`.** `widget.json` gains an optional boolean (default `true`), carried on
`WidgetDescriptor` beside the existing `accepts_input`, and checked in `canvas.new_tab`. Rejected:
hardcoding `widget_type == \"terminal\"` in `canvas.py` — one line, but the next human-only widget gets
no protection and no failing test. The flag also turns the terminal `widget.json`'s existing prose
claim into something enforced, colocated with the claim.

**D3 — a failed PTY kill does not block `clear_canvas`.** Mirrors `close_tab`'s documented semantics
(*\"Kill failures are logged, not raised — they must never block tab removal\"*). One rule for both
paths; a wedged PTY must not permanently block clearing the canvas.

## Verifiable acceptance criteria

**C1 — the agent cannot create a widget marked un-createable.**
WHEN `canvas.new_tab` is called with a widget whose descriptor sets `agent_createable: false`,
THE SYSTEM SHALL reject the call and create no tab.
- CHECK: `uv run pytest tests/test_canvas.py::test_new_tab_rejects_agent_uncreateable_widget`
- The assertion must (a) load a widget registry in which `terminal` is present, and (b) assert the
  rejection **reason**, not merely `ok is False`.
- Demonstrated absent at intake: with the real bundled registry loaded,
  `new_tab(cfg, \"c1\", \"terminal\", {\"session_id\": \"x\"})` → `ok=True, error=''`.
- **Why the reason must be asserted:** triage's original check was `assert .ok is False`, which
  passes vacuously whenever the widget registry is uninitialised — observed returning
  `ok=False, error='widget registry not initialized'` with no fix applied. That check was
  satisfiable without the work.

**C2 — closing a terminal tab through the agent tool kills its PTY.**
`tool_canvas_close_tab` SHALL pass a non-`None` registry through to `canvas.close_tab`.
- CHECK: `uv run pytest tests/test_canvas_tools.py::test_canvas_close_tab_passes_registry`
- Demonstrated absent at intake: spying on `canvas_tools.canvas_mod.close_tab`, the captured kwargs
  were `['emit']`; `registry` was `<MISSING>`.

**C3 — clearing the canvas kills terminal PTYs.**
`canvas.clear_canvas` SHALL accept a registry and kill sessions for `terminal` tabs, fail-open per D3.
- CHECK: `uv run pytest tests/test_canvas.py::test_clear_canvas_kills_terminal_ptys`
- Demonstrated absent at intake: `inspect.signature(canvas.clear_canvas)` is
  `(config, conv_id: str, emit=None) -> CanvasOpResult` — no registry parameter, so passing one
  raises `TypeError`. (`close_tab` already has `registry=None`.)

**C4 — the object reachable from agent-side code cannot touch a PTY.**
THE SYSTEM SHALL expose to agent tools an object providing `get` and `kill` and **no** PTY-access
method (`spawn`, `attach`, `detach`, `write_input`, `set_viewport`, `shutdown_all`).
- CHECK: `uv run pytest tests/test_terminals.py::test_agent_terminal_handle_exposes_no_pty_access`
- The assertion must be **conjunctive**: the façade exists, `get` and `kill` are callable, *and* each
  forbidden name is absent. Asserting only the absences is satisfiable by never building the façade
  at all (`hasattr(None, \"attach\")` is `False`).
- Demonstrated absent at intake: no façade type exists in `terminals.py` today.

## Regression guards (pass today; must keep passing — not criteria)

- **G1:** `tests/test_canvas_tools.py::test_canvas_close_tab_returns_new_active` and
  `::test_canvas_clear_with_tabs` — non-terminal close/clear behaviour for the agent tools.
- **G2:** `tests/test_canvas.py::test_close_tab_kills_terminal_pty` and
  `::test_close_tab_non_terminal_does_not_kill` — the existing module-level kill / no-kill logic.
- **G3 (boundary):** `tests/test_terminals.py::test_no_agent_side_imports` — `terminals.py` stays
  un-imported by anything under `decafclaw/tools/` or `decafclaw/skills/`. Necessary but **not
  sufficient**, which is why C4 exists.
- **G4 (negative control):** `tests/test_canvas_tools.py::test_canvas_new_tab_returns_tab_id` and
  `tests/test_canvas.py::test_new_tab_creates_and_activates` — normal widgets stay agent-createable.
  Blocks an over-broad C1 fix that rejects everything.
- Observed at intake, all seven together: `7 passed in 2.34s`.

## What we're NOT doing

- **Not giving the agent any PTY read/write/attach capability.** C4 exists to keep that true; this
  issue only ever *removes* a shell, never touches one.
- **Not changing the `/terminal` command path.** Spawning stays server-side and human-initiated.
- **Not reworking `max_sessions_per_conv`.** Orphaned shells counting against the cap is the symptom;
  the fix is not leaking them, not raising or redesigning the limit.
- **Not auditing other widgets for `agent_createable`.** The flag defaults to `true`, so every
  existing widget keeps its current behaviour; only `terminal` is marked in this change.
- **Not retrofitting the other two `Fix ideas`** from the original text: threading the raw
  `TerminalRegistry` (superseded by D1's façade) and reject-by-`widget_type`-name (superseded by D2).

## Tier: `needs-review`

**Trigger 2 — authorization.** Every criterion above reduces to a concrete test and all four were
demonstrated failing today, so trigger 1 does not fire and the withheld decisions are resolved. But
the diff governs what agent-side code is permitted to do to human-only PTYs and introduces a
capability-bearing object reachable from the agent path. `acceptance-criteria.md` is explicit that a
perfectly-tested authorization change still deserves human eyes, and the intake pass itself is the
argument for the rule: the first proposed wiring would have handed the agent `spawn`/`attach`/
`write_input` while leaving every existing guard green.

Not downgraded to `auto-ok` despite four checkable criteria — the trigger is the *path*, not the
verifiability.
