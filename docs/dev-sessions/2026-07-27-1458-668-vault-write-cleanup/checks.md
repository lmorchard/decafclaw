# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/668
**Frozen at:** 1add3f0 (2026-07-27)
**Check files — read-only from Phase 1 onward:**
- `src/decafclaw/web/static/components/wiki-metadata.test.js`

## C1

CRITERION: WHEN the vault write path validates a `frontmatter_raw` payload, it SHALL do so through
`frontmatter.parse_frontmatter_block`, the function it already imports, and SHALL NOT parse the
YAML itself.

CHECK: `grep -c "yaml\.safe_load" src/decafclaw/http_server.py` returns `0`.

AT FREEZE: returns **1** — the behavior is genuinely absent (there is one `yaml.safe_load` call
at `http_server.py:1457` inside the `frontmatter_raw` branch of `vault_write`).

## C2

CRITERION: GIVEN the raw-YAML editor is open and a raw save has been dispatched, WHEN the user
types more into the textarea before that request resolves and the save then succeeds, THEN the
text the user typed SHALL NOT be discarded by the post-save reseed.

CHECK: `npm test` in `src/decafclaw/web/static` — specifically the test
`wiki-metadata #raw-editor race > keystrokes typed while a raw save is in flight survive the save`
in `components/wiki-metadata.test.js`.

AT FREEZE: fails — `AssertionError: expected '_rawText' to contain user token after closeRaw +
frontmatterRaw assignment`. The reseed in `wiki-metadata.js:58-63` fires because `closeRaw()` has
already set `_rawOpen = false`, so the `!this._rawOpen` guard no longer holds.

## Guards

(Pass today; must keep passing. Not criteria — they can't fail at freeze.)

- **G1:** `uv run pytest tests/test_vault_api.py::test_vault_write_frontmatter_raw_malformed_is_rejected tests/test_vault_api.py::test_vault_write_frontmatter_raw_non_mapping_is_rejected`
  — the two existing rejection tests. Passed at freeze (2 passed).
- **G2:** `make test` — the full Python test suite. Passed at freeze (3606 passed, 2 skipped).
- **G3:** `npm test` in `src/decafclaw/web/static` — the full JS test suite, including existing
  `components/wiki-editor.test.js`. Passed at freeze (41 passed).

## Amendments

(Append-only. Empty unless an amendment was made.)

### Clarification: C2 test now exercises actual input path

**Date:** 2026-07-27
**Scope:** `wiki-metadata.test.js` — changes how the test simulates typing, not what it asserts

The frozen test manipulated `_rawText` directly (`el._rawText = ...`), bypassing the component's
`@input` handler. The fix for C2 tracks dirty state via that handler, so the direct mutation
didn't exercise the fix. The test now:
1. Clicks the actual toggle button to open the raw editor
2. Dispatches an `input` event on the textarea to simulate typing

**Why this is a clarification, not an amendment:** The assertion is unchanged
(`expect(el._rawText).toContain(userToken)`). The criterion is unchanged (keystrokes survive).
The test was written to simulate typing but didn't trigger the input path — it's a setup error,
not a change in what the criterion asserts.

**Re-run evidence:** With the original test (direct `_rawText` mutation) against the fix,
the test fails — the dirty flag is never set because the input handler never runs. With the
corrected test (input event dispatch) against the pre-fix code, the test also fails — the
reseed still overwrites. Both directions discriminate correctly after the clarification.

## Pre-squash tamper check

**Checked at:** 2026-07-27 before squash
**Command:** `git diff 1add3f0 -- src/decafclaw/web/static/components/wiki-metadata.test.js`
**Verdict:** Non-empty diff, explained by logged clarification above. The assertion is unchanged;
only the test setup changed to use actual DOM events instead of direct state mutation.
**Status:** clean-with-clarification
