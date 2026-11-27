"""Shared state holder for bundled-skill module globals (#1373).

Bundled skills define a ``SkillConfig`` dataclass plus an
``init(config, skill_config)`` hook that the skill loader calls on activation.
Before this helper each skill hand-rolled its own mutable module global, a
``global`` statement in ``init``, and inconsistent None-guard fallbacks at
every read site (some hardcoded the default, some used ``getattr``).

A skill declares one holder — ``_skill_config = skill_state(SkillConfig())``:
the dataclass field defaults are the fallback. ``init()`` stores the resolved
value, and reads — ``get()`` or plain attribute access — fall back to the
default until then, so read sites need no None guards.

The loader imports a fresh module object per activation
(``_import_tools_module``), so each activation gets independent state.
"""

from typing import Any, Generic, TypeVar

T = TypeVar("T")


class SkillState(Generic[T]):
    """Holds one mutable module-level value with a default fallback.

    ``get()`` returns the stored value once ``init()`` has run, otherwise
    ``default``. Attribute access on the holder (``state.window_days``)
    delegates to ``get()`` so read sites can use the config's fields directly.

    The holder deliberately has no custom ``__repr__``: a skill config may
    carry secret fields, and the default object repr keeps them out of logs.
    """

    def __init__(self, default: T) -> None:
        self._default = default
        self._value: T | None = None

    def get(self) -> T:
        """Return the active value, falling back to ``default``."""
        return self._value if self._value is not None else self._default

    def init(self, value: T) -> None:
        """Store the active value (called from the skill's ``init`` hook)."""
        self._value = value

    def __getattr__(self, name: str) -> Any:
        # Delegate undeclared attribute access to the active value so read
        # sites can write ``_skill_config.window_days`` without a None guard.
        if name in ("_default", "_value"):
            raise AttributeError(name)  # internals missing — never recurse
        return getattr(self.get(), name)


def skill_state(default: T, value: T | None = None) -> SkillState[T]:
    """Create a ``SkillState`` holder, optionally pre-seeded with ``value``."""
    state = SkillState(default)
    if value is not None:
        state.init(value)
    return state
