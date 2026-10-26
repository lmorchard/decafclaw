# Survey of Tool Widget Presentation Opportunities

This document provides a comprehensive survey and architectural evaluation of custom visual widget presentation opportunities across DecafClaw's tool ecosystem, following issues #151, #621, and #931.

DecafClaw's web UI supports rich interactive presentations via the `<dc-widget-host>` framework. Tools can return a `WidgetRequest` attached to a `ToolResult`, allowing the browser to render interactive components (such as sortable tables, syntax-highlighted diffs, or collapsible JSON trees) without altering the text or data contracts seen by LLMs, terminals, or chat platforms.

---

## 1. Architecture & Integration Contract

### The Dual-Surface Principle
Every tool execution produces a `ToolResult`. Rich widgets adhere to a strict separation of concerns:
1. **Model & Non-Web Contract (`ToolResult.text`, `ToolResult.data`)**:
   - `text` is the sole representation sent to the LLM context, recorded in session archives, and displayed in headless/terminal or Mattermost clients.
   - `data` provides structured machine-readable metadata.
2. **Display Contract (`ToolResult.widget`)**:
   - `widget` is a `WidgetRequest(widget_type, data, target="inline"|"canvas")`.
   - Never forwarded to the LLM or external chat channels.
   - Validated server-side by `tool_execution.py` against the widget's `data_schema` via `WidgetRegistry.validate()`.
   - If validation fails or the widget type is unknown, the widget is stripped (warning logged), and the tool result renders cleanly as fallback text.

```python
from decafclaw.media import ToolResult, WidgetRequest

# Example: surgical text edit returning a diff_view widget
return ToolResult(
    text=f"Edited {path}: replaced {count} occurrence(s)\n\n{diff}",
    widget=WidgetRequest(
        widget_type="diff_view",
        data={
            "before": original_content,
            "after": updated_content,
            "filename": path,
            "view": "unified",
        },
    ),
)
```

---

## 2. Comprehensive Tool Audit Inventory

An audit of all 63 built-in tools across 19 modules and all capability/skill tools was conducted.

### 2.1 Built-in Tools (`src/decafclaw/tools/`)

| Module | Tool | Current Output Shape | Widget Opportunity | Recommended Widget | Priority / Tier |
|---|---|---|---|---|---|
| **workspace** | `workspace_read` | Numbered text + metadata `.data` | Line-numbered code preview | `code_block` | Medium |
| | `workspace_write` | Summary text string | File preview on creation | `code_block` / `markdown_document` | Low |
| | `workspace_list` | Formatted text + `.data.entries` | Sortable file & directory table | `data_table` | **High (Immediate)** |
| | `workspace_append` | Summary text string | Mutation confirmation | None / Text | Low |
| | `workspace_edit` | Summary text + mini-diff text | Interactive unified/split diff | `diff_view` | **High (Immediate)** |
| | `workspace_insert` | Summary text + mini-diff text | Interactive unified/split diff | `diff_view` | Medium |
| | `workspace_replace_lines` | Summary text + mini-diff text | Interactive unified/split diff | `diff_view` | Medium |
| | `workspace_search` | Grouped file excerpts + line numbers | Structured search result table | `data_table` / Proposed `search_results_explorer` | Medium |
| | `workspace_glob` | Formatted file paths | File list table with size/type | `data_table` | Low |
| | `workspace_move` | Summary text string | None | None / Text | Low |
| | `workspace_copy` | Summary text string | None | None / Text | Low |
| | `workspace_delete` | Summary text string | None | None / Text | Low |
| | `workspace_diff` | Unified diff text | Interactive side-by-side / unified diff | `diff_view` | **High (Immediate)** |
| | `workspace_mkdir` | Summary text string | None | None / Text | Low |
| | `workspace_preview_markdown`| Markdown text | Rendered markdown preview | `markdown_document` (Already implemented) | Done |
| | `file_share` | Text + file attachment media | File card | Proposed `attachment_card` | Low |
| **admin** | `admin_read` | Text of admin file | Rendered markdown / code block / JSON | `markdown_document` / `code_block` / `json_view` | Medium |
| | `admin_list` | Formatted text list | File list table | `data_table` | Medium |
| | `admin_write` | Summary text | Rendered markdown preview | `markdown_document` | Low |
| | `admin_edit` | Summary text + mini-diff | Interactive diff view | `diff_view` | Medium |
| | `admin_replace_lines` | Summary text + mini-diff | Interactive diff view | `diff_view` | Medium |
| | `admin_delete` | Summary text | None | None / Text | Low |
| **schedule** | `schedule_list` | Bulleted text + `.data.schedules` | Sortable wake schedule table | `data_table` | **High (Immediate)** |
| | `schedule_turn` | Confirmation string | None / Badge | None / Text | Low |
| | `schedule_cancel` | Confirmation string | None / Badge | None / Text | Low |
| **checklist** | `checklist_create` | Markdown steps + sticky slot | Sticky multi-step tracker | `progress_tracker` (Bundled) | Done |
| | `checklist_step_done`| Markdown steps + sticky slot | Sticky multi-step tracker | `progress_tracker` (Bundled) | Done |
| | `checklist_status` | Markdown steps text | Inline multi-step tracker | `progress_tracker` | Medium |
| | `checklist_abort` | Status message | Progress tracker cleanup | `progress_tracker` (Bundled) | Done |
| **conversation**| `conversation_search`| Scored snippets + metadata | Search result table / timeline | `data_table` / Proposed `timeline_view` | Medium |
| | `conversation_compact`| Markdown compaction summary | Formatted summary document | `markdown_document` | Low |
| **notes** | `notes_read` | Timestamped text entries | Structured notes table or document | `data_table` / `markdown_document` | Medium |
| | `notes_append` | Confirmation string | None | None / Text | Low |
| **health** | `health_status` | Key-value diagnostic text | System diagnostic dashboard | `data_table` / Proposed `key_value_inspector` | Medium |
| **core** | `context_stats` | Token budget statistics text | Token allocation breakdown | `data_table` / Proposed `key_value_inspector` | Medium |
| | `debug_context` | Context dump text + attachments | Collapsible context message tree | `json_view` | Medium |
| | `ask_user_multiple_choice`| Prompt text + options | Interactive option picker | `multiple_choice` (Bundled) | Done |
| | `ask_user_text` | Prompt text + fields | Interactive form input | `text_input` (Bundled) | Done |
| | `current_time` | ISO timestamp text | None | None / Text | Low |
| | `wait` | Waiting confirmation text | None | None / Text | Low |
| **http** | `web_fetch` | Raw HTML/text response | Syntax-highlighted HTML or rendered doc | `code_block` / `markdown_document` | Medium |
| | `http_request` | Status, headers, body text | HTTP Inspector with tabs | `json_view` / Proposed `multi_tab_view` | Medium |
| **attachment**| `list_attachments` | JSON array string | File attachments table | `data_table` | Medium |
| | `get_attachment` | Base64 or markdown image | Media display card | `code_block` / Image | Low |
| **shell** | `shell` | stdout/stderr text | Terminal / syntax-highlighted code | `code_block` / `terminal` | Medium |
| | `shell_patterns` | List of approved patterns | Patterns table | `data_table` | Low |
| | `shell_guidance` | Guidance text | Markdown document | `markdown_document` | Low |
| **skill** | `skill_validate` | Check list with pass/fail marks | Validation results table | `data_table` | Medium |
| | `refresh_skills` | Discovered skill summary text | Skills catalog table | `data_table` | Low |
| | `activate_skill` | Confirmation text | None | None / Text | Low |
| **canvas** | `canvas_read` | JSON text of tab descriptors | Tab summary table / JSON tree | `data_table` / `json_view` | Low |
| | `canvas_new_tab` | Tab creation confirmation | Opens canvas tab | Canvas host | Done |
| | `canvas_update` | Tab update confirmation | Updates canvas tab | Canvas host | Done |
| | `canvas_close_tab`| Tab close confirmation | Closes canvas tab | Canvas host | Done |
| | `canvas_clear` | Canvas reset confirmation | Clears canvas | Canvas host | Done |
| **sticky** | `widget_pin_sticky`| Confirmation text | Pins widget | Sticky host | Done |
| | `widget_unpin_sticky`| Confirmation text | Unpins widget | Sticky host | Done |
| **delegate** | `delegate_task` | Subtask response text | Task execution card | Proposed `task_card` | Low |
| | `delegate_tasks` | Subtask responses text | Parallel task progress tracker | `progress_tracker` / Proposed `task_board` | Medium |
| **email** | `send_email` | Confirmation / preview string | Email preview card | Proposed `email_preview_card` | Low |
| **notification**| `send_notification`| Delivery status text | None | None / Text | Low |
| **heartbeat** | `heartbeat_trigger`| Execution status text | None | None / Text | Low |
| **prompt** | `update_system_prompt`| Confirmation string + diff | Interactive diff view | `diff_view` | Medium |

---

### 2.2 Skill Tools (`src/decafclaw/skills/` and `contrib/skills/`)

| Skill | Tool | Output Shape | Widget Opportunity | Recommended Widget |
|---|---|---|---|---|
| **vault** | `vault_search` | Formatted matches + snippets | Sortable results table with scores | `data_table` (Already implemented) |
| | `vault_read` | Markdown page content | Rendered markdown document | `markdown_document` |
| | `vault_list` | Text list of pages | Sortable vault pages table | `data_table` |
| | `vault_recent` | Text list of recent pages | Recent pages table with timestamps | `data_table` |
| | `vault_tags` | Tag list and counts | Tags and counts table | `data_table` |
| | `vault_backlinks`| Link list | Backlinks table | `data_table` |
| | `vault_write` | Confirmation text | Diff or preview | `diff_view` / `markdown_document` |
| **background**| `shell_background_list` | Text list of background jobs | Active jobs table (PID, command, status, elapsed) | `data_table` |
| | `shell_background_status`| Status text of a job | Job status card with streaming output | `progress_tracker` / Proposed `key_value_inspector` |
| **project** | `project_list` | Text list of projects | Projects table (Name, Status, Active) | `data_table` |
| | `project_status` | Spec, plan, step list | Multi-step tracker + spec | `progress_tracker` + `markdown_document` |
| **tabstack** | `tabstack_extract_json` | JSON string extracted from web | Collapsible JSON tree | `json_view` |
| | `tabstack_extract_markdown` | Markdown extracted from web | Rendered markdown document | `markdown_document` |
| **mcp** | `mcp_status` | Status text of servers and tools | Servers and tools status table | `data_table` |
| | `mcp_list_resources` | Resource URI list | Resources table (URI, name, mimeType) | `data_table` |
| | `mcp_list_prompts` | Prompt list | Prompts table (name, description) | `data_table` |
| **dream** | `dream_recent_runs` | Text list of distillation runs | Runs table (date, duration, pages generated) | `data_table` |
| **friction** | `tool_friction_analyze` | Friction themes text | Themes and proposed additions table | `data_table` |
| **kindle** | `kindle_list_books` | Text list of books | Books table (Title, Author, Highlights) | `data_table` |
| | `kindle_fetch_highlights`| Formatted highlight text | Rendered markdown / cards | `markdown_document` |
| **claude_code** / **opencode** | `*_sessions` | List of sessions | Sessions table (ID, model, status) | `data_table` |
| **writing-clearly** | `edit_with_strunk` | Text diff of suggested edits | Side-by-side or unified diff | `diff_view` |

---

## 3. Opportunities for Existing Bundled Widgets

DecafClaw bundles several mature widgets ready for broader adoption:

### 3.1 `diff_view`
The `diff_view` widget displays side-by-side or unified diffs with syntax highlighting and file navigation. It accepts:
```json
{
  "before": "original text",
  "after": "new text",
  "filename": "path/to/file.py",
  "view": "unified"
}
```
- **Primary Candidate: `workspace_edit`**: When replacing text in a workspace file, `workspace_edit` already computes a unified diff using `difflib`. Passing `before` (the normalized file before replacement) and `after` (the normalized file after replacement) provides immediate visual clarity in the web UI.
- **Primary Candidate: `workspace_diff`**: Designed specifically to compare two files. Passing the full contents of `path1` and `path2` allows users to toggle between unified and split side-by-side views.
- **Secondary Candidates**: `admin_edit`, `admin_replace_lines`, `workspace_replace_lines`, `edit_with_strunk`.

### 3.2 `data_table`
The `data_table` widget renders sortable, filterable rows with defined columns and optional captions:
```json
{
  "caption": "Directory listing: src/",
  "columns": [
    {"key": "name", "label": "Name"},
    {"key": "type", "label": "Type"},
    {"key": "size", "label": "Size"}
  ],
  "rows": [
    {"name": "index.js", "type": "file", "size": "1.2 KB"}
  ]
}
```
- **Primary Candidate: `workspace_list`**: The directory listing returns structured records (`name`, `is_dir`, `size`). Presenting this as a table makes navigation and size comparisons significantly faster.
- **Primary Candidate: `schedule_list`**: Lists pending wake turns with `schedule_id`, `fire_at`, `remaining` seconds, and `reason`. A table organizes upcoming background events cleanly.
- **Secondary Candidates**: `shell_background_list`, `project_list`, `list_attachments`, `vault_list`, `vault_recent`, `vault_tags`.

### 3.3 `json_view`
The `json_view` widget renders interactive collapsible trees with search/filter paths:
```json
{
  "value": { "status": "ok", "items": [1, 2, 3] },
  "expand_depth": 2
}
```
- **Candidates**: `debug_context`, `http_request` (when the response is `application/json`), `tabstack_extract_json`.

### 3.4 `code_block`
The `code_block` widget renders syntax-highlighted code with line numbers, copy buttons, and an "Open in Canvas" shortcut:
```json
{
  "code": "def hello():\n    print('world')",
  "language": "python",
  "filename": "hello.py"
}
```
- **Candidates**: `workspace_read` for code files, `web_fetch` for raw HTML/JS/CSS, `shell` for complex script execution outputs.

### 3.5 `markdown_document`
The `markdown_document` widget renders formatted Markdown with inline collapsing and canvas tab editing:
```json
{
  "content": "# Document Title\n\nFormatted prose..."
}
```
- **Candidates**: `workspace_preview_markdown` (already implemented), `vault_read`, `tabstack_extract_markdown`, `conversation_compact`.

---

## 4. Concrete Specifications for Proposed New Widgets

To address presentations where existing widgets are suboptimal, we propose five new widgets:

### 4.1 `key_value_inspector`
**Purpose**: Display structured key-value configurations, diagnostic summaries, and single-record entity details (e.g. `health_status`, `context_stats`, `shell_background_status`).

```json
{
  "name": "key_value_inspector",
  "description": "Displays structured key-value pairs grouped into sections with optional status badges.",
  "modes": ["inline", "canvas"],
  "accepts_input": false,
  "data_schema": {
    "type": "object",
    "required": ["items"],
    "properties": {
      "title": { "type": "string" },
      "groups": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["name", "items"],
          "properties": {
            "name": { "type": "string" },
            "items": {
              "type": "array",
              "items": {
                "type": "object",
                "required": ["key", "value"],
                "properties": {
                  "key": { "type": "string" },
                  "value": { "type": ["string", "number", "boolean"] },
                  "badge": { "type": "string", "enum": ["ok", "warn", "error", "info"] },
                  "note": { "type": "string" }
                }
              }
            }
          }
        }
      },
      "items": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["key", "value"],
          "properties": {
            "key": { "type": "string" },
            "value": { "type": ["string", "number", "boolean"] },
            "badge": { "type": "string", "enum": ["ok", "warn", "error", "info"] }
          }
        }
      }
    }
  }
}
```

### 4.2 `file_tree`
**Purpose**: Hierarchical visual tree representation for workspace browsing, directory maps, and search result grouping.

```json
{
  "name": "file_tree",
  "description": "Hierarchical directory tree with collapsible folders, file icons, and file size indicators.",
  "modes": ["inline", "canvas"],
  "accepts_input": false,
  "data_schema": {
    "type": "object",
    "required": ["root"],
    "properties": {
      "title": { "type": "string" },
      "root": {
        "type": "object",
        "required": ["name", "type"],
        "properties": {
          "name": { "type": "string" },
          "type": { "type": "string", "enum": ["dir", "file"] },
          "size": { "type": "integer" },
          "children": {
            "type": "array",
            "items": { "$ref": "#/properties/root" }
          }
        }
      }
    }
  }
}
```

### 4.3 `multi_tab_view`
**Purpose**: Multi-tab container for tools returning heterogeneous aspects of an operation (e.g. `http_request` showing Status/Headers in Tab 1, JSON/Body in Tab 2; or search tools showing Results Table in Tab 1 and Excerpt Diffs in Tab 2).

```json
{
  "name": "multi_tab_view",
  "description": "Container displaying multiple sub-widgets inside switchable tab headers.",
  "modes": ["inline", "canvas"],
  "accepts_input": false,
  "data_schema": {
    "type": "object",
    "required": ["tabs"],
    "properties": {
      "active_tab": { "type": "string" },
      "tabs": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["id", "label", "widget_type", "data"],
          "properties": {
            "id": { "type": "string" },
            "label": { "type": "string" },
            "widget_type": { "type": "string" },
            "data": { "type": "object" }
          }
        }
      }
    }
  }
}
```

### 4.4 `timeline_view`
**Purpose**: Sequential visual event stream for temporal records (`conversation_search` matches, `notes_read` scratchpad chronology, `dream_recent_runs` history).

```json
{
  "name": "timeline_view",
  "description": "Vertical chronological timeline showing events with timestamps, icons, and expandable summaries.",
  "modes": ["inline", "canvas"],
  "accepts_input": false,
  "data_schema": {
    "type": "object",
    "required": ["events"],
    "properties": {
      "title": { "type": "string" },
      "events": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["timestamp", "title"],
          "properties": {
            "timestamp": { "type": "string" },
            "title": { "type": "string" },
            "description": { "type": "string" },
            "category": { "type": "string" },
            "status": { "type": "string", "enum": ["success", "pending", "failed", "info"] }
          }
        }
      }
    }
  }
}
```

### 4.5 `diff_review_card`
**Purpose**: Staged diff reviewer providing hunk-level accept/reject capabilities and line-by-line annotation support for agent code proposals.

```json
{
  "name": "diff_review_card",
  "description": "Enhanced patch review card displaying per-hunk diffs, addition/deletion metrics, and interactive approval markers.",
  "modes": ["inline", "canvas"],
  "accepts_input": true,
  "data_schema": {
    "type": "object",
    "required": ["filename", "hunks"],
    "properties": {
      "filename": { "type": "string" },
      "stats": {
        "type": "object",
        "properties": {
          "additions": { "type": "integer" },
          "deletions": { "type": "integer" }
        }
      },
      "hunks": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["header", "lines"],
          "properties": {
            "header": { "type": "string" },
            "lines": {
              "type": "array",
              "items": { "type": "string" }
            }
          }
        }
      }
    }
  }
}
```

---

## 5. Implementation Roadmap & Priority Matrix

| Phase | Target Tools | Target Widgets | Risk / Complexity | Status |
|---|---|---|---|---|
| **Phase 1** | `workspace_edit`<br>`workspace_diff`<br>`workspace_list`<br>`schedule_list` | `diff_view`<br>`diff_view`<br>`data_table`<br>`data_table` | Low / Zero breaking changes | **Current Target (Issue #931)** |
| **Phase 2** | `admin_edit`<br>`admin_list`<br>`shell_background_list`<br>`project_list` | `diff_view`<br>`data_table`<br>`data_table`<br>`data_table` | Low / Moderate scope | Planned Follow-up |
| **Phase 3** | `health_status`<br>`context_stats`<br>`http_request` | `key_value_inspector`<br>`multi_tab_view` | Medium / Requires new widget frontend | Proposed Widget Track |
