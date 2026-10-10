# Spec: Restrict shell_guidance preset activation to per-conversation scope (#986)

## Problem

Currently, `shell_guidance` accepts a `persistent=True` flag that writes enabled/disabled presets and custom rules to `config.agent_path / "shell_approval_guidance.json"`.

As originally specified in #954, situational prompt selection was intended to be strictly **per-conversation**:
> *"It would be interesting to have several different auto-approval prompts, for different situations. For example, if we know that this conversation is explicitly about code development involving github, enable those approvals... Leaving these approval sets out of the prompt by default in other situations could help keep auto-approvals preventing unexpected actions."*

Persisting preset activations globally across all future conversations erodes this situational safety boundary:
1. Activating `developer` or `github` during an interactive coding session permanently relaxes shell evaluation for future unrelated conversations, casual chat, and automated/unattended tasks.
2. It breaks the principle of least privilege per session.

## Decisions (from Issue Triage & Les Interview)

1. **Remove `persistent` option from `shell_guidance`**:
   - `shell_guidance` operates exclusively on the current conversation (`ctx.tools.active_aux_approval_presets`, `ctx.tools.disabled_aux_approval_presets`, and `ctx.tools.aux_approval_guidance`).
   - If permanent global presets or rules are desired, they must be declared explicitly in static `config.json` (`shell.active_aux_approval_presets`, `shell.aux_approval_guidance`), not mutated dynamically by tool calls into a global state file.
   - Removing `persistent` also removes "persistently disable a config preset". If an admin wants to disable a preset globally, they remove it from `config.json`.

2. **Clean up runtime persistence**:
   - Remove `_save_persistent_preset`, `_remove_persistent_preset`, `_save_persistent_rule`, `_remove_persistent_rule`, and runtime writes to `shell_approval_guidance.json`.
   - Remove `persistent` parameter from `tool_shell_guidance` and its tool definition.
   - Remove persistent sources from `tool_shell_guidance(action="list")` and `resolve_aux_approval_guidance`.

3. **Existing `shell_approval_guidance.json` files**:
   - Ignored after this change.
   - If `config.agent_path / "shell_approval_guidance.json"` exists, log one warning on first check naming the file and telling the admin to move rules/presets into `config.json` (`shell.active_aux_approval_presets`, `shell.aux_approval_guidance`).
   - No migration and no compatibility read.

## Acceptance Criteria

- `tool_shell_guidance` no longer accepts `persistent`.
- `shell_guidance` tool definition has `persistent` removed from parameters and description.
- `resolve_aux_approval_guidance` does not load from `shell_approval_guidance.json`.
- `tool_shell_guidance(action="list")` lists presets and rules with config and session sources only.
- If `shell_approval_guidance.json` exists on disk, a warning is logged once advising to move settings to `config.json`, and its contents are ignored.
- Tests in `tests/test_shell_approval_guidance.py` covering persistence are removed/rewritten, and a test verifies that existing `shell_approval_guidance.json` logs a warning and is ignored.
- Documentation in `docs/tools.md` updated to reflect strictly per-conversation scope.
- Full test and gate suites pass.
