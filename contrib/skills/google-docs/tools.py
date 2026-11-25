"""Google Docs & Drive native tools for DecafClaw."""

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_PATH = os.path.expanduser("~/.config/google-docs/token.json")

STYLE_RANKS = {
    "TITLE": 0,
    "HEADING_1": 1,
    "HEADING_2": 2,
    "HEADING_3": 3,
    "HEADING_4": 4,
    "HEADING_5": 5,
    "HEADING_6": 6,
}


def _get_credentials():
    if not os.path.exists(TOKEN_PATH):
        raise FileNotFoundError(
            f"Google Docs OAuth token not found at {TOKEN_PATH}. Run scripts/auth_google_docs.py first."
        )
    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def _parse_doc_id(input_str: str) -> str:
    """Extract document ID from either raw ID or Google Docs URL."""
    input_str = input_str.strip()
    match = re.search(r"/document/d/([a-zA-Z0-9_-]+)", input_str)
    if match:
        return match.group(1)
    return input_str


def _get_tab_target(doc: dict, tab_id: Optional[str] = None) -> Tuple[Optional[str], str, dict]:
    """Returns (tabId_or_None, tab_title, body_dict)."""
    tabs = doc.get("tabs", [])
    if tabs:
        if tab_id:
            for t in tabs:
                tid = t.get("tabProperties", {}).get("tabId")
                title = t.get("tabProperties", {}).get("title", "")
                if tid == tab_id or title.lower() == tab_id.lower():
                    return tid, title, t.get("documentTab", {}).get("body", {})
        first = tabs[0]
        return (
            first.get("tabProperties", {}).get("tabId"),
            first.get("tabProperties", {}).get("title", "Tab 1"),
            first.get("documentTab", {}).get("body", {}),
        )
    return None, doc.get("title", "Document"), doc.get("body", {})


def _find_text_range(body: dict, target_text: str) -> Optional[Tuple[int, int]]:
    """Locate target_text across all text runs in the body and return (startIndex, endIndex)."""
    full_text = ""
    index_map = []
    for el in body.get("content", []):
        p = el.get("paragraph")
        if p:
            for r in p.get("elements", []):
                tr = r.get("textRun")
                if tr and tr.get("content"):
                    c = tr.get("content")
                    start = r.get("startIndex")
                    for offset, ch in enumerate(c):
                        full_text += ch
                        index_map.append(start + offset)

    pos = full_text.find(target_text)
    if pos == -1:
        return None
    start_idx = index_map[pos]
    end_idx = index_map[pos + len(target_text) - 1] + 1
    return start_idx, end_idx


def _cell_inline_text(cell: dict) -> str:
    """Flatten a table cell's nested content into a single line of markdown.

    A Docs table cell is a list of structural elements, and those elements can
    themselves be lists of paragraphs (nested lists produce `[{'paragraph':...},
    {'paragraph':...}]` in a single element). Anything that is not a paragraph
    is dropped rather than emitted as a placeholder, since a table inside a
    table is rare enough that a marker line is less useful than the text.
    """
    parts: List[str] = []

    def walk(elements: List[dict], depth: int = 0) -> None:
        for el in elements:
            if isinstance(el, list):
                walk(el, depth)
                continue
            paragraph = el.get("paragraph")
            if not paragraph:
                continue
            text = ""
            for run in paragraph.get("elements", []):
                text_run = run.get("textRun")
                if text_run:
                    text += text_run.get("content", "")
            text = " ".join(text.split())
            if not text:
                continue
            if "bullet" in paragraph:
                parts.append(f"{'  ' * depth}- {text}")
            else:
                parts.append(text)

    walk(cell.get("content", []))
    joined = " <br> ".join(parts)
    # An unescaped pipe would silently split the cell into extra columns.
    return joined.replace("|", "\\|")


def _table_to_markdown(table: dict) -> str:
    """Render a Docs table as a GitHub-style markdown table.

    Docs always leaves a trailing empty paragraph in a cell, so blank rows are
    dropped. If every cell in the table is empty the table carries no content
    and an explicit marker is returned instead of a table full of pipes.
    """
    rows = []
    for row in table.get("tableRows", []):
        cells = [_cell_inline_text(c) for c in row.get("tableCells", [])]
        if any(c.strip() for c in cells):
            rows.append(cells)

    if not rows:
        return "[Table present but empty]\n"

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |"]
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines) + "\n"


def _docs_to_text(body: dict, markdown: bool = False) -> str:
    """Convert a Google Docs body structure to text or basic markdown."""
    content = body.get("content", [])
    output = []

    for element in content:
        paragraph = element.get("paragraph")
        if paragraph:
            style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
            para_text = ""
            for el in paragraph.get("elements", []):
                text_run = el.get("textRun")
                if text_run:
                    t = text_run.get("content", "")
                    text_style = text_run.get("textStyle", {})
                    if markdown:
                        if text_style.get("bold") and t.strip():
                            t = f"**{t.strip()}** "
                        elif text_style.get("italic") and t.strip():
                            t = f"*{t.strip()}* "
                    para_text += t

            if markdown:
                if style == "TITLE":
                    para_text = f"# {para_text.strip()}\n"
                elif style == "HEADING_1":
                    para_text = f"## {para_text.strip()}\n"
                elif style == "HEADING_2":
                    para_text = f"### {para_text.strip()}\n"
                elif style == "HEADING_3":
                    para_text = f"#### {para_text.strip()}\n"
                elif style == "HEADING_4":
                    para_text = f"##### {para_text.strip()}\n"

            bullet = paragraph.get("bullet")
            if bullet is not None and markdown:
                # Docs reports nesting as bullet.nestingLevel (0-based). The
                # flat "- " this replaces lost real structure: in a doc like an
                # onboarding plan, which logger a channel sits under IS the
                # information. Two spaces per level is the markdown convention.
                level = bullet.get("nestingLevel") or 0
                try:
                    level = int(level)
                except (TypeError, ValueError):
                    level = 0
                level = max(0, level)
                para_text = f"{'  ' * level}- {para_text.strip()}\n"

            output.append(para_text)

        table = element.get("table")
        if table:
            # Previously emitted "[Table omitted]" — which silently discarded
            # real content (a doc whose whole checklist lives in a table read
            # back as empty). Render it; markdown table syntax reads fine as
            # plain text too, so it is used for both formats.
            if output and not output[-1].endswith("\n\n"):
                output.append("\n")
            output.append(_table_to_markdown(table))
            output.append("\n")

    return "".join(output)


def gdoc_list(ctx, query: str = "", max_results: int = 10) -> str:
    """List or search Google Docs in Google Drive."""
    try:
        creds = _get_credentials()
        drive = build("drive", "v3", credentials=creds)

        q = "mimeType='application/vnd.google-apps.document' and trashed=false"
        if query:
            q += f" and (name contains '{query}' or fullText contains '{query}')"

        res = (
            drive.files()
            .list(
                q=q,
                pageSize=max_results,
                fields="files(id, name, modifiedTime, webViewLink)",
                orderBy="modifiedTime desc",
            )
            .execute()
        )

        files = res.get("files", [])
        if not files:
            return f"No Google Docs found matching query: '{query}'" if query else "No Google Docs found."

        lines = [f"Found {len(files)} Google Doc(s):"]
        for f in files:
            lines.append(f"- **{f['name']}**")
            lines.append(f"  ID: `{f['id']}`")
            lines.append(f"  Modified: {f.get('modifiedTime', '')}")
            lines.append(f"  URL: {f.get('webViewLink', '')}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing Google Docs: {e}"


def gdoc_list_tabs(ctx, doc_id: str) -> str:
    """List all document tabs with titles and tab IDs."""
    try:
        real_id = _parse_doc_id(doc_id)
        creds = _get_credentials()
        docs = build("docs", "v1", credentials=creds)

        doc = docs.documents().get(documentId=real_id, includeTabsContent=True).execute()
        title = doc.get("title", "Untitled Document")
        tabs = doc.get("tabs", [])

        if not tabs:
            return f"Document '{title}' (`{real_id}`) is a single-tab document."

        lines = [f"# Tabs for: {title} (`{real_id}`)\n"]
        for i, t in enumerate(tabs):
            props = t.get("tabProperties", {})
            t_id = props.get("tabId")
            t_title = props.get("title", f"Tab {i + 1}")
            body = t.get("documentTab", {}).get("body", {})
            elements_count = len(body.get("content", []))
            lines.append(f"{i + 1}. **{t_title}** (ID: `{t_id}`) — {elements_count} structural elements")

        return "\n".join(lines)
    except Exception as e:
        return f"Error listing tabs for Google Doc '{doc_id}': {e}"


def gdoc_add_tab(ctx, doc_id: str, title: str) -> str:
    """Create a new document tab."""
    try:
        real_id = _parse_doc_id(doc_id)
        creds = _get_credentials()
        docs = build("docs", "v1", credentials=creds)

        res = (
            docs.documents()
            .batchUpdate(
                documentId=real_id, body={"requests": [{"addDocumentTab": {"tabProperties": {"title": title}}}]}
            )
            .execute()
        )

        new_props = res.get("replies", [{}])[0].get("addDocumentTab", {}).get("tabProperties", {})
        return f"Successfully created tab '{title}' (ID: `{new_props.get('tabId')}`) in document {real_id}."
    except Exception as e:
        return f"Error adding tab to Google Doc '{doc_id}': {e}"


def gdoc_read(ctx, doc_id: str, format: str = "markdown", tab_id: Optional[str] = None) -> str:
    """Read a Google Doc by ID or URL, optionally targeting a specific tab."""
    try:
        real_id = _parse_doc_id(doc_id)
        creds = _get_credentials()
        docs = build("docs", "v1", credentials=creds)

        doc = docs.documents().get(documentId=real_id, includeTabsContent=True).execute()
        title = doc.get("title", "Untitled Document")

        if format == "metadata":
            tabs_info = [
                f"{t.get('tabProperties', {}).get('title')} ({t.get('tabProperties', {}).get('tabId')})"
                for t in doc.get("tabs", [])
            ]
            return f"Title: {title}\nID: {real_id}\nRevision: {doc.get('revisionId')}\nTabs: {', '.join(tabs_info) if tabs_info else 'Single Tab'}"

        resolved_tab_id, tab_title, body = _get_tab_target(doc, tab_id)
        is_md = format == "markdown"
        body_text = _docs_to_text(body, markdown=is_md)

        header = f"# {title} — Tab: {tab_title} (ID: {real_id})\n\n"
        return header + body_text
    except Exception as e:
        return f"Error reading Google Doc '{doc_id}': {e}"


def gdoc_outline(ctx, doc_id: str, tab_id: Optional[str] = None) -> str:
    """Return structural heading outline of a doc or specific tab with character index spans."""
    try:
        real_id = _parse_doc_id(doc_id)
        creds = _get_credentials()
        docs = build("docs", "v1", credentials=creds)

        doc = docs.documents().get(documentId=real_id, includeTabsContent=True).execute()
        title = doc.get("title", "Untitled Document")
        resolved_tab_id, tab_title, body = _get_tab_target(doc, tab_id)

        lines = [f"# Outline: {title} — Tab: {tab_title} (`{real_id}`)\n"]
        for el in body.get("content", []):
            p = el.get("paragraph")
            if not p:
                continue
            style = p.get("paragraphStyle", {}).get("namedStyleType", "")
            if style in STYLE_RANKS:
                rank = STYLE_RANKS[style]
                indent = "  " * rank
                text = "".join(r.get("textRun", {}).get("content", "") for r in p.get("elements", [])).strip()
                start = el.get("startIndex")
                end = el.get("endIndex")
                prefix = "#" * max(1, rank)
                lines.append(f"{indent}- {prefix} **{text}** `[{start}:{end}]`")

        return "\n".join(lines)
    except Exception as e:
        return f"Error generating outline for Google Doc '{doc_id}': {e}"


def gdoc_edit(
    ctx, doc_id: str, old_text: str, new_text: str, match_case: bool = True, tab_id: Optional[str] = None
) -> str:
    """Replace exact text matches in a Google Doc or specific tab."""
    try:
        real_id = _parse_doc_id(doc_id)
        creds = _get_credentials()
        docs = build("docs", "v1", credentials=creds)

        req: Dict[str, Any] = {
            "containsText": {
                "text": old_text,
                "matchCase": match_case,
            },
            "replaceText": new_text,
        }
        if tab_id:
            req["tabsCriteria"] = {"tabIds": [tab_id]}

        res = docs.documents().batchUpdate(documentId=real_id, body={"requests": [{"replaceAllText": req}]}).execute()

        reply = res.get("replies", [{}])[0].get("replaceAllText", {})
        count = reply.get("occurrencesChanged", 0)
        return f"Successfully replaced {count} occurrence(s) of '{old_text}' in document {real_id}."
    except Exception as e:
        return f"Error editing Google Doc '{doc_id}': {e}"


def gdoc_insert(
    ctx, doc_id: str, anchor_text: str, text: str, position: str = "after", tab_id: Optional[str] = None
) -> str:
    """Insert text immediately before or after an anchor phrase anywhere in the document/tab."""
    try:
        real_id = _parse_doc_id(doc_id)
        creds = _get_credentials()
        docs = build("docs", "v1", credentials=creds)

        doc = docs.documents().get(documentId=real_id, includeTabsContent=True).execute()
        resolved_tab_id, tab_title, body = _get_tab_target(doc, tab_id)

        match = _find_text_range(body, anchor_text)
        if not match:
            return f"Error: Anchor text '{anchor_text}' not found in tab '{tab_title}'."

        start_idx, end_idx = match
        target_idx = end_idx if position.lower() == "after" else start_idx

        insert_text = text
        if position.lower() == "after" and not insert_text.startswith("\n"):
            insert_text = "\n" + insert_text
        elif position.lower() == "before" and not insert_text.endswith("\n"):
            insert_text = insert_text + "\n"

        location: Dict[str, Any] = {"index": target_idx}
        if resolved_tab_id:
            location["tabId"] = resolved_tab_id

        requests = [{"insertText": {"location": location, "text": insert_text}}]

        docs.documents().batchUpdate(documentId=real_id, body={"requests": requests}).execute()

        return f"Successfully inserted text {position} '{anchor_text}' (index {target_idx}) in tab '{tab_title}'."
    except Exception as e:
        return f"Error inserting text into Google Doc '{doc_id}': {e}"


def gdoc_append(ctx, doc_id: str, text: str, tab_id: Optional[str] = None) -> str:
    """Append text to the end of a Google Doc or tab."""
    try:
        real_id = _parse_doc_id(doc_id)
        creds = _get_credentials()
        docs = build("docs", "v1", credentials=creds)

        doc = docs.documents().get(documentId=real_id, includeTabsContent=True).execute()
        resolved_tab_id, tab_title, body = _get_tab_target(doc, tab_id)

        content = body.get("content", [])
        end_index = 1
        if content:
            end_index = content[-1].get("endIndex", 1) - 1

        location: Dict[str, Any] = {"index": max(1, end_index)}
        if resolved_tab_id:
            location["tabId"] = resolved_tab_id

        requests = [{"insertText": {"location": location, "text": text if text.startswith("\n") else "\n" + text}}]

        docs.documents().batchUpdate(documentId=real_id, body={"requests": requests}).execute()

        return f"Successfully appended text to tab '{tab_title}' in document {real_id}."
    except Exception as e:
        return f"Error appending to Google Doc '{doc_id}': {e}"


def gdoc_comments(ctx, doc_id: str, include_resolved: bool = False) -> str:
    """Fetch inline comments, suggestions, author names, and replies on a Google Doc."""
    try:
        real_id = _parse_doc_id(doc_id)
        creds = _get_credentials()
        drive = build("drive", "v3", credentials=creds)

        res = (
            drive.comments()
            .list(
                fileId=real_id,
                includeDeleted=False,
                fields="comments(id, author(displayName, emailAddress), content, createdTime, resolved, quotedFileContent(value), replies(id, author(displayName), content, createdTime))",
            )
            .execute()
        )

        comments = res.get("comments", [])
        if not include_resolved:
            comments = [c for c in comments if not c.get("resolved")]

        if not comments:
            return f"No active comments found on document '{doc_id}'."

        lines = [f"Found {len(comments)} comment thread(s):"]
        for c in comments:
            author = c.get("author", {}).get("displayName", "Unknown")
            status = " [RESOLVED]" if c.get("resolved") else ""
            lines.append(f"\n### Comment by {author}{status} (ID: `{c.get('id')}` - {c.get('createdTime')})")
            quoted = c.get("quotedFileContent", {}).get("value")
            if quoted:
                lines.append(f'> *Anchor:* "{quoted.strip()}"')
            lines.append(f"{c.get('content')}")

            replies = c.get("replies", [])
            for r in replies:
                r_author = r.get("author", {}).get("displayName", "Unknown")
                lines.append(f"  - **{r_author}** ({r.get('createdTime')}): {r.get('content')}")

        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching comments for Google Doc '{doc_id}': {e}"


def gdoc_comment_create(ctx, doc_id: str, text: str, quoted_text: Optional[str] = None) -> str:
    """Post a new comment on a Google Doc, optionally anchored to quoted text."""
    try:
        real_id = _parse_doc_id(doc_id)
        creds = _get_credentials()
        drive = build("drive", "v3", credentials=creds)

        body: Dict[str, Any] = {"content": text}
        if quoted_text:
            body["quotedFileContent"] = {"value": quoted_text, "mimeType": "text/html"}

        res = drive.comments().create(fileId=real_id, fields="id,content,createdTime,author", body=body).execute()

        return f"Successfully created comment (ID: `{res.get('id')}`) on document {real_id}."
    except Exception as e:
        return f"Error creating comment on Google Doc '{doc_id}': {e}"


def gdoc_comment_reply(ctx, doc_id: str, comment_id: str, text: str) -> str:
    """Reply to an existing comment thread on a Google Doc."""
    try:
        real_id = _parse_doc_id(doc_id)
        creds = _get_credentials()
        drive = build("drive", "v3", credentials=creds)

        res = (
            drive.replies()
            .create(
                fileId=real_id, commentId=comment_id, fields="id,content,createdTime,author", body={"content": text}
            )
            .execute()
        )

        return f"Successfully posted reply (ID: `{res.get('id')}`) to comment {comment_id}."
    except Exception as e:
        return f"Error replying to comment '{comment_id}' on Google Doc '{doc_id}': {e}"


TOOLS = {
    "gdoc_list": gdoc_list,
    "gdoc_list_tabs": gdoc_list_tabs,
    "gdoc_add_tab": gdoc_add_tab,
    "gdoc_read": gdoc_read,
    "gdoc_outline": gdoc_outline,
    "gdoc_edit": gdoc_edit,
    "gdoc_insert": gdoc_insert,
    "gdoc_append": gdoc_append,
    "gdoc_comments": gdoc_comments,
    "gdoc_comment_create": gdoc_comment_create,
    "gdoc_comment_reply": gdoc_comment_reply,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "gdoc_list",
            "description": "List or search Google Docs in Google Drive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional search term to filter docs by name or content.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of documents to return (default 10).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gdoc_list_tabs",
            "description": "List all document tabs with titles and tab IDs.",
            "parameters": {
                "type": "object",
                "properties": {"doc_id": {"type": "string", "description": "Google Doc document ID or full URL."}},
                "required": ["doc_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gdoc_add_tab",
            "description": "Create a new document tab in a Google Doc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Google Doc document ID or full URL."},
                    "title": {"type": "string", "description": "Title for the new tab."},
                },
                "required": ["doc_id", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gdoc_read",
            "description": "Read a Google Doc or a specific document tab.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Google Doc document ID or full URL."},
                    "format": {
                        "type": "string",
                        "enum": ["markdown", "text", "metadata"],
                        "description": "Output format: 'markdown' (default), 'text', or 'metadata'.",
                    },
                    "tab_id": {
                        "type": "string",
                        "description": "Optional tabId or tab title to read. Defaults to the active/first tab.",
                    },
                },
                "required": ["doc_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gdoc_outline",
            "description": "Return the structural heading outline of a Google Doc or specific tab with character index ranges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Google Doc document ID or full URL."},
                    "tab_id": {"type": "string", "description": "Optional tabId or tab title."},
                },
                "required": ["doc_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gdoc_edit",
            "description": "Replace exact text matches in a Google Doc using Docs API replaceAllText.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Google Doc document ID or full URL."},
                    "old_text": {"type": "string", "description": "Exact text to find and replace."},
                    "new_text": {"type": "string", "description": "Replacement text."},
                    "match_case": {"type": "boolean", "description": "Whether to match case exactly (default true)."},
                    "tab_id": {
                        "type": "string",
                        "description": "Optional tabId to scope replacements to a specific tab.",
                    },
                },
                "required": ["doc_id", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gdoc_insert",
            "description": "Insert text immediately before or after an anchor phrase anywhere in the Google Doc or tab.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Google Doc document ID or full URL."},
                    "anchor_text": {"type": "string", "description": "Existing text in the document/tab to anchor to."},
                    "text": {"type": "string", "description": "Text to insert."},
                    "position": {
                        "type": "string",
                        "enum": ["after", "before"],
                        "description": "Insert 'after' (default) or 'before' the anchor text.",
                    },
                    "tab_id": {"type": "string", "description": "Optional tabId or tab title."},
                },
                "required": ["doc_id", "anchor_text", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gdoc_append",
            "description": "Append text to the end of a Google Doc or tab.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Google Doc document ID or full URL."},
                    "text": {"type": "string", "description": "Text content to append."},
                    "tab_id": {"type": "string", "description": "Optional tabId or tab title."},
                },
                "required": ["doc_id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gdoc_comments",
            "description": "Fetch all inline comments, suggestions, author names, and replies on a Google Doc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Google Doc document ID or full URL."},
                    "include_resolved": {
                        "type": "boolean",
                        "description": "Whether to include resolved comments (default false).",
                    },
                },
                "required": ["doc_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gdoc_comment_create",
            "description": "Post a new comment on a Google Doc, optionally anchored to quoted text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Google Doc document ID or full URL."},
                    "text": {"type": "string", "description": "The comment message to post."},
                    "quoted_text": {
                        "type": "string",
                        "description": "Optional text snippet in the document to anchor this comment to.",
                    },
                },
                "required": ["doc_id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gdoc_comment_reply",
            "description": "Reply to an existing comment thread on a Google Doc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string", "description": "Google Doc document ID or full URL."},
                    "comment_id": {"type": "string", "description": "ID of the parent comment thread."},
                    "text": {"type": "string", "description": "Reply message text."},
                },
                "required": ["doc_id", "comment_id", "text"],
            },
        },
    },
]


def get_tools(ctx):
    return TOOLS, TOOL_DEFINITIONS
