# Spec — Schedule frontmatter editing in the web UI

**Session:** 2026-07-30-1322-schedule-frontmatter-ui
**Branch:** `schedule-frontmatter-ui`
**Follows:** #729 / PR #730 (unrecognized model names)
**Related, not included:** #731 (workspace-tier preapproval)

## Problem

The schedules page exposes three form controls — cron, channel, enabled — plus
the body in a `wiki-editor`. Every other frontmatter field is unreachable from
the UI. Changing a schedule's model means ssh and a text editor.

This is the same failure shape as #729 one layer up. There, a field existed in
the file format, nothing read it, and nothing said so. Here, fields exist in
the format and the UI simply cannot see them.

Four hand-maintained field lists have drifted apart at four different points:

| Field | `ScheduleTask` | `serialize_to_markdown` | `write_overlay` patch | wire (`_schedule_to_dict`) | UI form |
|---|:--:|:--:|:--:|:--:|:--:|
| `schedule` | yes | yes | yes | yes | yes |
| `enabled` | yes | yes | yes | yes | yes |
| `channel` | yes | yes | yes | yes | yes |
| `body` | yes | yes | yes | yes | yes |
| `model` | yes | yes | yes | yes | **no** |
| `allowed_tools` | yes | yes | yes | yes | **no** |
| `required_skills` | yes | yes | yes | yes | **no** |
| `pre_script` | yes | yes | yes | **no** | **no** |
| `shell_patterns` | yes | folded | **no** | **no** | **no** |
| `email_recipients` | yes | yes | **no** | **no** | **no** |

`shell_patterns` and `email_recipients` cannot be set through the API at all.
`pre_script` is writable but invisible.

CLAUDE.md's "never enumerate fields when copying, forking, snapshotting, or
serializing" applies directly. These are exactly the hand-maintained lists that
rot silently.

## Goals

1. Every `ScheduleTask` field is viewable and editable from the schedules page,
   with trust-boundary fields visually distinguished.
2. Adding a field to `ScheduleTask` fails a test until it is wired through or
   explicitly exempted.
3. Frontmatter keys the parser does not recognize become visible instead of
   being silently discarded.

## Non-goals

- Fixing #731. That changes a security boundary and must not ride along in a UI
  PR.
- A generic schema-driven metadata component serving both vault and schedules.
  Considered and rejected; see "Rejected alternatives".
- Editable raw YAML for schedules. See "Raw view is read-only".

## Design

### Server

**Drift guard.** A test iterates `dataclasses.fields(ScheduleTask)` and checks
two things per field, with a separate exemption set for each.

*Every field must reach the wire.* Three are renamed by `_schedule_to_dict`, so
the test carries an explicit rename map rather than exempting them:

| dataclass field | wire key |
|---|---|
| `source` | `source_tier` |
| `path` | `source_path` |

`name` keeps its name. No field is exempt from this half — if it exists on the
dataclass, the UI can see it.

*Every field must be patchable, except:* `name`, `source`, `path` (identity and
provenance — the file's location is not user-editable content) and
`unknown_keys` (parser diagnostic, read-only by construction).

Both exemption sets are small and semantically stable, so they do not rot in
lockstep with the bug they exist to catch. The wire and patch lists in the
implementation stay explicit and readable; the test is what keeps them honest.

**Close existing gaps.**

- `write_overlay` patch keys gain `shell_patterns` and `email_recipients`.
  Both are lists; they get the same `isinstance(list)` rejection the other list
  fields already have.
- `_schedule_to_dict` gains `shell_patterns`, `email_recipients`, `pre_script`.

`allowed_tools` and `shell_patterns` both serialize into the single
`allowed-tools:` YAML key, and `serialize_to_markdown` already merges them.
Patching either independently must leave the other intact.

**Unknown-key capture.** `parse_schedule_file` collects frontmatter keys it does
not recognize into a new field:

```python
unknown_keys: list[str] = field(default_factory=list)
```

Diagnostic only — never written back by `serialize_to_markdown`, never
patchable. Per the drift guard above, it reaches the wire but is exempt from
the patchability half.

Recognized keys: `schedule`, `enabled`, `channel`, `model`, `effort` (legacy
alias), `allowed-tools`, `pre_script`, `required-skills`, `email-recipients`.

**Raw frontmatter on the wire.** `_schedule_to_dict` gains `frontmatter_raw`:
the literal frontmatter block read from `task.path`, not a re-serialization of
the parsed task. The distinction matters — the raw view exists to show what is
actually on disk, so an unrecognized key is visibly present next to the warning
naming it. A re-serialization would omit exactly the thing the user needs to
see.

**`GET /api/models`** returns `{"models": [...sorted...], "default": "..."}`
from `config.model_configs` / `config.default_model`. A new route rather than
riding on the schedules payload, so the page works with no conversation loaded
and a global config list is not duplicated per schedule item.

### Client

**`chip-list.js`** — extracted from `wiki-metadata`'s `#renderChipInput`.
Props `label`, `items`, `readonly`; emits `chips-change` with the new array.
Vault refactors onto it (2 uses: tags, keywords); schedules consumes it
(4 uses: required-skills, allowed-tools, shell-patterns, email-recipients).

**`schedule-metadata.js`** — new presentational component. No I/O. Follows
`wiki-metadata`'s established contract:

- emits `metadata-change {fields: {...}}` for typed edits
- the host owns every PUT, so metadata writes serialize against the body
  autosave and keep mtime in sync

It does **not** copy `wiki-metadata`'s 409 conflict banner. `schedules_update`
pops the `modified` hint and does not enforce it — `docs/web-ui.md` states the
schedules surface is last-write-wins — so there is no 409 to handle and a
Reload/Overwrite affordance would imply a guarantee the server does not make.
The panel gets a plain error banner instead, for the 400s `write_overlay` does
raise (invalid cron, non-list value). Adding conflict detection to the
schedules endpoint is a separate concern from exposing its fields.

Props: `data` (schedule dict), `models` (from `/api/models`), `readonly`,
`metaError`.

Layout:

```
Cron         [0 3 * * *          ]
Channel      [                   ]
Model        [vertex-gemini-pro v]
Enabled      [x]
Pre-script   [                   ]
Required skills  [dream] [vault] [+]

⚠ Permissions — these bypass confirmation
Allowed tools    [vault_read] [+]
Shell patterns   [$SKILL_DIR/fetch.sh] [+]
Email recipients [ ] [+]

▸ raw (read-only)
```

**Model dropdown.** Options come from `/api/models`. If the stored `model` is
not among them — an overlay still saying `strong` — it renders as a selected
but flagged option, `⚠ strong (not configured)`, rather than as a blank field.
Blank reads as "no model set", which is precisely the ambiguity that kept #729
invisible for months.

**Raw view is read-only.** Vault frontmatter is open-ended, so unknown keys are
legitimate and its raw editor round-trips them. Schedule frontmatter is closed:
it maps onto a fixed dataclass, and `serialize_to_markdown` writes only known
fields. An editable raw box would accept `foo: bar`, save without error, and
drop it on the next write — the #729 failure mode exactly. So raw is a
collapsible read-only block, with a warning strip naming any unrecognized keys:

```
⚠ 2 keys in this file are not recognized and are ignored: modle, efort
```

Once every field has a control the typed set is complete by construction, so
there is nothing an editable raw box could reach that the form cannot.

**`schedule-page.js`** drops its three inline controls and hosts the panel,
reusing its existing `#patchField` PUT path.

### Error handling

| Case | Behavior |
|---|---|
| Invalid cron | Existing 400 from `write_overlay`, into the panel's error banner |
| Non-list value for a list field | Existing 400, same banner |
| Concurrent write | Last-write-wins, unchanged. Out of scope; see above |
| Stored model not in `model_configs` | Flagged option in the dropdown, not an error |
| Unrecognized frontmatter keys | Non-blocking warning strip above the raw block |
| `/api/models` unreachable | Dropdown falls back to a plain text input; the field stays editable |

### Testing

**Python**
- Drift guard over `dataclasses.fields(ScheduleTask)`.
- `parse_schedule_file` populates `unknown_keys`; recognized keys never appear
  in it; `serialize_to_markdown` never writes them back.
- `write_overlay` round-trips `shell_patterns` and `email_recipients`, and
  patching one of `allowed_tools` / `shell_patterns` preserves the other.
- `_schedule_to_dict` includes every non-exempt field.
- `GET /api/models` shape and auth.

**Vitest**
- `schedule-metadata` renders every field and emits `metadata-change` with the
  right payload per control.
- Unrecognized-key warning appears with the key names.
- Permissions group carries its marking.
- Model dropdown flags an unconfigured stored value.
- `chip-list` add / remove / readonly.
- `wiki-metadata`'s existing tests still pass after the chip extraction.

**No evals.** Deterministic UI and serialization; nothing LLM-visible. Per
CLAUDE.md, evals are for LLM-driven behavior.

### Docs

- `docs/schedules.md` — complete the frontmatter table (`shell_patterns`,
  `email-recipients`, `pre_script`), document unrecognized-key handling, add a
  short UI section.
- `docs/web-ui.md` — update the schedules page description.
- `CLAUDE.md` — only if the key-files list changes (two new JS components).

## Rejected alternatives

**Generic schema-driven `<metadata-panel>` for both vault and schedules.**
Server sends field descriptors, the panel renders itself, drift becomes
structurally impossible. Rejected: the abstraction would have to absorb vault's
importance slider and schedules' cron field and every future oddity, and the
drift test already buys the safety at a fraction of the cost. It also reverses
the explicit decision to keep the field lists readable.

**Inline in `schedule-page.js`.** Rejected: roughly doubles a 208-line file
doing two jobs, and leaves no unit-testable panel.

**Editable raw YAML with unknown keys preserved** (adding an `extra: dict` to
`ScheduleTask`). Rejected: invents a place to store data nothing reads.

**Editable raw YAML with unknown keys rejected at save.** Rejected as more
machinery than the read-only view plus warning, for no additional capability.

## Known interaction with #731

#731's fix will likely make workspace-tier `allowed-tools` a visibility filter
rather than a pre-approval grant. If it lands first, the permissions group
should label that tier accordingly. Not a blocker in either order; whichever
lands second picks up the labeling.

## Success criteria

1. Every `ScheduleTask` field is editable from the schedules page.
2. Adding a field to the dataclass fails a test until wired through or exempted.
3. A schedule whose stored model is not configured shows that in the dropdown
   rather than rendering blank.
4. A frontmatter key the parser ignores is named in the UI.
5. `make check` and `make test` green; `make test-js` green.
