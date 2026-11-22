"""Per-verb read-only contracts (#1641).

Each module owns one verb family's complete contract: its verb list, every
flag that can write/execute/escape, and its predicate. The registry maps a
verb to exactly one owner, so a verb's policy cannot drift across files.

Polarity: an unregistered verb is never read-only. Registering a verb is an
explicit act here, and the ownership test pins the registered set to the
pre-refactor vocabulary so a refactor cannot silently add or drop one.
"""

from pathlib import Path
from typing import Protocol, cast

from . import curl, filesystem, gh, git, jq, search, text


class VerbModule(Protocol):
    """A verb family module: its declared verbs and a single contract."""

    VERBS: frozenset[str]

    def allows(self, verb: str, args: list[str], cwd: Path, workspace: Path) -> bool: ...


_VERB_MODULES = (filesystem, search, text, git, gh, curl, jq)

VERB_OWNERS: dict[str, VerbModule] = {}
for _module in _VERB_MODULES:
    _owner = cast(VerbModule, _module)
    for _verb in _owner.VERBS:
        if _verb in VERB_OWNERS:
            raise RuntimeError(f"read-only verb {_verb!r} is claimed by more than one module")
        VERB_OWNERS[_verb] = _owner
del _module, _owner, _verb

READ_ONLY_VERBS = frozenset(VERB_OWNERS)


def allows(verb: str, args: list[str], cwd: Path, workspace: Path) -> bool:
    """Dispatch one verb to its owner; an unregistered verb is not read-only."""
    owner = VERB_OWNERS.get(verb)
    if owner is None:
        return False
    return owner.allows(verb, args, cwd, workspace)
