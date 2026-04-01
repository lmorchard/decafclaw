# Wiki Chat Context — Spec

GitHub Issue: #168

## Overview

When a user is viewing a wiki page in the web UI side panel, or references a wiki page with `@[[PageName]]` syntax in their message, automatically inject that page's content into the conversation context so the agent can discuss it.

## Feature 1: Auto-context from open wiki page

When the user sends a chat message while a wiki page is open in the side panel, the page content is injected into the conversation as context.

- The web UI includes an optional `wiki_page` field in the WebSocket `send` message, set to the currently open page name (or omitted if no page is open).
- The server checks whether that page has already been injected for this conversation. If not, it injects the page content as a one-time context message.
- If the user navigates to a different wiki page and sends another message, the new page is also injected (since it hasn't been seen before).
- Pages that have already been injected are skipped — no repeated injection even if the page is still open.

## Feature 2: @[[PageName]] inline references

Users can reference wiki pages in their message text using `@[[PageName]]` syntax. The page content is injected as context, similar to the auto-context feature.

- The `@[[PageName]]` text is left as-is in the user's message — the agent sees the literal reference.
- The server parses `@[[...]]` patterns from the message text and resolves each page.
- Page content is injected as a one-time context message, using the same tracking as auto-context (shared "already injected" set per conversation).
- If a referenced page does not exist, an error note is injected: `[Wiki page 'PageName' not found]`.

## Injection mechanism

- **`@[[PageName]]` parsing happens in `agent.py`**, not in any channel-specific handler. This means it works across all channels: web, Mattermost, terminal.
- Context messages use a role similar to `memory_context` — a separate message injected before the user's turn, remapped to "user" role for the LLM.
- Tracking of which pages have been injected is determined by scanning conversation history for existing `wiki_context` role messages. No separate state dict needed — history is the source of truth.
- No token budget or truncation for now — full page content is injected. Address if it becomes a problem.

## System prompt

A brief note is added to the system prompt explaining:
- `@[[PageName]]` references mean the user is referencing a wiki page whose content has been provided.
- `[Currently viewing wiki page: PageName]` means the user has that page open in the side panel.

This helps the agent understand and respond to these annotations.

## Channel-specific changes

- **Web UI**: `conversation-store` includes `wiki_page` field in `send` messages when a wiki page is open. WebSocket handler passes it through to agent via `ctx`.
- **Mattermost / Terminal**: No changes needed — `@[[PageName]]` parsing in the agent works automatically.
- No autocomplete UI for `@[[...]]` in this iteration.

## Agent changes (channel-agnostic)

- `agent.py` `_prepare_messages()`: parse `@[[...]]` from user message text, resolve pages via wiki skill's `_resolve_page`, plus handle optional `wiki_page` from ctx.
- Scan conversation history for existing `wiki_context` messages to determine already-injected pages.
- Inject `wiki_context` role messages for new pages only.

## Deferred

- Autocomplete for `@[[...]]` (and workspace file references) — future issue.
- Token budget / truncation for large pages.
