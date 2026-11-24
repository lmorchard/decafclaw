# Scavenger CLI for shell-only agents

Use this file when your agent has a shell but no DecafClaw tools (Claude Code, Codex, OpenCode, a CI runner). It documents `scavenger.py`, which uses only the Python standard library and `ssh`. It needs Python 3.9 or newer.

`scavenger.py` sends heavy work (test suites, builds, ML jobs) to idle machines you own, over SSH. The target machines run no daemon.

```sh
python3 contrib/skills/scavenger/scavenger.py [--config FLEET_JSON] <command> [options]
```

`--config` goes before the command name. `--json` goes after it.

## Setup

1. Write a fleet file at `~/.config/scavenger/fleet.json`. See `fleet.example.json` and `ENROLLMENT.md`. Pass `--config PATH` to use another file.
2. Check the setup: `python3 contrib/skills/scavenger/scavenger.py probe --json`.

If no fleet file exists, every command fails with exit code 1.

## Output and exit codes

- Add `--json` to any command to get one JSON document on stdout. Without it, you get text for people.
- On failure, `--json` prints `{"error": "<message>"}` on stdout. Without it, the message goes to stderr as `Error: <message>`.
- Exit code 0 means success. Exit code 1 means the command failed (unknown host, no lease, no fleet file, host not eligible). Exit code 124 means a timeout.
- For `run`, `exec-native`, and `env-exec`, the exit code is the exit code of your command. Read it from `exit_code` in the JSON output as well.
- `env-create` always prints JSON, with or without `--json`.

## Commands

| Command | What it does | JSON output |
| --- | --- | --- |
| `probe [--host H] [--no-wake]` | Check which hosts are online and eligible. | List of host status objects. |
| `wake HOST` | Send Wake-on-LAN and wait for SSH. Exit 1 if the host stays down. | `{host, awake, message}` |
| `run COMMAND [--repo DIR] [--host H] [--arch A] [--image I] [--mem M] [--cpus N] [--timeout S] [--force]` | One-shot container job. `--repo` sends the git `HEAD` of that directory to `/workspace`. | `{host, exit_code, stdout, stderr}` |
| `exec-native HOST COMMAND [--timeout S]` | Run a command on the host itself, as the `scavenger` user. Use it for Apple Silicon Metal jobs. | `{host, exit_code, stdout, stderr}` |
| `env-create ENV_ID [--kind container\|native] [--recipe R] [--recipes-dir DIR] [--image I] [--setup-script F] [--sync-dir DIR] [--ttl 2h] [--host H] [--arch A]` | Reserve an environment that keeps its state between commands. | The lease object. |
| `env-exec ENV_ID COMMAND [--timeout S]` | Run a command in the environment. | `{env_id, host, exit_code, stdout, stderr}` |
| `env-info ENV_ID` | Show the host, target, and time left. | Lease object plus `expires_in_seconds` and `expired`. |
| `env-sync ENV_ID up\|down LOCAL_DIR` | Copy a directory to or from `/workspace`. | `{env_id, direction, local_path, ok}` |
| `env-list` | List active leases. | Map of env id to lease. |
| `env-destroy ENV_ID` | Remove the environment and its lease. Exit 1 if it did not exist. | `{env_id, destroyed}` |
| `reap` | Remove all expired leases. | `{reaped: [env ids]}` |

Leases are kept in `~/.config/scavenger/leases.json` on the machine that runs the CLI. Run `reap` now and then, because an expired environment keeps using memory on the target until it is reaped.

## Recipes

A recipe is a Dockerfile named `<recipe>.Dockerfile`. `env-create --recipe NAME` builds it once on the target host and reuses the image.

The CLI looks for the recipe directory in this order:

1. The `--recipes-dir` option.
2. The `recipes_dir` key in `fleet.json`. A relative path is relative to `fleet.json`.
3. `./dockerfiles`, in the current directory.

If the recipe is not in that directory, the CLI also checks the recipes bundled with this skill (`android-builder`, `node-dev`). If it finds none, `env-create` fails with `Recipe not found`.

## Typical session

```sh
S=contrib/skills/scavenger/scavenger.py

python3 $S probe --json
python3 $S env-create ci-1 --recipe node-dev --sync-dir . --ttl 1h
python3 $S env-exec ci-1 'npm ci && npm test' --json
python3 $S env-sync ci-1 down ./artifacts
python3 $S env-destroy ci-1
```

For a single job with no saved state:

```sh
python3 $S run --repo . --image python:3.12 'pip install -e . && pytest' --json
```

## Rules

- Always run `probe` first. Do not assume what hosts exist.
- Always run `env-destroy` when you finish. Pick a short `--ttl` as a safety net.
- A host can be "not eligible" because it runs on battery or is busy. That is normal. Choose another host or try later. Use `run --force` only if the operator allows it.
- Quote the command as one argument. The CLI wraps it in `sh -c '...'`, so a single quote inside the command breaks it. Put complex commands in a script, send it with `--sync-dir`, and run the script.

## Not available in the CLI

The canvas terminal (`scavenger_env_terminal`), automatic wake-ups (`wake_on`, `scavenger_env_watch`), and background execution (`bg=True`) exist only as DecafClaw tools. In a shell, run `env-exec` in the foreground with a `--timeout`.
