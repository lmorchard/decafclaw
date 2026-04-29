# Web UI design system — spec

Issue: [#426](https://github.com/lmorchard/decafclaw/issues/426)
Branch: `web-ui-design-system`
Session: `docs/dev-sessions/2026-04-29-1417-web-ui-design-system/`

> **Scope-shift note (added during plan phase):** This spec was written to scope option **B** (doc + floating-btn consolidation, defer icon-button primitive to a follow-up). During plan-phase verification, research-pass-2 found that the floating-btn migrations were *already done* (research-pass-1 missed `index.html` and `app.js`), invalidating the premise of B. Scope was promoted to **B'** — doc + floating-btn cleanup *and* coining the `.dc-icon-btn` primitive + migrating its 13 sites. Decisions 1 and 3 below describe the original scope; the actual delivered scope is reflected in `plan.md`'s self-review section and the PR. Specifically: 4 primitives × {light, dark} = 8 PNGs (not 6), and the icon-button primitive ships in this PR (not deferred).

## Goal

Produce a shared visual-layer vocabulary so future styling asks read as *"match the floating-control primitive"* rather than *"match the hamburger button."* The PR #408 styling iteration ran ~30 round-trips of one-off CSS tweaks because consistency expectations were system-level but each request looked element-scoped. This session makes the system-level layer explicit.

## Current state

**Three shared primitive classes already exist** in `src/decafclaw/web/static/styles/variables.css`:
- `.dc-floating-btn` (variables.css:66–80) — border, radius, shadow, 44px tap target
- `.dc-overlay-header` (variables.css:48–57, mobile-only) — flex header row for mobile overlays
- `button.dc-overlay-close-x` (variables.css:29–41) — close-X glyph

**Applied in 6 places** across components: `chat-view.js:185`, `conversation-sidebar.js:466,475`, `canvas-panel.js:207,208,214`.

**Inconsistency surfaced in research:** two existing buttons re-declare floating-btn properties locally instead of applying the class:
- `.hamburger-btn` at `mobile.css:50–60` (and `resize.css:26`) — comment at `mobile.css:46` *claims* it uses the primitive, but the class isn't applied
- `.canvas-resummon-pill` at `canvas.css:92–123` — sets the same border/radius/shadow inline

**Unrecognized primitive (deferred — see "Not doing").** Seven+ "muted → primary on hover, no border, icon-shaped" buttons share visual treatment without sharing a class: `.config-back-btn`, `.config-close-btn`, `.wiki-edit-btn`, `.wiki-delete-btn`, `.wiki-close-btn`, `.theme-btn`, inspector `.close-btn`, plus the wiki-editor `.wiki-rename-btn` family.

**Memory entries already capture the operative content** for the new doc:
- `reference_pico_cascade_gotchas.md` — Pico v2 variable re-scoping inside `<button>`, specificity, the `--pico-secondary-background` text-color trap
- `feedback_styling_workflow.md` — audit-first on cluster asks, probe-the-render via Playwright

**No `docs/assets/` directory exists** (research §5).

## Desired end state

A reader who needs to add or modify a styled control can:

1. Open `docs/web-ui-design.md` and recognize the primitive at a glance from a screenshot.
2. Find the canonical class definition in `src/decafclaw/web/static/styles/primitives.css`.
3. Read the Pico-in-context section before reaching for any `--pico-*` var, so they don't get bitten by button-scope re-aliasing.
4. Find the most-bitten gotchas surfaced inline in `CLAUDE.md` (Pico var re-scoping in particular) without needing to click through.

The codebase honors the primitives the doc names — at the floating-button tier — so the doc can be cited without lying about reality.

## Design decisions

### Decision 1: Scope = doc + floating-button consolidation only

**Decided:** Option B from brainstorm. Ship the doc, the `primitives.css` extraction, the CLAUDE.md update, AND apply `.dc-floating-btn` to `.hamburger-btn` and `.canvas-resummon-pill` (replacing local declarations). Defer the icon-button primitive coining + 7-place migration to a follow-up session (likely the very next one).

**Why:**
- Shipping doc-only would publish a doc that names primitives the codebase doesn't honor — undermines the doc's authority.
- Bundling the icon-button primitive into this PR is two unrelated refactors stacked, harder to review and revert.
- The floating-btn consolidation is pure replacement (the local declarations already match the primitive) — minimal regression surface.

### Decision 2: Extract primitives to `primitives.css`

**Decided:** New file `src/decafclaw/web/static/styles/primitives.css` houses the three shared classes. Variables.css shrinks to custom properties (`--sidebar-width`, `--header-height`) and `.hidden`. `primitives.css` becomes the second `@import` in `style.css` (after `variables.css`), so the conceptual flow reads tokens → primitives → components.

**Naming:** `primitives.css` (no leading underscore — `_foo` is a Sass-partial convention; we use vanilla browser `@import`).

**Why:** Gives the doc a single canonical home to point at. Separates concerns. Low-risk move (research confirms primitives don't depend on our local custom properties — only Pico vars).

### Decision 3: Screenshots checked into `docs/assets/web-ui/`

**Decided:** 3 primitives × {light, dark} = 6 small (~320–400px wide) mobile-viewport PNGs at `docs/assets/web-ui/`. Total ~150KB. Captured via Playwright MCP against a running dev server.

**Why:** A 2-second visual short-circuits the "match X" guessing game faster than file:line refs to CSS. Both themes because Pico v2 theme handling is itself one of the things that bit us in PR #408 — side-by-side captures double as smoke-test baselines for theme-related regressions in future sessions.

**Mobile viewport because** two of the three primitives (`.dc-overlay-header`, `.dc-overlay-close-x`) are mobile-only (`@media max-width: 639px`). `.dc-floating-btn` shows up on mobile as the hamburger anyway.

### Decision 4: CLAUDE.md gets a new short subsection

**Decided:** New `### Web UI styling` subsection between `### Tools` and `### Skills` under the existing Conventions section. Pointer to `docs/web-ui-design.md` plus 4 inline gotcha bullets:
- Pico v2 re-scopes `--pico-color` / `--pico-background-color` inside `<button>` → use `color: inherit` or non-aliased vars
- `--pico-secondary-background` is a TEXT color, not a bg
- Tag-qualify custom button rules (`button.foo`, not `.foo`)
- Reach for primitives first (`.dc-floating-btn` etc. in `primitives.css`)

**Why:** "Most-bitten" gotchas are Pico-specific surprises that catch you before you've thought to check the doc. Workflow-shaped advice (audit-first, probe-the-render) stays in the memory entries — that's session behavior, not project convention.

### Decision 5: Hybrid voice for the doc's Cascade-rules section

**Decided:** Each rule = prescriptive bullet + one-line "why" naming the specific PR #408 incident or Pico behavior that necessitates it. ≤6 rules.

**Why:** Pure rules rot when their context is forgotten; pure narrative is hard to scan. Hybrid lets the reader judge edge cases instead of applying mechanically. Mirrors the shape of the existing `feedback_*` memory entries that have worked well.

## Deliverables

### 1. `docs/web-ui-design.md` (new)

Four sections per the issue:

- **Pico-in-context** — which `--pico-*` vars are stable, which get re-scoped (button aliasing prominently flagged), which we deliberately don't touch, escape hatches (`color: inherit`, non-aliased card-bg vars).
- **Component primitives** — visual catalog with screenshot per primitive:
  - `.dc-floating-btn` — what it is, what it isn't, components that use it (cite `chat-view.js:185`, `mobile.css:50` after migration, `canvas.css:92` after migration, `canvas-panel.js:208`)
  - `.dc-overlay-header` — same shape
  - `.dc-overlay-close-x` — same shape
  - **Deferred section: "Primitives that should exist."** Names the icon-button primitive (with the 7+ candidate sites listed) and explains it's targeted for the immediate next session. Reader who lands here mid-extraction can find the gap clearly marked.
- **Cascade rules of engagement** — ≤6 hybrid-voice rules: tag-qualify selectors, `box-sizing: border-box` for tap-target floors, explicit `display:` inside `@media` blocks (the `display:none` desktop default trap), primitives load before components in `style.css`, `--pico-secondary-background` is text not bg, button-scope variable re-scoping.
- **Token vocabulary** — small approved list of magic numbers: 44px tap target, 6px radius, 0.4rem header padding, `0 2px 8px rgba(0,0,0,0.25)` shadow, 57px overlay-header min-height. "Anything outside this list is a signal we need a new token, not another one-off."

### 2. `src/decafclaw/web/static/styles/primitives.css` (new)

Houses `.dc-floating-btn`, `.dc-overlay-header`, `button.dc-overlay-close-x`. Moved verbatim from `variables.css`.

### 3. `src/decafclaw/web/static/styles/variables.css` (modified)

Drops the three primitive blocks. Retains custom properties at `:root` and the `.hidden` utility.

### 4. `src/decafclaw/web/static/style.css` (modified)

Adds `@import "styles/primitives.css";` as the second import (after `variables.css`).

### 5. `src/decafclaw/web/static/styles/mobile.css` + `resize.css` (modified)

`.hamburger-btn` strips its locally-declared border / border-radius / box-shadow / min-width / min-height / box-sizing / cursor (the floating-btn primitive's surface). Retains `.hamburger-btn`-specific rules (positioning, z-index, font-size, background-color override).

`resize.css` `.hamburger-btn` rule (resize.css:26) similarly cleaned up.

`conversation-sidebar.js` and any other component rendering the hamburger gets `.dc-floating-btn` added to its class list.

### 6. `src/decafclaw/web/static/styles/canvas.css` (modified)

`.canvas-resummon-pill` strips its locally-declared border / radius / shadow / min-height. Retains pill-specific rules (primary-filled bg, unread dot, position, animation if any).

`chat-view.js` (or wherever the resummon pill is rendered) gets `.dc-floating-btn` added to its class list.

### 7. `docs/assets/web-ui/` (new directory, 6 PNGs)

`floating-btn-light.png`, `floating-btn-dark.png`, `overlay-header-light.png`, `overlay-header-dark.png`, `overlay-close-x-light.png`, `overlay-close-x-dark.png`. ~320–400px wide, mobile viewport, captured via Playwright.

### 8. `CLAUDE.md` (modified)

New `### Web UI styling` subsection between `### Tools` and `### Skills` per Decision 4.

### 9. `docs/index.md` (modified)

Add a line linking `docs/web-ui-design.md` under the appropriate web-UI cluster.

## Patterns to follow

- **Existing primitive class shape.** New `primitives.css` mirrors the three blocks already at `variables.css:29–80` — no rewrites, just relocation.
- **`@import` order in `style.css`.** Tokens → primitives → components. Adds one line; preserves existing per-component order.
- **Component-class composition pattern** already in use:
  - `class="canvas-mobile-disclosure dc-floating-btn"` (`canvas-panel.js:208`) — primitive applied alongside component class
  - `class="sidebar-header dc-overlay-header"` (`conversation-sidebar.js:466`)
  - Same pattern extended to hamburger and resummon-pill in this session.
- **CLAUDE.md subsection style** mirrors existing `### Tools`, `### Skills`, `### Workflow` subsections — bulleted, file/path refs in `code`, brief.
- **Doc tone** mirrors existing `docs/web-ui.md`, `docs/web-ui-mobile.md` — terse, file:line refs prominent, screenshots-when-helpful.

## What we're NOT doing (deferred / out of scope)

### Deferred to immediate-next session ("session C")

- **Coining the icon-button primitive.** Candidate name TBD (`.dc-icon-btn`? `.dc-icon-link-btn`?). Visual: no border, no bg, muted color → primary on hover, icon-shaped padding. Migration touches at minimum: `.config-back-btn` (config-panel.css:23), `.config-close-btn` (config-panel.css:23–37), `.wiki-edit-btn` / `.wiki-delete-btn` / `.wiki-close-btn` (wiki.css:51–78), `.theme-btn` (sidebar.css:244–259), `.close-btn` (context-inspector.css:41), `.wiki-rename-btn` / `.file-rename-btn` (wiki.css:126–138), and the `.file-edit-btn` family (wiki.css:51–78). The new primitive lives in `primitives.css`; the doc's "Primitives that should exist" section turns into a fourth entry in the catalog. CLAUDE.md gets a fifth gotcha bullet referencing it.

### Out of scope entirely

- **Visual redesign or theme changes.** This session is documentation + extraction. No new colors, fonts, spacing scales.
- **Replacing or upgrading Pico.** We keep Pico v2.
- **Frontend framework changes.** No Lit / build / vendor changes.
- **Form, input, table, modal, toast restyling.** The audit was scoped to button-like / control / header surfaces. Other components are out of scope until they show similar cluster-style fragility.
- **Visual regression test infrastructure.** The 6 screenshots are reference images, not part of an automated test suite. Connecting them to a regression harness is a separate effort.
- **CSS-loading refactor.** Sticking with vanilla `@import` chain in `style.css`. No bundler, no PostCSS, no Sass.

## Verification before merge

- `make check` passes (lint + typecheck Python + JS).
- `make test` passes — no Python tests reference the moved CSS rules, but verifying the baseline.
- Manual: `make dev`, open web UI in browser, walk through:
  - Desktop: scroll-to-bottom pill renders correctly, canvas resummon pill renders correctly.
  - Mobile (≤639px viewport via DevTools): hamburger button renders correctly, sidebar overlay header + close-X render correctly, canvas mobile header + close-X render correctly.
  - Both light and dark themes for each of the above.
- Computed-style probe via Playwright: confirm `.hamburger-btn` and `.canvas-resummon-pill` resolve to the same border / radius / shadow / min-height as `.dc-floating-btn` after the migration (this is exactly the kind of "is the primitive actually applied" question the styling-workflow memory says to answer with computed styles, not source).
- Doc review: open `docs/web-ui-design.md` in a markdown preview, verify all 6 screenshots load and all `file:line` refs resolve to real lines.
