# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/785
**Frozen at:** 8381ad48e4936b060d697804fc65b47ddb9901a4
**Check files — read-only from Phase 1 onward:**
(Every criterion is a command rather than a test; this list is empty.)

## C1
CRITERION: WHEN the `make gen-api-client` command is run, the system SHALL install FastAPI, read the backend API schema, and generate a TypeScript client file using an OpenAPI-to-TypeScript generator.
CHECK: Run `make gen-api-client` and assert the output `.ts` file is created/updated.
AT FREEZE: fails — `make: *** No rule to make target 'gen-api-client'. Stop.`

## C2
CRITERION: WHEN the backend API schema changes but the frontend is not updated, the frontend build SHALL fail.
CHECK: Apply a test patch changing a response field in a backend endpoint, run `make gen-api-client`, and assert that `make check-js` fails.
AT FREEZE: fails — `make: *** No rule to make target 'gen-api-client'. Stop.`

## Guards
- G1: `make test` passes.
- G2: `make check` passes.

## Adjudication
- C1: accepted — the command does not exist, so it fails for the correct reason. No cheaper fix exists.
- C2: accepted — the command does not exist, so it fails for the correct reason.
- G1: accepted — the test suite passes today and must continue to pass.
- G2: accepted — the check suite passes today and must continue to pass.

## Amendments
