# Notes — Turn-closure archive markers (#517)

## Issue summary

Follow-up to #491. Three turn-abort paths besides cancel may leave the archive without a clear "turn closed" signal, so the next user turn sees an open prior request and may re-fulfill it:

1. **Generic exception handler** in `conversation_manager.py` (confirmed missing archive write).
2. **Max-iterations exhaustion** (needs verification — likely OK but trace edge cases).
3. **Circuit breaker** (probably non-issue; just confirm).

Cross-link: the `abort_recovery.yaml` eval suite in #528 depends on this fix.

## Pattern reference
- `_write_cancel_archive` + `_write_cancel_marker_once` in `conversation_manager.py`
- `cancel_marker` role in `context_composer.ROLE_REMAP`
- Spec/notes: `docs/dev-sessions/2026-05-15-1219-fix-491-cancelled-turn-archive/`
