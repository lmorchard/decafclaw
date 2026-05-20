# Plan

1. **Implement body injection in `schedules.py:run_schedule_task`.**
   - Add a helper that takes `config` + a list of required-skill names and returns a `<loaded_skills>...</loaded_skills>` block string (empty string if no resolved bodies).
   - Resolve skills via `config.discovered_skills`; skip unknown names with a warning log.
   - Substitute `$SKILL_DIR` using `skill_info.location.resolve()` (match `activate_skill_internal`).
   - HTML-escape the skill name in the `<skill name="…">` attribute (match `prompts/__init__.py:106`).
   - Prepend the block to `prompt` between `preamble` and `body`.

2. **Escape-hatch exemption in `setup_schedule_ctx`.**
   - When `allowed_tools_set is not None`, unconditionally `.update({"tool_search", "activate_skill"})`.
   - Do NOT add to `preapproved` — those are user-confirmation bypasses for tools that would otherwise prompt; these tools don't need that treatment.

3. **Regression test** in `tests/test_schedules.py`:
   - Build a `SkillInfo` with `body="MASTODON-SKILL-BODY-MARKER"` and `name="mastodon-ingest"`.
   - Build a `ScheduleTask` with `required_skills=["mastodon-ingest"]`, body `"Follow the mastodon-ingest skill instructions to completion."`.
   - Patch `manager.enqueue_turn` (AsyncMock) to capture kwargs and resolve with a future returning `"ok"`.
   - Stub `config.discovered_skills`.
   - Run `run_schedule_task` and assert `kwargs["prompt"]` contains `"MASTODON-SKILL-BODY-MARKER"` and `"<loaded_skills>"`.
   - Second test: ensure missing required-skill name is skipped without raising.
   - Third test: assert `tool_search` and `activate_skill` are added to `ctx.tools.allowed` when a schedule has an allow-list.

4. **Lint + typecheck + tests.** `make check && make test`.

5. **Docs.** Update `docs/schedules.md` to mention the body-injection behavior and the escape-hatch exemption.

6. **Commit + PR.** Branch already created (`fix-558-schedule-skill-body`). Single commit. PR closes #558.
