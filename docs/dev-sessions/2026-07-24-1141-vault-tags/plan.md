# First-class Vault Tags Implementation Plan (#318)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make vault tags first-class — a shared extraction module, inline `#tags`, tag-filtered search, a `vault_tags()` tool + REST endpoint, and a web UI Tags tab.

**Architecture:** A greenfield `tags.py` is the core primitive (normalize / parse inline / extract-per-file / collect-all / filter); everything else consumes it. Unification is at the index/query layer — journal files keep their daily-file `## timestamp` format. On-demand scanning, no persistent tag index.

**Tech Stack:** Python 3.13, `uv`, pytest (xdist), YAML frontmatter, sqlite-vec embeddings, Starlette (http_server.py) REST, Lit web components.

## Global Constraints

- `uv run` inside the worktree; never bare `python`.
- Fail-open on file IO in scan/extract paths (`except Exception as exc: log.debug(...)`); no bare `except`. Never let a tag scan break a search/turn.
- New runtime state (if any) on dataclasses; no `setattr`/`getattr` of undeclared fields.
- Journal daily-file `## YYYY-MM-DD HH:MM` format and `vault_journal_append`'s signature MUST stay compatible with `read_recent_journal_entries` (#306) — guarded by tests.
- Case-insensitive tags: lowercase canonical key, preserve first-seen display casing; do NOT merge `-`/`_` variants.
- Tag AND-by-default; `any_tag=true` = OR.
- Reuse `frontmatter.parse_frontmatter` / `get_frontmatter_field` — don't reimplement frontmatter parsing/coercion.
- `make check` + `make test` green before each phase commit. Suite baseline ends with 2 known #638 forkpty warnings — introduce no new warnings.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- WebSocket/wire-type changes: none expected. Web UI styling: reach for primitives; tag-qualify custom button rules (Pico gotchas in CLAUDE.md).
- Rebase on `origin/main` before final squash. Update the feature's `docs/` page in the same PR.

## Cross-phase interface: `tags.py` (Phase 1 defines; all later phases consume)

```python
def normalize_tag(tag: str) -> str: ...            # strip leading '#', lowercase, trim
def parse_inline_tags(body: str) -> set[str]: ...  # normalized inline #tags, code excluded
def extract_tags(content: str, source_type: str) -> set[str]: ...  # union for one file, normalized
def collect_all_tags(config) -> dict[str, dict]: ...   # {norm_tag: {"count": int, "display": str, "pages": list[str]}}
def pages_with_tags(config, tags: list[str], any_tag: bool = False) -> list[str]: ...  # vault-rel page paths
```
All tag comparisons use `normalize_tag`. `extract_tags` reads frontmatter `tags:` + (journal only) the `- **tags:**` bullet + inline `#tags`.

---

## Phase 1 — `tags.py` foundation

**Files:**
- Create: `src/decafclaw/tags.py`
- Create: `tests/test_tags.py`

**Interfaces:** Produces the five functions above.

- [ ] **Step 1: Write failing tests** (`tests/test_tags.py`)

```python
import pytest
from decafclaw.tags import (
    normalize_tag, parse_inline_tags, extract_tags,
    collect_all_tags, pages_with_tags,
)

def test_normalize():
    assert normalize_tag("#Rust") == "rust"
    assert normalize_tag("  async ") == "async"
    assert normalize_tag("rust-lang") == "rust-lang"  # hyphen preserved, distinct from "rust"

def test_parse_inline_basic():
    assert parse_inline_tags("working on #rust and #async-io today") == {"rust", "async-io"}

def test_parse_inline_start_of_line_and_slash():
    assert parse_inline_tags("#project/alpha notes") == {"project/alpha"}

def test_parse_inline_rejects_digit_start_and_midword():
    # "#42" (digit start) is not a tag; "a#b" (not preceded by whitespace/SOL) is not
    assert parse_inline_tags("issue #42 and foo a#b") == set()

def test_parse_inline_ignores_code():
    body = "text #real\n```\n#fenced-not-tag\n```\ninline `#inline-not-tag` end"
    assert parse_inline_tags(body) == {"real"}

def test_parse_inline_atx_heading_not_a_tag():
    # "# Heading" (hash + space at SOL) is a markdown heading, not a tag
    assert parse_inline_tags("# Heading\n#realtag") == {"realtag"}

def test_extract_page_frontmatter_plus_inline():
    content = "---\ntags: [Rust, Async]\n---\nbody with #extra tag"
    assert extract_tags(content, "page") == {"rust", "async", "extra"}

def test_extract_journal_bullet_plus_inline():
    content = "## 2026-07-24 10:00\n\n- **tags:** rust, async\n\nsome #extra note"
    assert extract_tags(content, "journal") == {"rust", "async", "extra"}
```

- [ ] **Step 2: Run → fail.** `cd .claude/worktrees/318-vault-tags && uv run pytest tests/test_tags.py -q` → import error.

- [ ] **Step 3: Implement `tags.py`.**

```python
"""First-class vault tags (#318): extraction, normalization, on-demand scan.

Tags come from three sources, unioned at the query layer (not stored
uniformly on disk): page frontmatter `tags:`, the journal `- **tags:**`
bullet, and Obsidian-style inline `#tags` in body prose. All comparisons
use the lowercased canonical form; display casing is the first seen.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from decafclaw.frontmatter import get_frontmatter_field, parse_frontmatter

log = logging.getLogger(__name__)

# Inline #tag: preceded by whitespace/SOL, starts with letter/_/ '/', then
# word chars / - / /. Digit-start excluded so "#42" isn't a tag.
_INLINE_TAG_RE = re.compile(r"(?:(?<=\s)|^)#([A-Za-z_/][\w\-/]*)", re.MULTILINE)
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_JOURNAL_TAGS_BULLET_RE = re.compile(r"^- \*\*tags:\*\* (.+)$", re.MULTILINE)


def normalize_tag(tag: str) -> str:
    return tag.lstrip("#").strip().lower()


def _strip_code(body: str) -> str:
    body = _FENCED_CODE_RE.sub(" ", body)
    return _INLINE_CODE_RE.sub(" ", body)


def parse_inline_tags(body: str) -> set[str]:
    stripped = _strip_code(body)
    return {normalize_tag(m.group(1)) for m in _INLINE_TAG_RE.finditer(stripped)}


def extract_tags(content: str, source_type: str) -> set[str]:
    metadata, body = parse_frontmatter(content)
    tags: set[str] = set()
    for t in get_frontmatter_field(metadata, "tags", []) or []:
        tags.add(normalize_tag(str(t)))
    if source_type == "journal":
        for m in _JOURNAL_TAGS_BULLET_RE.finditer(content):
            for part in m.group(1).split(","):
                if part.strip():
                    tags.add(normalize_tag(part))
    tags |= parse_inline_tags(body)
    tags.discard("")
    return tags
```
Then `collect_all_tags(config)` and `pages_with_tags(config, tags, any_tag=False)`: iterate page dirs + journal dirs (reuse the walk from `embeddings._iter_vault_pages` for pages and `config.vault_agent_journal_dir` for journals — read those to match), call `extract_tags` per file (fail-open per file), aggregate. `collect_all_tags` keys on normalized tag, tracks count (distinct files), first-seen display casing, and vault-relative page paths. `pages_with_tags` returns paths whose extracted tags ⊇ requested (AND) or ∩ (any_tag); normalize the requested tags first.

- [ ] **Step 4: Run → green.** Add tests for `collect_all_tags` counts/display and `pages_with_tags` AND vs `any_tag` over a tmp vault (use the `config` fixture; write page + journal files). Then `make check`.

- [ ] **Step 5: Commit** `feat(vault): tags.py — extraction, normalization, on-demand scan (#318)`.

---

## Phase 2 — inline `#tags` in composite embeddings

**Files:**
- Modify: `src/decafclaw/frontmatter.py` (`build_composite_text`, line ~88)
- Modify: `tests/test_frontmatter.py` (or wherever build_composite_text is tested)

**Interfaces:** Consumes `tags.parse_inline_tags`.

- [ ] **Step 1: Failing test** — `build_composite_text` includes inline `#tags` from the body in the composite metadata text (so they shape embeddings), in addition to frontmatter tags.

```python
def test_composite_includes_inline_tags():
    from decafclaw.frontmatter import build_composite_text
    out = build_composite_text({"tags": ["rust"]}, "body mentioning #async")
    assert "rust" in out and "async" in out
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — in `build_composite_text`, union `parse_inline_tags(body)` into the tags line it already builds from frontmatter. Import `parse_inline_tags` from `decafclaw.tags` at module level (watch for an import cycle: `tags.py` imports `frontmatter`, so `frontmatter` importing `tags` would cycle — if so, do a function-level import inside `build_composite_text` and add a comment noting the cycle-break, per the CLAUDE.md exception).
- [ ] **Step 4: Run → green; `make check`.** Note in the commit body + docs that existing pages need `make reindex` to pick this up.
- [ ] **Step 5: Commit** `feat(vault): fold inline #tags into composite embeddings (#318)`.

---

## Phase 3 — journal inline `#tags` emission

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py` (`tool_vault_journal_append`, ~line 586)
- Modify: `tests/test_vault_tools.py`

- [ ] **Step 1: Failing tests** — after `vault_journal_append(ctx, tags=["rust","async"], content="hi")`: (a) the entry still contains the `- **tags:** rust, async` bullet (back-compat), (b) the entry body also contains inline `#rust #async`, (c) `read_recent_journal_entries` still parses the file into one entry (import it; assert one `RecentJournalEntry` with the expected timestamp). Assert `extract_tags(file_content, "journal")` returns `{"rust","async"}` with no duplication.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — in `tool_vault_journal_append`, after the existing bullet, append a line of inline tags (e.g. `\n#rust #async`) into the entry body before writing. Keep the bullet. Do not change the `## {now:%Y-%m-%d %H:%M}` header or the append-to-daily-file behavior. Mirror the change in the embedded `entry_text` used for indexing so search sees them.
- [ ] **Step 4: Run → green** (incl. an existing `read_recent_journal_entries` test still passing); `make check`.
- [ ] **Step 5: Commit** `feat(vault): journal entries emit inline #tags (#318)`.

---

## Phase 4 — tag-filtered `vault_search`

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py` (`tool_vault_search`, line 655; + its `TOOL_DEFINITIONS` entry)
- Modify: `tests/test_vault_tools.py`
- Create: `evals/vault-tags.yaml`; Modify: `evals/tool_choice/core_overlaps.yaml`

**Interfaces:** Consumes `tags.extract_tags`, `tags.pages_with_tags`.

- [ ] **Step 1: Failing tests** — read the current `tool_vault_search` signature/body first. Cases: (a) `query="x", tags=["async"]` → only results whose `extract_tags` include `async`; (b) `query="", tags=["rust","async"]` → pure filter, AND semantics (only pages with both); (c) `any_tag=True` → OR; (d) tags filter spans page + journal source types. Build a tmp vault with tagged pages + journal entries.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — add `tags: list[str] = []`, `any_tag: bool = False`. When `query` is empty and `tags` given → return `pages_with_tags(...)` results (formatted like existing search output). When both given → run the existing semantic/substring search, then drop candidates whose `extract_tags` don't satisfy the filter. Normalize requested tags. Update the `TOOL_DEFINITIONS` description to document `tags`/`any_tag` tightly (control surface).
- [ ] **Step 4: Run → green; `make check`.**
- [ ] **Step 5: Add evals.** `evals/vault-tags.yaml`: seed tagged pages via `setup.workspace_files`; a case asserting a tag-filtered ask uses `vault_search` with tags and returns the right page (`expect_tool: vault_search`, bounded). Add a `tool_choice` case: "list all the tags I've used" → `vault_tags` (near-miss `vault_search`) — placeholder now, real once Phase 5 lands; if ordering matters, add this case in Phase 5 instead. Run `uv run python -m decafclaw.eval evals/vault-tags.yaml --verbose`.
- [ ] **Step 6: Commit** `feat(vault): tag-filtered vault_search (#318)`.

---

## Phase 5 — `vault_tags()` tool + `/api/vault/tags`

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py` (new `tool_vault_tags` + register in `TOOLS`/`TOOL_DEFINITIONS`)
- Modify: `src/decafclaw/http_server.py` (new `GET /api/vault/tags` route; read existing vault routes ~`_vault_root`/`_vault_source_type` to match auth + response conventions)
- Modify: `tests/test_vault_tools.py`, and the http/api test file (find the existing vault/workspace API tests)
- Modify: `evals/tool_choice/core_overlaps.yaml` (vault_tags vs vault_search)

**Interfaces:** Consumes `tags.collect_all_tags`.

- [ ] **Step 1: Failing tests** — `tool_vault_tags(ctx)` returns tags sorted by count desc with counts (assert shape + order over a seeded vault). API test: `GET /api/vault/tags` returns JSON `{tags: [{tag, count, ...}]}` (mirror an existing vault/workspace API test's client setup).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** the tool (thin wrapper over `collect_all_tags`, ToolResult with `data`) + the route (mirror an existing `/api/vault/*` or `/api/workspace/*` handler for auth/JSON). Register the tool. Tighten description.
- [ ] **Step 4: Run → green; `make check`.** Add the `vault_tags` vs `vault_search` `tool_choice` case; run `make eval-tools`.
- [ ] **Step 5: Commit** `feat(vault): vault_tags tool + /api/vault/tags endpoint (#318)`.

---

## Phase 6 — Tags tab (web UI)

**Files:**
- Modify: the sidebar component that owns the vault/wiki tab — confirm whether it's `web/static/components/vault-sidebar.js` or `conversation-sidebar.js` (grep `_sidebarTab`, `#switchTab`, the `wiki`/`files`/`schedules` tab buttons ~line 489) — add a `tags` tab button + panel there.
- Modify: `web/static/service/*` or wherever `/api/*` fetches live (find the existing vault API client call).
- Reference: `docs/web-ui.md` / `docs/web-ui-design.md` for primitives + row conventions.

- [ ] **Step 1** Add a **Tags** tab button alongside the existing tabs (mirror the `wiki`/`files` button markup + `#switchTab('tags')`). Fetch `/api/vault/tags` on activation; render tags sorted by count as clickable rows (reuse the sidebar row convention — plain `<div @click>` per `reference_sidebar_clickable_row_convention`); clicking a tag lists its pages (from the endpoint's per-tag page list) and opens a page on click (reuse the existing wiki-page open path).
- [ ] **Step 2** `make check-js` (tsc --checkJs) green; `make vendor` if the bundle needs rebuilding.
- [ ] **Step 3: Browser smoke** — Playwright against a local web-only server (`MATTERMOST_ENABLED=false HTTP_PORT=18895`), OR a headless check: load the UI, open the Tags tab, assert tags render + click-through works. NOTE: Playwright can't run while `make dev` is up (shared Chrome cache) — coordinate with Les to pause `make dev`, or use the headless client. Capture a screenshot.
- [ ] **Step 4: Commit** `feat(web): vault Tags tab (#318)`.

---

## Docs (fold into the phases that touch them, or a final sweep)

- `docs/vault.md` — tags section: the three extraction sources, inline `#tag` rules, tag-filtered search, `vault_tags`, journal behavior, and the reindex note for inline-tag embeddings.
- `docs/web-ui.md` — the Tags tab.
- `CLAUDE.md` — key-files entry for `tags.py`.

## Self-review (done at write time)

- **Spec coverage:** tag extractor + normalization (P1), inline `#tags` P4-of-spec (P1 parser + P2 embeddings + P3 journal + P4 search all consume it), tag-filtered search / Part 1 (P4), `vault_tags` + REST / Part 2 backend (P5), Tags tab / Part 2 UI (P6), unified-storage / Part 3 as index-layer + journal emission (P1 extract + P3). ✓
- **Type consistency:** the five `tags.py` signatures in the cross-phase block are used verbatim by P2–P6. `extract_tags(content, source_type)` and `pages_with_tags(config, tags, any_tag)` names stable across phases. ✓
- **Ordering:** P1 before all; P4/P5 before P6 (UI needs the endpoint); P5's `tool_choice` case after the tool exists. ✓
- **Import-cycle risk** flagged in P2 (frontmatter ↔ tags).

## Execution notes

- Subagent-driven, commit per phase, fresh subagent each; each `cd`s into the worktree, uses `uv run`, reads real files before editing, TDD, stops at its commit for review.
- Checkpoint to Les after Phase 1 (defines the shared interface) and before/at Phase 6 (needs `make dev` paused for the Playwright smoke).
- Highest-risk phase: P3 (journal format vs #306). Give its review extra attention.
