# Notes — #673 unconstrained `vault_search`

Run mode: `agent-session express`, unattended (board-driver). Tier `auto-ok`.

## What shipped

An unconstrained `vault_search` — empty `query` and none of `tags` / `folder` / `source_type` /
`days` — now returns an `[error: …]` `ToolResult` naming `vault_list`, instead of returning the
whole vault under a `Found N result(s)` heading. Three commits of substance plus the freeze.

- `48aab5e` — freeze (acceptance test + guard edits + manifest). Tamper-diff baseline.
- `8f4a144` — record the freeze sha (a commit can't contain its own hash).
- Phase 1 — the guard in `tool_vault_search`, the `query` schema description, `docs/vault.md`.
- Phase 2 — a `tool_choice` eval case for `vault_list` vs `vault_search`.

## The two findings that actually changed the outcome

Both came from the **check-reviewer** — a read-only context given the manifest and the repo but
not the plan or the criteria's rationale — run before the freeze commit. Neither was visible from
inside the implementing context, and both would have produced a green run that didn't do the work.

1. **`result.data` is model-visible.** The criterion says "SHALL NOT list any page path," and the
   three named assertions all read `result.text`. But `execute_single_tool` appends
   `json.dumps(result.data)` to the tool message as a fenced JSON block, and the old dump carried
   the page list in `data["results"]` too. Swapping the text for a refusal while leaving `data`
   attached would have greened every assertion and still shipped every page path to the model.
   The check now asserts over the serialized `data` as well; the implementation returns no `data`.

2. **C1's boundary had no fence on its non-tag side.** The suite contained *zero* empty-query cases
   using `folder`, `days` or `source_type` — every empty-query call passed `tags` or nothing. So
   `if not query and not req_tags: refuse` would have passed C1, G3 *and* G4 while breaking exactly
   the constrained searches G3 exists to protect. Three boundary cases were added at freeze.

A third catch is worth remembering as a pattern: **G1 would have gone toothless the moment C1
landed.** Its two calls are both fully unconstrained, i.e. inside C1's refusal domain, and C1(c)
forces the refusal ahead of the `if not query and req_tags:` branch — so post-C1 its
`assert_not_called()` would be satisfied by the refusal rather than by the conjunct it exists to
guard. Mutating that line to `if not query:` would have left it green. Fixed by adding one
constrained-but-tagless call inside the same patch block. This is the general shape: *a new
short-circuit can defang an older guard downstream of it without touching the guard's source.*

## Deliberately not done

- **`source_type` is ignored on the entire substring path.** `_substring_search` (`tools.py:877`)
  takes no `source_type` parameter and its only call site never passes one, so
  `vault_search(ctx, "", source_type="page")` returns the whole vault today. The semantic branch and
  `_tag_filter_search` both honor it. Pre-existing, broader than #673, **not fixed here** — and
  G3's `source_type` case is deliberately narrowed to "is not refused" so a later fix can't be
  smuggled into a boundary guard. Worth its own issue.
- Cap-and-label: closed by Les's recorded decision, not merely unchosen.
- No cleanup of `tool_vault_search`'s surroundings (stray double blank line, function-level
  `from datetime import timedelta` imports).

## Environment gotchas hit during the run

- **Bash cwd is not stable between calls in this harness.** A `git status` that had been running in
  the worktree silently began running in the main clone mid-session. Everything after that used
  absolute paths (`git -C`, `uv run --directory`, `make -C`). Worth doing from the start.
- Unattended `dontAsk` denies compound `cd`, shell redirection (`>`), `cp`, and bare `python`.
  `uv run --directory` / `make -C` / the Write tool cover all of it.
- `make install` in the worktree rewrote `src/decafclaw/web/static/package-lock.json` (npm pruned
  optional deps). Caught in the diffstat before the freeze and reverted; it would otherwise have
  ridden along in the PR.

## What the branch self-review caught (after the checks were already green)

Two real defects in the fix itself, neither visible to any frozen check — a useful reminder that a
clean verifier report grades the criterion, not the change.

1. **`not query` let `query=" "` through.** A single space is a substring of nearly every markdown
   file, so `vault_search(query=" ")` returned the whole vault under `Found N result(s)` — the #673
   defect exactly, one character from the refused call. Now `not query.strip()`.
2. **The refusal message recommended `source_type`** — the one axis that narrows nothing on the
   default substring strategy. The error string was handing the model a one-token route back to a
   dump. The condition still accepts `source_type` (C1's boundary requires it); the message no
   longer suggests it.

Also fixed: the function docstring never mentioned the refusal, and the always-loaded vault
`SKILL.md` pushed toward `vault_search` without naming `vault_list` as the enumeration tool — the
steer had been living only in the `query` parameter description.

## Verification state

`make check` green. `make test` 3738 passed / 2 skipped (freeze baseline: 3737 passed + 1 failing
C1, 2 skipped). Tamper diff against `48aab5e` empty. `make eval-tools` 29/33 with the new
`vault_list ↔ vault_search` case passing; the 4 failures are pre-existing and non-vault (the two
known-deterministic tabstack cases plus two from the run-to-run unstable set) — `make eval-tools`
exits nonzero on `origin/main` too and is not one of this run's frozen guards.
