"""Guard that scavenger tests never write to the operator's real state files."""

import os

from contrib.skills.scavenger import scavenger as sc


def test_host_state_files_are_redirected(isolate_host_state, tmp_path):
    assert "LEASES_FILE" in isolate_host_state
    assert "ENV_VARS_FILE" in isolate_host_state
    for name, path in isolate_host_state.items():
        assert os.path.isabs(getattr(sc, name))
        assert getattr(sc, name) == path, f"{name} is not isolated"
        assert path.startswith(str(tmp_path) + os.sep), f"{name} points outside tmp_path"
