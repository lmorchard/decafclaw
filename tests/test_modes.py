from dataclasses import replace

from decafclaw.config import Config
from decafclaw.context import Context, TaskMode
from decafclaw.context_types import (
    SKIP_MODES,
    SKIP_TASK_MODES,
    TASK_MODE_TRAITS,
    UNATTENDED_TASK_MODES,
    UNWATCHED_TASK_MODES,
    ComposerMode,
)
from decafclaw.conversation_manager import TASK_KINDS, TurnKind
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


# -- Canonical mode/skip classification (#1355) -------------------------------


def test_canonical_skip_set_matches_current_behavior():
    """The canonical composer skip set is exactly the non-interactive modes."""
    assert SKIP_MODES == frozenset({ComposerMode.HEARTBEAT, ComposerMode.SCHEDULED, ComposerMode.CHILD_AGENT})
    assert ComposerMode.INTERACTIVE not in SKIP_MODES
    for mode in ComposerMode:
        assert mode.is_skip is (mode in SKIP_MODES)


def test_canonical_task_mode_partitions_match_current_behavior():
    """Derived TaskMode partitions equal the literals still in the call sites."""
    assert SKIP_TASK_MODES == frozenset({TaskMode.HEARTBEAT, TaskMode.SCHEDULED, TaskMode.CHILD_AGENT})
    assert UNATTENDED_TASK_MODES == frozenset({TaskMode.HEARTBEAT, TaskMode.SCHEDULED, TaskMode.BACKGROUND_WAKE})
    assert UNWATCHED_TASK_MODES == frozenset({TaskMode.HEARTBEAT, TaskMode.SCHEDULED, TaskMode.CHILD_AGENT})
    assert TaskMode.INTERACTIVE not in SKIP_TASK_MODES
    assert TaskMode.INTERACTIVE not in UNATTENDED_TASK_MODES
    assert TaskMode.INTERACTIVE not in UNWATCHED_TASK_MODES
    assert set(TASK_MODE_TRAITS) == set(TaskMode)


def test_canonical_sets_agree_with_unmigrated_callsites():
    """Until #1357-#1359 adopt them, the local copies must not drift."""
    from decafclaw.agent import _UNWATCHED_TASK_MODES

    assert _UNWATCHED_TASK_MODES == UNWATCHED_TASK_MODES
    assert Context.UNATTENDED_TASK_MODES == UNATTENDED_TASK_MODES
    # The six inline composer copies use ``is_skip`` (#1356), which is
    # defined from this set.
    assert SKIP_MODES == frozenset(m for m in ComposerMode if m is not ComposerMode.INTERACTIVE)


def test_child_agent_and_background_wake_asymmetry_is_deliberate():
    """child_agent skips context but stays attended; background_wake is the reverse."""
    assert TaskMode.CHILD_AGENT in SKIP_TASK_MODES
    assert TaskMode.CHILD_AGENT not in UNATTENDED_TASK_MODES
    assert TaskMode.BACKGROUND_WAKE in UNATTENDED_TASK_MODES
    assert TaskMode.BACKGROUND_WAKE not in SKIP_TASK_MODES
    # Both run without a live subscriber, but for different reasons: child
    # turns use synthetic conv ids; wake turns reuse the user's conversation.
    assert TaskMode.CHILD_AGENT in UNWATCHED_TASK_MODES
    assert TaskMode.BACKGROUND_WAKE not in UNWATCHED_TASK_MODES


def test_task_mode_traits_map_to_composer_modes():
    """Every TaskMode has one composer mode; only interactive ones skip nothing."""
    for mode in TaskMode:
        traits = TASK_MODE_TRAITS[mode]
        assert traits.composer_mode in ComposerMode
        assert (mode in SKIP_TASK_MODES) is (traits.composer_mode in SKIP_MODES)


def test_turn_kind_task_mode_for_mapping():
    assert TurnKind.task_mode_for(TurnKind.USER) is TaskMode.INTERACTIVE
    assert TurnKind.task_mode_for(TurnKind.WORKFLOW) is TaskMode.INTERACTIVE
    assert TurnKind.task_mode_for(TurnKind.HEARTBEAT_SECTION) is TaskMode.HEARTBEAT
    assert TurnKind.task_mode_for(TurnKind.SCHEDULED_TASK) is TaskMode.SCHEDULED
    assert TurnKind.task_mode_for(TurnKind.CHILD_AGENT) is TaskMode.CHILD_AGENT
    assert TurnKind.task_mode_for(TurnKind.WAKE) is TaskMode.BACKGROUND_WAKE
    assert {TurnKind.task_mode_for(kind) for kind in TASK_KINDS} == {
        TaskMode.HEARTBEAT,
        TaskMode.SCHEDULED,
        TaskMode.CHILD_AGENT,
        TaskMode.BACKGROUND_WAKE,
    }
