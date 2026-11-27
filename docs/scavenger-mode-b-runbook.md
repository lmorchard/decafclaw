# Runbook: Mode B Remote Scavenger Orchestration with ghflow and OpenCode

Tracking Epic: [lmorchard/decafclaw#1902](https://github.com/lmorchard/decafclaw/issues/1902)

## Overview

In **Mode B (Local Orchestrator, Remote Worker)**:

- **DecafClaw (VM 302)** acts strictly as the **observer and coordinator**:
  - Manages GitHub project board transitions (`queue select`, `board set-status`).
  - Prepares handoffs and formats directives.
  - Monitors the streaming JSON event log in real time.
  - Posts live narrative updates to GitHub Discussions.
  - Takes zero memory or CPU hit for compiling, heavy test runners, or large subagent contexts.
- **Scavenger Compute Node** (for example `mandelbrot3` or `portia`) acts as the **heavy worker**:
  - Hosts the persistent, isolated `opencode-dev` container.
  - Runs compiler toolchains, Node 22, Python and uv, make, git, and OpenCode.
  - Executes issue implementations, test gates (`make check`), and independent code reviews.

The two sides meet at a single seam: `run_queue.py --worker-cmd`, which executes any
program that speaks the OpenCode CLI shape (`run --auto --format json "<prompt>"`).
Scavenger supplies one such program, its runner adapter. There is no bespoke bridge to
maintain, and an alternative agent can be swapped in without touching the runner.

---

## Architecture and components

```
+-----------------------------------------------------------+
| DecafClaw Host (VM 302: 4 vCPU, 3.8GB RAM)                |
|                                                           |
|  [ run_queue.py / ghflow coordinator ]                    |
|      |                                                    |
|      v  --worker-cmd / GHFLOW_WORKER_CMD                  |
|  [ scavenger runner adapter ]                             |
|    contrib/skills/scavenger/runners/                       |
|      opencode_scavenger_runner.py                         |
+-----------------------------------------------------------+
                          |
            SSH / Podman / Docker exec stream
                          |
+-----------------------------------------------------------+
| Scavenger Node (e.g. mandelbrot3: 62GB RAM, 16 threads)   |
|                                                           |
|  Container: scavenger-env-<env-id>                        |
|  Image: scavenger-recipe-opencode-dev:<digest>            |
|                                                           |
|  [ opencode run ... ]                                     |
|      |-> tools: bash, make, uv, git, gh                   |
|      |-> test gates: make check                           |
|      |-> emits streaming JSON events                      |
+-----------------------------------------------------------+
```

---

## Operational workflow

### 1. Provision the warm scavenger environment

Before a delivery or burndown session:

```python
scavenger_env_create(
    env_id="ghflow-worker",
    recipe="opencode-dev",
    mem="16g",
    cpus=8,
    ttl="4h",
)
```

Credentials go in `env_vars`, never in the volume. The volume outlives the run, so a
script that re-exports secrets from a file in `/workspace` leaves them on the target
disk. `env_vars` lives in the controller's 0600 store and is injected per exec.

### 2. Point `run_queue.py` at the runner adapter

Resolve the adapter path from the scavenger tools rather than hardcoding it:

```bash
WORKER=$(python3 -c 'from contrib.skills.scavenger.tools import scavenger_runner_path; print(scavenger_runner_path(None))')

python3 projects/ghflow/scripts/run_queue.py \
    --owner lmorchard \
    --project decafclaw \
    --worker-cmd "$WORKER" \
    --model openai/gpt-5.4-mini \
    --once
```

Or export it once:

```bash
export GHFLOW_WORKER_CMD="$WORKER"
```

The adapter reads `SCAVENGER_ENV_ID` (default `ghflow-worker`) to pick the container,
and forwards `GH_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and the matching
`*_BASE_URL` values from the controller environment into the container process. None of
them are written to the remote filesystem.

`--opencode-cmd` is a deprecated alias for `--worker-cmd`.

### 3. Status reporting and observation signals

DecafClaw observes the standard streaming events forwarded by the adapter:

- `step_start`: turn number and timestamp.
- `tool` / `tool_use`: tool executions (`bash`, `edit`, `read`, `make check`).
- `step_finish`: token usage, reasoning tokens, running session cost.
- `text`: model responses and final verdicts.

If a turn stalls or exceeds budget, the local runner terminates the remote container
process cleanly over SSH.

---

## Verified behavior

- **Environment:** `opencode-dev` recipe in a rootless container, built and cached on the
  target host.
- **Toolchains:** Node 22 LTS, Python 3, uv, make, ripgrep, jq, git, and the `gh` CLI.
- **Event stream:** the adapter relays the full `step_start` / `text` / `step_finish`
  lifecycle back to DecafClaw with no local memory footprint.
- **Version handling:** `run_queue.py` probes the worker's OpenCode major version and
  passes `--standalone` only for 2.x, because that flag does not exist in 1.x.

---

## Known gaps

- The worker seam still carries OpenCode-specific knowledge: `worker_major()` and
  `worker_run_flags()` exist only to handle `--standalone`, and the session-cleanup path
  hardcodes `session delete`. Supporting a genuinely different agent needs those
  generalized before the seam is agent-agnostic.
- The adapter falls back to `gh auth token` when `GH_TOKEN` is unset, which reads a
  credential from the controller's gh config. Prefer setting `GH_TOKEN` explicitly.
