# Research — #671 vault_section path resolution

Measured in this worktree at `b1909a8` (current `main`, post-#689).

## The premise holds — not a harness artifact

My #670 retro speculated that #671 might be wholly or partly a harness
artifact, because its eval case started passing after the eval system-prompt
fix. **That was wrong**, and checking cost one command:

```python
from decafclaw.skills.vault._sections import Document
text = '# Project Notes\n\n## Background\n\nsome bg\n\n## TODO\n\n- Old item\n'
d = Document(text)

find_section('Background')                 -> None
find_section('Project Notes/Background')   -> Background
find_section('TODO')                       -> None
find_section('## Background')              -> None
find_section('background')                 -> None

add_section('Status', level=2, after='Background')               -> False
add_section('Status', level=2, after='Project Notes/Background') -> True
```

Deterministic, no LLM involved. The eval case passes now because the agent
spends **4 tool calls instead of 2** (`2026-07-24-2323` bundle) — with a real
system prompt it reaches for `vault_show_sections` first and learns the
qualified path, rather than failing twice and blowing `max_tool_errors: 1`.
The tool is still wrong; the agent just pays around it.

Claim corrected in the open retro PR #697.

## Mechanism

`Document.find_section` (`_sections.py:155-158`):

```python
def find_section(self, path: str) -> Section | None:
    self._ensure_parsed()
    parts = [p.strip().lower() for p in path.split("/")]
    return _walk_path(self._sections, parts)
```

`_walk_path` (`:548-558`) matches `parts[0]` against the **top-level** section
list and recurses into `children`. `_build_tree` (`:534-546`) nests by heading
level, so an H1 page yields exactly one top-level section (the H1) with the
`##` headings as its children. Hence every `##` needs the `<H1>/` prefix.

Note the `.lower()` in `find_section` is redundant with `normalize_title`
(`:31-35`), which `Section.normalized_title` already applies — it lowercases
and strips wiki-links, but **not** leading `#`.

## Call sites — 13, all through `find_section`

| file:line | context |
|---|---|
| `_sections.py:282` | `move_section` re-resolve after mutation |
| `_sections.py:428` | `add_section(after=…)` |
| `_sections.py:433` | `add_section(before=…)` |
| `_sections.py:438` | `add_section(parent=…)` |
| `_sections.py:447` | `remove_section` |
| `_sections.py:455` | `rename_section` |
| `_sections.py:468` | `get_section_text` |
| `_sections.py:482` | `set_section_text` |
| `_sections.py:491` | `move_section(after=…)` |
| `_sections.py:496` | `move_section(before=…)` |
| `_sections.py:610` | `_insert_into_doc` |
| `_sections.py:621` | `_insert_into_doc` retry |
| `tools.py:1292` | `vault_show_sections` |

Fixing `find_section` therefore fixes every section operation at once. Nothing
else parses paths independently.

## Error sites — where the message needs to improve

| file:line | current text |
|---|---|
| `tools.py:1294` | `[error: section not found: {section}]` |
| `tools.py:1421` | `[error: target section not found]` — no path echoed |
| `tools.py:1434` | `[error: section not found: {section}]` |
| `tools.py:1446` | `[error: section not found: {section}]` |
| `_sections.py:612` | `section not found: {to_section}` (bare string, not ToolResult) |

`tools.py:1421` is the one the eval hit — it doesn't even echo the path that
failed.

## Reusable helpers already present

- `_section_path(sec, top_sections)` (`:568-584`) renders a section's full
  slash path. Exactly what a candidate list and a "known paths" list need.
- `list_sections(depth=0)` / `_flatten_sections` (`:160-165`, `:560-566`)
  enumerate every section with depth.

Together these mean the suffix matcher and both error messages can be built
without new traversal code.

## Tool descriptions to update

`tools.py` — `:1991` (`vault_show_sections`), `:2038` (target page section),
`:2083` (`section`), `:2106` (`after`), `:2113` (`before`), `:2120` (`parent`).
All say "Slash-separated section path" with the example `'top/first'`, which
reads as a nesting path among sections and gives no hint that the first segment
must be the page H1.
