# Conversation lifecycle client migration

Implementation record for [issue #871](https://github.com/lmorchard/decafclaw/issues/871),
2026-10-01. Base: `ef8056cef854d66dc6ddcfceaabd34cdd8c84637`.
Implementation dispatch model: `gpt-6-astra`.

The change exposes creation inputs and its HTTP 201 metadata response, plus the
archive, restore, and delete identifiers and acknowledgements. Existing store
methods use these generated contracts. The schema describes creation inputs
without replacing legacy JSON parsing, coercions, defaults, or the `effort` fallback.
The store keeps its selection, message, listing, and failure behavior. Unused
acknowledgement bodies use the existing generated discard overloads.

The evidence plan covers actual store requests and effects, backend compatibility,
unchanged caller diagnostics after deliberate contract mutations, and Chromium
requests against the isolated backend. Schema, client types, and browser output
are regenerated together. API documentation is in `docs/web-ui.md`.

Python, frontend, and Chromium prerequisites were installed before the baseline.
No credentials were copied. Baseline `make test` passed with 3987 passed and two
skipped. Baseline `make test-js` passed with 252 tests. Baseline `make check` passed.
The new schema regression first failed with the expected missing `requestBody`.

Final `make check` passed. Final `make test-js` passed with 282 tests.
Final `make test` passed with 4023 passed and two skipped. npm-installing gates ran
serially before Python tests so the code-generation fixtures retained stable dependencies.
A targeted duration run of the new lifecycle tests passed with 36 tests.
Its slowest tests were deliberate generation and TypeScript compilation runs
(about 3.4 seconds), with no fixed sleeps or missing service mocks.

Seven mutation cases independently change the creation title/model/folder inputs,
the consumed response title, and each lifecycle identifier. Each requires a passing
baseline and the expected diagnostic at the unchanged production caller.
The browser regression exercises the actual store through the emitted client.
Route tests cover authentication, ownership, legacy coercions, storage retention,
assignment cleanup, and terminal shutdown before file deletion.

Self-review covered the full base-to-worktree diff and generated signatures.
Independent review and post-merge manual smoke checks are separate evidence.
LLM evals do not apply because this migration changes no LLM-visible behavior.
