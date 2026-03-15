# Heartbeat Prompt — Session Notes & Retro

## What we built

Periodic heartbeat system that reads HEARTBEAT.md and performs tasks:

- **Section splitting** on `##` headers — each section gets its own agent turn
- **Concurrent execution** — sections run in parallel via `asyncio.gather`
- **Dual file merge** — admin HEARTBEAT.md + workspace HEARTBEAT.md with trust boundary
- **Interval parsing** — `Nm`, `Nh`, `NhNm` formats, disable with empty/0
- **Overlap protection** — skip tick if previous cycle still running, with warning log
- **HEARTBEAT_OK suppression** — OK sections show `✅ Title — OK`, non-OK show full response
- **Mattermost reporting** — marker + pre-posted placeholders per section, edited as they complete
- **Interactive reporting** — stdout with section headers
- **Manual trigger** — `heartbeat_trigger` tool, fire-and-forget, posts to channel
- **Admin auto-approve** — admin HEARTBEAT.md sections auto-approve skill activation and shell
- **Optional reporting** — heartbeat runs even without a channel configured
- **`current_time` tool** — avoids shell `date` commands
- **Graceful shutdown** — timer cancelled, in-flight cycle completed

## What went well

- **Live testing caught real issues fast.** Every deploy-test cycle found something: None response, shell confirmation deadlock, missing channel membership, Mattermost delete ghosts. Way more productive than trying to predict these in the spec.
- **The section-splitting design worked.** Sections running as independent turns with concurrent execution is a clear win over OpenClaw's single-blob approach.
- **Trust boundary for admin vs workspace.** The distinction between admin-authored (auto-approve) and agent-writable (require confirmation) HEARTBEAT.md is clean and follows the existing data layout pattern.

## What could be better

- **Duplicated section execution logic.** `run_heartbeat_cycle` in heartbeat.py and `_run_heartbeat_to_channel` in heartbeat_tools.py both create contexts and run agent turns. The interactive mode uses the former, Mattermost uses the latter. Should be refactored to share a common runner.
- **Lots of UX iteration commits.** The Mattermost reporting went through several cycles (delete → edit → placeholder → no-delete → etc). Could have thought through the UX more in the spec before coding.
- **Shell/skill confirmation in heartbeat is a footgun.** Workspace HEARTBEAT.md sections can't get confirmation (no one to confirm), so confirmation-requiring tools just timeout. Works but not a great experience — should document clearly.
- **Timed heartbeat not live-tested.** Only tested via `heartbeat_trigger` tool. The timer path should work the same but hasn't been verified with a real `HEARTBEAT_INTERVAL`.

## Design decisions worth noting

- **`user_id = "heartbeat-admin"` vs `"heartbeat-workspace"`** — source-tagged user IDs let tools check trust level without a new context field
- **Fire-and-forget trigger** — `heartbeat_trigger` returns immediately, runs cycle in background. Doesn't block the calling conversation.
- **No deletions in Mattermost** — learned that deleting thread posts and root posts both leave "(message deleted)" ghosts. Everything is edit-based now.
- **Reporting is optional** — heartbeat runs for side effects even without a channel. Three independent config layers: interval (run?), HEARTBEAT.md (what?), channel (report where?)

## Bugs found during live testing

- **`run_agent_turn` returns None** when LLM uses tools but gives no final text. Fixed with `response or "(no response)"` and None guard in `is_heartbeat_ok`.
- **Shell confirmation deadlock** — heartbeat turns have no confirmation handler, so shell commands hang for 60s. Fixed by auto-approving for admin sections, steering prompt toward workspace tools.
- **Skill activation confirmation timeout** — same issue as shell. Fixed with admin auto-approve.
- **403 on post edit/delete** — bot wasn't a member of the heartbeat channel. Mattermost allows posting via token but not editing without membership.
- **"(message deleted)" ghosts** — Mattermost shows these for deleted thread posts. Switched to edit-only approach.

## Also built this session

- **`current_time` tool** — simple date/time tool to avoid shell `date` commands
- **Various prompt steering** — heartbeat prompt tells agent to prefer workspace tools over shell

## 156 tests, 20+ commits on heartbeat-prompt branch
