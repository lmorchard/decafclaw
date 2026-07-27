# Vault write path cleanups — Issue #668

**Source:** https://github.com/lmorchard/decafclaw/issues/668

## Context

Three small items deferred from #672's final review. None affects correctness of the shipped feature; grouped because they all live in the vault write path.

## 1. Duplicated frontmatter validation

`http_server.py`'s `vault_write` re-implements what `frontmatter.parse_frontmatter_block` already does — `yaml.safe_load` plus an `isinstance(parsed, dict)` check — in a module that imports that very function. The duplication exists only to produce two distinct error messages (`invalid YAML: …` vs `frontmatter_raw must be a mapping`).

Two places now decide "is this valid frontmatter" and can drift. Either widen `parse_frontmatter_block` to return a discriminated error the caller can turn into a message, or reuse it and accept a single message.

## 2. `vault_write` has grown

It now handles four payload shapes (`rename_to`, body-only, `frontmatter` patch, `frontmatter_raw` replace) across ~140 lines with six mid-function `return JSONResponse(...)` exits. Still followable, but the frontmatter-resolution block is a self-contained `(new_raw, error_response)` unit waiting to be extracted — which would also let it be unit-tested without going through a client.

## 3. Narrow keystroke loss in the raw-YAML editor

Typing into the raw-YAML textarea *while a raw save you already triggered is in flight*: on success the host calls `closeRaw()` and `willUpdate` reseeds `_rawText` from the server response, discarding keystrokes entered during the request.

Same class as the navigation misdelivery fixed in #672, but a much smaller window and it loses only in-flight typing rather than misdelivering it. Fix is probably to skip the reseed if the textarea changed since the request was issued.

## Provenance

Deferred from #672's final whole-branch review (Minors 5, 13, 14). Explicitly out of scope there.

---

## Acceptance criteria (agent-session intake)

Scoped to **items 1 and 3**. Item 2 was split out to #718 — see Scope below.

**C1 — `vault_write` no longer re-implements frontmatter validation.**
WHEN the vault write path validates a `frontmatter_raw` payload, it SHALL do so through
`frontmatter.parse_frontmatter_block`, the function it already imports, and SHALL NOT parse the
YAML itself.
- CHECK: `grep -c "yaml\.safe_load" src/decafclaw/http_server.py` returns `0`.
- Verified discriminating at intake: returns **1** today (`src/decafclaw/http_server.py:1457`,
  inside the `frontmatter_raw` branch of `vault_write`).

**C2 — keystrokes typed while a raw-YAML save is in flight survive the save.**
GIVEN the raw-YAML editor is open and a raw save has been dispatched, WHEN the user types more
into the textarea before that request resolves and the save then succeeds, THEN the text the user
typed SHALL NOT be discarded by the post-save reseed.
- CHECK: a vitest case in `src/decafclaw/web/static/components/wiki-metadata.test.js` that
  (a) sets `frontmatterRaw`, (b) opens the raw editor, (c) mutates `_rawText` to text containing
  a token the server response does not, (d) calls `closeRaw()` and then assigns the server's
  `frontmatterRaw`, and (e) asserts that token is **still present** in `_rawText`.
  Run with `npm test` in `src/decafclaw/web/static`.
- Verified discriminating at intake with a throwaway reproduction of exactly that sequence:
  `AssertionError: expected 'a: 2' to contain 'b: 3'`. The reseed in
  `wiki-metadata.js:58-63` fires because `closeRaw()` has already set `_rawOpen = false`
  (`wiki-metadata.js:126-129`), so the `!this._rawOpen` guard no longer holds. The throwaway was
  deleted; it is not the acceptance test.

### Regression guards
(Pass today; must keep passing. Not criteria — they cannot fail at freeze, so they grade nothing
new and do not affect the tier.)

- **G1:** the two existing rejection tests still pass —
  `uv run pytest tests/test_vault_api.py::test_vault_write_frontmatter_raw_malformed_is_rejected tests/test_vault_api.py::test_vault_write_frontmatter_raw_non_mapping_is_rejected`.
  **Confirmed passing at intake** (`2 passed`). This is the guard that makes C1 non-gameable: the
  cheapest way to drive C1's grep to zero is to delete the validation outright, and that turns
  both of these from 400 into 200.
- **G2:** `make test` — no test lost, newly skipped, or newly failing. Stated as an invariant, not
  a pinned count, because upstream legitimately moves the totals.
- **G3:** `npm test` in `src/decafclaw/web/static` green, including the existing
  `components/wiki-editor.test.js`.

## Tier: `auto-ok`

Trigger 1 does not fire: both criteria reduce to concrete commands, both were demonstrated
failing, both oracles exist now (`grep`; and the vitest + jsdom harness, with
`components/wiki-editor.test.js` as the sibling precedent), and neither is satisfiable without
the work — C1 is backstopped by G1, and C2 names the specific assertion rather than the existence
of a test.

Trigger 2 does not fire: the touched paths are one HTTP handler's validation branch and one
front-end component. No authentication/authorization logic, no secrets, no data migration or
deletion, no deploy/infra/CI config, no dependency changes.

## Design decisions

- **Item 1 resolves by reusing `parse_frontmatter_block` and accepting its single error message**,
  rather than widening it to return a discriminated error the caller re-formats.
  - *Why:* the duplication exists only to produce two distinct message strings, and **no test
    asserts either of them** (verified: no match for `invalid YAML` or
    `frontmatter_raw must be a mapping` anywhere under `tests/`). The two rejection tests assert
    status 400, that `error` is truthy, and that the non-mapping case's message contains
    `"mapping"` — which `parse_frontmatter_block`'s own `"frontmatter is not a mapping"`
    satisfies. So the message split is costing a second source of truth for something nothing
    depends on.
  - *Rejected:* widening `parse_frontmatter_block` to return a tagged error. It preserves both
    messages exactly, but grows a shared function's API to serve one caller's message formatting.
  - *Consequence, stated plainly:* the `invalid YAML: …` prefix disappears from the API response
    for malformed YAML. Nothing checks it, but it is a user-visible string change.
- **Because the choice above changes no criterion, it did not affect the tier.** Either
  resolution satisfies C1's grep and both guards, so this is implementation style, not a withheld
  decision — `acceptance-criteria.md`'s trigger 1 turns on whether the choice changes *which
  criteria apply*, and here it does not.
- **Item 3's fix is left open deliberately.** C2 asserts the behaviour, not the mechanism. The
  issue suggests skipping the reseed when the textarea changed since the request was issued;
  reseeding only from a save this component initiated would work too. C2 grades either.

## Scope

- **In:** item 1 (duplicated frontmatter validation) and item 3 (in-flight keystroke loss).
- **Out:** item 2 (`vault_write` has grown) → split to **#718**. Its honest criterion is
  "a unit test for the extracted helper exists and asserts the resolution behaviour", which is
  the test-coverage hard case — the deliverable is the oracle, so the implementer would author
  the thing that grades it. It needs a human to name the assertions or read the result, i.e.
  `needs-review`, and it does not belong in an issue that is otherwise cleanly `auto-ok`.

## Corrections to the observation above

- Item 2's numbers were wrong, which is why #718 restates them: `vault_write` is
  `http_server.py:1353-1518` — **166 lines with 16 `return JSONResponse(...)` statements**, not
  "~140 lines with six".

---
*Acceptance criteria + tier added via `agent-session intake`. Original issue text preserved verbatim above.*
