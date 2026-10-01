# Evidence

Implementation model: gpt-6-astra. No credentials or live services used.
Dependencies installed with `uv sync` and `make install-js` before baseline.
Baseline `make test`: 4045 passed, 2 skipped.
Initial component tests caught read-all attempting to access an absent path argument.
The transport now encodes an identifier only when a path argument exists.
The focused component suite passes, including HTTP errors and rejected requests for both actions.
Six generated-contract mutations pass their expected failure checks.
Board edit requested In progress. Initial readback encountered the GitHub GraphQL rate limit.
After recovery, a targeted readback confirmed In progress and P1.

`make check` passes, including generation, lint, Python/JavaScript type checks, and browser asset checks.
`make test-js` passes: 327 tests across 27 files, including 14 inbox caller tests.
The first full changed Python run passed 4050 tests but failed a browser path assertion.
Starlette's URL representation reparsed a decoded question mark in the record identifier.
The route received the correct identifier. The assertion now reads the ASGI request path.
The focused clean-build Chromium test passes and verifies list/count, encoded read navigation,
mark-all, cookies, empty request bodies, and the list limit at the real routes.

No live application or provider tests ran. No push, PR, deployment, or cleanup occurred.
Independent review remains the next phase. Self-review checked the full diff and generated models.


Final full regression command: `uv run pytest tests/ contrib/skills/ --durations=25`.
Result: 4051 passed, 2 skipped. Existing build/session/sticky/conversation regressions pass.
The new mutation tests in the top 25 each run real generation and TypeScript checking twice.
Their runtime is compilation work, not a fixed sleep or an unmocked service.
`git diff --check` passes. No lockfile or dependency changes.
