# Procedural toil audit (Three Strikes)

The improvement scout ([#1482](extensions.md#improvement-scout)) records
*negative* friction: tool errors, denied permissions, and user corrections.
It cannot see **invisible procedural toil** — a successful turn where the
agent burns tokens and context rediscovering a command sequence or building a
defensive shell pipeline that a deterministic primitive should replace.

The audit applies the C2 rule
[Three Strikes and You Automate](https://wiki.c2.com/?ThreeStrikesAndYouAutomate)
across conversation archives. It is a batched, on-demand scanner: nothing runs
per turn and no state accumulates between audits.

## Detection

1. Read conversation archives (`sessions/` and legacy `conversations/`) inside
   a lookback window.
2. Extract ordered `tool_calls` from assistant messages.
3. Normalize variable arguments: paths, temp files, SHAs, URLs, issue refs,
   numbers, and free-text search values become placeholders; volatile payload
   arguments (`content`, `purpose`, `old_text`, …) never enter a signature.
4. Mine every contiguous sequence of 2–5 calls and count **distinct
   conversations** per normalized sequence.
5. Prune a sequence when a longer sequence of the same kind contains it and
   appears in at least as many conversations.
6. Keep only sequences that exhibit one of three toil signatures and reach
   `--min-strikes` distinct conversations (default 3).

## Toil signatures

| Kind | Detection | Remedy template |
|---|---|---|
| `defensive_shell` | A shell call with `2>&1`, `> /tmp/…`, `| tail`/`head`/`grep`/`rg`, `|| echo`/`true`, `> /dev/null`, or `echo $?` | **A — Missing wrapper:** add an atomic tool or compact CLI command with bounded output. |
| `read_after_write` | A write tool immediately followed by a read tool on the same workspace path or vault page | **C — Untrusted write:** return verification metadata (path, version, size, hash) from the write. |
| `discovery_ceremony` | Two or more directory traversals or dependency checks in one sequence (`workspace_list`, `workspace_glob`, `vault_list`, `ls`, `find`, `git status`, `uv sync`, `make install`, …) | **B — Incomplete primitive:** complete the setup/discovery contract of the existing tool. |

## CLI

```sh
decafclaw toil audit                      # last 7 days, 3 strikes, text report
decafclaw toil audit --days 30            # wider lookback
decafclaw toil audit --days 0             # every archive
decafclaw toil audit --min-strikes 5
decafclaw toil audit --json               # machine-readable report
decafclaw toil audit --export             # buffer new candidates in the inbox
```

`--export` appends each new candidate to `workspace/improvement_inbox.jsonl`
as a `toil_strike_3` record, reusing the #1482 contract:

- `trigger`: `toil_strike_3`
- `subsystem`: `shell`, `workspace`, `vault`, `tabstack`, or `unknown`
- `severity`: `high` at five or more strikes, else `medium`
- `title`: `Procedural toil: <sequence summary>`
- `observed_friction`: the normalized sequence plus the distinct conversation
  ids
- `suggested_improvement`: the remedy template for the kind
- `toil_sequence` and `strike_count`: structured audit fields

An existing inbox record with the same sequence or title is skipped, so a
nightly audit does not duplicate candidates. Audit writes go through the same
locked append path as the scout.

## Skill tool

The `improvement-inbox` contrib skill exposes `toil_audit`, which runs the
same scan from inside a conversation (for example during a nightly retro):

```text
toil_audit(days=7, min_strikes=3, export=false)
```

`export=true` buffers new candidates in the inbox. The tool's `data` payload
contains the full report and, when exporting, the new item ids.

## Limits

The audit is a heuristic over archived tool **names and arguments**, not
success or latency. It sees only calls that reached the archive (compaction
keeps the archive). A sequence that is genuinely appropriate to repeat — for
example reading a different file after each write — should be dismissed with a
reason rather than treated as a missing primitive.
