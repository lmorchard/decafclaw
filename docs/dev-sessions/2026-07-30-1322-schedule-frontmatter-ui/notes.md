# Notes — Schedule frontmatter editing in the web UI

**Session:** 2026-07-30-1322-schedule-frontmatter-ui
**Branch:** `schedule-frontmatter-ui`
**Follows:** #729 / PR #730 (a `model:` value nothing read, and nothing said so)

## What shipped

Every `ScheduleTask` field is now viewable and editable from the schedules
page, and a test fails if a future field is added without being wired through.

**Server**

- `parse_schedule_file` collects frontmatter keys it does not understand into
  `ScheduleTask.unknown_keys` — diagnostic only, never serialized back.
- `write_overlay` accepts patches for `shell_patterns` and `email_recipients`,
  which previously could not be set through the API at all.
- `_schedule_to_dict` emits `shell_patterns`, `email_recipients`, `pre_script`,
  `unknown_keys` and `frontmatter_raw`; three of those had no wire
  representation before.
- `GET /api/models` returns `{models, default}` for UI model pickers.
- `tests/test_schedule_wire_drift.py` derives its expectations from
  `dataclasses.fields(ScheduleTask)` and holds the four hand-maintained
  enumerations (the dataclass, `serialize_to_markdown`, `write_overlay`'s patch
  keys, `_schedule_to_dict`) against each other.

**Client**

- `<chip-list>` extracted from `wiki-metadata` and shared.
- New `<schedule-metadata>` panel: cron, channel, model, enabled, required
  skills, plus a marked permissions group (allowed tools, shell patterns, email
  recipients, pre-script) and a read-only raw frontmatter view.
- `<schedule-page>` drops its three inline controls and hosts the panel,
  reusing its existing `#patchField` PUT path. Server 400s, network failures and
  unrecognized keys all surface in the UI.

**Docs** — `docs/schedules.md` gained a full frontmatter reference, the
`/api/models` endpoint, and an accurate description of the side-panel editor.

## Decisions worth remembering

**The raw section is read-only, unlike the vault panel's.** Schedule
frontmatter maps onto a fixed dataclass, and `serialize_to_markdown` writes
only recognized fields. An editable raw box would happily accept a key and drop
it on the next write — reproducing #729 inside the very component built to make
#729 visible. Once every field has its own control the typed set is complete by
construction, so there is nothing an editable raw box could reach that the form
cannot.

**No 409 conflict banner, deliberately.** `wiki-metadata` has one;
`schedules_update` pops the `modified` hint and does not enforce it, and
`docs/web-ui.md` documents the schedules surface as last-write-wins. A
Reload/Overwrite affordance would imply a guarantee the server does not make.
The panel gets a plain error banner for the 400s `write_overlay` actually
raises (invalid cron, non-list value). Adding conflict detection to the
schedules endpoint is a separate concern from exposing its fields.

**Unrecognized keys are removed, not preserved.** Late correction. The UI first
said they were "ignored", which is true at run time and misleading on disk: any
single-field edit re-serializes the whole file and destroys them along with any
YAML comments. Both the warning strip and the docs now say so.

**`pre_script` belongs in the permissions group.** It shipped in the plain form
between Channel and Required skills, unmarked, while `email_recipients` got a
red box. It executes arbitrary Python as the bot process on the next fire — the
most powerful control on the panel.

**Never bind `.value` on a `<select>` whose options come from a child part.**
lit commits parts in tree order, so `.value` lands on an empty select:
`selectedIndex` goes to -1, appending the options fires the select reset
algorithm, and the first option wins. lit then never re-commits the unchanged
string, so it cannot self-heal. Express selection per-option with `?selected=`,
as `conversation-sidebar.js` already did. This shipped as a real bug and made a
configured model read as "(default)" — #729's ambiguity, reintroduced.

### The drift guard took three hardening rounds

The guard is the most interesting artifact here, and its first two versions
could be silenced without failing:

1. **Round one** asserted key *presence* only, so a hardcoded stub
   (`"pre_script": ""`) passed. It also validated rename targets against other
   *field* names only, so mapping an unwired field onto an existing wire key
   made the presence check green while the field stayed invisible — the exact
   failure mode the guard exists to catch.
2. **Round two** added distinct per-field values (so a *misrouted* value fails,
   not just a dropped one), extended injectivity to the whole emitted keyspace
   via `NON_FIELD_WIRE_KEYS`, and probed `enabled` with two opposite fixtures
   (a two-valued type cannot distinguish a constant from a real field on one
   sample). It also removed `test_unknown_keys_is_not_patchable`, which could
   not fail: `serialize_to_markdown` never emits `unknown_keys`, so the reparse
   always yields `[]` regardless of what `write_overlay` does.
3. **Round three** (final review) closed two more. `NON_FIELD_WIRE_KEYS` was
   itself a hand-maintained enumeration *inside* the anti-enumeration guard,
   and it feeds the masking check — a stale copy silently narrows it. It is now
   pinned to `set(payload) - {field wire keys}`. And the two-opposite-fixtures
   bool fix had landed only in the non-parametrized
   `test_wire_value_for_bool_field_round_trips`; the parametrized patchability
   test still used a fixed `SAMPLES[bool] = False`, so a future bool defaulting
   `False` would be "patched" to `False` and asserted equal to `False`,
   passing whether or not it was ever wired. Samples now derive from
   `not spec.default`.

Standing lesson: for a guard test, "does it pass?" is the wrong question. Every
assertion in that file was validated by inducing the regression it claims to
catch, and three of them turned out not to catch it.

### Process notes

- Reviews were dispatched per task and again over the whole branch. **The
  per-task reviews caught the server-side problems and missed every serious
  client-side one**, because nothing ever opened a browser or exercised the
  component the way the app assembles it. The `.value`-on-`<select>` bug, the
  missing scroll container, and the dead `label.inline` class all survived to
  the final whole-branch review.
- Three tasks shipped tests that could not fail. Confirming a new test fails
  *before* the fix is cheap and was worth every minute it cost.
- The layout regression (CSS deleted with the old inline form, never replaced)
  is invisible to `make check`, `make test` and `make test-js` alike. jsdom does
  not lay out.

## Known gaps / follow-ups

Nothing here blocks merge; all of it was consciously left.

**Needs a human in a browser.** The panel's layout and scrolling
(`schedule-metadata.css`, `.schedule-page schedule-metadata` in
`schedule-page.css`) are unverified. They were written by reading the deleted
`.schedule-page-form` rules and the surrounding stylesheets. Check on a short
viewport specifically: the permissions group and the unrecognized-key warning
should be reachable by scrolling the panel, and `wiki-editor` should keep a
usable height.

**Deferred, with reasons.**

- `_read_frontmatter_raw` re-reads every schedule file on each list request,
  duplicating a read `discover_schedules` already did. Negligible at current
  task counts; a real cost if the list grows.
- `#onMetadataChange` fans out multi-key patches sequentially and is
  last-write-wins on `_saveError`, so one key's success would clear another
  key's error. Unreachable today — the panel emits exactly one field per edit.
- `<schedule-metadata>`'s `readonly` property has no test exercising
  `readonly=true`, and nothing in production sets it. It exists for a future
  read-only tier.
- `test_unknown_keys_are_not_written_back` (in `test_schedules.py`) is
  trivially true: `serialize_to_markdown` never enumerated `unknown_keys` in
  the first place. Kept as a regression guard, but it is not evidence of
  anything today.
- No conflict detection on `PUT /api/schedules/{name}`. See the 409 decision
  above; it wants its own issue if we decide the schedules surface should stop
  being last-write-wins.
- #731 (workspace-tier preapproval) was explicitly kept out — it moves a
  security boundary and should not ride in a UI PR.

**Cross-cutting, pre-existing.** The vault / files / schedules sidebars use
plain `<div @click>` rows with no `role` or `tabindex`; tracked as #555.
