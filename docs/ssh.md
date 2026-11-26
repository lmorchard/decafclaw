# SSH tools

`ssh_exec` and `ssh_copy` are native tools for remote commands and file transfer. They replace stringing `ssh`/`scp` through the generic shell tool, so the agent gets non-interactive safety and structured output instead of escaped shell text.

## Host resolution

Hosts must be concrete aliases in `~/.ssh/config` (the tool ignores wildcard, negation, and pattern entries). Raw IP addresses are never treated as aliases.

An unknown alias or raw IP returns an error and an explicit confirmation prompt. After approval, the model retries the same call with `allow_unlisted_host=True`. Do not set that flag without a human approving the host.

## `ssh_exec`

```text
ssh_exec(host, command, timeout=30, allow_unlisted_host=False)
```

Runs `ssh -o BatchMode=yes -o ConnectTimeout=5 <host> <command>` with a bounded wall-clock timeout. `BatchMode=yes` prevents password, host-key, and sudo prompts from hanging the turn; `ConnectTimeout=5` bounds the connection attempt.

The result carries structured data:

```json
{"host": "sandbox", "exit_code": 0, "stdout": "...", "stderr": ""}
```

A non-zero exit returns an `[error: ssh exited N ...]` text with the same data. A timeout kills the process and sets `timed_out: true`.

## `ssh_copy`

```text
ssh_copy(host, local_path, remote_path, direction="up"|"down", timeout=60, allow_unlisted_host=False)
```

Uses `scp` with the same `BatchMode`/`ConnectTimeout` options. `local_path` must resolve inside the agent workspace; absolute paths outside it are rejected. `direction="up"` sends the local file to `host:remote_path`; `"down"` fetches it.

## Rulebook integration

Approval rules can key on `(tool="ssh_exec", host="<alias>")` and `(tool="ssh_copy", host="<alias>")`, so a `scavenger-*` worker can be pre-approved while infrastructure hosts stay gated. The tool-level confirmation above is the fallback when no rule matches.
