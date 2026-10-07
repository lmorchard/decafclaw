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
        assert "Enabled shell auto-approval preset `developer` for this conversation" in res
        assert "developer" in ctx.tools.active_aux_approval_presets

        # Verify prompt includes it
        prompt = build_aux_approval_prompt(ctx, "pytest")
        assert "pytest" in prompt

        # Disable
        res_dis = await tool_shell_guidance(ctx, action="disable_preset", preset="developer")
        assert "Disabled shell auto-approval preset `developer` for this conversation" in res_dis
        assert "developer" not in ctx.tools.active_aux_approval_presets
        assert "developer" in ctx.tools.disabled_aux_approval_presets

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
async def test_tool_shell_guidance_persistent_preset(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)

    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as mock_confirm:
        mock_confirm.return_value = {"approved": True}

        res = await tool_shell_guidance(ctx, action="enable_preset", preset="developer", persistent=True)
        assert "persistently (across all conversations)" in res

        # Check persistent file exists
        p_file = ctx.config.agent_path / "shell_approval_guidance.json"
        assert p_file.exists()
        import json

        data = json.loads(p_file.read_text())
        assert "developer" in data["active_presets"]

        # Disable persistent
        res_dis = await tool_shell_guidance(ctx, action="disable_preset", preset="developer", persistent=True)
        assert "Disabled" in res_dis
        data_after = json.loads(p_file.read_text())
        assert "developer" not in data_after["active_presets"]


@pytest.mark.asyncio
async def test_tool_shell_guidance_add_and_remove_rule(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)

    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as mock_confirm:
        mock_confirm.return_value = {"approved": True}

        # Session rule
        res = await tool_shell_guidance(ctx, action="add_rule", rule="Auto-approve pytest tests/")
        assert "Added shell auto-approval rule for this conversation" in res
        assert "Auto-approve pytest tests/" in ctx.tools.aux_approval_guidance

        res_rem = await tool_shell_guidance(ctx, action="remove_rule", rule="Auto-approve pytest tests/")
        assert "Removed shell auto-approval rule for this conversation" in res_rem
        assert "Auto-approve pytest tests/" not in ctx.tools.aux_approval_guidance

        # Persistent rule
        res_p = await tool_shell_guidance(ctx, action="add_rule", rule="Persistent rule 1", persistent=True)
        assert "persistently" in res_p
        p_file = ctx.config.agent_path / "shell_approval_guidance.json"
        import json

        data = json.loads(p_file.read_text())
        assert "Persistent rule 1" in data["rules"]

        res_p_rem = await tool_shell_guidance(ctx, action="remove_rule", rule="Persistent rule 1", persistent=True)
        assert "Removed" in res_p_rem
        data_after = json.loads(p_file.read_text())
        assert "Persistent rule 1" not in data_after["rules"]


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
