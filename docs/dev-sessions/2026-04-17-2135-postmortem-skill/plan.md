# Plan: Postmortem skill

Spec: [spec.md](./spec.md). Decisions locked — `postmortem` / `inline` / always-write / one eval case.

Branch: `session/postmortem-skill` (already checked out).

## Pre-flight (verified 2026-04-17)

- Skill discovery: dropping `src/decafclaw/skills/postmortem/SKILL.md` is sufficient — bundled skills auto-discover via `src/decafclaw/skills/__init__.py:178` (`discover_skills()`). No registry update.
- `context: inline` semantics: the skill body is substituted and injected as a user message in the **current turn**, with full conversation history visible. Pre-approved tools and activated skills land on `ctx` before injection. (`src/decafclaw/commands.py:358+`.) **Implication for Phase 1**: write the SKILL.md body in second-person imperative voice ("Analyze what just went wrong…"). It will be read as a user-voiced instruction to the agent, not a description of a skill.
- `$ARGUMENTS` substitution: literal token `$ARGUMENTS`. If body has no placeholder but user supplied args, they're appended as `ARGUMENTS: ...`. (`commands.py:44`.)
- `vault_write` auto-creates parent folders via `path.parent.mkdir(parents=True, exist_ok=True)` (`src/decafclaw/skills/vault/tools.py:164`). No explicit folder creation step needed.
- Eval harness is **not** wired into `make test`. Run evals with `uv run python -m decafclaw.eval evals/postmortem.yaml`. (`src/decafclaw/eval/__main__.py`, Makefile.)
- **Les likely has `make dev` running.** I won't start my own bot instance. For Phase 2 smoke testing, Les exercises the skill in his running web UI and shares the output; I iterate on the SKILL.md.
- **Name collision check**: no existing `postmortem` references in `src/` Python code.

## Phase 1: Skill scaffold

1. Create `src/decafclaw/skills/postmortem/SKILL.md`.
2. Frontmatter:
   ```yaml
   name: postmortem
   description: Structured blameless analysis of what went wrong in this conversation — identifies root causes and proposes minimal fixes
   user-invocable: true
   context: inline
   allowed-tools: vault_write, current_time
   ```
3. Skill body (second-person imperative, because inline injection makes this a user-voiced instruction):
   - Opening: short context framing — this is a blameless analysis, not a task to resume.
   - Section headings the agent must produce: `## Anomaly`, `## Root cause hypotheses`, `## Proposed patches`, `## Systemic vs session-specific`, `## Next steps`.
   - Root-cause categories to consider: ambiguous instruction, missing guardrail, tool-description gap, skill-body issue, LLM quirk, missing capability, user-facing ambiguity.
   - Blameless framing rule: third-person / system-level phrasing. Forbid "I apologize", "I should have", "my mistake", "I'm sorry".
   - Proposed patches must name specific artifacts (file paths, tool names, AGENT.md lines). No "rewrite everything".
   - `$ARGUMENTS` block: if present, narrow the analysis to that focus; otherwise analyze whatever is most salient in the conversation.
   - Persistence: call `vault_write` to save the report to `agent/pages/postmortems/{YYYY-MM-DD-HHMM}-{slug}.md` with YAML frontmatter (`tags: [postmortem]`, `importance: 0.6`) and a `## Sources` section listing the conversation ID. Use `current_time` for the timestamp.
   - Turn boundary: after writing and delivering the report, stop. Do not resume the original task unless the user asks.
4. No `tools.py`, no `SkillConfig` — markdown-only.

**Verify**: `make lint` (Python lint — should be unchanged since this is markdown-only, but run it to confirm nothing breaks). Ask Les to confirm the skill appears in his running dev instance (may need a restart to pick up a new skill directory).

**Commit**: `feat: bundle postmortem skill (scaffold)`.

## Phase 2: Manual smoke test + prompt tuning

1. Ask Les to exercise `/postmortem` in his web UI against a conversation with a simulated failure pattern (repeated tool errors, an ambiguous instruction, etc.).
2. Check the output against all five acceptance criteria from the spec:
   - All five sections present and populated.
   - Report written to `agent/pages/postmortems/…md`.
   - No apology/self-flagellation phrasing.
   - Proposed patches reference specific artifacts.
   - Agent stops after delivering the report.
3. Iterate on SKILL.md until output is consistently structured and blameless. **Expect 2–4 rewrites** — skill bodies are control surfaces.
4. Exercise the `$ARGUMENTS` path at least once (`/postmortem focused on the tool thrashing`) to confirm it narrows focus.

**Verify**: Les signs off on an output sample before Phase 3.

**Commit**: `polish: postmortem skill body tuning` (fold into Phase 1 if only one pass needed).

## Phase 3: Eval case

1. Create `evals/postmortem.yaml`.
2. One case, simplest reliable shape — single turn where the user's message describes a failure pattern in prose and invokes the skill:
   ```yaml
   - name: "postmortem produces structured blameless report"
     setup:
       skills: [postmortem]
     input: |
       I asked you three times to search the vault for "quarterly plans"
       and each attempt failed with an argument-type error on the query
       parameter. /postmortem
     expect:
       response_contains:
         - "re:##\\s*Anomaly"
         - "re:##\\s*Root cause"
         - "re:##\\s*Proposed patches"
         - "re:##\\s*Systemic"
         - "re:##\\s*Next steps"
       response_not_contains:
         - "I apologize"
         - "I should have"
         - "my mistake"
         - "I'm sorry"
       max_tool_calls: 6
       max_tool_errors: 0
   ```
   (Prose-described failure is more reliable than trying to actually produce tool errors mid-eval. It tests the skill's output structure and framing — the real validation of pattern-analysis behavior on live failures happens in Phase 2's manual tests.)
3. Run: `uv run python -m decafclaw.eval evals/postmortem.yaml`.
4. Tune SKILL.md only if failures reflect real ambiguity; adjust the seeded prose if the setup itself is the flaky variable.

**Verify**: eval passes.

**Commit**: `test: eval case for postmortem skill`.

## Phase 4: Docs

1. Create `docs/postmortem-skill.md` describing: purpose, when to invoke, trigger syntax, report structure, vault page location, relationship to dream/garden, non-goals (no auto-trigger, no cross-session v1).
2. Add an entry to `docs/index.md`.
3. Update `CLAUDE.md` "Skills" key-files list to mention `src/decafclaw/skills/postmortem/`.
4. Check `README.md` — only update if it already enumerates bundled skills.

**Verify**: `make lint` still clean. Scan new docs for stale claims (paths, behavior descriptions).

**Commit**: `docs: postmortem skill`.

## Phase 5: PR + retro

1. Fill in `notes.md` — what needed rewording in Phase 2, any surprises, final decisions.
2. Push branch; open PR targeting `main`, body includes `Closes #279`.
3. After merge: live-test `/postmortem` one more time against a real conversation; update docs if behavior diverged from written description.

## Out of scope

- Auto-trigger on repeated errors (v2 — needs error-pattern detection).
- Cross-session pattern analysis via `conversation_search` (v2).
- Auto-applying proposed patches (never — user reviews).

## Risks to watch

- **Skill body drift**: prompt wording is the whole feature. Phase 3 eval is the guardrail; do not skip it.
- **Dev instance reload**: a newly added skill directory may not register without a bot restart. Confirm during Phase 1 handoff to Les; if needed, ask him to restart `make dev`.
- **Importance weight**: `importance: 0.6` in frontmatter is a guess — tune if postmortem pages drown out higher-value pages in retrieval.
- **Prose-described eval vs real failures**: the Phase 3 eval tests structure and framing, not live pattern analysis on real conversation history. If Phase 2 smoke-testing surfaces that the skill does well on prose-described failures but poorly on actual conversation analysis, we need a richer eval setup (seeded history) — defer to v2 unless Phase 2 signals it's urgent.
