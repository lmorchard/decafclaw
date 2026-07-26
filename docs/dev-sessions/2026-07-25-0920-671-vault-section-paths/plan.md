# vault_section path resolution — Implementation Plan

**Goal:** Let section paths resolve as bare titles or partial paths instead of
requiring the page-H1 root, fail loudly (not silently) on ambiguity, and make
the miss message self-correcting.

**Approach:** Exact full-path match keeps first priority; on a miss, fall back
to a unique *suffix* match against every section's full path. Ambiguity returns
`None` so no mutation proceeds on a guess. A separate `section_candidates()`
lets the error sites distinguish ambiguous from missing without changing
`find_section`'s signature at 13 call sites.

**Tech stack:** Python 3.13, pytest + pytest-asyncio, `uv run`.

**Working directory for every command below:**
`/Users/lorchard/devel/decafclaw/.claude/worktrees/671-vault-section-paths`
Use an absolute `cd` in each verification command — a stray `cd` to the main
clone makes `make test` report a wrong-tree pass count and `pytest <newfile>`
say "no tests ran", both of which look like success.

**Baseline:** `make test` in this worktree = **3469 passed, 2 skipped**.

---

## Phase 1: Suffix resolution and `#` tolerance in the matcher

Makes `Background`, `Archive/Background`, and `## Background` all resolve,
while ambiguity resolves to nothing. This is the whole user-visible fix; the
later phases are diagnostics and docs.

**Files:**
- Modify: `src/decafclaw/skills/vault/_sections.py` — `normalize_title`,
  `Document.find_section`, new module-level `_find_by_suffix`
- Test: `tests/test_vault_sections_helpers.py` — add to the existing file
  (it already covers `Document` round-trip and `find_section` by path)

**Key changes:**

`normalize_title` (currently `_sections.py:31-35`) — strip leading heading
hashes as well:

```python
def normalize_title(raw: str) -> str:
    """Strip wiki-links and leading heading hashes, then lowercase for matching."""
    stripped = WIKILINK_RE.sub(r"\1", raw)
    return stripped.lstrip("#").strip().lower()
```

New module-level helper, next to `_walk_path` (`:548-558`):

```python
def _find_by_suffix(sections: list[Section], parts: list[str]) -> list[Section]:
    """Every section whose full path ends with ``parts``.

    Lets a caller address a section by a bare title or any trailing portion of
    its path, instead of rooting every path at the page H1 (#671). Returns all
    matches so the caller can distinguish unique from ambiguous.
    """
    matches: list[Section] = []

    def _walk(secs: list[Section], trail: list[str]) -> None:
        for sec in secs:
            current = trail + [sec.normalized_title]
            if current[-len(parts):] == parts:
                matches.append(sec)
            _walk(sec.children, current)

    _walk(sections, [])
    return matches
```

`Document.find_section` (`:155-158`) — exact first, then unique suffix:

```python
    def find_section(self, path: str) -> Section | None:
        """Resolve a section path.

        Tries the exact rooted path first, then falls back to a unique suffix
        match so a bare title or partial path resolves (#671). Ambiguous paths
        deliberately return None — these back mutating operations, so a wrong
        guess silently edits the wrong section. Callers wanting to tell
        ambiguous from missing use ``section_candidates``.
        """
        self._ensure_parsed()
        parts = [normalize_title(p) for p in path.split("/") if p.strip()]
        if not parts:
            return None
        exact = _walk_path(self._sections, parts)
        if exact is not None:
            return exact
        matches = _find_by_suffix(self._sections, parts)
        return matches[0] if len(matches) == 1 else None
```

Note this replaces the old `p.strip().lower()` with `normalize_title(p)` so the
`#` stripping applies per path segment, and drops empty segments so `/Background`
and `Background/` behave.

**Tests to add** (`tests/test_vault_sections_helpers.py`, matching the existing
plain-function style — no fixture needed):

```python
NESTED = (
    "# Project Notes\n\n"
    "## Background\n\nbg\n\n"
    "## Archive\n\n### Background\n\nold bg\n\n"
    "## TODO\n\n- Old item\n"
)


def test_bare_title_resolves_without_h1_root():
    """#671: a ## heading was only addressable as '<H1>/<Section>'."""
    doc = Document.from_text("# Project Notes\n\n## Background\n\nbg\n\n## TODO\n\n- x\n")
    assert doc.find_section("Background") is not None
    assert doc.find_section("TODO") is not None
    # The rooted form keeps working.
    assert doc.find_section("Project Notes/Background") is not None


def test_partial_path_resolves():
    doc = Document.from_text(NESTED)
    sec = doc.find_section("Archive/Background")
    assert sec is not None and sec.level == 3


def test_exact_path_wins_over_suffix():
    doc = Document.from_text(NESTED)
    sec = doc.find_section("Project Notes/Background")
    assert sec is not None and sec.level == 2


def test_ambiguous_bare_title_resolves_to_nothing():
    """Two sections titled 'Background' — a guess would edit the wrong one."""
    doc = Document.from_text(NESTED)
    assert doc.find_section("Background") is None


def test_heading_hashes_are_tolerated():
    doc = Document.from_text("# Project Notes\n\n## Background\n\nbg\n")
    assert doc.find_section("## Background") is not None
    assert doc.find_section("#Background") is not None


def test_add_section_after_bare_title():
    """The operation from the #671 repro."""
    doc = Document.from_text("# Project Notes\n\n## Background\n\nbg\n\n## TODO\n\n- x\n")
    assert doc.add_section("Status", level=2, after="Background") is True
    assert "## Status" in doc.to_text()


def test_empty_and_slash_only_paths_resolve_to_nothing():
    doc = Document.from_text(NESTED)
    assert doc.find_section("") is None
    assert doc.find_section("/") is None
```

**Order of work (TDD):** write the tests, run them, confirm
`test_bare_title_resolves_without_h1_root` and friends fail; then apply the
three `_sections.py` edits; re-run.

**Verification — automated:**
- [x] `cd <worktree> && uv run pytest tests/test_vault_sections_helpers.py -v`
      fails before the edits, on the bare-title assertions — **4 failed, 5 passed**
- [x] same command passes after (9 tests: 2 existing + 7 new) — **9 passed in 2.40s**
- [x] `cd <worktree> && uv run pytest tests/test_vault_section_tools.py tests/test_vault_tools.py -v` passes — the existing rooted-path callers are unaffected — **159 passed**
- [x] `cd <worktree> && make test` passes, count >= 3476 (baseline 3469 + 7)
      — **3476 passed, 2 skipped**
- [x] `cd <worktree> && make check` passes — **exit 0**

**Verification — manual:**
- [x] Re-run the issue's repro by hand and confirm every spelling now resolves
      except the genuinely ambiguous one — all four `add_section` spellings from
      the issue return `True`; `find_section('TODO')` resolves; the rooted form
      still works

**Note on one test's strength:** `test_ambiguous_bare_title_resolves_to_nothing`
passed in the RED run too, for the wrong reason (everything returned `None`
before the fix). It is meaningful only alongside the suffix tests, where it
guards against the fallback over-reaching. Kept deliberately.

---

## Phase 2: Distinguish ambiguous from missing, and say so

Turns the two dead-end messages into self-correcting ones. `find_section`
already returns `None` for both cases after Phase 1; this phase gives the error
sites a way to tell them apart.

**Files:**
- Modify: `src/decafclaw/skills/vault/_sections.py` — add
  `Document.section_candidates`, `Document.all_section_paths`, and a
  module-level `describe_section_miss`
- Modify: `src/decafclaw/skills/vault/tools.py` — use it at `:1294`, `:1421`,
  `:1434`, `:1446`
- Modify: `src/decafclaw/skills/vault/_sections.py:612` — the bare-string error
  from `_insert_into_doc`
- Test: `tests/test_vault_sections_helpers.py`, `tests/test_vault_section_tools.py`

**Key changes:**

First, let `_section_path` (`:568-584`) render real titles as well as
normalized ones. It currently builds paths from `normalized_title`, so an error
message would read `project notes/background` — technically valid input, but
poor to show a human. Existing callers keep the normalized form via the
default; only the new diagnostics opt in:

```python
def _section_path(
    sec: Section, top_sections: list[Section], *, display: bool = False,
) -> str:
    """Full slash path to ``sec``.

    ``display=True`` renders the headings' real titles, for error messages and
    anything else a human reads. The default normalized form stays the
    round-trippable one used for re-resolution after a mutation.
    """
    def _find(sections: list[Section], target_line: int, prefix: str) -> str | None:
        for s in sections:
            name = s.title.strip() if display else s.normalized_title
            current = f"{prefix}/{name}" if prefix else name
            if s.heading_line == target_line:
                return current
            found = _find(s.children, target_line, current)
            if found:
                return found
        return None
    fallback = sec.title.strip() if display else sec.normalized_title
    return _find(top_sections, sec.heading_line, "") or fallback
```

Both forms are accepted as input, since `find_section` normalizes whatever it
is given.

On `Document`:

```python
    def section_candidates(self, path: str) -> list[str]:
        """Full paths of every section matching ``path`` as a suffix.

        Empty means nothing matched; more than one means the path was
        ambiguous, which is why ``find_section`` returned None. Paths are
        rendered with real titles for display; both forms resolve.
        """
        self._ensure_parsed()
        parts = [normalize_title(p) for p in path.split("/") if p.strip()]
        if not parts:
            return []
        return [
            _section_path(sec, self._sections, display=True)
            for sec in _find_by_suffix(self._sections, parts)
        ]

    def all_section_paths(self) -> list[str]:
        """Full slash path of every section, in document order, for display."""
        self._ensure_parsed()
        return [
            _section_path(sec, self._sections, display=True)
            for _depth, sec in self.list_sections()
        ]
```

Module-level, so both `tools.py` and `_insert_into_doc` share one wording.
Returns the *inner* message with no `[error: ]` wrapper, because
`_insert_into_doc` returns bare strings that `tools.py:1524` wraps:

```python
# Cap on paths listed in a "not found" message. Pages rarely have more; a
# truncated list still teaches the path shape, which is the point.
_MAX_LISTED_PATHS = 20


def describe_section_miss(doc: "Document", path: str) -> str:
    """Explain why ``path`` didn't resolve, with enough detail to retry.

    Ambiguous paths list every candidate; missing ones list the page's known
    paths. Returns the message body without the ``[error: ]`` wrapper.
    """
    candidates = doc.section_candidates(path)
    if len(candidates) > 1:
        listed = "\n  ".join(candidates)
        return (
            f"ambiguous section path {path!r} matches {len(candidates)} sections:\n"
            f"  {listed}\n"
            f"Use a longer path to disambiguate."
        )
    known = doc.all_section_paths()
    if not known:
        return f"section not found: {path!r} (page has no sections)"
    shown = known[:_MAX_LISTED_PATHS]
    listed = "\n  ".join(shown)
    more = (
        "" if len(known) <= _MAX_LISTED_PATHS
        else f"\n  … and {len(known) - _MAX_LISTED_PATHS} more"
    )
    return f"section not found: {path!r}. Known paths:\n  {listed}{more}"
```

Call sites in `tools.py` — all four become the same shape. Import
`describe_section_miss` alongside the existing `Document, _insert_into_doc`
import at `tools.py:30`:

```python
        return ToolResult(text=f"[error: {describe_section_miss(doc, section)}]")
```

`tools.py:1421` currently reads `[error: target section not found]` with no
path at all. It sits in the `add` branch, where the failed path is whichever of
`after` / `before` / `parent` was supplied:

```python
        target = after or before or parent or ""
        return ToolResult(text=f"[error: {describe_section_miss(doc, target)}]")
```

`_sections.py:612` inside `_insert_into_doc`:

```python
            return describe_section_miss(doc, to_section)
```

**Tests to add:**

```python
# tests/test_vault_sections_helpers.py

def test_section_candidates_lists_ambiguous_matches():
    doc = Document.from_text(NESTED)
    got = doc.section_candidates("Background")
    assert got == ["Project Notes/Background", "Project Notes/Archive/Background"]


def test_section_candidates_empty_when_missing():
    doc = Document.from_text(NESTED)
    assert doc.section_candidates("Nonexistent") == []


def test_candidate_paths_are_valid_input():
    """Whatever the error offers must resolve when pasted back."""
    doc = Document.from_text(NESTED)
    for candidate in doc.section_candidates("Background"):
        assert doc.find_section(candidate) is not None


def test_all_section_paths_in_document_order():
    doc = Document.from_text(NESTED)
    assert doc.all_section_paths() == [
        "Project Notes",
        "Project Notes/Background",
        "Project Notes/Archive",
        "Project Notes/Archive/Background",
        "Project Notes/TODO",
    ]


def test_describe_section_miss_ambiguous_names_candidates():
    doc = Document.from_text(NESTED)
    msg = describe_section_miss(doc, "Background")
    assert "ambiguous" in msg
    assert "Project Notes/Background" in msg
    assert "Project Notes/Archive/Background" in msg


def test_describe_section_miss_missing_lists_known_paths():
    doc = Document.from_text(NESTED)
    msg = describe_section_miss(doc, "Nope")
    assert "not found" in msg
    assert "Project Notes/TODO" in msg


def test_describe_section_miss_truncates_long_lists():
    body = "# Root\n\n" + "".join(f"## S{i}\n\ntext\n\n" for i in range(30))
    doc = Document.from_text(body)
    msg = describe_section_miss(doc, "Nope")
    assert "and 11 more" in msg   # 31 sections total, 20 shown
```

```python
# tests/test_vault_section_tools.py — follows the existing vault_ctx / _write_note style

AMBIGUOUS_TEXT = (
    "# Top\n\n## Notes\n\na\n\n## Archive\n\n### Notes\n\nb\n"
)


@pytest.mark.asyncio
async def test_section_error_lists_known_paths(vault_ctx):
    _write_note(vault_ctx)
    result = await tool_vault_section(
        vault_ctx, page="agent/pages/note", action="rename",
        section="Nonexistent", title="X",
    )
    assert "not found" in result.text
    assert "Top/Sub A" in result.text


@pytest.mark.asyncio
async def test_section_add_after_bare_title(vault_ctx):
    """#671: 'Sub A' had to be spelled 'Top/Sub A'."""
    note_path = _write_note(vault_ctx)
    with patch("decafclaw.skills.vault.tools._reindex_page", new=AsyncMock()):
        result = await tool_vault_section(
            vault_ctx, page="agent/pages/note", action="add",
            title="Status", level=2, after="Sub A",
        )
    assert "[error" not in result.text
    assert "## Status" in note_path.read_text()


@pytest.mark.asyncio
async def test_section_ambiguous_path_errors_with_candidates(vault_ctx):
    note_dir = vault_ctx.config.vault_root / "agent" / "pages"
    note_dir.mkdir(parents=True, exist_ok=True)
    (note_dir / "amb.md").write_text(AMBIGUOUS_TEXT)
    result = await tool_vault_section(
        vault_ctx, page="agent/pages/amb", action="rename",
        section="Notes", title="X",
    )
    assert "ambiguous" in result.text
    assert "Top/Notes" in result.text
    assert "Top/Archive/Notes" in result.text
```

**Verification — automated:**
- [x] `cd <worktree> && uv run pytest tests/test_vault_sections_helpers.py tests/test_vault_section_tools.py -v` passes
      — **17 + 22 = 39 passed**
- [x] `cd <worktree> && grep -rn "target section not found" src/` returns nothing —
      the no-path-echoed message is gone — **exit 1, no matches**
- [x] `cd <worktree> && make test` passes, count >= 3485 — **3488 passed, 2 skipped**
- [x] `cd <worktree> && make check` passes — **exit 0**

**Verification — manual:**
- [x] Read one ambiguous and one missing error message end to end — does each
      tell you exactly what to type next? — yes:

      ```
      ambiguous section path 'Background' matches 2 sections:
        Project Notes/Background
        Project Notes/Archive/Background
      Use a longer path to disambiguate.

      section not found: 'Statuz'. Known paths:
        Project Notes
        Project Notes/Background
        Project Notes/Archive
        Project Notes/Archive/Background
        Project Notes/TODO
      ```

---

## Phase 3: Tool descriptions and docs

The descriptions currently say "Slash-separated section path (e.g. 'top/first')",
which is what led the caller to believe a bare title was invalid — and gave no
hint the first segment had to be the page H1.

**TDD opt-out:** description and doc text, no behavior. Phases 1-2 cover the
behavior.

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py` — `:1991`, `:2038`, `:2083`,
  `:2106`, `:2113`, `:2120`
- Modify: `docs/vault.md` — section-addressing note

**Key changes:** each of the six parameter descriptions moves from
"Slash-separated section path (e.g. 'top/first')" to wording that states the
bare form is fine and the path is a suffix. For `section`:

```python
                            "Section path — a bare heading title ('Background'), "
                            "a partial path ('Archive/Background'), or the full "
                            "path from the page title "
                            "('Project Notes/Archive/Background'). Must match "
                            "exactly one section; if it matches several the "
                            "error lists them. Required for remove, rename, move."
```

`after` / `before` / `parent` get the same "bare title or partial path" phrasing
with their own role sentence. `docs/vault.md` gains a short paragraph under the
section-tools description covering the same three forms plus the ambiguity rule.

**No `tool_choice` eval case.** CLAUDE.md asks for one on a "new or sharpened
tool description", but that convention targets *disambiguation between
overlapping tools* — which tool the model picks. These are parameter
descriptions inside a single tool; `tool_choice` cases assert on tool name only
and cannot observe an argument value. Phase 4 covers the behavior instead.

**Verification — automated:**
- [x] `cd <worktree> && make check` passes — **exit 0**
- [x] `cd <worktree> && grep -c "top/first" src/decafclaw/skills/vault/tools.py`
      returns 0 — **0**; the other stale examples (`top/sub a`, `today/inbox`)
      are gone too
- [x] `cd <worktree> && make test` passes — **3488 passed, 2 skipped**

**Verification — manual:**
- [x] Read the four param descriptions together — consistent, and each states
      the bare-title form — verified by rendering `TOOL_DEFINITIONS`; all six
      section-path params (not four — `vault_show_sections.section` and
      `vault_move_lines.to_section` too) now share one `SECTION_PATH_HELP`
      constant, so they cannot drift apart

**Adaptation:** rather than editing six description strings independently, the
shared sentence became a module-level `SECTION_PATH_HELP` constant that each
param concatenates. Six hand-maintained copies of the same explanation is the
drift shape CLAUDE.md warns about.

---

## Phase 4: Confirm the agent benefits

The point of the fix is fewer round-trips. The existing eval case already
exercises this exact path and passed at **4 tool calls** in the
`2026-07-24-2323` bundle (2 of them spent discovering the path); it should now
need fewer.

**TDD opt-out:** measurement against real LLM calls; no code changes.

**Files:**
- Modify: `docs/dev-sessions/2026-07-25-0920-671-vault-section-paths/notes.md`

**Commands:**

```bash
cd /Users/lorchard/devel/decafclaw/.claude/worktrees/671-vault-section-paths
uv run python -m decafclaw.eval evals/vault.yaml
```

Record per-case pass/fail and the tool-call count for "adds a section without
rewriting other sections", against the 4 from `2026-07-24-2323`.

**Do not tune anything to chase a number.** Per spec the eval case and its
`max_tool_errors: 1` bound stay untouched. A tool-call count that doesn't drop
is a finding to record, not a reason to edit the eval.

**Note on flakiness:** a single eval run is not evidence of a stable count.
If the count looks unchanged or worse, re-run the file before drawing any
conclusion — three separate cases in the last session moved between runs.

**Verification — automated:**
- [!] `evals/vault.yaml` — "adds a section without rewriting other sections"
      passes — **2/4 across four runs, not reliably.** The #671 defect *is*
      fixed: the bare title `after: "Background"` was accepted in every run,
      including the failures. What blocks it now is a different, pre-existing
      gap — `vault_section add` can't set the new section's body — addressed in
      Phase 5.
- [x] Its tool-call count recorded and compared against 4 (re-run once if it
      hasn't dropped, before concluding) — **dropped to 2 on passing runs**;
      re-ran 3× before concluding, per the plan
- [!] No other case in the file regresses vs the 6/6 it scored on 2026-07-24 —
      **case 1 (`saves user-level fact … on 'remember'`) failed once**, picking
      `vault_write` over `vault_journal_append`. That case sampled 10/10 two
      days ago and nothing in this branch touches either description; treated
      as noise-floor, recorded in `notes.md`, not chased.

**Verification — manual:**
- [x] Les reviews the before/after tool-call comparison — reviewed; scoped the
      `content` gap in as Phase 5 rather than deferring it

---

## Phase 5: let `vault_section add` set the new section's body

Closes the gap Phase 4 surfaced. `Document.add_section` has always accepted
`content`; the tool never exposed it, so "add a section titled X with body Y"
needs a second operation — and that second operation is where the agent goes
wrong (a full-page `vault_write` that mangles layout, or an error from guessing
`content=` at the tool).

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py` — `tool_vault_section` signature,
  the `add` branch, and the schema
- Test: `tests/test_vault_section_tools.py`

**Key changes:**

Signature gains the parameter, defaulting to `None` so existing callers are
unaffected:

```python
async def tool_vault_section(
    ctx,
    page: str,
    action: str,
    section: str | None = None,
    title: str | None = None,
    level: int = 1,
    content: str | None = None,
    after: str | None = None,
    before: str | None = None,
    parent: str | None = None,
) -> ToolResult:
```

The `add` branch passes it through — `add_section` already defaults to `""`:

```python
        added = doc.add_section(
            title, level=level, content=content or "",
            after=after, before=before, parent=parent,
        )
```

Schema entry alongside `title` / `level`:

```python
                    "content": {
                        "type": "string",
                        "description": (
                            "Body text for the new section. Only used by add; "
                            "omit for an empty section. Saves a second call — "
                            "there is no need to add the section and then "
                            "rewrite the page to fill it."
                        ),
                    },
```

**Tests to add:**

```python
@pytest.mark.asyncio
async def test_section_add_with_content(vault_ctx):
    """One call should both create the section and fill it."""
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    (agent_pages / "note.md").write_text(
        "# Project Notes\n\n## Background\n\nkeep me\n\n## TODO\n\n- Old item\n"
    )
    result = await tool_vault_section(
        vault_ctx, page="agent/pages/note", action="add",
        title="Status", level=2, content="Working on it.", after="Background",
    )
    assert "[error" not in result.text.lower()
    text = (agent_pages / "note.md").read_text()
    assert "## Status" in text
    assert "Working on it." in text
    # Ordering and the untouched sections survive.
    assert text.index("## Background") < text.index("## Status") < text.index("## TODO")
    assert "keep me" in text
    assert "- Old item" in text


@pytest.mark.asyncio
async def test_section_add_without_content_still_empty(vault_ctx):
    """content is optional — omitting it keeps the old behavior."""
    vault = vault_ctx.config.vault_root
    agent_pages = vault / "agent" / "pages"
    agent_pages.mkdir(parents=True)
    (agent_pages / "note.md").write_text("# Top\n\n## First\n")
    result = await tool_vault_section(
        vault_ctx, page="agent/pages/note", action="add",
        title="Second", level=2, after="First",
    )
    assert "[error" not in result.text.lower()
    assert "## Second" in (agent_pages / "note.md").read_text()
```

**Verification — automated:**
- [x] `cd <worktree> && uv run pytest tests/test_vault_section_tools.py -v` passes
      — **26 passed**
- [x] `cd <worktree> && make test` passes, count >= 3490 — **3499 passed, 2 skipped**
- [x] `cd <worktree> && make check` passes — **exit 0**
- [!] `cd <worktree> && uv run python -m decafclaw.eval tmp/addsec.yaml` — the
      3-rep repro. Record the pass count and call count; **re-run before
      concluding** either way, since the case measured 2/4 pre-Phase-5.
      — **Phase 5 alone made it WORSE: 0/3.** Root-caused to the `level=1`
      default, fixed in Phase 6 below. Final: **5/6 across two runs**, best runs
      at 1–2 calls vs 4 at baseline.

**Verification — manual:**
- [ ] Les reviews the final eval numbers, including whether the case is now
      reliable rather than merely better

---

## Phase 6: infer heading level from the anchor

Added mid-execution. Phase 5 made the one-call path viable, which made the
agent hit `level`'s default of 1 far more often — inserting an H1 mid-page,
which silently reparents every following section. Les scoped it in after the
0/3 measurement.

**Files:**
- Modify: `src/decafclaw/skills/vault/_sections.py` — `Document.add_section`
- Modify: `src/decafclaw/skills/vault/tools.py` — signature, the `level`
  validation guard, and the `level` description
- Test: `tests/test_vault_sections_helpers.py`, `tests/test_vault_section_tools.py`

**Key change:** `level: int | None = None`; resolved from the anchor —
`after`/`before` → the anchor's level (sibling), `parent` → one below, no
anchor → 1. Explicit levels still win. The anchor is resolved once and drives
both the insertion point and the default.

**Verification — automated:**
- [x] `cd <worktree> && uv run pytest tests/test_vault_sections_helpers.py tests/test_vault_section_tools.py -v`
      — **24 + 26 = 50 passed**
- [x] `cd <worktree> && make test` — **3499 passed, 2 skipped**
- [x] `cd <worktree> && make check` — **exit 0**
- [x] Eval repro re-run twice — **3/3 then 2/3 = 5/6**, vs 2/4 before Phase 5

**Verification — manual:**
- [ ] Les reviews the tree-integrity behavior (a section added after `##`
      should be a sibling, not a new H1)

**Two defects found by running the real thing, not the unit tests:**

1. The tool guarded `not isinstance(level, int)` before `level` became
   optional, so omitting it failed with `level must be between 1 and 6, got
   None`. The Phase 6 unit tests call `Document.add_section` directly and sailed
   straight past the tool-layer guard. Fixed, plus two tool-level tests.
2. Phase 2's own ambiguity message was a dead end when two headings share a
   path — it listed identical candidates under "Use a longer path to
   disambiguate", advice that cannot be followed. The agent looped and blew its
   call budget. Duplicate paths now get a distinct message.

---

## Plan self-review

**Spec coverage:**

| spec "Desired end state" item | phase |
|---|---|
| 1. Any trailing portion of a path resolves | 1 |
| 2. Exact full-path matches still win | 1 (`test_exact_path_wins_over_suffix`) |
| 3. Ambiguity fails, error names candidates | 1 (returns None) + 2 (message) |
| 4. Missing path error lists known paths | 2 |
| 5. `normalize_title` strips leading `#` | 1 |
| 6. Descriptions state bare/partial is accepted | 3 |

Design decisions all land: suffix matching (Phase 1 `_find_by_suffix`),
ambiguity-as-error (Phase 1 `None` + Phase 2 message), unchanged `find_section`
signature with a companion `section_candidates` (Phases 1-2 — no call site
outside `find_section` itself is touched), `#` stripping in `normalize_title`
(Phase 1). The spec's open question is answered by `_MAX_LISTED_PATHS = 20` with
a `… and N more` tail, tested.

**Placeholder scan:** no TBD. Every test body and command is written out.

**Type consistency:** `_find_by_suffix(sections: list[Section], parts: list[str])
-> list[Section]` is defined in Phase 1 and consumed by `section_candidates` in
Phase 2 with the same signature. `_section_path` gains a keyword-only
`display: bool = False` in Phase 2; its two existing callers (`:282` re-resolve,
and the old body) keep the default, so only the new diagnostics see real
titles. `list_sections()` returns `list[tuple[int, Section]]` (`:160`), which
`all_section_paths` unpacks as `_depth, sec`.
`describe_section_miss(doc, path) -> str` returns an unwrapped message, matching
both the `f"[error: {err}]"` wrap at `tools.py:1524` and the direct
`f"[error: {...}]"` at the four `tools.py` sites.

**Single-observation assertions:** the only measured number carried into a
checkbox is the tool-call count in Phase 4, and that checkbox explicitly says to
re-run before concluding. The 4-call figure is labelled as coming from one
specific bundle rather than stated as the case's stable cost.

**One risk, accepted:** `normalize_title` now strips leading `#` from *section
titles* too, so a heading literally named `## #hashtag` normalizes to `hashtag`.
It applies symmetrically to stored titles and queries, so lookups stay
consistent; only a page with two headings differing solely by a leading `#`
would newly collide. Judged not worth guarding.
