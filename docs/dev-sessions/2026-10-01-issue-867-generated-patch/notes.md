# Conversation PATCH client, 2026-10-01

Issue: https://github.com/lmorchard/decafclaw/issues/867

Base: `29b92e7d8156cef4788724b8eec109d736f4cb88` from current `origin/main`.
Branch: `issue-867-patch-client`. Implementation model: `gpt-6-astra`.
The shared checkout and existing worktrees were preserved. No credentials or live
data were copied. The issue moved from Ready to In progress on project 6.
Parent #843 remained Ready/P1 and open.

## Plan and evidence

Expose the existing PATCH path and body contracts without replacing the handler's
parsing and validation. Preserve response key presence with an optional `folder`
key and no default. Migrate both store calls through generated transport. Keep the
rename result typed through the listing merge. Give move a generated overload
that skips response decoding and returns `void`.

Authenticated HTTP tests cover persistence, ownership, error status, conditional
response keys, null and omitted fields, coercion, folder trimming, invalid
folder validation order, and supported identifier decoding. JS tests call the
real store through the generated transport and cover state, events, request
content, and separate HTTP, network, and decoding failures. The Chromium test
checks session cookies and PATCH path/body values against isolated routes.

Four backend mutation tests independently change the identifier, request title,
request folder, and returned title. Each regenerates from a passing baseline,
requires exact diagnostics at unchanged caller lines, and compares caller bytes.
Identifier and request-title errors also produce a dependent spread diagnostic,
which the tests require alongside the direct argument diagnostic.

## Baseline and workflow findings

Installed the declared Python packages, frontend packages, generated client, and
Chromium before baseline testing. `make check` and `make test-js` passed.
The initial `make test` run had four codegen failures because parallel check/JS
targets ran `npm ci` while the Python tests shared those dependencies. The failures
reported missing TypeScript or Leaflet, not product failures. After installation
finished, all original `tests/test_api_codegen.py` tests passed before implementation.
Run targets that install frontend dependencies serially with Python codegen tests.

Before implementation, the new HTTP behavior cases passed and the new OpenAPI
contract test failed because PATCH had no path parameters. New JS migration tests
failed because the generated PATCH method did not yet exist. This distinguishes
the missing contract from preserved runtime behavior.

## Verification

Use `make check`, `make test-js`, and `make test` in sequence. The targeted run
`uv run pytest tests/test_web_conversations.py tests/test_api_codegen.py -n 0 -q --durations=25`
also exercises the browser. Its duration report shows the mutation checks spend
time in real generation/type-check subprocesses, without added sleeps or live calls.
Evals do not apply because this change does not affect LLM-visible behavior.
Observed on 2026-10-01: `make check` passed, `make test-js` passed 222 tests,
and `make test` passed 3954 tests with 2 skips. The full suite includes the
session, sticky, listing, browser, reconnect, and four PATCH mutation regressions.
After the final standard-library type import change, `make check` passed again
and the targeted backend/codegen/browser command passed 109 tests. No source
changes followed those checks. Independent review and publishing remain separate steps.
