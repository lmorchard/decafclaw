---
ghflow_project_facts: 1
written: 2026-10-14
sources:
  - AGENTS.md c051f329d7f18632b0a9578f3ad80d0ff1d093d7
  - CLAUDE.md c051f329d7f18632b0a9578f3ad80d0ff1d093d7
  - Makefile a1ce2143c4fa03d4885e6a392f6d58b75f6611fb
  - .github/workflows/ci.yml 2de18948e0afc3ee4c14570649660a08aea316d5
  - conftest.py fb46496f6536760ece617b135afc7aef3c6eac93
  - tests/conftest.py 2d6b3d8b2f004425aa727bd4f80bb1322da0aa16
  - pyproject.toml 04e6750836aea68c8801d38f2b29e1b3f3ca4972
  - scripts/gen_message_types.py 06a0ab1456ab2e0655d70eab5e68f24a2eda9be9
  - docs/config.md cc3cbc8e7053729b82e02f8b5ace95dcd95811af
---
# decafclaw project facts

Stable facts for agent workers. `AGENTS.md` is a symlink to `CLAUDE.md`; read it in full. `ghflow facts` reports when a source above changes.

## Worktree setup
- Worktrees live at `.claude/worktrees/{branch}/`. Branch from `origin/main`. Never edit the main clone.
- `uv sync` (or `make install` for Python and JS). A doc-only worktree can skip it; say so in the report.
- `cp ../../../.env .env` from the main clone (it points at the main clone's `data/`), then add `HTTP_PORT=<port>` (default 18880) to avoid port conflicts with other worktrees.
- Run `make test` once for a baseline before code changes.

## Gates
- `make fmt` before every commit. Then `make check` = `install-js check-message-types fmt-check lint typecheck check-js check-browser-assets`, then `make test` = `uv run pytest tests/ contrib/skills/`.
- CI: required `lint-and-test` (`make check`, `playwright install chromium`, pytest) and `js-test` (`make test-js`); `check-tui` runs `make check-tui test-tui` outside `make check`.
- `make install-js` uses `npm ci`. Do not run `npm install` in `src/decafclaw/web/static/`; dependency changes go through `make vendor`.

## Generated files
- `src/decafclaw/web/message_types.json` generates `src/decafclaw/web/message_types.py`, `src/decafclaw/web/static/lib/message-types.js`, `docs/websocket-messages.md`, and `tui/src/types.generated.ts` via `make gen-message-types`. `make check-message-types` (inside `make check`) fails on a hand edit.
- `make gen-api-client` (inside `make check-js`) regenerates the committed OpenAPI client under `src/decafclaw/web/static/lib/api-client/`. `make vendor` rebuilds the committed vendor bundle.

## Test layout
- Root `conftest.py` holds the shared fixtures `config`, `ctx`, `tmp_data`, and autouse `mock_embed_text`. `tests/conftest.py` adds `stub_llm` and an autouse guard that fails any unstubbed LLM call. `tests/web/conftest.py` adds `http_config`, `app`, `client`.
- One file: `uv run pytest tests/test_x.py -n 0` (`-n 0` turns off xdist). Default addopts: `-n auto --dist=worksteal -m 'not integration'`; `testpaths = ["tests", "contrib"]`.
- Markers `integration` and `live_llm` call real APIs; `make test` excludes `integration`. Browser tests need Playwright chromium.

## Session notes
- `docs/dev-sessions/YYYY-MM-DD-HHMM-slug/` with `spec.md`, `plan.md`, `notes.md`. Session docs are committed.

## Never run
- `make run`, `make dev`, `make debug`, `make run-pro`: one bot instance per Mattermost account, and Les usually has `make dev` running.

## Commits and PRs
- Subjects mostly `type(scope): summary`, for example `fix(agent): ...`. Every change goes through a PR. `Closes #N` closes the issue on merge.

## Board
- Owner `lmorchard`, project `6`, field `Status`: Backlog, Ready, In progress, In review, Done. Priority P0/P1/P2; Size XS to XL.

Live state: `python3 "$GHFLOW_CLI" facts --issue ISSUE_URL`.
