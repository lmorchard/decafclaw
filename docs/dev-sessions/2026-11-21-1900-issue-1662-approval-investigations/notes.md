# #1662 — approval follow-up investigations

Date: 2026-11-21
Scope: the three questions deferred from #1641 / PR #1642.

Environment limit, stated once and applying to all three: the operator's live
agent data is **not present in this checkout**. There is no `~/.decafclaw`;
the repository's `data/decafclaw/workspace` holds 3,003 archives from an older
dev corpus (no `shell_approval` records, no `approval_decision` audit events).
Anything that needs the 4,227-archive live corpus or a real judge model call was
therefore answered by code inspection and a reproducible method, not by rerunning
the original measurement. The commands are given so the answer can be confirmed
where the data lives.

## 1. The 230 prompts that followed an explicit judge ALLOW

**Finding: an artifact of message-order pairing, not a 230-prompt bug.**

The original measurement walked messages in order and treated a
`confirmation_request` as "preceded by" the most recent `shell_approval`
record. That is the wrong join. In `check_shell_approval()`
(`src/decafclaw/tools/shell_tools.py`), an aux ALLOW ends that command with no
prompt. The *next* command can prompt for reasons that never reach the judge at
all:

- the security monitor returns `ASK` before the direction/judge stages
  (`security_monitor_ask`);
- a new activity prompts before the judge (`activity_new`);
- an unattended turn defers or denies before the judge;
- the read-only tier is disabled and the command falls through;
- a persisted pattern does not match and the aux judge is disabled.

When that next command prompts, no new `shell_approval` record is written, so
the previous command's `ALLOWED` record is the nearest one. The prefix
"prompt after ALLOW" is then guaranteed for a whole class of unrelated prompts.
The 230 are not evidence that an ALLOW was overridden; they are prompts for
commands that never had a judge verdict.

The archive already carries the right keys for a correct join:

- `confirmation_request.confirmation_id`
- `confirmation_request.tool_call_id`
- `confirmation_request.action_data.command`
- `confirmation_response.confirmation_id` → `approved`
- `shell_approval.content` → the command that was judged

Corrected measurement:

1. Build `confirmation_id → confirmation_request`.
2. For each `confirmation_response`, read `approved` and resolve the request.
3. Join the request's `tool_call_id` to the `assistant` message's
   `tool_calls[].id` to get the command (fall back to
   `action_data.command`).
4. Look for a `shell_approval` record **whose content names that same
   command**. Count a prompt as "after ALLOW" only when that command has an
   `ALLOWED` verdict.

`src/decafclaw/approval_replay.py` (PR #1670/#1671) implements this pairing for
replay (`_human_prompts`); `tests/test_approval_replay.py::
test_denials_pair_by_tool_call_id_not_message_order` pins that a judge record
between request and response does not shift the pairing.

To confirm the corrected count on the live corpus:

```sh
decafclaw approvals replay --all --show-denials --denials-out /tmp/denials.json
```

The replay's `human_approved` field is paired by `tool_call_id`, and its
`historical_judge` field comes from the command-matched `shell_approval`
record, so `decision=prompt` + `historical_judge=ALLOWED` is the corrected
"prompt after ALLOW" set. A `--check-denials` run additionally proves the 94
real denials are still prompted.

The replay's local shakedown (3,003-archive dev corpus) found no judge records
at all, so it could not reproduce the 230 count — consistent with this being a
measurement-join question rather than a live pipeline state.

## 2. Judge model A/B (`shell.aux_approval_model`)

**Not run here; no live judge traffic and no unmocked model calls.**

The checkout has no `approval_decision` records (`audit.jsonl` has 0), and the
rule "never make real unmocked LLM calls" rules out generating them. The
comparison also only becomes meaningful after the Phase 2 prompt change
(risk-category-only declines) has accumulated data, which is the issue's own
sequencing requirement.

Procedure to run where the live data exists:

1. Pick a window with the current judge model and record
   `decafclaw approvals report --days N --json`: `judge_decline_rate`,
   `human_approval_rate`, `human_denied`.
2. Change only `shell.aux_approval_model` (or the sidecar
   `aux_approval_model` pointer) to the candidate; hold
   `SHELL_APPROVAL.md` and `activity_window` fixed.
3. Run the same window length and compare the same three numbers, plus the
   `by_tier` breakdown. A capability change should show up as a lower
   `aux_decline` rate without a higher `human_denied` count.
4. Record the two reports (issue comment or vault page) with the commit and
   model ids.

This is deliberately second-order: direction reduces the number of judge
calls; the judge model changes the residual decline rate. Do not tune one
against the other in the same window.

## 3. Disabled Claude entries on otari-local

**Decision: remove the three hand-authored `model_configs` entries, do not
enable catalog models selectively. Apply on the live agent; this checkout has
no `otari-local` provider or Claude catalog entries to edit.**

Why remove:

- The catalog (`data/{agent_id}/model_catalog.json`) is the discovery source
  of truth and currently marks all 14 otari-local Claude models disabled. That
  is an operator decision; enabling exactly the three hand-authored names
  would advertise a subset for no stated reason and leave the other 11
  quietly unavailable.
- Hand-authored entries are intentionally never pruned by catalog changes
  (`apply_catalog` never claims a colliding hand key;
  `tests/test_model_catalog.py::test_disable_colliding_discovered_key_keeps_hand_config`
  asserts the picker still serves one). So a disabled catalog model plus a
  hand-authored name is a stable, silent mismatch: the name stays in the
  picker and fails at request time.
- If Claude on otari-local should be available, enable the desired catalog
  entries instead; that keeps one source of truth and the enable/disable UI
  reflects reality.

Apply (agent data is admin-tier, not in git):

```sh
# inspect first
jq '.model_configs | with_entries(select(.key | startswith("claude-")))' \
  data/{agent_id}/config.json

# remove the three advertised-but-unservable entries
jq 'del(.model_configs["claude-haiku-4.5"],
        .model_configs["claude-sonnet-4.5"],
        .model_configs["claude-opus-4.6"])' \
  data/{agent_id}/config.json > /tmp/config.json && \
  mv /tmp/config.json data/{agent_id}/config.json
```

Verify: `GET /api/models` (or the web model picker) no longer lists the three
names; the catalog UI still shows the otari-local Claude entries disabled.
`model_catalog.json` is not watched by `config_watcher`, so restart the service
after editing it (the `config.json` edit is hot-reloaded).

If the operator prefers to keep the names, the alternative is to enable those
three entries in `model_catalog.json` and restart; then picker and provider
agree. That trades a smaller diff for a partial enablement of a provider the
operator disabled wholesale.
