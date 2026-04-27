# Plan

See `spec.md` for architecture + decisions.

## Phase 1 — core module + config

- `notes.py`: `Note` dataclass, `notes_path`, `append_note`, `read_notes`, `format_notes_for_context`. Atomic append with parent-dir mkdir; lock-free since each conv has its own file.
- `config_types.py`: `NotesConfig` dataclass.
- `config.py`: import + register sub-config + add to Config dataclass.
- `tests/test_notes.py`: covers append, read (limit + char cap), truncation, format, empty, ordering, unicode/newline handling.

## Phase 2 — always-loaded tools

- `tools/notes_tools.py`: `tool_notes_append`, `tool_notes_read`. Critical priority. Returns ToolResult.
- `tools/__init__.py`: register NOTES_TOOLS / NOTES_TOOL_DEFINITIONS in the global maps.
- `tests/test_notes_tools.py`: tool dispatch tests.

## Phase 3 — context auto-inject

- `context_composer.py`: `_compose_notes(ctx, config, mode)` returning `(messages, SourceEntry | None)`. Skip in HEARTBEAT/SCHEDULED/CHILD_AGENT modes. Append to history + to_archive. Add `conversation_notes` to `role_remap`. Add tokens to fixed-cost calculation.
- `tests/test_notes_inject.py` (new) or extend `tests/test_context_composer.py`: notes injected when present; skipped in non-interactive; role remapped.

## Phase 4 — docs

- `docs/notes.md` (new) — scratchpad model, tools, config, examples.
- `docs/index.md` — link.
- `docs/config.md` — `notes.*` reference table.
- `CLAUDE.md` — context-engineering bullet + key-files entry.

## Phase 5 — squash, push, PR, request Copilot

`Closes #299`. Move project board to In review.
