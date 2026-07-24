# Vault Frontmatter Rendering + Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the web UI frontmatter-aware so vault pages render their YAML as structured metadata instead of mangled markdown, and so editing a page can never corrupt its frontmatter.

**Architecture:** The server splits frontmatter from body before either reaches `marked` or Milkdown, mirroring the existing `/api/schedules/*` pattern. Body-only writes splice the original frontmatter block back **verbatim** (never through `yaml.dump`), so key order, comments, and even malformed YAML survive. Metadata gets its own `<wiki-metadata>` component with typed controls plus a raw-YAML escape hatch; `wiki-page` owns all metadata PUTs so it can keep `<wiki-editor>`'s mtime in sync.

**Tech Stack:** Python 3.13 / Starlette / PyYAML on the server; Lit 3 + Milkdown + `marked` on the client. Tests: pytest (`pytest-xdist -n auto`). JS verification: `tsc --checkJs` via `make check-js` — there is no JS unit-test harness.

## Global Constraints

- Work in the worktree `/Users/lorchard/devel/decafclaw/.claude/worktrees/vault-frontmatter-ui` on branch `feat/vault-frontmatter-ui`. Run `cd` to it and confirm with `pwd` and `git branch --show-current` before any edit. Baseline is green: 3234 passed, 2 skipped.
- **Never enumerate fields when copying/snapshotting/serializing.** Use `dataclasses.replace` / `asdict` / `copy.copy`. Applies to any dataclass touched here.
- **Stdlib imports at module level.** Function-level imports are for breaking import cycles only — and must carry a comment saying which cycle.
- **Bare `except: pass` is never acceptable.** Use `except Exception as exc: log.debug(...)`.
- **Tag-qualify custom button CSS rules** (`button.foo`, not `.foo`). Pico's `button:not(...)` is 0,1,1 and beats a bare class.
- **Reach for `primitives.css` first** (`.dc-icon-btn`, `.dc-overlay-header`, …) before declaring per-component border/radius/shadow/hover rules.
- **Do not add `js-yaml` or any new JS dependency.** All YAML parsing is server-side.
- **Do not touch `src/decafclaw/tags.py` or add tag-query endpoints.** #318 owns those; see the spec's "Relationship to #318".
- `make check` (lint + typecheck Python and JS) and `make test` must pass before each commit.
- Commit messages end with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- Do **not** push or open a PR. Les reviews locally first.

---

## File Structure

**Create:**
- `src/decafclaw/web/static/components/wiki-metadata.js` — the metadata panel. Presentational: renders typed controls + raw-YAML editor, emits events. Owns no I/O.
- `src/decafclaw/web/static/styles/wiki-metadata.css` — panel styling, imported from `style.css`.

**Modify:**
- `src/decafclaw/frontmatter.py` — gains `split_frontmatter`, `join_frontmatter`, `parse_frontmatter_block`; receives `merge_frontmatter` relocated from the vault skill.
- `src/decafclaw/http_server.py` — `vault_read` (`:1204`), `vault_write` (`:1285`), `vault_list` (`:1105`), `vault_recent` (`:1180`).
- `src/decafclaw/skills/vault/tools.py:1167` — `merge_frontmatter` removed, imported from `frontmatter` instead.
- `src/decafclaw/backfill_frontmatter.py:30` — import re-pointed.
- `src/decafclaw/web/static/components/wiki-page.js` — consumes `body`, hosts `<wiki-metadata>`, owns metadata PUTs, `_loaded` flag.
- `src/decafclaw/web/static/components/vault-sidebar.js` — summary subtitles.
- `src/decafclaw/web/static/style.css` — one `@import` line.
- `tests/test_frontmatter.py`, `tests/test_vault_api.py`.
- `docs/vault.md`, `docs/web-ui.md`.

**Responsibility split that matters:** `wiki-metadata.js` never calls `fetch`. It emits `metadata-change` (typed patch) and `metadata-raw-save` (raw string); `wiki-page.js` performs every PUT. This is what lets `wiki-page` serialize metadata writes against body writes and push the resulting mtime into `<wiki-editor>`.

---

## Task 1: Body-only writes preserve frontmatter verbatim

The load-bearing fix. Everything else is presentation.

**Files:**
- Modify: `src/decafclaw/frontmatter.py`
- Modify: `src/decafclaw/http_server.py:1313-1331` (`vault_write` body path)
- Test: `tests/test_frontmatter.py`, `tests/test_vault_api.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `split_frontmatter(text: str) -> tuple[str | None, str]` — `(raw_yaml, body)`; `raw_yaml` is the text *between* the `---` delimiters exactly as written, with no trailing newline; `None` when there is no block.
  - `join_frontmatter(raw_yaml: str | None, body: str) -> str` — inverse of `split_frontmatter`.
  - `parse_frontmatter_block(raw_yaml: str | None) -> tuple[dict, str | None]` — `(metadata, error_message)`; `({}, None)` when `raw_yaml` is `None` or blank; `({}, "…")` when malformed or not a mapping.

- [ ] **Step 1: Write the failing regression test**

Append to `tests/test_vault_api.py` after `test_vault_write_new_page`:

```python
@pytest.mark.asyncio
async def test_vault_write_body_only_preserves_frontmatter(client, http_config):
    """A body-only PUT must leave the frontmatter block byte-identical.

    Regression test: the web UI had no frontmatter awareness, so Milkdown
    parsed the YAML into markdown nodes and serialized the mangled result
    back over the file on save.
    """
    path = http_config.vault_agent_pages_dir / "Fm.md"
    original_block = (
        "---\n"
        "importance: 0.7\n"
        "tags:\n"
        "- 0din\n"
        "---\n"
    )
    path.write_text(original_block + "# 0din\n\nOld body.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Fm",
        json={"content": "# 0din\n\nNew body.\n"},
    )
    assert resp.status_code == 200

    text = path.read_text()
    assert text.startswith(original_block)
    assert text == original_block + "# 0din\n\nNew body.\n"


@pytest.mark.asyncio
async def test_vault_write_body_only_preserves_malformed_frontmatter(
    client, http_config,
):
    """Malformed YAML must survive a body write untouched.

    parse_frontmatter returns ({}, body) on YAMLError, so reserializing via
    serialize_frontmatter would silently delete the block entirely.
    """
    path = http_config.vault_agent_pages_dir / "Broken.md"
    original_block = "---\nthis: is: not: valid: yaml\n---\n"
    path.write_text(original_block + "Body.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Broken",
        json={"content": "New body.\n"},
    )
    assert resp.status_code == 200
    assert path.read_text() == original_block + "New body.\n"


@pytest.mark.asyncio
async def test_vault_write_body_only_preserves_key_order_and_comments(
    client, http_config,
):
    """Hand-authored formatting must survive a body write.

    yaml.dump defaults to sort_keys=True and drops comments, so this is what
    catches a regression back to reserializing on the body path.
    """
    path = http_config.vault_agent_pages_dir / "Hand.md"
    original_block = (
        "---\n"
        "# why this matters\n"
        "tags:\n"
        "- zeta\n"
        "importance: 0.4\n"
        "---\n"
    )
    path.write_text(original_block + "Body.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Hand",
        json={"content": "Edited.\n"},
    )
    assert resp.status_code == 200
    assert path.read_text() == original_block + "Edited.\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/vault-frontmatter-ui
uv run pytest tests/test_vault_api.py -k "body_only" -v
```

Expected: all three FAIL. The file gets overwritten with the body alone, so `assert text.startswith(original_block)` fails on the first and the equality assertions fail on the others.

- [ ] **Step 3: Write the failing `split_frontmatter` unit tests**

Append to `tests/test_frontmatter.py`, and add `join_frontmatter`, `parse_frontmatter_block`, `split_frontmatter` to the existing `from decafclaw.frontmatter import (...)` block at the top of the file:

```python
# -- split_frontmatter / join_frontmatter ---------------------------------------


class TestSplitFrontmatter:
    def test_splits_block_without_parsing(self):
        text = "---\ntitle: Test\n---\n# Hello\nBody."
        raw, body = split_frontmatter(text)
        assert raw == "title: Test"
        assert body == "# Hello\nBody."

    def test_absent_block(self):
        text = "# Hello\nNo frontmatter."
        raw, body = split_frontmatter(text)
        assert raw is None
        assert body == text

    def test_empty_block_is_not_none(self):
        """An empty block existed; None means no block at all."""
        raw, body = split_frontmatter("---\n\n---\n# Hello")
        assert raw == ""
        assert body == "# Hello"

    def test_malformed_yaml_round_trips(self):
        text = "---\nthis: is: not: valid\n---\nBody."
        raw, body = split_frontmatter(text)
        assert raw == "this: is: not: valid"
        assert body == "Body."

    def test_body_starting_with_hr_is_not_swallowed(self):
        """The regex is non-greedy and anchored, so the real block wins."""
        text = "---\ntitle: T\n---\n---\nAn hr, not a delimiter.\n"
        raw, body = split_frontmatter(text)
        assert raw == "title: T"
        assert body == "---\nAn hr, not a delimiter.\n"

    @pytest.mark.parametrize("text", [
        "---\ntitle: Test\n---\n# Hello\nBody.",
        "# No frontmatter here.\n",
        "---\n\n---\nEmpty block.",
        "---\nbroken: : yaml\n---\n",
        "---\ntitle: T\n---\n---\nhr body\n",
    ])
    def test_round_trip_is_byte_identical(self, text):
        assert join_frontmatter(*split_frontmatter(text)) == text


class TestParseFrontmatterBlock:
    def test_valid_block(self):
        meta, error = parse_frontmatter_block("title: Test\ntags:\n- a")
        assert meta == {"title": "Test", "tags": ["a"]}
        assert error is None

    def test_none_block(self):
        assert parse_frontmatter_block(None) == ({}, None)

    def test_blank_block(self):
        assert parse_frontmatter_block("   \n") == ({}, None)

    def test_malformed_block_reports_error(self):
        meta, error = parse_frontmatter_block("this: is: not: valid")
        assert meta == {}
        assert error is not None

    def test_non_mapping_block_reports_error(self):
        meta, error = parse_frontmatter_block("- just\n- a\n- list")
        assert meta == {}
        assert error == "frontmatter is not a mapping"
```

- [ ] **Step 4: Run them to verify they fail**

```bash
uv run pytest tests/test_frontmatter.py -k "SplitFrontmatter or ParseFrontmatterBlock" -v
```

Expected: collection error — `ImportError: cannot import name 'split_frontmatter'`.

- [ ] **Step 5: Add the three helpers to `frontmatter.py`**

Insert after `serialize_frontmatter` (which currently ends at `:55`):

```python
def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split off the raw frontmatter block *without* parsing its YAML.

    Returns ``(raw_yaml, body)``. *raw_yaml* is the text between the ``---``
    delimiters exactly as written, with no trailing newline; ``None`` means the
    file has no frontmatter block at all (distinct from ``""``, an empty one).

    Purely lexical, so malformed YAML round-trips byte-for-byte. That is what
    lets body-only writes preserve a block they cannot parse — reserializing
    through ``parse_frontmatter`` + ``serialize_frontmatter`` would silently
    delete malformed frontmatter, since the parser reports ``{}`` on error.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None, text
    return match.group(1), text[match.end():]


def join_frontmatter(raw_yaml: str | None, body: str) -> str:
    """Re-attach a raw block from :func:`split_frontmatter`.

    ``join_frontmatter(*split_frontmatter(t)) == t`` for any *t*.
    """
    if raw_yaml is None:
        return body
    return f"---\n{raw_yaml}\n---\n{body}"


def parse_frontmatter_block(raw_yaml: str | None) -> tuple[dict, str | None]:
    """Parse a raw block from :func:`split_frontmatter`.

    Returns ``(metadata, error)``. On success *error* is ``None``; on malformed
    YAML or a non-mapping document *metadata* is ``{}`` and *error* carries a
    human-readable message. Unlike :func:`parse_frontmatter`, which logs and
    swallows both cases, this reports them so callers can refuse to write.
    """
    if raw_yaml is None or not raw_yaml.strip():
        return {}, None
    try:
        parsed = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        return {}, str(exc)
    if not isinstance(parsed, dict):
        return {}, "frontmatter is not a mapping"
    return parsed, None
```

- [ ] **Step 6: Run the unit tests to verify they pass**

```bash
uv run pytest tests/test_frontmatter.py -v
```

Expected: PASS, including the pre-existing `TestParseFrontmatter` class.

- [ ] **Step 7: Rewrite the `vault_write` body path**

In `http_server.py`, replace the block from `content = body.get("content")` through `target.write_text(content, encoding="utf-8")` (currently `:1313-1331`) with:

```python
    if "content" in body and "body" not in body:
        body["body"] = body.pop("content")
    new_body = body.get("body")
    if new_body is None or not isinstance(new_body, str):
        return JSONResponse({"error": "content (string) required"}, status_code=400)
    modified = body.get("modified")
    if modified is not None:
        try:
            modified = float(modified)
        except (TypeError, ValueError):
            return JSONResponse({"error": "modified must be a number"}, status_code=400)
        if target.exists():
            file_mtime = target.stat().st_mtime
            if file_mtime > modified + 1.0:
                return JSONResponse(
                    {"error": "conflict", "server_modified": file_mtime},
                    status_code=409,
                )
    existed = target.exists()
    # Splice the existing frontmatter block back verbatim rather than
    # reserializing it: yaml.dump would reorder keys and drop comments, and
    # parse_frontmatter reports {} for malformed YAML, which would delete it.
    existing_raw = None
    if existed:
        existing_raw, _ = split_frontmatter(target.read_text(encoding="utf-8"))
    content = join_frontmatter(existing_raw, new_body)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
```

Add `join_frontmatter` and `split_frontmatter` to the existing module-level `from .frontmatter import ...` in `http_server.py`. If there is no such import yet, add `from .frontmatter import join_frontmatter, split_frontmatter` alongside the other module-level relative imports at the top of the file.

- [ ] **Step 8: Run the regression tests to verify they pass**

```bash
uv run pytest tests/test_vault_api.py -v
```

Expected: PASS, including `test_vault_write_new_page` unchanged — a bare `content` PUT to a nonexistent page has `existing_raw = None`, so `join_frontmatter` returns the body alone and no frontmatter is invented.

- [ ] **Step 9: Full check and commit**

```bash
make check && make test
git add src/decafclaw/frontmatter.py src/decafclaw/http_server.py tests/test_frontmatter.py tests/test_vault_api.py
git commit -m "$(cat <<'EOF'
fix(vault-api): body-only writes preserve the frontmatter block verbatim

Editing a vault page through the web UI round-tripped its YAML through
Milkdown and wrote the mangled result back to disk. Body writes now splice
the original block back byte-for-byte via split/join_frontmatter, so key
order, comments, and malformed YAML all survive.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Relocate `merge_frontmatter` to `frontmatter.py`

Pure refactor, no behavior change. Independent of Task 1 — can be done in either order.

**Files:**
- Modify: `src/decafclaw/frontmatter.py`
- Modify: `src/decafclaw/skills/vault/tools.py:1167-1189` (definition removed), `:1231` (call site)
- Modify: `src/decafclaw/backfill_frontmatter.py:30`
- Modify: `docs/vault.md:115-118`

**Interfaces:**
- Consumes: nothing.
- Produces: `merge_frontmatter(existing: dict, fields: dict, overwrite: bool) -> dict`, importable as `from decafclaw.frontmatter import merge_frontmatter`. Signature and behavior byte-identical to the current version.

- [ ] **Step 1: Verify the current callers, so nothing is missed**

```bash
grep -rn "merge_frontmatter" src/ tests/ docs/
```

Expected: the definition at `skills/vault/tools.py:1167`, its call at `:1231`, a docstring mention at `:1195`, the import at `backfill_frontmatter.py:30`, its call at `:170`, and prose in `docs/vault.md`. **No tests import it directly** — it is covered indirectly through `tests/test_vault_tools.py` and `tests/test_backfill_frontmatter.py`.

- [ ] **Step 2: Move the function into `frontmatter.py`**

Cut the whole `def merge_frontmatter(...)` block from `skills/vault/tools.py:1167-1189` and paste it into `frontmatter.py` after `get_frontmatter_field` (which ends at `:85`). Delete its function-level import line — `get_frontmatter_field` is now in the same module — and update the docstring accordingly:

```python
def merge_frontmatter(existing: dict, fields: dict, overwrite: bool) -> dict:
    """Merge coerced field values into existing frontmatter metadata.

    Pure function — no ctx, no I/O — so callers (dream generation, the
    backfill CLI, garden importance tuning, the vault REST API) can reuse the
    merge logic without a running agent context. Coercion goes through
    `get_frontmatter_field` (importance clamped to [0, 1]; tags/keywords to
    list[str]; summary to str) so the merged result and the parser agree on
    shape.

    When `overwrite` is False, only fields that are absent or empty in
    `existing` are filled. When True, every field in `fields` is set,
    replacing any existing value.

    Note there is no deletion path: a ``None`` value coerces to ``None`` and
    is *set*, producing ``field: null`` rather than removing the key. Callers
    that need deletion strip null values from the result themselves.
    """
    merged = dict(existing)
    for field, raw_value in fields.items():
        coerced = get_frontmatter_field({field: raw_value}, field)
        if not overwrite and merged.get(field) not in (None, "", []):
            continue
        merged[field] = coerced
    return merged
```

- [ ] **Step 3: Re-point both importers**

In `src/decafclaw/skills/vault/tools.py`, the existing function-level import inside `tool_vault_update_frontmatter` becomes:

```python
    from decafclaw.frontmatter import (
        merge_frontmatter,
        parse_frontmatter,
        serialize_frontmatter,
    )
```

In `src/decafclaw/backfill_frontmatter.py`, delete line 30 (`from decafclaw.skills.vault.tools import merge_frontmatter`) and extend the existing module-level import at line 28:

```python
from decafclaw.frontmatter import (
    merge_frontmatter,
    parse_frontmatter,
    serialize_frontmatter,
)
```

- [ ] **Step 4: Verify no stale references remain**

```bash
grep -rn "from decafclaw.skills.vault.tools import merge_frontmatter" src/ tests/
```

Expected: no output.

- [ ] **Step 5: Run the affected suites**

```bash
uv run pytest tests/test_vault_tools.py tests/test_backfill_frontmatter.py tests/test_frontmatter.py -v
```

Expected: PASS with no changes to test files. `vault_update_frontmatter`'s behavior is unchanged, which is the point of this task.

- [ ] **Step 6: Update `docs/vault.md`**

In the "Frontmatter merge (#197)" section around `:115`, change the prose describing `merge_frontmatter` as living in the vault skill so it reads:

```markdown
`vault_update_frontmatter` is a thin async wrapper around a pure helper,
`merge_frontmatter(existing: dict, fields: dict, overwrite: bool) -> dict` in
`frontmatter.py` (coercion mirrors `get_frontmatter_field`'s rules so the merge
and the parser agree on shape). It lives beside the parser rather than in the
skill because the backfill CLI and the vault REST API both need it without a
running agent context.
```

Keep the rest of the section as-is.

- [ ] **Step 7: Full check and commit**

```bash
make check && make test
git add src/decafclaw/frontmatter.py src/decafclaw/skills/vault/tools.py src/decafclaw/backfill_frontmatter.py docs/vault.md
git commit -m "$(cat <<'EOF'
refactor(vault): move merge_frontmatter to frontmatter.py

backfill_frontmatter.py (a core CLI) already reached into the skill package
for it, and the vault REST API needs it next — three call sites. Beside the
parser it also loses its cycle-breaking function-level import.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `vault_read` returns `frontmatter`, `frontmatter_raw`, `body`

**Files:**
- Modify: `src/decafclaw/http_server.py:1204-1223` (`vault_read`)
- Modify: `tests/test_vault_api.py:110-124` (`test_vault_read_page` asserts on `content`)

**Interfaces:**
- Consumes: `split_frontmatter`, `parse_frontmatter_block` from Task 1.
- Produces: the `GET /api/vault/{page}` response shape consumed by Task 7 —
  `{title: str, path: str, frontmatter: dict, frontmatter_raw: str, body: str, modified: float}`, plus `frontmatter_error: str` only when the block is malformed. **No `content` key.**

- [ ] **Step 1: Write the failing tests**

Replace the body of the existing `test_vault_read_page` in `tests/test_vault_api.py` (keep its name and docstring) so its last assertion reads `assert "Hello world." in data["body"]` instead of `data["content"]`, then append:

```python
@pytest.mark.asyncio
async def test_vault_read_splits_frontmatter(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Split.md").write_text(
        "---\nimportance: 0.7\ntags:\n- a\n---\n# Split\n\nBody text.\n"
    )
    resp = await client.get("/api/vault/agent/pages/Split")
    assert resp.status_code == 200
    data = resp.json()
    assert data["frontmatter"] == {"importance": 0.7, "tags": ["a"]}
    assert data["body"] == "# Split\n\nBody text.\n"
    assert "---" not in data["body"]
    assert data["frontmatter_raw"] == "importance: 0.7\ntags:\n- a"
    assert "frontmatter_error" not in data
    assert "content" not in data


@pytest.mark.asyncio
async def test_vault_read_no_frontmatter(client, http_config):
    """frontmatter_raw is "" — not null — when there is no block."""
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Plain.md").write_text("# Plain\n\nJust body.\n")
    resp = await client.get("/api/vault/agent/pages/Plain")
    data = resp.json()
    assert data["frontmatter"] == {}
    assert data["frontmatter_raw"] == ""
    assert data["body"] == "# Plain\n\nJust body.\n"
    assert "frontmatter_error" not in data


@pytest.mark.asyncio
async def test_vault_read_malformed_frontmatter(client, http_config):
    """Malformed YAML surfaces as an error plus the raw block, not silence.

    frontmatter_raw is present on well-formed pages too, so the raw editor
    can be seeded with real bytes rather than a re-serialized dict.
    """
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Bad.md").write_text("---\nthis: is: not: valid\n---\nBody.\n")
    resp = await client.get("/api/vault/agent/pages/Bad")
    assert resp.status_code == 200
    data = resp.json()
    assert data["frontmatter"] == {}
    assert data["frontmatter_raw"] == "this: is: not: valid"
    assert data["frontmatter_error"]
    assert data["body"] == "Body.\n"
```

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run pytest tests/test_vault_api.py -k "vault_read" -v
```

Expected: `test_vault_read_page` FAILS with `KeyError: 'body'`; the three new tests FAIL the same way.

- [ ] **Step 3: Rewrite `vault_read`'s response**

Replace the tail of `vault_read` (from `content = resolved.read_text(...)` to the closing `})`) with:

```python
    content = resolved.read_text(encoding="utf-8")
    stat = resolved.stat()
    vault = _vault_root(config).resolve()
    rel = resolved.relative_to(vault)
    raw_block, page_body = split_frontmatter(content)
    metadata, fm_error = parse_frontmatter_block(raw_block)
    payload = {
        "title": resolved.stem,
        "path": str(rel.with_suffix("")),
        "frontmatter": metadata,
        # Always the real bytes: the raw editor has replace semantics, so
        # re-serializing the parsed dict would reorder keys and drop comments
        # the moment anyone opened the panel.
        "frontmatter_raw": raw_block or "",
        "body": page_body,
        "modified": stat.st_mtime,
    }
    if fm_error is not None:
        payload["frontmatter_error"] = fm_error
    return JSONResponse(payload)
```

Add `parse_frontmatter_block` to the `from .frontmatter import ...` line established in Task 1.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_vault_api.py -v
```

Expected: PASS.

- [ ] **Step 5: Confirm nothing else consumed `content`**

```bash
grep -rn "\.content\b\|\[.content.\]" src/decafclaw/web/static/components/wiki-page.js
grep -rn "api/vault" src/decafclaw/web/static/components/ src/decafclaw/web/static/lib/
```

Expected: hits only in `wiki-page.js` (rewired in Task 7), `wiki-editor.js` (reads `data.content` in `#reload`, also Task 7), and `vault-sidebar.js` (list endpoints only, no `content`). Note any additional consumer in `notes.md` before proceeding.

- [ ] **Step 6: Commit**

```bash
make check && make test
git add src/decafclaw/http_server.py tests/test_vault_api.py
git commit -m "$(cat <<'EOF'
feat(vault-api): vault_read splits frontmatter from body

GET /api/vault/{page} now returns frontmatter, frontmatter_raw, and body,
and drops content. Malformed YAML reports frontmatter_error alongside the
raw block instead of being silently swallowed.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: PUT `frontmatter` — patch semantics

**Files:**
- Modify: `src/decafclaw/http_server.py` (`vault_write`, the block written in Task 1)
- Test: `tests/test_vault_api.py`

**Interfaces:**
- Consumes: `merge_frontmatter` (Task 2), `split_frontmatter` / `join_frontmatter` / `parse_frontmatter_block` (Task 1), the Task 1 `vault_write` body block.
- Produces: `PUT /api/vault/{page}` accepting `frontmatter: dict` and responding `{ok: True, modified: float, frontmatter: dict}`. `body`/`content` becomes **optional** when `frontmatter` is present.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vault_api.py`:

```python
@pytest.mark.asyncio
async def test_vault_write_frontmatter_patch_merges(client, http_config):
    path = http_config.vault_agent_pages_dir / "Patch.md"
    path.write_text("---\nimportance: 0.4\ntags:\n- keep\n---\nBody.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Patch",
        json={"frontmatter": {"summary": "A summary."}},
    )
    assert resp.status_code == 200
    assert resp.json()["frontmatter"]["summary"] == "A summary."

    from decafclaw.frontmatter import parse_frontmatter
    meta, body = parse_frontmatter(path.read_text())
    assert meta == {
        "importance": 0.4,
        "tags": ["keep"],
        "summary": "A summary.",
    }
    assert body == "Body.\n"


@pytest.mark.asyncio
async def test_vault_write_frontmatter_patch_coerces(client, http_config):
    """importance 1.7 -> 1.0 proves merge_frontmatter is really in the path."""
    path = http_config.vault_agent_pages_dir / "Coerce.md"
    path.write_text("---\nimportance: 0.4\n---\nBody.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Coerce",
        json={"frontmatter": {"importance": 1.7, "tags": "solo"}},
    )
    assert resp.status_code == 200
    data = resp.json()["frontmatter"]
    assert data["importance"] == 1.0
    assert data["tags"] == ["solo"]


@pytest.mark.asyncio
async def test_vault_write_frontmatter_null_removes_key(client, http_config):
    """A patch cannot delete by omission, so null means remove.

    merge_frontmatter would otherwise write a literal `tags: null`.
    """
    path = http_config.vault_agent_pages_dir / "Del.md"
    path.write_text("---\nimportance: 0.4\ntags:\n- gone\n---\nBody.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Del",
        json={"frontmatter": {"tags": None}},
    )
    assert resp.status_code == 200
    assert "tags" not in resp.json()["frontmatter"]
    text = path.read_text()
    assert "tags" not in text
    assert "null" not in text
    assert "importance: 0.4" in text


@pytest.mark.asyncio
async def test_vault_write_frontmatter_patch_leaves_body_alone(
    client, http_config,
):
    path = http_config.vault_agent_pages_dir / "BodySafe.md"
    path.write_text("---\nimportance: 0.4\n---\n# Head\n\nExact body.\n")
    resp = await client.put(
        "/api/vault/agent/pages/BodySafe",
        json={"frontmatter": {"summary": "S"}},
    )
    assert resp.status_code == 200
    assert path.read_text().endswith("# Head\n\nExact body.\n")


@pytest.mark.asyncio
async def test_vault_write_frontmatter_patch_on_malformed_is_rejected(
    client, http_config,
):
    """Merging into an unparseable block would silently discard it."""
    path = http_config.vault_agent_pages_dir / "BadPatch.md"
    original = "---\nthis: is: not: valid\n---\nBody.\n"
    path.write_text(original)

    resp = await client.put(
        "/api/vault/agent/pages/BadPatch",
        json={"frontmatter": {"summary": "S"}},
    )
    assert resp.status_code == 400
    assert "malformed" in resp.json()["error"]
    assert path.read_text() == original


@pytest.mark.asyncio
async def test_vault_write_frontmatter_and_body_one_write(client, http_config):
    path = http_config.vault_agent_pages_dir / "Both.md"
    path.write_text("---\nimportance: 0.4\n---\nOld.\n")
    resp = await client.put(
        "/api/vault/agent/pages/Both",
        json={"frontmatter": {"summary": "S"}, "body": "New.\n"},
    )
    assert resp.status_code == 200
    text = path.read_text()
    assert "summary: S" in text
    assert text.endswith("New.\n")


@pytest.mark.asyncio
async def test_vault_write_frontmatter_stale_modified_conflicts(
    client, http_config,
):
    """A merge against a stale read would resurrect a just-deleted key."""
    path = http_config.vault_agent_pages_dir / "Stale.md"
    path.write_text("---\nimportance: 0.4\n---\nBody.\n")
    resp = await client.put(
        "/api/vault/agent/pages/Stale",
        json={"frontmatter": {"summary": "S"}, "modified": 1.0},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_vault_write_requires_a_payload(client, http_config):
    resp = await client.put("/api/vault/agent/pages/Nothing", json={})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run pytest tests/test_vault_api.py -k "frontmatter_patch or null_removes or requires_a_payload or frontmatter_and_body or frontmatter_stale" -v
```

Expected: FAIL with 400 `content (string) required` — `frontmatter` is not yet recognized, so a payload without a body is rejected.

- [ ] **Step 3: Implement the patch path**

In `vault_write`, replace the Task 1 block starting at `if "content" in body and "body" not in body:` through `target.write_text(content, encoding="utf-8")` with:

```python
    if "content" in body and "body" not in body:
        body["body"] = body.pop("content")
    new_body = body.get("body")
    fm_patch = body.get("frontmatter")
    if new_body is not None and not isinstance(new_body, str):
        return JSONResponse({"error": "body must be a string"}, status_code=400)
    if fm_patch is not None and not isinstance(fm_patch, dict):
        return JSONResponse(
            {"error": "frontmatter must be an object"}, status_code=400,
        )
    if new_body is None and fm_patch is None:
        return JSONResponse({"error": "content (string) required"}, status_code=400)

    modified = body.get("modified")
    if modified is not None:
        try:
            modified = float(modified)
        except (TypeError, ValueError):
            return JSONResponse({"error": "modified must be a number"}, status_code=400)
        if target.exists():
            file_mtime = target.stat().st_mtime
            if file_mtime > modified + 1.0:
                return JSONResponse(
                    {"error": "conflict", "server_modified": file_mtime},
                    status_code=409,
                )

    existed = target.exists()
    existing_text = target.read_text(encoding="utf-8") if existed else ""
    existing_raw, existing_body = split_frontmatter(existing_text)
    existing_meta, fm_error = parse_frontmatter_block(existing_raw)

    # Splice the existing block back verbatim when only the body changed:
    # yaml.dump would reorder keys and drop comments, and parse_frontmatter
    # reports {} for malformed YAML, which would delete it outright.
    new_raw = existing_raw
    if fm_patch is not None:
        if fm_error is not None:
            return JSONResponse(
                {"error": f"existing frontmatter is malformed: {fm_error}"},
                status_code=400,
            )
        merged = merge_frontmatter(existing_meta, fm_patch, overwrite=True)
        # merge_frontmatter has no deletion path — it would write `field: null`.
        merged = {key: value for key, value in merged.items() if value is not None}
        new_raw = _dump_frontmatter(merged)

    final_body = existing_body if new_body is None else new_body
    content = join_frontmatter(new_raw, final_body)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    result_meta, _ = parse_frontmatter_block(new_raw)
```

Add this module-level helper next to the other `_vault_*` helpers in `http_server.py`:

```python
def _dump_frontmatter(metadata: dict) -> str | None:
    """Serialize a metadata dict to a raw frontmatter block, or None if empty.

    Returns the block body only — no delimiters, no trailing newline — for
    `join_frontmatter`.
    """
    if not metadata:
        return None
    return yaml.dump(
        metadata, default_flow_style=False, allow_unicode=True,
    ).rstrip("\n")
```

Add `import yaml` to `http_server.py`'s module-level imports if absent, and `merge_frontmatter` to the `from .frontmatter import ...` line.

- [ ] **Step 4: Return the resulting frontmatter**

Change `vault_write`'s final return (`:1345`, `return JSONResponse({"ok": True, "modified": ...})`) to:

```python
    return JSONResponse({
        "ok": True,
        "modified": target.stat().st_mtime,
        "frontmatter": result_meta,
    })
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/test_vault_api.py -v
```

Expected: PASS, all Task 1 and Task 3 tests included.

- [ ] **Step 6: Commit**

```bash
make check && make test
git add src/decafclaw/http_server.py tests/test_vault_api.py
git commit -m "$(cat <<'EOF'
feat(vault-api): PUT accepts a frontmatter patch

Merged via the relocated merge_frontmatter with overwrite=True, so UI edits
and tool writes coerce identically. Null values remove keys (the helper would
write `field: null`), and a patch against malformed YAML is refused rather
than silently discarding the block.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: PUT `frontmatter_raw` — replace semantics

**Files:**
- Modify: `src/decafclaw/http_server.py` (`vault_write`)
- Test: `tests/test_vault_api.py`

**Interfaces:**
- Consumes: Task 4's `vault_write` block.
- Produces: `PUT /api/vault/{page}` accepting `frontmatter_raw: str`, mutually exclusive with `frontmatter`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vault_api.py`:

```python
@pytest.mark.asyncio
async def test_vault_write_frontmatter_raw_replaces(client, http_config):
    """Replace, not merge: a key absent from the submission is gone.

    This is the test that distinguishes the two paths — it fails if the raw
    field is wired to merge_frontmatter.
    """
    path = http_config.vault_agent_pages_dir / "Raw.md"
    path.write_text("---\nimportance: 0.4\ntags:\n- gone\n---\nBody.\n")

    resp = await client.put(
        "/api/vault/agent/pages/Raw",
        json={"frontmatter_raw": "summary: Only this.\n"},
    )
    assert resp.status_code == 200
    assert resp.json()["frontmatter"] == {"summary": "Only this."}
    text = path.read_text()
    assert "tags" not in text
    assert "importance" not in text
    assert text.endswith("Body.\n")


@pytest.mark.asyncio
async def test_vault_write_frontmatter_raw_preserves_user_text(
    client, http_config,
):
    """Stored verbatim, so hand-written comments and key order survive."""
    path = http_config.vault_agent_pages_dir / "RawVerbatim.md"
    path.write_text("---\nimportance: 0.4\n---\nBody.\n")

    resp = await client.put(
        "/api/vault/agent/pages/RawVerbatim",
        json={"frontmatter_raw": "# a note\nzeta: 1\nalpha: 2\n"},
    )
    assert resp.status_code == 200
    assert path.read_text() == (
        "---\n# a note\nzeta: 1\nalpha: 2\n---\nBody.\n"
    )


@pytest.mark.asyncio
async def test_vault_write_frontmatter_raw_empty_removes_block(
    client, http_config,
):
    path = http_config.vault_agent_pages_dir / "RawEmpty.md"
    path.write_text("---\nimportance: 0.4\n---\nBody.\n")
    resp = await client.put(
        "/api/vault/agent/pages/RawEmpty",
        json={"frontmatter_raw": "   \n"},
    )
    assert resp.status_code == 200
    assert path.read_text() == "Body.\n"
    assert resp.json()["frontmatter"] == {}


@pytest.mark.asyncio
async def test_vault_write_frontmatter_raw_malformed_is_rejected(
    client, http_config,
):
    path = http_config.vault_agent_pages_dir / "RawBad.md"
    original = "---\nimportance: 0.4\n---\nBody.\n"
    path.write_text(original)
    resp = await client.put(
        "/api/vault/agent/pages/RawBad",
        json={"frontmatter_raw": "this: is: not: valid\n"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]
    assert path.read_text() == original


@pytest.mark.asyncio
async def test_vault_write_frontmatter_raw_non_mapping_is_rejected(
    client, http_config,
):
    path = http_config.vault_agent_pages_dir / "RawList.md"
    original = "---\nimportance: 0.4\n---\nBody.\n"
    path.write_text(original)
    resp = await client.put(
        "/api/vault/agent/pages/RawList",
        json={"frontmatter_raw": "- just\n- a list\n"},
    )
    assert resp.status_code == 400
    assert "mapping" in resp.json()["error"]
    assert path.read_text() == original


@pytest.mark.asyncio
async def test_vault_write_frontmatter_raw_with_delimiter_is_rejected(
    client, http_config,
):
    """A bare `---` line inside the block would split the file in two."""
    path = http_config.vault_agent_pages_dir / "RawDelim.md"
    original = "---\nimportance: 0.4\n---\nBody.\n"
    path.write_text(original)
    resp = await client.put(
        "/api/vault/agent/pages/RawDelim",
        json={"frontmatter_raw": "a: 1\n---\nb: 2\n"},
    )
    assert resp.status_code == 400
    assert "---" in resp.json()["error"]
    assert path.read_text() == original


@pytest.mark.asyncio
async def test_vault_write_frontmatter_both_shapes_rejected(
    client, http_config,
):
    """Patch and replace cannot be reconciled in one write."""
    path = http_config.vault_agent_pages_dir / "RawBoth.md"
    path.write_text("---\nimportance: 0.4\n---\nBody.\n")
    resp = await client.put(
        "/api/vault/agent/pages/RawBoth",
        json={"frontmatter": {"summary": "S"}, "frontmatter_raw": "a: 1\n"},
    )
    assert resp.status_code == 400
    assert "mutually exclusive" in resp.json()["error"]
```

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run pytest tests/test_vault_api.py -k "frontmatter_raw or both_shapes" -v
```

Expected: FAIL with 400 `content (string) required` — `frontmatter_raw` is not yet recognized.

- [ ] **Step 3: Implement the replace path**

In `vault_write`, extend the validation block. After the `fm_patch` type check, add:

```python
    fm_raw = body.get("frontmatter_raw")
    if fm_raw is not None and not isinstance(fm_raw, str):
        return JSONResponse(
            {"error": "frontmatter_raw must be a string"}, status_code=400,
        )
    if fm_patch is not None and fm_raw is not None:
        return JSONResponse(
            {"error": "frontmatter and frontmatter_raw are mutually exclusive"},
            status_code=400,
        )
```

Change the "nothing to do" guard to account for the third shape:

```python
    if new_body is None and fm_patch is None and fm_raw is None:
        return JSONResponse({"error": "content (string) required"}, status_code=400)
```

Then, in the frontmatter-resolution block, add the `fm_raw` branch **before** the `fm_patch` branch:

```python
    new_raw = existing_raw
    if fm_raw is not None:
        stripped = fm_raw.strip()
        if not stripped:
            new_raw = None
        else:
            # A bare `---` line would terminate the block early and push the
            # rest into the body on the next read.
            if any(line.strip() == "---" for line in fm_raw.splitlines()):
                return JSONResponse(
                    {"error": "frontmatter_raw must not contain a '---' line"},
                    status_code=400,
                )
            try:
                parsed = yaml.safe_load(stripped)
            except yaml.YAMLError as exc:
                return JSONResponse(
                    {"error": f"invalid YAML: {exc}"}, status_code=400,
                )
            if not isinstance(parsed, dict):
                return JSONResponse(
                    {"error": "frontmatter must be a mapping"}, status_code=400,
                )
            # Stored verbatim rather than re-dumped, so the comments and key
            # order the user typed survive.
            new_raw = fm_raw.strip("\n")
    elif fm_patch is not None:
        ...  # unchanged from Task 4
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_vault_api.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
make check && make test
git add src/decafclaw/http_server.py tests/test_vault_api.py
git commit -m "$(cat <<'EOF'
feat(vault-api): PUT accepts frontmatter_raw with replace semantics

Backs the raw-YAML editor: keys absent from the submission are removed, and
the user's text is stored verbatim so comments and key order survive. Rejects
malformed YAML, non-mappings, an embedded '---' line, and combining it with
the frontmatter patch field.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Sidebar summaries

Independent of Tasks 3–5; touches only the list endpoints and the sidebar.

**Files:**
- Modify: `src/decafclaw/http_server.py:1128-1143` (`vault_list` page loop), `:1180-1215` (`vault_recent` page loop)
- Modify: `src/decafclaw/web/static/components/vault-sidebar.js:257-265` and `:274-288`
- Test: `tests/test_vault_api.py`

**Interfaces:**
- Consumes: `split_frontmatter`, `parse_frontmatter_block` (Task 1).
- Produces: page rows in `GET /api/vault` and `GET /api/vault/recent` gain `summary: str` (`""` when absent).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_vault_api.py`:

```python
@pytest.mark.asyncio
async def test_vault_list_includes_summary(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "WithFm.md").write_text(
        "---\nsummary: A short summary.\n---\n# Body\n"
    )
    (pages_dir / "NoFm.md").write_text("# Body only\n")
    resp = await client.get("/api/vault?folder=agent/pages")
    assert resp.status_code == 200
    pages = {p["title"]: p for p in resp.json()["pages"]}
    assert pages["WithFm"]["summary"] == "A short summary."
    assert pages["NoFm"]["summary"] == ""


@pytest.mark.asyncio
async def test_vault_list_summary_survives_malformed_frontmatter(
    client, http_config,
):
    """A broken page must not break the whole listing."""
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Bad.md").write_text("---\nthis: is: not: valid\n---\nBody.\n")
    resp = await client.get("/api/vault?folder=agent/pages")
    assert resp.status_code == 200
    pages = {p["title"]: p for p in resp.json()["pages"]}
    assert pages["Bad"]["summary"] == ""


@pytest.mark.asyncio
async def test_vault_recent_includes_summary(client, http_config):
    pages_dir = http_config.vault_agent_pages_dir
    (pages_dir / "Recent.md").write_text("---\nsummary: Recent one.\n---\nB\n")
    resp = await client.get("/api/vault/recent")
    assert resp.status_code == 200
    pages = {p["title"]: p for p in resp.json()["pages"]}
    assert pages["Recent"]["summary"] == "Recent one."
```

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run pytest tests/test_vault_api.py -k "summary" -v
```

Expected: FAIL with `KeyError: 'summary'`.

- [ ] **Step 3: Add the shared reader helper**

Add next to `_dump_frontmatter` in `http_server.py`:

```python
def _page_summary(path: Path) -> str:
    """Read a page's frontmatter `summary`, or "" if absent or unreadable.

    Fail-open: a malformed or unreadable page must not break a listing.
    """
    try:
        raw_block, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    except OSError as exc:
        log.debug("Could not read %s for summary: %s", path, exc)
        return ""
    metadata, _ = parse_frontmatter_block(raw_block)
    summary = metadata.get("summary")
    return str(summary) if summary else ""
```

- [ ] **Step 4: Use it in both list endpoints**

In `vault_list`, add one key to the appended dict:

```python
            pages.append({
                "title": child.stem,
                "path": str(rel.with_suffix("")),
                "folder": folder_param,
                "modified": stat.st_mtime,
                "summary": _page_summary(child),
            })
```

In `vault_recent`, add `"summary": _page_summary(md_file),` to its appended dict alongside the existing `"modified"` key.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/test_vault_api.py -k "summary" -v
```

Expected: PASS.

- [ ] **Step 6: Render the subtitle in the sidebar**

In `vault-sidebar.js`, update the browse row (currently `:260-264`) to:

```javascript
          <div class="conv-item wiki-item ${isOpen ? 'active' : ''}" @click=${() => this.#handleWikiSelect(pagePath)} title=${pagePath}>
            <span class="conv-title">${p.title}</span>
            ${p.summary ? html`<span class="wiki-item-summary">${p.summary}</span>` : nothing}
          </div>
```

And the recent row (currently `:281-285`) to:

```javascript
          <div class="conv-item wiki-item recent-item ${isOpen ? 'active' : ''}" @click=${() => this.#handleWikiSelect(pagePath)} title=${pagePath}>
            ${p.folder ? html`<span class="recent-folder">${p.folder}/</span>` : nothing}
            <span class="conv-title">${p.title}</span>
            <span class="recent-time">${this.#formatRelativeTime(p.modified)}</span>
            ${p.summary ? html`<span class="wiki-item-summary">${p.summary}</span>` : nothing}
          </div>
```

Extend the JSDoc typedef on `_wikiPages` (at `:20`) and the corresponding one for `_recentPages` to include `summary?: string`, so `make check-js` stays clean.

- [ ] **Step 7: Style the subtitle**

Append to `src/decafclaw/web/static/styles/sidebar.css`:

```css
/* Vault page summary subtitle — clamped to two lines so long summaries
   don't push rows apart. */
.wiki-item-summary {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  width: 100%;
  font-size: 0.75rem;
  line-height: 1.3;
  color: var(--pico-muted-color);
}
```

- [ ] **Step 8: Verify and commit**

```bash
make check && make test
git add src/decafclaw/http_server.py src/decafclaw/web/static/components/vault-sidebar.js src/decafclaw/web/static/styles/sidebar.css tests/test_vault_api.py
git commit -m "$(cat <<'EOF'
feat(vault-ui): show page summaries in the vault sidebar

vault_list and vault_recent read frontmatter summaries fail-open, and both
sidebar views render them as a clamped subtitle.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `<wiki-metadata>` read-only strip, and `wiki-page` consumes `body`

This is where view mode stops rendering YAML as markdown. **No JS unit-test harness exists**, so verification is `make check-js` plus a Playwright check with the dev server; the assertions that can live in Python already do, in Tasks 3–6.

**Files:**
- Create: `src/decafclaw/web/static/components/wiki-metadata.js`
- Create: `src/decafclaw/web/static/styles/wiki-metadata.css`
- Modify: `src/decafclaw/web/static/style.css` (add `@import` after line 12)
- Modify: `src/decafclaw/web/static/components/wiki-page.js`
- Modify: `src/decafclaw/web/static/components/wiki-editor.js:246-247` (`#reload` reads `data.content`)

**Interfaces:**
- Consumes: the Task 3 GET shape (`frontmatter`, `frontmatter_raw`, `body`, `frontmatter_error`).
- Produces: `<wiki-metadata>` with properties `.frontmatter` (Object), `.frontmatterRaw` (String), `.frontmatterError` (String), `readonly` (Boolean attribute). Task 8 adds its events.

- [ ] **Step 1: Create the component, read-only path only**

Create `src/decafclaw/web/static/components/wiki-metadata.js`:

```javascript
/**
 * Vault page metadata panel — renders frontmatter as structured chrome
 * instead of letting the YAML reach the markdown renderer.
 *
 * Presentational: performs no I/O. The host (wiki-page) owns every PUT so it
 * can serialize metadata writes against wiki-editor's body autosave and keep
 * its mtime in sync.
 *
 * Read-only mode shows a compact strip that expands to full detail. Edit
 * controls arrive with the metadata-change / metadata-raw-save events.
 */

import { LitElement, html, nothing } from 'lit';

const EXPANDED_KEY = 'wiki-metadata-expanded';

/** Fields with purpose-built controls; everything else lives in raw YAML. */
const KNOWN_FIELDS = ['summary', 'importance', 'tags', 'keywords'];

/** Chips shown before the "+N" overflow in the collapsed strip. */
const CHIP_PREVIEW_LIMIT = 3;

export class WikiMetadata extends LitElement {
  static properties = {
    frontmatter: { attribute: false },
    frontmatterRaw: { attribute: false },
    frontmatterError: { attribute: false },
    readonly: { type: Boolean },
    _expanded: { state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    /** @type {Record<string, any>} */ this.frontmatter = {};
    /** @type {string} */ this.frontmatterRaw = '';
    /** @type {string} */ this.frontmatterError = '';
    this.readonly = false;
    this._expanded = localStorage.getItem(EXPANDED_KEY) === 'true';
  }

  #toggle() {
    this._expanded = !this._expanded;
    localStorage.setItem(EXPANDED_KEY, String(this._expanded));
  }

  /**
   * @param {string} field
   * @returns {string[]}
   */
  #list(field) {
    const value = this.frontmatter?.[field];
    if (Array.isArray(value)) return value.map(v => String(v));
    if (typeof value === 'string' && value) return [value];
    return [];
  }

  /** Keys with no typed control — surfaced so they're never invisible. */
  #otherKeys() {
    return Object.keys(this.frontmatter || {})
      .filter(k => !KNOWN_FIELDS.includes(k))
      .sort();
  }

  #hasAnything() {
    return Boolean(this.frontmatterError)
      || Object.keys(this.frontmatter || {}).length > 0;
  }

  /** @param {string[]} tags */
  #renderChips(tags) {
    return tags.map(tag => html`<span class="wiki-md-chip">${tag}</span>`);
  }

  #renderStrip() {
    const summary = this.frontmatter?.summary
      ? String(this.frontmatter.summary)
      : '';
    const importance = this.frontmatter?.importance;
    const tags = this.#list('tags');
    const shown = tags.slice(0, CHIP_PREVIEW_LIMIT);
    const overflow = tags.length - shown.length;
    return html`
      ${summary ? html`<span class="wiki-md-summary-line">${summary}</span>` : nothing}
      <span class="wiki-md-facts">
        ${importance == null ? nothing : html`<span class="wiki-md-importance">${importance}</span>`}
        ${this.#renderChips(shown)}
        ${overflow > 0 ? html`<span class="wiki-md-overflow">+${overflow}</span>` : nothing}
      </span>
    `;
  }

  #renderDetail() {
    const others = this.#otherKeys();
    return html`
      <dl class="wiki-md-detail">
        ${this.frontmatter?.summary ? html`
          <dt>summary</dt><dd>${String(this.frontmatter.summary)}</dd>
        ` : nothing}
        ${this.frontmatter?.importance == null ? nothing : html`
          <dt>importance</dt><dd>${this.frontmatter.importance}</dd>
        `}
        ${this.#list('tags').length ? html`
          <dt>tags</dt><dd>${this.#renderChips(this.#list('tags'))}</dd>
        ` : nothing}
        ${this.#list('keywords').length ? html`
          <dt>keywords</dt><dd>${this.#renderChips(this.#list('keywords'))}</dd>
        ` : nothing}
        ${others.map(key => html`
          <dt>${key}</dt><dd>${JSON.stringify(this.frontmatter[key])}</dd>
        `)}
      </dl>
    `;
  }

  render() {
    if (!this.#hasAnything()) return nothing;

    const label = this._expanded ? 'Collapse metadata' : 'Expand metadata';
    return html`
      <div class="wiki-metadata ${this._expanded ? 'expanded' : ''}">
        <button
          type="button"
          class="wiki-md-toggle"
          aria-expanded=${this._expanded ? 'true' : 'false'}
          title=${label}
          aria-label=${label}
          @click=${() => this.#toggle()}
        >${this._expanded ? '▾' : '▸'}</button>
        <div class="wiki-md-content">
          ${this.frontmatterError
            ? html`<div class="wiki-md-error">Frontmatter is not valid YAML: ${this.frontmatterError}</div>`
            : nothing}
          ${this._expanded ? this.#renderDetail() : this.#renderStrip()}
        </div>
      </div>
    `;
  }
}

customElements.define('wiki-metadata', WikiMetadata);
```

- [ ] **Step 2: Create the stylesheet**

Create `src/decafclaw/web/static/styles/wiki-metadata.css`:

```css
/* Vault page metadata panel. Sits between the toolbar and the page body in
   both view and edit mode. */

.wiki-metadata {
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--pico-muted-border-color);
  font-size: 0.8125rem;
}

.wiki-md-content {
  flex: 1;
  min-width: 0;
}

/* Tag-qualified: Pico's `button:not(...)` is 0,1,1 and would otherwise win. */
button.wiki-md-toggle {
  width: auto;
  margin: 0;
  padding: 0 0.25rem;
  border: none;
  background: none;
  /* Pico re-scopes --pico-color inside <button> to white-on-blue. */
  color: inherit;
  line-height: 1.4;
}

.wiki-md-summary-line {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  color: var(--pico-muted-color);
}

.wiki-md-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  align-items: center;
  margin-top: 0.25rem;
}

.wiki-md-importance {
  font-variant-numeric: tabular-nums;
  color: var(--pico-muted-color);
}

.wiki-md-chip {
  padding: 0.0625rem 0.375rem;
  border: 1px solid var(--pico-muted-border-color);
  border-radius: 1rem;
  color: var(--pico-muted-color);
  white-space: nowrap;
}

.wiki-md-overflow {
  color: var(--pico-muted-color);
}

.wiki-md-error {
  margin-bottom: 0.375rem;
  color: var(--pico-del-color);
}

.wiki-md-detail {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.25rem 0.75rem;
  margin: 0;
}

.wiki-md-detail dt {
  color: var(--pico-muted-color);
  font-weight: 600;
}

.wiki-md-detail dd {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin: 0;
}
```

- [ ] **Step 3: Register the stylesheet**

In `src/decafclaw/web/static/style.css`, insert after line 12 (`@import './styles/wiki-editor.css';`):

```css
@import './styles/wiki-metadata.css';
```

- [ ] **Step 4: Rewire `wiki-page.js` onto `body`**

Four edits in `src/decafclaw/web/static/components/wiki-page.js`:

Add the import beside `import './wiki-editor.js';`:

```javascript
import './wiki-metadata.js';
```

Replace the `_content` state declarations with body + metadata state. In `static properties`, replace `_content: { state: true },` with:

```javascript
    _body: { state: true },
    _frontmatter: { state: true },
    _frontmatterRaw: { state: true },
    _frontmatterError: { state: true },
    _loaded: { state: true },
```

In the constructor, replace `/** @type {string} */ this._content = '';` with:

```javascript
    /** @type {string} */ this._body = '';
    /** @type {Record<string, any>} */ this._frontmatter = {};
    /** @type {string} */ this._frontmatterRaw = '';
    /** @type {string} */ this._frontmatterError = '';
    this._loaded = false;
```

Replace `_fetchPage`'s body with:

```javascript
  async _fetchPage() {
    this._loading = true;
    this._error = '';
    this._loaded = false;
    this._body = '';
    try {
      const res = await fetch('/api/vault/' + encodePagePath(this.page));
      if (!res.ok) {
        this._error = res.status === 404 ? `Page "${this.page}" not found.` : `Error loading page (${res.status}).`;
        return;
      }
      const data = await res.json();
      this._title = data.title;
      this._body = data.body ?? '';
      this._frontmatter = data.frontmatter ?? {};
      this._frontmatterRaw = data.frontmatter_raw ?? '';
      this._frontmatterError = data.frontmatter_error ?? '';
      this._modified = data.modified;
      this._loaded = true;
    } catch (e) {
      this._error = 'Failed to load page.';
    } finally {
      this._loading = false;
    }
  }
```

In `render()`, replace the `if (!this._content) return nothing;` guard with:

```javascript
    // Guard on load state, not on body text: a page with frontmatter but an
    // empty body is legitimate and must still render its metadata.
    if (!this._loaded) {
      return nothing;
    }
```

- [ ] **Step 5: Render the panel in both modes**

In `render()`, define the panel just before the `if (this._editing)` branch:

```javascript
    const metadataPanel = html`
      <wiki-metadata
        readonly
        .frontmatter=${this._frontmatter}
        .frontmatterRaw=${this._frontmatterRaw}
        .frontmatterError=${this._frontmatterError}
      ></wiki-metadata>
    `;
```

In the edit-mode branch, insert `${metadataPanel}` between the opening `<div class="wiki-page">` and `<wiki-editor`, and change `.content=${this._content}` to `.content=${this._body}`.

In the view-mode branch, insert `${metadataPanel}` between the closing `</div>` of `.wiki-page-toolbar` and the opening `<div class="wiki-page-body">`, and change `renderMarkdown(this._content)` to `renderMarkdown(this._body)`.

- [ ] **Step 6: Fix `wiki-editor`'s reload path**

`#reload` at `wiki-editor.js:246-247` reads `data.content`, which the vault endpoint no longer returns. Change those two lines to:

```javascript
      this.content = data.body ?? data.content ?? '';
      this.modified = data.modified;
```

The `?? data.content` fallback is deliberate and not dead code: `wiki-editor` is shared with `schedule-page.js` and `config-panel.js`, whose endpoints still return `content`.

- [ ] **Step 7: Typecheck**

```bash
make check-js
```

Expected: no errors. If `_frontmatter`'s index access complains, confirm the constructor's `@type {Record<string, any>}` annotations are present.

- [ ] **Step 8: Verify in the browser**

Ask Les to stop `make dev` first — Playwright and the dev server share a Chrome cache directory and collide. Then:

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/vault-frontmatter-ui
make run
```

Open the vault sidebar, open the `0din` page in **view** mode. Confirm: no `<hr>` at the top, no YAML bullet list, the summary and tag chips appear in the strip, the body starts at the `# 0din` heading. Expand the strip and confirm all four known fields plus any unknown keys are listed. Switch to **edit** mode and confirm Milkdown shows only the body.

Then the corruption check — the whole point of the session:

```bash
git -C /Users/lorchard/devel/decafclaw status --short data/decafclaw/workspace/vault/
```

Type a character into the body in edit mode, wait two seconds for the autosave, then:

```bash
git -C /Users/lorchard/devel/decafclaw diff -- data/decafclaw/workspace/vault/agent/pages/0din.md
```

Expected: the diff touches **only** body lines. Zero changes inside the `---` block. Record the result in `notes.md`.

- [ ] **Step 9: Answer the open question from the spec**

The spec flagged as unverified whether `wiki-editor.js:109`'s `md !== prev` listener fires on initial load, which would mean merely *opening* a page corrupted it. Determine it now: open a page in edit mode, touch nothing, wait three seconds, and check `git diff`. Record the answer in `notes.md` either way — it belongs in the PR description.

- [ ] **Step 10: Commit**

```bash
make check && make test
git add src/decafclaw/web/static/components/wiki-metadata.js src/decafclaw/web/static/components/wiki-page.js src/decafclaw/web/static/components/wiki-editor.js src/decafclaw/web/static/styles/wiki-metadata.css src/decafclaw/web/static/style.css docs/dev-sessions/2026-07-24-1300-vault-frontmatter-ui/notes.md
git commit -m "$(cat <<'EOF'
feat(vault-ui): render frontmatter as a metadata strip, not markdown

wiki-page consumes the split body so neither marked nor Milkdown ever sees
the YAML. New wiki-metadata component shows a compact read-only strip that
expands to full detail, including unknown keys.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Metadata editing

**Files:**
- Modify: `src/decafclaw/web/static/components/wiki-metadata.js`
- Modify: `src/decafclaw/web/static/styles/wiki-metadata.css`
- Modify: `src/decafclaw/web/static/components/wiki-page.js`

**Interfaces:**
- Consumes: Task 4's `frontmatter` patch field, Task 5's `frontmatter_raw` field, Task 7's component.
- Produces: two events off `<wiki-metadata>` —
  - `metadata-change`, `detail: {fields: Record<string, any>}` — a partial patch; a `null` value means remove the key.
  - `metadata-raw-save`, `detail: {raw: string}` — the complete frontmatter as YAML text.

- [ ] **Step 1: Add the edit controls to `wiki-metadata.js`**

Add `_rawOpen`, `_rawText`, and `_rawError` to `static properties` as `{ state: true }`, initialize them in the constructor (`this._rawOpen = false; this._rawText = ''; this._rawError = '';`), and add these methods:

```javascript
  /** @param {Map<string, any>} changed */
  willUpdate(changed) {
    // Reseed the raw editor from the server's bytes whenever the page's
    // frontmatter changes underneath us, unless the user is mid-edit.
    if (changed.has('frontmatterRaw') && !this._rawOpen) {
      this._rawText = this.frontmatterRaw;
    }
  }

  /**
   * @param {string} field
   * @param {any} value — null removes the key
   */
  #emitChange(field, value) {
    this.dispatchEvent(new CustomEvent('metadata-change', {
      detail: { fields: { [field]: value } },
      bubbles: true,
      composed: true,
    }));
  }

  /** @param {string} field @param {string[]} tags */
  #emitList(field, tags) {
    this.#emitChange(field, tags.length ? tags : null);
  }

  /** @param {string} field @param {string} tag */
  #removeTag(field, tag) {
    this.#emitList(field, this.#list(field).filter(t => t !== tag));
  }

  /** @param {string} field @param {KeyboardEvent} e */
  #addTagKey(field, e) {
    if (e.key !== 'Enter' && e.key !== ',') return;
    e.preventDefault();
    const input = /** @type {HTMLInputElement} */ (e.target);
    const value = input.value.trim().replace(/,$/, '');
    if (!value) return;
    const existing = this.#list(field);
    if (!existing.includes(value)) this.#emitList(field, [...existing, value]);
    input.value = '';
  }

  #toggleRaw() {
    this._rawOpen = !this._rawOpen;
    this._rawError = '';
    if (this._rawOpen) this._rawText = this.frontmatterRaw;
  }

  #saveRaw() {
    this._rawError = '';
    this.dispatchEvent(new CustomEvent('metadata-raw-save', {
      detail: { raw: this._rawText },
      bubbles: true,
      composed: true,
    }));
  }

  /** Called by the host when a raw save is rejected. @param {string} message */
  setRawError(message) {
    this._rawError = message;
  }

  /** Called by the host after a raw save succeeds. */
  closeRaw() {
    this._rawOpen = false;
    this._rawError = '';
  }

  /** @param {string} field @param {string} label */
  #renderChipInput(field, label) {
    const tags = this.#list(field);
    return html`
      <dt>${label}</dt>
      <dd>
        ${tags.map(tag => html`
          <span class="wiki-md-chip">
            ${tag}
            <button
              type="button"
              class="wiki-md-chip-x"
              title="Remove ${tag}"
              aria-label="Remove ${tag}"
              @click=${() => this.#removeTag(field, tag)}
            >&times;</button>
          </span>
        `)}
        <input
          class="wiki-md-chip-input"
          type="text"
          placeholder="add…"
          aria-label="Add ${label}"
          @keydown=${(/** @type {KeyboardEvent} */ e) => this.#addTagKey(field, e)}
        />
      </dd>
    `;
  }

  #renderEditControls() {
    const importance = this.frontmatter?.importance;
    const disabled = Boolean(this.frontmatterError);
    return html`
      <dl class="wiki-md-detail">
        <dt>summary</dt>
        <dd>
          <textarea
            class="wiki-md-summary-input"
            rows="2"
            aria-label="Summary"
            ?disabled=${disabled}
            .value=${this.frontmatter?.summary ? String(this.frontmatter.summary) : ''}
            @change=${(/** @type {Event} */ e) => {
              const value = /** @type {HTMLTextAreaElement} */ (e.target).value.trim();
              this.#emitChange('summary', value || null);
            }}
          ></textarea>
        </dd>
        <dt>importance</dt>
        <dd>
          <input
            type="range"
            min="0" max="1" step="0.05"
            aria-label="Importance"
            ?disabled=${disabled}
            .value=${importance == null ? '0.5' : String(importance)}
            @change=${(/** @type {Event} */ e) => {
              const value = Number(/** @type {HTMLInputElement} */ (e.target).value);
              this.#emitChange('importance', value);
            }}
          />
          <span class="wiki-md-importance">${importance == null ? '—' : importance}</span>
        </dd>
        ${disabled ? nothing : this.#renderChipInput('tags', 'tags')}
        ${disabled ? nothing : this.#renderChipInput('keywords', 'keywords')}
      </dl>
      <div class="wiki-md-raw">
        <button type="button" class="wiki-md-raw-toggle" @click=${() => this.#toggleRaw()}>
          ${this._rawOpen ? '▾' : '▸'} edit raw YAML
        </button>
        ${this._rawOpen ? html`
          <textarea
            class="wiki-md-raw-input"
            rows="8"
            aria-label="Raw frontmatter YAML"
            .value=${this._rawText}
            @input=${(/** @type {Event} */ e) => {
              this._rawText = /** @type {HTMLTextAreaElement} */ (e.target).value;
            }}
          ></textarea>
          <div class="wiki-md-raw-actions">
            <button type="button" @click=${() => this.#saveRaw()}>Save YAML</button>
            <button type="button" class="secondary" @click=${() => this.#toggleRaw()}>Cancel</button>
            ${this._rawError ? html`<span class="wiki-md-error">${this._rawError}</span>` : nothing}
          </div>
        ` : nothing}
      </div>
    `;
  }
```

Two changes to the existing code: in `render()`, replace `${this._expanded ? this.#renderDetail() : this.#renderStrip()}` with

```javascript
          ${this._expanded
            ? (this.readonly ? this.#renderDetail() : this.#renderEditControls())
            : this.#renderStrip()}
```

and change `#hasAnything()` so an empty-but-editable panel still renders:

```javascript
  #hasAnything() {
    if (!this.readonly) return true;
    return Boolean(this.frontmatterError)
      || Object.keys(this.frontmatter || {}).length > 0;
  }
```

- [ ] **Step 2: Style the controls**

Append to `src/decafclaw/web/static/styles/wiki-metadata.css`:

```css
.wiki-md-summary-input,
.wiki-md-raw-input {
  width: 100%;
  margin: 0;
  font-size: 0.8125rem;
}

.wiki-md-raw-input {
  font-family: var(--pico-font-family-monospace);
}

.wiki-md-chip-input {
  width: 6rem;
  margin: 0;
  padding: 0 0.25rem;
  font-size: 0.75rem;
}

/* Tag-qualified so Pico's button:not(...) (0,1,1) doesn't win. */
button.wiki-md-chip-x,
button.wiki-md-raw-toggle {
  width: auto;
  margin: 0;
  border: none;
  background: none;
  color: inherit;
}

button.wiki-md-chip-x {
  padding: 0 0 0 0.25rem;
  font-size: 0.875rem;
  line-height: 1;
}

button.wiki-md-raw-toggle {
  padding: 0.25rem 0;
  font-size: 0.75rem;
  color: var(--pico-muted-color);
}

.wiki-md-raw {
  margin-top: 0.5rem;
}

.wiki-md-raw-actions {
  display: flex;
  gap: 0.5rem;
  align-items: center;
  margin-top: 0.375rem;
}

.wiki-md-raw-actions button {
  width: auto;
  margin: 0;
  padding: 0.125rem 0.5rem;
  font-size: 0.75rem;
}
```

- [ ] **Step 3: Own the PUTs in `wiki-page.js`**

Add these private fields and methods to `WikiPage`:

```javascript
  /** @type {ReturnType<typeof setTimeout> | null} */
  #metaTimer = null;
  /** @type {Record<string, any>} */
  #pendingFields = {};
  /** @type {Promise<void> | null} */
  #metaInFlight = null;

  /** @param {CustomEvent} e */
  _onMetadataChange(e) {
    Object.assign(this.#pendingFields, e.detail.fields);
    if (this.#metaTimer != null) clearTimeout(this.#metaTimer);
    this.#metaTimer = setTimeout(() => { this.#flushMetadata(); }, 600);
  }

  /** Send any debounced typed patch now. Resolves when the write completes. */
  async #flushMetadata() {
    if (this.#metaTimer != null) {
      clearTimeout(this.#metaTimer);
      this.#metaTimer = null;
    }
    if (this.#metaInFlight) await this.#metaInFlight;
    const fields = this.#pendingFields;
    this.#pendingFields = {};
    if (!Object.keys(fields).length) return;
    this.#metaInFlight = this.#putMetadata({ frontmatter: fields })
      .then(() => { this.#metaInFlight = null; });
    await this.#metaInFlight;
  }

  /** @param {CustomEvent} e */
  async _onMetadataRawSave(e) {
    // A typed patch landing after a raw replace would resurrect a key the
    // raw save just deleted, so flush it first. The two PUT shapes are
    // mutually exclusive server-side.
    await this.#flushMetadata();
    const panel = this.querySelector('wiki-metadata');
    const res = await this.#putMetadata({ frontmatter_raw: e.detail.raw });
    if (res.ok) {
      /** @type {any} */ (panel)?.closeRaw();
    } else {
      /** @type {any} */ (panel)?.setRawError(res.error);
    }
  }

  /**
   * @param {Record<string, any>} payload
   * @returns {Promise<{ok: boolean, error: string}>}
   */
  async #putMetadata(payload) {
    try {
      const res = await fetch('/api/vault/' + encodePagePath(this.page), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, modified: this._modified }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        return { ok: false, error: data.error || `Save failed (${res.status})` };
      }
      this._frontmatter = data.frontmatter ?? {};
      this._modified = data.modified;
      // Push the new mtime into the body editor, or its next autosave 409s.
      /** @type {any} */
      const editor = this.querySelector('wiki-editor');
      if (editor) editor.modified = data.modified;
      // Reseed the raw editor from the server's bytes.
      const fresh = await fetch('/api/vault/' + encodePagePath(this.page));
      if (fresh.ok) {
        const page = await fresh.json();
        this._frontmatterRaw = page.frontmatter_raw ?? '';
        this._frontmatterError = page.frontmatter_error ?? '';
      }
      return { ok: true, error: '' };
    } catch (err) {
      return { ok: false, error: 'Save failed (network error)' };
    }
  }
```

Then make the panel editable in edit mode and wire the events. Replace the `metadataPanel` definition from Task 7 with:

```javascript
    const metadataPanel = html`
      <wiki-metadata
        ?readonly=${!this._editing}
        .frontmatter=${this._frontmatter}
        .frontmatterRaw=${this._frontmatterRaw}
        .frontmatterError=${this._frontmatterError}
        @metadata-change=${this._onMetadataChange}
        @metadata-raw-save=${this._onMetadataRawSave}
      ></wiki-metadata>
    `;
```

Finally, flush pending metadata alongside the editor flush in `willUpdate` and `_toggleMode`: in each place that currently calls `editor.flushSave()`, add `await this.#flushMetadata();` immediately before it (`willUpdate` is not async — use `void this.#flushMetadata();` there, matching how it already fires `flushSave()` without awaiting).

- [ ] **Step 4: Typecheck**

```bash
make check-js
```

Expected: no errors.

- [ ] **Step 5: Verify editing in the browser**

With the server running (`make run`, dev stopped), on the `0din` page in edit mode:

1. Expand the panel. Edit the summary, blur, wait one second. Confirm the sidebar and panel reflect it and `git diff` shows only the `summary:` line changed.
2. Drag the importance slider. Confirm the value updates and only `importance:` changes on disk.
3. Remove a tag chip; add a new one with Enter. Confirm the `tags:` list changes and nothing else does.
4. Remove **every** tag. Confirm the `tags:` key is gone from the file — not `tags: null`.
5. Set importance above 1 is impossible via the slider, so verify coercion via the API instead:
   ```bash
   curl -s -X PUT localhost:18897/api/vault/agent/pages/0din \
     -H 'Content-Type: application/json' -b "$COOKIE" \
     -d '{"frontmatter": {"importance": 1.7}}' | python3 -m json.tool
   ```
   Expected: `"importance": 1.0`. (Tests already assert this; this confirms the wire path.)
6. Open **edit raw YAML**. Confirm the textarea holds the real bytes including any comments. Introduce a syntax error, Save YAML, and confirm the inline error appears and the file is unchanged on disk. Fix it, save, and confirm it applies.
7. Delete a key in the raw editor and save. Confirm it is gone — this is replace semantics working.
8. Edit the summary and, within the 600ms debounce, click Save YAML. Confirm the raw save wins and the typed patch does not resurrect anything.

Record any deviation in `notes.md`.

- [ ] **Step 6: Commit**

```bash
make check && make test
git add src/decafclaw/web/static/components/wiki-metadata.js src/decafclaw/web/static/components/wiki-page.js src/decafclaw/web/static/styles/wiki-metadata.css docs/dev-sessions/2026-07-24-1300-vault-frontmatter-ui/notes.md
git commit -m "$(cat <<'EOF'
feat(vault-ui): edit frontmatter via typed controls and raw YAML

Typed controls for summary/importance/tags/keywords debounce into a
frontmatter patch; the raw editor sends whole-frontmatter replace. wiki-page
owns every PUT so it can flush the typed patch before a raw save and push the
resulting mtime into wiki-editor.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Docs and session wrap-up

**Files:**
- Modify: `docs/vault.md`, `docs/web-ui.md`
- Modify: `docs/dev-sessions/2026-07-24-1300-vault-frontmatter-ui/notes.md`

**Interfaces:**
- Consumes: everything.
- Produces: nothing code depends on.

- [ ] **Step 1: Document the web-UI frontmatter surface in `docs/vault.md`**

Add after the "Page Frontmatter" section:

```markdown
### Editing frontmatter in the web UI

Vault pages in the web UI split frontmatter from body server-side, so neither
the markdown renderer nor the Milkdown editor ever sees the YAML. View mode
shows a compact metadata strip that expands to full detail; edit mode swaps it
for typed controls (`summary`, `importance`, `tags`, `keywords`) plus an
**edit raw YAML** escape hatch covering the whole block, including keys with no
typed control.

Two write paths, mutually exclusive on the wire:

- Typed controls send a **patch** (`PUT /api/vault/{page}` with `frontmatter`),
  merged via `merge_frontmatter(overwrite=True)`. A `null` value removes a key.
- The raw editor sends a **replace** (`frontmatter_raw`), stored verbatim so
  hand-written comments and key order survive. Keys absent from the submission
  are removed.

Body-only writes never reserialize the block — they splice the original bytes
back via `split_frontmatter` / `join_frontmatter`. Key order, comments, and even
malformed YAML survive a body edit untouched. A page whose frontmatter fails to
parse reports `frontmatter_error`, disables the typed controls, and can still be
repaired through the raw editor.
```

- [ ] **Step 2: Document the component and API change in `docs/web-ui.md`**

Add `wiki-metadata.js` to whatever component list the file maintains, and record the changed vault contract:

```markdown
`GET /api/vault/{page}` returns `frontmatter` (parsed dict), `frontmatter_raw`
(the block's exact text, `""` when absent), and `body` (frontmatter-stripped).
It does **not** return `content`. `frontmatter_error` appears only when the
block fails to parse.

`PUT /api/vault/{page}` accepts `content`/`body` (body-only; the frontmatter
block is preserved verbatim), `frontmatter` (dict patch), or `frontmatter_raw`
(string, whole-block replace). The last two are mutually exclusive. The
response carries `modified` and the resulting `frontmatter` — `wiki-page` pushes
that `modified` into `<wiki-editor>` so the body autosave doesn't 409.
```

- [ ] **Step 3: Write the session summary in `notes.md`**

Cover: what shipped, the answer to the load-time-corruption question from Task 7 Step 9, anything the browser pass turned up, and the #318 coordination note (tag chips are inert; `frontmatter.py` is touched by both branches, so whichever lands second resolves there).

- [ ] **Step 4: Final verification**

```bash
make check && make test
```

Expected: green, with a test count above the 3234 baseline.

```bash
uv run pytest --durations=25 2>&1 | tail -30
```

Expected: none of the new tests in the top 25. A new test appearing there means a missing mock or a fixed sleep.

- [ ] **Step 5: Commit**

```bash
git add docs/vault.md docs/web-ui.md docs/dev-sessions/2026-07-24-1300-vault-frontmatter-ui/notes.md
git commit -m "$(cat <<'EOF'
docs(vault): document web-UI frontmatter editing and the vault API contract

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Rebase before handing off**

`origin/main` moved twice during the brainstorm alone.

```bash
git fetch origin && git rebase origin/main
make check && make test
```

If `frontmatter.py` conflicts, #318 has landed — keep both sets of additions; they occupy different regions of the file. Stop and report rather than guessing.

Do **not** push or open a PR. Report status and let Les review.

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: `split_frontmatter` + verbatim body writes → Task 1; `merge_frontmatter` relocation → Task 2; the GET contract → Task 3; the `frontmatter` patch field, null deletion, 409 → Task 4; `frontmatter_raw` replace, its four 400 cases, mutual exclusion → Task 5; sidebar summaries → Task 6; `<wiki-metadata>` read-only, `_loaded` flag, `wiki-page` on `body` → Task 7; typed controls, raw editor, debounce-flush ordering, mtime push → Task 8; docs → Task 9. The spec's out-of-scope list is enforced by the Global Constraint forbidding `tags.py` edits.

**Two gaps found and closed while writing.** The spec's error table did not cover a `frontmatter` **patch** arriving against a page whose existing YAML is malformed — `parse_frontmatter` reports `{}`, so the merge would silently discard the block, the exact bug class the spec exists to prevent. Task 4 rejects it with a 400 and a test asserting the file is unchanged. Separately, nothing guarded a raw submission containing a bare `---` line, which `join_frontmatter` would splice into a block terminator and split the file in two on the next read; Task 5 rejects it with a test.

**Placeholder scan.** No TBD/TODO. Every code step carries complete code. `test_vault_read_page`'s modification is spelled out rather than left as "update the assertion." The JS tasks state plainly that no unit-test harness exists and give concrete browser steps with expected outcomes instead of "verify it works."

**Type consistency.** `split_frontmatter` returns `tuple[str | None, str]` in Task 1 and every consumer (Tasks 3, 4, 5, 6) destructures two values. `parse_frontmatter_block` returns `(dict, str | None)` consistently. The wire keys `frontmatter`, `frontmatter_raw`, `frontmatter_error`, `body` are spelled identically in Tasks 3–8; the JS property names `frontmatterRaw` / `frontmatterError` are camelCase throughout and read from the snake_case JSON keys only inside `_fetchPage` and `#putMetadata`. `#flushMetadata` is defined once in Task 8 and called from `_onMetadataRawSave`, `willUpdate`, and `_toggleMode`. `_dump_frontmatter` and `_page_summary` are both defined in the tasks that introduce them (4 and 6).
