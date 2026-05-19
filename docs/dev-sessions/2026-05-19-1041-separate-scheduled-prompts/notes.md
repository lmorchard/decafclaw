# Session notes — Separate scheduled prompts from skills (#254)

## Outcome

Shipped 5 phases across the session, ~12 commits before squash:

1. **Phase 1** — SCHEDULE.md sidecar replaces `schedule:` in SKILL.md. Bundled dream/garden/newsletter migrated; three contrib skills (kindle/linkding-ingest/mastodon-ingest) also migrated to avoid orphaned `schedule:` fields. `SkillInfo` lost `schedule` + `enabled` fields. Contrib SCHEDULE.md is force-disabled at discovery.
2. **Phase 2** — `/api/schedules` REST API with GET list, PUT patch, DELETE-overlay. Overlay store at `data/{agent_id}/schedules/{name}.md` (admin standalone dir doubles as the overlay store).
3. **Phase 3** — `<schedules-sidebar>` Lit component as a fourth tab. Inline expand-to-edit panel (later replaced in Phase 4).
4. **Phase 4** — moved editor into the `#wiki-main` side panel via `<schedule-page>` + `<wiki-editor>`. Workspace tier became user-editable (no more 403). Added GET single-schedule endpoint, `modified` field, `content` alias for `body`, deep-link URL.
5. **Phase 5** — `POST /api/schedules/{name}/run` + "Run now" button. Bypasses the `enabled` flag.

## Surprises / pivots

- Phase 3 originally had inline expand-to-edit. After UX feedback ("open a sidebar editor like file editing"), pivoted to Phase 4 with `<schedule-page>` in `#wiki-main`. Cleaner UX, but required understanding the existing wiki-page / file-page / config-panel mutual-exclusion pattern in `app.js`.
- **Milkdown / wiki-editor remount gotcha**: Milkdown reads its initial content once in `firstUpdated`; later `.content` property changes are ignored. Schedule-page was reusing the same wiki-editor element across schedule switches, so only the first schedule's body ever displayed. Fixed by making `render()` swap on `_loading` (not just `!_data`) so `#fetchSchedule`'s loading=true/false cycle unmounts + remounts the editor. Worth a CLAUDE.md note if we see this pattern again.
- **Metadata refresh after wiki-editor save**: `#onWikiSaved` originally only updated `_data.modified`. After a body save that created an overlay, the badge stayed `bundled` and reset button stayed hidden until the user reloaded. Fixed by refetching metadata while preserving the in-editor body so the editor doesn't remount mid-edit.
- **wiki-editor's modified contract** is top-level: it reads `data.modified` (not `data.schedule.modified`). Handler returns both for safety.
- **HEARTBEAT_OK sentinel** surfaced as noise during this session — filed [#553](https://github.com/lmorchard/decafclaw/issues/553) for the structured-status follow-up; not addressed here.

## Code review consistency

Each phase got per-phase code review with follow-up commits for the issues. Patterns the reviewer caught repeatedly:
- Stale docstrings / comments after behavior changes (Phase 1, Phase 4)
- Function-level stdlib imports (Phase 1, Phase 5 tests)
- Silent-catch error handling on UI fetch paths (Phase 3, Phase 4 `#patchField`)
- Missing tag-qualification on button CSS rules (Phase 3, Phase 4)

Worth checking these proactively in next session's per-phase implementer prompts.

## Test count

2675 → 2697 over the session (+22 new tests across phases). Full suite stays under 12s.

## Out of scope (filed or noted)

- [#553](https://github.com/lmorchard/decafclaw/issues/553) — Replace HEARTBEAT_OK sentinel with structured signaling.
- [#555](https://github.com/lmorchard/decafclaw/issues/555) — Improve a11y for clickable sidebar list rows (cross-cutting; vault/files/schedules all have the same gap).
- wiki-editor's `#reload()` hardcoded to `/api/vault/{page}` — file a follow-up if conflict detection ever lands for schedules.
- New-schedule / delete-schedule UI affordances (spec listed as v1 non-goals).
- WebSocket push for cross-tab schedule changes (v1 uses local DOM events + activation refresh).

---

## Retrospective

### What shipped

Five phases, one PR (#554), 11 Copilot comments addressed in-place (one reverted as cross-cutting and refiled), 2698 tests passing.

### Scope drift

- **Phase 3 → Phase 4 pivot.** Original plan had inline expand-to-edit. After seeing it built, the UX call ("open a sidebar editor like file editing") materialized. The fix was a full Phase 4 redesign, not a tweak — introduced a new `<schedule-page>` component, hooked into `#wiki-main` mutual exclusion, added a single-schedule GET endpoint, accepted `content` as an alias for `body`, changed sidebar to dispatch instead of expand. This worked because the dev-session structure made it easy to "add a phase" rather than rewrite Phase 3.
- **Phase 5 (Run now) added mid-execute.** Exploratory tone in user message ("might also be interesting"); confirmed before implementing.
- **Workspace-editable bundled in with Phase 4.** Originally a v1 non-goal in spec.md. Came up in the same UX-feedback message as the side-panel pivot. Small enough that bundling was right.
- **Three contrib skill migrations unsolicited but defensible** (kindle / linkding-ingest / mastodon-ingest). The `schedule:` field was being removed from SkillInfo; those files would have ended up orphaned. Spec compliance reviewer flagged the scope extension explicitly so it was visible.

### Surprises

- **Milkdown's content-once behavior.** Setting `.content` on `<wiki-editor>` after `firstUpdated` is a no-op — Milkdown takes its initial buffer from `defaultValueCtx` and ignores subsequent property changes. This bit us when reusing the same `<wiki-editor>` across schedule switches. Workaround: `_loading` swap in `render()` causes Lit to unmount + remount the editor. Predictable in retrospect — `file-page.js` already had a "force editor remount" comment that I missed during planning.
- **Pico v2 styles `[role="button"]` like a `<button>`.** Adding the attribute for a11y broke the row visual. Same cascade gotcha as the existing `<button>` rule, just on a different selector. Worth adding to the Pico-gotchas memory.
- **wiki-editor's save-response contract is top-level `data.modified`,** not nested. When the schedule PUT returns `{schedule: {modified}}`, wiki-editor's conflict tracking would silently break. Implementer chose to mirror `modified` at both top level and nested — pragmatic adapter, but worth a comment somewhere about why the duplication exists.
- **Bundled skills with schedules numbered more than I tracked.** Spec said three (dream/garden/newsletter); reality had three more in contrib. The Phase 1 implementer correctly extended scope; future spec-writing should grep for `schedule:` across all skill dirs (not just bundled) when reasoning about migration impact.

### Workflow friction

- **Recurring code-review findings.** Across all five phases, the code-quality reviewer flagged the same families of issues: stale docstrings after behavior changes, function-level stdlib imports, silent-catch error handling on UI fetch paths, tag-qualification on button CSS rules. These are mechanical and could be caught earlier — either pre-flighted in implementer prompts or via a dedicated "convention sweep" subagent between spec-compliance and code-quality reviews.
- **`AskUserQuestion` UX quirk.** Two questions came back with "no answer provided" despite the user selecting an option. Had to clarify and re-ask. Not a process issue with this session but worth knowing.
- **Mid-session pivots worked well.** Phase 3 → 4 redesign and Phase 5 add-on slotted cleanly into the plan-then-dispatch flow. The dev-session structure absorbs scope changes as long as each new phase gets its own plan section + dispatch.
- **Subagent-driven loop caught real bugs.** Notable: Phase 2's "invalid cron writes corrupt overlay then returns 404" (critical, only surfaced because the code-quality reviewer pushed on edge cases), Phase 5's unhandled-exception traceback noise risk (CLAUDE.md zero-tolerance policy match), Phase 1's stale `ScheduleTask.source` comment.

### Misses

- **Didn't recognize the Milkdown remount pattern from `file-page.js`.** A pre-planning grep for "force editor remount" / "Milkdown" would have surfaced it. The bug shipped to Les and required a follow-up fix.
- **Didn't predict Pico's `[role="button"]` cascade impact.** When Copilot suggested the a11y fix, I passed it through to the implementer verbatim without checking how Pico would style it. Could have caught it by remembering CLAUDE.md's gotcha applies to anything Pico considers a button, not just the `<button>` element.
- **Didn't surface the cross-sidebar a11y gap during brainstorm.** Each sidebar uses the same plain-`<div>` clickable-row pattern; could have noticed during research and decided up front whether to fix as part of this PR or accept the convention.

### Memory candidates (act on these)

- **Milkdown / `<wiki-editor>` content-once behavior** — host components reusing one editor element across content changes need a remount strategy (Lit `keyed`, `_loading` swap, or similar). Reference memory.
- **Pico v2 styles `[role="button"]` like `<button>`** — extend the existing Pico cascade memory.
- **wiki-editor save contract:** top-level `data.modified` for conflict tracking; nested shapes need a top-level mirror. Reference memory.
- **Vault/files/schedules sidebar convention:** clickable rows use plain `<div @click>` consistently (no role/tabindex/aria) — accept a11y limitation for clean Pico visuals. Codebase convention.
- **Recurring code-review findings across this session:** stale docstrings after behavior changes, function-level stdlib imports, silent-catch error handling on UI fetch paths, tag-qualification on button CSS rules. Pre-flight checklist candidate for implementer prompts.

### Skill candidates

- **Implementer prompt pre-flight checklist.** When the task involves hosting a stateful third-party component (CodeMirror, Milkdown, similar), list the component and confirm it reacts to prop changes mid-lifecycle. If not, plan the remount strategy up front.
- **Convention-sweep subagent** between spec-compliance and code-quality reviews. Cheap pass that grepping for the recurring issues catches before the heavyweight code-quality review fires. Faster iteration loop.

### Q&A from retro

**Brainstorm depth for UX placement:** *build first cut, iterate is fine.* Phase-add-on is cheap; speculative UX questions in every brainstorm make them heavier without payoff most of the time. Don't pre-emptively grow the brainstorm checklist for "where does the panel live."

**Convention sweep subagent:** *yes, add it.* Worth one more cheap subagent dispatch per phase to catch the mechanical recurring issues (stale docstrings, function-level imports, silent catches, button rule qualification) before the heavyweight code-quality review fires.
