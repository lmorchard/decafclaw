# Session Notes

## 2026-04-16

- Session started for GitHub issue [#257](https://github.com/lmorchard/decafclaw/issues/257) — Situational tool selection: dynamic tool visibility based on conversation context.
- Builds on PR #263 (priority system + skill extraction) and PR #265 (tool discovery polish — did-you-mean errors, tightened prompts).
- Starting direction from Les: focus on pre-emptive tool search using the current user message text.

## Step 1 — Scaffolding (done)

- Added `PreemptiveSearchConfig` dataclass with `enabled: bool = True`, `max_matches: int = 10`. Nested under `AgentConfig`.
- Added generic nested-dataclass handling to `load_sub_config` — if a field's type is a dataclass and the JSON value is a dict, recurse. Makes future nested configs trivial.
- Added `preempt_matches: set[str]` field to `ToolState` in `context.py`. `dataclasses.replace` in `fork_for_tool_call` preserves it automatically.
- 2 new tests in `test_config.py`: default values, JSON file loading for nested dataclass.
- 1502 tests passing.

## Step 2 — Matching library (done)

- New module `src/decafclaw/preempt_search.py` with:
  - `STOPWORDS` — hardcoded ~70-word English list, with a `TODO(followup)` comment about evaluating a maintained library.
  - `tokenize(text)` — lowercase, non-alphanumeric split, drop `<3` chars and stopwords.
  - `match_tools(input_tokens, candidates, max_matches)` — score = `|input ∩ tool_tokens|`, sort by `(-score, name)`, cap at `max_matches`.
  - `extract_last_assistant_text(history)` — reverse-scan for most recent `role=assistant` with non-empty, non-`[cancelled]` string content.
- 29 new tests in `tests/test_preempt_search.py`. One test adjusted: "get" is in the stopword list (common verb, no discriminating power when many tools have `get_*` naming).
- 1531 tests passing. No integration yet — step 3 wires this into the composer.

## Step 3 — Composer integration + mid-turn wiring (done)

- Extended `classify_tools` with keyword-only `preempt_matches` kwarg. Merged into `force_critical` alongside fetched/skill names.
- `_build_tool_list` in `agent.py` now passes `ctx.tools.preempt_matches` — so mid-turn reclassification stays consistent across agent iterations.
- `_compose_tools` in the composer passes the same through.
- New `_compose_preempt_matches()` method on ContextComposer:
  - Short-circuits if disabled
  - Tokenizes user message + prior assistant response
  - Builds candidate pool: all tools minus already-critical (declared critical, env override, fetched, always-loaded skill tools, activated skill tools)
  - Calls `match_tools`, stores names on `ctx.tools.preempt_matches`
  - Returns a `SourceEntry` with per-match metadata (score, matched_tokens) for the diagnostics sidecar
  - Logs at INFO with conv ID and promoted tool names
- Called from `compose()` between wiki references and tool classification.
- 10 new tests in `test_context_composer.py` (`TestComposePreemptMatches`) + 1 integration test in existing `TestComposeTools`. 1 new test in `test_tool_registry.py` for the classifier kwarg.
- 1541 tests passing.
- Skipped manual smoke during step 3 — will do end-to-end manual verification before PR.

## Step 4 — Web UI diagnostics surfacing (done)

- Added `preempt_matches` to `SOURCE_COLORS` and `SOURCE_LABELS` in `context-inspector.js`.
- Added case in `#sourceDetail()` that renders matched tool names with scores (e.g. `vault_backlinks(2), vault_search(1)`) and a tooltip showing the input tokens that triggered matches.
- Existing waffle-chart + source-table render the new source automatically via the generic loop.
- Type-check clean.

## Step 5 — Documentation pass (done)

- New `docs/preemptive-tool-search.md` — comprehensive end-to-end doc: how it works, why user + previous assistant, ephemeral vs persistent, config, mode applicability, debugging (logs, sidecar, web UI popover), interactions with other mechanisms, design rationale, #257 follow-up list, related links.
- Cross-references added in `docs/tool-search.md`, `docs/tool-priority.md`, `docs/context-map.md` (tool-budget discussion).
- `docs/config.md` — new rows for `preemptive_search.enabled` and `preemptive_search.max_matches` under the `agent` group.
- `docs/index.md` — indexed under the "Tool system" area.
- `CLAUDE.md` — added `src/decafclaw/preempt_search.py` to Key files, added a conventions bullet summarizing the mechanism at a glance.
- 1541 tests still passing.
