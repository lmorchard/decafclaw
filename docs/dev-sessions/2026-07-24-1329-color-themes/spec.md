# Spec: Selectable color themes (Dracula + Alucard)

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
palette (**Dracula**) and its official light counterpart (**Alucard**) — so both the
light-base and dark-base paths are exercised.

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
| `dracula` | `dark`          | `dracula`    |
| `alucard` | `light`         | `alucard`    |

Applying a theme sets or clears **both** attributes from the descriptor. Because the
base modes for the custom palettes are baked into the registry, nonsensical combos
(e.g. "Dracula in light mode") are unrepresentable.

### Persistence

localStorage stores a single value under the existing key `decafclaw-theme` — the theme
**name** (`light` / `dark` / `system` / `dracula` / `alucard`). This is **backward
compatible**: previously stored `light` / `dark` / `system` values still resolve through
the registry with no migration. On load, an unknown/missing value falls back to `system`.

## CSS

- New files `styles/palettes/dracula.css` and `styles/palettes/alucard.css`, each a
  single `:root[data-palette="…"] { … }` block. Imported by `style.css` **after**
  `variables.css` so palette tokens sit downstream of the base layer.
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

- **Dracula** (dark base): background `#282a36`, current-line `#44475a`, foreground
  `#f8f8f2`, comment `#6272a4`, purple `#bd93f9` (primary), plus the standard Dracula
  accents. Source: <https://draculatheme.com/contribute>.
- **Alucard** (light base): Dracula's official light counterpart. Source:
  <https://draculatheme.com/alucard>.

Exact hex mapping to each `--pico-*` token is fixed during implementation and captured
in the palette CSS files (self-documenting).

## Code blocks (in scope, zero extra work)

`styles/hljs-themes.css` already switches syntax-highlight colors on
`:root[data-theme="dark"]` vs light. Because each palette sets a base `data-theme`,
Dracula (dark base) automatically gets Atom One Dark and Alucard (light base) gets Atom
One Light. Coherent by construction; no palette-specific highlight rules needed for v1.

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

- Unit tests (JS) for the registry + apply logic:
  - name → correct `data-theme` / `data-palette` attribute state (including absence)
  - persistence round-trip via the `decafclaw-theme` localStorage key
  - **backward compatibility:** a legacy stored `light`/`dark`/`system` value resolves
    correctly through the registry
  - unknown/missing stored value falls back to `system`
- No eval case — this is deterministic UI/storage logic with no LLM-visible behavior.
- Manual verification in the live web UI: each of the five themes renders correctly in
  both mount points, and buttons/forms/cards/code blocks read correctly under Dracula and
  Alucard (watch the button-scoped primary tokens specifically).

## Documentation

- Add a "Palettes / theming" section to `docs/web-ui-design.md` documenting the
  `data-theme` × `data-palette` two-axis model, the registry, and the primary-family
  exception. Update in the same PR.

## Key files

- `src/decafclaw/web/static/components/theme-toggle.js` — registry + apply logic + UI
- `src/decafclaw/web/static/style.css` — import the palette CSS files
- `src/decafclaw/web/static/styles/palettes/dracula.css` (new)
- `src/decafclaw/web/static/styles/palettes/alucard.css` (new)
- `src/decafclaw/web/static/styles/sidebar.css` — `.theme-btn` / popover styling if needed
- `docs/web-ui-design.md` — theming section
