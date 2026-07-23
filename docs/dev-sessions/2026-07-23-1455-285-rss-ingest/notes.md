# Notes — rss-ingest contrib skill (#285)

## Outcome

New `contrib/skills/rss-ingest/` skill, mirroring `mastodon-ingest`:
`SKILL.md` + `SCHEDULE.md` (prose contract), `fetch.sh` (wrapper +
`list`/`add`/`remove`), `fetch_feeds.py` (logic + feedparser adapter + CLI),
`test_fetch_feeds.py` + `fixtures/`. Delivered in 5 TDD commits.

## Key decisions (brainstorm)

- **Fetch mechanism:** `feedparser` (not hand-rolled stdlib parsing, not a Go
  binary in a separate repo).
- **Dependency delivery:** PEP 723 inline script metadata + `uv run
  --no-project` — isolated, cached, no `setup.sh`, no vendoring, no coupling to
  core deps. (Les redirected from an initial "setup.sh venv" idea to uv.)
- **Feeds config:** `workspace/skill-state/rss-ingest/feeds.txt`, managed via
  `fetch.sh add/list/remove` so no broad `workspace_write` grant is needed.
- **Output:** flat `agent/pages/rss/` (garden promotes clusters). No
  per-item `delegate_task` in v1 (YAGNI).
- **No eval** for a contrib skill (per convention).

## Deviations from the plan (found during execution)

1. **importorskip placement.** The plan put `feedparser = pytest.importorskip(...)`
   at module level. That skips the ENTIRE test file when feedparser is absent
   (project env) — which would hide the pure-logic tests from `make test`.
   Fixed: per-test `pytest.importorskip("feedparser")` inside the two adapter
   tests only. Pure tests run in `make test`; adapter tests skip there and run
   under `uv run --with feedparser python -m pytest`.
2. **Test invocation.** `uv run pytest` uses the project venv (no feedparser);
   the `pytest` console-script shebang bypasses `uv run --with`. Correct
   incantation for the adapter tests: `uv run --with feedparser python -m
   pytest ...`. Documented in SKILL/README.
3. **clean_summary added.** Raw feed summaries are HTML; the spec called for a
   "cleaned excerpt." Added a pure, stdlib-only `clean_summary()` (HTMLParser
   strip + unescape + whitespace-collapse + 500-char truncate), applied in the
   adapter, with its own unit test.

## Verification

- `make test`: 3105 passed, 2 skipped (our adapter tests correctly skip in the
  feedparser-absent project env). Pure logic (9 tests) runs there.
- `uv run --with feedparser python -m pytest contrib/skills/rss-ingest/`: 11
  passed (adapter included).
- `make lint` clean; `ruff check contrib/skills/rss-ingest/` clean.
- Live smoke against `simonwillison.net/atom/everything/`: markdown on first
  run, `(no new items)` on the immediate second run (incremental state
  advances); `add`/`list`/`remove` work and `add` is idempotent.

## Out-of-scope finding (candidate follow-up issue)

`make test` now emits **2 `DeprecationWarning: forkpty() may lead to deadlocks`**
from `pty.py` — originating from a pty-spawning test (the #626 terminal
integration test on the base commit), **not** from this work. It does not fail
CI (only `PytestUnraisableExceptionWarning` is promoted to error). Worth a
small follow-up issue like #632 was for the subprocess-transport leak.

## Notes for reviewers

- `pyright`/`make typecheck` do not scan `contrib/` (include = `src/decafclaw`),
  so the lazy `import feedparser` shows an IDE-only "unresolved import" that is
  not a gate. Intentional — feedparser is only present under `uv run`.
- `make lint` only lints `src/ tests/`; the skill was linted explicitly with
  `ruff check contrib/skills/rss-ingest/`.
