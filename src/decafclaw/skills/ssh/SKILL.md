---
name: ssh
description: Run commands and copy files on SSH host aliases, and manage the host approval rulebook.
---

# SSH hosts

Use this skill when the user asks for work on a remote machine, or when a task needs a file or command on a configured SSH host. Use `ssh_exec` and `ssh_copy` — do not bury `ssh`/`scp` inside the generic `shell` tool. The native tools resolve hosts, enforce non-interactive flags, return structured output, and apply the host rulebook.

## Hosts

Hosts are concrete `Host` aliases from `~/.ssh/config`. Wildcard, negation, and pattern entries are not accepted as aliases; raw IP addresses are never aliases.

An unlisted alias or raw IP returns `[error: host ...]` with an explicit confirmation prompt. Do not retry with `allow_unlisted_host=True` on your own: the user must approve the host first. After approval, retry the same call with that flag set. Never use `allow_unlisted_host=True` to bypass a denial.

## Commands

`ssh_exec(host, command, timeout=30, allow_unlisted_host=False)` runs:

```text
ssh -o BatchMode=yes -o ConnectTimeout=5 <host> <command>
```

`BatchMode=yes` prevents password/host-key/sudo prompts from hanging the turn; `ConnectTimeout=5` bounds the connection. The wall-clock timeout kills the process.

The result carries structured data:

```json
{"host": "sandbox", "exit_code": 0, "stdout": "...", "stderr": ""}
```

Check `exit_code` instead of guessing from text. A non-zero exit is a real failure; a timeout sets `timed_out: true`.

## Files

`ssh_copy(host, local_path, remote_path, direction="up"|"down", timeout=60, allow_unlisted_host=False)` uses `scp` with the same non-interactive options.

- `local_path` must resolve inside the agent workspace; absolute paths outside it are rejected.
- Use `direction="up"` to send the workspace file to `host:remote_path`, `"down"` to fetch it.
- Do not use `ssh`/`scp` through `shell` to work around a rejected local path.

## Host rulebook

Admin rules live at `data/{agent_id}/ssh_rules.jsonl` (admin-protected, outside the workspace). One JSON object per line:

```json
{"tool": "ssh_exec", "host": "scavenger-*", "action": "allow"}
{"tool": "ssh_copy", "host": "edge-*", "action": "deny"}
{"tool": "*", "host": "sandbox", "action": "ask"}
```

- `tool`: `ssh_exec`, `ssh_copy`, or `*`.
- `host`: an `fnmatch` pattern over the alias.
- `action`: `allow`, `ask`, or `deny`.

Precedence is **deny > allow > ask** across all matching rules. `allow` skips the unlisted-host confirmation; `deny` refuses; `ask` or no match keeps normal behavior. A missing/malformed rulebook fails open to normal behavior; malformed lines are skipped and never treated as `allow`.

To inspect or change rules, use `admin_read`/`admin_write` (admin writes are confirmation-gated). Read the current file first, keep one object per line, and do not add broad `allow` rules for raw IPs, production/edge hosts, or `tool: "*"` without explicit user review. Prefer the narrowest host pattern that covers the request.

## Safety

- Do not run interactive commands (editors, prompts, `sudo` waiting for a password). Use non-interactive flags and fail instead of hanging.
- Do not put secrets, tokens, or private keys in commands or rule files. Reference existing host configuration.
- Treat remote changes as production changes: state the exact command and host, and check the structured exit code.
- If a host or a rule is not approved, stop and ask. The confirmation path is the boundary, not an obstacle.
