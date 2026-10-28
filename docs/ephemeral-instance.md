# Ephemeral Test Instance (headless end-to-end testing)

One command starts a **throwaway** DecafClaw server from *this checkout's*
code, runs a scripted conversation against it with the
[headless client](client.md), prints the client's JSON summary, and tears
everything down — child process and temporary data — on every exit path
(success, failure, timeout, Ctrl-C). This lets a coding agent verify
behavioral changes end to end from inside a turn, without CI, credentials,
or the live daemon (#957).

## Command

```bash
uv run python scripts/ephemeral_instance.py \
    --responses tests/fixtures/ephemeral/tool-roundtrip.responses.json \
    --prompt "Write the exact text hello to the file notes.txt in the workspace."
```

or, for the bundled tool round-trip scenario:

```bash
make ephemeral-test
```

Flags:

| Flag | Default | Meaning |
|---|---|---|
| `--responses` | (required) | JSON file of scripted LLM responses — one entry per LLM call, in order. Format: the [scripted provider](testing.md#scripted-provider-decafclawtesting) response objects (`content`, `thinking`, `tool_calls`, `usage`, `finish_reason`, `error`); see the example files under `tests/fixtures/ephemeral/`. |
| `--prompt` | — | Prompt text. Repeatable; runs sequentially. |
| `--script` | — | File of prompts, one per line (blank lines skipped). |
| `--timeout` | `180` | Per-turn timeout passed to `decafclaw-client`. |
| `--health-timeout` | `60` | Seconds to wait for the child's `/health` endpoint. |
| `--stop-timeout` | `10` | SIGTERM grace period before SIGKILL on teardown. |
| `--format` | `summary` | Client output format (`summary` JSON or `jsonl`). |
| `--log-level` | `INFO` | Child process `LOG_LEVEL`. |

Exit codes are the client's — `0` complete, `1` error, `2` halted on a
confirmation, `3` timeout (see [client.md](client.md#exit-codes)) — plus
`4` for launcher-level failures (missing `uv`, unreadable responses file,
child died at startup, server never became healthy).

## How it works

1. Creates a temporary `DATA_HOME` and writes a `config.json`: a
   `model_configs` entry whose provider is the scripted one,
   `mattermost.enabled: false`, empty `heartbeat.interval`,
   `schedules.enabled: false`, `reflection.enabled: false`,
   `vault_retrieval.enabled: false`, and `http.enabled: true` on a free
   port bound to `127.0.0.1`.
2. Creates a web token in the temp data with `create_token`.
3. Starts the child with the checkout's own interpreter —
   `uv run --project <checkout> python -m decafclaw.testing.ephemeral_server`
   — so the code under test is this checkout, not whatever is installed
   elsewhere. The child entry point loads the responses file, registers the
   [scripted provider](testing.md#scripted-provider-decafclawtesting) under
   its fixed name *after* `init_providers` (in the `start_app`
   extra-provider hook — the same startup function `decafclaw.main()` uses),
   and starts the server.
4. Polls `GET /health` until ready (no fixed sleeps).
5. Runs `decafclaw-client send` with the token and host, printing the
   client's JSON summary to stdout.
6. Stops the child (SIGTERM to its process group, SIGKILL after the grace
   period) and deletes the temp directory — on success, failure, timeout,
   and Ctrl-C.

The launcher relays the child's diagnostic lines to its own stderr: the
registered provider list (must be `['scripted']`), the data home used, and
the child's environment variable names. On failure it also prints the last
30 lines of the child log.

## Safety properties

- **No live-daemon impact.** The child runs against a fresh temporary
  `DATA_HOME`; the live daemon's data directory is never read or written.
- **No credentials, no network.** The child gets an explicit minimal
  environment (`PATH`, temp `HOME`/`TMPDIR`/`UV_CACHE_DIR`, `DATA_HOME`,
  `HTTP_PORT`) and sets `DECACLAW_LOAD_DOTENV=0` so `load_config()` skips
  `load_dotenv()` — the checkout's `.env` (real credentials, real
  `DATA_HOME`) is never imported. The only registered LLM provider is the
  scripted one, which is in-memory.
- **No Mattermost, no schedules, no heartbeat.** `mattermost.enabled:
  false` stops the Mattermost client; `schedules.enabled: false` stops the
  schedule timer (so bundled `dream`/`garden` can never fire); the empty
  `heartbeat.interval` disables the heartbeat; `reflection` and
  `vault_retrieval` are off (no judge calls, no embedding calls).
- **Local only.** The HTTP server binds to `127.0.0.1` on a free port.
- **Production config can never select the scripted provider.** Only the
  child entry point registers it, in-process, after `init_providers`.
- **No orphans.** Teardown kills the child's whole process group and
  removes the temp directory on every exit path.

## Tests

`tests/test_ephemeral_instance.py` exercises the launcher end to end
against the current tree: the tool round-trip (exit 0, expected tool call,
no remaining child process or temp dir, scripted-only provider registry),
the confirmation halt (exit 2, recorded confirmation), a child that never
becomes healthy (clear error, non-zero exit, full teardown), a planted
`.env` with a fake credential and fake `DATA_HOME` (ignored), and a check
that `decafclaw.main()` and the child entry point share the same startup
function. `tests/test_runner.py` covers the `schedules.enabled` switch.
