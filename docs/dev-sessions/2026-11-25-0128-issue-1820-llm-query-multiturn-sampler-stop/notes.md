# issue-1820-llm-query-multiturn-sampler-stop

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1820
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1820-llm-query-multiturn-sampler-stop
- Branch: issue-1820-llm-query-multiturn-sampler-stop
- Base: origin/main at c314fe80995ebc6a0217056ea2e9262c2309c31b
- Status: implementation complete; local gates run; commit pending.

## Decisions

- `prompt` becomes optional and `messages` is a validated `[{role, content}]`
  history (`system`/`user`/`assistant` only). `prompt` appends a final user
  turn; `messages` + prompt list is rejected. `system` merges into a leading
  system message, else prepends.
- One tool parameter, `max_tokens`, is the portable name. openai-compat sends
  `max_tokens`; openai-responses maps it to `max_output_tokens`; Vertex maps
  to `maxOutputTokens`. openai-responses has no stop parameter, so a non-empty
  `stop` raises instead of being silently dropped.
- Provider plumbing was in scope: `openai_compat.complete`, `vertex.complete`,
  and `openai_responses.complete` previously swallowed `**kwargs`, so a
  tool-only change would have accepted the parameters and discarded them.
- `ScriptedRequest.options` captures provider kwargs so tool tests can assert
  the sampler values reach the provider.

## Evidence

- `make check`: pass (ruff, pyright, JS, generated types).
- `make test`: 7046 passed, 3 skipped; only the 4 pre-existing
  `tests/test_host_metrics.py` failures on this macOS host.
- `make eval-tools` case `delegate-vs-llm-query-needs-tools`: 3/3 pass.
- New positive tool-choice case attempted for sampler/multi-turn routing
  (four scenario wordings). The configured eval model (`vertex-gemini-flash`)
  picked `<no_tool>` or `llm_models`; no reliable case was found, so no eval
  case was added. Existing routing case still passes.


## Log
