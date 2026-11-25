---
name: google-docs
description: Structured Google Docs and Drive operations via official Google APIs. Read, search, surgical edit, outline, document tabs, comments, and appends without headless browser scraping.
auto-approve: false
---

# Google Docs & Drive Skill

Direct, structured access to Google Docs and Google Drive via the official
Google APIs. Reads and edits real document structure (headings, lists, tables,
tabs, comments) instead of scraping a rendered page, so multi-tab documents and
surgical text replacement both work.

## Requirements

- The `google-api-python-client` and `google-auth` Python packages (declared as
  project dependencies; nothing to install per-deployment).
- A Google Cloud OAuth **desktop client** secret saved to
  `~/.config/google-docs/client_secret.json`.
- A one-time OAuth handshake producing `~/.config/google-docs/token.json`.
  Run `scripts/auth_google_docs.py` from the DecafClaw repo
  (`uv run python scripts/auth_google_docs.py`). It serves the redirect on
  `localhost:8085`, so on a remote host forward that port first:

  ```bash
  ssh -L 8085:localhost:8085 <host>
  ```

- Scopes requested: `documents`, `drive.readonly`, `drive.file`. The refresh
  token is written back to `token.json` on each renewal, and an expired token is
  refreshed automatically on the next call.

The token path is fixed at `~/.config/google-docs/token.json` (via
`expanduser`, so it follows `$HOME`). It is not yet configurable through
`SkillConfig` / environment variables; if you need that, that is the change to
make.

## Setup note

Enable the **Google Docs API** and **Google Drive API** in the same Cloud
project as the OAuth client. A client secret with the APIs disabled produces a
`403` that reads like an auth failure but is a project-configuration problem.

## Available Tools

### Reading & Discovery

- `gdoc_list(query="", max_results=10)` — search or list Docs in Drive by title
  or full text, newest first.
- `gdoc_list_tabs(doc_id)` — list document tabs with titles and tab IDs.
- `gdoc_add_tab(doc_id, title)` — create a new document tab.
- `gdoc_read(doc_id, format="markdown", tab_id=None)` — read a doc or one tab.
  Formats: `markdown` (headings, bold/italic, lists, tables), `text`, or
  `metadata`.
- `gdoc_outline(doc_id, tab_id=None)` — heading tree with levels and character
  index spans. Cheap way to survey a long doc before reading it.

### Surgical Document Editing

- `gdoc_edit(doc_id, old_text, new_text, match_case=True, tab_id=None)` — atomic
  exact-text replacement, document-wide or scoped to a tab.
- `gdoc_insert(doc_id, anchor_text, text, position="after"|"before", tab_id=None)`
  — insert relative to an anchor phrase.
- `gdoc_append(doc_id, text, tab_id=None)` — append to the end of a doc or tab.

### Comments & Collaboration

- `gdoc_comments(doc_id, include_resolved=False)` — all inline comment threads,
  authors, timestamps, and replies.
- `gdoc_comment_create(doc_id, text, quoted_text=None)` — post a comment,
  optionally anchored to a quoted snippet.
- `gdoc_comment_reply(doc_id, comment_id, text)` — reply to a thread.

`doc_id` accepts a raw document ID or a full Docs URL.

## Rendering behavior

`gdoc_read` converts the Docs API body structure to text itself. What it
preserves, and what it does not:

**Preserved**

- Table content, rendered as GitHub-flavored markdown. Cell text uses `<br>` for
  multiple paragraphs, pipes are escaped, ragged rows are padded, and blank rows
  are dropped. A structurally empty table renders as
  `[Table present but empty]` rather than silently disappearing.
- List nesting, via `bullet.nestingLevel`, at two spaces per level. This matters
  more than it sounds: in a structured doc, which item sits under which parent
  often *is* the information.

**Not preserved**

- Inline formatting beyond bold and italic (underline, strikethrough, links,
  colors, super/subscript).
- Nested tables (rendered as their flattened text, without the inner structure).
- Images, drawings, and embedded charts — not represented in the output at all.
- Footnotes and suggestion-mode markup.

Copy the output rather than assuming it is lossless.

## Pitfalls

- **Reviewing before editing.** `gdoc_edit` and `gdoc_insert` match text
  exactly. Read the doc (or `gdoc_read` the relevant range) first; a substring
  that appears more than once will not behave as intended.
- **Trailing newlines.** Docs structures every paragraph with a terminating
  newline. Text that looks like `Foo` in the UI is `Foo\n` in the API, so exact
  matches against visually trailing content need the newline accounted for.
- **Multi-tab documents.** Without `tab_id`, the first tab is read. Check
  `gdoc_list_tabs` first when a document has more than one tab, or edits may
  land in the wrong place.
- **Shared-drive files** are reachable; the tool passes `supportsAllDrives`
  where it lists.
