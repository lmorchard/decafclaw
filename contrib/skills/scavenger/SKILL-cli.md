# Scavenger CLI (for shell-only agents)

Run heavy builds and tests on idle LAN machines over SSH. This file is for agents that only have a shell (Claude Code, Codex, OpenCode, a CI runner). It needs Python 3.9+ and `ssh`. It has no other dependencies and does not need decafclaw.

```sh
python3 path/to/scavenger.py [--config FLEET_JSON] <command> [--json] ...
```

## Setup

1. Write a fleet file at `~/.config/scavenger/fleet.json`, or pass `--config PATH`. Copy `fleet.example.json` and edit it. `ENROLLMENT.md` explains how to enroll a host.
2. Optional: add `"recipes_dir": "/path/to/recipes"` to `fleet.json`. A relative path is relative to `fleet.json`.
3. Lease state lives in `~/.config/scavenger/leases.json`.

## Rules

- Add `--json` to any command to get JSON on stdout. Without it you get text for people.
- Every command exits 0 on success and non-zero on failure. With `--json`, a failure prints `{"error": "..."}`.
- `run`, `exec-native`, and `env-exec` exit with the remote command's exit code.
- `env-create` always prints JSON.
- Always `probe` first. Do not assume which hosts exist.
- Always `env-destroy` an environment when you finish. Leases also expire (`--ttl`, default 2h), and `reap` removes expired ones.

## Commands

| Command | Purpose |
| --- | --- |
| `probe [--host H] [--no-wake]` | Check which hosts are online and eligible (battery, game streaming). |
| `wake HOST` | Send a Wake-on-LAN packet and wait for SSH. Exit 1 if the host stays asleep. |
| `run CMD [--repo DIR] [--host H] [--arch A] [--image I] [--mem M] [--cpus N] [--timeout S] [--force]` | One-shot job in a throwaway container. `--repo` sends the git HEAD of DIR into `/workspace`. With `--json`, stdout and stderr come back in the JSON. |
| `exec-native HOST CMD [--timeout S]` | Run a command directly on the host OS as the scavenger user. |
| `env-create ID [--host H] [--kind container\|native] [--image I] [--recipe R] [--recipes-dir DIR] [--setup-script F] [--sync-dir DIR] [--ttl 2h] [--arch A]` | Reserve a stateful environment. |
| `env-exec ID CMD [--timeout S]` | Run a command in the environment. |
| `env-info ID` | Show host, target, and remaining TTL. |
| `env-sync ID up\|down DIR` | Copy files into `/workspace` (`up`) or out of it (`down`). |
| `env-list` | List active leases. |
| `env-destroy ID` | Remove the environment and its lease. |
| `reap` | Destroy every expired lease. |

## Recipes

`--recipe NAME` looks for `NAME.Dockerfile` in this order:

1. `--recipes-dir`.
2. `recipes_dir` in `fleet.json`.
3. `./dockerfiles`.
4. The `dockerfiles/` directory next to `scavenger.py` (`node-dev`, `android-builder`).

Setup scripts (`NAME.sh` or `setup-NAME.sh`) are searched in `setup-scripts/` next to the recipes directory, then next to `scavenger.py`. An unknown recipe is an error.

## Example

```sh
S=path/to/scavenger.py
python3 $S probe --json
python3 $S env-create build1 --recipe node-dev --sync-dir . --ttl 1h
python3 $S env-exec build1 'cd /workspace && npm ci && npm test' --json
python3 $S env-sync build1 down ./artifacts
python3 $S env-destroy build1
```

## Not in the CLI

The decafclaw canvas terminal and `wake_on` background watches only exist as decafclaw tools (see `SKILL.md`). In a shell, run long jobs with `env-exec` and a generous `--timeout`.

## Container labels

Containers carry the labels `scavenger.env_id` and `scavenger.expires_at`. Containers made before the rename used `decafclaw.env_id` and `decafclaw.expires_at`. Tools that read labels must accept both.
