**Concept from opencode:**
`opencode` declares its entire API using an abstract HTTP framework and uses a custom code generator (`packages/httpapi-codegen`) to parse it into an "SDK Contract IR" (Intermediate Representation). It then emits a 100% type-safe frontend client. Backend schema changes immediately trigger frontend build failures.

**How `decafclaw` could implement this:**
`decafclaw` currently uses Starlette (backend) and Lit (frontend), relying on loosely typed `fetch` calls. While WebSocket messages are generated (`make gen-message-types`), REST endpoints lack end-to-end type safety.

**Proposed Implementation:**
- Adopt a Python-to-TypeScript bridge. Migrating to **FastAPI** (or exporting an OpenAPI spec from Starlette using a library) allows native OpenAPI JSON schema generation.
- Use a tool like `openapi-typescript-codegen` tied to a `make gen-api-client` command.
- Update Lit components to use the generated client, providing auto-complete and strict type-safety for every REST endpoint, eliminating API drift.

---

## Acceptance Criteria

- **CRITERION:** WHEN the `make gen-api-client` command is run, the system SHALL install FastAPI, read the backend API schema, and generate a TypeScript client file using an OpenAPI-to-TypeScript generator.
  - **CHECK:** Run `make gen-api-client` and assert the output `.ts` file is created/updated.
- **CRITERION:** WHEN the backend API schema changes but the frontend is not updated, the frontend build SHALL fail.
  - **CHECK:** Apply a test patch changing a response field in a backend endpoint, run `make gen-api-client`, and assert that `make check-js` fails.

## Regression guards

- **GUARD:** Existing REST and WebSocket endpoints remain functional.
  - **CHECK:** `make test` passes.
- **GUARD:** The codebase remains clean and typed.
  - **CHECK:** `make check` passes.

## Design decisions

- **Migrate to FastAPI:** We are migrating to FastAPI to natively emit OpenAPI JSON schemas, rather than exporting from Starlette. This decision was ratified by the user in the triage pass. We will install FastAPI and use an OpenAPI-to-TypeScript generator.

## Tier: auto-ok
**Reason:** The migration to FastAPI and use of an OpenAPI-to-TypeScript generator has been explicitly approved by the human reviewer. Verifiable criteria and checks are established.
