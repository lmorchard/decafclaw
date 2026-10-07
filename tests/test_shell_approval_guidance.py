from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.config_types import ShellConfig
from decafclaw.context import Context, ToolState
from decafclaw.security_monitor import SecurityDecision, SecurityStatus
from decafclaw.tools.shell_tools import (
    DEFAULT_AUX_APPROVAL_PRESETS,
    build_aux_approval_prompt,
    check_shell_approval,
    resolve_aux_approval_guidance,
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
    ctx.is_unattended = False
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
