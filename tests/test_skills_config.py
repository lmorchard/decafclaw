"""Tests for skills configuration resolution: always-loaded gates and repo env aliases (split from #1419)."""

import sys

from decafclaw.config import load_sub_config
from decafclaw.skills import discover_skills
from decafclaw.tools.skill_tools import _load_native_tools
from tests.skill_helpers import _write_skill


def test_discover_includes_garden(config):
    """The bundled garden skill still loads end-to-end (#1301).

    The loader execs tools.py in a fresh module object that can leave
    sys.modules (a failed re-import pops it, or a same-named skill shadows
    it); with PEP 563 string annotations, get_type_hints in load_sub_config
    then raises NameError on any non-builtin annotation and the skill is
    silently dropped from the catalog. The import itself is gated by
    check-skill-annotations; this pins the load path it protects.
    """
    skills = discover_skills(config)
    by_name = {s.name: s for s in skills}
    assert "garden" in by_name, f"bundled garden skill not discovered; got {sorted(by_name)}"
    g = by_name["garden"]
    assert g.trust_tier == "bundled"
    assert g.has_native_tools is True

    tools, tool_defs, module = _load_native_tools(g)
    expected = {"vault_recompute_importance", "vault_reorganize_folders"}
    assert expected <= set(tools), f"garden tools missing after load: {sorted(tools)}"
    assert tool_defs, "garden should export TOOL_DEFINITIONS alongside TOOLS"

    # The documented failure state: the loader's sys.modules registration is
    # gone. Restore it afterwards so no later test sees a half-loaded module.
    module_name = f"decafclaw_skill_{g.name}"
    saved_module = sys.modules.get(module_name)
    sys.modules.pop(module_name, None)
    try:
        skill_config = load_sub_config(module.SkillConfig, {}, "SKILLS_GARDEN")
    finally:
        if saved_module is not None:
            sys.modules[module_name] = saved_module
    assert isinstance(skill_config, module.SkillConfig)


def test_skills_always_loaded_config_promotes_extra_skill(tmp_path, config):
    """config.skills_always_loaded promotes a trusted-tier skill into the
    always-loaded set without editing its frontmatter."""
    extra = tmp_path / "external"
    _write_skill(
        extra / "promoted",
        "name: promoted\ndescription: Default on-demand contrib.",
    )
    config.extra_skill_paths = [str(extra)]
    config.skills_always_loaded = ["promoted"]
    skills = discover_skills(config)
    matching = [s for s in skills if s.name == "promoted"]
    assert matching[0].always_loaded is True


def test_skills_always_loaded_config_denied_for_workspace(config, caplog):
    """config.skills_always_loaded entry for a workspace skill is rejected
    with a warning."""
    _write_skill(
        config.workspace_path / "skills" / "ws-promo",
        "name: ws-promo\ndescription: Workspace.",
    )
    config.skills_always_loaded = ["ws-promo"]
    with caplog.at_level("WARNING"):
        skills = discover_skills(config)
    matching = [s for s in skills if s.name == "ws-promo"]
    assert matching[0].always_loaded is False
    assert any("workspace skill" in r.message and "always-load" in r.message for r in caplog.records)


def test_decafclaw_repo_auto_populated_when_unset(tmp_path, config, monkeypatch):
    """`$DECAFCLAW_REPO` resolves against the auto-detected source root
    when the env var is unset — letting `extra_skill_paths` entries
    work without a manual `.env` setting."""
    monkeypatch.delenv("DECAFCLAW_REPO", raising=False)
    # The detected root is the repo we're running tests from; place a
    # marker skill there relative to its `contrib/skills/` and verify
    # discovery picks it up via `$DECAFCLAW_REPO`.
    from decafclaw.skills import _AUTO_REPO_ROOT

    assert _AUTO_REPO_ROOT is not None, "test must run from a source checkout"
    # Use a real existing contrib skill as the target so we don't have
    # to write into the repo from a test.
    config.extra_skill_paths = ["$DECAFCLAW_REPO/contrib/skills/writing-clearly"]
    skills = discover_skills(config)
    assert any(s.name == "writing-clearly" for s in skills)


def test_decafclaw_repo_explicit_env_wins(tmp_path, config, monkeypatch):
    """An explicit `$DECAFCLAW_REPO` env value overrides auto-detection."""
    _write_skill(
        tmp_path / "elsewhere" / "contrib" / "skills" / "elsewhere-skill",
        "name: elsewhere-skill\ndescription: Marker skill.",
    )
    monkeypatch.setenv("DECAFCLAW_REPO", str(tmp_path / "elsewhere"))
    config.extra_skill_paths = ["$DECAFCLAW_REPO/contrib/skills/elsewhere-skill"]
    skills = discover_skills(config)
    assert any(s.name == "elsewhere-skill" for s in skills)


def test_contrib_shorthand_auto_populated(tmp_path, config, monkeypatch):
    """`$CONTRIB` auto-resolves to `$DECAFCLAW_REPO/contrib` so the
    shorter form works in `extra_skill_paths`."""
    monkeypatch.delenv("DECAFCLAW_REPO", raising=False)
    monkeypatch.delenv("CONTRIB", raising=False)
    config.extra_skill_paths = ["$CONTRIB/skills/writing-clearly"]
    skills = discover_skills(config)
    assert any(s.name == "writing-clearly" for s in skills)


def test_contrib_follows_explicit_decafclaw_repo(tmp_path, config, monkeypatch):
    """When the user overrides `$DECAFCLAW_REPO`, the derived `$CONTRIB`
    follows — pointing at the new checkout's contrib/ rather than the
    auto-detected one."""
    _write_skill(
        tmp_path / "alt" / "contrib" / "skills" / "alt-skill",
        "name: alt-skill\ndescription: Alt skill.",
    )
    monkeypatch.setenv("DECAFCLAW_REPO", str(tmp_path / "alt"))
    monkeypatch.delenv("CONTRIB", raising=False)
    config.extra_skill_paths = ["$CONTRIB/skills/alt-skill"]
    skills = discover_skills(config)
    assert any(s.name == "alt-skill" for s in skills)


def test_skills_always_loaded_config_field_default(config):
    """config.skills_always_loaded defaults to empty list."""
    assert config.skills_always_loaded == []


def test_contrib_explicit_env_wins(tmp_path, config, monkeypatch):
    """An explicit `$CONTRIB` env value wins over the derived default."""
    _write_skill(
        tmp_path / "myown" / "skills" / "own-skill",
        "name: own-skill\ndescription: Own skill.",
    )
    monkeypatch.setenv("CONTRIB", str(tmp_path / "myown"))
    config.extra_skill_paths = ["$CONTRIB/skills/own-skill"]
    skills = discover_skills(config)
    assert any(s.name == "own-skill" for s in skills)
