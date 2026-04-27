# Notes (per-conversation scratchpad)

A cheap, persistent scratchpad scoped to a single conversation. The agent uses it to jot "user said X", "we decided Y", "try Z next turn" — things that should survive across turns within this conversation but don't belong in the vault (which is curated, cross-conversation knowledge) or the checklist (which is in-turn step-by-step execution).

Tracking issue: #299.

## Where notes fit alongside other primitives

| Primitive | Scope | Lifetime | Curation |
|---|---|---|---|
| **Checklist** | Per-conversation | In-turn (deleted on completion) | Step-by-step execution loop |
| **Notes** (this) | Per-conversation | Persistent across turns + compactions | Append-only, lightly curated |
| **Vault** | Cross-conversation | Persistent across conversations | Curated pages, journal, user notes |

If the answer to "where do I keep this for later?" is "until the conversation ends," it goes here. If it's "for any future conversation," vault.

## Tools

Two always-loaded tools (priority `critical`, never deferred):

- **`notes_append(text)`** — append a single note. Newlines collapsed to spaces (each note stays on a single line). Silently truncated to `notes.max_entry_chars` (default 1024 chars) — over-long notes return the truncated form so the agent sees what landed.
- **`notes_read(limit=20)`** — return the most recent N notes formatted as the same block the auto-inject uses. Use this only when the agent needs to scan beyond what was auto-injected (e.g. searching for an older detail). For near-recent notes, the auto-inject already has them.

There's no `notes_clear` tool in v1 — manual cleanup via the workspace files tab. Keeps the agent from accidentally nuking its own context.

## Auto-inject on turn start

Every interactive turn, the `ContextComposer` reads up to `notes.context_max_entries` (default 20) recent notes (capped by `notes.context_max_chars`, default 4096 chars total — drops oldest first when over cap) and injects them as a single message:

```
[Conversation notes — your scratchpad for this conversation]

- 2026-04-27T15:30:42Z — User prefers concise replies
- 2026-04-27T15:31:18Z — Decided to use vertex provider
- 2026-04-27T15:32:55Z — Try the gemini-2.5-pro model next
```

The message has role `conversation_notes` (remapped to `user` for the LLM), so the agent reads its own scratchpad without a tool call. Skipped in heartbeat / scheduled / child-agent modes — those don't have a meaningful per-conversation scope to inject into.

## Storage

`{workspace}/conversations/{conv_id}.notes.md` — append-only markdown, one entry per line. Colocated with the conversation archive (`{conv_id}.jsonl`) and other sidecars (`.context.json`, `.decisions.json`):

```
- 2026-04-27T15:30:42Z — User prefers concise replies
- 2026-04-27T15:31:18Z — Decided to use vertex provider
```

Each line is `- {ISO-8601 UTC timestamp} — {text}`. Markdown-readable on disk so a human can scan it in an editor.

The file survives compaction (it's a sidecar, not part of the JSONL archive). Manual edits are tolerated — the parser skips lines that don't match the expected shape. Conversation deletion (web UI) cleans up the notes file alongside the archive and other sidecars.

## Configuration

See [config.md#notes](config.md#notes). Tunables:

- `notes.enabled` (default `true`)
- `notes.max_entry_chars` (default `1024`)
- `notes.context_max_entries` (default `20`)
- `notes.context_max_chars` (default `4096`)
- `notes.max_total_entries` (default `1000`) — file-level cap. When an append would push the file over the limit, oldest entries get dropped via an atomic rewrite so long-running conversations don't accumulate unbounded read cost per turn.

`notes.enabled = false` short-circuits both the tools (they return an error stub) and the auto-inject (no message added).

## When to use vs other places

- **Use `notes_append`** for things you want to remember across turns within this conversation but **not** across conversations. Agent preference observations, partial results, "try X next turn" hints, things you'd lose to compaction otherwise.
- **Use `vault_write`** for knowledge that's worth keeping across conversations. Curated, indexed, semantic-searchable.
- **Use `checklist_create`** when you have a fixed multi-step plan to execute *in this turn* and want the loop to drive you through it.

## Out of scope (for v1)

- `notes_clear` tool — manual clear only.
- Tags / categories — single sequential stream.
- Searchable / embeddable — covered for the recent window by auto-inject.
- `dream` / `garden` graduation of repeated notes into vault pages — file as follow-up if useful.
