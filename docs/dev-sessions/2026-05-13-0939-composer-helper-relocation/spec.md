# Context Composer Helper Relocation Spec

**Goal:** Stop `context_composer.py` from importing underscore-prefixed helpers out of `agent.py`. Move each helper to a module that legitimately owns it so the public import surface is honest.

**Source:** [Issue #439](https://github.com/lmorchard/decafclaw/issues/439)

## Current state

`src/decafclaw/context_composer.py` reaches into `agent.py` for four underscore-prefixed helpers:

| Line | Import | Lives in |
|---|---|---|
| `:269` | `from .agent import _resolve_attachments` | `agent.py:65` |
| `:725` | `from .agent import _get_already_injected_pages, _parse_wiki_references, _read_wiki_page` | `agent.py:901–953` |
| `:826`, `:1016` | `from .agent import _collect_all_tool_defs` | `agent.py:381` |

The underscore prefix lies about the surface — these are effectively part of the public API of `agent.py`, but cross-module imports of `_`-names are a coupling smell.

## Desired end state

1. **`_collect_all_tool_defs` → `tool_definitions.collect_all_tool_defs`.** This relocation is performed by issue #438; this session only needs to update the import in `context_composer.py` to `from .tool_definitions import collect_all_tool_defs`.
2. **Wiki/page-injection helpers** (`_get_already_injected_pages`, `_parse_wiki_references`, `_read_wiki_page`) → new home, either `vault_refs.py` (new module) or folded into `memory_context.py` where related vault retrieval lives. Rename to drop the underscore. Both `agent.py` and `context_composer.py` import from the new home.
3. **`_resolve_attachments` → `attachments.py`** (where it already imports `read_attachment_base64`). Drop the underscore. Update `tests/test_resolve_attachments.py` to the new import path.
4. Grep audit confirms no `from .agent import _` in any non-test module.

## Design decisions

- **Decision:** Pick `memory_context.py` vs new `vault_refs.py` based on cohesion. If `memory_context.py` already has wiki-link handling, fold the three helpers there; otherwise create `vault_refs.py`.
  - **Why:** Avoid module proliferation when an existing module already owns the same concept; create a new module only if the existing candidates aren't a good fit.
  - **Rejected:** Forcing into `agent.py` (the status quo) — fails the refactor goal.

- **Decision:** Drop underscores on the relocated helpers.
  - **Why:** Same reason as #438 — cross-module imports of `_`-names are a coupling smell. The move is the moment to fix it.

- **Decision:** Treat `_resolve_attachments` as *not* dead code, despite an earlier exploration agent reporting otherwise. Verification showed it's actively called at `context_composer.py:425` and has dedicated tests.

## Patterns to follow

- New-module shape mirrors existing peers (`memory_context.py`, `attachments.py`) — top-level functions, no class wrappers unless state demands it.
- If creating `vault_refs.py`: keep helpers private (with `_` prefix) unless imported externally; export only the three relocated helpers + anything they need.

## What we're NOT doing

- **NOT performing the `_collect_all_tool_defs` extraction itself** — that's #438. This session only updates the import target.
- **NOT modifying `_resolve_attachments` behavior** — pure relocation + rename.
- **NOT modifying wiki-link parsing logic** — pure relocation + rename.
- **NOT introducing a new module unless `memory_context.py` is the wrong home.**
- **NOT touching anything in `agent.py` not directly related to these three helpers.**

## Validation

- `make check` (lint + typecheck) green.
- `make test` green, including `tests/test_resolve_attachments.py` (with updated import path).
- Grep audit: `grep -rn "from .agent import _" src/` returns nothing in non-test code.
- Grep audit: `grep -rn "from decafclaw.agent import _" src/` returns nothing in non-test code.

## Sequencing dependency

This session depends on #438 landing first (or at minimum, `tool_definitions.py` being available). If #438 hasn't merged yet, this branch will need to either:
1. Wait for #438's PR to merge to main, then rebase onto main, OR
2. Rebase onto the `refactor/agent-split-tools` branch directly so the agent has access to `tool_definitions.py`.

## Open questions

- None blocking — module-home decision (`memory_context.py` vs new `vault_refs.py`) is made during plan/execute by reading `memory_context.py` cohesion.
