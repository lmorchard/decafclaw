# Vault user-page writes via confirmation gate

**Goal:** Let the agent write/delete/rename pages outside its `agent/` folder when the user explicitly asks, gated by user confirmation. Add a static config allowlist for trusted folders and a per-conversation grant tool for batch operations.

**Source:** User request from 2026-05-07 (chat session).

## Current state

The vault has three write-class tools that hard-refuse any path outside `config.vault_agent_dir`:

- `tool_vault_write` — refuses at `src/decafclaw/skills/vault/tools.py:171` with `"only pages under the agent folder may be written"`.
- `tool_vault_delete` — refuses at `src/decafclaw/skills/vault/tools.py:204`.
- `tool_vault_rename` — refuses at `src/decafclaw/skills/vault/tools.py:248` and `:251`.

All three use the helper `_is_in_agent_dir(config, path)` (`tools.py:114`).

`SKILL.md` already tells the LLM "only write outside `agent/` when the user explicitly asks" (`src/decafclaw/skills/vault/SKILL.md:18`), but the runtime ignores that affordance. This session aligns runtime with the documented policy.

The codebase already has the confirmation infrastructure we need:

- `request_confirmation(ctx, tool_name, command, message, timeout=60, **extra)` wrapper at `src/decafclaw/tools/confirmation.py:106` — async-await primitive. Returns a dict with at least `{"approved": bool}`. Routes through ConversationManager when available (persisted, per-conversation scoped); falls back to legacy event-bus path otherwise. Persistence: request/response pair archives as `confirmation_request` / `confirmation_response` messages — survives page reload + server restart.
- `send_email` (`src/decafclaw/tools/email_tools.py:133-158`) is the closest precedent: build allowlist → if all recipients allowed return `{"approved": True}`; otherwise call `await request_confirmation(ctx, tool_name=..., command=..., message=...)` and use the dict result.
- Per-tool short-circuit at `confirmation.py:124-126`: if `tool_name in ctx.tools.preapproved`, the wrapper returns approved without prompting. We don't need to add this check ourselves — it's inside `request_confirmation()`.
- `EndTurnConfirm` (`src/decafclaw/media.py:18-33`) is a separate "review gate" pattern where the tool returns *"I asked for review"* as the result. **Not what we want here** — the LLM cares about the write outcome, not "I asked." We use the email pattern.

See `research.md` for full file:line refs.

## Desired end state

### Behavior

For `vault_write`, `vault_delete`, `vault_rename`, when the resolved target path is **outside** `config.vault_agent_dir`:

1. **Path matches `vault.user_writable_paths`** (static config allowlist, prefix match against vault-relative path) → execute directly, no confirmation. Mirrors email's allowlist short-circuit.
2. **Path is under a folder granted in this conversation** (in-memory grant set, persisted to a per-conversation sidecar) → execute directly.
3. **Otherwise** → `await request_confirmation(ctx, tool_name="vault_write", command=..., message=...)` with a preview of the operation. The tool blocks until the user responds. On `{"approved": True}`: execute the op and return the normal success message. On `{"approved": False}`: return `ToolResult(text="[error: vault_<op> to '<page>' was denied by user]")`. Mirrors `send_email` (`email_tools.py:166-219`).

**Non-interactive contexts (heartbeat / scheduled / child agent):** `request_confirmation()` documents (`confirmation.py:130-141`) that calls from non-ConversationManager contexts fall back to a legacy event-bus path the comment flags as "unexpected." For the vault gate, we short-circuit explicitly: if `ctx.request_confirmation is None`, return `ToolResult(text="[error: vault_<op> outside agent folder requires interactive confirmation; not available from this context]")` instead of calling the wrapper. This keeps scheduled tasks / heartbeats from blocking on a UI that isn't there. Same short-circuit for `vault_grant_folder`.

### New tool: `vault_grant_folder(folder, reason)`

- Always-loaded under the vault skill (the skill itself is `always-loaded: true`).
- `folder` parameter: vault-relative folder path. Same path-safety as `_safe_write_path` (no `..`, no absolute prefixes; resolved must stay under `vault_root`). Reject if the folder resolves inside `agent/` (no grant needed) or escapes the vault.
- Calls `await request_confirmation(ctx, tool_name="vault_grant_folder", command=f"trust folder '{folder}'", message=...)` with a preview like:
  ```
  Grant vault write/delete/rename trust for folder 'creative/in-progress/fungal-world/' for the rest of this conversation?

  Reason: <agent-supplied reason>
  ```
- On approve: append the folder to the conversation grant sidecar (atomic write, deduped); return `ToolResult(text="Folder 'X' trusted for this conversation.")`.
- On deny: return `ToolResult(text="[error: folder grant for '<folder>' was denied by user]")`.
- Tool description teaches the agent: "Use this **before** a batch operation on user-owned folders to avoid per-page confirmations. For one-off writes, the regular vault_write/delete/rename tools handle confirmation per-call."

### Config

Add to `VaultConfig` at `src/decafclaw/config_types.py:203`:

```python
user_writable_paths: list[str] = field(default_factory=list)
```

Path semantics: vault-relative folder paths. Prefix match — if `user_writable_paths = ["creative/", "notes/"]`, then any page whose vault-relative path starts with one of those prefixes is auto-approved. Both ends of the comparison are normalized: leading `/` stripped, trailing `/` enforced, `..` rejected (skip the entry with a warning at config-load time rather than fail). An entry like `creative` matches `creative/` and everything under it. Empty by default — opt-in.

Documented in `docs/vault.md` and `docs/config.md` (the "Vault" config section).

### Per-conversation grant persistence

Sidecar at `{config.workspace_path}/conversations/{conv_id}.vault_grants.json` (same convention and safety checks as `_canvas_sidecar_path` in `src/decafclaw/canvas.py:41-50`):

```json
{"folders": ["creative/in-progress/fungal-world/"]}
```

- `conv_id` comes from `ctx.conv_id` (set on every Context fork; see `src/decafclaw/context.py:78`).
- Created on first grant; rewritten on each grant (atomic write, dedup on append).
- Per-conversation scope — does not leak across conversations.
- Survives page reload and server restart (the grant tool writes the sidecar after `await request_confirmation()` returns approved; the gate functions read it on each call).
- Clearable by deleting the file (manual user action; not exposed as a tool in v1 — see "What we're NOT doing").
- If `ctx.conv_id` is empty (rare — non-conversation contexts), the grant path is unavailable; falls through as if no grants exist.

### Tool description and SKILL.md updates

- **`vault_write` / `vault_delete` / `vault_rename` descriptions** in `tools.py` `TOOL_DEFINITIONS`: replace the "admin and user pages are off-limits" / "Agent-owned pages only" sentences with: "Pages outside the agent folder require user confirmation per call. For batch operations, request folder trust via `vault_grant_folder` first." Apply consistent wording across all three.
- **New tool entry** in `TOOL_DEFINITIONS` for `vault_grant_folder` with `folder` (string, required, vault-relative folder path) and `reason` (string, required, short user-facing explanation).
- **`SKILL.md` "Boundaries" section** (the bulleted list before "Editing Sections") needs two edits:
  - The `vault_delete` bullet ("Only for pages you own (under `agent/`) ...") — generalize to "Only delete pages that are definitively wrong, duplicate, or no longer reachable. For pages outside `agent/`, the deletion will trigger a user confirmation."
  - The "user files are readable but not yours to modify autonomously" bullet — replace with: "User pages outside `agent/` are writable on explicit user request. Each write/delete/rename triggers a user confirmation; for batches, call `vault_grant_folder` first to trust a folder for the rest of the conversation."
- **`SKILL.md` "Your Home Folder" section** ("Write to `agent/` by default. ... only write outside `agent/` when the user explicitly asks.") — keep as-is. This affordance is already correct; the runtime now honors it.

### Confirmation message formats

Mirror the email_tools preview style (`email_tools.py:116-130`):

- **vault_write:**
  ```
  Vault write to: creative/foo/bar.md
  <"(overwrites existing page)" if file exists, else "(new page)">
  Content: 1.4 KB
  ---
  <first 200 chars of content>
  ```
- **vault_delete:**
  ```
  Vault delete: creative/foo/bar.md
  (cannot be undone — page and embeddings will be removed)
  ```
- **vault_rename:**
  ```
  Vault rename: creative/foo/bar.md → creative/foo/baz.md
  ```

## Design decisions

- **Decision:** Loosen all three of `vault_write`, `vault_delete`, `vault_rename` symmetrically.
  - **Why:** Same boundary, same code path (`_is_in_agent_dir`). User wants to "work in my area" — that includes moving and removing pages, not just creating.
  - **Rejected:** "Loosen `write` only." Would leave the agent unable to fully manage user pages, defeating the use case.

- **Decision:** Static config allowlist (`vault.user_writable_paths`) + per-conversation folder grant tool, both feeding the same gate logic.
  - **Why:** Allowlist handles stable trust ("I always let the agent edit creative/"); per-conversation grant handles ad-hoc batches without permanent config edits. Each addresses a different friction surface.
  - **Rejected:** Pure per-call confirmation. High friction for batch ops. Pure config allowlist. Forces premature decision about which folders are "trusted" globally; doesn't help one-off batch sessions.

- **Decision:** Per-conversation grants live in `{workspace}/conversations/{conv_id}.vault_grants.json` sidecar.
  - **Why:** Matches the codebase convention for per-conversation state (`canvas`, `notes`, `decisions`). Crash-recoverable, debuggable, clearable. No new schema in conversation archive.
  - **Rejected:** Replay from `confirmation_response` archive entries. Adds coupling between confirmation infrastructure and vault gate logic; harder to inspect or clear.

- **Decision:** Separate tool `vault_grant_folder(folder, reason)` for batch trust, distinct from `vault_write/delete/rename`.
  - **Why:** Granting trust is its own deliberate action — keeping it separate makes the user-facing confirmation message clear and avoids overloading existing tool params with mode flags.
  - **Rejected:** Boolean `grant_folder` param on each write tool. Conflates two semantics in one parameter; tool description gets harder to write.

- **Decision:** Allowlist matching is prefix-based on vault-relative paths (folder paths only, no globs).
  - **Why:** Simplest model; matches the user's mental model of "trust this folder." Globs are a rabbit hole (escaping, performance, edge cases) and the use case doesn't need them.
  - **Rejected:** Glob/fnmatch matching. Adds complexity without clear demand.

- **Decision:** Admin folder concerns (SOUL.md, AGENT.md, etc.) are **out of scope** because admin lives at `data/{agent_id}/`, not in the vault. Vault gate doesn't touch admin files.
  - **Why:** Confirmed in `research.md` — admin is a separate filesystem location with its own access path. The vault has `agent/` and "everything else" (user pages); the gate is binary.

## Patterns to follow

- **Confirmation fallthrough:** `check_email_approval` at `src/decafclaw/tools/email_tools.py:133-158`. Allowlist short-circuit, then `await request_confirmation(...)`, then denied-message return on `{"approved": False}`. **This is the load-bearing pattern** — copy the shape.
- **Confirmation preview message:** `_format_confirmation_message` at `src/decafclaw/tools/email_tools.py:116-130`.
- **Persisted confirmations:** `src/decafclaw/confirmations.py:24-90`. Survives reload via archive replay — we don't need to extend this; existing flow handles us automatically when we use `request_confirmation()`.
- **Per-conversation sidecar:** `src/decafclaw/notes.py` and `src/decafclaw/canvas.py` (especially `_canvas_sidecar_path` at `canvas.py:41-50`) — `{config.workspace_path}/conversations/{conv_id}.<kind>.json` filename, with directory-traversal guards on `conv_id`.
- **Tool description as control surface:** CLAUDE.md "Tools" section — wording changes ("MUST", "NEVER") measurably change LLM behavior. Run `make eval-tools` if it covers vault.

## What we're NOT doing

- **No `vault_revoke_folder` tool.** Grants reset between conversations; manual sidecar deletion is the escape hatch. Don't ship a tool for a need that hasn't been demonstrated.
- **No glob/regex matching in `user_writable_paths`.** Prefix match only.
- **No persistent grants across conversations.** That's what the static config is for.
- **No "always trust" UI button on the per-call confirmation.** The grant tool is the way to expand scope; we're not extending the confirmation UI to add a third action.
- **No changes to `vault_journal_append` or read-only vault tools.** The boundary is unchanged for those — journal stays in `agent/`, reads are unrestricted.
- **No admin folder integration.** Admin files live outside the vault; this session doesn't touch their access controls.
- **No special-case for admin-style files inside the vault** (e.g., a hypothetical `vault/admin/`). If someone creates one, it's just another user folder under this design.
- **No bulk migration / cleanup of existing tool descriptions for unrelated vault tools.** Touch only the three changed tools and the new grant tool.

## Open questions

- **Should the confirmation auto-skip for very small files (e.g., empty stub pages)?** Default answer: **no.** Confirmation is cheap; auto-skipping by size adds policy surface. Reconsider if friction shows up.
- **Does the grant tool need a TTL within the conversation (e.g., expires after N writes)?** Default answer: **no.** Conversation lifetime is the natural scope; the user can always start a new conversation to reset.
