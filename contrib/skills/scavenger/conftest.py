"""Keep scavenger tests away from the operator's real configuration state."""

import os

import pytest

from contrib.skills.scavenger import scavenger as sc


def host_state_files():
    """Return absolute ``*_FILE`` module constants that point at operator state."""
    return {
        name: value
        for name, value in vars(sc).items()
        if name.endswith("_FILE") and isinstance(value, str) and os.path.isabs(value)
    }


@pytest.fixture(autouse=True)
def isolate_host_state(tmp_path, monkeypatch):
    for name in host_state_files():
        monkeypatch.setattr(sc, name, str(tmp_path / f"{name.lower()}.json"))
    return host_state_files()
