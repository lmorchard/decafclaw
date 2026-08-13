# Implementation Plan

## Phase 0: Freeze
- [x] Author checks
- [x] Freeze commit

## Phase 1: Basic Textual App and Chunk Appending
**Advances:** C1
- Create `src/decafclaw/tui/__init__.py` and `src/decafclaw/tui/app.py`.
- Define `DecafClawApp` extending `textual.app.App`.
- Implement basic layout with a central `RichLog` or `Markdown` widget.
- Subscribe to `ConversationManager` events.
- Implement event handler for `chunk` to update the message log.
- [ ] `pytest tests/test_tui.py::test_tui_appends_chunks`

## Phase 2: Confirmation Modal
**Advances:** C2, G1
- Define `ConfirmationModal` extending `textual.screen.ModalScreen`.
- Implement `confirmation_request` handler to push the modal.
- Map Yes/No/Always inputs to `manager.respond_to_confirmation`.
- [ ] `pytest tests/test_tui.py::test_tui_shows_confirmation_modal`
- [ ] `rg "input\(" src/decafclaw/tui/`

## Phase 3: Sidebar and History Loading
**Advances:** C3, G2
- Add a `ListView` or `OptionList` Sidebar to the app layout.
- Fetch available conversations and populate the Sidebar.
- Handle conversation switch click events to clear log and load history.
- Ensure all loops use `manager.send_message` and standard flow.
- [ ] `pytest tests/test_tui.py::test_tui_switches_conversation_on_click`
