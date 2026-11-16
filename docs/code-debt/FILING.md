# Filing instructions (for the filing worker)

This file tells a smaller/cheaper model how to turn `filing-manifest.json` into
real GitHub issues. The manifest is the machine-readable source; file from it, in
list order, and read back every write.

## Inputs

- `filing-manifest.json` — the issue list. Fields:
  - `id` (`E.N` or `E.Na`), `gh_title` (use verbatim as the issue title), `body`
    (verbatim markdown body), `size` (XS/S/M/L/XL), `priority` (P0/P1/P2),
    `triage` (`ready`/`blocked`), `blocked_by` (list of ids), `area` (optional),
    `parent` (the grouping when `id` is a subdivided slice), and `impl_model`
    (`local`/`strong` — label it `impl:local`/`impl:strong`).
- Repo `lmorchard/decafclaw`. Board owner `lmorchard`, project `6`.

## Labels (ensure they exist first)

- `triage:ready` — apply to every `triage == "ready"` issue.
- `triage:blocked` — apply to every `triage == "blocked"` issue.
- `impl:local` — apply to every `impl_model == "local"` issue (small/quantized-
  model-appropriate).
- `impl:strong` — apply to every `impl_model == "strong"` issue (frontier or
  larger non-quantized model).
- `area:<x>` — OPTIONAL; skip unless Les says to use area labels (`area` field is
  provisional).

Each issue gets exactly two labels: one `triage:*` and one `impl:*`.

If `gh issue create --label` fails because a label is missing, create it with a
short description and retry.

## Procedure (file in list order; the list is already dependency-safe)

1. Maintain a mapping `id -> issue number`.
2. For each issue in order:
   - Write `body` to a UTF-8 temp file.
   - `gh issue create --repo lmorchard/decafclaw --title "<gh_title>" --body-file <tmp> --label <triage label>,<impl label>`.
     The `<impl label>` is `impl:local` or `impl:strong` per `impl_model`.
   - Record the returned number against `id`. Log `id -> URL/NUMBER` immediately.
   - **Board fields** (priority and size): set `Priority = priority` and
     `Size = size` on the project card. Leave `Status` unset (curation sets it).
   - **Blocked-by**: if `blocked_by` is non-empty, map each `E.N` to its real
     number (already filed, since the list is dependency-safe) and set the native
     relationship: `gh issue edit <number> --add-blocked-by <prereq-number>`.
3. Read back every created issue (title, body, labels, board fields) and every
   blocked-by edit. Report any write that does not confirm.

## Notes

- The `body` text still contains `**Triage:** blocked-by #E.N` and `**Deps.**
  blocked-by #E.N` lines with *placeholder* ids. That is fine to leave as-is;
  it is advisory. The authoritative dependency is the native blocked-by
  relationship you set in step 2.
- Priorities and area labels are PROVISIONAL. Do not re-derive them; file as
  given, and report the totals so Les can bulk-adjust afterward.
- If a filing request is interrupted, resume by looking up which `id`s already
  have issue numbers (check recent issues / the mapping log) and continue from
  the next unfiled `id`.
- 131 issues is a lot of writes; file in small batches and verify each batch
  before the next. Do not parallelize `gh issue create` writes unboundedly.