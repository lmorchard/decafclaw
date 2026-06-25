# Workflow skill activation Spec

**Goal:** Make skill-bundled tools reachable from `wf.tool_call` inside workflows. Workflow turns auto-activate the always-loaded skill set (matching USER-turn behavior), and `@workflow(...)` gains a `requires_skills=[...]` declaration for workflows that need additional skills like `tabstack_research`. Unblocks `/research`'s use of `tabstack_research` and any future workflow that wants vault writes or other skill tools.

**Source:** [Issue #580](https://github.com/lmorchard/decafclaw/issues/580). Filed as a follow-up from #574's PR #579 live smoke (Finding 1).

## Current state

Workflows (`TurnKind.WORKFLOW`) bypass the agent loop's tool registry setup entirely. Specifically (see `research.md` §3):

- `ConversationManager._start_turn` routes WORKFLOW turns through `run_workflow_turn` (`src/decafclaw/workflow/resume.py:30-79`), NOT `_setup_turn_state` (which is the always-loaded-activation site at `src/decafclaw/agent.py:395-411`).
- The workflow engine (`src/decafclaw/workflow/engine.py`) calls `wf_llm.call_structured` directly with the orchestrator's `system=`/`prompt=` args — it never goes through `ContextComposer` or `build_tool_list`.
- Result: `ctx.tools.extra` is empty at workflow-turn start. `wf.tool_call("tabstack_research", ...)` calls `execute_tool(ctx, "tabstack_research", ...)` → `[error: unknown tool ...]`.
- This is broader than the #580 title suggests: ALL skill tools (including the always-loaded vault/background/mcp) are unreachable from workflows, not just tabstack.

`@workflow(...)` and `WorkflowSpec` (`src/decafclaw/workflow/registry.py:10-15`) carry only `name`, `fn`, and `model` — no skill/tool declaration surface today.

## Desired end state

**Workflow turns auto-activate the always-loaded skill set**, the same way USER turns do via `_setup_turn_state`. This makes vault / background / mcp tools reachable from any workflow without per-workflow opt-in.

**`@workflow(...)` accepts a `requires_skills: list[str] = []` argument.** Workflows declare additional non-always-loaded skills they need. `WorkflowSpec` carries this as `requires_skills: tuple[str, ...] = ()`. At workflow-turn start, `run_workflow_turn` activates the union of always-loaded skills and `spec.requires_skills` before invoking `run_workflow`.

**Activation reuses the existing `tool_activate_skill` flow.** No new code path — the same machinery that populates `ctx.tools.extra` / `ctx.tools.extra_definitions` / `ctx.skills.activated` for the agent loop populates them for the workflow turn.

**Activation failure raises fail-loud.** A missing or init-failing skill named in `requires_skills` (or in the always-loaded set, which is admin-configured and should always be reachable) raises before `run_workflow` is invoked. The workflow turn returns an error `ToolResult`; the engine never sees the orchestrator. Symmetric with how the agent loop today surfaces activation errors during `_setup_turn_state`.

**`/research` updated to declare `requires_skills=["tabstack"]`.** This is the load-bearing acceptance smoke — the live walk that was blocked in #579's smoke (Finding 1) should now reach a real `tabstack_research` invocation.

## Design decisions

- **Decision:** Hybrid — always-loaded auto-activation for workflow turns + per-workflow `requires_skills=[...]`.
  - **Why:** Composes the two existing skill-availability models. Always-loaded is the "universally available" baseline (vault, background, mcp); `requires_skills` is the per-workflow ask (tabstack). Avoids the trap of marking tabstack as always-loaded (which would bloat every agent-loop turn's critical-tool budget) and the trap of forcing every workflow to redeclare vault even though it's universal.
  - **Rejected:** Always-loaded only (doesn't unblock `/research`); per-workflow only (forces redundant `requires_skills=["vault", "background", "mcp"]` everywhere); pre-activate the entire catalog (broad, no per-workflow control).

- **Decision:** Activation failure is fail-loud — raises before `run_workflow` runs the orchestrator.
  - **Why:** Decafclaw's general posture is zero tolerance for silent skips. A typo in `requires_skills` becoming a runtime "unknown tool" error 30s into a turn is worse than a setup-time failure. Matches how the agent loop surfaces activation errors today.
  - **Rejected:** Fail-soft + log warning (silent feature loss); fail-loud-declared-only + fail-soft-always-loaded (the always-loaded set is admin-configured and should always work — if vault's init fails, the whole agent has problems, not just workflows).

- **Decision:** Extract the always-loaded activation logic from `_setup_turn_state` into a shared helper (likely `activate_always_loaded(ctx, *, extra=())` in `src/decafclaw/skills/__init__.py`).
  - **Why:** Both `_setup_turn_state` and `run_workflow_turn` need to do the same thing. The third-call-site rule allows but doesn't require extraction at two sites; the existing code is small enough that extraction adds clarity rather than premature abstraction. Putting the helper in `skills/__init__.py` keeps the activation logic colocated with the skill subsystem.
  - **Rejected:** Duplicate the activation logic in `run_workflow_turn` (would drift); move into `Context.for_task` (wrong layer — Context is data, not behavior; also would affect heartbeat/scheduled/child paths which we're not changing).

- **Decision:** `WorkflowSpec.requires_skills: tuple[str, ...] = ()`. Decorator signature is `@workflow(name, *, model="vertex-gemini-flash", requires_skills=())`.
  - **Why:** Tuple for immutability — it's a spec attribute, not mutable state. Empty tuple default keeps existing `@workflow("name")` calls valid. Keyword-only `requires_skills` prevents positional confusion with `model`.
  - **Rejected:** `list[str]` (mutable spec field is a smell); class-level constant on the orchestrator function (less discoverable than the decorator arg).

- **Decision:** `WorkflowToolNotAllowed` (from #574) remains the wf.tool_call gate. Activation runs orthogonal to the allowlist.
  - **Why:** Two different concerns. `requires_skills` says "make these tools available." `ctx.tools.allowed` (if non-None) says "but only this subset is callable from the orchestrator." A workflow can activate a skill for some of its tools and `ctx.tools.allowed`-narrow which subset the orchestrator may invoke.
  - **Rejected:** Couple them (overloaded semantics, less flexible).

- **Decision:** No changes to `wf.subagent`'s child-agent dispatch.
  - **Why:** `delegate._run_child_turn` already inherits the parent's allowed_tools (minus delegations / vault-writes); skills activated on the parent ctx flow through naturally via `ctx.tools.extra` inheritance. If the workflow activated tabstack, the subagent inherits it.
  - **Rejected:** Build a separate child-agent skill-inheritance mechanism (the existing one already works).

## Patterns to follow

- **Activation flow:** Mirror `_setup_turn_state`'s call into `tool_activate_skill` (`src/decafclaw/tools/skill_tools.py:127-178`). The mutated ctx fields are `ctx.tools.extra`, `ctx.tools.extra_definitions`, `ctx.tools.dynamic_providers`, `ctx.tools.dynamic_provider_names`, `ctx.skills.activated`, and `ctx.config.always_loaded_skill_tools`. Don't reimplement — call through.
- **Always-loaded discovery:** `config.discovered_skills` filtered by `always_loaded == True` is the source of truth (`src/decafclaw/skills/__init__.py:258-349`). The shared helper iterates this set.
- **Workflow turn entry:** Activation runs in `run_workflow_turn` (`src/decafclaw/workflow/resume.py:30-79`), AFTER the journal is loaded and BEFORE `await run_workflow(...)` is called. On activation failure, the turn returns a `ToolResult(text="[error: skill activation failed: ...]")` and the journal status is set to "error" via the existing engine-error pattern.
- **Spec test pattern:** Mirror `tests/test_workflow_research.py`'s mock-LLM walkthrough. Add tests at the `run_workflow_turn` level that verify activation happened before the first `wf.tool_call`.
- **Decorator update:** `src/decafclaw/workflow/registry.py:20-26` — extend the `@workflow` signature without breaking existing `@workflow("name")` calls. The new param is keyword-only.

## What we're NOT doing

- **Changing the workflow LLM's system-prompt assembly.** Workflows pass `system=` directly to `wf.llm_call`. We don't inject `<loaded_skills>` or `<skill_catalog>` into workflow LLM prompts. The orchestrator drives tool selection (via `wf.tool_call` from Python), not the LLM.
- **Auto-activating skills for `TurnKind.CHILD_AGENT` (or any other non-WORKFLOW kind).** Heartbeat / scheduled / child-agent paths intentionally skip `_setup_turn_state` today; that's outside #580's scope. (`wf.subagent` dispatches CHILD_AGENT turns; those inherit the parent workflow's activated skills via `ctx.tools.extra`, which is enough.)
- **Activating MCP tools via `requires_skills`.** MCP is already always-loaded; this lands as a free side-effect, not a separate feature.
- **Wire a "list activated skills" introspection tool for workflows.** The orchestrator author knows what they declared in `requires_skills`. No runtime introspection needed in v1.
- **Removing `/research`'s fail-fast guard** (the "all results errored → raise" check added in #574 PR #579). It's defense-in-depth and remains correct.
- **Changing skill activation semantics for any non-workflow code path.** The shared helper is extracted in a way that doesn't change behavior for USER/WAKE turns.
- **Auto-resuming `status=running` workflows on server startup** (separate concern — #581).

## Open questions

- **Q: Should `requires_skills` activation also run on every workflow-turn resume (e.g., when a user_input suspension resolves), or just on the first turn?**
  - **Default:** activate on every workflow turn (idempotent — already-activated skills short-circuit). Symmetric with how USER turns re-run `_setup_turn_state` per turn. Doesn't require a "first turn vs subsequent turn" branch.

- **Q: Should the shared activation helper accept an explicit list (`activate_skills(ctx, names)`) or always operate on the always-loaded set + an optional extra list?**
  - **Default:** `activate_always_loaded(ctx, *, extra=())` — caller passes additional names beyond always-loaded. Single entry point that USER turn calls with empty extras and workflow turn calls with `spec.requires_skills`.

- **Q: What happens if a skill in `requires_skills` matches a tool that the workflow's `ctx.tools.allowed` would reject?**
  - **Default:** the activation populates the registry; the per-call allowlist gate in `wf.tool_call` still applies. So a workflow can activate a skill for some of its tools and `ctx.tools.allowed`-narrow which subset the orchestrator can invoke. No special interaction.
