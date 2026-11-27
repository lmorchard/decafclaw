"""Tests for the shared SkillState holder (#1373).

The holder centralizes the per-skill boilerplate: a mutable module global,
the ``init()`` assignment (no ``global`` statement), and the None-guard
fallback to a default. These tests pin each behavior so a regression in any
one of the three shows up here, not in a skill's config tests.
"""

from dataclasses import dataclass, field

import pytest

from decafclaw.skill_state import SkillState, skill_state


@dataclass
class _Cfg:
    window_days: int = field(default=7)
    name: str = "default"


def test_get_falls_back_to_default_before_init():
    state = skill_state(_Cfg())
    assert state.get() is not None
    assert state.get().window_days == 7


def test_init_stores_value_and_get_returns_it():
    state = skill_state(_Cfg())
    custom = _Cfg(window_days=3)
    state.init(custom)
    assert state.get() is custom


def test_attribute_access_delegates_to_active_value():
    """Read sites write ``state.window_days`` — no None guard, no .get()."""
    state = skill_state(_Cfg())
    assert state.window_days == 7  # default before init
    state.init(_Cfg(window_days=3))
    assert state.window_days == 3  # stored value after init


def test_seeded_value_is_active_from_creation():
    custom = _Cfg(name="seeded")
    state = skill_state(_Cfg(), custom)
    assert state.get() is custom
    assert state.name == "seeded"


def test_missing_attribute_raises_attribute_error():
    """Delegation must not invent fields: a typo fails loudly, like it would
    on the config dataclass itself."""
    state = skill_state(_Cfg())
    with pytest.raises(AttributeError):
        _ = state.nonexistent_field  # noqa: B018


def test_holders_are_independent():
    """Each skill module instance gets its own holder; one init must not leak
    into another (the loader re-imports tools.py per activation)."""
    a = skill_state(_Cfg())
    b = skill_state(_Cfg())
    a.init(_Cfg(window_days=1))
    assert b.window_days == 7


def test_none_default_round_trips():
    """tabstack holds a client whose default is None: get() returns None until
    init, and the fail-fast raise in _get_client still fires."""
    state = SkillState[object | None](None)
    assert state.get() is None
    sentinel = object()
    state.init(sentinel)
    assert state.get() is sentinel
