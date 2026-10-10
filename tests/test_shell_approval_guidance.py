import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.config import load_sub_config
from decafclaw.config_types import ShellConfig
from decafclaw.context import Context, SkillState, ToolState
from decafclaw.media import ToolResult
from decafclaw.security_monitor import SecurityDecision, SecurityStatus
from decafclaw.tools.shell_tools import (
    DEFAULT_AUX_APPROVAL_PRESETS,
    _load_guidance_text,
    _suggest_aux_approval_pattern,
    _suggest_pattern,
    build_aux_approval_prompt,
    check_shell_approval,
    resolve_aux_approval_guidance,
    tool_shell_guidance,
)


def _make_mock_ctx(tmp_path: Path):
    ctx = MagicMock(spec=Context)
    ctx.config = MagicMock()
    ctx.config.workspace_path = tmp_path / "workspace"
    ctx.config.agent_path = tmp_path / "agent"
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    ctx.config.agent_path.mkdir(parents=True, exist_ok=True)
    ctx.config.agent.confirmation_timeout_sec = 60
    ctx.config.shell = ShellConfig(aux_approval_enabled=True)
    ctx.tools = ToolState()
    ctx.skills = SkillState()
    ctx.skip_vault_retrieval = False
    ctx.is_unattended = False
    ctx.task_mode = ""
    ctx.skip_archive = True
    return ctx


def test_build_prompt_default_strict(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)
    prompt = build_aux_approval_prompt(ctx, "ls -la")

    assert "Only auto-approve low risk read-only or harmless commands" in prompt
    assert "Additional Approval Guidelines:" not in prompt
    assert '"suggested_rule": "<string or null>"' in prompt
    assert "If auto_approve is false, formulate a concise, natural-language exception rule in suggested_rule" in prompt
    assert "If auto_approve is true or no rule makes sense, set suggested_rule to null." in prompt


def test_build_prompt_builtin_developer_preset(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)
    ctx.config.shell.active_aux_approval_presets = ["developer"]

    guidance = resolve_aux_approval_guidance(ctx)
    assert len(guidance) == 1
    assert guidance[0] == DEFAULT_AUX_APPROVAL_PRESETS["developer"]

    prompt = build_aux_approval_prompt(ctx, "pytest tests/")
    assert "Additional Approval Guidelines:" in prompt
    assert "- Auto-approve standard software development commands" in prompt
    assert "pytest" in prompt
    assert "uv run" in prompt
    assert "pyright" in prompt
    assert "git fetch" in prompt
    assert "&&" in prompt
    assert "unless explicitly permitted by the additional approval guidelines below" in prompt


def test_build_prompt_builtin_github_preset(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)
    ctx.config.shell.active_aux_approval_presets = ["github"]

    guidance = resolve_aux_approval_guidance(ctx)
    assert len(guidance) == 1
    assert guidance[0] == DEFAULT_AUX_APPROVAL_PRESETS["github"]

    prompt = build_aux_approval_prompt(ctx, "gh issue list")
    assert "Additional Approval Guidelines:" in prompt
    assert "- Auto-approve GitHub CLI (gh) commands" in prompt
    assert "gh pr list/view/diff/checkout/create/checks" in prompt


def test_build_prompt_custom_and_overridden_presets(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)
    ctx.config.shell.aux_approval_presets = {
        "developer": "Custom dev rule: only pytest.",
        "my_preset": "Custom preset rule for deployment.",
    }
    ctx.config.shell.active_aux_approval_presets = ["developer", "my_preset"]

    guidance = resolve_aux_approval_guidance(ctx)
    assert len(guidance) == 2
    assert "Custom dev rule: only pytest." in guidance
    assert "Custom preset rule for deployment." in guidance

    prompt = build_aux_approval_prompt(ctx, "pytest")
    assert "- Custom dev rule: only pytest." in prompt
    assert "- Custom preset rule for deployment." in prompt


def test_build_prompt_inline_guidance(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)
    ctx.config.shell.aux_approval_guidance = "Auto-approve ruff check and ruff format inside workspace."

    guidance = resolve_aux_approval_guidance(ctx)
    assert len(guidance) == 1
    assert guidance[0] == "Auto-approve ruff check and ruff format inside workspace."

    prompt = build_aux_approval_prompt(ctx, "ruff check .")
    assert "Auto-approve ruff check and ruff format" in prompt


def test_build_prompt_file_guidance(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)
    rules_file = ctx.config.agent_path / "shell_rules.txt"
    rules_file.write_text("Auto-approve cargo test and cargo build.\n")

    ctx.config.shell.aux_approval_guidance = "shell_rules.txt"

    guidance = resolve_aux_approval_guidance(ctx)
    assert len(guidance) == 1
    assert guidance[0] == "Auto-approve cargo test and cargo build."

    prompt = build_aux_approval_prompt(ctx, "cargo test")
    assert "Auto-approve cargo test and cargo build." in prompt


def test_build_prompt_file_guidance_rejects_workspace_files(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)
    ws_file = ctx.config.workspace_path / "shell_rules.txt"
    ws_file.write_text("Injected rule inside workspace\n")

    # Relative path should only look in agent_path; when missing there, use raw string
    ctx.config.shell.aux_approval_guidance = "shell_rules.txt"
    guidance = resolve_aux_approval_guidance(ctx)
    assert guidance == ["shell_rules.txt"]

    # Absolute path into workspace must also be rejected from file resolution
    ctx.config.shell.aux_approval_guidance = str(ws_file)
    guidance2 = resolve_aux_approval_guidance(ctx)
    assert guidance2 == [str(ws_file)]


def test_build_prompt_session_scoped_guidance(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)
    ctx.tools.active_aux_approval_presets.append("developer")
    ctx.tools.aux_approval_guidance.append("Session rule: approve docker ps.")

    guidance = resolve_aux_approval_guidance(ctx)
    assert len(guidance) == 2
    assert guidance[0] == DEFAULT_AUX_APPROVAL_PRESETS["developer"]
    assert guidance[1] == "Session rule: approve docker ps."

    prompt = build_aux_approval_prompt(ctx, "docker ps")
    assert "Session rule: approve docker ps." in prompt
    assert "- Auto-approve standard software development commands" in prompt


@pytest.mark.asyncio
async def test_check_shell_approval_passes_guidance_to_aux_llm(tmp_path: Path, monkeypatch):
    ctx = _make_mock_ctx(tmp_path)
    ctx.config.shell.active_aux_approval_presets = ["developer"]

    # Security monitor allows command to proceed to aux approval
    async def mock_sec(*args, **kwargs):
        return SecurityDecision(status=SecurityStatus.ALLOW, reason="ok")

    monkeypatch.setattr("decafclaw.tools.shell_tools.evaluate_command_llm", mock_sec)

    captured_prompt = None

    async def mock_llm_call(messages):
        nonlocal captured_prompt
        captured_prompt = messages[0]["content"]
        return {
            "content": '```json\n{"auto_approve": true, "reason": "pytest is approved under developer preset", "risk": "low"}\n```'
        }

    ctx.aux_llm = MagicMock(return_value=mock_llm_call)
    ctx.publish = AsyncMock()

    result = await check_shell_approval(ctx, "pytest tests/test_foo.py")

    assert result["approved"] is True
    assert captured_prompt is not None
    assert "Additional Approval Guidelines:" in captured_prompt
    assert "pytest" in captured_prompt
    # Pattern should be cached in session
    assert any("pytest" in p for p in ctx.tools.llm_approved_shell_patterns)


@pytest.mark.asyncio
async def test_tool_shell_guidance_list(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)
    ctx.config.shell.active_aux_approval_presets = ["developer"]
    ctx.tools.aux_approval_guidance = ["Session rule 1"]

    result = await tool_shell_guidance(ctx, action="list")
    assert isinstance(result, str)
    assert "### Shell Auto-Approval Presets" in result
    assert "**`developer`** [ACTIVE" in result
    assert "**`github`**:" in result
    assert "- *(session)*: Session rule 1" in result


@pytest.mark.asyncio
async def test_tool_shell_guidance_enable_and_disable_preset_session(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)

    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as mock_confirm:
        mock_confirm.return_value = {"approved": True}

        # Enable
        res = await tool_shell_guidance(ctx, action="enable_preset", preset="developer")
        assert isinstance(res, str)
        assert "Enabled shell auto-approval preset `developer` for this conversation" in res
        assert "developer" in ctx.tools.active_aux_approval_presets

        # Verify prompt includes it
        prompt = build_aux_approval_prompt(ctx, "pytest")
        assert "pytest" in prompt

        # Disable
        ctx.tools.llm_approved_shell_patterns = ["pytest *"]
        res_dis = await tool_shell_guidance(ctx, action="disable_preset", preset="developer")
        assert isinstance(res_dis, str)
        assert "Disabled shell auto-approval preset `developer` for this conversation" in res_dis
        assert "developer" not in ctx.tools.active_aux_approval_presets
        assert "developer" in ctx.tools.disabled_aux_approval_presets
        assert ctx.tools.llm_approved_shell_patterns == []

        # Verify prompt no longer includes it
        prompt_dis = build_aux_approval_prompt(ctx, "pytest")
        assert "Additional Approval Guidelines:" not in prompt_dis


@pytest.mark.asyncio
async def test_tool_shell_guidance_enable_preset_denied(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)

    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as mock_confirm:
        mock_confirm.return_value = {"approved": False}

        res = await tool_shell_guidance(ctx, action="enable_preset", preset="developer")
        assert isinstance(res, ToolResult)
        assert "[error: denied]" in res.text
        assert "developer" not in ctx.tools.active_aux_approval_presets


@pytest.mark.asyncio
async def test_tool_shell_guidance_add_and_remove_rule(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)

    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as mock_confirm:
        mock_confirm.return_value = {"approved": True}

        # Session rule
        res = await tool_shell_guidance(ctx, action="add_rule", rule="Auto-approve pytest tests/")
        assert isinstance(res, str)
        assert "Added shell auto-approval rule for this conversation" in res
        assert "Auto-approve pytest tests/" in ctx.tools.aux_approval_guidance
        ctx.tools.llm_approved_shell_patterns = ["pytest *"]
        res_rem = await tool_shell_guidance(ctx, action="remove_rule", rule="Auto-approve pytest tests/")
        assert isinstance(res_rem, str)
        assert "Removed shell auto-approval rule for this conversation" in res_rem
        assert "Auto-approve pytest tests/" not in ctx.tools.aux_approval_guidance
        assert ctx.tools.llm_approved_shell_patterns == []


@pytest.mark.asyncio
async def test_tool_shell_guidance_save_preset_new_and_update(tmp_path: Path):
    """Test saving active conversation rules to a new preset, and updating it (#1031)."""
    ctx = _make_mock_ctx(tmp_path)
    ctx.tools.aux_approval_guidance = [
        "Auto-approve gh pr create and git push",
        "Auto-approve npm run test:watch",
    ]

    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as mock_confirm:
        mock_confirm.return_value = {"approved": True}

        # 1. Save new preset with all active conversation rules
        res = await tool_shell_guidance(ctx, action="save_preset", preset="my_project")
        assert isinstance(res, str)
        assert "Created custom shell auto-approval preset `my_project`" in res
        assert "- Auto-approve gh pr create and git push" in res
        assert "- Auto-approve npm run test:watch" in res
        assert mock_confirm.call_args.kwargs.get("force") is True

        # Check saved presets file
        p_file = ctx.config.agent_path / "shell_approval_presets.json"
        assert p_file.exists()
        data = json.loads(p_file.read_text())
        assert "my_project" in data
        assert "- Auto-approve gh pr create and git push" in data["my_project"]

        # 2. Update existing preset with a specific new rule
        res_update = await tool_shell_guidance(
            ctx, action="save_preset", preset="my_project", rule="Auto-approve make check"
        )
        assert isinstance(res_update, str)
        assert "Updated custom shell auto-approval preset `my_project`" in res_update
        assert "- Auto-approve make check" in res_update

        # Verify it merged on disk
        data_updated = json.loads(p_file.read_text())
        assert "- Auto-approve make check" in data_updated["my_project"]
        assert "- Auto-approve gh pr create and git push" in data_updated["my_project"]

        # 3. Verify newly saved preset appears in 'list' and can be enabled
        res_list = await tool_shell_guidance(ctx, action="list")
        assert "**`my_project`**" in res_list

        fresh_ctx = _make_mock_ctx(tmp_path)
        res_enable = await tool_shell_guidance(fresh_ctx, action="enable_preset", preset="my_project")
        assert isinstance(res_enable, str)
        assert "Enabled shell auto-approval preset `my_project` for this conversation" in res_enable
        assert "my_project" in fresh_ctx.tools.active_aux_approval_presets
        guidelines = resolve_aux_approval_guidance(fresh_ctx)
        assert any("gh pr create" in g for g in guidelines)

        # 4. Save preset when target initially exists only in config.shell.aux_approval_presets (#1033 review)
        fresh_ctx.config.shell.aux_approval_presets = {
            "from_config": "Auto-approve make build",
        }
        res_update_config = await tool_shell_guidance(
            fresh_ctx, action="save_preset", preset="from_config", rule="Auto-approve make test"
        )
        assert isinstance(res_update_config, str)
        assert "Updated custom shell auto-approval preset `from_config`" in res_update_config
        # Verify both configured and new rules are present in the disk file
        data_cfg_updated = json.loads(p_file.read_text())
        assert "- Auto-approve make build" in data_cfg_updated["from_config"]
        assert "- Auto-approve make test" in data_cfg_updated["from_config"]


@pytest.mark.asyncio
async def test_tool_shell_guidance_save_preset_validations(tmp_path: Path):
    """Test validations for save_preset (empty name, built-in overwrite, no rules)."""
    ctx = _make_mock_ctx(tmp_path)

    # 1. Missing preset name
    res_no_name = await tool_shell_guidance(ctx, action="save_preset", preset="")
    assert isinstance(res_no_name, ToolResult)
    assert "preset' name is required" in res_no_name.text

    # 2. Cannot overwrite built-in preset
    res_builtin = await tool_shell_guidance(ctx, action="save_preset", preset="developer")
    assert isinstance(res_builtin, ToolResult)
    assert "cannot overwrite built-in preset 'developer'" in res_builtin.text

    # 3. No rules to save
    res_no_rules = await tool_shell_guidance(ctx, action="save_preset", preset="custom_prj")
    assert isinstance(res_no_rules, ToolResult)
    assert "no guidance rules to save" in res_no_rules.text

    # 4. Denied by user confirmation
    ctx.tools.aux_approval_guidance = ["Auto-approve something"]
    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as mock_confirm:
        mock_confirm.return_value = {"approved": False}
        res_denied = await tool_shell_guidance(ctx, action="save_preset", preset="custom_prj")
        assert isinstance(res_denied, ToolResult)
        assert "[error: denied]" in res_denied.text

    # 5. Malformed presets file aborts and does not overwrite existing file
    p_file = ctx.config.agent_path / "shell_approval_presets.json"
    p_file.write_text("NOT VALID JSON")
    res_corrupt = await tool_shell_guidance(ctx, action="save_preset", preset="custom_prj")
    assert isinstance(res_corrupt, ToolResult)
    assert "unreadable or malformed" in res_corrupt.text
    # Verify file was not overwritten with blank catalog
    assert p_file.read_text() == "NOT VALID JSON"

    # 6. Presets file with non-string values is rejected
    p_file.write_text(json.dumps({"bad_entry": None}))
    res_non_string = await tool_shell_guidance(ctx, action="save_preset", preset="custom_prj")
    assert isinstance(res_non_string, ToolResult)
    assert "unreadable or malformed" in res_non_string.text
    assert "must be strings" in res_non_string.text


@pytest.mark.asyncio
async def test_tool_shell_guidance_unattended_denied(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)
    ctx.is_unattended = True

    # List is still allowed on unattended turn
    res_list = await tool_shell_guidance(ctx, action="list")
    assert isinstance(res_list, str)

    # Modification actions are denied
    res_enable = await tool_shell_guidance(ctx, action="enable_preset", preset="developer")
    assert isinstance(res_enable, ToolResult)
    assert "denied on unattended turn" in res_enable.text


def test_suggest_aux_approval_pattern_no_destructive_wildcards():
    # Without guidance: legacy behavior wildcards subcommands
    assert _suggest_aux_approval_pattern("git checkout feature/topic", has_guidance=False) == "git checkout *"

    # With guidance: state-changing commands are cached with EXACT string, never wildcarded
    assert (
        _suggest_aux_approval_pattern("git checkout feature/topic", has_guidance=True) == "git checkout feature/topic"
    )
    assert _suggest_aux_approval_pattern("git add path/to/file.py", has_guidance=True) == "git add path/to/file.py"
    assert _suggest_aux_approval_pattern("git commit -m 'feat'", has_guidance=True) == "git commit -m 'feat'"
    assert _suggest_aux_approval_pattern("find . -name '*.tmp'", has_guidance=True) == "find . -name '*.tmp'"
    assert _suggest_aux_approval_pattern("ruff format file.py", has_guidance=True) == "ruff format file.py"
    assert _suggest_aux_approval_pattern("ruff check src/foo.py -v", has_guidance=True) == "ruff check *"

    # Safe read-only / test commands are still allowed to wildcard
    assert (
        _suggest_aux_approval_pattern("pytest tests/test_foo.py -v", has_guidance=True) == "pytest tests/test_foo.py *"
    )
    assert _suggest_aux_approval_pattern("git status", has_guidance=True) == "git status"
    assert _suggest_aux_approval_pattern("git diff HEAD~1", has_guidance=True) == "git diff *"


def test_env_dict_coercion_for_presets(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SHELL_AUX_APPROVAL_PRESETS", '{"ci": "Auto-approve ci scripts"}')
    cfg = load_sub_config(ShellConfig, json_data={}, env_prefix="SHELL")
    assert isinstance(cfg.aux_approval_presets, dict)
    assert cfg.aux_approval_presets == {"ci": "Auto-approve ci scripts"}

    ctx = _make_mock_ctx(tmp_path)
    ctx.config.shell = cfg
    ctx.config.shell.active_aux_approval_presets = ["ci"]
    guidelines = resolve_aux_approval_guidance(ctx)
    assert "Auto-approve ci scripts" in guidelines


def test_load_guidance_text_no_cwd_leakage(tmp_path: Path, monkeypatch):
    ctx = _make_mock_ctx(tmp_path)
    # Create a file in current working directory
    cwd_file = Path("test_leak_marker.txt")
    try:
        cwd_file.write_text("CWD secret content")
        # Relative path without matching file in agent_path or workspace_path
        # must NOT read from CWD
        result = _load_guidance_text(ctx, "test_leak_marker.txt")
        assert result == "test_leak_marker.txt"
        assert result != "CWD secret content"
    finally:
        if cwd_file.exists():
            cwd_file.unlink()


def test_conversation_manager_persists_guidance_across_turns(tmp_path: Path):
    from decafclaw.conversation_manager import ConversationManager, ConversationState

    cm = MagicMock(spec=ConversationManager)
    cm._restore_per_conv_state = ConversationManager._restore_per_conv_state.__get__(cm)
    cm._save_conversation_state = ConversationManager._save_conversation_state.__get__(cm)

    state = ConversationState(conv_id="test-conv")

    # Turn 1: ctx adds presets and guidance
    ctx1 = _make_mock_ctx(tmp_path)
    ctx1.tools.active_aux_approval_presets = ["developer"]
    ctx1.tools.disabled_aux_approval_presets = ["github"]
    ctx1.tools.aux_approval_guidance = ["Rule 1"]
    ctx1.tools.llm_approved_shell_patterns = ["pytest *"]

    cm._save_conversation_state(state, ctx1)

    assert state.persisted.active_aux_approval_presets == ["developer"]
    assert state.persisted.disabled_aux_approval_presets == ["github"]
    assert state.persisted.aux_approval_guidance == ["Rule 1"]
    assert state.persisted.llm_approved_shell_patterns == ["pytest *"]

    # Turn 2: fresh ctx restores persisted state
    ctx2 = _make_mock_ctx(tmp_path)
    cm._restore_per_conv_state(state, ctx2)

    assert ctx2.tools.active_aux_approval_presets == ["developer"]
    assert ctx2.tools.disabled_aux_approval_presets == ["github"]
    assert ctx2.tools.aux_approval_guidance == ["Rule 1"]
    assert ctx2.tools.llm_approved_shell_patterns == ["pytest *"]


@pytest.mark.asyncio
async def test_tool_shell_guidance_cannot_be_bypassed_by_preapproved(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)
    ctx.tools.preapproved.add("shell_guidance")

    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as mock_confirm:
        mock_confirm.return_value = {"approved": False}

        res = await tool_shell_guidance(ctx, action="enable_preset", preset="developer")
        assert isinstance(res, ToolResult)
        assert "[error: denied]" in res.text
        # Verify request_confirmation was called with force=True
        mock_confirm.assert_called_once()
        assert mock_confirm.call_args.kwargs.get("force") is True


def test_existing_persistent_guidance_file_ignored_and_warned_once(tmp_path: Path, caplog):
    """A pre-existing shell_approval_guidance.json on disk is ignored and produces one warning (#986)."""
    ctx = _make_mock_ctx(tmp_path)
    p_file = ctx.config.agent_path / "shell_approval_guidance.json"
    p_file.write_text(
        json.dumps(
            {
                "active_presets": ["developer"],
                "disabled_presets": [],
                "rules": ["Legacy auto-approve anything"],
            }
        )
    )

    with caplog.at_level(logging.WARNING):
        guidance1 = resolve_aux_approval_guidance(ctx)
        assert guidance1 == []
        assert "Found deprecated" in caplog.text
        assert "shell_approval_guidance.json" in caplog.text

        # Second call produces no duplicate warning
        caplog.clear()
        guidance2 = resolve_aux_approval_guidance(ctx)
        assert guidance2 == []
        assert "Found deprecated" not in caplog.text


def test_conversation_manager_clearing_and_reenabling_across_turns(tmp_path: Path):
    from decafclaw.conversation_manager import ConversationManager, ConversationState

    cm = MagicMock(spec=ConversationManager)
    cm._restore_per_conv_state = ConversationManager._restore_per_conv_state.__get__(cm)
    cm._save_conversation_state = ConversationManager._save_conversation_state.__get__(cm)

    state = ConversationState(conv_id="test-conv")

    # Turn 1: enable developer preset
    ctx1 = _make_mock_ctx(tmp_path)
    ctx1.tools.active_aux_approval_presets = ["developer"]
    cm._save_conversation_state(state, ctx1)
    assert state.persisted.active_aux_approval_presets == ["developer"]

    # Turn 2: restore and clear it (e.g. user disabled it)
    ctx2 = _make_mock_ctx(tmp_path)
    cm._restore_per_conv_state(state, ctx2)
    assert ctx2.tools.active_aux_approval_presets == ["developer"]
    ctx2.tools.active_aux_approval_presets.clear()
    ctx2.tools.disabled_aux_approval_presets.append("developer")
    cm._save_conversation_state(state, ctx2)
    assert state.persisted.active_aux_approval_presets == []
    assert state.persisted.disabled_aux_approval_presets == ["developer"]

    # Turn 3: restore and re-enable it
    ctx3 = _make_mock_ctx(tmp_path)
    cm._restore_per_conv_state(state, ctx3)
    assert ctx3.tools.active_aux_approval_presets == []
    assert ctx3.tools.disabled_aux_approval_presets == ["developer"]
    ctx3.tools.disabled_aux_approval_presets.clear()
    ctx3.tools.active_aux_approval_presets.append("developer")
    cm._save_conversation_state(state, ctx3)
    assert state.persisted.active_aux_approval_presets == ["developer"]
    assert state.persisted.disabled_aux_approval_presets == []

    # Turn 4: restore verifies re-enabled state sticks
    ctx4 = _make_mock_ctx(tmp_path)
    cm._restore_per_conv_state(state, ctx4)
    assert ctx4.tools.active_aux_approval_presets == ["developer"]
    assert ctx4.tools.disabled_aux_approval_presets == []


@pytest.mark.asyncio
async def test_check_shell_approval_feature_branch_push_with_developer_preset(tmp_path: Path):
    from decafclaw.tools.shell_tools import check_shell_approval

    ctx = _make_mock_ctx(tmp_path)
    ctx.config.shell.aux_approval_enabled = True
    ctx.config.shell.active_aux_approval_presets = ["developer"]

    mock_llm = AsyncMock(
        return_value={"content": '{"auto_approve": true, "risk": "low", "reason": "pushing to feature branch"}'}
    )
    ctx.aux_llm = MagicMock(return_value=mock_llm)

    res = await check_shell_approval(ctx, "git push -u origin feat/my-new-feature")
    assert res.get("approved") is True
    assert mock_llm.call_count == 1


@pytest.mark.asyncio
async def test_check_shell_approval_main_push_requires_confirmation(tmp_path: Path):
    from decafclaw.tools.shell_tools import check_shell_approval

    ctx = _make_mock_ctx(tmp_path)
    ctx.config.shell.aux_approval_enabled = True
    ctx.config.shell.active_aux_approval_presets = ["developer"]

    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as mock_confirm:
        mock_confirm.return_value = {"approved": False}
        res = await check_shell_approval(ctx, "git push origin main")
        assert res.get("approved") is False
        assert mock_confirm.call_count == 1


@pytest.mark.asyncio
async def test_exception_rule_added_on_approval_surfaces_in_subsequent_aux_evaluations(tmp_path: Path):
    """End-to-end exception flow:

    1. Command declined by aux reviewer with suggested exception rule.
    2. User confirms with 'Approve + remember why' (add_rule=True).
    3. Rule is added to ctx.tools.aux_approval_guidance.
    4. Subsequent command evaluation receives the added rule in its prompt.
    """
    from decafclaw.tools.shell_tools import check_shell_approval

    ctx = _make_mock_ctx(tmp_path)
    ctx.config.shell.aux_approval_enabled = True

    # 1. First command declined
    mock_llm_decline = AsyncMock(
        return_value={
            "content": json.dumps(
                {
                    "auto_approve": False,
                    "risk": "medium",
                    "reason": "Creating PR modifies remote state",
                    "suggested_rule": "Auto-approve gh pr create in this repository",
                }
            )
        }
    )
    ctx.aux_llm = MagicMock(return_value=mock_llm_decline)

    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as mock_confirm:
        # User approves and remembers the rule (possibly edited)
        mock_confirm.return_value = {
            "approved": True,
            "add_rule": True,
            "rule": "Auto-approve gh pr create in this repository",
        }
        res1 = await check_shell_approval(ctx, "gh pr create --title 'Test PR'")
        assert res1.get("approved") is True
        assert "Auto-approve gh pr create in this repository" in ctx.tools.aux_approval_guidance

    # 2. Second command evaluated by aux LLM: verify prompt contains the exception rule
    captured_messages = []

    async def mock_llm_subsequent(messages):
        captured_messages.extend(messages)
        return {
            "content": json.dumps(
                {
                    "auto_approve": True,
                    "risk": "low",
                    "reason": "Matches exception rule for gh pr create",
                }
            )
        }

    ctx.aux_llm = MagicMock(return_value=mock_llm_subsequent)
    res2 = await check_shell_approval(ctx, "gh pr create --title 'Another PR'")
    assert res2.get("approved") is True
    assert len(captured_messages) == 1
    prompt_text = captured_messages[0]["content"]
    assert "Auto-approve gh pr create in this repository" in prompt_text
