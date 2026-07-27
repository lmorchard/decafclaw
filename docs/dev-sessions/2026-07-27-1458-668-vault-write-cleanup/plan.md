# Vault Write Path Cleanups Implementation Plan

**Goal:** Remove duplicated frontmatter validation and fix keystroke loss race in raw-YAML editor.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/668 — **Tier:** `auto-ok` (both criteria reduce to concrete commands, no risk-gated paths)

**Approach:**
- C1: Replace the inline `yaml.safe_load` in `vault_write` with a call to `parse_frontmatter_block`, accepting its single error message.
- C2: Track whether the raw editor's content has been modified since the save was dispatched, and skip the reseed if so.

**Criteria:** C1 vault_write no longer re-implements frontmatter validation · C2 keystrokes typed while a raw save is in flight survive the save

---

## Phase 0: Freeze the acceptance checks ✓

Write `checks.md` and author the tests the checks name, per `references/frozen-checks.md`.
No implementation in this phase.

**Files:**
- Create: `docs/dev-sessions/2026-07-27-1458-668-vault-write-cleanup/checks.md` — criteria + checks copied verbatim from the spec, ids assigned
- Create: `src/decafclaw/web/static/components/wiki-metadata.test.js` — the test C2 names

**Verification — automated:**
- [x] C1's check runs and **fails for the expected reason**: `grep -c "yaml\.safe_load" src/decafclaw/http_server.py` returns `1` (should be `0`)
- [x] C2's check runs and **fails for the expected reason**: `npm test` fails with `expected 'a: 2' to contain 'b: user-typed-during-flight'`
- [x] G1 passes: `uv run pytest tests/test_vault_api.py::test_vault_write_frontmatter_raw_malformed_is_rejected tests/test_vault_api.py::test_vault_write_frontmatter_raw_non_mapping_is_rejected` — 2 passed
- [x] G2 passes: `make test` — 3606 passed, 2 skipped
- [x] G3 passes: `npm test` (pre-freeze) — 41 passed
- [x] Freeze commit made: `1add3f0`; sha recorded in `checks.md`: `6170fc2`

---

## Phase 1: Remove duplicated frontmatter validation

Replace the inline YAML parsing in `vault_write`'s `frontmatter_raw` branch with a call to `parse_frontmatter_block`.

**Advances:** C1

**Files:**
- Modify: `src/decafclaw/http_server.py` — replace inline `yaml.safe_load` with `parse_frontmatter_block` call

**Key changes:**

Current code at `http_server.py:1456-1465`:
```python
try:
    parsed = yaml.safe_load(stripped)
except yaml.YAMLError as exc:
    return JSONResponse(
        {"error": f"invalid YAML: {exc}"}, status_code=400,
    )
if not isinstance(parsed, dict):
    return JSONResponse(
        {"error": "frontmatter_raw must be a mapping"}, status_code=400,
    )
```

Replace with:
```python
_, fm_error = parse_frontmatter_block(stripped)
if fm_error is not None:
    return JSONResponse({"error": fm_error}, status_code=400)
```

Note: The `---` line check at line 1451 must remain — `parse_frontmatter_block` doesn't check for embedded `---` delimiters, and that is a semantic constraint specific to vault_write (you can't store a block that would terminate itself on next read).

**Verification — automated:**
- [ ] C1's check passes: `grep -c "yaml\.safe_load" src/decafclaw/http_server.py` returns `0`
- [ ] G1 still passes: `uv run pytest tests/test_vault_api.py::test_vault_write_frontmatter_raw_malformed_is_rejected tests/test_vault_api.py::test_vault_write_frontmatter_raw_non_mapping_is_rejected`
- [ ] G2 still passes: `make test`
- [ ] `make lint` passes

---

## Phase 2: Fix keystroke loss in raw-YAML editor

Track whether `_rawText` has been modified since the save was dispatched, and skip the `willUpdate` reseed if the user typed during the flight.

**Advances:** C2

**Files:**
- Modify: `src/decafclaw/web/static/components/wiki-metadata.js` — add dirty tracking, skip reseed when dirty

**Key changes:**

Add a new state property to track whether the raw editor was modified after a save was dispatched:
```javascript
_rawDirty: { state: true },  // true if _rawText changed since last closeRaw/save dispatch
```

Initialize in constructor:
```javascript
this._rawDirty = false;
```

Mark dirty on input:
```javascript
@input=${(/** @type {Event} */ e) => {
  this._rawText = /** @type {HTMLTextAreaElement} */ (e.target).value;
  this._rawDirty = true;  // User typed something
}}
```

Clear dirty on save dispatch (in `#saveRaw`):
```javascript
#saveRaw() {
  this._rawError = '';
  this._rawDirty = false;  // About to save, reset the flag
  this.dispatchEvent(new CustomEvent('metadata-raw-save', {
    detail: { raw: this._rawText },
    bubbles: true,
    composed: true,
  }));
}
```

Clear dirty when opening raw editor (in `#toggleRaw`):
```javascript
if (this._rawOpen) {
  this._rawText = this.frontmatterRaw;
  this._rawDirty = false;  // Fresh copy from server
}
```

Update `willUpdate` to skip reseed when dirty:
```javascript
willUpdate(changed) {
  // Reseed the raw editor from the server's bytes whenever the page's
  // frontmatter changes underneath us, unless the user is mid-edit OR
  // the user typed while a save was in flight (dirty flag set).
  if (changed.has('frontmatterRaw') && !this._rawOpen && !this._rawDirty) {
    this._rawText = this.frontmatterRaw;
  }
}
```

Clear dirty after successful save (host calls `closeRaw`):
```javascript
closeRaw() {
  this._rawOpen = false;
  this._rawError = '';
  // Don't clear _rawDirty here — if it's true, the user typed during the flight
  // and we should NOT reseed from the server response. The dirty flag stays
  // true until the next save dispatch or the user reopens the raw editor.
}
```

Actually, the simpler fix: don't clear `_rawDirty` in `closeRaw()`, and let `willUpdate` check it. But we also need to clear it eventually — otherwise the editor would never reseed. The safest approach:

- In `#saveRaw()`: set `_rawDirty = false` (we're about to save this content)
- In `#toggleRaw()` when opening: set `_rawDirty = false` (fresh from server)
- In `willUpdate`: skip reseed if `_rawDirty` is true
- On input: set `_rawDirty = true`

This way, if the user types after `#saveRaw()` sets dirty=false, the flag goes true and the reseed is skipped.

**Verification — automated:**
- [ ] C2's check passes: `npm test` in `src/decafclaw/web/static` passes (wiki-metadata.test.js now green)
- [ ] G3 still passes: `npm test` — all JS tests pass
- [ ] `make check-js` passes

---

## Phase 3: Final verification and cleanup

Ensure all checks pass and the implementation is complete.

**Advances:** (verification only, no new criteria)

**Files:**
- No changes

**Verification — automated:**
- [ ] C1's check passes: `grep -c "yaml\.safe_load" src/decafclaw/http_server.py` returns `0`
- [ ] C2's check passes: `npm test` in `src/decafclaw/web/static` — wiki-metadata test passes
- [ ] G1 still passes: `uv run pytest tests/test_vault_api.py::test_vault_write_frontmatter_raw_malformed_is_rejected tests/test_vault_api.py::test_vault_write_frontmatter_raw_non_mapping_is_rejected`
- [ ] G2 still passes: `make test`
- [ ] G3 still passes: `npm test` in `src/decafclaw/web/static`
- [ ] `make check` passes (lint + typecheck)
