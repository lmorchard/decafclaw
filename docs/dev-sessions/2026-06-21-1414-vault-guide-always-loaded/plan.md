# Always-loaded vault `AGENTS.md` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inject the vault's authored guide file (default `AGENTS.md` at the vault root) into every interactive turn's context as an always-loaded `system` section, so vault-layout/protocol rules are present *before* the model makes any tool decisions.

**Architecture:** A new synchronous `ContextComposer._compose_vault_guide(config, mode)` helper reads `<vault_root>/<path>` fresh each turn, wraps it in a `<vault_guide>` XML section, and returns `(text, SourceEntry|None)`. `compose()` adds its tokens to the fixed-token budget and inserts it as a `system` message immediately after the main system prompt, before the deferred-tools/preempt sections. Config lives in a new `VaultGuideConfig` dataclass wired onto `Config` via the standard `load_sub_config` path. Independent of `vault_retrieval` (no embeddings).

**Tech Stack:** Python 3, dataclasses, pytest (xdist). Repo conventions: `wrap_xml` from `decafclaw.prompts`, `estimate_tokens` from `decafclaw.util` (~4 chars/token), `load_sub_config` env resolution.

**Worktree:** `.claude/worktrees/592-vault-guide-always-loaded` (branch `592-vault-guide-always-loaded`, off `origin/main`, venv synced). Run all commands from there.

---

## File Structure

- `src/decafclaw/config_types.py` — add `VaultGuideConfig` dataclass (near `VaultRetrievalConfig`, ~line 237).
- `src/decafclaw/config.py` — add `vault_guide` field to `Config` (~line 178); add `load_sub_config` call (~line 448) and pass it into the `Config(...)` constructor (~line 540).
- `src/decafclaw/context_composer.py` — add `_compose_vault_guide` method; call it in `compose()` (add to `fixed_tokens` + `sources`, insert `system` message in the assembly block).
- `tests/test_config.py` — `VaultGuideConfig` defaults + env override.
- `tests/test_context_composer.py` — helper unit tests + `compose()` integration tests.
- `docs/context-composer.md`, `docs/vault.md` — doc updates.

---

## Task 1: `VaultGuideConfig` dataclass + Config wiring

**Files:**
- Modify: `src/decafclaw/config_types.py` (after `VaultRetrievalConfig`, ~line 254)
- Modify: `src/decafclaw/config.py` (`Config` field ~line 178; `load_sub_config` ~line 448; constructor ~line 540)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py` (inside the existing test class alongside `test_default_config`, or as new methods at module test-class level — match the file's style):

```python
    def test_vault_guide_defaults(self):
        """VaultGuideConfig defaults: enabled, AGENTS.md, 2000-token cap."""
        from decafclaw.config_types import VaultGuideConfig
        c = Config()
        assert isinstance(c.vault_guide, VaultGuideConfig)
        assert c.vault_guide.enabled is True
        assert c.vault_guide.path == "AGENTS.md"
        assert c.vault_guide.max_tokens == 2000

    def test_vault_guide_env_override(self, tmp_path, monkeypatch):
        """Env prefix VAULT_GUIDE_* overrides the guide config."""
        monkeypatch.setenv("VAULT_GUIDE_ENABLED", "false")
        monkeypatch.setenv("VAULT_GUIDE_PATH", "protocols/GUIDE.md")
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        c = load_config()
        assert c.vault_guide.enabled is False
        assert c.vault_guide.path == "protocols/GUIDE.md"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k vault_guide -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'vault_guide'` (and import error for `VaultGuideConfig`).

- [ ] **Step 3: Add the dataclass**

In `src/decafclaw/config_types.py`, immediately after the `VaultRetrievalConfig` dataclass (before `class RelevanceConfig`):

```python
@dataclass
class VaultGuideConfig:
    """Always-loaded vault guide (e.g. AGENTS.md).

    When a guide file is present at ``<vault_root>/<path>``, its contents
    are injected as an authoritative ``system`` section on every
    interactive turn (#592). Independent of vault_retrieval — plain file
    read, no embeddings.
    """
    enabled: bool = True
    path: str = "AGENTS.md"      # relative to vault root
    max_tokens: int = 2000       # AGENTS.md is ~500 tok; generous cap
```

- [ ] **Step 4: Wire onto Config**

In `src/decafclaw/config.py`, add the field to the `Config` dataclass next to `vault` (~line 178):

```python
    vault_guide: VaultGuideConfig = field(default_factory=VaultGuideConfig)
```

Add the import to the existing `config_types` import block (find where `VaultConfig`, `VaultRetrievalConfig` are imported) so `VaultGuideConfig` is in scope.

In `load_config()`, after the `vault = load_sub_config(...)` block (~line 448):

```python
    vault_guide = load_sub_config(
        VaultGuideConfig, file_data.get("vault_guide", {}), "VAULT_GUIDE")
```

In the `Config(...)` constructor call, next to `vault=vault,` (~line 540):

```python
        vault_guide=vault_guide,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -k vault_guide -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/config_types.py src/decafclaw/config.py tests/test_config.py
git commit -m "feat(592): add VaultGuideConfig"
```

---

## Task 2: `_compose_vault_guide` helper

**Files:**
- Modify: `src/decafclaw/context_composer.py` (new method on `ContextComposer`, place near `_compose_vault_references`)
- Test: `tests/test_context_composer.py` (new `TestComposeVaultGuide` class)

- [ ] **Step 1: Write the failing tests**

Add a new test class to `tests/test_context_composer.py` (model on the existing `TestComposeWikiContext`). The `config`, `ctx`, and `tmp_path` fixtures come from `tests/conftest.py`:

```python
class TestComposeVaultGuide:
    def _write_guide(self, config, tmp_path, text):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir(exist_ok=True)
        config.vault.vault_path = str(vault_dir)
        (vault_dir / config.vault_guide.path).write_text(text, encoding="utf-8")
        return vault_dir

    def test_returns_section_when_present(self, config, tmp_path):
        self._write_guide(config, tmp_path, "# Vault Guide\nUser journals live in journals/.")
        composer = ContextComposer()
        text, entry = composer._compose_vault_guide(config, ComposerMode.INTERACTIVE)
        assert text.startswith("<vault_guide>")
        assert "journals/" in text
        assert entry is not None
        assert entry.source == "vault_guide"
        assert entry.items_included == 1
        assert entry.tokens_estimated > 0

    def test_skips_when_disabled(self, config, tmp_path):
        self._write_guide(config, tmp_path, "# Guide")
        config.vault_guide.enabled = False
        composer = ContextComposer()
        text, entry = composer._compose_vault_guide(config, ComposerMode.INTERACTIVE)
        assert text == ""
        assert entry is None

    def test_skips_when_file_absent(self, config, tmp_path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        config.vault.vault_path = str(vault_dir)  # no AGENTS.md written
        composer = ContextComposer()
        text, entry = composer._compose_vault_guide(config, ComposerMode.INTERACTIVE)
        assert text == ""
        assert entry is None

    def test_skips_when_file_empty(self, config, tmp_path):
        self._write_guide(config, tmp_path, "   \n  ")
        composer = ContextComposer()
        text, entry = composer._compose_vault_guide(config, ComposerMode.INTERACTIVE)
        assert text == ""
        assert entry is None

    @pytest.mark.parametrize("mode", [
        ComposerMode.HEARTBEAT, ComposerMode.SCHEDULED, ComposerMode.CHILD_AGENT,
    ])
    def test_skips_background_modes(self, config, tmp_path, mode):
        self._write_guide(config, tmp_path, "# Guide")
        composer = ContextComposer()
        text, entry = composer._compose_vault_guide(config, mode)
        assert text == ""
        assert entry is None

    def test_truncates_oversized_guide(self, config, tmp_path):
        config.vault_guide.max_tokens = 10  # ~40 char budget
        self._write_guide(config, tmp_path, "WORD " * 200)  # ~1000 chars
        composer = ContextComposer()
        text, entry = composer._compose_vault_guide(config, ComposerMode.INTERACTIVE)
        assert "[vault guide truncated]" in text
        assert entry.items_truncated == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context_composer.py::TestComposeVaultGuide -v`
Expected: FAIL — `AttributeError: 'ContextComposer' object has no attribute '_compose_vault_guide'`.

- [ ] **Step 3: Implement the helper**

In `src/decafclaw/context_composer.py`, add this method to the `ContextComposer` class, immediately after `_compose_vault_references` (keep the vault-related helpers together):

```python
    def _compose_vault_guide(
        self, config, mode: ComposerMode,
    ) -> tuple[str, SourceEntry | None]:
        """Inject the vault's authored guide (default ``AGENTS.md``) as an
        always-loaded ``system`` section.

        Read fresh each interactive turn from ``<vault_root>/<path>`` so the
        single source of truth is the vault file itself (no duplication into
        SOUL.md/AGENT.md/USER.md, no drift). Authoritative ``system`` role by
        design (#592): unlike *retrieved* vault content — which the composer
        remaps to ``user`` for prompt-injection posture — this is the user's
        own protocol doc and the goal is reliable enforcement.

        Fail-open: missing file / no vault / read error → ("", None).
        Skipped for heartbeat / scheduled / child-agent modes and when
        ``config.vault_guide.enabled`` is False. Returns (wrapped_text, entry).
        """
        from .prompts import wrap_xml
        from .util import estimate_tokens

        skip_modes = {
            ComposerMode.HEARTBEAT, ComposerMode.SCHEDULED, ComposerMode.CHILD_AGENT,
        }
        if not config.vault_guide.enabled or mode in skip_modes:
            return "", None

        try:
            guide_path = config.vault_root / config.vault_guide.path
            if not guide_path.is_file():
                return "", None
            content = guide_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            log.debug("vault guide read failed: %s", exc)
            return "", None

        if not content:
            return "", None

        truncated = 0
        max_tokens = config.vault_guide.max_tokens
        if estimate_tokens(content) > max_tokens:
            full_len = len(content)
            content = content[: max_tokens * 4].rstrip() + "\n\n[vault guide truncated]"
            truncated = 1
            log.warning(
                "vault guide %s exceeds max_tokens=%d; truncated from %d chars",
                config.vault_guide.path, max_tokens, full_len,
            )

        text = wrap_xml("vault_guide", content)
        entry = SourceEntry(
            source="vault_guide",
            tokens_estimated=estimate_tokens(text),
            items_included=1,
            items_truncated=truncated,
            details={"path": config.vault_guide.path},
        )
        return text, entry
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_context_composer.py::TestComposeVaultGuide -v`
Expected: PASS (all 6 cases incl. the 3 parametrized background modes).

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/context_composer.py tests/test_context_composer.py
git commit -m "feat(592): _compose_vault_guide helper"
```

---

## Task 3: Wire the guide into `compose()`

**Files:**
- Modify: `src/decafclaw/context_composer.py` (`compose()` — budget math + assembly block)
- Test: `tests/test_context_composer.py` (add to `TestCompose`)

- [ ] **Step 1: Write the failing integration tests**

Add to `tests/test_context_composer.py`. Model the setup on the existing `TestCompose` class (it already builds `composer`/`ctx` and calls `composer.compose(...)`). These tests write a guide into the vault dir, run `compose()`, and assert the `<vault_guide>` system message is present for `INTERACTIVE` and absent for `HEARTBEAT`:

```python
class TestComposeVaultGuideIntegration:
    async def test_guide_injected_for_interactive(self, ctx, config, tmp_path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        config.vault.vault_path = str(vault_dir)
        (vault_dir / "AGENTS.md").write_text(
            "# Vault Guide\nUser journals: journals/YYYY/.", encoding="utf-8")
        composer = ContextComposer()
        result = await composer.compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)
        system_msgs = [m["content"] for m in result.messages if m["role"] == "system"]
        assert any("<vault_guide>" in c and "journals/YYYY/" in c for c in system_msgs)
        # Appears right after the main system prompt, before any other content.
        guide_idx = next(i for i, m in enumerate(result.messages)
                         if m["role"] == "system" and "<vault_guide>" in m["content"])
        assert guide_idx == 1
        assert any(s.source == "vault_guide" for s in result.sources)

    async def test_guide_skipped_for_heartbeat(self, ctx, config, tmp_path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        config.vault.vault_path = str(vault_dir)
        (vault_dir / "AGENTS.md").write_text("# Vault Guide", encoding="utf-8")
        composer = ContextComposer()
        result = await composer.compose(ctx, "hello", [], mode=ComposerMode.HEARTBEAT)
        system_msgs = [m["content"] for m in result.messages if m["role"] == "system"]
        assert not any("<vault_guide>" in c for c in system_msgs)
```

> Note: `TestCompose` methods in this file are already `async` and rely on the project's asyncio test config — match the existing decorator/style in that class (e.g. `@pytest.mark.asyncio` if present there; otherwise none).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context_composer.py::TestComposeVaultGuideIntegration -v`
Expected: FAIL — no `<vault_guide>` system message present (assertion error), because `compose()` doesn't call the helper yet.

- [ ] **Step 3: Wire into `compose()` — budget + sources**

In `src/decafclaw/context_composer.py`, in `compose()`, find the budget block that ends just before `# Response reserve (leave room for the model's response)`. Immediately after the `preempt_skill_entry` token accounting:

```python
        if preempt_skill_entry is not None:
            fixed_tokens += preempt_skill_entry.tokens_estimated
```

add:

```python
        # -- Vault guide (always-loaded authored vault doc, e.g. AGENTS.md) --
        vault_guide_text, vault_guide_entry = self._compose_vault_guide(config, mode)
        if vault_guide_entry is not None:
            sources.append(vault_guide_entry)
            fixed_tokens += vault_guide_entry.tokens_estimated
```

(Placing it here ensures its tokens are subtracted from the memory-retrieval budget computed on the next lines.)

- [ ] **Step 4: Wire into `compose()` — message assembly**

Find the assembly block under the `# -- Assemble final messages --` comment:

```python
        messages = [{"role": "system", "content": system_text}]
        if deferred_text:
            messages.append({"role": "system", "content": deferred_text})
```

Insert the guide message between the system prompt and `deferred_text`:

```python
        messages = [{"role": "system", "content": system_text}]
        if vault_guide_text:
            messages.append({"role": "system", "content": vault_guide_text})
        if deferred_text:
            messages.append({"role": "system", "content": deferred_text})
```

- [ ] **Step 5: Run the integration tests + full composer suite**

Run: `uv run pytest tests/test_context_composer.py -v`
Expected: PASS (new integration tests pass; no regressions in existing tests).

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/context_composer.py tests/test_context_composer.py
git commit -m "feat(592): inject vault guide as always-loaded system section in compose()"
```

---

## Task 4: Docs

**Files:**
- Modify: `docs/context-composer.md`
- Modify: `docs/vault.md`

- [ ] **Step 1: Update `docs/context-composer.md`**

Add a subsection documenting the always-loaded vault guide. Place it near the memory-retrieval / always-loaded context discussion. Content to include:

```markdown
### Vault guide (always-loaded)

When a guide file exists at the vault root (default `AGENTS.md`, configurable
via `vault_guide.path`), `ContextComposer._compose_vault_guide` reads it fresh
each interactive turn and injects it as an authoritative `<vault_guide>`
**system** section, immediately after the main system prompt and before the
deferred-tools/preempt sections.

This is for *always-applies* vault rules (folder layout, which paths are the
user's vs the agent's, protocols) that must be present before the model makes
any tool decision — unlike memory retrieval, which is similarity-gated and can
miss a procedural rule when the query embeds far from it.

- **Authoritative `system` role** — unlike retrieved vault content (remapped to
  `user`), the guide is treated as binding instructions. Trust assumption: it is
  the user's own authored file.
- **Independent of `vault_retrieval`** — plain file read, no embeddings; works
  even when semantic search is disabled.
- **Skipped** for heartbeat / scheduled / child-agent modes.
- **Fail-open** — missing file / read error → no section.
- **Token cap** (`vault_guide.max_tokens`, default 2000) — oversized guides are
  truncated (head kept) with a logged warning.

Config: `vault_guide.{enabled,path,max_tokens}` (`VaultGuideConfig`).
```

- [ ] **Step 2: Update `docs/vault.md`**

Add one line where vault structure / AGENTS.md is relevant:

```markdown
A guide file at the vault root (default `AGENTS.md`) is auto-injected into every
interactive turn as an always-loaded `system` section — see
[context-composer.md](context-composer.md#vault-guide-always-loaded). Use it for
vault-layout and protocol rules the agent must always follow.
```

- [ ] **Step 3: Commit**

```bash
git add docs/context-composer.md docs/vault.md
git commit -m "docs(592): document always-loaded vault guide"
```

---

## Final verification

- [ ] **Run lint + typecheck + full test suite**

```bash
uv run make check && uv run make test
```

Expected: clean (no warnings), all tests pass. (If `make` targets don't take `uv run`, run `make check` / `make test` directly inside the worktree venv.)

- [ ] **Manual smoke (optional, ask Les before touching a running server)**

Confirm via a CLI compose smoke or local web-only run that the `<vault_guide>` section appears for an interactive turn when an `AGENTS.md` exists at the vault root. Do **not** start a server that collides with Les's running `make dev`.

---

## Spec coverage check

- ✅ `_compose_vault_guide`, fresh read, `<vault_guide>` system section — Task 2.
- ✅ Placement after `system_text`, before deferred/preempt — Task 3 Step 4.
- ✅ Mode skip (heartbeat/scheduled/child) — Task 2 (parametrized) + Task 3 integration.
- ✅ Fail-open (missing/empty/unreadable) — Task 2.
- ✅ Token cap + logged truncation — Task 2.
- ✅ `SourceEntry(source="vault_guide")` diagnostics — Task 2 + budget wiring Task 3 Step 3.
- ✅ `VaultGuideConfig` (enabled/path/max_tokens), independent of retrieval — Task 1.
- ✅ Unit-only, no eval — all tasks.
- ✅ Docs (`context-composer.md`, `vault.md`) — Task 4.
