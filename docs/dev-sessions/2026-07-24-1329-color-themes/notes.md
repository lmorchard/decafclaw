# Notes: Selectable color themes (Dracula + Solarized Light)

**Issue:** [#654](https://github.com/lmorchard/decafclaw/issues/654)
**Branch:** `color-themes`
**Date:** 2026-07-24

## What shipped

- **Two-axis theme model.** `<html>` now carries `data-theme` (`light`/`dark`/absent
  = Pico's structural base, unchanged) and `data-palette` (`dracula`/`solarized-light`/
  absent = our color overlay), both managed by a new pure module `lib/theme.js`. A
  single theme *name* resolves through a `THEMES` registry to a descriptor carrying
  both attributes, so a palette always pins its required base mode and nonsensical
  combos (e.g. Dracula-in-light) are unrepresentable.
- **Two palettes, one dark and one light**, so both base paths get exercised:
  `styles/palettes/dracula.css` and `styles/palettes/solarized-light.css`, each a
  single `:root[data-palette="…"]` block overriding ~15-20 color tokens on top of
  Pico's base. Palettes are the one sanctioned place we set `--pico-primary-background`
  / `--pico-primary-inverse` — that's exactly what makes the button-scoped alias
  resolve to the palette color instead of stock Pico blue.
- **`theme-toggle` component** now surfaces base light/dark/system buttons plus a
  🎨 palette popover, as one logical selection: a base button clears any palette,
  a palette selection supersedes the base. Persisted under localStorage
  `decafclaw-theme`, backward compatible with the legacy `light`/`dark`/`system`
  values.
- **vitest + jsdom** — the repo's first JS unit-test runner. `lib/theme.js` was
  extracted as a pure module (registry + resolve/apply/persistence, operating on
  the global `document`/`localStorage` that jsdom supplies) precisely so it could
  be unit-tested independently of the Lit component; `theme-toggle.js` imports from
  it and owns only rendering. Covered in `lib/theme.test.js`: resolve/apply
  correctness (including the `null` = absent cases), unknown-name fallback to
  `system`, and legacy-value backward compatibility. New `make test-js` Makefile
  target; `tsconfig.json` excludes `**/*.test.js` so `tsc --checkJs` doesn't choke
  on vitest globals.

## The Alucard → Solarized Light pivot

The spec originally planned Dracula's light counterpart, **Alucard**, as the second
palette — a natural pairing since both come from the same design family. Partway
through spec review we found Alucard ships only with the paid **Dracula PRO**
product: there's no open, verifiable color spec to reproduce, and reproducing a
paid palette's exact values raised a licensing question we didn't want to carry.

We swapped in **Solarized Light** instead — Ethan Schoonover's classic light
scheme, fully open (MIT-licensed), with published exact hex values — and it serves
the same structural purpose (a light-base palette to exercise the light path
alongside Dracula's dark path) without either the licensing ambiguity or the
missing spec. The spec and plan docs in this session directory were revised in
place to reflect the swap before implementation started, so no code was ever
written against Alucard.

## Follow-up: #658

Introducing vitest as the first JS test runner also enables a broader backfill
effort tracked separately: [#658](https://github.com/lmorchard/decafclaw/issues/658)
covers extending unit-test coverage to the other 20+ Lit components, which today
are verified only by `tsc --checkJs` and manual checking. This session's scope
stayed limited to `lib/theme.js` — the extraction that made testing this specific
feature clean is a template for that broader effort, not a first installment of it.

## Manual verification: pending

Task 6 (manual verification in the live UI — visual check of both palettes across
`data-theme` combinations, popover interaction, persistence across reload, and the
sidebar-footer / login-view mount points) has not run yet as of writing this note.
No results to report here — this is a placeholder marking that the automated work
(Tasks 1-4) is done and reviewed, and live-UI verification is the next step before
this branch is considered ready to merge.
