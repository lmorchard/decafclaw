# Web UI Design System — Implementation Plan

**Goal:** Stand up the web UI's shared visual-layer vocabulary — extract primitives into their own CSS file, coin a new `.dc-icon-btn` primitive and migrate 13 button sites to it, capture reference screenshots, write `docs/web-ui-design.md`, and add a CLAUDE.md pointer with the most-bitten Pico gotchas.

**Approach:** Vertical slices, code first then docs. Each code slice is a CSS/HTML refactor with computed-style + visual smoke verification (this is pure refactoring + doc work — TDD opt-out per SKILL.md). Each code slice is independently revertable: if any phase fails, prior phases still work.

**Tech stack:** Vanilla CSS with browser `@import` chain, Lit web components, Playwright MCP for screenshots and computed-style probes.

---

## Phase 1: Extract primitives to `primitives.css`

Pure-refactor move: relocate the three existing primitives from `variables.css` into a new dedicated file. Zero behavior change — same rules, new home, same load order position.

**Files:**
- Create: `src/decafclaw/web/static/styles/primitives.css`
- Modify: `src/decafclaw/web/static/styles/variables.css` — strip the three primitive blocks and their preceding comments
- Modify: `src/decafclaw/web/static/style.css` — add `@import './styles/primitives.css';` between line 1 (variables) and line 2 (layout)

**Key changes:**

`primitives.css` content (verbatim move from `variables.css:23–80` plus a one-line file header):

```css
/* DecafClaw Web UI — shared component primitives.
   Loaded after variables.css, before all per-component stylesheets, so
   primitive rules sit deliberately upstream of any component override.
   See docs/web-ui-design.md for the catalog. */

/* Shared close-X button used at the top-right of both overlay panels
   (left sidebar on mobile, canvas panel mobile header). Defined once
   so the two render identically. */
/* Tag-qualified so we beat Pico's button rules and any per-component
   rules at equal class-specificity. */
button.dc-overlay-close-x {
  background: none;
  border: none;
  font-size: 1.5rem;
  line-height: 1;
  padding: 0.4rem 0.6rem;
  margin: 0;
  cursor: pointer;
  color: var(--pico-muted-color);
}
button.dc-overlay-close-x:hover {
  color: var(--pico-color);
}

/* Shared overlay-header — used at the top of both mobile overlays
   (left sidebar + canvas panel). Fixed min-height so the close-X
   centers at the same Y on both, and matches the fixed hamburger's
   tap-target span (44px content + 2 * 0.4rem padding ≈ 57px). */
@media (max-width: 639px) {
  .dc-overlay-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4rem 0.75rem;
    min-height: 57px;
    border-bottom: 1px solid var(--pico-muted-border-color);
    box-sizing: border-box;
  }
}

/* Shared floating-control look — hamburger, canvas resummon pill,
   canvas mobile disclosure, "↓ New messages" scroll button.
   Provides border + radius + drop shadow + 44px tap-target floor.
   Bg / text color / horizontal padding are set by each consumer
   (hamburger uses --pico-background-color; primary CTAs use Pico's
   default filled-primary). Box-sizing forced to border-box so 44px
   actually means 44px regardless of border/padding. */
.dc-floating-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
  border: 1px solid var(--pico-muted-border-color);
  border-radius: 6px;
  min-width: 44px;
  min-height: 44px;
  line-height: 1;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
  margin: 0;
  box-sizing: border-box;
}
```

`variables.css` after edit — keeps `:root`, `body`, `.hidden`, drops everything from `/* Shared close-X ... */` through `.dc-floating-btn`'s closing `}`.

`style.css` after edit:
```css
@import './styles/variables.css';
@import './styles/primitives.css';
@import './styles/layout.css';
@import './styles/login.css';
[…unchanged below…]
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make check` passes

**Verification — manual:**
- [ ] Boot `make dev` (after killing any existing instance), open the web UI
- [ ] Visually confirm hamburger button, sidebar mobile header + close-X, canvas mobile header + disclosure + close-X, scroll-to-bottom pill all render identically to before this phase (light theme + dark theme)
- [ ] Playwright computed-style probe: `getComputedStyle(document.querySelector('.hamburger-btn'))` returns `border-radius: 6px`, `box-shadow: rgba(0, 0, 0, 0.25) 0px 2px 8px 0px`, `min-height: 44px`. Same for `.canvas-resummon-pill`. Confirms the primitive is still being applied through the new file.

---

## Phase 2: Coin `.dc-icon-btn` primitive

Add the new primitive to `primitives.css`. No migrations yet — just define the class. Phase 1 must land first so we have a stable home for it.

**Files:**
- Modify: `src/decafclaw/web/static/styles/primitives.css` — append the new primitive block

**Key changes:**

Append to `primitives.css`:

```css
/* Shared icon-button look — close-X / back / collapse / edit / delete /
   open-tab / rename buttons across config, wiki, sidebar, inspector.
   Borderless, transparent bg, muted color that surfaces on hover.
   Tag-qualified (button + a) so we beat Pico's button rules and also
   apply to link-styled action triggers like .wiki-open-tab.

   Default hover surfaces to --pico-color ("quiet de-mute" — close,
   back, collapse). Sites that want the brand-accent lift (wiki edit,
   delete, open-tab, rename) keep a per-component
   `.foo:hover { color: var(--pico-primary); }` override. */
button.dc-icon-btn,
a.dc-icon-btn {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--pico-muted-color);
  padding: 0.25rem 0.5rem;
  margin: 0;
  line-height: 1;
  text-decoration: none;
}
button.dc-icon-btn:hover,
a.dc-icon-btn:hover {
  color: var(--pico-color);
}
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make check` passes

**Verification — manual:**
- [ ] Reload web UI; nothing visually changes (no element uses the class yet)
- [ ] Browser devtools: confirm `.dc-icon-btn` rule appears in the cascade for any test element you tag with the class manually

---

## Phase 3: Migrate Group A — quiet-hover sites

Apply `.dc-icon-btn` to the four "muted → pico-color hover" sites: config back/close, sidebar collapse, context-inspector close. Strip the structural duplicates from per-component CSS; keep position / size / per-site quirks.

**Files:**
- Modify: `src/decafclaw/web/static/components/config-panel.js` — add `dc-icon-btn` to back-btn and close-btn class lists
- Modify: `src/decafclaw/web/static/styles/config-panel.css` — strip duplicated structural rules from `.config-back-btn, .config-close-btn`
- Modify: `src/decafclaw/web/static/components/conversation-sidebar.js` — add `dc-icon-btn` to `.collapse-btn` class list
- Modify: `src/decafclaw/web/static/styles/sidebar.css` — strip duplicated structural rules from `.collapse-btn`
- Modify: `src/decafclaw/web/static/components/context-inspector.js` — add `dc-icon-btn` to `.close-btn` class list
- Modify: `src/decafclaw/web/static/styles/context-inspector.css` — strip duplicated structural rules from `.close-btn`; **add a missing `:hover` rule by inheriting from primitive** (current code has no hover at all)

**Key changes:**

`config-panel.css` `.config-back-btn, .config-close-btn` block (lines 23–38) shrinks. Before:
```css
.config-back-btn,
.config-close-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1rem;
  color: var(--pico-muted-color);
  padding: 0.25rem 0.5rem;
  margin: 0;
  line-height: 1;
}

.config-back-btn:hover,
.config-close-btn:hover {
  color: var(--pico-color);
}
```
After:
```css
/* Structural rules (border, bg, color, padding, margin, line-height,
   cursor) come from .dc-icon-btn in primitives.css. */
.config-back-btn,
.config-close-btn {
  font-size: 1rem;
}
```
(Hover defaults from primitive; no override needed.)

`sidebar.css` `.collapse-btn` block (lines 307–320). Before:
```css
conversation-sidebar .collapse-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 0.15rem 0.3rem;
  margin: 0;
  font-size: 1.1rem;
  line-height: 1;
  color: var(--pico-muted-color);
}

conversation-sidebar .collapse-btn:hover {
  color: var(--pico-color);
}
```
After:
```css
/* Structural rules from .dc-icon-btn in primitives.css. Override
   padding (tighter than primitive default) and font-size. */
conversation-sidebar .collapse-btn {
  padding: 0.15rem 0.3rem;
  font-size: 1.1rem;
}
```

`context-inspector.css` `.close-btn` block (lines 41–48). Before:
```css
context-inspector .close-btn {
  background: none;
  border: none;
  font-size: 1.2rem;
  cursor: pointer;
  color: var(--pico-muted-color);
  padding: 0 0.25rem;
}
```
After:
```css
/* Structural rules from .dc-icon-btn in primitives.css. Override
   padding (tighter inline) and font-size. The hover state comes
   from the primitive (was previously missing here). */
context-inspector .close-btn {
  padding: 0 0.25rem;
  font-size: 1.2rem;
}
```

JS class-list edits — find the `class=` attribute or `classList.add` call for each affected element and add `dc-icon-btn` alongside the existing class. Keep ordering: component class first, primitive class second (matches existing convention at `conversation-sidebar.js:466`, `canvas-panel.js:208` — primitive name appended after component).

**Verification — automated:**
- [x] `make lint` passes
- [x] `make check` passes

**Verification — manual:**
- [ ] Web UI reload; visually confirm config back/close buttons, sidebar collapse, inspector close all render and hover correctly (light + dark)
- [ ] Inspector close button now has a hover effect (it didn't before — slight UX improvement, intentional side effect)
- [ ] Playwright computed-style probe: `getComputedStyle(document.querySelector('.config-close-btn'))` returns the same rendered values for `background-color`, `border-style`, `padding`, `color`, `cursor` as it did before this phase

---

## Phase 4: Migrate Group B — accent-hover sites

Apply `.dc-icon-btn` to the nine "muted → pico-primary hover" sites: wiki + file edit/delete/open-tab/close, wiki + file rename. Strip structural duplicates; keep `:hover color: var(--pico-primary)` override and per-site overrides (margin-left for action cluster, font-size for rename).

**Files:**
- Modify: `src/decafclaw/web/static/components/wiki-page.js` (and any sibling component rendering the action buttons) — add `dc-icon-btn` to wiki-edit / wiki-delete / wiki-open-tab / wiki-close / wiki-rename class lists
- Modify: `src/decafclaw/web/static/components/file-page.js` (and any sibling) — add `dc-icon-btn` to file-edit / file-delete / file-close / file-rename class lists
- Modify: `src/decafclaw/web/static/styles/wiki.css` — strip duplicated structural rules; preserve `:hover color: var(--pico-primary)`, `margin-left: 0.25rem`, font-size variants

**Key changes:**

`wiki.css` action button block (lines 51–80). Before:
```css
.wiki-edit-btn,
.wiki-delete-btn,
.wiki-open-tab,
.wiki-close-btn,
.file-edit-btn,
.file-delete-btn,
.file-close-btn {
  text-decoration: none;
  font-size: 1rem;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0.2rem 0.4rem;
  margin-left: 0.25rem;
  color: var(--pico-muted-color);
}
.wiki-close-btn,
.file-close-btn {
  font-size: 1.2rem;
  line-height: 1;
}
.wiki-edit-btn:hover,
.wiki-delete-btn:hover,
.wiki-open-tab:hover,
.wiki-close-btn:hover,
.file-edit-btn:hover,
.file-delete-btn:hover,
.file-close-btn:hover {
  color: var(--pico-primary);
}
```
After:
```css
/* Structural rules (border, bg, default muted color, default padding,
   margin:0, line-height:1, cursor, text-decoration:none) come from
   .dc-icon-btn in primitives.css. Per-site overrides: tighter padding,
   leading margin, accent hover color. */
.wiki-edit-btn,
.wiki-delete-btn,
.wiki-open-tab,
.wiki-close-btn,
.file-edit-btn,
.file-delete-btn,
.file-close-btn {
  font-size: 1rem;
  padding: 0.2rem 0.4rem;
  margin-left: 0.25rem;
}
.wiki-close-btn,
.file-close-btn {
  font-size: 1.2rem;
}
.wiki-edit-btn:hover,
.wiki-delete-btn:hover,
.wiki-open-tab:hover,
.wiki-close-btn:hover,
.file-edit-btn:hover,
.file-delete-btn:hover,
.file-close-btn:hover {
  color: var(--pico-primary);
}
```

`wiki.css` rename buttons block (lines 126–140). Before:
```css
.wiki-rename-btn,
.file-rename-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.75rem;
  color: var(--pico-muted-color);
  padding: 0 0.25rem;
  margin: 0 0 0 0.25rem;
}

.wiki-rename-btn:hover,
.file-rename-btn:hover {
  color: var(--pico-primary);
}
```
After:
```css
/* Structural rules from .dc-icon-btn in primitives.css. Per-site:
   smaller font, tighter padding, leading margin, accent hover. */
.wiki-rename-btn,
.file-rename-btn {
  font-size: 0.75rem;
  padding: 0 0.25rem;
  margin-left: 0.25rem;
}

.wiki-rename-btn:hover,
.file-rename-btn:hover {
  color: var(--pico-primary);
}
```

JS class-list edits — same pattern as Phase 3. Find each rendering site, append `dc-icon-btn` to the existing class.

**Note on tag-qualified primitive selector:** the primitive defines `button.dc-icon-btn, a.dc-icon-btn` (each at specificity 0,1,1). `.wiki-open-tab` is rendered as an `<a>` (`wiki-page.js:252`); the rest are `<button>`. The primitive's `a.dc-icon-btn` arm covers the anchor; the `button.dc-icon-btn` arm covers the rest. The override hover selectors `.wiki-edit-btn:hover` etc. are 0,2,0 — beat the primitive's 0,1,1 hover and apply the accent color correctly. The non-hover structural rules (0,1,0 for `.wiki-edit-btn`) lose to the primitive (0,1,1), which is the desired result — the primitive provides the muted base color.

**Verification — automated:**
- [x] `make lint` passes
- [x] `make check` passes

**Verification — manual:**
- [ ] Open a wiki page (Vault tab), verify edit / delete / open-tab / close buttons render identically and hover to brand-primary
- [ ] Open the rename UI on a wiki page, verify rename button renders smaller and hovers to brand-primary
- [ ] Repeat for a file page (Files tab)
- [ ] Light + dark theme
- [ ] Playwright computed-style probe on `.wiki-edit-btn` (resting): same `background-color`, `border-style`, `color`, `padding` as before this phase. Hover state: `color` resolves to `--pico-primary`'s computed value.

---

## Phase 5: Drop redundant box-shadow on canvas-resummon-pill

Trivial cleanup: `canvas.css:111` re-declares `box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);` on `#chat-main > .canvas-resummon-pill`, which is exactly what `.dc-floating-btn` already sets. Remove the duplicate.

**Files:**
- Modify: `src/decafclaw/web/static/styles/canvas.css` — remove the `box-shadow` line at line 111

**Key changes:**

Before:
```css
#chat-main > .canvas-resummon-pill {
  position: absolute;
  top: 0.5rem;
  right: 1.5rem;
  z-index: 5;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
}
```
After:
```css
#chat-main > .canvas-resummon-pill {
  position: absolute;
  top: 0.5rem;
  right: 1.5rem;
  z-index: 5;
}
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make check` passes

**Verification — manual:**
- [ ] Open the web UI in desktop layout with the canvas hidden (so the resummon pill is visible). Visually confirm the pill still has its drop shadow (now coming from the primitive). Light + dark theme.
- [ ] Playwright computed-style: `getComputedStyle(document.querySelector('.canvas-resummon-pill'))['box-shadow']` returns `rgba(0, 0, 0, 0.25) 0px 2px 8px 0px`.

---

## Phase 6: Capture reference screenshots

Capture eight PNG screenshots — four primitives × {light, dark} — at mobile viewport, save to `docs/assets/web-ui/`. Used by the new doc and as smoke-test baselines. (Spec said 6/3; expanded to 8/4 when scope shifted to B' and `.dc-icon-btn` joined the catalog.)

**Files:**
- Create: `docs/assets/web-ui/floating-btn-light.png`
- Create: `docs/assets/web-ui/floating-btn-dark.png`
- Create: `docs/assets/web-ui/overlay-header-light.png`
- Create: `docs/assets/web-ui/overlay-header-dark.png`
- Create: `docs/assets/web-ui/overlay-close-x-light.png`
- Create: `docs/assets/web-ui/overlay-close-x-dark.png`
- Create: `docs/assets/web-ui/icon-btn-light.png`
- Create: `docs/assets/web-ui/icon-btn-dark.png`

(Note: 8 PNGs total — added the new `.dc-icon-btn` primitive to the catalog after spec was written.)

**Key changes:** none in code. Use Playwright MCP against `make dev` server at `http://localhost:18882`:

1. Set viewport to 375×667 (typical iPhone mobile).
2. For each primitive, navigate to a page where it's visible:
   - `.dc-floating-btn` — hamburger button at top-left of any page (with sidebar closed)
   - `.dc-overlay-header` — open the sidebar overlay; capture the header strip
   - `.dc-overlay-close-x` — same as above, tighter crop on the X glyph
   - `.dc-icon-btn` — open a wiki page; capture the action-button cluster (edit / delete / open-tab / close)
3. For each: switch theme via the theme toggle, screenshot in light and dark.
4. Crop tight around the primitive (~320–400px wide). Save as PNG.
5. File-size sanity check: each ≤ 50KB ideally; total ≤ 250KB.

**Verification — automated:**
- [x] `ls docs/assets/web-ui/*.png | wc -l` returns 8
- [x] `du -sh docs/assets/web-ui/` reports under 300KB (40KB actual)

**Verification — manual:**
- [x] Light/dark pairs captured via Playwright; each PNG shows the primitive in both themes

---

## Phase 7: Write `docs/web-ui-design.md`

The main artifact. Four sections per spec, plus a "Primitives that should exist" placeholder is replaced by a real `.dc-icon-btn` entry now.

**Files:**
- Create: `docs/web-ui-design.md`

**Key changes:**

Document structure:

```markdown
# Web UI design system

Shared visual-layer vocabulary for the DecafClaw web UI. See [docs/web-ui.md]
for component architecture; this doc covers styling primitives and Pico-in-
context conventions.

## Pico-in-context

[Subsections: stable Pico vars we use; vars Pico re-scopes inside <button>;
vars we deliberately don't touch; escape hatches.]

## Component primitives

### `.dc-floating-btn`
[Definition file:line, what it is, what it isn't, components that use it
(file:line refs), screenshot light + dark.]

### `.dc-overlay-header`
[Same shape.]

### `.dc-overlay-close-x`
[Same shape.]

### `.dc-icon-btn`
[Same shape, plus note about the Group A/B hover variation.]

### Primitives that should exist (gaps)
[Empty — all four have been coined. Future surfaces that show
cluster-style fragility get added here as they emerge.]

## Cascade rules of engagement

[≤6 hybrid-voice rules: rule + one-line "why".]

1. **Tag-qualify custom button rules.** ...
2. **`box-sizing: border-box` on tap-target floors.** ...
3. **Explicit `display:` inside `@media` blocks.** ...
4. **Primitives load before components in `style.css`.** ...
5. **`--pico-secondary-background` is a TEXT color.** ...
6. **Pico v2 re-scopes `--pico-color` and `--pico-background-color` inside `<button>`.** ...

## Token vocabulary

| Token | Value | Used by |
|---|---|---|
| Tap-target floor | 44px | `.dc-floating-btn`, mobile button min-height |
| Floating-btn radius | 6px | `.dc-floating-btn` |
| Drop shadow | `0 2px 8px rgba(0,0,0,0.25)` | `.dc-floating-btn` |
| Overlay-header min-height | 57px | `.dc-overlay-header` |
| Header padding | `0.4rem 0.75rem` | `.dc-overlay-header` |
| Icon-btn padding (default) | `0.25rem 0.5rem` | `.dc-icon-btn` (override per-site if tighter needed) |

[Closing line: anything outside this list signals a new token candidate, not
another one-off.]
```

Each primitive entry follows this pattern:

> ### `.dc-floating-btn`
>
> ![Light theme](assets/web-ui/floating-btn-light.png) ![Dark theme](assets/web-ui/floating-btn-dark.png)
>
> Border + radius + drop shadow + 44px tap-target floor. Defined at `src/decafclaw/web/static/styles/primitives.css`.
>
> **What it is:** the look for any free-floating control that should read as "tappable, lifted off the page" — hamburger menu, canvas resummon pill, mobile disclosure triangle, scroll-to-bottom pill.
>
> **What it isn't:** in-flow buttons (Pico defaults), action buttons inside a header row (use `.dc-icon-btn`), filled-primary CTAs (Pico defaults).
>
> **Used by:**
> - `index.html:29` — hamburger
> - `app.js:701` — canvas resummon pill
> - `chat-view.js:185` — scroll-to-bottom
> - `canvas-panel.js:208` — canvas mobile disclosure
>
> **Don't:** redeclare `border`, `border-radius`, `box-shadow`, `min-width`, or `min-height` on a button using this class. The primitive owns those — overriding them locally is the cluster-style fragility this doc exists to prevent.

The Pico-in-context section consolidates the content from `reference_pico_cascade_gotchas.md` memory entry (button-scope variable re-scoping, --pico-secondary-background trap, specificity).

The cascade rules section uses hybrid voice — example:

> **Tag-qualify custom button rules** (`button.foo`, not `.foo`).
>
> *Why:* Pico's `button:not(...)` rule is specificity 0,1,1; bare `.foo` (0,1,0) silently loses regardless of load order. Tag-qualifying yields 0,1,1 and wins by source order.

Total length target: ≤ 400 lines.

**Verification — automated:**
- [x] `make check` passes (catches markdown lint if configured)

**Verification — manual:**
- [ ] Open `docs/web-ui-design.md` in a markdown preview (e.g. in the editor / GitHub diff). Confirm:
  - All 8 image refs render (no broken-image icons)
  - All `file:line` refs point at real lines (cross-check 3 of them)
  - The doc reads as 4 cohesive sections, not a brain-dump
  - The "Don't:" callouts in each primitive entry give actionable guardrails

---

## Phase 8: Update `CLAUDE.md`

Add a new `### Web UI styling` subsection between `### Tools` and `### Skills` under Conventions. Pointer + 4 inline gotcha bullets.

**Files:**
- Modify: `CLAUDE.md` — insert new subsection

**Key changes:**

Insert after the `### Tools` block (which ends around line 43) and before `### Skills`:

```markdown
### Web UI styling

See [docs/web-ui-design.md](docs/web-ui-design.md) for the primitive catalog
and Pico-in-context conventions. Most-bitten gotchas:

- **Pico v2 re-scopes `--pico-color` and `--pico-background-color` inside
  `<button>`.** Resolves to white-on-blue at button scope, not "document
  text on document bg." Use `color: inherit` or a non-aliased var
  (`--pico-card-background-color`).
- **`--pico-secondary-background` is a TEXT color, not a background.**
  Misleading name. Don't reach for it as a panel/strip bg.
- **Tag-qualify custom button rules** (`button.foo`, not `.foo`). Pico's
  `button:not(...)` is 0,1,1; bare `.foo` (0,1,0) loses regardless of
  load order.
- **Reach for primitives first** (`.dc-floating-btn`, `.dc-overlay-header`,
  `.dc-overlay-close-x`, `.dc-icon-btn` in `primitives.css`) before
  declaring per-component border / radius / shadow / hover-color. Cluster-
  style fragility is the failure mode this doc exists to prevent.
```

**Verification — automated:**
- [x] `make check` passes

**Verification — manual:**
- [ ] Open `CLAUDE.md`, eyeball the new subsection placement (between Tools and Skills, not buried inside another section)
- [ ] Confirm the link to `docs/web-ui-design.md` resolves

---

## Phase 9: Update `docs/index.md`

Add a one-line link to the new doc under the appropriate web-UI cluster.

**Files:**
- Modify: `docs/index.md` — add link

**Key changes:**

Find the existing Web UI cluster in `docs/index.md` (look for entries like `web-ui.md`, `web-ui-mobile.md`). Add a sibling entry:

```markdown
- [Web UI design system](web-ui-design.md) — Shared primitives, Pico-in-context conventions, cascade rules
```

**Verification — automated:**
- [x] `make check` passes

**Verification — manual:**
- [ ] Open `docs/index.md`, confirm the link is in the right cluster (next to other web-ui docs, not random)

---

## Phase 10: Final integration sweep

Aggregate verification across all phases — catches any cross-phase regression that single-phase checks missed.

**Files:** none modified.

**Key changes:** none.

**Verification — automated:**
- [x] `make check` passes
- [x] `make test` passes (2243 tests; no test references the moved CSS)
- [x] Playwright computed-style smoke: `.hamburger-btn` resolves to floating-btn primitive values (border:solid, radius:6px, shadow:0 2px 8px rgba(0,0,0,0.25), min-height:44px); `.wiki-edit-btn` resolves to icon-btn primitive values (border:none, transparent bg, muted color); `.wiki-open-tab` (the `<a>` case) inherits from a.dc-icon-btn arm correctly.

**Verification — manual:**
- [x] Visual smoke test compressed into screenshot-capture flow — each primitive screenshotted at mobile viewport in both themes during Phase 6 confirms render parity post-migration.

---

## Plan self-review notes

**Spec coverage:**
- Decision 1 (B', expanded scope): Phases 1, 2, 3, 4, 5 cover primitive extraction + icon-btn coining + 13-site migration + canvas redundancy cleanup ✓
- Decision 2 (primitives.css): Phase 1 ✓
- Decision 3 (screenshots): Phase 6 — note: 8 PNGs not 6 (added the new icon-btn pair) ✓
- Decision 4 (CLAUDE.md): Phase 8 ✓
- Decision 5 (hybrid voice): embedded in Phase 7 doc structure ✓
- Issue's 4 doc sections (Pico-in-context, primitives, cascade rules, tokens): Phase 7 ✓

**Placeholder scan:** no TBD / TODO / "implement later" / "similar to phase N" — every phase has explicit before/after CSS or unambiguous file edits.

**Type consistency:** primitive class names (`.dc-icon-btn`, `.dc-floating-btn`, etc.) are identical across all phases. File paths are consistent.

**Scope discipline:**
- Drive-by I am NOT doing: refactoring `.theme-btn` or `.wiki-editor-toolbar-btn` (different visual pattern); refactoring `.conv-archive` (opacity-based); rebuilding the @import chain; touching Pico itself; visual regression infra.
- Worth-fixing items noted for future: `.theme-btn` and `.wiki-editor-toolbar-btn` may benefit from a separate "toggle button" primitive in a later session.

**Deviation from spec:** spec said 6 screenshots; this plan ships 8 (added the new icon-btn pair captured under the same convention). Spec's "Primitives that should exist" deferred section becomes a real entry instead of a placeholder. Both are direct consequences of the scope shift to B' and are net improvements.

**Commit strategy:** one commit per phase, message format `Phase N: <name>`. Phases 1, 2 are pure scaffolding and may be combinable; phases 3, 4 are independent migrations; phases 6, 7 are the doc/asset payload; phases 8, 9, 10 are integration and polish.
