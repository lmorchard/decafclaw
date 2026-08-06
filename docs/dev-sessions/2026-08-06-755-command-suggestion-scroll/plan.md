# Command suggestion menu scroll Implementation Plan

**Goal:** Scroll the highlighted command suggestion item into view when navigating suggestions with ArrowDown/ArrowUp.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/755 — **Tier:** `auto-ok` (specific mechanism on specific element, harness exists, discriminates, no risk paths)

**Approach:** Implement Lit's `updated(changedProperties)` hook in `ChatInput` (`src/decafclaw/web/static/components/chat-input.js`). When `_highlight` or `_trigger` changes, locate `.command-menu-item.highlighted` in the updated DOM and call `scrollIntoView({ block: "nearest" })`.

**Criteria:** C1 (scroll highlighted item into view with `block: "nearest"`) · C2 (wrap case targets row N-1)

---

## Phase 0: Freeze the acceptance checks

Write `checks.md` and author the tests C1 and C2 in `chat-input.test.js`, per `references/frozen-checks.md`.

**Files:**
- Created: `docs/dev-sessions/2026-08-06-755-command-suggestion-scroll/checks.md`
- Created: `docs/dev-sessions/2026-08-06-755-command-suggestion-scroll/spec.md`
- Modified: `src/decafclaw/web/static/vitest.setup.js`
- Modified: `src/decafclaw/web/static/components/chat-input.test.js`

**Verification — automated:**
- [x] Every criterion's check runs and **fails for the expected reason**: `AssertionError: expected "vi.fn()" to be called 1 times, but got 0 times` for both C1 and C2.
- [x] Every guard runs and **passes**: 22 pre-existing tests pass.
- [x] Adjudication recorded in `checks.md`.
- [x] Freeze commit `d5df31a` made and recorded.

---

## Phase 1: Implement command suggestion menu scrolling in `chat-input.js`

Add `updated(changedProperties)` lifecycle hook in `ChatInput` to scroll `.command-menu-item.highlighted` into view with `{ block: "nearest" }` when `_highlight` or `_trigger` changes.

**Advances:** C1, C2

**Files:**
- Modify: `src/decafclaw/web/static/components/chat-input.js` — add `updated(changedProperties)` lifecycle method.

**Key changes:**
- `updated(changedProperties)` hook:
```javascript
  updated(changedProperties) {
    super.updated(changedProperties);
    if (changedProperties.has('_highlight') || changedProperties.has('_trigger')) {
      const highlighted = this.querySelector('.command-menu-item.highlighted');
      highlighted?.scrollIntoView?.({ block: 'nearest' });
    }
  }
```

**Verification — automated:**
- [ ] C1's check passes: `export PATH="$HOME/.nvm/versions/node/v22.16.0/bin:$PATH" && npx vitest run components/chat-input.test.js`
- [ ] C2's check passes: `export PATH="$HOME/.nvm/versions/node/v22.16.0/bin:$PATH" && npx vitest run components/chat-input.test.js`
- [ ] Guards still pass: `export PATH="$HOME/.nvm/versions/node/v22.16.0/bin:$PATH" && npx vitest run components/chat-input.test.js components/chat-input-caret.test.js`
- [ ] `make check` passes
- [ ] `make test` passes
