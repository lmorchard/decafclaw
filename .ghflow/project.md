---
ghflow_project_facts: 1
written: 2026-11-26
sources:
  - AGENTS.md 2b8c7b9510883792dc97ce3bcf05e7925d1c463a
  - CLAUDE.md 2b8c7b9510883792dc97ce3bcf05e7925d1c463a
  - Makefile 117048f9668d222d165ea3f5fd709516000bd410
  - .github/workflows/ci.yml ec6ef8f98a0a7ef7911df8ce3b302212255e31d0
  - conftest.py 2b63e029a3b2c4524ade2c71f18f181f3997dff1
  - tests/conftest.py 3e6f545a3c6d2adf28d926ba539fbba8d574b559
  - tests/web/conftest.py 10cc18c08878e55b406bd3c1d6ed80e8baae60b8
  - pyproject.toml aa087fb9d09cc0b4941afbfa4af643153eb115a5
  - scripts/gen_message_types.py 06a0ab1456ab2e0655d70eab5e68f24a2eda9be9
  - docs/config.md c2b1dce35052989122e98519f103998a2148e1d1
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
- CI required: `lint-and-test` (`make check`, then pytest `-m "not integration and not browser"`) and `js-test` (`make test-js`). Separate jobs: `browser-test` (Playwright chromium, pytest `-m "browser and not integration"`) and `check-tui` (`make check-tui test-tui`). Required checks also run on the merge queue's `merge_group`.
- npm policy (#930): direct deps exact-pinned; every Makefile npm install runs `--ignore-scripts` (allowlisted scripts via `npm rebuild`); `make check-npm-policy` gates it. `make install-js` uses `npm ci`; do not `npm install` in `src/decafclaw/web/static/` — dependency changes go through `make vendor`.

## Generated files
- `make gen` regenerates every committed generated output — `gen-message-types` (from `src/decafclaw/web/message_types.json`: `message_types.py`, `static/lib/message-types.js`, `docs/websocket-messages.md`, `tui/src/types.generated.ts`) and `gen-api-client` (`openapi.json`/`openapi.yaml`, client under `static/lib/api-client/`). Run it after changing wire types or the HTTP API, and commit the output with the code.
- Drift gates `check-message-types` and `check-js` (both in `make check`) fail on a hand edit or stale output, naming `make gen`; `make vendor` rebuilds the committed vendor bundle.

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
