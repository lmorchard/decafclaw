---
name: code_execution
description: Run a small Python script that drives a curated set of decafclaw tools, returning only what the script prints — for deterministic multi-step work where intermediate per-tool outputs would waste context.
always-loaded: true
---

# Code Execution — Programmatic Tool Calling

Run a Python script in an isolated subprocess. The script can call a curated set of decafclaw tools through the `dc.*` proxy and `print(...)` the values you actually want returned. Intermediate tool outputs stay inside the subprocess — only stdout, stderr, and the exit code come back to the conversation.

The framing is **deterministic multi-step**: you already know the sequence of tool calls and the post-processing. The point is to suppress intermediate noise, not to "explore." If you'd describe the work as "I'll figure it out as I go," call tools directly instead.

## When to use

Deterministic multi-step work where you'd otherwise pay for several large tool outputs in context just to extract one field from each. Examples:

- Read 5 vault pages, pull the date from each, return only the earliest.
- Call `tabstack_research` twice, diff the result sets, return the unique items.
- Walk a notes file, count occurrences of a term, return the count.

## When NOT to use

- **Single lookups** — call the underlying tool directly. One `vault_read` / `vault_search` / `notes_read` / `workspace_read` does not need a sandbox; the wrapper just adds overhead and obscures the result.
- **Anything that needs user confirmation** — confirmations (shell approval, `send_email` allowlist fall-through, `vault_write` outside the agent folder, `http_request` outside admin allow-patterns) don't route through the subprocess; the inner call gets a non-interactive-error result instead of prompting. Call those tools directly outside the sandbox.
- **Exploratory work** — if you don't already know which tools you need and in what order, call them interactively first, then collapse into a script if it's worth it.

## Allowlist (v1)

The script can call these 11 tools via `dc.<name>(...)`:

| Tool | Role inside the sandbox |
|------|-------------------------|
| `vault_read` | Read a vault page by path |
| `vault_search` | Semantic search over vault pages + journal |
| `vault_journal_append` | Append an entry to the journal |
| `vault_write` | Create/update a vault page (agent folder only — outside hits non-interactive error) |
| `workspace_read` | Read a file under the workspace |
| `workspace_list` | List workspace files / dirs |
| `notes_read` | Read the per-conversation scratchpad |
| `notes_append` | Append to the per-conversation scratchpad |
| `tabstack_extract_markdown` | Fetch a URL as readable markdown (Tabstack) |
| `tabstack_extract_json` | Fetch a URL extracted to structured JSON (Tabstack) |
| `tabstack_research` | Multi-source web research (Tabstack) |

Not included by design: confirmation-gated tools (`shell`, `send_email`, `http_request`) and tools whose effects are interactive (widgets, canvas, end-turn signals).

## Limits

- 300s wall-clock per script — subprocess killed on expiry
- 50 tool calls per script — over-limit `dc.*` calls return an error to the script (not a hard subprocess abort; the script keeps running but won't get more dispatches)
- 50KB stdout, 10KB stderr (each byte-truncated if exceeded — head 40% + marker + tail; bounded read in the parent so a runaway `print` loop can't OOM the agent)
- 512MB virtual-memory cap (Linux only — `RLIMIT_AS` via `preexec_fn`; no-op on macOS where the limit can't be lowered below `RLIM_INFINITY`)

Defaults live on `SkillConfig` in `src/decafclaw/skills/code_execution/tools.py`; override via env (`SKILLS_CODE_EXECUTION_TIMEOUT_SECONDS=...`) or `config.skills.code_execution` in `data/{agent_id}/config.json`.

## Usage

Each `dc.<tool>(...)` call returns a result object with three attributes:

- `.text` — string payload (always present)
- `.data` — structured dict or `None`
- `.error` — error string or `None` if the call succeeded

```python
from decafclaw_tools import dc

# Read a few vault pages, return only the shortest one's name.
pages = ["agent/pages/A", "agent/pages/B", "agent/pages/C"]
lengths = {p: len(dc.vault_read(page=p).text) for p in pages}
print(min(lengths, key=lengths.get))
```

Whatever you `print` is what the conversation sees. Don't print large intermediates — keep them in local variables. Catch `.error` if you want to handle per-call failures without aborting the whole script.
