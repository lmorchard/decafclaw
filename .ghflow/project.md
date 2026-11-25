---
ghflow_project_facts: 1
written: 2026-11-24
sources:
  - AGENTS.md 68aea5815e0b5fb9b88e23c297bd72cb3ed53860
  - CLAUDE.md 68aea5815e0b5fb9b88e23c297bd72cb3ed53860
  - Makefile 5c566d7d51b8d9fca722d69184164f4215447ca9
  - .github/workflows/ci.yml aa8dd08ffe089400fe54b30172d21f740a0b05cf
  - conftest.py 2b63e029a3b2c4524ade2c71f18f181f3997dff1
  - tests/conftest.py 3e6f545a3c6d2adf28d926ba539fbba8d574b559
  - tests/web/conftest.py 10cc18c08878e55b406bd3c1d6ed80e8baae60b8
  - pyproject.toml ea12eff96088d2b4c79567f68b463f85137fb556
  - scripts/gen_message_types.py 06a0ab1456ab2e0655d70eab5e68f24a2eda9be9
  - docs/config.md efa6fa6d20219bdc8e32c1fb42fd3dde3879368c
  - docs/testing.md 162454c9807cfed6ae7fb45399b31cfb891ed600
worktree:
  copy:
    - .env
  setup:
    - uv sync
  generated:
    - openapi.json
    - openapi.yaml
    - src/decafclaw/web/static/lib/api-client/
---
# decafclaw project facts

`AGENTS.md` is a symlink to `CLAUDE.md`; read it in full. `ghflow facts` reports when a source above changes.

## Worktree setup
- Worktrees at `.claude/worktrees/{branch}/`, branched from `origin/main`; never edit the main clone. `uv sync` (or `make install`) for the venv; a doc-only worktree skips it (say so in the report).
- `cp ../../../.env .env` from the main clone (it points at shared agent data), then set a distinct `HTTP_PORT`. Run `make test` once for a baseline before code changes.

## Gates
- `make fmt` before every commit. `make check` = `install-js check-npm-policy check-message-types check-skill-annotations fmt-check lint typecheck check-js check-browser-assets`; `make test` = `uv run pytest tests/ contrib/skills/`.
- CI required: `lint-and-test` (`make check`, then pytest `-m "not integration and not browser"`) and `js-test` (`make test-js`). Separate jobs: `browser-test` (Playwright chromium, pytest `-m "browser and not integration"`) and `check-tui` (`make check-tui test-tui`).
- npm policy (#930): direct deps exact-pinned; every Makefile npm install runs `--ignore-scripts` (allowlisted scripts via `npm rebuild`); `make check-npm-policy` gates it. `make install-js` uses `npm ci`; do not `npm install` in `src/decafclaw/web/static/` — dependency changes go through `make vendor`.

## Generated files
- `src/decafclaw/web/message_types.json` generates `message_types.py`, `static/lib/message-types.js`, `docs/websocket-messages.md`, and `tui/src/types.generated.ts` via `make gen-message-types`; `make check-message-types` (in `make check`) fails on a hand edit.
- `make gen-api-client` regenerates `openapi.json`/`openapi.yaml` and the committed client under `static/lib/api-client/`; `make check-js` (in `make check`) fails on drift there. `make vendor` rebuilds the committed vendor bundle.

## Test layout
- Root `conftest.py`: fixtures `config`, `ctx` (seeds the extension manager with default-enabled core extensions), `tmp_data`; autouse `mock_embed_text`. `tests/conftest.py`: `stub_llm`, plus autouse guards that fail unstubbed LLM calls, tests setting OpenTelemetry's global tracer provider (#1424), and tests leaking asyncio's running-loop mark. `tests/web/conftest.py`: package-scoped `http_config`, `bus`, `app`, `client`.
- One file: `uv run pytest tests/test_x.py -n 0` (turns off xdist). Default addopts `-n auto --dist=worksteal -m 'not integration'`; `testpaths = ["tests", "contrib"]`.
- Markers: `integration` hits real LLM APIs (excluded by default; `make test-integration`); `browser` launches Playwright (`make test-browser`, separate CI job, needs chromium installed); `live_llm` opts out of the unstubbed-LLM guard (real provider code, HTTP mocked below it); `scripted_provider` drives the normal LLM path through in-memory `decafclaw.testing.ScriptedProvider` (#923). `make ephemeral-test` runs the scripted end-to-end round-trip (#957; `docs/ephemeral-instance.md`).

## Session notes
- `docs/dev-sessions/YYYY-MM-DD-HHMM-slug/` with `spec.md`, `plan.md`, `notes.md`. Session docs are committed.

## Never run
- `make run`/`dev`/`debug`: one bot instance per Mattermost account (a second misses WebSocket events); Les may already have `make dev` running — check before starting, ask him to stop it if live logs need replacing.
- In a worktree use `make run-dev` instead: refuses to run from the main clone, seeds gitignored `data-dev/` (created-if-missing copies of the main clone's `config.json` + `web_tokens.json`), Mattermost/email/DMs/heartbeat off, web UI on the worktree's `HTTP_PORT` (default 18880); `make stop-dev` stops it via the PIDfile.

## Commits and PRs
- Subjects mostly `type(scope): summary` (e.g. `fix(agent): ...`). Every change goes through a PR; Les reviews before merge. `Closes #N` closes the issue on merge.

## Board
- Owner `lmorchard`, project `6`, field `Status`; the flow uses Ready, In progress, In review, Done. Priority P0/P1/P2; Size XS to XL.

Live state: `python3 "$GHFLOW_CLI" facts --issue ISSUE_URL`.
