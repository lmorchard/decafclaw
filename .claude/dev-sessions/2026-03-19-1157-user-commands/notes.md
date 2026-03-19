# User-Invokable Commands — Notes

## Session Log

### Phase 1 — Frontmatter + lookup (done)
- Added allowed_tools, context, argument_hint to SkillInfo
- find_command and list_commands helpers

### Phase 2 — Command engine (done)
- commands.py: parse_command_trigger, substitute_arguments, format_help, execute_command
- Extracted activate_skill_internal from tool_activate_skill
- Added preapproved_tools to Context

### Phase 3 — Chat layer wiring (done)
- Mattermost: ! prefix detection in _process_conversation
- Web UI: / prefix detection in _handle_send
- !help / /help returns command list directly

### Phase 4 — Pre-approved tools + docs (done)
- Shell pre-approval check
- request_confirmation pre-approval check
- delegate child inherits preapproved_tools
- docs/commands.md, CLAUDE.md updated

### Bug fixes during QA
- $ARGUMENTS left as literal when no args — now always replaced
- Shell-based skills were double-activated (body as skill activation result AND user message) — now only native-tool skills get activated

### Known issues
- Gemini Flash sometimes hallucates tool calls from command names (e.g. hello → hello tool). This is model behavior — the command infrastructure works correctly. Clean conversations don't have this issue.
- SKILL.md supports both `user_invocable` (underscore) and `user-invocable` (hyphen) in frontmatter — the parser reads `user-invocable`, underscore version is ignored but defaults to True anyway.

## Summary

530 tests passing. Branch `user-commands`, PR #77. 6 commits.
