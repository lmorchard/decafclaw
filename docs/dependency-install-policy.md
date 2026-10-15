# Dependency Install Policy

Supply-chain policy for the two npm projects in this repo —
`src/decafclaw/web/static/` (web UI vendor bundle) and `tui/` (terminal UI).
Adopted in #930; the lockfiles from #706/#716 remain the ground truth for
transitive versions. The Python side (uv) is out of scope.

## Pinning rule

Direct external dependencies are **exact-pinned** in both `package.json`
files. `^` and `~` ranges are forbidden: a range is how an unreviewed version
enters the lockfile, and update PRs are the review point.

- `scripts/check_npm_policy.py` rejects any `^`/`~` specifier in
  `dependencies`, `devDependencies`, `optionalDependencies`, or
  `peerDependencies` of either project. Non-semver references (`file:`,
  `git+`, `npm:` aliases) are legitimate internal references and remain valid.
- The check runs as `make check-npm-policy`, composed into `make check` — so
  CI gates it through the make target (the `lint-and-test` job), never a
  standalone command copy in the workflow.
- Both `.npmrc` files set `save-exact=true`, so `npm install <pkg>` writes an
  exact version rather than re-introducing a range.

## Lifecycle-script policy

Every npm install in the Makefile — `install`, `install-js`, `install-tui`,
`vendor` — runs with `--ignore-scripts`, so **no dependency install/build
script executes** on the normal check/CI install path.

Packages that genuinely need their lifecycle scripts are named in the
project's `.npm-script-allowlist` (one next to each `package.json`). The
install targets then run `npm rebuild` for exactly those packages. To add an
entry:

1. Add the package name to `.npm-script-allowlist`, with the reason in a
   comment above it (e.g. a native module that compiles at install time).
2. Commit it with the dependency change. `make check` fails if an entry does
   not name a package that carries an install script
   (`hasInstallScript`) in the adjacent `package-lock.json`, so stale entries
   cannot linger silently — remove the entry when the package goes away.

Both allowlists are currently empty. Notable non-exceptions:

- **esbuild** resolves its platform binary through the `@esbuild/<platform>`
  optional dependency; its postinstall only verifies. A clean install with
  scripts disabled builds the vendor bundle and runs vitest in both projects
  (verified in #930: `make vendor`, `make check-js`, `make check-browser-assets`,
  `make test-js`, `make check-tui`, `make test-tui` all pass).
- **fsevents** ships the prebuilt `fsevents.node` binary in the package; the
  node-gyp fallback in its install script is never needed.

## Proof

`tests/test_npm_policy.py` guards the policy:

- A Makefile scan asserts every `npm install` / `npm ci` invocation carries
  `--ignore-scripts` (catches a future target that opts out).
- A throwaway fixture dependency — built in a temp dir, deliberately outside
  the live installs — carries a `preinstall` script that writes a marker
  file. With scripts enabled the marker appears (control, proves the probe is
  not vacuous); under `npm install --ignore-scripts` and under
  `npm ci --ignore-scripts` (the CI install path) it does not.
- The pin/allowlist check is exercised against throwaway `package.json`
  files for both the rejecting and accepting cases.

## Updating a dependency

1. Edit the exact version in the project's `package.json`.
2. Regenerate the lockfile through the policy path: `make vendor` for
   `web/static` (also rebuilds the committed bundle), or
   `npm install --ignore-scripts` in `tui/`.
3. Commit `package.json` + `package-lock.json` (and the vendor bundle if it
   changed). The update is reviewed as a lockfile diff.
4. `make check` (includes the pin check) and the full test gate.

## Deliberately not done

- **Minimum release age** was dropped from #930's scope: CI's Node 22 ships
  npm 10, and npm 10's minimum-release-age enforcement was not confirmed.
  The issue forbids claiming protection the package manager ignores, so the
  setting is absent rather than silently inert. Revisit when the toolchain
  enforces it.
- Audit findings from `npm install` output are handled through explicit
  update decisions (the flow above), not automatic broad upgrades.
