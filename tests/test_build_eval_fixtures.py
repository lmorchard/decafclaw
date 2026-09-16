"""Guards the config redirect in scripts/build-eval-fixtures.py (#856).

The script used to do `config.data_home = tmp; config.agent_id = "fixture"`.
Neither is a field on Config — they live on `config.agent` — and Config is not
frozen, so those assignments silently created undeclared attributes that
nothing read. The redirect was a no-op: `index_entry` wrote to the real
`data/decafclaw/workspace/embeddings.db`, polluting the live embedding index,
and the subsequent `shutil.move` then failed because its hand-assembled
pickup path had the components in the wrong order.

The module is loaded by path because its filename is hyphenated.
"""

import dataclasses
import importlib.util
import pathlib

import pytest

from decafclaw.config import Config

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "build-eval-fixtures.py"


@pytest.fixture(scope="module")
def script_module():
    spec = importlib.util.spec_from_file_location("build_eval_fixtures", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fixture_config_redirects_workspace_into_tmp(script_module, tmp_path):
    """The redirect must actually move workspace_path under the temp dir."""
    cfg = Config()
    fixture_cfg = script_module.fixture_config(cfg, str(tmp_path))

    assert fixture_cfg.workspace_path == tmp_path / "fixture" / "workspace"
    assert fixture_cfg.agent.data_home == str(tmp_path)
    assert fixture_cfg.agent.id == "fixture"


def test_fixture_config_leaves_the_original_untouched(script_module, tmp_path):
    """dataclasses.replace must not mutate the caller's config."""
    cfg = Config()
    before = cfg.workspace_path

    script_module.fixture_config(cfg, str(tmp_path))

    assert cfg.workspace_path == before
    assert str(tmp_path) not in str(cfg.workspace_path)


def test_redirect_cannot_be_done_by_attribute_assignment(tmp_path):
    """Pins the root cause, so a future 'simplification' cannot reintroduce it.

    data_home / agent_id are not Config fields; assigning them is a silent
    no-op rather than an error.
    """
    cfg = Config()
    field_names = {f.name for f in dataclasses.fields(Config)}
    assert "data_home" not in field_names
    assert "agent_id" not in field_names

    cfg.data_home = str(tmp_path)  # type: ignore[attr-defined]
    assert str(tmp_path) not in str(cfg.workspace_path)
