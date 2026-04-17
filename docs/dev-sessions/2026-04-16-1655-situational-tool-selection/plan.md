# Implementation Plan

Source spec: `spec.md` in the same directory. Read that for design context.

## Strategy

Sequence the work so each step is independently commitable, tests stay green, and behavior only changes at the integration step. Order:

1. **Scaffolding** — config, context field. No behavior change; just plumbing.
2. **Matching library** — pure tokenization + match functions with full test coverage. No integration.
3. **Composer integration** — extend `classify_tools`, add `_compose_preempt_matches`, wire through to `_compose_tools`, add diagnostics entry. This is where behavior changes.
4. **Web UI diagnostics surfacing** — confirm the context-inspector popover shows the new source entry; update if needed.
5. **Documentation pass** — new doc, cross-references, CLAUDE.md key files list, config table, #257 punch-list comment.

Each step ends with `make check` + `make test` clean and a focused commit. Step 3 requires a manual smoke test in the web UI (the match is behavior-affecting).

---

## Step 1 — Scaffolding

**Goal:** land the config schema and context field without behavior change.

**Files:**
- `src/decafclaw/config_types.py` — new `PreemptiveSearchConfig` dataclass:
  ```python
  @dataclass
  class PreemptiveSearchConfig:
      enabled: bool = True
      max_matches: int = 10
  ```
  Nest under `AgentConfig` as `preemptive_search: PreemptiveSearchConfig = field(default_factory=PreemptiveSearchConfig)`.
- `src/decafclaw/config.py` — ensure `load_sub_config` resolves the nested config. No env-var aliases for v1 (just config.json + defaults).
- `src/decafclaw/context.py` — add `preempt_matches: set[str] = field(default_factory=set)` to `ToolState`. Update `fork_for_tool_call` if it explicitly copies — looking at the current code it uses `dataclasses.replace(self.tools, current_call_id=...)`, which preserves the field, so no change. Verify.
- `tests/test_config.py` — add a test for the new field's default.

**Commit:** `feat: add PreemptiveSearchConfig scaffolding`

**State after:** the config field exists and has a default; nothing reads it yet.

---

## Step 2 — Matching library

**Goal:** a pure, well-tested module that does tokenization and match scoring. No integration into the turn lifecycle.

**Files:**
- `src/decafclaw/preempt_search.py` — new module:
  - Module-level `STOPWORDS` set — ~50 common English words. Add a code comment noting that we should evaluate a maintained library (e.g., `stop-words` on PyPI, or sourcing from scikit-learn / spaCy) as a follow-up.
  - `tokenize(text: str) -> set[str]` — lowercase, split on non-alphanumeric runs, drop tokens shorter than 3 chars, drop stopwords.
  - `match_tools(input_tokens: set[str], candidates: list[dict], max_matches: int) -> list[dict]` — for each candidate, compute `tool_tokens = tokenize(name + description)`, score = `|input_tokens ∩ tool_tokens|`; keep those with score ≥ 1, sort by (-score, name), take top `max_matches`. Return list of `{"name": str, "score": int, "matched_tokens": list[str]}`.
  - `extract_last_assistant_text(history: list[dict]) -> str` — scan `history` in reverse for a `role == "assistant"` entry with non-empty `content` that isn't `"[cancelled]"`. Return content or empty string.
- `tests/test_preempt_search.py` — comprehensive unit tests:
  - Tokenization: lowercasing, non-alphanumeric splitting (hyphens, underscores, punctuation), stopword filter, min-length filter, idempotence.
  - Matching: single-word input, multi-word input, no matches (empty result), tie-breaking (alphabetical), safety cap (top N by score).
  - `extract_last_assistant_text`: empty history (returns ""), multi-turn history (returns most recent assistant), skips tool roles / cancelled markers / non-assistant.

**Commit:** `feat: keyword-overlap matching library for pre-emptive tool search`

**State after:** pure-library module ready for integration. No callers yet.

---

## Step 3 — Composer integration

**Goal:** wire the matching library into the turn lifecycle. This is the behavior change.

### 3a — Extend `classify_tools`

Add a keyword-only `preempt_matches: set[str] | None = None` parameter to `classify_tools` in `src/decafclaw/tools/tool_registry.py`. Merge into `force_critical` alongside `fetched_names` and `skill_tool_names`.

Update `tests/test_tool_registry.py`:
- New test: tools named in `preempt_matches` are promoted to active regardless of priority, same hard-floor semantics as fetched/skill.
- Existing tests unchanged — default `None` means current behavior.

### 3b — Composer method + call site

In `src/decafclaw/context_composer.py`:
- New private method `_compose_preempt_matches(self, ctx, config, user_message: str, history: list, mode: ComposerMode) -> tuple[set[str], SourceEntry | None]`:
  - Short-circuit: return `(set(), None)` if `config.agent.preemptive_search.enabled is False` OR if `mode` is one we skip (none for v1 — all composer-driven modes apply).
  - Assemble input text: `user_message + "\n" + extract_last_assistant_text(history)`.
  - Tokenize.
  - Build candidate list: `_collect_all_tool_defs(ctx)` minus anything already in `force_critical` (declared `priority: critical`, env override, activated skill tools, fetched tools, always-loaded skill tools). Use the same logic that `classify_tools` would use internally. To avoid duplication, compute the `force_critical` set once and pass it here plus to `_compose_tools`.
  - Call `match_tools(input_tokens, candidates, config.agent.preemptive_search.max_matches)`.
  - Store the set of matched names on `ctx.tools.preempt_matches`.
  - Return `(matches_set, SourceEntry(source="preempt_matches", tokens_estimated=..., items_included=len(matches), details={"matches": [...with scores/tokens]}))`.
- In `compose()`, call `_compose_preempt_matches` after `_compose_vault_references` and before `_compose_tools`.
- Update `_compose_tools` to pass `ctx.tools.preempt_matches` into `classify_tools`.
- Append the returned `SourceEntry` to the sources list.

### 3c — Mid-turn classifier call site

`src/decafclaw/agent.py` has a second call to `classify_tools` in `_build_tool_list()` — it runs **every iteration of the agent loop** to refresh tool classification (catches dynamic skill tools, newly-fetched tools, etc.). If we only wire preempt_matches into the composer, the matches drop off after the first iteration because `_build_tool_list` re-classifies without them.

Fix: `_build_tool_list` reads `ctx.tools.preempt_matches` and passes it to `classify_tools`. Since the field was populated once by the composer at turn start and never cleared mid-turn, it stays consistent across iterations. No re-matching needed.

### 3d — Tests

- `tests/test_context_composer.py`:
  - `_compose_preempt_matches` populates `ctx.tools.preempt_matches` with matched names based on user message.
  - Previous-assistant heuristic: with a multi-turn history and a short user message, matching input includes prior assistant tokens.
  - `config.agent.preemptive_search.enabled = False` → empty result.
  - `max_matches` cap is honored.
  - `SourceEntry` with source `"preempt_matches"` is emitted with correct shape.
- `tests/test_tool_registry.py` — new test: `classify_tools(preempt_matches={"foo"})` promotes `foo` to active alongside fetched/skill.
- `tests/test_preempt_search.py` — fresh integration tests: end-to-end call of `ContextComposer.compose()` with a small fake tool set, assert the right tools land in `active_tools`; multi-iteration test asserts matches persist across `_build_tool_list` calls within the same turn.

Refactoring detail: `get_critical_names()` already returns the env override + always-loaded skill tools set. `fetched_names` and `skill_tool_names` live on ctx. Encapsulate the "what would be force-critical" computation in a small helper (new or existing) so both `_compose_preempt_matches` and `classify_tools` can share it without drift.

### 3c — Tests

- `tests/test_context_composer.py`:
  - `_compose_preempt_matches` populates `ctx.tools.preempt_matches` with matched names based on user message.
  - Previous-assistant heuristic: with a multi-turn history and a short user message, matching input includes prior assistant tokens.
  - `config.agent.preemptive_search.enabled = False` → empty result.
  - `max_matches` cap is honored.
  - `SourceEntry` with source `"preempt_matches"` is emitted with correct shape.
- `tests/test_preempt_search.py` — fresh integration tests: end-to-end call of `ContextComposer.compose()` with a small fake tool set, assert the right tools land in `active_tools`.

**Smoke test before commit:** start `make dev`, open a fresh web UI conversation, ask "what are my vault backlinks for decafclaw?" — verify (a) `vault_backlinks` lands in the active set without the agent calling `tool_search` first, (b) the context diagnostics JSON sidecar includes a `preempt_matches` source entry with matching tokens.

**Commit:** `feat: pre-emptive tool search — match user message and prior assistant to promote tools`

**State after:** behavior live. Pre-emptive match runs on every interactive / delegated / heartbeat / scheduled turn.

---

## Step 4 — Web UI diagnostics surfacing

**Goal:** make the match visible in the context-inspector popover.

**Files:**
- Inspect `src/decafclaw/web/static/components/context-inspector.js`. Confirm whether it auto-renders new source entries from the JSON or needs an explicit case.
- If auto-rendered: verify it looks reasonable; maybe tweak the display (e.g., show matched tokens inline per tool).
- If not auto-rendered: add a new block for `source == "preempt_matches"` showing tool names, scores, and matched tokens.
- `src/decafclaw/web/conversations.py` (or wherever the sidecar gets served) — usually nothing to do, the JSON flows through.

Manual verification: click the context bar in the UI, see a "Pre-emptive matches" entry with the turn's matches.

**Commit:** `feat(web): surface pre-emptive matches in context diagnostics popover`

**State after:** end-to-end visibility — agent side, sidecar JSON, UI popover.

---

## Step 5 — Documentation pass

**Goal:** bring docs in line with the new mechanism.

**Files:**
- New: `docs/preemptive-tool-search.md` — explains the mechanism end to end: what it matches, when it runs, what a match does, how it relates to priority/fetched/skill tools, edge cases, how to disable, how to debug via the sidecar. Include a short "design rationale" paragraph summarizing why keyword-only for v1.
- `docs/tool-search.md` — add a cross-reference near the top pointing at the new doc.
- `docs/tool-priority.md` — add a paragraph in the "Related" section linking to it.
- `docs/context-map.md` — mention pre-emptive matching in the tool budget discussion.
- `docs/config.md` — add `preemptive_search.enabled` and `preemptive_search.max_matches` rows under the `agent` group.
- `docs/index.md` — index the new page under "Tool system."
- `CLAUDE.md` — add `src/decafclaw/preempt_search.py` to the Key files list. Add a "Tool discovery" convention bullet summarizing the match mechanism at a glance.

**Commit:** `docs: pre-emptive tool search documentation`

**Post-merge (out of the PR):** add a comment to #257 noting what shipped in this PR and the follow-up punch list from `spec.md`.

---

## Verification gates

At each step:
1. `uv run ruff check src/ tests/`
2. `uv run pyright`
3. `uv run pytest`
4. For step 3: manual smoke test in web UI before commit.
5. Stage specific files, focused commit message, commit.

After step 5, before opening a PR:
- Full `make check` + `make test`.
- Manual end-to-end smoke in the web UI matching the acceptance criteria in the spec (fresh conv → vault query → observe match; follow-up "and for X?" → match via prior-assistant; topic pivot → old match drops, new match appears).

## Risks and rollback

- **Verified (not a risk):** heartbeat and scheduled tasks call `run_agent_turn` (`heartbeat.py:162`, `schedules.py:268`), which uses `ContextComposer`. All four modes (interactive, child_agent, heartbeat, scheduled) converge on the same composer path, so the step 3 integration covers them automatically. Both pass `history=[]`, so the prior-assistant heuristic is a no-op and matching runs on the task prompt only — intended behavior.
- **Risk:** keyword match is too loose and over-promotes on long user messages, blowing budget. **Mitigation:** the `max_matches` cap defaults to 10. Worst case is 10 extra tools in the active set; tight budgets already warn on critical overruns. Tune the cap or raise the threshold if observed in practice.
- **Risk:** keyword match is too strict and under-promotes (synonym misses). **Mitigation:** explicit in the spec as an accepted v1 limitation; punch-list item for TF-IDF/embeddings upgrade.
- **Risk:** tokenization performance at scale. **Mitigation:** tool-side tokenization can be cached; profile first, only optimize if needed.
- **Rollback:** each step is a focused commit. If step 3 causes trouble, revert it — config scaffolding (step 1) and the matching library (step 2) are dead weight but harmless. The feature can also be disabled via `config.agent.preemptive_search.enabled = false`.
