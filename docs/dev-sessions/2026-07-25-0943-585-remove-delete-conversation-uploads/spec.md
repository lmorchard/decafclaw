# Spec — remove production-orphaned `delete_conversation_uploads`

**Source:** https://github.com/lmorchard/decafclaw/issues/585

Captured verbatim from the issue body (the `<!-- agent-session:spec -->` marker and the
triage footer stripped). This is the oracle; nothing below is authored by the implementer.

---

Follow-up to #576 / #578.

`src/decafclaw/attachments.py::delete_conversation_uploads` is no longer called from production code — the conversation-delete handler now calls `delete_conversation_files`, which `rmtree`s the whole `{conv_id}/` dir (uploads included). Only its own unit tests in `tests/test_attachments.py` still reference it.

Decide: remove it (and its tests), or keep it as a documented standalone utility. If kept, a one-line docstring noting it's a utility (not on the delete path) would prevent future "why is this unused?" confusion.

Context: `docs/dev-sessions/2026-06-10-1728-conversation-sidecar-dirs/notes.md`.

---

## Acceptance criteria (agent-session triage)

**Decision resolved at triage:** *remove* the function (the issue's first branch). Grounding: production delete goes through `delete_conversation_files` (`src/decafclaw/conversation_paths.py`, called from `src/decafclaw/http_server.py:657-674`), and `docs/dev-sessions/2026-06-10-1728-conversation-sidecar-dirs/notes.md` records that the `rmtree` of `{conv_id}/` already covers uploads — so dropping the separate call was safe.

**C1 — the orphaned function is gone.**
- CHECK: `grep -c "def delete_conversation_uploads" src/decafclaw/attachments.py` returns `0`.
- Verified discriminating at triage: returns `1` today (`src/decafclaw/attachments.py:96`).

**C2 — its dedicated tests are gone with it.**
- CHECK: `grep -c "def test_delete_conversation_uploads" tests/test_attachments.py` returns `0`.
- Verified discriminating at triage: returns `2` today.

### Regression guards (pass today; must keep passing — not criteria)

- **G1:** `uv run pytest tests/test_attachments.py -q` — the rest of the attachment suite (save / list / read / uploads_dir sandboxing). Observed at triage: `11 passed in 15.01s`.
- **G2:** the real production delete path is untouched — `grep -n "delete_conversation_files" src/decafclaw/http_server.py` still shows the import and the call. Observed at triage: 2 hits.

## Tier: `auto-ok`

Both criteria reduce to greps that fail today; no risk-gated path (no auth, secrets, migration, deploy/CI, or dependency changes) — the blast radius is one dead function and its tests. The issue was originally `needs-review` only because it posed a binary decision rather than specifying an outcome; that decision is now made above.
