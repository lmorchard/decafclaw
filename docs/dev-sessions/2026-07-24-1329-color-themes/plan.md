# Selectable Color Themes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let web-UI users pick a color palette (Dracula, Solarized Light) in addition to the existing light/dark/system base, via a two-attribute Pico theming model.

**Architecture:** `<html>` carries `data-theme` (light|dark|absent = Pico's structural base) × `data-palette` (dracula|solarized-light|absent = our color overlay). A single theme *name* resolves through a registry to a descriptor carrying both attributes, so nonsensical combos are unrepresentable. Palette CSS files override ~15–20 `--pico-*` color tokens on top of Pico's base. Logic lives in a pure `lib/theme.js` module (unit-tested); `theme-toggle.js` owns only rendering.

**Tech Stack:** Pico v2 (CSS custom properties), Lit (web components), vitest + jsdom (new — first JS test infra in the repo).

## Global Constraints

- Work happens in the worktree `.claude/worktrees/color-themes/` on branch `color-themes`. All paths below are relative to the repo root within that worktree.
- **Button-scope trap** (`docs/web-ui-design.md`): Pico re-aliases `--pico-color`/`--pico-background-color` inside `<button>`. Use `color: inherit` or a non-aliased var in custom button rules; tag-qualify custom button selectors.
- **Palettes are the one sanctioned place to set `--pico-primary-background` / `--pico-primary-inverse`** — scoped under `[data-palette="…"]` only. Component CSS still must not touch them.
- localStorage key is the existing `decafclaw-theme`; changes must stay backward compatible with stored `light`/`dark`/`system` values.
- Verify Python untouched: this is a JS/CSS/docs-only change; no `.py` files are modified.
- Dracula hex values are verified against <https://spec.draculatheme.com/>; Solarized values are Ethan Schoonover's published palette.

---

### Task 1: vitest + jsdom test infrastructure

**Files:**
- Modify: `src/decafclaw/web/static/package.json`
- Create: `src/decafclaw/web/static/vitest.config.js`
- Modify: `src/decafclaw/web/static/tsconfig.json`
- Modify: `Makefile`
- Create (temporary, removed in Task 2): `src/decafclaw/web/static/lib/smoke.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces: a working `make test-js` target that runs vitest with the jsdom environment over `**/*.test.js` files under `lib/` and `components/`.

- [ ] **Step 1: Install vitest + jsdom as devDependencies**

Run (lets npm pin resolved versions rather than guessing):
```bash
cd src/decafclaw/web/static && npm install -D vitest jsdom
```
Expected: `package.json` `devDependencies` gains `vitest` and `jsdom`; `node_modules` populated (gitignored).

- [ ] **Step 2: Add the `test` npm script**

Edit `src/decafclaw/web/static/package.json` `scripts` block to add a `test` entry alongside the existing `build`/`clean`:
```json
  "scripts": {
    "build": "node build-vendor.mjs",
    "clean": "rm -rf vendor/bundle node_modules",
    "test": "vitest run"
  },
```

- [ ] **Step 3: Create the vitest config**

Create `src/decafclaw/web/static/vitest.config.js`:
```js
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['lib/**/*.test.js', 'components/**/*.test.js'],
  },
});
```

- [ ] **Step 4: Exclude test files from tsc**

`tsc --checkJs` would try to type-check vitest globals in `*.test.js`. Edit `src/decafclaw/web/static/tsconfig.json` to add an `exclude` key as a sibling of `include`:
```json
  "include": ["app.js", "lib/**/*.js", "components/**/*.js", "widgets/**/*.js"],
  "exclude": ["**/*.test.js"]
```

- [ ] **Step 5: Add the `test-js` Makefile target**

Edit `Makefile`, directly below the existing `check-js` target:
```makefile
test-js: install-js
	cd src/decafclaw/web/static && npx vitest run
```

- [ ] **Step 6: Add a temporary smoke test**

Create `src/decafclaw/web/static/lib/smoke.test.js` (proves wiring; removed in Task 2):
```js
import { describe, it, expect } from 'vitest';

describe('vitest wiring', () => {
  it('runs a test', () => {
    expect(1 + 1).toBe(2);
  });
  it('has a jsdom document', () => {
    expect(typeof document).toBe('object');
    expect(document.documentElement.tagName).toBe('HTML');
  });
});
```

- [ ] **Step 7: Run the smoke test**

Run: `make test-js`
Expected: PASS — 1 test file, 2 tests passing.

- [ ] **Step 8: Confirm tsc still passes (test file excluded)**

Run: `make check-js`
Expected: PASS, no errors about `smoke.test.js`.

- [ ] **Step 9: Commit**

```bash
git add src/decafclaw/web/static/package.json src/decafclaw/web/static/package-lock.json \
  src/decafclaw/web/static/vitest.config.js src/decafclaw/web/static/tsconfig.json \
  src/decafclaw/web/static/lib/smoke.test.js Makefile
git commit -m "test(web): add vitest + jsdom JS test infrastructure (#654)"
```

---

### Task 2: `lib/theme.js` — registry + resolve/apply/persistence (TDD)

**Files:**
- Delete: `src/decafclaw/web/static/lib/smoke.test.js`
- Create: `src/decafclaw/web/static/lib/theme.test.js`
- Create: `src/decafclaw/web/static/lib/theme.js`

**Interfaces:**
- Consumes: vitest runner from Task 1; global `document` / `localStorage` (jsdom in tests, browser in prod).
- Produces (imported by Task 4's `theme-toggle.js`):
  - `THEMES: ThemeDescriptor[]` — ordered list; each `{ name, label, kind: 'base'|'palette', dataTheme: 'light'|'dark'|null, dataPalette: string|null, icon?: string, dot?: string }`
  - `STORAGE_KEY: string` (`'decafclaw-theme'`), `DEFAULT_THEME: string` (`'system'`)
  - `resolveTheme(name: string|null): ThemeDescriptor` — unknown → default descriptor
  - `applyTheme(name: string): string` — sets/clears both `<html>` attributes, returns resolved name
  - `loadStoredTheme(): string` — valid name or `DEFAULT_THEME`
  - `storeTheme(name: string): void`

- [ ] **Step 1: Remove the temporary smoke test**

```bash
git rm src/decafclaw/web/static/lib/smoke.test.js
```

- [ ] **Step 2: Write the failing test**

Create `src/decafclaw/web/static/lib/theme.test.js`:
```js
import { describe, it, expect, beforeEach } from 'vitest';
import {
  THEMES, STORAGE_KEY, DEFAULT_THEME,
  resolveTheme, applyTheme, loadStoredTheme, storeTheme,
} from './theme.js';

describe('THEMES registry', () => {
  it('exposes the five expected themes in order', () => {
    expect(THEMES.map((t) => t.name)).toEqual([
      'light', 'dark', 'system', 'dracula', 'solarized-light',
    ]);
  });
  it('classifies base vs palette themes', () => {
    expect(THEMES.filter((t) => t.kind === 'base').map((t) => t.name))
      .toEqual(['light', 'dark', 'system']);
    expect(THEMES.filter((t) => t.kind === 'palette').map((t) => t.name))
      .toEqual(['dracula', 'solarized-light']);
  });
});

describe('resolveTheme', () => {
  it('resolves base themes to data-theme with no palette', () => {
    expect(resolveTheme('light')).toMatchObject({ dataTheme: 'light', dataPalette: null });
    expect(resolveTheme('dark')).toMatchObject({ dataTheme: 'dark', dataPalette: null });
  });
  it('resolves system to no attributes', () => {
    expect(resolveTheme('system')).toMatchObject({ dataTheme: null, dataPalette: null });
  });
  it('resolves palette themes to base + palette', () => {
    expect(resolveTheme('dracula')).toMatchObject({ dataTheme: 'dark', dataPalette: 'dracula' });
    expect(resolveTheme('solarized-light'))
      .toMatchObject({ dataTheme: 'light', dataPalette: 'solarized-light' });
  });
  it('falls back to the default theme for unknown or null names', () => {
    expect(resolveTheme('nope').name).toBe(DEFAULT_THEME);
    expect(resolveTheme(null).name).toBe(DEFAULT_THEME);
  });
});

describe('applyTheme', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('data-theme');
    document.documentElement.removeAttribute('data-palette');
  });
  it('sets data-theme for a base theme and leaves palette absent', () => {
    applyTheme('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(document.documentElement.hasAttribute('data-palette')).toBe(false);
  });
  it('clears data-theme for system', () => {
    document.documentElement.setAttribute('data-theme', 'dark');
    applyTheme('system');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });
  it('sets both attributes for a palette theme', () => {
    applyTheme('dracula');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(document.documentElement.getAttribute('data-palette')).toBe('dracula');
  });
  it('switching from a palette back to a base clears the palette', () => {
    applyTheme('dracula');
    applyTheme('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(document.documentElement.hasAttribute('data-palette')).toBe(false);
  });
  it('returns the resolved theme name (default for bogus input)', () => {
    expect(applyTheme('dracula')).toBe('dracula');
    expect(applyTheme('bogus')).toBe(DEFAULT_THEME);
  });
});

describe('persistence', () => {
  beforeEach(() => localStorage.clear());
  it('stores and loads a theme name', () => {
    storeTheme('dracula');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('dracula');
    expect(loadStoredTheme()).toBe('dracula');
  });
  it('defaults to system when nothing is stored', () => {
    expect(loadStoredTheme()).toBe('system');
  });
  it('is backward compatible with legacy base values', () => {
    localStorage.setItem(STORAGE_KEY, 'dark');
    expect(loadStoredTheme()).toBe('dark');
  });
  it('falls back to system for an unknown stored value', () => {
    localStorage.setItem(STORAGE_KEY, 'winamp');
    expect(loadStoredTheme()).toBe('system');
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `make test-js`
Expected: FAIL — cannot resolve `./theme.js` (module does not exist yet).

- [ ] **Step 4: Write the implementation**

Create `src/decafclaw/web/static/lib/theme.js`:
```js
/**
 * Theme registry + resolve/apply/persistence logic for the web UI.
 *
 * Two-axis model: `data-theme` (light|dark|absent = Pico's structural base) ×
 * `data-palette` (dracula|solarized-light|absent = our color overlay). A single
 * theme *name* selects a descriptor carrying both, so nonsensical combos
 * (e.g. Dracula-in-light) are unrepresentable. See docs/web-ui-design.md.
 *
 * Persists the active theme name under localStorage `decafclaw-theme`
 * (backward compatible with the legacy light/dark/system values).
 */

export const STORAGE_KEY = 'decafclaw-theme';
export const DEFAULT_THEME = 'system';

/**
 * @typedef {Object} ThemeDescriptor
 * @property {string} name
 * @property {string} label
 * @property {'base'|'palette'} kind
 * @property {'light'|'dark'|null} dataTheme
 * @property {string|null} dataPalette
 * @property {string} [icon]  emoji shown on base buttons
 * @property {string} [dot]   representative color for a palette swatch
 */

/** @type {ThemeDescriptor[]} — ordered for UI rendering. */
export const THEMES = [
  { name: 'light',           label: 'Light',           kind: 'base',    dataTheme: 'light', dataPalette: null,              icon: '☀️' },
  { name: 'dark',            label: 'Dark',            kind: 'base',    dataTheme: 'dark',  dataPalette: null,              icon: '🌙' },
  { name: 'system',          label: 'System',          kind: 'base',    dataTheme: null,    dataPalette: null,              icon: '💻' },
  { name: 'dracula',         label: 'Dracula',         kind: 'palette', dataTheme: 'dark',  dataPalette: 'dracula',         dot: '#bd93f9' },
  { name: 'solarized-light', label: 'Solarized Light', kind: 'palette', dataTheme: 'light', dataPalette: 'solarized-light', dot: '#268bd2' },
];

const THEME_BY_NAME = new Map(THEMES.map((t) => [t.name, t]));

/**
 * @param {string|null|undefined} name
 * @returns {ThemeDescriptor}
 */
export function resolveTheme(name) {
  return THEME_BY_NAME.get(name) ?? THEME_BY_NAME.get(DEFAULT_THEME);
}

/**
 * Apply a theme by name to <html>, setting or clearing both attributes.
 * @param {string} name
 * @returns {string} the resolved theme name
 */
export function applyTheme(name) {
  const t = resolveTheme(name);
  const el = document.documentElement;
  if (t.dataTheme) el.setAttribute('data-theme', t.dataTheme);
  else el.removeAttribute('data-theme');
  if (t.dataPalette) el.setAttribute('data-palette', t.dataPalette);
  else el.removeAttribute('data-palette');
  return t.name;
}

/** @returns {string} a valid theme name (falls back to DEFAULT_THEME) */
export function loadStoredTheme() {
  let stored = null;
  try { stored = localStorage.getItem(STORAGE_KEY); } catch { /* unavailable */ }
  return THEME_BY_NAME.has(stored) ? stored : DEFAULT_THEME;
}

/** @param {string} name */
export function storeTheme(name) {
  try { localStorage.setItem(STORAGE_KEY, name); } catch { /* unavailable */ }
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `make test-js`
Expected: PASS — all `theme.test.js` cases green.

- [ ] **Step 6: Confirm tsc passes**

Run: `make check-js`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/decafclaw/web/static/lib/theme.js src/decafclaw/web/static/lib/theme.test.js
git rm --cached src/decafclaw/web/static/lib/smoke.test.js 2>/dev/null || true
git commit -m "feat(web): theme registry + resolve/apply/persistence (#654)"
```

---

### Task 3: Palette CSS files + style.css imports

**Files:**
- Create: `src/decafclaw/web/static/styles/palettes/dracula.css`
- Create: `src/decafclaw/web/static/styles/palettes/solarized-light.css`
- Modify: `src/decafclaw/web/static/style.css`

**Interfaces:**
- Consumes: the `data-palette` attribute set by `applyTheme` (Task 2).
- Produces: `:root[data-palette="dracula"]` and `:root[data-palette="solarized-light"]` token overrides, loaded globally.

- [ ] **Step 1: Create the Dracula palette**

Create `src/decafclaw/web/static/styles/palettes/dracula.css`:
```css
/* Dracula palette — dark base. Hex verified against https://spec.draculatheme.com/
 *
 * Applied when <html data-palette="dracula"> (set alongside data-theme="dark"
 * by lib/theme.js). Overrides only the color tokens that define Dracula, on top
 * of Pico's dark base — including the --pico-primary-* family, which palettes
 * are the sanctioned place to set (see docs/web-ui-design.md). */

:root[data-palette="dracula"] {
  --pico-background-color: #282a36;
  --pico-color: #f8f8f2;
  --pico-muted-color: #6272a4;

  --pico-card-background-color: #2f313f;
  --pico-card-sectioning-background-color: #2f313f;
  --pico-form-element-background-color: #21222c;
  --pico-muted-border-color: #44475a;

  --pico-primary: #bd93f9;
  --pico-primary-hover: #caa6fa;
  --pico-primary-focus: rgba(189, 147, 249, 0.375);
  --pico-primary-background: #bd93f9;
  --pico-primary-inverse: #282a36;

  --pico-code-background-color: #21222c;
  --pico-mark-background-color: #f1fa8c;
  --pico-mark-color: #282a36;
  --pico-del-color: #ff5555;
  --pico-ins-color: #50fa7b;
}
```

- [ ] **Step 2: Create the Solarized Light palette**

Create `src/decafclaw/web/static/styles/palettes/solarized-light.css`:
```css
/* Solarized Light palette — light base. Ethan Schoonover's published palette
 * (https://ethanschoonover.com/solarized/), MIT-licensed.
 *
 * Applied when <html data-palette="solarized-light"> (set alongside
 * data-theme="light" by lib/theme.js). Overrides only the color tokens on top
 * of Pico's light base — including the --pico-primary-* family (see
 * docs/web-ui-design.md). */

:root[data-palette="solarized-light"] {
  --pico-background-color: #fdf6e3;   /* base3  */
  --pico-color: #657b83;              /* base00 */
  --pico-muted-color: #93a1a1;        /* base1  */

  --pico-card-background-color: #eee8d5;            /* base2 */
  --pico-card-sectioning-background-color: #eee8d5; /* base2 */
  --pico-form-element-background-color: #fdf6e3;    /* base3 */
  --pico-muted-border-color: #93a1a1;               /* base1 */

  --pico-primary: #268bd2;            /* blue */
  --pico-primary-hover: #1a6ea8;
  --pico-primary-focus: rgba(38, 139, 210, 0.25);
  --pico-primary-background: #268bd2;
  --pico-primary-inverse: #fdf6e3;

  --pico-code-background-color: #eee8d5;   /* base2 */
  --pico-mark-background-color: #eee8d5;   /* base2 */
  --pico-mark-color: #657b83;              /* base00 */
  --pico-del-color: #dc322f;          /* red   */
  --pico-ins-color: #859900;          /* green */
}
```

- [ ] **Step 3: Import both palettes in style.css**

Edit `src/decafclaw/web/static/style.css` — insert the two imports immediately after the `variables.css` import (line 1), before `primitives.css`:
```css
@import './styles/variables.css';
@import './styles/palettes/dracula.css';
@import './styles/palettes/solarized-light.css';
@import './styles/primitives.css';
```

- [ ] **Step 4: Verify the CSS is well-formed and loads**

Run: `make check-js`
Expected: PASS (no JS impact; confirms nothing else broke).
Manual sanity: grep confirms the imports are present:
```bash
grep -n "palettes/" src/decafclaw/web/static/style.css
```
Expected: two lines, dracula then solarized-light.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/web/static/styles/palettes/dracula.css \
  src/decafclaw/web/static/styles/palettes/solarized-light.css \
  src/decafclaw/web/static/style.css
git commit -m "feat(web): Dracula + Solarized Light palette token overrides (#654)"
```

---

### Task 4: `theme-toggle.js` rewrite + palette popover styling

**Files:**
- Modify (full rewrite): `src/decafclaw/web/static/components/theme-toggle.js`
- Modify: `src/decafclaw/web/static/styles/sidebar.css`

**Interfaces:**
- Consumes: `THEMES`, `applyTheme`, `loadStoredTheme`, `storeTheme` from `../lib/theme.js` (Task 2); the palette CSS from Task 3.
- Produces: the `<theme-toggle>` custom element (already mounted in `conversation-sidebar.js` and `login-view.js` — no mount changes).

- [ ] **Step 1: Rewrite the component**

Replace the entire contents of `src/decafclaw/web/static/components/theme-toggle.js`:
```js
import { LitElement, html } from 'lit';
import { THEMES, applyTheme, loadStoredTheme, storeTheme } from '../lib/theme.js';

/**
 * Theme toggle: base light/dark/system buttons + a color-palette popover.
 *
 * A single active theme name is chosen either via a base button (which clears
 * any custom palette) or via a palette item (which supersedes the base and
 * drives its own base mode). Persists via lib/theme.js.
 */
export class ThemeToggle extends LitElement {
  static properties = {
    _theme: { type: String, state: true },
    _paletteOpen: { type: Boolean, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this._theme = loadStoredTheme();
    this._paletteOpen = false;
    applyTheme(this._theme);
    /** @param {MouseEvent} e */
    this._onDocClick = (e) => {
      if (this._paletteOpen && !this.contains(/** @type {Node} */ (e.target))) {
        this._paletteOpen = false;
      }
    };
  }

  connectedCallback() {
    super.connectedCallback();
    document.addEventListener('click', this._onDocClick);
  }

  disconnectedCallback() {
    document.removeEventListener('click', this._onDocClick);
    super.disconnectedCallback();
  }

  /** @param {string} name */
  #select(name) {
    this._theme = applyTheme(name);
    storeTheme(this._theme);
    this._paletteOpen = false;
  }

  render() {
    const bases = THEMES.filter((t) => t.kind === 'base');
    const palettes = THEMES.filter((t) => t.kind === 'palette');
    const paletteActive = palettes.some((p) => p.name === this._theme);
    return html`
      <div class="theme-toggle">
        ${bases.map((t) => html`
          <button
            class="theme-btn ${this._theme === t.name ? 'active' : ''}"
            @click=${() => this.#select(t.name)}
            title=${t.label}
          >${t.icon}</button>
        `)}
        <div class="theme-palette-wrap">
          <button
            class="theme-btn ${paletteActive ? 'active' : ''}"
            @click=${() => { this._paletteOpen = !this._paletteOpen; }}
            title="Color palette"
            aria-haspopup="true"
            aria-expanded=${this._paletteOpen ? 'true' : 'false'}
          >🎨</button>
          ${this._paletteOpen ? html`
            <div class="theme-palette-menu" role="menu">
              ${palettes.map((p) => html`
                <button
                  class="theme-palette-item ${this._theme === p.name ? 'active' : ''}"
                  role="menuitem"
                  @click=${() => this.#select(p.name)}
                >
                  <span class="theme-palette-dot" style="background:${p.dot}"></span>
                  ${p.label}
                </button>
              `)}
            </div>
          ` : ''}
        </div>
      </div>
    `;
  }
}

customElements.define('theme-toggle', ThemeToggle);
```

- [ ] **Step 2: Add popover styling to sidebar.css**

Edit `src/decafclaw/web/static/styles/sidebar.css` — add these rules directly after the existing `.theme-toggle .theme-btn.active` block (keep the existing `.theme-toggle` / `.theme-btn` rules unchanged):
```css
.theme-palette-wrap {
  position: relative;
  display: inline-flex;
}

.theme-palette-menu {
  position: absolute;
  bottom: calc(100% + 4px);   /* toggle sits in the sidebar footer — open upward */
  right: 0;
  z-index: 10;
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  padding: 0.25rem;
  background: var(--pico-card-background-color);
  border: 1px solid var(--pico-muted-border-color);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
  min-width: 9rem;
}

.theme-palette-menu .theme-palette-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  background: none;
  border: none;
  border-radius: 4px;
  padding: 0.3rem 0.5rem;
  margin: 0;
  color: inherit;              /* escape the button-scoped --pico-color alias */
  font-size: 0.85rem;
  line-height: 1;
  cursor: pointer;
  white-space: nowrap;
  text-align: left;
}

.theme-palette-menu .theme-palette-item:hover {
  background: var(--pico-secondary-hover-background);
}

.theme-palette-menu .theme-palette-item.active {
  background: var(--pico-primary-background);
  color: var(--pico-primary-inverse);
}

.theme-palette-dot {
  width: 0.75rem;
  height: 0.75rem;
  border-radius: 50%;
  flex: 0 0 auto;
  border: 1px solid var(--pico-muted-border-color);
}
```

- [ ] **Step 3: Confirm tsc passes**

Run: `make check-js`
Expected: PASS (no type errors in the rewritten component).

- [ ] **Step 4: Rebuild the vendor bundle if needed, then verify no test regressions**

The component imports `lit` (already vendored) and `../lib/theme.js` (source-served) — no vendor rebuild required. Run the JS tests to confirm nothing broke:
Run: `make test-js`
Expected: PASS (theme.test.js still green; component has no unit test).

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/web/static/components/theme-toggle.js \
  src/decafclaw/web/static/styles/sidebar.css
git commit -m "feat(web): palette popover in theme-toggle, driven by lib/theme.js (#654)"
```

---

### Task 5: Documentation + session notes

**Files:**
- Modify: `docs/web-ui-design.md`
- Modify: `CLAUDE.md`
- Create/append: `docs/dev-sessions/2026-07-24-1329-color-themes/notes.md`

**Interfaces:**
- Consumes: the finished feature (Tasks 1–4).
- Produces: doc coverage of the theming model.

- [ ] **Step 1: Add a theming section to web-ui-design.md**

Append a new section to `docs/web-ui-design.md` (after the Pico-in-context section) documenting:
- The two-axis model: `data-theme` (light|dark|absent, Pico's base) × `data-palette` (dracula|solarized-light|absent, our overlay), both set on `<html>` by `lib/theme.js`.
- The theme registry (name → descriptor) and one-logical-selection UI (base buttons clear palette; palette items supersede base).
- **The primary-family exception**: palettes are the one sanctioned place to set `--pico-primary-background` / `--pico-primary-inverse`, scoped under `[data-palette]`; this is what makes the button-scoped alias resolve to the palette color. Component CSS still must not touch them.
- How to add a palette: new `styles/palettes/<name>.css` (`:root[data-palette="<name>"]` block) + `@import` in `style.css` + one `THEMES` entry in `lib/theme.js`.

```markdown
## Color palettes / theming

The UI theme is two orthogonal attributes on `<html>`, both managed by
`lib/theme.js`:

- `data-theme` = `light` | `dark` | *(absent = system)* — Pico's built-in
  structural base. Unchanged from stock Pico.
- `data-palette` = `dracula` | `solarized-light` | *(absent = stock Pico
  colors)* — our color overlay, layered on top of the base.

A single theme **name** resolves through the `THEMES` registry in `lib/theme.js`
to a descriptor carrying both attributes, so a palette always pins its required
base mode (Dracula → dark, Solarized Light → light) and nonsensical combos are
unrepresentable. The active name persists under localStorage `decafclaw-theme`
(backward compatible with the legacy `light`/`dark`/`system` values).

The `theme-toggle` component surfaces this as base light/dark/system buttons plus
a 🎨 palette popover. It's one logical selection: clicking a base button clears
any palette; picking a palette supersedes the base.

### The primary-family exception

Palettes are the **one sanctioned place** we set `--pico-primary-background` and
`--pico-primary-inverse` (listed under "variables we deliberately don't touch"
above for component code). Recoloring the brand primary is the whole point of a
palette, and setting these is exactly what makes the button-scoped alias resolve
to the palette's color instead of stock Pico blue. The exception is scoped to the
`[data-palette="…"]` block only.

### Adding a palette

1. Create `styles/palettes/<name>.css` with a single `:root[data-palette="<name>"]`
   block overriding the color tokens (see `dracula.css` for the full token list).
2. Add the `@import` to `style.css` after `variables.css`.
3. Add one entry to `THEMES` in `lib/theme.js` (`kind: 'palette'`, the required
   `dataTheme` base, `dataPalette` = the file name, and a `dot` swatch color).
```

- [ ] **Step 2: Update CLAUDE.md conventions**

Two edits to `CLAUDE.md`:

(a) In the "Running" fenced block, add the new target after the `make check` line:
```
make test-js      # vitest (JS unit tests)
```

(b) In the "Web UI styling" gotchas list, add a bullet:
```markdown
- **Color palettes are a two-axis model** (`data-theme` × `data-palette`, managed by `lib/theme.js`). Palettes are the ONLY sanctioned place to set `--pico-primary-background`/`-inverse`, scoped under `[data-palette]`. See [docs/web-ui-design.md](docs/web-ui-design.md#color-palettes--theming).
```

- [ ] **Step 3: Write the session notes summary**

Create `docs/dev-sessions/2026-07-24-1329-color-themes/notes.md` summarizing: what shipped (Dracula + Solarized Light, two-axis model, vitest infra), the Alucard→Solarized pivot and why, the follow-up issue #658, and manual-verification results.

- [ ] **Step 4: Commit**

```bash
git add docs/web-ui-design.md CLAUDE.md docs/dev-sessions/2026-07-24-1329-color-themes/notes.md
git commit -m "docs(web): document the color-palette theming model (#654)"
```

---

### Task 6: Manual verification in the live web UI

**Files:** none (verification only).

**Interfaces:** consumes the full feature.

> **Note:** requires running the web server. Per project rules, do NOT start `make dev`/`run` without checking with Les first (one bot instance / port). Use the worktree's `HTTP_PORT=18891`. This task is a checklist Les runs (or approves running).

- [ ] **Step 1: Start the worktree server** (with Les's go-ahead), e.g. web-only: `MATTERMOST_ENABLED=false HTTP_PORT=18891 make run` — or confirm the running instance picks up the built assets.

- [ ] **Step 2: For each of the five themes** (Light, Dark, System, Dracula, Solarized Light), select it in the sidebar toggle and confirm:
  - `<html>` gets the expected `data-theme` / `data-palette` attributes (DevTools).
  - Background, text, cards, form fields, and **buttons** (watch the primary-scoped tokens) render correctly.
  - Code blocks read correctly (Atom One Dark under Dracula, Atom One Light under Solarized Light).

- [ ] **Step 3: Confirm the palette popover** opens on 🎨, closes on outside-click and on selection, and that picking a base button clears an active palette.

- [ ] **Step 4: Reload the page** and confirm the selected theme persists. Manually set `localStorage['decafclaw-theme'] = 'dark'` (legacy value) and reload → resolves to dark.

- [ ] **Step 5: Repeat the toggle check in the login view** (log out or view `/` unauthenticated) — the toggle renders and works there too.

- [ ] **Step 6: Record results** in `notes.md` and proceed to PR.

---

## Self-Review

**Spec coverage:**
- Two-attribute model → Task 2 (`applyTheme`) + Task 3 (palette CSS). ✓
- Registry + backward-compatible persistence → Task 2. ✓
- Dracula + Solarized Light palettes → Task 3. ✓
- Buttons + palette popover, one-logical-selection → Task 4. ✓
- vitest + jsdom infra, `lib/theme.js` extraction, unit tests → Tasks 1–2. ✓
- Code-block coherence (no extra work) → verified in Task 6 Step 2. ✓
- Docs (web-ui-design.md theming section, primary-family exception) → Task 5. ✓
- Non-goals (hljs recolor, xterm/leaflet, custom palettes, second picker) → not implemented. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. Task 5 Step 3 (notes.md) is prose-summary-by-nature, acceptable for a notes doc.

**Type consistency:** `resolveTheme`/`applyTheme`/`loadStoredTheme`/`storeTheme`/`THEMES`/`STORAGE_KEY`/`DEFAULT_THEME` names and signatures match between Task 2's definitions, its tests, and Task 4's imports. Descriptor fields (`name`, `label`, `kind`, `dataTheme`, `dataPalette`, `icon`, `dot`) are consistent across the module, tests, and component.
