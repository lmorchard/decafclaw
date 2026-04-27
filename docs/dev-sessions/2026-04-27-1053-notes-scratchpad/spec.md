# Per-conversation scratchpad

Tracking issue: #299

## Problem

Two adjacent persistent stores already exist:

- **Checklist** — in-turn execution loop, deleted on completion, not surfaced across turns.
- **Vault** — global, shared across all conversations, curated.

Neither fits a cheap per-conversation scratchpad — the place to jot
"user said X", "we decided Y", "try Z next turn" without polluting
the vault or overloading the checklist.

## Goal

Always-loaded `notes_append` and `notes_read` tools backed by an
append-only markdown file at `{workspace}/notes/{conv_id}.md`. Recent
notes auto-inject into context at turn start with a small fixed
budget so the model doesn't pay a tool call per turn to read them.
Notes survive compaction (they live outside the JSONL archive).

## Decisions (autonomous brainstorm)

1. **Per-entry length cap of 1024 chars.** Prevents the model from
   dumping tool output into notes. Configurable via
   `notes.max_entry_chars`.
2. **No tags/categories.** Single sequential stream. Simpler
   schema, simpler semantics; revisit only if a real need surfaces.
3. **Not searchable / not embeddable** in v1. The auto-inject
   covers the dominant access pattern (recent notes). If notes
   accumulate beyond the inject budget and the agent still needs
   them, that's a follow-up.
4. **No `notes_clear` tool.** Manual clear via the workspace files
   tab (or future `vault_delete`-style tool). The agent won't accidentally
   nuke its own context this way. (Out of scope for the always-loaded set.)
5. **Format: one entry per line, ISO timestamp + body.** Markdown-
   compatible (`- {timestamp} — {text}`); mostly so a human can scan
   it but the machine reading is just split-on-lines.
6. **Auto-inject as `conversation_notes` role**, role-remapped to
   `user` for the LLM (mirrors `vault_references` / `vault_retrieval`
   pattern). Skipped in HEARTBEAT/SCHEDULED/CHILD_AGENT modes.

## Architecture

### Core module — `src/decafclaw/notes.py`

```python
def notes_path(config, conv_id) -> Path: ...

def append_note(config, conv_id, text, *, now=None, max_chars: int) -> str:
    """Append one note. Truncates at max_chars. Returns the line written."""

def read_notes(config, conv_id, *, limit: int | None = None,
               max_chars: int | None = None) -> list[Note]:
    """Read newest-first. limit caps count; max_chars caps the total
    body bytes (drops oldest entries until under cap)."""

def format_notes_for_context(notes: list[Note]) -> str:
    """Render the inject block. Returns "" when notes is empty."""
```

`Note` is a small `@dataclass(frozen=True)` with `timestamp: str` and
`text: str`.

### Tools — `src/decafclaw/tools/notes_tools.py`

Two always-loaded tools, both `priority="critical"` so they don't
get deferred:

- `notes_append(text)` — append a single note. Errors on empty/whitespace.
  Truncates silently if text exceeds `max_entry_chars` (returns the
  truncated form so the agent sees what landed).
- `notes_read(limit=20)` — return the last N notes formatted for the
  agent. Returns "[no notes yet]" on empty.

### Context auto-inject — `_compose_notes`

New helper on `ContextComposer`. Returns `(messages, source_entry)`:

- Skip in `HEARTBEAT`/`SCHEDULED`/`CHILD_AGENT` modes.
- Read up to `notes.context_max_entries` (default 20) entries, capped
  by `notes.context_max_chars` (default 4096) total chars.
- Format as a single message:
  ```
  role: conversation_notes
  content:
  [Conversation notes — your scratchpad for this conversation]

  - 2026-04-27T15:30:42Z — User prefers concise replies
  - 2026-04-27T15:31:18Z — Decided to use vertex provider
  ```
- Append to `history` and `to_archive`. Add `conversation_notes` to
  the `role_remap` dict (mapped to `user` for the LLM).

### Configuration — `NotesConfig`

```python
@dataclass
class NotesConfig:
    enabled: bool = True
    max_entry_chars: int = 1024
    context_max_entries: int = 20
    context_max_chars: int = 4096
```

## Out of scope

- `notes_clear` tool — manual clear only for v1.
- Tags / categories.
- Searchable / embeddable notes.
- `dream`/`garden` graduation of repeated notes into vault pages —
  follow-up if value surfaces.
- UI exposure (notes are visible via the Files tab of the web UI
  since they live under `workspace/notes/`).

## Acceptance criteria

- `notes_append("hello")` writes a line to
  `{workspace}/notes/{conv_id}.md`. Subsequent appends are appended
  in order.
- `notes_read()` returns the latest entries (default 20).
- Context auto-inject adds a single `conversation_notes` message at
  turn start when notes exist. Skipped in non-interactive modes.
- Notes file survives compaction (not touched by `compact_history`).
- Per-entry cap silently truncates oversized text and returns the
  truncated form.
- `notes.enabled = false` disables tools (return error stubs) and
  context inject (no message).

## Testing

- **Unit tests** (`tests/test_notes.py`): append, read with various
  limits/caps, truncation at `max_entry_chars`, format helper, empty
  cases, special characters in text (newlines / unicode), file
  ordering on disk.
- **Tool tests** (`tests/test_notes_tools.py`): tool wrappers
  return expected messages, reject empty input, truncate over-long
  inputs.
- **Composer integration test** (`tests/test_context_composer.py`
  extension or new `test_notes_inject.py`): notes injected when
  present; skipped in heartbeat mode; role remapped to `user` in
  LLM message list.

## Files touched

- `src/decafclaw/notes.py` (new)
- `src/decafclaw/tools/notes_tools.py` (new)
- `src/decafclaw/tools/__init__.py` — register NOTES_TOOLS / NOTES_TOOL_DEFINITIONS.
- `src/decafclaw/config_types.py` — `NotesConfig`.
- `src/decafclaw/config.py` — register `notes` sub-config.
- `src/decafclaw/context_composer.py` — `_compose_notes`, `role_remap`, sidecar entry.
- `tests/test_notes.py`, `tests/test_notes_tools.py` (new)
- `docs/notes.md` (new), `docs/index.md`, `docs/config.md`,
  `CLAUDE.md`.
