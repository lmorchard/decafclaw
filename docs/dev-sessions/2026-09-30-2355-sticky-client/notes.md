# Typed sticky lookup — issue #863

Issue: <https://github.com/lmorchard/decafclaw/issues/863>
Base: `9909cf1997c7ba8fd0c31e86cbc627c4cd5c1aeb` (`origin/main`).
Implementation model: unknown; no reliable runtime model metadata was available.

## Plan and scope

1. Expose a string conversation identifier and a required, nullable response
   envelope on the existing sticky GET route. Preserve authentication, ownership,
   safe-identifier validation, and heterogeneous widget data.
2. Move `setActiveConv()` to the generated method without erasing its argument or
   response types. Preserve its cache, publication, and warning behavior.
3. Test the real generated request, HTTP route, isolated incompatible contract
   changes at the unchanged caller, and authenticated browser loading. Run the
   existing build and regression gates before committing.

No widget schema, other route, session migration, or WebSocket protocol changes.
The board transition from Ready to In progress was read back successfully.

## Implementation decisions

The legacy authentication decorator exposes only `Request` to FastAPI. This
route now resolves authentication directly, retaining its existing JSON 401,
400, and 404 responses. `StickyResponse` validates only the envelope; its object
payload retains unknown properties and nested values.

The generated fetch transport uses `encodeURI` and swallows JSON decoding
errors after logging them. Those defaults differ from the existing sticky
caller. The generation step therefore preserves the stock transport as
`generated-request.ts` and installs a small operation-specific adapter from
`scripts/sticky_api_request.ts`. Only the sticky GET uses the adapter's encoding,
same-origin credentials, and JSON error propagation. All other operations
use the stock transport without global configuration changes.

## Evidence (2026-09-30)

- The initial `make test` attempt had four code-generation setup failures because
  frontend dependencies were absent. After `make install-js` and
  `uv run playwright install chromium`, baseline `make test` passed:
  3899 passed, 2 skipped.
- Before implementation, `npx vitest run lib/sticky-state.test.js` passed ten
  existing-behavior cases and failed the generated-method case because the
  required method did not exist. After implementation, all eleven passed.
- `uv run pytest tests/test_http_sticky.py tests/test_sticky.py
  tests/test_api_codegen.py --durations=25`: 22 passed. Both isolated mutations
  first pass generation/typechecking, then require exactly the expected compiler
  diagnostic at the unchanged sticky caller: TS2345 for an integer identifier,
  TS2339 for a renamed response property. The test inspects diagnostics rather
  than treating any nonzero exit as proof.
- The browser test loads a clean-built client and restores nested widget data
  through an authenticated test server using a safe identifier. A separate
  frontend test checks path-segment encoding without broadening accepted IDs.
- `make check`: passed, including lint, Python/frontend typing, generation, and
  browser asset checks. Pyright reported zero errors and zero warnings.
- `make test-js`: 22 files and 193 tests passed.
- Final `make test`: 3904 passed, 2 skipped (84.86 seconds).
- `git diff --check` passed. The preserved generated transport matched the
  base revision byte for byte (`cmp`).

The initial focused Python command was accidentally run from the frontend
subdirectory and collected no tests; the root-level focused command above is
its valid replacement. Tests use temporary data; no live credentials were
copied and no bot services were started. Evals are not applicable because the
change is not LLM-visible. Self-review is not independent review.
