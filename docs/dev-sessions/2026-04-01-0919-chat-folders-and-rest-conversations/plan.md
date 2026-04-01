# Chat Folders and REST Conversations — Plan

_Implementation plan for GitHub issues #184, #187, plus user-defined chat folders_

## Overview

Seven phases, each building on the previous. The dependency chain:

1. **Folder Index Backend** — data model foundation
2. **REST Listing Endpoints** — folder-aware conversation listing
3. **REST Action Endpoints** — conversation + folder mutations
4. **Frontend Store Migration** — ConversationStore switches from WebSocket to REST
5. **Sidebar Folder Navigation** — file-browser UI replacing collapsible sections
6. **Folder Management UI** — create/rename/delete folders, move conversations
7. **WebSocket Cleanup** — remove dead WebSocket conversation handlers

---

## Phase 1: Folder Index Backend

**Goal:** Create the `ConversationFolderIndex` class that manages `conversation_folders.json` per user.

**Files to create/modify:**
- Create `src/decafclaw/web/conversation_folders.py`

### Prompt 1

```
Read src/decafclaw/web/conversations.py for context on how ConversationIndex works (file I/O patterns, path conventions, locking).

Create a new file src/decafclaw/web/conversation_folders.py with a ConversationFolderIndex class:

Data file location: data/{agent_id}/web/users/{username}/conversation_folders.json

JSON structure:
{
  "folders": ["projects", "projects/bot-redesign", "research"],
  "assignments": {
    "web-les-abc123": "projects/bot-redesign"
  }
}

Methods needed:
- __init__(self, config, username: str) — resolve file path, load or create empty
- _load() -> dict — read JSON, return default if missing
- _save(data: dict) -> None — atomic write (write to tmp, rename)
- list_folders(parent: str = "") -> list[str] — immediate child folder names under parent
- create_folder(path: str) -> bool — add to folders list, auto-create parents, reject "_" prefix, return success
- delete_folder(path: str) -> bool — remove if empty (no conversations assigned, no child folders), return success
- rename_folder(old_path: str, new_path: str) -> bool — update folder list + all assignments under old_path. If new_path exists, merge (combine conversations). Reject "_" prefix on new_path.
- get_folder(conv_id: str) -> str — return folder path or "" for top-level
- set_folder(conv_id: str, folder: str) -> bool — assign conversation to folder (folder must exist or be "")
- remove_assignment(conv_id: str) -> None — remove from assignments dict
- list_conversations_in_folder(folder: str = "") -> list[str] — return conv_ids assigned to this exact folder

Path validation: reject paths containing "..", leading "/", or starting with "_". Use a _validate_path() helper.

Use asyncio.Lock for thread safety on reads/writes. Follow the project convention of simple file-based storage.

Write tests in tests/test_conversation_folders.py covering:
- Create/delete/rename folders
- Assign/move/remove conversations
- Parent auto-creation
- "_" prefix rejection
- Merge on rename collision
- Empty folder deletion guard
- Path traversal rejection
```

**Verify:** `make test` passes, `make check` passes.

---

## Phase 2: REST Listing Endpoints

**Goal:** Add folder-aware listing endpoints that mirror the vault pattern, using the folder index.

**Files to modify:**
- `src/decafclaw/http_server.py` — add/update routes
- `src/decafclaw/web/conversations.py` — may need minor adjustments to list methods

### Prompt 2

```
Read these files for context:
- src/decafclaw/http_server.py (especially vault_list around line 243 for the folder listing pattern)
- src/decafclaw/web/conversations.py (ConversationIndex.list_for_user)
- src/decafclaw/web/conversation_folders.py (the folder index from Phase 1)

Update GET /api/conversations to support folder-aware listing:

1. Accept ?folder= query parameter (default: "" for top-level)
2. Load ConversationFolderIndex for the authenticated user
3. Get all conversations for the user via ConversationIndex.list_for_user()
4. Filter to only conversations assigned to the requested folder (or unassigned for top-level)
5. Get immediate child folders from the folder index
6. At top level, append virtual folders: {"name": "Archived", "path": "_archived", "virtual": true} and {"name": "System", "path": "_system", "virtual": true}
7. Return: { "folder": str, "folders": [...], "conversations": [...] }

Add GET /api/conversations/archived:
1. Accept ?folder= query parameter
2. List archived conversations (ConversationIndex.list_for_user with include_archived=True, filter archived=True)
3. Filter by folder assignment from the folder index
4. Return same shape as /api/conversations — child folders derived from assignments of archived conversations

Add GET /api/conversations/system:
1. Accept ?folder= query parameter
2. Use existing list_system_conversations() function
3. At top level, return sub-folders: heartbeat, scheduled, delegated
4. With ?folder=heartbeat (etc.), filter by conv_type
5. Return same response shape

All endpoints require @_authenticated. Follow the vault_list pattern for path validation.

Write tests for the new/updated endpoints.
```

**Verify:** `make test` passes, `make check` passes. Manually test with `curl`.

---

## Phase 3: REST Action Endpoints

**Goal:** Add REST endpoints for all conversation mutations: rename+move, unarchive, folder CRUD, and update create to accept folder.

**Files to modify:**
- `src/decafclaw/http_server.py`

### Prompt 3

```
Read src/decafclaw/http_server.py for existing route patterns.

Add/update these REST endpoints:

1. Update PUT or PATCH /api/conversations/{conv_id}:
   - Accept body: { "title": "...", "folder": "..." } (both optional)
   - If title provided: call ConversationIndex.rename()
   - If folder provided: call ConversationFolderIndex.set_folder() (validate folder exists or is "")
   - Return updated conversation dict + folder
   - Note: there's already a PATCH route — update it to also handle folder changes

2. Add POST /api/conversations/{conv_id}/unarchive:
   - Call ConversationIndex.unarchive()
   - Return {"ok": true}

3. Update POST /api/conversations:
   - Accept optional "folder" and "effort" in body
   - After creating, assign to folder via ConversationFolderIndex if provided
   - If effort provided (and not "default"), append effort message to archive (same logic as _handle_create_conv in websocket.py)

4. Add POST /api/conversations/folders:
   - Body: { "path": "projects/new-folder" }
   - Call ConversationFolderIndex.create_folder()
   - Return {"ok": true, "path": "..."} or 400/409 on error

5. Add DELETE /api/conversations/folders/{path:path}:
   - Call ConversationFolderIndex.delete_folder()
   - Return {"ok": true} or 409 if not empty

6. Add PUT /api/conversations/folders/{path:path}:
   - Body: { "path": "new/path" }
   - Call ConversationFolderIndex.rename_folder()
   - Return {"ok": true} or 400 on error

All endpoints require @_authenticated. Include proper error responses (400, 404, 409).

Write tests for each new endpoint.
```

**Verify:** `make test` passes, `make check` passes.

---

## Phase 4: Frontend Store Migration

**Goal:** Convert ConversationStore from WebSocket to REST for all conversation management operations. Keep WebSocket for real-time chat streaming and `conv_created` signal.

**Files to modify:**
- `src/decafclaw/web/static/lib/conversation-store.js`

### Prompt 4

```
Read these files:
- src/decafclaw/web/static/lib/conversation-store.js (full file — the store to migrate)
- src/decafclaw/web/static/lib/auth-client.js (REST call pattern to follow)
- src/decafclaw/web/static/components/conversation-sidebar.js (to understand what the store consumers expect)

Convert ConversationStore methods from WebSocket sends to REST calls:

1. listConversations(folder = '') → GET /api/conversations?folder=
   - Store response: _conversations, _folders, _currentFolder
   - Add new properties: _folders (array), _currentFolder (string)
   - Add getter: get folders(), get currentFolder()

2. listArchivedConversations(folder = '') → GET /api/conversations/archived?folder=
   - Store response: _archivedConversations, _archivedFolders, _archivedCurrentFolder

3. listSystemConversations(folder = '') → GET /api/conversations/system?folder=
   - Store response: _systemConversations, _systemFolders, _systemCurrentFolder

4. createConversation(title, effort, folder) → POST /api/conversations
   - Body: { title, effort, folder }
   - On success, re-fetch current folder listing

5. renameConversation(convId, title) → PATCH /api/conversations/{convId}
   - Body: { title }
   - On success, re-fetch current listing

6. moveConversation(convId, folder) → PATCH /api/conversations/{convId}
   - Body: { folder }
   - On success, re-fetch current listing

7. archiveConversation(convId) → POST /api/conversations/{convId}/archive
   - On success, re-fetch current listing

8. unarchiveConversation(convId) → POST /api/conversations/{convId}/unarchive
   - On success, re-fetch current listing

9. Folder management methods (new):
   - createFolder(path) → POST /api/conversations/folders
   - deleteFolder(path) → DELETE /api/conversations/folders/{path}
   - renameFolder(oldPath, newPath) → PUT /api/conversations/folders/{path}
   - Each re-fetches current listing on success

Remove WebSocket message handling for: conv_list, archived_list, system_conv_list, conv_renamed, conv_archived, conv_unarchived.

Keep WebSocket handling for: all chat streaming messages (chunk, message_complete, tool_start, tool_end, tool_status, turn_start, turn_end, etc.), conv_selected, conv_history, effort_changed, confirm_request, error.

On WebSocket open, call listConversations() via REST instead of sending list_convs message.

Use async/await with fetch(). Follow auth-client.js pattern for error handling.

Each method should call #emitChange() after updating state.
```

**Verify:** `make check-js` passes. Manually test in browser — sidebar should still load conversations.

---

## Phase 5: Sidebar Folder Navigation

**Goal:** Replace collapsible Archived/System sections with file-browser navigation matching the vault tab pattern. Add breadcrumbs, folder listing, virtual folder support.

**Files to modify:**
- `src/decafclaw/web/static/components/conversation-sidebar.js`

### Prompt 5

```
Read src/decafclaw/web/static/components/conversation-sidebar.js — study the vault tab's folder navigation pattern (#renderVaultBreadcrumbs, #navigateToFolder, #fetchWikiPages, #renderVaultTab) as the template.

Refactor the Chats tab to use the same file-browser navigation pattern:

1. Remove collapsible _showArchived / _showSystem sections and their toggle logic.

2. Add navigation state:
   - _chatSection: '' (active), '_archived', '_system' — which section we're in
   - _chatFolder: '' — current folder path within the section
   - These map to the REST endpoints: '' → /api/conversations, '_archived' → /api/conversations/archived, '_system' → /api/conversations/system

3. Add #navigateChatFolder(section, folder) method:
   - Updates _chatSection and _chatFolder
   - Calls the appropriate store method: store.listConversations(folder), store.listArchivedConversations(folder), or store.listSystemConversations(folder)

4. Add #renderChatBreadcrumbs() method (mirror #renderVaultBreadcrumbs):
   - When at top level (_chatSection = '', _chatFolder = ''): no breadcrumbs needed
   - When in Archived/System: show "Chats / Archived / subfolder / ..." with clickable segments
   - When in a user folder: show "Chats / folder / subfolder / ..." with clickable segments
   - Root "Chats" segment always navigates back to top level

5. Update #renderChatsTab() (currently #renderConversationList or similar):
   - Render breadcrumbs at top
   - List folders first (from store.folders), then conversations
   - Folders render with folder icon, click navigates into them
   - Virtual folders (Archived, System) render with folder icon but no rename/delete
   - User folders render with folder icon (rename/delete controls come in Phase 6)
   - Conversations render with existing item renderer
   - In Archived section: show unarchive button instead of archive button
   - In System section: no archive/unarchive buttons, show type badge

6. "+" button behavior:
   - Creates new conversation in the current folder (pass folder to createConversation)
   - Only show in active section, not in Archived or System

7. When a conversation is selected that's in a different folder, navigate to that folder.

8. Preserve scroll position and selected state across folder navigation.

Keep all existing vault tab code unchanged.
```

**Verify:** `make check-js` passes. Test in browser: navigate folders, breadcrumbs work, Archived/System as virtual folders, creating conversations in folders.

---

## Phase 6: Folder Management UI

**Goal:** Add UI for creating, renaming, deleting user-defined folders, and moving conversations between folders.

**Files to modify:**
- `src/decafclaw/web/static/components/conversation-sidebar.js`

### Prompt 6

```
Read src/decafclaw/web/static/components/conversation-sidebar.js — study how the vault tab handles folder creation (+ Folder button), page rename (inline input), and page delete (delete button with confirm).

Add folder management to the Chats tab:

1. "+ Folder" button next to "+ Chat" in the sidebar header:
   - Shows inline input field (like vault's folder creation)
   - On submit, calls store.createFolder(currentPath + '/' + name) or store.createFolder(name) at top level
   - Validates: no "_" prefix, no empty name
   - Re-fetches listing on success

2. Folder rename (inline):
   - Double-click on a user-created folder name → editable input (same pattern as conversation rename)
   - On submit, calls store.renameFolder(oldPath, newPath)
   - Not available on virtual folders (Archived, System)

3. Folder delete:
   - Small delete button on user-created folders (hidden on hover, like vault)
   - Confirmation before delete
   - Calls store.deleteFolder(path)
   - Shows error if folder is not empty
   - Not available on virtual folders

4. Move conversation:
   - When renaming a conversation, support path syntax: "folder/New Title"
   - Parse the path to extract folder + title
   - Call store.moveConversation(convId, folder) + store.renameConversation(convId, title)
   - Alternative: add a "Move to..." action that shows a folder picker (simpler UX, could defer)

5. Accessibility:
   - Focus management on inline inputs
   - Keyboard support (Enter to confirm, Escape to cancel)
   - aria-labels on icon-only buttons
   - :focus-visible styles

Match the vault tab's visual style for consistency.
```

**Verify:** `make check-js` passes. Test in browser: create folders, rename, delete, move conversations.

---

## Phase 7: WebSocket Cleanup

**Goal:** Remove dead WebSocket conversation management handlers from backend and frontend.

**Files to modify:**
- `src/decafclaw/web/websocket.py`
- `src/decafclaw/web/static/lib/conversation-store.js`

### Prompt 7

```
Read:
- src/decafclaw/web/websocket.py
- src/decafclaw/web/static/lib/conversation-store.js

Remove WebSocket handlers that are now replaced by REST:

Backend (websocket.py):
- Remove _handle_list_convs and the "list_convs" dispatch entry
- Remove _handle_list_archived and the "list_archived" dispatch entry
- Remove _handle_list_system_convs and the "list_system_convs" dispatch entry
- Remove _handle_archive_conv and the "archive_conv" dispatch entry
- Remove _handle_unarchive_conv and the "unarchive_conv" dispatch entry
- Remove _handle_rename_conv and the "rename_conv" dispatch entry
- Remove _handle_create_conv and the "create_conv" dispatch entry (creation is now REST-only via POST /api/conversations)
- Keep: _handle_send, _handle_select_conv, _handle_load_history, _handle_set_effort, _handle_cancel_turn, _handle_confirm_response

Frontend (conversation-store.js):
- Remove WebSocket message handlers for: conv_list, archived_list, system_conv_list, conv_renamed, conv_archived, conv_unarchived
- Remove conv_created handler (creation is now REST-only)
- Keep handlers for: conv_selected, conv_history, chunk, message_complete, tool_start, tool_end, tool_status, error, turn_start, turn_end, reflection, compaction_start, compaction_end, effort_changed, confirm_request

Verify no remaining references to removed message types in either file.

Run make check and make test to confirm nothing is broken.
```

**Verify:** `make test` passes, `make check` passes. Full browser test of conversation flow.

---

## Final Verification Checklist

After all phases:
- [ ] Conversation list loads via REST (not WebSocket) — #184
- [ ] Archived/System shown as navigable folders with breadcrumbs — #187
- [ ] System sub-folders: heartbeat, scheduled, delegated
- [ ] User-defined folders: create, rename, delete, navigate
- [ ] Conversations can be moved between folders
- [ ] Archive preserves folder assignment
- [ ] Unarchive restores to original folder
- [ ] New conversations created in current folder
- [ ] Virtual folders (Archived, System) are read-only
- [ ] "_" prefix reserved for virtual folders
- [ ] Folder rename with collision merges
- [ ] WebSocket only used for real-time chat streaming
- [ ] No dead WebSocket handlers remain
- [ ] `make test` passes
- [ ] `make check` passes
- [ ] Browser smoke test: full conversation lifecycle

## Docs to Update

- `docs/` — web UI docs, API docs (if they exist)
- `CLAUDE.md` — key files list (conversation_folders.py), conventions (REST-only conversation management)
- `README.md` — if API surface is documented there
