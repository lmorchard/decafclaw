# Spec — Inject vault-root `AGENTS.md` as always-loaded context

**Issue:** [#592](https://github.com/lmorchard/decafclaw/issues/592) — enhancement, core · Ready / P1 / S
**Branch:** `592-vault-guide-always-loaded`

## Problem

The vault can contain a user-authored guide to vault structure and agent protocols
(`obsidian/main/AGENTS.md` on the deployed agent) — folder layout, which paths are the
user's vs the agent's, append-only rules. Today that file is just another vault page:
the agent only sees it when (a) semantic retrieval happens to surface it for the current
query, or (b) the agent explicitly reads it.

That fails for procedural rules that must *always* apply regardless of query. Observed on
the deployed agent: asked to "summarize my meetings this week," it confused
`agent/journal/` (its own journal) with `journals/YYYY/…` (the user's journal). The
distinction **is** documented in the vault's `AGENTS.md`, but a meeting-summary query
embeds nowhere near a vault-structure guide, so retrieval never surfaced it. The agent
guessed wrong, was corrected, journaled the lesson — then repeated the identical mistake
(and re-journaled it) across three conversations in ~6 minutes, because the journaled
lesson is *also* similarity-gated and never feeds back proactively.

Root cause: an *always-applies* rule is routed through an *occasionally-matched* episodic
store. A tool-triggered injection ("inject before vault tools run") can't fix it either —
a tool call is the model's output, so by the time the call exists the wrong decision is
already made; tool-triggered injection is always one step late and can't prevent the
*first* wrong call. The only injection point that prevents the first mistake is **turn
assembly**.

## Solution

Add a `ContextComposer` step that, when a vault-guide file is present, injects its
contents as an **always-loaded** section every interactive turn — read fresh from the
vault's own `AGENTS.md` (single source of truth, no duplication into `USER.md`/`AGENT.md`,
no drift).

### Behavior

1. **New step `_compose_vault_guide`**, called from `ContextComposer.compose()`.
2. Resolves `<vault_root>/<guide path>` (default `AGENTS.md`) using the same
   `_vault_root(config)` logic the vault tools use (`skills/vault/tools.py:39`).
3. If the file exists and is non-empty, reads it **fresh** (plain file read — no
   embeddings, so it works even when semantic search is off), wraps it in a
   `<vault_guide>` XML section per the system-prompt section convention, and injects it
   as a **`system`** message.
   - **Role rationale:** authoritative, like `SOUL.md`/`AGENT.md`, because the goal is
     reliable enforcement of vault-layout rules. This intentionally departs from the
     composer's convention of remapping *retrieved* vault content to the `user` role
     (prompt-injection posture). Trust assumption: `AGENTS.md` is the user's own authored
     file in a single-user agent. Documented as such.
4. **Placement:** immediately after the main `system_text` message, *before*
   `deferred_text` / `preempt_skill_text` (`context_composer.py:438`). It is stable,
   authoritative content, so it belongs adjacent to the system prompt and early in the
   prefix for prompt-cache stability (deferred/preempt churn more).
5. **Mode skip:** skip for `HEARTBEAT` / `SCHEDULED` / `CHILD_AGENT` — mirror the
   `skip_modes` set in `_compose_vault_retrieval` (`context_composer.py:~571`).
6. **Fail-open:** missing file, no vault, or read error → no section, no error
   (log at debug).
7. **Token cap:** if the guide exceeds `max_tokens`, truncate to the cap (keep the head)
   and log a warning — never silent.
8. **Diagnostics:** add a `SourceEntry(source="vault_guide", …)` so it appears in the
   diagnostics waffle chart and the `[Context: …]` status line.

Independent of `vault_retrieval`: separate enable flag, no embedding dependency, not
affected by retrieval `mode`.

### Config

New `VaultGuideConfig` dataclass in `config_types.py` (mirrors `VaultRetrievalConfig`'s
shape rather than crowding `VaultConfig`), resolved via the standard defaults →
`config.json` → env order:

```python
@dataclass
class VaultGuideConfig:
    enabled: bool = True
    path: str = "AGENTS.md"      # relative to vault root
    max_tokens: int = 2000       # AGENTS.md is ~500 tok; generous cap
```

Wired onto `Config` alongside `vault` / `vault_retrieval`.

## Testing

Unit-only (deterministic context-assembly, not LLM routing/tool-choice — **no eval**):

- Present file → `<vault_guide>` `system` message appears in composed output for
  `INTERACTIVE`.
- Absent file / empty file / unreadable → no section, no error (fail-open).
- Oversized file → truncated to cap + warning logged.
- Per-mode skip → iterate `ComposerMode` values (don't hand-list), assert skipped for the
  three background modes, present for `INTERACTIVE`.
- `enabled = False` → no section.

## Docs

Same-PR doc updates:

- `docs/context-composer.md` — new always-loaded section in context assembly.
- `docs/vault.md` — one line on the vault guide.

## Out of scope (YAGNI)

- A configurable *list* of always-loaded vault docs — single guide file only; generalize
  later if a real need appears.
- `USER.md` stopgap — intentionally skipped in favor of this durable fix.
- Lazy inject-on-first-vault-tool-use — rejected (reactive; can't prevent the first wrong
  call).

## Files (anticipated)

- `src/decafclaw/config_types.py` — `VaultGuideConfig`, wired onto `Config`.
- `src/decafclaw/config.py` — defaults/env resolution if needed.
- `src/decafclaw/context_composer.py` — `_compose_vault_guide`, call site, `SourceEntry`.
- `tests/…` — unit coverage above.
- `docs/context-composer.md`, `docs/vault.md`.
