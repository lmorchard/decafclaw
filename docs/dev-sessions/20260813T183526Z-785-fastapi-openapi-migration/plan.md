# Implementation plan

## Phase 0: Freeze
- [x] Write `checks.md` with C1..C2 and guards
- [x] Check C1 fails for the correct reason (`make: *** No rule to make target 'gen-api-client'. Stop.`)
- [x] Check C2 fails for the correct reason (`make: *** No rule to make target 'gen-api-client'. Stop.`)
- [x] Check guards pass
- [x] Adjudicate checks
- [x] Freeze commit + SHA recording

## Phase 1: Migrate to FastAPI
- **Advances:** C1 (partially, by installing FastAPI and exposing the OpenAPI schema)
- Update `pyproject.toml` to replace `starlette` with `fastapi`.
- Refactor `src/decafclaw/api/` to use FastAPI routers and response models.
- Run `make test` to ensure existing functionality remains intact.

## Phase 2: Generate OpenAPI Client
- **Advances:** C1
- Add `openapi-typescript` or `openapi-typescript-codegen` to `package.json`.
- Add a script in `scripts/gen_api_client.py` or bash to dump OpenAPI JSON from FastAPI and run the generator.
- Add `gen-api-client` target to `Makefile`.
- [ ] Run `make gen-api-client` and assert `.ts` file created.

## Phase 3: Update Frontend & Ensure Type Safety
- **Advances:** C2
- Refactor frontend `src/decafclaw/web/static/` to use the generated API client.
- Ensure all endpoints are fully typed.
- [ ] Apply a test patch changing a response field in a backend endpoint, run `make gen-api-client`, and assert that `make check-js` fails.
