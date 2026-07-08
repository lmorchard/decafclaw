"""Unit coverage for the eval runner's per-test ``setup`` overrides.

These are the fields that let a single eval case tweak a slice of the
resolved ``Config`` (agent budget, reflection gate, …) without affecting
the rest of the suite. See ``docs/eval-loop.md`` setup-fields table for
the accepted keys.
"""

from decafclaw.config import Config
from decafclaw.eval.runner import _build_test_config


def _tmp(tmp_path):
    return str(tmp_path)


def test_agent_data_home_and_id_are_always_set(tmp_path):
    """Every test gets an isolated data_home + the sentinel 'eval' agent id
    regardless of what ``setup`` overrides are present."""
    cfg = Config()
    out = _build_test_config(cfg, {"setup": {}}, _tmp(tmp_path))
    assert out.agent.data_home == _tmp(tmp_path)
    assert out.agent.id == "eval"


def test_reflection_enabled_default_inherits_from_config(tmp_path):
    """Without an explicit override, config.reflection.enabled is preserved."""
    cfg = Config()
    # Ambient default is True; flip both ways and confirm each is preserved.
    cfg.reflection.enabled = True
    assert _build_test_config(cfg, {}, _tmp(tmp_path)).reflection.enabled is True
    cfg.reflection.enabled = False
    assert _build_test_config(cfg, {}, _tmp(tmp_path)).reflection.enabled is False


def test_reflection_enabled_false_overrides_config(tmp_path):
    """``setup.reflection_enabled: false`` forces reflection off even when
    the base config has it enabled (the typical case)."""
    cfg = Config()
    cfg.reflection.enabled = True
    out = _build_test_config(
        cfg, {"setup": {"reflection_enabled": False}}, _tmp(tmp_path),
    )
    assert out.reflection.enabled is False
    # Base config is untouched — we returned a modified copy, not a mutation.
    assert cfg.reflection.enabled is True


def test_reflection_enabled_true_overrides_disabled_config(tmp_path):
    """The override works both ways — a suite-wide reflection.enabled=False
    can be opted back into for a specific test."""
    cfg = Config()
    cfg.reflection.enabled = False
    out = _build_test_config(
        cfg, {"setup": {"reflection_enabled": True}}, _tmp(tmp_path),
    )
    assert out.reflection.enabled is True


def test_max_tool_iterations_override(tmp_path):
    """Regression: the existing max_tool_iterations override still applies
    through the extracted helper (this shape shipped in #448)."""
    cfg = Config()
    baseline = cfg.agent.max_tool_iterations
    out = _build_test_config(
        cfg, {"setup": {"max_tool_iterations": 3}}, _tmp(tmp_path),
    )
    assert out.agent.max_tool_iterations == 3
    # Absent override → the config default flows through unchanged.
    unset = _build_test_config(cfg, {}, _tmp(tmp_path))
    assert unset.agent.max_tool_iterations == baseline


def test_overrides_compose_independently(tmp_path):
    """Setting one override doesn't clobber the other — both nested replaces
    happen in a single pass on top of the base config."""
    cfg = Config()
    cfg.reflection.enabled = True
    out = _build_test_config(
        cfg,
        {"setup": {"reflection_enabled": False, "max_tool_iterations": 2}},
        _tmp(tmp_path),
    )
    assert out.reflection.enabled is False
    assert out.agent.max_tool_iterations == 2
