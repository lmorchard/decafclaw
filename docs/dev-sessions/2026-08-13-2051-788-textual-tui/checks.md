# Frozen checks

- C1: `pytest tests/test_tui.py::test_tui_appends_chunks`
- C2: `pytest tests/test_tui.py::test_tui_shows_confirmation_modal`
- C3: `pytest tests/test_tui.py::test_tui_switches_conversation_on_click`
- G1: `rg "input\(" src/decafclaw/tui/`
- G2: Code review confirms `tui` acts purely as an event subscriber and `manager.send_message` caller

## Adjudication

- C1: Good. Appending chunks tests real-time reactive streaming. Fails with ModuleNotFoundError.
- C2: Good. Modal overlay tests blocking for confirmation requests without raw `input()`. Fails with ModuleNotFoundError.
- C3: Good. Conversation switching tests history loading in UI. Fails with ModuleNotFoundError.
- G1: Good. Ensures no raw `input()` blocking calls.
- G2: Good. Ensures ConversationManager is used correctly.
Freeze commit: 226d681ee2b92d27569ecd7c2120e065963cc2cb
