from dataclasses import replace

from decafclaw.config import Config
from decafclaw.modes import DEFAULT_MODES, SessionMode, get_all_modes, resolve_active_mode


def test_default_modes_defined():
    assert "default" in DEFAULT_MODES
    assert "dev" in DEFAULT_MODES
    assert "research" in DEFAULT_MODES
    assert "admin" in DEFAULT_MODES

    dev = DEFAULT_MODES["dev"]
    assert "developer" in dev.presets
    assert "github" in dev.presets
    assert "workspace_diff" in dev.promoted_tools
    assert "workspace_search" in dev.promoted_tools
    assert "workspace_glob" in dev.promoted_tools

    research = DEFAULT_MODES["research"]
    assert "web_fetch" in research.promoted_tools
    assert "vault_search" in research.promoted_tools

    admin = DEFAULT_MODES["admin"]
    assert "admin_read" in admin.promoted_tools
    assert "admin_list" in admin.promoted_tools


def test_resolve_active_mode_defaults():
    config = Config()
    mode = resolve_active_mode(config, "")
    assert mode.name == "default"

    unknown = resolve_active_mode(config, "nonexistent")
    assert unknown.name == "default"

    dev = resolve_active_mode(config, "dev")
    assert dev.name == "dev"
    assert dev.presets == ["developer", "github"]


def test_config_custom_modes_override_and_extend():
    custom_mode = SessionMode(
        name="pair",
        description="Pair programming workflow",
        presets=["developer"],
        promoted_tools=["notes_read"],
    )
    overridden_dev = SessionMode(
        name="dev",
        description="Overridden dev",
        presets=["developer"],
        promoted_tools=["shell"],
    )
    config = Config(modes={"pair": custom_mode, "dev": overridden_dev})

    all_modes = get_all_modes(config)
    assert "pair" in all_modes
    assert all_modes["pair"].promoted_tools == ["notes_read"]
    assert all_modes["dev"].description == "Overridden dev"

    resolved = resolve_active_mode(config, "pair")
    assert resolved.name == "pair"
    assert resolved.presets == ["developer"]


def test_custom_mode_name_is_reserved():
    reserved_mode = SessionMode(
        name="custom",
        description="Illegal custom mode name",
        presets=["developer"],
        promoted_tools=["shell"],
    )
    config = Config(modes={"custom": reserved_mode})
    all_modes = get_all_modes(config)
    assert "custom" not in all_modes


def test_resolve_active_mode_fallback_uses_configured_default():
    custom_default = SessionMode(
        name="default",
        description="Custom default mode",
        presets=["developer"],
        promoted_tools=["notes_read"],
    )
    config = Config(modes={"default": custom_default})

    fallback_empty = resolve_active_mode(config, "")
    assert fallback_empty.description == "Custom default mode"
    assert fallback_empty.presets == ["developer"]

    fallback_unknown = resolve_active_mode(config, "unknown-mode")
    assert fallback_unknown.description == "Custom default mode"
    assert fallback_unknown.presets == ["developer"]
