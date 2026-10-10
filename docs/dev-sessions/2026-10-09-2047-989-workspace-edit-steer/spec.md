# 989 — Steer agent to workspace_edit for multi-line edits

_Source: GitHub issue [lmorchard/decafclaw#989](https://github.com/lmorchard/decafclaw/issues/989) (triage:ready). Issue body already carries Les's confirmed decisions, so brainstorm is complete._

## Problem & Bounded Slice

Agents do not use `workspace_edit` for multi-line edits in workspace files. They write ad-hoc `python3 - << EOF` scripts in `shell` instead. Those scripts bypass the workspace tools, start shell confirmation prompts, and fail on quoting errors.

Evidence from the conversation that produced this issue (see the comments of 2026-10-08):

- The agent never called `workspace_edit`. It made 4 heredoc Python edits. One edit failed because the target text was invented from memory.
- The files were inside the workspace, in a repo checkout at `workspace/decafclaw/`. The agent treated the checkout as "shell-only" because it ran `git -C decafclaw ...`.
- Three things steered the agent away from the tool. The `workspace_edit` description says "USE SPARINGLY". A vault Agent page note says "I have repeatedly misused `workspace_edit` for multi-line content". And `workspace_edit` is `priority: normal`, so it is deferred.

`workspace_edit` already supplies the requested `workspace_replace_block` behavior. It accepts multi-line `old_text`, fails on 0 matches, fails on more than 1 match unless `replace_all=true`, and returns a diff (`src/decafclaw/tools/workspace_tools.py`, `tool_workspace_edit`).

**Intended result:** for a multi-line edit to a workspace file, including a file in a repo checkout under `workspace/`, the agent copies the current text from a fresh `workspace_read` and calls `workspace_edit`. It does not use `shell` to edit file text.

**Confirmed decisions (Les, 2026-10-08):** do not add a new tool. Retarget `workspace_edit`. The vault Agent page note revision is in scope.

### Decision on item 2: make `workspace_edit` critical (in scope)

Token-budget check at `a1db3e4` with default config (`compaction.max_tokens` 100000, `agent.tool_context_budget_pct` 0.10, `agent.max_active_tools` 30), using `estimate_tool_tokens`:

| Set | Tools | Estimated tokens |
|---|---|---|
| Core `critical` tools | 14 | 2793 |
| Always-loaded skill tools (`vault`, `background`, `mcp`) | 24 | 4898 |
| Critical floor total | 38 | 7691 of 10000 |
| `workspace_edit` today | 1 | 386 |

The critical floor (38 tools) already exceeds `max_active_tools` (30). So `classify_tools` admits no `normal` tool, and every `normal` tool is deferred on every turn. Today `workspace_edit` is callable only after `tool_search` or a pre-emptive keyword match on the user message or the last assistant response (`docs/preemptive-tool-search.md`). An edit that the agent starts in the middle of a task often gets no match.

Promotion adds about 386 tokens (less if the new description is shorter) and moves the floor to 39 tools, about 8077 tokens. That is within the 10000-token budget. We recommend the promotion. It is the only way to make the tool visible by default without a change to the budget rules. Les can reject this part in review without loss of the other changes.

This check used default config, not the deployed `config.json`. A deployed `CRITICAL_TOOLS` or `MAX_ACTIVE_TOOLS` override can change the numbers.

### Decision on item 6: split out

CRLF normalization and a near-match hint did not apply to the observed failure. The failed match used invented text. These changes are split to #997.

## Concrete Changes & File Targets

1. `src/decafclaw/tools/workspace_tools.py`, the `workspace_edit` entry in `WORKSPACE_TOOL_DEFINITIONS`:
   - Rewrite the description. Make `workspace_edit` the default tool for surgical edits, including multi-line blocks, when `old_text` is copied from a fresh `workspace_read` or from an earlier edit diff.
   - Remove "USE SPARINGLY" and the advice to prefer `workspace_replace_lines` for multi-line edits.
   - Keep the warning against text reconstructed from memory. Keep the unique-match and `replace_all` rules.
   - Update the `old_text` parameter description to match.
   - Change `"priority": "normal"` to `"priority": "critical"`.
   - Keep the definition at or below its current estimate of 386 tokens.
2. `src/decafclaw/prompts/AGENT.md`, section "Workspace — Your Filesystem" (the selection guide, currently lines 252-272):
   - Revise the guide to match the new description. Recommend `workspace_edit` for text that is in view, and `workspace_replace_lines` or `workspace_insert` for edits by line number.
   - Add a rule: files under the workspace, including git checkouts such as `workspace/decafclaw/`, are read and edited with `workspace_*` tools. Use `shell` for git, build, and test commands, not to edit file text with `sed`, heredocs, or Python.
3. The vault Agent page note "workspace_edit Misuse" (agent vault, outside this repository):
   - Rewrite the note so it keeps the lesson (copy text from a fresh read, do not reconstruct it) and stops steering away from the tool.
   - The note is live agent data, not a repository file. Put the old and new text in the PR description. Apply the edit to the live vault after Les confirms it.
4. Tests:
   - Add a test (for example in `tests/test_tool_registry.py`) that runs `classify_tools` on the real core and always-loaded tool definitions with default config, and makes sure that `workspace_edit` is in the active set.
5. Evals:
   - `evals/tool_choice/core_overlaps.yaml`: add 2 cases with `expected: workspace_edit` and `near_miss: [shell, workspace_replace_lines]`. Case A edits a multi-line block in a workspace `.py` file. Case B edits a file in a repo checkout path such as `decafclaw/src/decafclaw/foo.py`. Put the current file text in the scenario so the first tool call is the edit, not a read.
   - `evals/workspace-tools.yaml`: add a full-loop case. Seed a multi-line `.py` file and ask for a multi-line change. Assert `expect_tool: workspace_edit` and `expect_no_tool: shell`. Set `max_tool_calls`, `max_tool_errors: 0`, and `config_overrides: {reflection.enabled: false}`. Check the result with `expect_workspace`. This case runs with real deferral, so it also tests the priority change.
6. Docs: update `docs/tools.md` (the `workspace_edit` row) and `docs/tool-priority.md` (the list of critical tools).

## Explicit Exclusions & Verification Criteria

**Out of scope:**

- A new `workspace_replace_block` tool.
- CRLF normalization and near-match hints (#997).
- Changes to `classify_tools`, `max_active_tools`, or the budget. The critical floor already exceeds `max_active_tools`. That is a separate concern.
- Migration of the AGENT.md guide into tool-owned metadata. #928 does that after this issue lands.

**Sequence with #928:** do this issue first. #928 then moves the revised AGENT.md workspace guide into per-tool `prompt_guidelines`, and its eval pins the routing that this issue sets.

**Verification:**

- `make check` and `make test` pass.
- The new registry test fails on `main` and passes after the change.
- Before the change, run the new `tool_choice` cases and the new `workspace-tools.yaml` case once, and record the result in the PR as a baseline.
- After the change, each new eval case must pass 5 consecutive runs on the default model. Record the runs in the PR.
- `make eval-tools` shows no new failure in the existing cases.
- Les examines the revised vault note text before it is applied.

## What we're NOT doing

- No new `workspace_replace_block` tool.
- No CRLF normalization / near-match hints (→ #997).
- No changes to `classify_tools`, `max_active_tools`, or the token budget.
- No migration of the AGENT.md guide into tool-owned `prompt_guidelines` (→ #928, after this lands).
- No change to the vault note without Les's sign-off on the new text.

## Spec confirmation

The issue body carries confirmed decisions and a concrete file-target list. No open design questions. Proceed to plan → execute → PR.
