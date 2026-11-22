from pathlib import Path
from unittest.mock import MagicMock

from decafclaw.config_types import ShellConfig
from decafclaw.context import Context, SkillState, TaskMode, ToolState
from decafclaw.context_types import ComposerState
from decafclaw.tools.shell_tools import (
    _suggest_aux_approval_pattern,
    build_aux_approval_prompt,
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
    ctx.composer = ComposerState()
    ctx.skip_vault_retrieval = False
    ctx.is_unattended = False
    ctx.task_mode = TaskMode.INTERACTIVE
    ctx.skip_archive = True
    return ctx


def test_build_prompt_default_strict(tmp_path: Path):
    ctx = _make_mock_ctx(tmp_path)
    prompt = build_aux_approval_prompt(ctx, "ls -la")

    assert "Decline auto-approval only for a concrete risk" in prompt
    assert "Native tools are advisory" in prompt
    assert "Additional Approval Guidelines:" not in prompt
    assert '"risk_category"' in prompt
    # #1684: the judge no longer proposes per-conversation exception rules.
    assert "suggested_rule" not in prompt


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
