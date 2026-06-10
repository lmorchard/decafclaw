from pathlib import Path
from types import SimpleNamespace

from decafclaw.workflow.paths import workflow_dir, workflow_path


def _cfg(tmp_path):
    return SimpleNamespace(workspace_path=tmp_path)


def test_workflow_path_is_in_conv_subdirectory(tmp_path):
    cfg = _cfg(tmp_path)
    assert workflow_path(cfg, "abc123") == (
        tmp_path / "conversations" / "abc123" / "workflow.json"
    )


def test_workflow_dir_is_created(tmp_path):
    cfg = _cfg(tmp_path)
    d = workflow_dir(cfg, "abc123", create=True)
    assert d.is_dir()
    assert d == tmp_path / "conversations" / "abc123"


def test_path_sandboxed_against_traversal(tmp_path):
    cfg = _cfg(tmp_path)
    p = workflow_path(cfg, "../../etc/passwd")
    base = (tmp_path / "conversations").resolve()
    assert p.resolve().is_relative_to(base)


def test_empty_conv_id_falls_back(tmp_path):
    cfg = _cfg(tmp_path)
    p = workflow_path(cfg, "")
    assert p.name == "workflow.json"
    assert (tmp_path / "conversations").resolve() in p.resolve().parents


def test_single_dot_conv_id_falls_back(tmp_path):
    cfg = _cfg(tmp_path)
    p = workflow_path(cfg, ".")
    base = (tmp_path / "conversations").resolve()
    assert p.resolve().is_relative_to(base)
    # must land in a subdirectory, not directly in base
    assert p.resolve().parent != base
