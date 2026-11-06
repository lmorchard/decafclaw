---
ghflow_project_facts: 1
written: 2026-11-05
sources:
  - AGENTS.md 7ca5c7a9ded51a6b00a965d6866ab4c43586546a
  - CLAUDE.md 7ca5c7a9ded51a6b00a965d6866ab4c43586546a
  - Makefile 1796e2c277fa314664800bf69cffa88a651f9d0e
  - .github/workflows/ci.yml 2de18948e0afc3ee4c14570649660a08aea316d5
  - conftest.py fb46496f6536760ece617b135afc7aef3c6eac93
  - tests/conftest.py 49ff00ed9d4d72a8cc7aa2e07108a490c8802c0c
  - pyproject.toml b705dda2e2f0e144f03fa64a344fdf9ef122232d
  - scripts/gen_message_types.py 06a0ab1456ab2e0655d70eab5e68f24a2eda9be9
  - docs/config.md 5bba89b6642b397fe4f9bbe7df8d2f62680c172b
  - docs/testing.md e30d35456d9757a42f84f13abeca170998145fd2
---
# decafclaw project facts

`AGENTS.md` is a symlink to `CLAUDE.md`; read it in full. `ghflow facts` reports when a source above changes.

## Worktree setup
- Worktrees at `.claude/worktrees/{branch}/`, branched from `origin/main`; never edit the main clone. `uv sync` (or `make install`); a doc-only worktree skips it (say so in the report).
- `cp ../../../.env .env` from the main clone (it points at the main clone's `data/`), then add `HTTP_PORT=<port>` (default 18880) to avoid port conflicts with other worktrees. Run `make test` once for a baseline before code changes.

## Gates
- `make fmt` before every commit. `make check` = `install-js check-npm-policy check-message-types fmt-check lint typecheck check-js check-browser-assets`; `make test` = `uv run pytest tests/ contrib/skills/`.
- CI: required `lint-and-test` (`make check`, `playwright install chromium`, pytest) and `js-test` (`make test-js`); `check-tui` runs `make check-tui test-tui` outside `make check`.
- npm policy (#930): direct deps exact-pinned (no ^/~); every Makefile npm install runs `--ignore-scripts` (allowlisted scripts via `npm rebuild`); `make check-npm-policy` gates it (detail in `docs/dependency-install-policy.md`). `make install-js` uses `npm ci`; do not `npm install` in `src/decafclaw/web/static/` — dependency changes go through `make vendor`.

## Generated files
- `src/decafclaw/web/message_types.json` generates `src/decafclaw/web/message_types.py`, `src/decafclaw/web/static/lib/message-types.js`, `docs/websocket-messages.md`, and `tui/src/types.generated.ts` via `make gen-message-types`. `make check-message-types` (inside `make check`) fails on a hand edit.
- `make gen-api-client` (inside `make check-js`) regenerates the committed OpenAPI client under `src/decafclaw/web/static/lib/api-client/`. `make vendor` rebuilds the committed vendor bundle.

## Test layout
- Root `conftest.py` holds the shared fixtures `config`, `ctx`, `tmp_data`, and autouse `mock_embed_text`. `tests/conftest.py` adds `stub_llm`, an autouse guard that fails any unstubbed LLM call, and an autouse guard that fails tests leaking asyncio's running-loop mark. `tests/web/conftest.py` adds `http_config`, `app`, `client`.
- One file: `uv run pytest tests/test_x.py -n 0` (`-n 0` turns off xdist). Default addopts: `-n auto --dist=worksteal -m 'not integration'`; `testpaths = ["tests", "contrib"]`.
- Markers: `integration` hits real LLM APIs (`make test` excludes it); `live_llm` opts a test out of the unstubbed-LLM-call guard in tests/conftest.py — real provider code with HTTP mocked below the guard; `scripted_provider` drives the normal LLM path through the in-memory `decafclaw.testing.ScriptedProvider` (#923) while the real network providers stay guarded. Browser tests need Playwright chromium. `make ephemeral-test` runs the scripted end-to-end tool round-trip (#957; details in `docs/ephemeral-instance.md`).

## Session notes
- `docs/dev-sessions/YYYY-MM-DD-HHMM-slug/` with `spec.md`, `plan.md`, `notes.md`. Session docs are committed.

## Running the app
- `make run-dev` from a worktree (#1096): refuses to run from the main clone, seeds the worktree's `data-dev/` (gitignored) with a copy of the main clone's `data/decafclaw/config.json` only, and runs `uv run decafclaw` with `DATA_HOME` set to it, `MATTERMOST_ENABLED=false`, `EMAIL_ENABLED=false`, `NOTIFICATIONS_CHANNELS_MATTERMOST_DM_ENABLED=false`, `HEARTBEAT_INTERVAL=0` (interval of 0 disables the heartbeat timer), `HTTP_ENABLED=true`, and the worktree's `HTTP_PORT` (from the worktree's `.env`; default 18880). Mattermost stays off unless an issue calls for it, and then only attended with Les's go-ahead.

## Commits and PRs
- Subjects mostly `type(scope): summary`, for example `fix(agent): ...`. Every change goes through a PR. `Closes #N` closes the issue on merge.

## Board
- Owner `lmorchard`, project `6`, field `Status`: Backlog, Ready, In progress, In review, Done. Priority P0/P1/P2; Size XS to XL.

Live state: `python3 "$GHFLOW_CLI" facts --issue ISSUE_URL`.
