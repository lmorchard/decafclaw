# Notes: vault frontmatter rendering + editing

Session working log. Final summary goes here before the PR.

## Baseline

Worktree `.claude/worktrees/vault-frontmatter-ui`, branch `feat/vault-frontmatter-ui`,
off `origin/main` at `60079d5`. `HTTP_PORT=18897`.
`make test` before any changes: **3234 passed, 2 skipped** in 86.58s. The two
warnings are pre-existing `forkpty` deprecations in the terminal tests.

## Task log

- Task 1 — body-only writes preserve frontmatter verbatim: done
- Task 2 — relocate `merge_frontmatter` to `frontmatter.py`: done
- Task 3 — `vault_read` splits frontmatter/body: done
- Task 4 — PUT `frontmatter` patch: done
- Task 5 — PUT `frontmatter_raw` replace: done
- Task 6 — sidebar summaries: done
- Task 7 — `<wiki-metadata>` read-only + `wiki-page` on `body`: done
- Task 8 — metadata editing: done
- Task 9 — docs + wrap-up: done

## Open question to answer during Task 7

Does `wiki-editor.js:109`'s `md !== prev` listener fire on initial load? If it
does, merely *opening* a page in edit mode corrupted its frontmatter, not just
editing one. Answer belongs in the PR description either way.

**Answer:** No. Opening a page in edit mode and touching nothing did not
corrupt the file. Verified empirically against the isolated `/tmp/fm-smoke`
fixture (`agent/pages/FmSmoke`): opened the page in edit mode, waited 3.5s,
and the status indicator stayed idle (no "Saving..." ever appeared) — the
file was byte-identical to the pristine copy afterward. By contrast, typing a
single character into the body did trigger autosave ~1s later ("Saved"
status), and a byte-compare of just the `---`-delimited frontmatter block
(MD5) confirmed it was untouched — the body diff showed only the intended
edit plus an incidental Milkdown list-marker normalization (`*   ` → `* `,
pre-existing WYSIWYG round-trip behavior unrelated to this branch). So the
`markdownUpdated` listener's initial `defaultValueCtx` load does not count as
a change relative to itself (`md === prev` on first render) — the bug this
branch fixes was real editing, not merely opening a page.

## Task 7 details

Implemented `<wiki-metadata>` (compact strip, expandable to full detail incl.
unknown keys) and rewired `wiki-page.js` onto `body`/`frontmatter*` per the
brief, plus the `_loaded`-flag guard so an empty body with frontmatter still
renders.

**Deviation from the brief:** Step 6 only names `wiki-editor.js:246-247`, but
`#reload()` has three more raw `data.content` reads just below those two
lines (`replaceAll(data.content)`, `#lastSavedContent`, `#currentMarkdown`).
`#reload`'s fetch is hardcoded to `/api/vault/...` regardless of
`saveEndpoint`, so on the vault page those would all evaluate to `undefined`
once the endpoint stopped returning `content`, corrupting the reload/conflict
path. Fixed by computing `data.body ?? data.content ?? ''` once into a local
`newContent` and using it for all four assignments, keeping the `?? data.content`
fallback (and its rationale) for wiki-editor's other hosts.

**Browser verification** (isolated `DATA_HOME=/tmp/fm-smoke`, fixture
`agent/pages/FmSmoke`, restored afterward):
- View mode: no `<hr>`, no YAML list, summary + tag chips in the strip, body
  starts at the `# 0din` heading. Expanded strip showed all four known
  fields (summary, importance, tags, keywords) — no unknown keys in this
  fixture.
- Edit mode: Milkdown showed only the body, metadata strip rendered above it.
- Corruption check: typed a character into the body, waited ~2.5s for
  autosave ("Saved" status appeared). Byte-compared (MD5) the `---`-delimited
  frontmatter block against the pristine copy — identical. Body diff showed
  only the intended edit plus an incidental Milkdown list-marker
  normalization, unrelated to this branch. Fixture restored via `cp` from
  the pristine copy.
- See the "Open question" section above for the initial-load check.

`make check` clean (ruff, pyright, `tsc --checkJs`, message-types drift
check). `make test`: 3276 passed, 2 skipped (up from the 3234 baseline —
Tasks 1–6 added coverage; no regressions).

## Decisions (from brainstorm)

- Frontmatter is chrome, not content — split server-side, mirroring the
  existing `/api/schedules/*` pattern. Milkdown never sees the YAML.
- Server-side split, not client-side: no `js-yaml` dependency, and the server
  stays the single YAML authority.
- Body writes splice the raw block back **verbatim**, never through
  `yaml.dump`. `parse_frontmatter` reports `{}` for malformed YAML, so
  reserializing would delete it; `yaml.dump` also sorts keys and drops
  comments, churning formatting on every body edit.
- Raw editor holds the **whole** frontmatter with replace semantics — one rule
  instead of partitioning known vs unknown keys and erroring on collisions.
- Typed controls patch; raw replaces; mutually exclusive on the wire.
- Read-only compact strip in view mode, expandable.
- Tag chips inert this session — #318 owns tag query semantics.

## Task 8 details

Implemented the brief's edit controls verbatim in `wiki-metadata.js`
(`willUpdate` reseed guard, `#emitChange`/`#emitList`/`#removeTag`/
`#addTagKey`, raw-editor toggle/save/setRawError/closeRaw, `#renderChipInput`,
`#renderEditControls`), the CSS block in `wiki-metadata.css`, and
`wiki-page.js`'s PUT ownership (`#metaTimer`/`#pendingFields`/`#metaInFlight`,
`_onMetadataChange`, `#flushMetadata`, `_onMetadataRawSave`, `#putMetadata`),
plus the flush calls added to `willUpdate` (`void`, unawaited — matches the
existing unawaited `editor.flushSave()` there) and `_toggleMode` (awaited).
Step 3c reframed the `#reload()` fallback comment in `wiki-editor.js` per the
brief's exact text — no behavior change, `?? data.content` expression kept.

`make check` clean (ruff, pyright, `tsc --checkJs`, message-types drift
check). `make test`: 3276 passed, 2 skipped — identical to the pre-task-8
baseline (this task is pure client-side JS, so no new Python test coverage
was expected or added).

**Browser verification** (isolated `DATA_HOME=/tmp/fm-smoke`, fixture
`agent/pages/FmSmoke`, restored via `cp` from `FmSmoke.orig.md` afterward,
byte-identical confirmed by `diff`). All eight checks from the brief's Step 5,
adapted to FmSmoke:

1. Edited the summary textarea, blurred (Tab), waited >600ms. Sidebar row for
   FmSmoke updated to the new summary text; `diff` against the previous disk
   state showed only the `summary:` line changed.
2. Set the importance slider via a `change` event. Panel showed the new value;
   `diff` showed only `importance:` changed.
3. Removed the "workflow" tag chip, typed "smoketest" + Enter in the tags
   input. `diff` showed only the `tags:` list changed (workflow out,
   smoketest in).
4. Removed all three remaining tag chips (0din, AI, smoketest) one at a time.
   Resulting file has **no `tags:` key at all** — confirmed by `cat`, not
   `tags: null`.
5. `curl -X PUT .../FmSmoke -d '{"frontmatter": {"importance": 1.7}}'` (used
   `127.0.0.1` not `localhost` — the session cookie's domain is `127.0.0.1`,
   so `localhost` gets "not authenticated"). Response: `"importance": 1.0` —
   coercion confirmed on the wire path.
6. Opened "edit raw YAML"; textarea held the real bytes (`JSON.stringify`'d
   the `.value` to confirm actual `\n` newlines, not accessibility-tree
   flattening). Replaced with an unterminated `[...` flow sequence and saved:
   inline error appeared ("invalid YAML: while parsing a flow sequence...");
   `cat` on disk showed the file untouched. Fixed the YAML and saved again:
   applied cleanly, editor auto-closed (`closeRaw()` fired), sidebar/panel
   updated to the new summary.
7. Deleted the `importance` key from the raw textarea and saved. Disk file
   has no `importance` key; panel's importance row shows "—" (its no-value
   placeholder). Confirms whole-frontmatter replace semantics, not a merge.
8. Race check: edited the summary textarea (arms `wiki-page`'s 600ms
   debounce) then, in the *same synchronous JS turn* (before the timer's
   macrotask could fire — verified this is guaranteed since `setTimeout`
   can't run until the current call stack unwinds past any pending
   microtasks), opened the raw editor, set different raw text, and clicked
   Save YAML. Captured the two resulting PUT request bodies via Playwright's
   network inspector: PUT #1 was
   `{"frontmatter":{"summary":"TYPED PENDING..."}}` (the flushed typed
   patch), PUT #2 was `{"frontmatter_raw":"...summary: RAW WINS"}` (the raw
   replace) — confirming `_onMetadataRawSave`'s `await this.#flushMetadata()`
   really does send the patch first. Final disk content: `summary: RAW WINS`
   — the raw save won and the typed edit did not resurrect or clobber it.

No deviations from the brief for Task 8 itself (Step 3c's comment rewrite was
the brief's own instruction, not a deviation).

## #318 coordination

#318 (first-class vault tags) is in flight in the `318-vault-tags` worktree and
reserves `/api/vault/tags` (Phase 5) and the web UI Tags tab (Phase 6).
`tags.py` already has `normalize_tag` and `pages_with_tags` with better
semantics than a frontmatter-only match — inline `#tags` too, AND-by-default
with an `any_tag` escape hatch.

Both branches edit `frontmatter.py`; the additions occupy different regions, so
whichever lands second resolves there. `split_frontmatter` must stay purely
lexical with no `tags` import so it doesn't deepen the existing
`frontmatter.py` ↔ `tags.py` cycle.

## Follow-up to file: `wiki-editor.js#reload()` ignores `saveEndpoint`

Found during Task 7/8 browser verification, deliberately **not fixed** — out
of this branch's scope (it predates this branch and isn't caused by it).

`#reload()` (`src/decafclaw/web/static/components/wiki-editor.js`, around line
243) does:

```js
const res = await fetch(`/api/vault/${encodePagePath(this.page)}`);
```

hardcoded to the vault endpoint, ignoring `this.saveEndpoint` — the property
that lets `<wiki-editor>` be reused against other hosts. Two other hosts
instantiate `<wiki-editor>` with a different `save-endpoint`:

- `schedule-page.js` → `/api/schedules/`
- `config-panel.js` → `/api/config/files/`

`#reload()` fires from the conflict-resolution UI's "Reload" button (a 409
`modified`-mismatch response shows Reload / Overwrite / Retry). On those two
hosts, clicking Reload fetches `/api/vault/{page}` instead of the host's own
endpoint — silently loading an unrelated vault page's content if one happens
to exist at that path, or a 404 (surfaced as `_error = "Reload failed: HTTP
404"`) if not. Either way the editor does not reload the schedule/config file
the user actually asked to reload.

This branch's Task 7 already had to touch `#reload()` for an unrelated reason
(the vault endpoint stopped returning `content`, only `body`), and folded a
comment there explaining the hardcoding so the next person doesn't have to
rediscover it via git blame. The fix itself — parameterizing the fetch URL on
`this.saveEndpoint` — is a one-line change but is explicitly out of scope
here per the task brief; someone should file it as its own issue.

## Session summary

**What shipped.** The web UI's vault surface is now frontmatter-aware
end-to-end. Server side: `frontmatter.py` gained `split_frontmatter` /
`join_frontmatter` / `parse_frontmatter_block` plus the relocated
`merge_frontmatter`; body-only writes splice the original frontmatter bytes
back verbatim instead of round-tripping through `yaml.dump` (which sorts keys
and drops comments) or `parse_frontmatter` (which reports `{}` for malformed
YAML and would silently delete the block). `vault_read` and `GET
/api/vault/{page}` now return `frontmatter` / `frontmatter_raw` / `body`
instead of a single mangled `content` string, with `frontmatter_error`
surfaced (not swallowed) on parse failure. `PUT /api/vault/{page}` accepts two
mutually-exclusive frontmatter write shapes — a `frontmatter` **patch**
(merged, `null` deletes a key) and a `frontmatter_raw` **replace** (verbatim,
rejects malformed YAML, non-mappings, and an embedded `---` line that would
otherwise get spliced into a spurious block terminator on the next read).
Client side: the vault sidebar shows page summaries from frontmatter
(fail-open); a new `<wiki-metadata>` component renders a compact strip in view
mode (expandable to full detail, including unknown keys) and typed
edit-mode controls (`summary`, `importance`, `tags`, `keywords`) plus a raw
YAML editor as an escape hatch; `wiki-page` was rewired onto `body` instead of
the old frontmatter-and-body-mashed-together `content`; a single write mutex
in `wiki-page` serializes the debounced typed-control patches against raw-editor
replaces so the two paths can't race each other onto disk; a conflict banner
(Reload / Overwrite / Retry) handles the 409 case.

**The corruption question, answered.** The bug this branch fixes was real
*editing*, not merely *opening* a page — see "Open question to answer during
Task 7" above for the empirical verification (Milkdown's `markdownUpdated`
listener does not fire a change on its own initial `defaultValueCtx` load;
only an actual keystroke armed the old body-only autosave that used to
reserialize the whole file, YAML included, through the WYSIWYG round-trip).
Anyone who had only *opened* vault pages in the old editor without editing
them did not lose frontmatter; anyone who typed anything into the body did.

**Browser verification.** Both Task 7 (read path: strip rendering, no stray
`<hr>`/YAML-as-bullets, body-only edits leaving the frontmatter block
byte-identical by MD5) and Task 8 (write path: all eight scenarios from the
brief — debounced patch, slider coercion, tag add/remove down to zero keys,
raw-YAML validation error leaving disk untouched, raw replace deleting a key
outright, and the concurrent patch-then-raw-replace race resolving in submit
order) passed against an isolated `DATA_HOME=/tmp/fm-smoke` fixture, restored
byte-for-byte afterward. Full detail lives in the "Task 7 details" and "Task 8
details" sections above.

**Live bug found, not fixed.** See "Follow-up to file" above —
`wiki-editor.js#reload()`'s hardcoded `/api/vault/` fetch breaks Reload on the
schedule and config-file hosts that reuse `<wiki-editor>`. Pre-existing,
out of scope, documented for a follow-up issue.

**#318 coordination.** Tag chips in `<wiki-metadata>` are inert this session —
#318 owns tag query semantics and the Tags tab. `frontmatter.py` is touched by
both branches in non-overlapping regions; whichever lands second resolves the
merge there.

**Test posture.** Baseline (pre-branch) was 3234 passed, 2 skipped. Post-Task-8
was 3276 passed, 2 skipped, 2 warnings (pre-existing `forkpty` deprecations).
Task 9 re-verified `make check` / `make test` before rebasing onto
`origin/main`, then again after — see the top-level report for exact
before/after counts.
