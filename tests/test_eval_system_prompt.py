"""The full-agent eval runner must assemble a real system prompt (#670).

Before this guard, ``config.system_prompt`` was only ever set by
``decafclaw/__init__.py`` (the app entry point). The eval CLI calls
``load_config()`` + ``run_eval()`` directly, so every eval turn ran with a
zero-length system message: no SOUL.md, no AGENT.md, no <skill_catalog>, and
no always-loaded skill bodies. Measured effect on tool routing was 1/15 vs
10/10 — see docs/dev-sessions/2026-07-24-1728-670-notes-vs-vault-harness-fidelity/research.md.

No LLM calls: ``run_agent_turn`` is patched at the seam.
"""

from unittest.mock import patch

import pytest

from decafclaw.config import Config
from decafclaw.eval.runner import _build_test_config, run_test
from decafclaw.media import ToolResult


@pytest.fixture
def captured_turn():
    """Patch run_agent_turn; record the ctx it was handed."""
    seen = {}

    async def _fake_turn(ctx, user_message, history, *args, **kwargs):
        seen["ctx"] = ctx
        seen["config"] = ctx.config
        history.append({"role": "assistant", "content": "ok"})
        return ToolResult(text="ok")

    with patch("decafclaw.eval.runner.run_agent_turn", _fake_turn):
        yield seen


@pytest.mark.asyncio
async def test_system_prompt_is_assembled_before_the_turn(tmp_path, captured_turn):
    config = _build_test_config(Config(), {"setup": {}}, str(tmp_path))
    assert config.system_prompt == "", "precondition: starts empty"

    await run_test(config, {"name": "t", "input": "hello"})

    prompt = captured_turn["config"].system_prompt
    assert prompt, "eval turn ran with an empty system prompt (#670)"


@pytest.mark.asyncio
async def test_prompt_carries_the_sections_production_gets(tmp_path, captured_turn):
    """Non-empty isn't enough — the always-loaded skill bodies are the part
    #670 turned on, so assert the sections individually."""
    config = _build_test_config(Config(), {"setup": {}}, str(tmp_path))
    await run_test(config, {"name": "t", "input": "hello"})
    prompt = captured_turn["config"].system_prompt

    assert "<skill_catalog>" in prompt
    assert "<loaded_skills>" in prompt
    # The vault skill is always-loaded and its body is what documents
    # vault_journal_append — the specific gap #670 surfaced.
    assert '<skill name="vault">' in prompt


def test_sandbox_clears_extra_skill_paths(tmp_path):
    """`load_system_prompt` runs `discover_skills`, which reads
    `extra_skill_paths`. Those live outside data_home, so the tmp sandbox does
    not reach them — without clearing, a developer's ~/.agents/skills lands in
    the <skill_catalog> of every eval prompt and results stop being
    reproducible across machines."""
    cfg = Config()
    cfg.extra_skill_paths = ["~/.agents/skills", "/somewhere/else"]

    out = _build_test_config(cfg, {"setup": {}}, str(tmp_path))

    assert out.extra_skill_paths == []


def test_config_overrides_cannot_restore_extra_skill_paths(tmp_path):
    """Sandbox fields are applied last, so a case can't opt itself back into
    machine-local skills — symmetric with data_home / agent.id."""
    out = _build_test_config(
        Config(),
        {"setup": {"config_overrides": {"extra_skill_paths": ["/evil"]}}},
        str(tmp_path),
    )

    assert out.extra_skill_paths == []
    assert out.agent.data_home == str(tmp_path)
    assert out.agent.id == "eval"


@pytest.mark.asyncio
async def test_prompt_excludes_extra_tier_skills(tmp_path, captured_turn):
    """End-to-end: the assembled prompt carries the bundled always-loaded
    skill body but no extra-tier catalog entries."""
    cfg = Config()
    cfg.extra_skill_paths = ["~/.agents/skills"]
    config = _build_test_config(cfg, {"setup": {}}, str(tmp_path))

    await run_test(config, {"name": "t", "input": "hello"})
    prompt = captured_turn["config"].system_prompt

    assert '<skill name="vault">' in prompt
    assert captured_turn["config"].extra_skill_paths == []
    # Every discovered skill must come from the bundled tree.
    tiers = {s.trust_tier for s in captured_turn["config"].discovered_skills}
    assert tiers == {"bundled"}, f"non-bundled skills leaked in: {tiers}"


@pytest.mark.asyncio
async def test_explicit_system_prompt_is_not_clobbered(tmp_path, captured_turn):
    """The assembly is guarded on falsiness so a SYSTEM_PROMPT env override
    (config.py reads it into config.system_prompt) still wins. An unconditional
    assignment would silently discard it."""
    config = _build_test_config(Config(), {"setup": {}}, str(tmp_path))
    config.system_prompt = "PRESET"

    await run_test(config, {"name": "t", "input": "hello"})

    assert captured_turn["config"].system_prompt == "PRESET"


@pytest.mark.asyncio
async def test_skill_tool_owners_still_populated(tmp_path, captured_turn):
    """load_system_prompt also returns discovered_skills, which would skip the
    `if not config.discovered_skills` branch and leave skill_tool_owners empty —
    silently breaking /foo command dispatch in evals."""
    config = _build_test_config(Config(), {"setup": {}}, str(tmp_path))
    await run_test(config, {"name": "t", "input": "hello"})

    ran_with = captured_turn["config"]
    assert ran_with.discovered_skills
    assert ran_with.skill_tool_owners
