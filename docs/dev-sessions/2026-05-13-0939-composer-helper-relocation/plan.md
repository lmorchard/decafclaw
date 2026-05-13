# Context Composer Helper Relocation — Implementation Plan

**Goal:** Move the wiki/page-injection helpers and `_resolve_attachments` out of `agent.py` to honest homes so `context_composer.py` stops importing underscore-prefixed names from `agent.py`.

**Approach:** Pure relocation + rename (drop the `_` prefix). Wiki helpers land in `memory_context.py` (already owns wiki-link handling: `_WIKI_LINK_RE` + `_expand_graph_links`). Attachment helper lands in `attachments.py` (already owns `read_attachment_base64`). Both `agent.py` and `context_composer.py` import from the new homes. No backwards-compat shims.

**Tech stack:** Python 3 stdlib + existing decafclaw modules.

**Scope note:** Sub-task #1 from the spec (updating the `_collect_all_tool_defs` import target) is **deferred** because `tool_definitions.py` does not yet exist on `origin/main` (issue #438 has not merged). This plan covers sub-tasks #2 (wiki helpers) and #3 (attachments). A follow-up issue will be filed after this PR opens.

---

## Phase 1: Relocate `_resolve_attachments` → `attachments.resolve_attachments`

Move the multimodal-content builder from `agent.py:65–110` into `attachments.py` where its only collaborator (`read_attachment_base64`) already lives. Drop the underscore. Update the two import sites (`agent.py` internal uses, `context_composer.py:269`) and the dedicated test file's import path.

**Files:**
- Modify: `src/decafclaw/attachments.py` — append a new top-level `resolve_attachments(config, message: dict) -> dict` function (body copied verbatim from `agent.py:_resolve_attachments`, minus the function-level `from .attachments import read_attachment_base64` since the call is now intra-module). The module already imports `base64`, `logging`, etc.; no new module-level imports beyond what is already present.
- Modify: `src/decafclaw/agent.py` — delete the `_resolve_attachments` function definition (lines 65–110). Update any internal use to `from .attachments import resolve_attachments`. Audit: grep showed only `context_composer.py` and the test file used the name externally; internal agent.py use, if any, will surface during edit — none was found in the read.
- Modify: `src/decafclaw/context_composer.py` — change `from .agent import _resolve_attachments` (line 269) to `from .attachments import resolve_attachments`. Update the call site (line 425): `[_resolve_attachments(config, m) for m in llm_history]` → `[resolve_attachments(config, m) for m in llm_history]`.
- Modify: `tests/test_resolve_attachments.py` — change `from decafclaw.agent import _resolve_attachments` (line 5) to `from decafclaw.attachments import resolve_attachments`. Update three call sites (lines 12, 28, 48) to use the new name.
- Modify: `src/decafclaw/llm/providers/vertex.py` — update the two docstring/comment mentions of `_resolve_attachments` (lines 397, 403) to `resolve_attachments` so the dangling reference doesn't rot.
- Modify: `tests/test_vertex_translation.py` — update the docstring mention of `_resolve_attachments` at line 463 to `resolve_attachments`.

**Key changes:**
- `resolve_attachments(config, message: dict) -> dict` — new public function in `attachments.py` (body identical to the old `_resolve_attachments`).

```python
# src/decafclaw/attachments.py — appended after read_attachment_base64

def resolve_attachments(config, message: dict) -> dict:
    """Transform a message with attachments into multimodal content for the LLM.

    Messages without attachments pass through unchanged. The archive stores
    plain text + attachment metadata; this builds the ephemeral content array.
    """
    atts = message.get("attachments")
    if not atts:
        return message

    content_parts: list[dict] = []
    text = message.get("content", "")
    if text:
        content_parts.append({"type": "text", "text": text})

    for att in atts:
        b64_data = read_attachment_base64(config, att)
        if b64_data is None:
            content_parts.append({
                "type": "text",
                "text": f"[attachment missing: {att.get('filename', '?')}]",
            })
            continue

        mime = att.get("mime_type", "application/octet-stream")
        # TODO(#137): MIME type is client-supplied — validate with magic bytes
        # server-side to prevent non-images from being base64-embedded
        if mime.startswith("image/"):
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64_data}"},
            })
        else:
            content_parts.append({
                "type": "text",
                "text": f"[file: {att.get('filename', '?')} ({mime})]",
            })

    result = {k: v for k, v in message.items() if k != "attachments"}
    result["content"] = content_parts
    return result
```

**Test-first approach:** The pre-existing `tests/test_resolve_attachments.py` already covers this function (pass-through, image, missing-file, non-image cases). The TDD step is to (a) update the test imports to the *new* path first, (b) confirm tests now fail (import error from a not-yet-created `resolve_attachments`), then (c) implement the move. This is the cleanest available "failing test first" for a pure relocation.

**Verification — automated:**
- [x] `grep -rn "_resolve_attachments" src/ tests/` returns no matches.
- [x] `pytest tests/test_resolve_attachments.py -v` passes (3 tests).
- [x] `pytest tests/test_vertex_translation.py -v` passes.
- [x] `make lint` passes.
- [x] `make check` passes.

**Verification — manual:**
- [x] Skim the diff of `attachments.py` and `agent.py` to confirm the move is byte-for-byte (modulo the dropped function-level `read_attachment_base64` import which is now intra-module).

---

## Phase 2: Relocate wiki helpers → `memory_context.py`

Move three helpers and their private regex from `agent.py:896–952` into `memory_context.py` where wiki-link handling already lives (`_WIKI_LINK_RE`, `_expand_graph_links`). Drop the underscore on each public name. Update the import sites (`context_composer.py:725`) and update existing tests to the new path.

**Files:**
- Modify: `src/decafclaw/memory_context.py` — add a new section "Wiki references — @[[Page]] mentions" (delimiter comment in the established style). Add three new top-level functions: `parse_wiki_references`, `read_wiki_page`, `get_already_injected_pages`. Add module-level `_WIKI_MENTION_RE = re.compile(r'@\[\[([^\]]+)\]\]')` (separate from the existing `_WIKI_LINK_RE` since `@[[...]]` mentions and bare `[[...]]` wiki-links are distinct concepts — preserve the distinction). `resolve_page` from `.skills.vault.tools` is needed; mirror agent.py's pattern of importing it inside `read_wiki_page` (function-local) since `memory_context._expand_graph_links` already does the same thing — keeps the lazy-import pattern consistent within this module and avoids any new import-cycle risk.
- Modify: `src/decafclaw/agent.py` — delete the wiki helper section (`# -- Wiki context helpers ---`, `_WIKI_MENTION_RE`, `_parse_wiki_references`, `_read_wiki_page`, `_get_already_injected_pages`, lines 896–952). The `import re as _re` at line 18 is used elsewhere in agent.py — verify with grep and keep it if so; remove only if `_WIKI_MENTION_RE` was its sole consumer.
- Modify: `src/decafclaw/context_composer.py` — change `from .agent import _get_already_injected_pages, _parse_wiki_references, _read_wiki_page` (line 725) to `from .memory_context import get_already_injected_pages, parse_wiki_references, read_wiki_page`. Update the three call sites at lines 731, 735, 743 to drop the underscores.
- Modify: `tests/test_wiki_context.py` — update the imports at lines 6–8 from `from decafclaw.agent import _get_already_injected_pages, _parse_wiki_references, _read_wiki_page` to `from decafclaw.memory_context import get_already_injected_pages, parse_wiki_references, read_wiki_page`. Update all in-test references (lines 16, 22, 30, 38, 45, 53, 59, 73, 80, 85, 92, 102, 110) to drop the underscore.
- Modify: `tests/test_vault_tools.py` — three references at lines 867, 869, 873, 875, 879, 881. Update `from decafclaw.agent import _WIKI_MENTION_RE` → `from decafclaw.memory_context import _WIKI_MENTION_RE` (regex stays underscore-prefixed since it's a private module global — tests reaching into it is acceptable, mirroring how `test_memory_context` already touches `_WIKI_LINK_RE` if any do). Update `_parse_wiki_references` → `parse_wiki_references` and adjust the import path.
- Modify: `tests/test_context_composer.py` — three patch paths at lines 396, 398, 416. Update `patch("decafclaw.agent._parse_wiki_references", ...)` → `patch("decafclaw.memory_context.parse_wiki_references", ...)` and `patch("decafclaw.agent._read_wiki_page", ...)` → `patch("decafclaw.memory_context.read_wiki_page", ...)`. **Important:** patch the *imported* binding inside `context_composer` if these are used post-import — verify by reading the test context. If `context_composer.py` does `from .memory_context import parse_wiki_references` at function scope (which it does — line 725 is a function-local import), the right patch target is `decafclaw.memory_context.parse_wiki_references` (the module where the name is defined). This matches the existing patch pattern in tests where the helpers were `agent._parse_wiki_references` (defined in agent, imported function-locally by composer).

**Key changes:**
- `parse_wiki_references(user_message: str, wiki_page: str | None = None) -> list[dict]` — new (relocated, renamed).
- `read_wiki_page(config, page_name: str) -> str | None` — new (relocated, renamed).
- `get_already_injected_pages(history: list) -> set[str]` — new (relocated, renamed).
- `_WIKI_MENTION_RE` — new module-level regex in `memory_context.py`.

```python
# src/decafclaw/memory_context.py — appended in a new section

# -- Wiki references (@[[Page]] mentions) --------------------------------------

# Matches @[[PageName]] (and @[[PageName|display]]) mentions in user messages.
# Distinct from _WIKI_LINK_RE above which matches bare [[PageName]] links
# inside vault page bodies.
_WIKI_MENTION_RE = re.compile(r'@\[\[([^\]]+)\]\]')


def parse_wiki_references(
    user_message: str, wiki_page: str | None = None,
) -> list[dict]:
    """Parse @[[PageName]] mentions and optional open wiki page.

    Returns a list of dicts: {"page": name, "source": "mention"|"open_page"}.
    Does NOT resolve or read pages — caller filters against already-injected
    pages first, then resolves only the ones needed.
    """
    seen: set[str] = set()
    results: list[dict] = []

    for match in _WIKI_MENTION_RE.finditer(user_message):
        raw = match.group(1).strip()
        page_name = raw.split("|")[0].strip()
        if page_name and page_name not in seen:
            seen.add(page_name)
            results.append({"page": page_name, "source": "mention"})

    if wiki_page and wiki_page not in seen:
        results.append({"page": wiki_page, "source": "open_page"})

    return results


def read_wiki_page(config, page_name: str) -> str | None:
    """Resolve and read a wiki page. Returns content or None. Fail-open."""
    from .skills.vault.tools import resolve_page

    resolved = resolve_page(config, page_name)
    if not resolved:
        return None
    try:
        return resolved.read_text()
    except (OSError, UnicodeError):
        log.warning("Failed to read wiki page %s at %s", page_name, resolved,
                    exc_info=True)
        return None


def get_already_injected_pages(history: list) -> set[str]:
    """Scan history for vault_references messages and return set of page names."""
    pages: set[str] = set()
    for msg in history:
        if msg.get("role") == "vault_references":
            page = msg.get("wiki_page")
            if page:
                pages.add(page)
    return pages
```

**Test-first approach:** Same as Phase 1 — update imports in the existing test files first so they fail (or break collection), confirm failure, then complete the move. `tests/test_wiki_context.py` provides comprehensive coverage of all three functions.

**Verification — automated:**
- [x] `grep -rn "_parse_wiki_references\|_read_wiki_page\|_get_already_injected_pages\|_WIKI_MENTION_RE" src/ tests/` returns only `_WIKI_MENTION_RE` in `memory_context.py` and test files (tests are allowed to reach module-private regex).
- [x] `pytest tests/test_wiki_context.py -v` passes.
- [x] `pytest tests/test_vault_tools.py::TestWikiMentionRegex -v` passes.
- [x] `pytest tests/test_context_composer.py -v` passes (patch targets correctly redirected).
- [x] `make lint` passes.
- [x] `make check` passes.

**Verification — manual:**
- [x] Confirm `_WIKI_LINK_RE` (existing) and `_WIKI_MENTION_RE` (new) live as two distinct regex constants in `memory_context.py`. A reader should be able to tell from the comment which matches which.

---

## Phase 3: Audit and final verification

Confirm the relocation goal — no non-test module imports an underscore-prefixed name from `agent.py` — and run the full suite.

**Files:** None modified in this phase (verification only).

**Verification — automated:**
- [x] `grep -rn "from .agent import _" src/` shows only the two deferred `_collect_all_tool_defs` lines (sub-task #1, blocked on #438).
- [x] `grep -rn "from decafclaw.agent import _" src/` returns nothing.
- [x] `grep -rn "_resolve_attachments\|_parse_wiki_references\|_read_wiki_page\|_get_already_injected_pages" src/` returns nothing.
- [x] `make check` passes (full lint + typecheck).
- [x] `make test` passes (full suite, 2419 tests).

**Verification — manual:**
- [x] Skim the final diff: changes are confined to relocation + rename + import-site updates. No drive-by edits, no behavior changes.

---

## Plan self-review

**Spec coverage:**
- Spec sub-task #1 (`_collect_all_tool_defs` import update): **deferred** — `tool_definitions.py` not on `origin/main` at plan time. Follow-up issue to be filed after PR opens.
- Spec sub-task #2 (wiki helpers): covered by Phase 2 (lands in `memory_context.py`, the cohesion check passed — `_WIKI_LINK_RE` and `_expand_graph_links` already live there).
- Spec sub-task #3 (`_resolve_attachments`): covered by Phase 1.
- Spec validation criterion (grep audit): covered by Phase 3.

**Placeholder scan:** no TBDs, no "implement later", no "similar to phase N". Each phase carries its full detail.

**Type consistency:** function signatures match between phases and between plan and existing source. New names are the underscore-stripped originals — no renames beyond the underscore drop.

**Rejected during planning:**
- Creating a new `vault_refs.py` module — rejected because `memory_context.py` already owns wiki-link handling and the helpers are tightly related to existing functions there. New module would be unjustified proliferation.
- Keeping `_WIKI_MENTION_RE` as a private constant inside `parse_wiki_references` — rejected because tests already import it directly (`tests/test_vault_tools.py:867`), and there's no harm in keeping it module-level (matches `_WIKI_LINK_RE` next door).
- Backwards-compat shims re-exporting the old names from `agent.py` — explicitly out of scope per the session brief.
