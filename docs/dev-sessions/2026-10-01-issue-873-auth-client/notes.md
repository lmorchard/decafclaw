# Typed browser authentication calls

Issue: https://github.com/lmorchard/decafclaw/issues/873

Base: `a0a2f904fb105ac723bc239c9cca6e6343b07c03`.

The change covers login, logout, and the standalone vault authentication guard.
It retains manual backend request parsing and extends the existing generated
response-discard overloads. The main session check keeps its existing transport.
No authentication redesign or unrelated route migration is included.

The evidence plan pairs runtime tests with unchanged-caller contract mutations.
Login request and consumed-response mutations must fail at `auth-client.js`.
Logout and the vault guard consume no response fields, so their evidence uses
concrete generated contracts and runtime assertions instead of artificial reads.
The vault module also has a mutation that proves it participates in type checking.

Python, frontend, and Chromium prerequisites were installed before the baseline.
The baseline passed `make test` and `make test-js`. Dependency-installing gates
run separately from Python tests that share the frontend dependency directory.

Backend regressions cover response shapes, missing and null tokens, extra request
fields, session cookie attributes, and cookie deletion through the HTTP client.
Frontend regressions cover request methods and bodies, state, events, HTTP errors,
transport errors, and JSON parsing. Logout accepts every HTTP response without
reading its body. The vault guard redirects only on an HTTP error.

The Chromium regression executes generated login and logout against the test
server. It loads the standalone page, resolves its modules, and verifies decoded
page names and content loading. It also exercises a malformed successful `/me`
body. To test an expired session, it serves the actual shell after logout and
checks its guard against the real unauthenticated backend. The server protects
`/vault` itself, so a new unauthenticated navigation cannot run that shell.

A pre-existing title mismatch remains outside this issue. The shell observes
`.wiki-page-title`, but the current component renders no such element. The browser
regression records the unchanged default title and separately inserts a matching
element to exercise the existing observer. That synthetic assertion does not
prove automatic title updates from real page content.

The first generation run failed because the existing overload helper expected
multiline method signatures. Normalizing zero-argument signatures fixed the
new auth overloads. The first browser assertions also exposed the title mismatch
and the protected-shell setup described above. These failures were investigated;
they are not counted as successful evidence.

No credentials were copied and no live bot, deployment, or external service was
started. Browser tests use an isolated local test server. Live manual smoke tests
and independent review remain separate from these automated checks.

Final automated verification on 2026-10-01 passed `make check`, `make test-js`,
and `make test`. The full suites reported 296 frontend tests and 4032 Python
tests passed, with two Python skips. The focused backend/codegen run used
`--durations=25`; its slower cases invoke the real generator and type checker.
The final Chromium test completed without browser errors. No LLM evals ran
because this migration changes no LLM-visible behavior.
