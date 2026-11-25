---
name: background
description: Start, monitor, and stop long-running background processes (servers, watchers, builds). Returns immediately with a job ID you can poll for output.
auto-approve: true
always-loaded: true
---

# Background processes

Use this skill when you need to run something without blocking the conversation — a dev server, a file watcher, a long build, or any command running longer than a few seconds.

## Name the condition before you wait

The failure mode here is not a missing tool, it is **waiting without having decided what you are waiting for.** Before any pause, name the condition:

- **"until it exits"** → `schedule_turn`, or a `status` call if it should already be done
- **"until I see line X"** → poll `status`, checking for X
- **"until the file changes"** → `watch_file`
- **"for a while, then look"** → this is the reflex. If you cannot name the condition, check `status` once instead of blocking.

If you cannot say what you are waiting for in one sentence, you are not waiting, you are stalling.

## Route by expected duration

| Duration | Shape | Why |
|---|---|---|
| **< 10s** | `start` then `status` immediately | It is probably already done. A sleep is pure latency. |
| **10s – a few min** | `start`, `status`, `wait(n)`, `status` | Block once with a realistic ceiling, not a guess-a-sleep chain. |
| **crosses a turn boundary** | `schedule_turn(delay_seconds=..., reason=...)` | `wait` cannot outlive the turn. Do not pretend it can. |
| **unbounded** | `start(max_lifetime_sec=0)` + `schedule_turn` | Exempt from expiry, then wake to check. |

Two habits to avoid: issuing `wait` as a "give it a moment" gesture, and re-waiting after a job has already completed.

## Make output observable

- Do **not** pipe a long job through `tail`, `head`, or `grep -m1`. The pipe buffers until EOF, so a killed, expired, or timed-out run loses every buffered line. Pipe only when you also accept losing the output.
- Emit one line per unit of work (`61/86 host classified`) so progress is visible in the captured buffer at any moment.
- Prefer a progress file the job appends to over a pipe you cannot see into.
- For jobs worth watching live, `start` with `pin_sticky=true` and a `progress_parser` preset (`step`, `percent`, `pytest`, `auto`).

## A block is not an absence

When a long job fetches from the network, a rate-limit or block can masquerade as a legitimate empty result. Before reporting "not found," "no data," or "zero results," confirm the request actually succeeded. Recording a `429` as "nothing there" produces a measurement that looks clean and is wrong.

Corollary: **investigation costs the same politeness budget as production.** Repeatedly probing the same host while debugging will earn a block, and the block then contaminates the diagnosis.

## Workflow

1. `shell_background_start(command, purpose=..., cwd=?, max_lifetime_sec=?)` returns a `job_id` immediately. Runs under shell approval, same rules as `shell`.
2. `shell_background_status(job_id)` reports status, exit code, and the last 500 lines each of stdout/stderr.
3. `shell_background_stop(job_id)` terminates the process (SIGTERM, then SIGKILL after 2s).
4. `shell_background_list()` shows all jobs in this conversation.

## Limits

- Jobs expire after `background.default_max_lifetime_sec` (default 1 hour). Override with `max_lifetime_sec`; `0` disables expiry for a run whose duration you cannot bound.
- `cwd` selects the starting directory inside the workspace. An invalid path fails before the process starts instead of silently running at the workspace root.
- On expiry or stop, partial stdout/stderr captured so far remains available through `status` and the completion record. If nothing was captured, status says so explicitly.
- stdout/stderr buffers hold the last 500 lines each — older lines are dropped, so a job that prints a lot and fails early may have lost the useful part by the time you look.
- Per-conversation job manager — jobs don't leak across conversations.

## Recovering a long job across turns

A wake turn has no memory of what it already read. When a job spans turns, record what you have seen and where you left off in a note or file before ending the turn. Do not rely on remembering the output offset.
