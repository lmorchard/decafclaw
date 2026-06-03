# Workflow Engine Research — Current State Documentation

## 1. Phase Dispatch Flow

**Inline phase dispatch** (tool layer to engine):
- `tool_phase_advance(ctx, target_phase_id, reason)` → `engine.advance(ctx, state, target, reason)` [workflow_tools.py:411, engine.py:59]
- `advance()` checks for gate; if gated, calls `_enter_gate(ctx, state, edge_idx, edge, reason)` [engine.py:84–85, 92–109]
- Non-gated: calls `_apply_transition(ctx, wf, state, edge_idx, target, reason, gate_response=None)` [engine.py:87–89, 159–184]

**Gate flow** (synchronous):
- `_enter_gate()` sets `state.status = RunStatus.PAUSED_GATE`, saves pending gate state, returns `AdvanceResult` with `EndTurnConfirm` signal [engine.py:92–109]
- Tool layer wraps buttons via `confirm.on_approve = _on_approve` / `on_deny = _on_deny` closures [workflow_tools.py:435–448]
- User clicks → `finalize_gate_response(ctx, state, approved=bool)` [engine.py:112–156]
- Reloads fresh state inside lock (TOCTOU guard), routes to target or on_deny, calls `_apply_transition(...)` with gate_response logged [engine.py:153–156]

**Subagent dispatch** (synchronous from tool layer):
- `tool_phase_advance()` calls `engine.dispatch_subagent_if_needed(ctx, fresh)` after non-gated transition [workflow_tools.py:452–460]
- `dispatch_subagent_if_needed(ctx, state)` → `dispatch_and_finalize_subagent(ctx, state, phase_id)` [engine.py:304–347, 187–270]
- `dispatch_and_finalize_subagent()`:
  1. Calls `subagent._run_child(ctx, state, phase)` via `from . import subagent as wf_subagent` [engine.py:207, 219–221]
  2. On return, `verify_subagent_outputs(ctx, state, phase_id)` checks declared outputs present [engine.py:231, 273–294]
  3. Auto-advances via `_apply_transition(ctx, wf, state, edge_idx=0, target=phase.next_phases[0].id, ...)` [engine.py:261–267]
- Chain-caps at 8 iterations to prevent infinite subagent→subagent loops [engine.py:301, 320]

**LLM vs code boundary**:
- Engine: pure state machine (code)
- Tool layer: LLM calls `phase_advance` → code handles transition
- Gate confirmation: LLM turns end, user clicks approve/deny → callbacks fire → code applies transition
- Subagent: code spawns child via `manager.enqueue_turn(TurnKind.CHILD_AGENT, ...)` [subagent.py:331–338]

---

## 2. Phase Frontmatter Contract

**Frontmatter fields** parsed in `loader.py:_parse_phase()` [94–143]:

| Field | Type | Parse Location | Consumed By |
|-------|------|---|---|
| `kind` | enum: `inline` \| `subagent` | loader.py:102–107 | PhaseDef.kind; validates INLINE vs SUBAGENT semantics in engine [engine.py:179, 325] |
| `tools` | `list[str]` (glob patterns) | loader.py:109–113 | workflow_tools._build_phase_allowed_set() expands globs [249–288]; subagent._resolve_phase_tools() [150–166] |
| `outputs` | `list[str]` (filenames, subagent only) | loader.py:115–124 | engine.verify_subagent_outputs() checks artifacts/ [273–294]; loader validates non-empty for subagent [237–242] |
| `next-phases` | `list[dict]` | loader.py:126; _parse_edges() [69–91] | engine._find_edge() routes transitions [52–56]; _apply_transition() picks first edge for subagent auto-advance [261–267] |
| ↳ `id` | string (target phase) | loader.py:80–83 | EdgeDef.id; must resolve in phases dict [loader.py:219–223] |
| ↳ `when` | string (annotation) | loader.py:88 | EdgeDef.when; LLM-facing routing hint in phase_advance enum description [workflow_tools.py:90–94] |
| ↳ `gate` | `dict` (optional) | loader.py:84–85; _parse_gate() [53–66] | EdgeDef.gate; triggers _enter_gate() path [engine.py:84–85]; blocks subagent phases [loader.py:247–251] |
| ↳ ↳ `type` | string: `"review"` only | loader.py:54, 55 | GateDef.type; EndTurnConfirm rendered [engine.py:101–107] |
| ↳ ↳ `message` | string | loader.py:62 | GateDef.message → EndTurnConfirm.message [engine.py:102] |
| ↳ ↳ `approve-label` | string (default: "Approve") | loader.py:63 | GateDef.approve_label → button text [engine.py:103] |
| ↳ ↳ `deny-label` | string (default: "Deny") | loader.py:64 | GateDef.deny_label → button text [engine.py:104] |
| ↳ ↳ `on-deny` | string (phase id, empty = stay) | loader.py:65 | GateDef.on_deny; routes to phase if user denies [engine.py:96, 137] |
| `context-profile` | `dict[str, any]` | loader.py:127–130 | PhaseDef.context_profile; passed to ContextComposer as overrides [context.py:134]; e.g. `memory-retrieval: off` [draft.md:4–6] |
| `subagent-skill` | string (skill name, subagent only) | loader.py:131 | PhaseDef.subagent_skill; if set, child boots skill body as prompt instead of phase.prompt [subagent.py:200–215] |

**SKILL.md frontmatter** (loader.py:155–203):
- `name`, `description`, `kind: workflow`, `workflow.initial-phase` [loader.py:157–169]
- `required-skills: [skill, ...]` — auto-activated before workflow starts [loader.py:193–203]; consumed by tool_workflow_start() [workflow_tools.py:323–329]
- `user-invocable: bool`, `argument-hint: string` [loader.py:210–212] — not workflow-engine–consumed, UX metadata

---

## 3. `phase_advance` Tool Surface

**Definition location**: `workflow_tools.py:72–123` (dynamic per-turn); registered in WORKFLOW_TOOLS dict [531–539] under key `phase_advance` (injected dynamically, not in static list)

**Enum of valid `target_phase_id` values** built dynamically:
- `build_phase_advance_definition(ctx)` [72–123] fetches current workflow + phase from state
- Enumerates `phase.next_phases[*].id` [85] — only valid targets are the current phase's outgoing edges
- Description includes `when:` clauses for LLM routing [90–94]
- Returns None if no workflow active or phase has no outgoing edges [79–83]

**Other workflow tools** (always-loaded, priority `normal` or `critical`):
- `workflow_start(ctx, name: str)` [293–354] — start a workflow
- `workflow_status(ctx)` [381–408] — show state, transitions, history
- `workflow_abort(ctx, reason: str)` [357–378] — abort and archive
- `workflow_artifact_read(ctx, relative_path: str)` [516–526] — read from artifacts/
- `workflow_artifact_write(ctx, relative_path: str, content: str)` [500–513] — write to artifacts/
- `phase_advance` (dynamic, priority `critical`) [72–123] — transition between phases

**Tool presentation per phase**:
- `refresh_workflow_tools(ctx)` [126–182] manages per-phase restrictions:
  - INLINE phases: tool catalog restricted to `_build_phase_allowed_set()` [249–288] union = (phase globs + WORKFLOW_TOOLS + critical baseline)
  - SUBAGENT phases: tools not restricted in main agent (dispatcher handles it); child receives phase whitelist minus `_BLOCKED_FOR_CHILDREN` [34–44]
- Workflow admin tools (`workflow_*`) always available when a workflow is active [279]
- Critical-priority tools (notes_*, checklist_*) always available even under phase restriction [284]
- `phase_advance` injected only if current phase has outgoing edges; removed otherwise [145–159]

---

## 4. Gate Firing

**Execution path**:
1. Tool layer calls `tool_phase_advance(target_phase_id, reason)` [workflow_tools.py:411]
2. Engine: `advance(ctx, state, target, reason)` → checks for `edge.gate` [engine.py:84]
3. If gated, `_enter_gate(ctx, state, edge_idx, edge, reason)`:
   - Sets `state.status = RunStatus.PAUSED_GATE` [engine.py:97]
   - Saves `state.pending_gate = {"edge_target": edge.id, "on_deny": on_deny}` [engine.py:98]
   - Persists state [engine.py:99]
   - Creates `EndTurnConfirm(message, approve_label, deny_label)` [engine.py:101–107]
   - Returns `AdvanceResult(new_phase=current, end_turn_signal=confirm)` [engine.py:108–109]

**Gate trigger**: explicit `gate:` dict in phase YAML under `next-phases[i]` → loader builds GateDef [loader.py:53–66, 84–85]

**Approval/denial routing**:
- Tool layer receives EndTurnConfirm, wires callbacks [workflow_tools.py:447–448]:
  - `confirm.on_approve = _on_approve` → calls `engine.finalize_gate_response(ctx, s, approved=True)` [workflow_tools.py:435–439]
  - `confirm.on_deny = _on_deny` → calls `engine.finalize_gate_response(ctx, s, approved=False)` [workflow_tools.py:441–445]
- Returns `ToolResult(text="Submitted for review.", end_turn=confirm)` [workflow_tools.py:449–450]
- User clicks button → callback fires → `finalize_gate_response(ctx, state, approved: bool)` [engine.py:112–156]:
  - Reloads fresh state inside lock (TOCTOU guard) [engine.py:124–131]
  - Routes to `target = pending["edge_target"]` if approved, else `pending["on_deny"]` [engine.py:137]
  - Calls `_apply_transition(..., gate_response="approved"|"denied")` [engine.py:153–156]
  - Persists new status (RUNNING or terminal) [engine.py:183]

---

## 5. Workflow Demo Skill + Test Footprint

**`src/decafclaw/skills/workflow_demo/` layout**:
```
workflow_demo/
├── SKILL.md                 # Workflow declaration: name=research_brief, initial-phase=gather, required-skills=[tabstack]
├── phases/
│   ├── gather.md            # kind: subagent, tools: [tabstack_research, ...], outputs: [sources.md]
│   ├── draft.md             # kind: inline, tools: [vault_read, ...], next-phases: [review, gather]
│   ├── review.md            # kind: inline, tools: [vault_read, ...], gated next-phases: [publish (gate), draft (on-deny)]
│   └── publish.md           # kind: inline (terminal), tools: [vault_write, ...], no next-phases
```

**Test files** (7 total, ~2000 lines):

1. **test_workflow_loader.py** (386 lines)  
   — Loader parsing: frontmatter split, YAML parsing, phase ID validation, kind enum, tools list, outputs tuple, next-phases edges with gate support, context-profile dict, required-skills validation, edge-target resolution, multi-edge `when:` requirement, subagent constraints (outputs required, single edge, no gates)

2. **test_workflow_engine.py** (440 lines)  
   — Engine state transitions: advance() non-gated path, _apply_transition() history tracking, _enter_gate() gate creation, finalize_gate_response() approve/deny routing, verify_subagent_outputs() artifact checking, dispatch_subagent_if_needed() chain dispatch with cap, status progression (RUNNING→DONE/ERROR/PAUSED_GATE/PAUSED_SUBAGENT)

3. **test_workflow_tools.py** (617 lines)  
   — Tool layer integration: workflow_start() skill activation + initial dispatch, tool_phase_advance() with gate signal handling + subagent dispatch, tool_workflow_status() output format, tool_workflow_artifact_read/write() path validation, build_phase_advance_definition() enum generation, refresh_workflow_tools() phase restriction application

4. **test_workflow_conv_state.py** (157 lines)  
   — State persistence: init_workflow_state() directory creation + history init, save_workflow_state() atomic writes + timestamp updates, load_workflow_state() JSON deserialization, archive_workflow_state() rotation to timestamped archives, conv_lock() per-conversation serialization

5. **test_workflow_skill_loader.py** (93 lines)  
   — Skill loader integration: workflow discovery in skill directories, LoaderError propagation, workflow registration in registry.py

6. **test_workflow_context.py** (159 lines)  
   — ContextComposer integration: consult_workflow_overlay() inline phase prompt injection, context_profile_overrides application, phase boundary signal, subagent phase defensive rendering, run_id metadata

7. **test_workflow_types.py** (141 lines)  
   — Data model round-trips: WorkflowState.to_json()/from_json() serialization, RunStatus enum, EdgeDef/GateDef/PhaseDef frozen dataclass construction, is_terminal property

