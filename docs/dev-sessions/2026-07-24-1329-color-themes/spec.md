# Spec: Selectable color themes (Dracula + Solarized Light)

**Issue:** [#654](https://github.com/lmorchard/decafclaw/issues/654)
**Branch:** `color-themes`
**Date:** 2026-07-24

## Problem

The web UI loads stock [Pico v2](https://picocss.com/) and exposes only the two
built-in schemes (light / dark) via a three-button `theme-toggle` component. There
is no way to select an alternate color palette such as
[Dracula](https://draculatheme.com/). We define zero `--pico-*` overrides of our
own today, so all color comes straight from Pico's defaults.

We want selectable color palettes, shipping two concrete ones from day one — a dark
palette (**Dracula**) and a light palette (**Solarized Light**) — so both the
light-base and dark-base paths are exercised.

> **Note on the light palette choice.** We initially considered *Alucard* (Dracula's
> light counterpart), but Alucard ships only with the paid **Dracula PRO** product and
> has no open, verifiable color spec — plus a plausible licensing wrinkle to reproducing
> a paid palette's values. We chose **Solarized Light** instead: Ethan Schoonover's
> classic light scheme, fully open (MIT), with published exact hex values.

## Goals

- Users can pick a color palette in addition to the existing light / dark / system base.
- Adding a future palette is a small, well-bounded change (one CSS file + one registry entry).
- No regression to the existing light / dark / system behavior, and existing
  persisted preferences keep working unchanged.

## Non-goals (v1)

- Syntax-highlight recoloring beyond the base light/dark treatment (already coherent —
  see "Code blocks" below).
- Theming xterm (web terminal) or Leaflet (map widget).
- User-authored / uploadable custom palettes.
- A separate palette axis surfaced as a second independent picker (we deliberately
  keep one logical selection — see Architecture).

## Architecture

### Two attributes, one logical selection

`<html>` carries two attributes:

- `data-theme` = `light | dark` (or **absent** for system) — Pico's structural base,
  unchanged from today.
- `data-palette` = `dracula | alucard` (or **absent** for stock Pico) — our color overlay.

Pico only recognizes `data-theme` values `light` / `dark` / (unset → light). An unknown
value like `data-theme="dracula"` would fall back to Pico's light `:root` defaults,
forcing us to redefine the full ~130-token set. Instead we layer a palette **on top of**
a known base mode: Pico's own dark/light base supplies the structural tokens for free,
and each palette overrides only the ~15–20 color tokens that define its identity.

### Theme registry

A single registry maps each selectable name to a descriptor. It is the source of truth
for both the toggle UI and the apply logic:

| name      | data-theme      | data-palette |
|-----------|-----------------|--------------|
| `light`   | `light`         | —            |
| `dark`    | `dark`          | —            |
| `system`  | (absent)        | —            |
| `dracula` | `dark`          | `dracula`         |
| `solarized-light` | `light` | `solarized-light` |

Applying a theme sets or clears **both** attributes from the descriptor. Because the
base modes for the custom palettes are baked into the registry, nonsensical combos
(e.g. "Dracula in light mode") are unrepresentable.

### Persistence

localStorage stores a single value under the existing key `decafclaw-theme` — the theme
**name** (`light` / `dark` / `system` / `dracula` / `solarized-light`). This is
**backward compatible**: previously stored `light` / `dark` / `system` values still
resolve through the registry with no migration. On load, an unknown/missing value falls
back to `system`.

## CSS

- New files `styles/palettes/dracula.css` and `styles/palettes/solarized-light.css`,
  each a single `:root[data-palette="…"] { … }` block. Imported by `style.css`
  **after** `variables.css` so palette tokens sit downstream of the base layer.
- Tokens each palette overrides (~15–20):
  - Backgrounds: `--pico-background-color`, `--pico-card-background-color`,
    `--pico-form-element-background-color`
  - Text: `--pico-color`, `--pico-muted-color`
  - Borders: `--pico-muted-border-color`
  - Semantic / inline: `--pico-code-background-color`, `--pico-mark-background-color`,
    `--pico-del-color`, `--pico-ins-color`
  - **Primary family:** `--pico-primary`, `--pico-primary-hover`, `--pico-primary-focus`,
    **`--pico-primary-background`**, **`--pico-primary-inverse`**

### Deliberate exception to `web-ui-design.md`

`docs/web-ui-design.md` lists `--pico-primary-background` and `--pico-primary-inverse`
under "variables we deliberately don't touch," because Pico owns the filled-primary look
and component code should not override it. **Palettes are the one sanctioned place we DO
set them** — recoloring the brand primary is the entire point of a palette, and setting
these is precisely what makes the button-scoped alias (the documented `<button>` trap,
where `--pico-color`/`--pico-background-color` re-alias to the primary family) resolve to
the palette's color instead of staying stock Pico blue. This exception is scoped to the
`[data-palette="…"]` block; component CSS still must not touch these vars.

### Palette values

- **Dracula** (dark base) — verified against <https://spec.draculatheme.com/>:
  background `#282A36`, selection/current-line `#44475A`, foreground `#F8F8F2`,
  comment `#6272A4`, purple `#BD93F9` (primary), red `#FF5555`, green `#50FA7B`,
  yellow `#F1FA8C`, plus the standard Dracula accents.
- **Solarized Light** (light base) — Ethan Schoonover's published palette: base3
  `#fdf6e3` (bg), base2 `#eee8d5` (raised bg), base00 `#657b83` (body text), base1
  `#93a1a1` (muted), blue `#268bd2` (primary), red `#dc322f`, green `#859900`,
  yellow `#b58900`.

Exact hex → `--pico-*` token mapping is captured in the palette CSS files
(self-documenting); the plan pins concrete starting values.

## Code blocks (in scope, zero extra work)

`styles/hljs-themes.css` already switches syntax-highlight colors on
`:root[data-theme="dark"]` vs light. Because each palette sets a base `data-theme`,
Dracula (dark base) automatically gets Atom One Dark and Solarized Light (light base)
gets Atom One Light. Coherent by construction; no palette-specific highlight rules
needed for v1.

## UI — `theme-toggle.js`

Keep the existing ☀️ / 🌙 / 💻 base buttons. Add a 🎨 **palette button** that opens a
small popover listing the custom palettes (Dracula, Alucard), each with a representative
color dot.

**One active selection.** The toggle represents a single active theme name, chosen either
via a base button or via the palette popover:

- Clicking a base button (☀️/🌙/💻) sets the active theme to `light`/`dark`/`system` and
  **clears** any custom palette (reverts to stock Pico).
- Selecting a palette from the popover sets the active theme to `dracula`/`alucard`, which
  **supersedes** the base-button selection and drives its own base mode.
- Active-state indicators reflect whichever was chosen last: when a palette is active, the
  🎨 button shows the active state (and the base buttons show none); when a base is active,
  the corresponding base button shows the active state.

The component renders unchanged in both mount points (sidebar footer, login view).

## Testing

**The repo currently has no JS test runner** — all 21 Lit components are verified only by
`make check-js` (`tsc --checkJs`) + manual checking. This feature **introduces vitest +
jsdom** as the first JS test infrastructure (see [#658](https://github.com/lmorchard/decafclaw/issues/658)
for the broader backfill effort it enables).

- Add `vitest` + `jsdom` as devDependencies, a `vitest.config.js` (jsdom environment), an
  npm `test` script, and a `make test-js` target. Exclude `**/*.test.js` from the tsconfig
  `include` so `tsc --checkJs` doesn't try to type-check vitest globals.
- To make the logic testable, extract the registry + resolve/apply/persistence logic into
  a pure module `lib/theme.js` (operating on the global `document` / `localStorage`, which
  jsdom supplies — matching the `lib/sticky-state.js` convention). `theme-toggle.js`
  imports from it and owns only rendering.
- Unit tests (`lib/theme.test.js`) for:
  - `resolveTheme(name)` → correct `dataTheme` / `dataPalette` descriptor (including the
    `null` = absent cases), and unknown name → `system` descriptor
  - `applyTheme(name)` sets/clears both `<html>` attributes correctly per theme
  - `loadStoredTheme()` / `storeTheme()` round-trip via the `decafclaw-theme` key
  - **backward compatibility:** a legacy stored `light`/`dark`/`system` value resolves
    correctly; unknown/missing value falls back to `system`
- No eval case — deterministic UI/storage logic, no LLM-visible behavior.
- Manual verification in the live web UI: each of the five themes renders correctly in
  both mount points, and buttons/forms/cards/code blocks read correctly under Dracula and
  Solarized Light (watch the button-scoped primary tokens specifically).

## Documentation

- Add a "Palettes / theming" section to `docs/web-ui-design.md` documenting the
  `data-theme` × `data-palette` two-axis model, the registry, and the primary-family
  exception. Update in the same PR.

## Key files

- `src/decafclaw/web/static/lib/theme.js` (new) — registry + resolve/apply/persistence logic
- `src/decafclaw/web/static/lib/theme.test.js` (new) — vitest unit tests
- `src/decafclaw/web/static/components/theme-toggle.js` — UI, imports `lib/theme.js`
- `src/decafclaw/web/static/style.css` — import the palette CSS files
- `src/decafclaw/web/static/styles/palettes/dracula.css` (new)
- `src/decafclaw/web/static/styles/palettes/solarized-light.css` (new)
- `src/decafclaw/web/static/styles/sidebar.css` — `.theme-btn` / palette popover styling
- `src/decafclaw/web/static/package.json` — vitest/jsdom devDeps + `test` script
- `src/decafclaw/web/static/vitest.config.js` (new)
- `src/decafclaw/web/static/tsconfig.json` — exclude `**/*.test.js`
- `Makefile` — `test-js` target
- `docs/web-ui-design.md` — theming section
