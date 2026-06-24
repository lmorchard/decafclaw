# Notes — Always-loaded vault `AGENTS.md` (#592)

## Origin

Started from a debugging thread: the deployed agent (Caffeina, gemini-2.5-flash)
re-learned the same lesson — "user journal = `journals/YYYY/…`, my journal =
`agent/journal/`" — five times in ~6 minutes across three conversations, and
journaled a near-duplicate each time. Diagnosis: an *always-applies* procedural
rule was routed through *similarity-gated* memory retrieval. A query about
"meetings this week" embeds far from a vault-structure rule, so retrieval never
surfaced it. The rule **was** documented — in `obsidian/main/AGENTS.md` — but
that's a vault page, not always-loaded prompt content, so it only appeared when
retrieval happened to match or the agent explicitly read it.

The agent also confabulated that the main agent does no proactive vault
retrieval (it does; the `allow_vault_retrieval` flag it half-remembered is the
*child-agent* default-deny). Verified on the deployed box: retrieval is on (mode
`always`, semantic), journal entries are indexed immediately on append and are
in the search pool — so the lesson was retrievable, just never query-matched.

## Decision

Fix the storage/timing mismatch: load the vault's own `AGENTS.md` into context
at **turn assembly** (the only point that precedes the model's first tool
decision). Tool-triggered injection was rejected — a tool call is the model's
output, so injecting in response to one is always a step too late to prevent the
first wrong call.

Key design choices (brainstorm):
- **`system` role** (authoritative), not the `user`-remap used for *retrieved*
  vault content — the goal is reliable enforcement; trust assumption is that
  `AGENTS.md` is the user's own authored file.
- **Single guide file** (YAGNI) — not a configurable list.
- **Read fresh from the vault** (single source of truth) rather than copying
  essentials into `USER.md` — avoids drift. The `USER.md` stopgap was
  intentionally skipped in favor of this durable fix.

## Implementation

Four tasks, subagent-driven (implementer + spec review + code-quality review
each):

1. `VaultGuideConfig` (`enabled`/`path="AGENTS.md"`/`max_tokens=2000`) on
   `Config`, env prefix `VAULT_GUIDE_`.
2. `ContextComposer._compose_vault_guide(config, mode)` — fresh read, wrap in
   `<vault_guide>`, fail-open, token cap (head + warning), skip background modes.
3. Wire into `compose()` — tokens added to `fixed_tokens` *before* the budget
   math; `system` message inserted between the main system prompt and
   `deferred_text`; `SourceEntry` for diagnostics.
4. Docs — `context-composer.md` (new section + layout/modes additions) and
   `vault.md` (mention + cross-link).

## Review-driven fixes

- **Task 2:** broadened `except OSError` → `except (OSError, ValueError)` so
  `UnicodeDecodeError` (non-UTF-8 guide file) is caught by the fail-open
  handler; added a regression test. Also `str()`-wrapped the `details` path.
- **Task 4:** corrected a doc inaccuracy that claimed no env-var prefix exists
  (it's `VAULT_GUIDE_`).

## Verification

- `make check` clean (ruff + pyright 0 errors, no message-type drift).
- Full suite: 2927 passed.
- Final holistic review: ready to merge.

## Follow-ups (separate issues, out of scope here)

- File-wide function-level-import cleanup in `context_composer.py` (the new code
  follows the file's de-facto cycle-avoidance pattern; the stated convention is
  module-level).
- A `config.json` file-based `vault_guide` loading test (defaults + env are
  covered; file-based relies on shared `load_sub_config` coverage).
- The context-window layout diagram shows always-loaded sections (deferred
  tools, now vault guide) inside the SYSTEM-PROMPT box though each is a separate
  `system` message — a pre-existing diagram convention worth a holistic fix.

## Deployment note

Once merged and deployed, this only takes effect on the deployed agent once its
`obsidian/main/AGENTS.md` is present (it is) — no config change needed (defaults
enable it). The five duplicate journal entries from the origin incident remain;
`dream` will consolidate them, or they can be pruned manually.
