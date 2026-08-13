**Concept from opencode:**
`opencode` treats its Terminal User Interface (TUI) as a first-class citizen using a reactive component framework (`@opentui/solid`, an Ink/SolidJS port for terminals). It provides a full application lifecycle: declarative routing (`routes/session`), context providers (`DialogProvider`, `ProjectProvider`), and rich UI components (`CommandPalette`, `Sidebar`).

**How `decafclaw` could implement this:**
`decafclaw`'s current TUI (`interactive_terminal.py`) is a barebones REPL loop using raw `print()` and `input()`. It lacks sophisticated layout, overlays, or interactive menus.

**Proposed Implementation:**
- Adopt a modern Python TUI framework like **Textual** (by Textualize) which provides a React-like component model for the terminal (using CSS-like styling and reactive properties).
- Build a dedicated `decafclaw_tui` module that replaces the raw `interactive_terminal.py` loop.
- Key features to implement using the framework:
  - **Sidebar/History Pane**: showing conversation history or file structure.
  - **Overlays/Dialogs**: Using Textual's modal system for `EndTurnConfirm` or tool execution approvals, instead of relying on inline Y/n prompts that interrupt the stream.
  - **Streaming Markdown Renderer**: A scrollable central widget that formats LLM markdown in real-time.

## Verifiable acceptance criteria

- CRITERION: WHEN the agent yields `chunk` events, the TUI SHALL append the text to the current active message widget and trigger a render update in real-time.
  CHECK: `pytest tests/test_tui.py::test_tui_appends_chunks` passes. (Uses Textual's `app.run_test()` pilot to emit chunks and verify the document updates).

- CRITERION: WHEN a `confirmation_request` event arrives, the TUI SHALL push a modal overlay screen and SHALL pause turn processing until the user selects an option.
  CHECK: `pytest tests/test_tui.py::test_tui_shows_confirmation_modal` passes. (Asserts that `app.screen` becomes a `ConfirmationModal` and `manager.respond_to_confirmation` is called upon button press).

- CRITERION: WHEN the user selects a conversation from the Sidebar pane, the TUI SHALL load and display that conversation's history in the main message log.
  CHECK: `pytest tests/test_tui.py::test_tui_switches_conversation_on_click` passes.

## Regression guards

- GUARD: `rg "input\(" src/decafclaw/tui/` returns 0 — The TUI module SHALL NOT use the built-in `print()` or `input()` functions to block the event loop for user input. Passes today.
- GUARD: Code review confirms `tui` acts purely as an event subscriber and `manager.send_message` caller — The TUI SHALL utilize the existing `ConversationManager` rather than reimplementing the agent loop. Passes today.

## Design decisions

- **Decision:** Add `textual` dependency and adopt Textual for the TUI, with a first best-effort at look & feel using opencode as an inspiration.
  - **Why:** Explicitly approved by user (Les Orchard) in issue comments.
  - **Rejected:** Keeping raw REPL loop or simpler non-reactive print/input terminal.

## Tier: auto-ok

Approved by human user (Les Orchard: "I think adding the textual dependency is fine. And let's maybe do a first best-effort at the look & feel, maybe using opencode as an inspiration."). Risk-gated dependency addition ratified.
