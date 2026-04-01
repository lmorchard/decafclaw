# Chat Folders and REST Conversations — Spec

_Session for GitHub issues #184, #187, plus general chat folder organization_

## Issues

- **#184** — Load conversation list via REST API instead of WebSocket
- **#187** — Chat sidebar: folder-style navigation for archived and system conversations
- **General** — User-defined folder organization for conversations

## Summary

Replace all WebSocket-based conversation management (list, archive, rename) with REST endpoints. Add folder-style navigation to the chat sidebar — matching the vault tab's file-browser pattern — with three virtual folder sections (active, archived, system) plus user-defined folders. Conversations are organized via a per-user folder index file; actual JSONL archive files stay in place (no filesystem moves).

## Decisions

- **REST-only conversation management.** All list/archive/rename/folder operations go through REST. WebSocket stays focused on real-time chat streaming only.
- **Remove WebSocket conversation ops.** Delete `list_convs`, `list_archived`, `list_system_convs`, `archive`, `unarchive`, `rename` message handlers. No deprecation period — the web UI is the only client.
- **Folder index file, not per-conversation metadata.** A flat JSON file per user tracks folder structure and conversation-to-folder assignments. Conversations not in the index default to top-level. Avoids scanning all conversations on every list request.
- **Metadata-only folders.** Conversation files stay in their current filesystem location. Folders are a UI/API concept tracked in the index file, not filesystem directories.
- **Archive preserves folder structure.** Archiving a conversation in `projects/bot-redesign` keeps that folder assignment. The Archived section mirrors the user's folder structure. Unarchiving restores the conversation to its original folder.
- **System sub-folders by type.** System conversations are grouped into sub-folders: heartbeat, schedule, delegated.
- **Virtual folders are read-only.** Archived and System (and their sub-folders) support navigation but not rename or delete.

## Data Model

### Folder Index File

Location: `data/{agent_id}/web/users/{username}/conversation_folders.json`

```json
{
  "folders": ["projects", "projects/bot-redesign", "research"],
  "assignments": {
    "web-les-abc123": "projects/bot-redesign",
    "web-les-def456": "research"
  }
}
```

- `folders`: explicit list of user-created folders (supports empty folders)
- `assignments`: maps conv_id → folder path. Absent conv_ids are top-level.
- Flat structure — no tree nesting. Arbitrary depth supported.
- Folder names starting with `_` are reserved for virtual folders (`_archived`, `_system`). User-created folders with `_` prefix are rejected.

### ConversationMeta

No changes to the dataclass. Folder assignment lives in the index file, not on the conversation metadata.

## REST API

### Conversation Listing

**`GET /api/conversations?folder=`**
- Returns conversations + subfolders in the given folder (default: top-level)
- Top-level also includes virtual folder entries for "Archived" and "System"
- Response mirrors vault pattern:

```json
{
  "folder": "projects",
  "folders": [
    { "name": "bot-redesign", "path": "projects/bot-redesign" }
  ],
  "conversations": [
    {
      "conv_id": "web-les-abc123",
      "title": "Chat about tools",
      "created_at": "2026-03-31T10:00:00+00:00",
      "updated_at": "2026-03-31T10:00:00+00:00"
    }
  ]
}
```

- Top-level response includes additional virtual folders:

```json
{
  "folder": "",
  "folders": [
    { "name": "projects", "path": "projects" },
    { "name": "Archived", "path": "_archived", "virtual": true },
    { "name": "System", "path": "_system", "virtual": true }
  ],
  "conversations": [ ... ]
}
```

**`GET /api/conversations/archived?folder=`**
- Same shape, scoped to archived conversations
- Folder structure mirrors the user's folders (archive preserves folder assignment)

**`GET /api/conversations/system?folder=`**
- Same shape, with sub-folders for conversation types: `heartbeat`, `schedule`, `delegated`

### Conversation Actions

**`PUT /api/conversations/{conv_id}`**
- Rename and/or move a conversation
- Request body: `{ "title": "New Title", "folder": "projects/bot-redesign" }`
- Either field optional; both can change at once
- Moving updates the folder index; renaming updates ConversationMeta

**`POST /api/conversations/{conv_id}/archive`**
- Archives the conversation, preserving its folder assignment

**`POST /api/conversations/{conv_id}/unarchive`**
- Restores the conversation to its original folder

**`POST /api/conversations`**
- Create a new conversation
- Request body: `{ "folder": "projects" }` (optional — defaults to top-level)

### Folder Management

**`POST /api/conversations/folders`**
- Create a folder
- Request body: `{ "path": "projects/new-folder" }`
- Auto-creates parent folders if needed

**`DELETE /api/conversations/folders/{path}`**
- Delete an empty folder
- Returns 409 if folder contains conversations

**`PUT /api/conversations/folders/{path}`**
- Rename/move a folder
- Request body: `{ "path": "new/path" }`
- Updates all conversation assignments under the old path
- If the target path already exists, merge: conversations from both folders end up in the target, sub-folders are combined

## Sidebar UI

### File-Browser Navigation (matching vault pattern)

- **Top level** shows: user-created folders, active conversations not in any folder, plus virtual folder entries for Archived and System
- **Clicking a folder** replaces the list with that folder's contents + breadcrumbs
- **Breadcrumbs** at top — each segment clickable to navigate up
- **Folders listed first** (alphabetically), then conversations (by updated_at descending)

### Virtual Folders

- **Archived** — navigable with folder structure mirroring user folders. Conversations show unarchive action.
- **System** — navigable with sub-folders (heartbeat, schedule, delegated). No archive/unarchive actions.
- Virtual folders display with folder icon but no rename/delete controls.

### Conversation Actions in Sidebar

- **Select** — click to open
- **Rename/Move** — inline rename (like vault), path change moves between folders
- **Archive** — button on active conversations
- **Unarchive** — button on archived conversations
- **New Conversation** — creates in the currently viewed folder

### Folder Actions in Sidebar

- **Create folder** — button in sidebar (like vault's "+ Folder")
- **Rename/Move folder** — inline rename
- **Delete folder** — only if empty

## WebSocket Cleanup

Remove these message handlers from `websocket.py`:
- `list_convs` / `conv_list`
- `list_archived` / `archived_list`
- `list_system_convs` / `system_conv_list`
- `archive_conv` / `conv_archived`
- `unarchive_conv` / `conv_unarchived`
- `rename_conv` / `conv_renamed`

Remove corresponding methods from `ConversationStore` on the frontend.

Retain WebSocket for:
- Real-time chat messages (send/receive streaming)
- Conversation selection and history loading (session state)
- Effort level changes, turn cancellation, confirmation requests
- Any other real-time streaming events

## Deferred (Future Issues)

- **Filesystem path hashing** — hash-based subdirectories for conversation JSONL files to avoid large flat directories
- **Archive date-based organization** — pagination or date grouping for the archived conversations list
- **Drag-and-drop** — moving conversations between folders via drag
