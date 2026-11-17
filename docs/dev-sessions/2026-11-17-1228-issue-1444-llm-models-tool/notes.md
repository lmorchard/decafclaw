# issue-1444-llm-models-tool

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1444
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1444-llm-models-tool
- Branch: issue-1444-llm-models-tool
- Base: origin/main at 5e02403397fac4b688d7077f5b8a08c68ff2f34c
- Next action: implement, then publish draft PR.

## Log

- Implemented `tool_llm_models` in `src/decafclaw/tools/llm_tools.py` (read-only, `execution: shared`,
  `writes: False`); registered in `LLM_TOOLS` and `LLM_TOOL_DEFINITIONS`. Optional `provider` and `query`
  filters; structured `ToolResult.data` `{default_model, active_model, total, models[]}`.
- Default = `effective_default_model` (#1211 catalog pointer → `config.default_model`); a stale
  `ctx.active_model` is not reported active (mirrors `llm_query`'s fallback). `total` counts returned models.
- `llm_query` description now points at `llm_models` for model-name discovery.
- Tests: six cases in `tests/test_llm_tools.py::TestModelsDiscovery` (list/default/active, provider filter,
  query filter on name + model id, empty match, stale active, no configs).
- Docs: `docs/tools.md` section renamed "LLM Models & Query" with `llm_models` row and behavior bullets.
- Eval: `models-vs-llm-query-discovery` in `evals/tool_choice/meta_and_negative.yaml`; passes against
  vertex-gemini-flash (nearest pairs 0/1 swapped).
- Checks: `make fmt`, `make lint`, `make typecheck` clean; `make test` 5646 passed, 2 skipped (one unrelated
  timing failure in `tests/test_background_tools.py` on the first run; passed alone and on rerun).
