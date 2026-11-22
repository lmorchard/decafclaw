"""Externalized shell approval judge prompt (#1641 review).

The prompt is markdown: a bundled default plus a per-agent override in admin
data. These tests cover template loading, single-pass substitution, the policy
block, and the direction line.
"""

from pathlib import Path
from types import SimpleNamespace

from decafclaw.activity import Activity
from decafclaw.shell_judge import (
    AUX_RISK_CATEGORIES,
    activity_direction,
    render_aux_approval_prompt,
    template_path,
)


def _config(tmp_path: Path):
    return SimpleNamespace(agent_path=tmp_path / "agent")


def _render(config, *, command="make build", activity=None, guidelines=None):
    return render_aux_approval_prompt(
        config,
        command=command,
        working_dir="/workspace/decafclaw",
        stated_purpose="build it",
        activity=activity,
        native_tool_index="File reading/inspection: workspace_read",
        guidelines=guidelines or [],
    )


def test_bundled_template_renders_without_leftover_placeholders(tmp_path):
    config = _config(tmp_path)
    prompt = _render(config)
    assert "{{" not in prompt
    assert "Command: make build" in prompt
    assert "Working Directory: /workspace/decafclaw" in prompt
    assert "Stated Purpose: build it" in prompt
    assert "Conversation Activity: (not derived)" in prompt
    assert "Native tool index:\nFile reading/inspection: workspace_read" in prompt
    assert '"risk_category"' in prompt
    assert "Decline auto-approval only for a concrete risk" in prompt


def test_agent_override_replaces_the_bundled_template(tmp_path):
    config = _config(tmp_path)
    config.agent_path.mkdir(parents=True)
    (config.agent_path / "SHELL_APPROVAL.md").write_text(
        "CUSTOM judge for {{COMMAND}} in {{WORKING_DIR}}\n{{POLICY_BLOCK}}\n"
    )

    prompt = _render(config, command="touch marker")

    assert prompt.startswith("CUSTOM judge for touch marker in /workspace/decafclaw")
    assert "Policy: auto-approve" in prompt
    assert "You are evaluating a shell command" not in prompt


def test_broken_override_falls_back_to_bundled(tmp_path):
    config = _config(tmp_path)
    template_path(config).mkdir(parents=True)  # exists, but is not readable as a file

    prompt = _render(config)

    assert "You are evaluating a shell command for execution." in prompt


def test_substitution_is_single_pass(tmp_path):
    config = _config(tmp_path)
    prompt = _render(config, command="echo {{WORKING_DIR}}")
    assert "Command: echo {{WORKING_DIR}}" in prompt
    assert "Command: echo /workspace/decafclaw" not in prompt


def test_policy_block_with_guidance_lists_rules(tmp_path):
    prompt = _render(_config(tmp_path), guidelines=["Auto-approve make test."])
    assert "Additional Approval Guidelines:" in prompt
    assert "- Auto-approve make test." in prompt
    assert "they do not override a concrete risk category" in prompt


def test_activity_direction_names_state():
    assert activity_direction(None) == "Conversation Activity: (not derived)"
    assert activity_direction(Activity(domain="homelab", established=True)) == (
        "Conversation Activity: homelab (established earlier in this conversation)"
    )
    assert activity_direction(Activity(domain="blog", established=False)) == (
        "Conversation Activity: blog (new to this conversation)"
    )


def test_risk_category_set_is_the_prompt_contract():
    assert AUX_RISK_CATEGORIES == {
        "data_loss",
        "exfiltration",
        "privilege_escalation",
        "outside_workspace",
        "irreversible",
    }


def test_build_aux_approval_prompt_uses_override(ctx):
    ctx.config.agent_path.mkdir(parents=True, exist_ok=True)
    (ctx.config.agent_path / "SHELL_APPROVAL.md").write_text("OVERRIDE {{COMMAND}}")
    from decafclaw.tools.shell_tools import build_aux_approval_prompt

    assert build_aux_approval_prompt(ctx, "make build") == "OVERRIDE make build"
