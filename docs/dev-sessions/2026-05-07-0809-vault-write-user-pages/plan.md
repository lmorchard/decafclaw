# Vault user-page writes — Implementation Plan

**Goal:** Replace the hard-refuse on vault writes outside `agent/` with a three-tier gate: static config allowlist → per-conversation folder grants → user confirmation. Add a `vault_grant_folder` tool for batch-trust grants.

**Approach:** Build the gate helper + grant sidecar first (Phase 1), then wire each of `vault_write`/`vault_delete`/`vault_rename` to use it (Phases 2–4), then ship the grant tool plus user-facing docs (Phase 5). Each phase mirrors the `send_email` allowlist+confirmation precedent at `src/decafclaw/tools/email_tools.py:133-219`.

**Tech stack:** Python 3, dataclasses, `asyncio`, `pytest` + `unittest.mock`, JSON sidecars.

---

## Phase 1: Gate helper, config field, grant sidecar

Foundation slice. Adds the config field, the per-conversation grant sidecar I/O, and the `_check_user_write_allowed` helper. **No tool wiring yet** — this phase is purely additive plumbing with full unit-test coverage.

**Files:**
- Modify: `src/decafclaw/config_types.py` — add `user_writable_paths: list[str] = field(default_factory=list)` to `VaultConfig` (line 203). Use `field(default_factory=list)`.
- Create: `src/decafclaw/skills/vault/_grants.py` — sidecar I/O + path normalization helpers.
- Modify: `src/decafclaw/skills/vault/tools.py` — add `_check_user_write_allowed(ctx, path) -> GateOutcome` helper, the `GateOutcome` enum, `_is_in_user_writable_paths(config, path)`, and `_normalize_allowlist_entry(entry)` near the existing path helpers (around `tools.py:114`, after `_is_in_agent_dir`).
- Create: `tests/test_vault_grants.py` — unit tests for `_grants.py` (read/write/dedup, path safety, atomic write).
- Modify: `tests/test_vault_tools.py` — add `TestCheckUserWriteAllowed` class testing the gate logic against allowlist, grants, agent dir, non-interactive context. Reuse existing `ctx` and `vault_dir` fixtures; extend with `conv_id` where needed.
- Modify: `docs/config.md` — add `vault.user_writable_paths` row to the Vault config section.

**Key changes:**

```python
# src/decafclaw/skills/vault/_grants.py
"""Per-conversation vault folder grants — sidecar persistence.

Each conversation that has been granted folder trust gets a JSON sidecar at
{workspace}/conversations/{conv_id}.vault_grants.json. Stored as a sorted,
deduped list of folder paths (vault-relative, trailing slash enforced).
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _grants_sidecar_path(config, conv_id: str) -> Path:
    """Path to the grants JSON sidecar; guarded against directory traversal.

    Mirrors `_canvas_sidecar_path` in src/decafclaw/canvas.py:41-50.
    """
    base_dir = (config.workspace_path / "conversations").resolve()
    if not conv_id or "/" in conv_id or "\\" in conv_id or ".." in conv_id:
        return base_dir / "_invalid.vault_grants.json"
    path = (base_dir / f"{conv_id}.vault_grants.json").resolve()
    if not path.is_relative_to(base_dir):
        return base_dir / "_invalid.vault_grants.json"
    return path


def read_grants(config, conv_id: str) -> set[str]:
    """Read the grant set; fail-open with empty set on any error."""
    if not conv_id:
        return set()
    path = _grants_sidecar_path(config, conv_id)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        folders = data.get("folders", [])
        # Filter out invalid (normalized to "") entries — they would act as
        # a wildcard prefix in is_path_in_grants and let everything through.
        result: set[str] = set()
        for f in folders:
            if isinstance(f, str):
                norm = _normalize(f)
                if norm:
                    result.add(norm)
        return result
    except (json.JSONDecodeError, OSError):
        log.warning("Failed to read vault grants for %s; treating as empty",
                    conv_id, exc_info=True)
        return set()


def add_grant(config, conv_id: str, folder: str) -> bool:
    """Append a folder grant atomically. Returns True on success."""
    if not conv_id:
        return False
    folder = _normalize(folder)
    if not folder:
        return False
    grants = read_grants(config, conv_id)
    grants.add(folder)
    path = _grants_sidecar_path(config, conv_id)
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"folders": sorted(grants)}
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        tmp.replace(path)
        return True
    except OSError:
        log.warning("Failed to write vault grants for %s", conv_id, exc_info=True)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError as cleanup_exc:
            log.debug("vault_grants tmp cleanup failed: %s", cleanup_exc)
        return False


def _normalize(folder: str) -> str:
    """Normalize a folder path: strip leading /, enforce trailing /, reject ..

    Returns "" for invalid inputs (caller should treat as no-grant).
    """
    if not isinstance(folder, str):
        return ""
    folder = folder.strip()
    if not folder or ".." in folder:
        return ""
    if folder.startswith("/"):
        folder = folder.lstrip("/")
    if not folder.endswith("/"):
        folder = folder + "/"
    return folder


def is_path_in_grants(config, conv_id: str, path: Path) -> bool:
    """Check if a vault-relative path is under any granted folder."""
    grants = read_grants(config, conv_id)
    if not grants:
        return False
    try:
        rel = str(path.resolve().relative_to(config.vault_root.resolve()))
    except (ValueError, OSError):
        return False
    rel = rel.replace("\\", "/")
    return any(rel.startswith(g) for g in grants)
```

```python
# src/decafclaw/skills/vault/tools.py — added near _is_in_agent_dir (line ~114)
from enum import Enum

class GateOutcome(Enum):
    ALLOW = "allow"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NON_INTERACTIVE_ERROR = "non_interactive_error"


def _is_in_user_writable_paths(config, path: Path) -> bool:
    """Check if a vault-relative path is under any configured allowlist entry."""
    paths = getattr(config.vault, "user_writable_paths", []) or []
    if not paths:
        return False
    try:
        rel = str(path.resolve().relative_to(_vault_root(config).resolve()))
    except (ValueError, OSError):
        return False
    rel = rel.replace("\\", "/")
    for entry in paths:
        norm = _normalize_allowlist_entry(entry)
        if norm and rel.startswith(norm):
            return True
    return False


def _normalize_allowlist_entry(entry: str) -> str:
    """Normalize an allowlist entry; return '' for invalid (caller skips)."""
    if not isinstance(entry, str):
        return ""
    e = entry.strip()
    if not e or ".." in e:
        if e:
            log.warning("Skipping invalid vault.user_writable_paths entry: %r", entry)
        return ""
    if e.startswith("/"):
        e = e.lstrip("/")
    if not e.endswith("/"):
        e = e + "/"
    return e


def _check_user_write_allowed(ctx, path: Path) -> GateOutcome:
    """Decide how to handle a vault write/delete/rename for the given path.

    Returns:
        GateOutcome.ALLOW — write directly (path is in agent/, allowlist, or grants)
        GateOutcome.NEEDS_CONFIRMATION — caller must call request_confirmation()
        GateOutcome.NON_INTERACTIVE_ERROR — no UI available; caller returns error
    """
    if _is_in_agent_dir(ctx.config, path):
        return GateOutcome.ALLOW
    if _is_in_user_writable_paths(ctx.config, path):
        return GateOutcome.ALLOW
    from decafclaw.skills.vault._grants import is_path_in_grants
    if is_path_in_grants(ctx.config, ctx.conv_id, path):
        return GateOutcome.ALLOW
    if ctx.request_confirmation is None:
        return GateOutcome.NON_INTERACTIVE_ERROR
    return GateOutcome.NEEDS_CONFIRMATION
```

**Test outline (TDD — write these tests first; they should fail before the helpers exist):**

```python
# tests/test_vault_grants.py — new file
class TestNormalizeFolder:
    def test_strips_leading_slash(self): ...
    def test_enforces_trailing_slash(self): ...
    def test_rejects_dotdot(self): ...
    def test_rejects_empty(self): ...

class TestGrantsSidecarPath:
    def test_rejects_path_traversal_in_conv_id(self, config): ...
    def test_returns_invalid_path_for_empty_conv_id(self, config): ...

class TestReadAddGrants:
    def test_empty_when_no_sidecar(self, config): ...
    def test_add_then_read_roundtrip(self, config, tmp_path): ...
    def test_add_dedup(self, config): ...
    def test_corrupt_sidecar_treated_as_empty(self, config): ...
    def test_atomic_write_no_partial_on_failure(self, config): ...

class TestIsPathInGrants:
    def test_path_under_granted_folder(self, config): ...
    def test_path_not_under_any_grant(self, config): ...
    def test_no_grants_returns_false(self, config): ...

# tests/test_vault_tools.py — new class
class TestCheckUserWriteAllowed:
    async def test_agent_dir_allows(self, ctx, agent_pages): ...
    async def test_allowlist_allows(self, ctx, vault_dir):
        # Configure ctx.config.vault.user_writable_paths = ["creative/"]
        # Expect ALLOW for creative/foo.md
        ...
    async def test_grants_allow(self, ctx, vault_dir):
        # Pre-populate sidecar with "creative/foo/"
        # Expect ALLOW for creative/foo/bar.md
        ...
    async def test_outside_agent_with_request_conf_yields_needs_confirmation(self, ctx, vault_dir): ...
    async def test_no_request_conf_yields_non_interactive_error(self, ctx, vault_dir):
        # ctx.request_confirmation = None
        ...
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (2356 passed)
- [x] `make check` passes
- [x] `pytest tests/test_vault_grants.py -v` — all new tests green
- [x] `pytest tests/test_vault_tools.py::TestCheckUserWriteAllowed -v` — all green

**Verification — manual:**
- [ ] `make config` output includes `vault.user_writable_paths: []` (resolved config dump shows the new field). _Deferred to end-of-feature manual smoke._

---

## Phase 2: Wire `vault_write` to use the gate

Replaces the hard-refuse at `src/decafclaw/skills/vault/tools.py:171` with the three-tier check from Phase 1. After this phase, `vault_write` to user pages works (with confirmation) and the `vault_write` tool description teaches the LLM the new affordance.

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py` —
  - Replace lines 171-174 (the hard-refuse block in `tool_vault_write`) with the gate-driven flow.
  - Add `_format_vault_write_preview(config, path, content, exists)` near the existing helpers.
  - Update the `vault_write` entry in `TOOL_DEFINITIONS` (lines 833-844): replace the "Writes are restricted ..." sentence.
  - Add module-level import: `from decafclaw.tools.confirmation import request_confirmation`.
- Modify: `tests/test_vault_tools.py` — extend `TestVaultWrite` class with new tests for the gate paths.

**Key changes:**

```python
# src/decafclaw/skills/vault/tools.py — tool_vault_write rewritten gate logic
async def tool_vault_write(ctx, page: str, content: str) -> str | ToolResult:
    log.info(f"[tool:vault_write] page={page}")
    path = _safe_write_path(ctx.config, page)
    if path is None:
        return ToolResult(
            text=f"[error: invalid page name '{page}' — must be within vault directory]")
    if not content or not content.strip():
        return ToolResult(text=f"[error: refusing to write empty vault page '{page}']")

    outcome = _check_user_write_allowed(ctx, path)
    if outcome == GateOutcome.NON_INTERACTIVE_ERROR:
        return ToolResult(
            text=f"[error: vault_write to '{page}' outside agent folder "
                 f"requires interactive confirmation; not available from this context]")
    if outcome == GateOutcome.NEEDS_CONFIRMATION:
        msg = _format_vault_write_preview(ctx.config, path, content, path.exists())
        approval = await request_confirmation(
            ctx, tool_name="vault_write",
            command=f"vault_write to '{page}'",
            message=msg,
        )
        if not approval.get("approved"):
            return ToolResult(text=f"[error: vault_write to '{page}' was denied by user]")
    # outcome == ALLOW or approved confirmation — proceed
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    # ... existing indexing block unchanged ...
    return f"Vault page '{page}' saved."


def _format_vault_write_preview(config, path: Path, content: str, exists: bool) -> str:
    """Confirmation preview for vault_write. Mirrors _format_confirmation_message."""
    try:
        rel = str(path.resolve().relative_to(config.vault_root.resolve()))
    except ValueError:
        rel = path.name
    state = "(overwrites existing page)" if exists else "(new page)"
    size_kb = len(content.encode("utf-8")) / 1024
    preview = content[:200] + ("..." if len(content) > 200 else "")
    return "\n".join([
        f"Vault write to: {rel}",
        state,
        f"Content: {size_kb:.1f} KB",
        "---",
        preview,
    ])
```

```python
# TOOL_DEFINITIONS entry for vault_write — replace the "Writes are restricted ..." sentence
"description": (
    "Create or overwrite a vault page (knowledge base). ONLY for "
    "vault knowledge pages — NOT for workspace files, blog posts, "
    "code, configs, or any file in a project directory. Use "
    "workspace_write for those. ALWAYS vault_read first if "
    "updating an existing page to preserve content you want to keep. "
    "Use [[Page Name]] syntax to link to other pages. Include a "
    "## Sources section. Pages outside the agent folder (agent/pages/, "
    "agent/journal/) require user confirmation per call. For batch "
    "operations on user folders, request folder trust via "
    "vault_grant_folder first."
),
```

**Test outline:**
```python
# tests/test_vault_tools.py — extend TestVaultWrite
class TestVaultWriteUserFolders:
    async def test_allowlist_bypass_writes_directly(self, ctx, vault_dir):
        ctx.config.vault.user_writable_paths = ["creative/"]
        result = await tool_vault_write(ctx, "creative/foo", "content")
        assert "saved" in str(result)
        assert (vault_dir / "creative" / "foo.md").exists()

    async def test_grant_bypass_writes_directly(self, ctx, vault_dir):
        from decafclaw.skills.vault._grants import add_grant
        ctx.conv_id = "conv-123"
        add_grant(ctx.config, "conv-123", "creative/")
        result = await tool_vault_write(ctx, "creative/foo", "content")
        assert "saved" in str(result)

    async def test_no_allowlist_or_grant_confirms_then_writes(self, ctx, vault_dir):
        with patch("decafclaw.skills.vault.tools.request_confirmation",
                   AsyncMock(return_value={"approved": True})):
            result = await tool_vault_write(ctx, "creative/foo", "content")
        assert "saved" in str(result)

    async def test_denied_returns_error_no_write(self, ctx, vault_dir):
        with patch("decafclaw.skills.vault.tools.request_confirmation",
                   AsyncMock(return_value={"approved": False})):
            result = await tool_vault_write(ctx, "creative/foo", "content")
        assert "denied by user" in str(result.text)
        assert not (vault_dir / "creative" / "foo.md").exists()

    async def test_non_interactive_context_errors(self, ctx, vault_dir):
        ctx.request_confirmation = None
        result = await tool_vault_write(ctx, "creative/foo", "content")
        assert "interactive confirmation" in str(result.text)

    async def test_existing_test_rejects_outside_still_relevant(self, ...):
        # The original test_rejects_write_outside_agent_folder at line 149
        # MUST be updated — outside-agent no longer rejects unconditionally.
        # Replace with: agent_dir writes still work, no confirmation prompt.
```

**Critical:** the original test `test_rejects_write_outside_agent_folder` at `tests/test_vault_tools.py:149` asserts the old behavior and will fail under the new design. Rewrite it (don't delete) to assert the new contract:
- Write outside agent dir, no confirmation handler → error message about "interactive confirmation".
- Write outside agent dir, mocked approve → succeeds.

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (2362 passed; rewritten `test_rejects_write_outside_agent_folder` green)
- [x] `make check` passes
- [x] `pytest tests/test_vault_tools.py::TestVaultWriteUserFolders -v` — 6 new tests green
- [ ] `make eval-tools 2>&1 | grep -A2 vault_write` — _deferred to end-of-feature; vault tools may not have an eval. Report at retro._

**Verification — manual:**
- [ ] In a live `make dev` session: ask the agent to write to a page like `creative/test.md` — confirmation should appear in the web UI with the path, size, and content preview. Approve → write succeeds. _Deferred to end-of-feature manual smoke._
- [ ] Same flow, click Deny → tool returns the denied error and no file appears in the vault. _Deferred._
- [ ] With `vault.user_writable_paths: ["test/"]` in `data/{agent_id}/config.json`, ask agent to write `test/foo.md` — no confirmation prompted, write happens. _Deferred._

---

## Phase 3: Wire `vault_delete` to use the gate

Same shape as Phase 2, applied to `tool_vault_delete` at `src/decafclaw/skills/vault/tools.py:195`. Adds delete-specific preview and updates the tool description.

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py` —
  - Replace lines 204-206 (the hard-refuse in `tool_vault_delete`) with gate-driven flow.
  - Add `_format_vault_delete_preview(config, path)`.
  - Update the `vault_delete` entry in `TOOL_DEFINITIONS` (lines 868-876).
- Modify: `tests/test_vault_tools.py` — add a `TestVaultDeleteUserFolders` class parallel to Phase 2's `TestVaultWriteUserFolders`. Rewrite the existing `test_rejects_page_outside_agent_dir` at line 202 to assert the new contract.

**Key changes:**

```python
# tool_vault_delete after the path-exists check:
outcome = _check_user_write_allowed(ctx, path)
if outcome == GateOutcome.NON_INTERACTIVE_ERROR:
    return ToolResult(
        text=f"[error: vault_delete of '{page}' outside agent folder "
             f"requires interactive confirmation; not available from this context]")
if outcome == GateOutcome.NEEDS_CONFIRMATION:
    msg = _format_vault_delete_preview(ctx.config, path)
    approval = await request_confirmation(
        ctx, tool_name="vault_delete",
        command=f"vault_delete '{page}'",
        message=msg,
    )
    if not approval.get("approved"):
        return ToolResult(text=f"[error: vault_delete of '{page}' was denied by user]")
# proceed with delete (existing logic at lines 208-228 unchanged)


def _format_vault_delete_preview(config, path: Path) -> str:
    try:
        rel = str(path.resolve().relative_to(config.vault_root.resolve()))
    except ValueError:
        rel = path.name
    return (
        f"Vault delete: {rel}\n"
        f"(cannot be undone — page and embeddings will be removed)"
    )
```

```python
# vault_delete TOOL_DEFINITIONS description
"description": (
    "DESTRUCTIVE — permanently delete a vault page and its embedding "
    "entries. Pages outside the agent folder (agent/pages/, "
    "agent/journal/) require user confirmation per call. For batch "
    "operations, request folder trust via vault_grant_folder first. "
    "Prefer vault_write with updated content to retire or mark pages "
    "superseded; use delete only when the page is definitively wrong, "
    "duplicate, or no longer reachable."
),
```

**Test outline:** identical structure to Phase 2's `TestVaultWriteUserFolders`, adapted for delete (e.g., pre-create file in `creative/foo.md`, assert it's removed on approve / preserved on deny).

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (2368 passed; rewritten `test_rejects_page_outside_agent_dir` green)
- [x] `make check` passes
- [x] `pytest tests/test_vault_tools.py::TestVaultDeleteUserFolders -v` — 6 new tests green

**Verification — manual:**
- [ ] In `make dev`: have agent delete a user-folder page → confirmation message clearly says "(cannot be undone)". Approve → page gone. Deny → page preserved. _Deferred to end-of-feature manual smoke._

---

## Phase 4: Wire `vault_rename` to use the gate

`vault_rename` has TWO paths to gate (old_path + new_path). The single confirmation message must cover both. If either path requires confirmation, the operation requires confirmation.

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py` —
  - Replace lines 248-253 (the two hard-refuse blocks in `tool_vault_rename`) with combined gate check.
  - Add `_format_vault_rename_preview(config, old_path, new_path)`.
  - Update the `vault_rename` entry in `TOOL_DEFINITIONS` (lines 894-902).
- Modify: `tests/test_vault_tools.py` — add a `TestVaultRenameUserFolders` class. Rewrite the existing `test_rejects_rename_outside_agent_dir` at line 293.

**Key changes:**

```python
# In tool_vault_rename after both paths are resolved + existence checks pass:
old_outcome = _check_user_write_allowed(ctx, old_path)
new_outcome = _check_user_write_allowed(ctx, new_path)

# If EITHER path is non-interactive-error, the whole op fails.
if (old_outcome == GateOutcome.NON_INTERACTIVE_ERROR
        or new_outcome == GateOutcome.NON_INTERACTIVE_ERROR):
    return ToolResult(
        text=f"[error: vault_rename '{page}' -> '{rename_to}' touches a path "
             f"outside the agent folder and requires interactive confirmation; "
             f"not available from this context]")

# If EITHER path needs confirmation (and neither is non-interactive), confirm.
if (old_outcome == GateOutcome.NEEDS_CONFIRMATION
        or new_outcome == GateOutcome.NEEDS_CONFIRMATION):
    msg = _format_vault_rename_preview(ctx.config, old_path, new_path)
    approval = await request_confirmation(
        ctx, tool_name="vault_rename",
        command=f"vault_rename '{page}' -> '{rename_to}'",
        message=msg,
    )
    if not approval.get("approved"):
        return ToolResult(
            text=f"[error: vault_rename '{page}' -> '{rename_to}' was denied by user]")
# proceed with rename (existing logic at lines 255-285 unchanged)


def _format_vault_rename_preview(config, old_path: Path, new_path: Path) -> str:
    def _rel(p):
        try:
            return str(p.resolve().relative_to(config.vault_root.resolve()))
        except ValueError:
            return p.name
    return f"Vault rename: {_rel(old_path)} → {_rel(new_path)}"
```

```python
# vault_rename TOOL_DEFINITIONS description
"description": (
    "Rename or move a vault page. Updates the embedding index so "
    "search results stay consistent. Pages outside the agent folder "
    "(agent/pages/, agent/journal/) require user confirmation per "
    "call. For batch operations, request folder trust via "
    "vault_grant_folder first. Use this to reorganize or refine page "
    "names — prefer it over delete + rewrite when the content stays "
    "the same. Target must not already exist."
),
```

**Test outline:**
```python
class TestVaultRenameUserFolders:
    async def test_old_in_agent_new_in_user_confirms(self, ctx, agent_pages):
        # Rename agent/pages/X to creative/X — confirms (because new_path is outside)
        ...
    async def test_both_in_user_confirms_once(self, ctx, vault_dir):
        # Rename creative/A to creative/B — single confirmation
        ...
    async def test_both_in_user_with_grant_no_confirm(self, ctx, vault_dir):
        # Pre-grant "creative/" — rename within creative/ no confirmation
        ...
    async def test_old_in_grant_new_outside_grant_confirms(self, ctx, vault_dir):
        # Grant covers creative/ but rename target is notes/X — needs confirmation
        ...
    async def test_denied_no_rename(self, ctx, vault_dir):
        # Deny → original file remains, target doesn't exist
        ...
    async def test_non_interactive_errors(self, ctx, vault_dir):
        ctx.request_confirmation = None
        ...
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (2375 passed)
- [x] `make check` passes
- [x] `pytest tests/test_vault_tools.py::TestVaultRenameUserFolders -v` green (7 tests)

**Verification — manual:**
- [ ] In `make dev`: rename a user page within `creative/` — single confirmation. Approve → renamed. _Deferred to end-of-feature manual smoke._
- [ ] Rename across folder boundary (e.g., `creative/X` → `notes/X`) — single confirmation showing both paths. Approve → renamed. _Deferred._

---

## Phase 5: `vault_grant_folder` tool + SKILL.md + user docs

New tool that requests a per-conversation folder trust grant. After approval, future writes/deletes/renames under the folder skip the per-call confirmation. Also rewrites the SKILL.md "Boundaries" section and updates `docs/vault.md` to document the new permission model.

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py` —
  - Add `tool_vault_grant_folder(ctx, folder, reason)`.
  - Register in the `TOOLS` dict (line ~789): `"vault_grant_folder": tool_vault_grant_folder`.
  - Add `vault_grant_folder` entry to `TOOL_DEFINITIONS`.
- Modify: `src/decafclaw/skills/vault/SKILL.md` —
  - Rewrite the `vault_delete` bullet in "Boundaries".
  - Replace the "user files are readable but not yours to modify autonomously" bullet.
  - Add a sentence about `vault_grant_folder` to the "Boundaries" section.
- Modify: `docs/vault.md` — add a section "Writing to user pages" describing the gate, allowlist, and grant tool.
- Modify: `tests/test_vault_tools.py` — add `TestVaultGrantFolder` class.

**Key changes:**

```python
# src/decafclaw/skills/vault/tools.py
async def tool_vault_grant_folder(ctx, folder: str, reason: str) -> ToolResult:
    """Request per-conversation trust for a vault folder."""
    log.info(f"[tool:vault_grant_folder] folder={folder!r}")

    # Path safety: reject .., absolute, must resolve under vault root.
    if not isinstance(folder, str) or not folder.strip():
        return ToolResult(text="[error: folder is required]")
    f = folder.strip()
    if ".." in f or f.startswith("/"):
        return ToolResult(text=f"[error: invalid folder '{folder}']")

    vault = _vault_root(ctx.config).resolve()
    candidate = (vault / f).resolve()
    if not candidate.is_relative_to(vault):
        return ToolResult(text=f"[error: folder '{folder}' escapes the vault]")

    # Reject if folder is inside agent/ (no grant needed)
    try:
        agent = _agent_dir(ctx.config).resolve()
        if candidate == agent or candidate.is_relative_to(agent):
            return ToolResult(
                text=f"[error: '{folder}' is inside the agent folder; no grant needed]")
    except (ValueError, OSError):
        pass

    if ctx.request_confirmation is None:
        return ToolResult(
            text=f"[error: vault_grant_folder requires interactive confirmation; "
                 f"not available from this context]")

    if not ctx.conv_id:
        return ToolResult(
            text="[error: vault_grant_folder requires a conversation context]")

    rel = str(candidate.relative_to(vault)).replace("\\", "/")
    if not rel.endswith("/"):
        rel = rel + "/"

    msg = (
        f"Grant vault write/delete/rename trust for folder '{rel}' "
        f"for the rest of this conversation?\n\nReason: {reason}"
    )
    approval = await request_confirmation(
        ctx, tool_name="vault_grant_folder",
        command=f"trust folder '{rel}'",
        message=msg,
    )
    if not approval.get("approved"):
        return ToolResult(text=f"[error: folder grant for '{rel}' was denied by user]")

    from decafclaw.skills.vault._grants import add_grant
    if not add_grant(ctx.config, ctx.conv_id, rel):
        return ToolResult(
            text=f"[error: failed to persist folder grant for '{rel}']")
    return ToolResult(text=f"Folder '{rel}' trusted for this conversation.")
```

```python
# TOOL_DEFINITIONS entry — add after vault_rename
{
    "type": "function",
    "function": {
        "name": "vault_grant_folder",
        "description": (
            "Request user trust for a vault folder for the rest of this "
            "conversation. Use this BEFORE a batch operation on user-owned "
            "folders (3+ writes/deletes/renames in the same area) to avoid "
            "per-page confirmations. After approval, vault_write, "
            "vault_delete, and vault_rename under the folder skip "
            "confirmation. For one-off operations, the regular tools handle "
            "confirmation per-call — don't call this for a single page."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": (
                        "Vault-relative folder path "
                        "(e.g. 'creative/in-progress/fungal-world')."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Short user-facing reason. Shown in the confirmation "
                        "message so the user knows why the grant is being "
                        "requested."
                    ),
                },
            },
            "required": ["folder", "reason"],
        },
    },
},
```

**SKILL.md edits — replace the two bullets in the "Boundaries" section:**

Original (around lines 50-52):
```
- `vault_delete` permanently removes a page. Only for pages you own (under `agent/`) that are definitively wrong, duplicate, or no longer reachable — prefer editing over deleting when the page can be salvaged.
- The user's files are readable but not yours to modify autonomously. Only edit user files when asked.
```

Replace with:
```
- `vault_delete` permanently removes a page. Only for pages that are definitively wrong, duplicate, or no longer reachable — prefer editing over deleting when the page can be salvaged. Pages outside `agent/` will trigger a user confirmation.
- User pages outside `agent/` are writable on explicit user request. Each `vault_write` / `vault_delete` / `vault_rename` triggers a user confirmation. For batch operations (3+ pages in the same folder), call `vault_grant_folder` first to trust the folder for the rest of the conversation and skip per-page confirmations.
```

**docs/vault.md addition:**

Add a section between existing sections (location: after the tool list, before the indexing notes — confirm placement when reading the file):

```markdown
## Writing to user pages

Writes/deletes/renames under the agent folder (`agent/`) execute directly. Operations on pages outside `agent/` go through a three-tier gate:

1. **Static allowlist.** Folders listed in `vault.user_writable_paths` are pre-approved. Path matching is prefix-based on vault-relative paths (no globs). Example: `["creative/", "notes/"]`.
2. **Per-conversation grants.** The agent can call `vault_grant_folder(folder, reason)` to request trust for a folder. After user approval, all writes/deletes/renames under that folder skip confirmation for the rest of the conversation. Grants persist as a sidecar at `{workspace}/conversations/{conv_id}.vault_grants.json` and reset between conversations.
3. **Per-call confirmation.** Anything else triggers a confirmation request showing the operation and a content preview. Approve to proceed; deny returns an error and no change is made.

Heartbeat / scheduled / child-agent contexts can't display confirmations, so writes outside the agent folder fail with an error in those contexts.
```

**Test outline:**

```python
class TestVaultGrantFolder:
    async def test_approves_and_persists(self, ctx, vault_dir):
        ctx.conv_id = "conv-X"
        with patch("decafclaw.skills.vault.tools.request_confirmation",
                   AsyncMock(return_value={"approved": True})):
            result = await tool_vault_grant_folder(ctx, "creative", "batch rename")
        assert "trusted" in str(result.text)
        from decafclaw.skills.vault._grants import read_grants
        assert "creative/" in read_grants(ctx.config, "conv-X")

    async def test_denied_returns_error_no_persist(self, ctx, vault_dir):
        ctx.conv_id = "conv-X"
        with patch("decafclaw.skills.vault.tools.request_confirmation",
                   AsyncMock(return_value={"approved": False})):
            result = await tool_vault_grant_folder(ctx, "creative", "...")
        assert "denied by user" in str(result.text)

    async def test_rejects_dotdot(self, ctx, vault_dir):
        result = await tool_vault_grant_folder(ctx, "../etc", "escape")
        assert "invalid folder" in str(result.text)

    async def test_rejects_absolute(self, ctx, vault_dir):
        result = await tool_vault_grant_folder(ctx, "/etc/passwd", "escape")
        assert "invalid folder" in str(result.text)

    async def test_rejects_inside_agent(self, ctx, vault_dir):
        result = await tool_vault_grant_folder(ctx, "agent/pages", "...")
        assert "no grant needed" in str(result.text)

    async def test_rejects_no_conv_id(self, ctx, vault_dir):
        ctx.conv_id = ""
        result = await tool_vault_grant_folder(ctx, "creative", "...")
        assert "conversation context" in str(result.text)

    async def test_rejects_non_interactive(self, ctx, vault_dir):
        ctx.request_confirmation = None
        result = await tool_vault_grant_folder(ctx, "creative", "...")
        assert "interactive confirmation" in str(result.text)

    async def test_grant_then_write_skips_confirmation(self, ctx, vault_dir):
        """Integration: grant a folder, then write — no second confirmation."""
        ctx.conv_id = "conv-X"
        with patch("decafclaw.skills.vault.tools.request_confirmation",
                   AsyncMock(return_value={"approved": True})) as mock_conf:
            await tool_vault_grant_folder(ctx, "creative", "batch")
        # Now write — should NOT call request_confirmation again
        with patch("decafclaw.skills.vault.tools.request_confirmation",
                   AsyncMock()) as mock_conf2:
            result = await tool_vault_write(ctx, "creative/foo", "content")
        mock_conf2.assert_not_called()
        assert "saved" in str(result)
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (2385 passed)
- [x] `make check` passes
- [x] `pytest tests/test_vault_tools.py::TestVaultGrantFolder -v` green (10 tests)
- [ ] `make eval-tools 2>&1 | grep -A2 vault_grant_folder` if a vault eval exists; report findings. _Deferred to end-of-feature; report at retro._

**Verification — manual:**
- [ ] `make dev`: ask agent to "rename three pages in `creative/foo/` to kebab-case." Expect: agent calls `vault_grant_folder("creative/foo", "...")` first → confirmation → approve. Then three `vault_rename` calls execute silently. _Deferred to end-of-feature manual smoke._
- [ ] Reload the page; the grant survives (the JSON sidecar exists at `{workspace}/conversations/{conv_id}.vault_grants.json` and the agent can still rename without re-confirmation). _Deferred._
- [ ] Open a NEW conversation and ask the agent to write to `creative/foo/X.md` — confirmation prompts again (grants don't leak across conversations). _Deferred._
- [ ] `cat docs/vault.md | grep -A2 "Writing to user pages"` — section is present and accurate.

---

## What this plan does NOT do

(Mirrors `spec.md`'s "What we're NOT doing" — for plan reviewer reference.)

- No `vault_revoke_folder` tool.
- No glob/regex matching in allowlist.
- No persistent grants across conversations.
- No "always trust" UI button on confirmations.
- No changes to `vault_journal_append` or read-only tools.
- No admin folder integration.
- No drive-by refactoring of vault tool internals.

## Plan self-review notes

- **Spec coverage:** every requirement in `spec.md` is covered:
  - Three-tier gate logic → Phase 1 (helper) + Phases 2–4 (per-tool wiring).
  - `vault_grant_folder` tool → Phase 5.
  - Static allowlist config → Phase 1 (config field), Phase 5 (docs).
  - Sidecar persistence → Phase 1 (`_grants.py`), Phase 5 (manual verification of reload).
  - Tool description updates → Phases 2, 3, 4, 5.
  - SKILL.md edits → Phase 5.
  - Docs updates → Phase 1 (`config.md`), Phase 5 (`vault.md`).
  - Non-interactive context handling → Phase 1 (gate returns `NON_INTERACTIVE_ERROR`), Phases 2–5 (each tool returns the error message).
  - Confirmation message formats → Phases 2, 3, 4 (per-op preview formatters).
- **Type consistency:** `GateOutcome` enum is defined in Phase 1 and used in Phases 2–4 with consistent member names. `_check_user_write_allowed`, `_format_vault_*_preview`, `request_confirmation` signatures match across phases.
- **No placeholders:** all phases have concrete code snippets, signatures, and tests. No "TBD" / "implement later" / "similar to phase N".
