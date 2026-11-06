from pathlib import Path
from types import SimpleNamespace

from decafclaw.workflow.paths import workflow_dir, workflow_path


def _cfg(tmp_path):
    return SimpleNamespace(workspace_path=tmp_path)


def test_workflow_path_is_in_conv_subdirectory(tmp_path):
    cfg = _cfg(tmp_path)
    # #1112 review: a brand-new conversation resolves under the session root.
    assert workflow_path(cfg, "abc123") == (tmp_path / "sessions" / "abc123" / "workflow.json")


def test_workflow_path_legacy_fallback_when_resident_there(tmp_path):
    # #1112: unmigrated conversations stay pinned to the legacy root.
    cfg = _cfg(tmp_path)
    convdir = tmp_path / "conversations" / "abc123"
    convdir.mkdir(parents=True)
    assert workflow_path(cfg, "abc123") == convdir / "workflow.json"


def test_workflow_dir_is_created(tmp_path):
    cfg = _cfg(tmp_path)
    d = workflow_dir(cfg, "abc123", create=True)
    assert d.is_dir()
    # #1112: new conversations create under the session-scoped root.
    assert d == tmp_path / "sessions" / "abc123"


def test_workflow_path_prefers_session_layout_when_present(tmp_path):
    # #1112: once the session-scoped dir exists, workflow paths resolve there.
    cfg = _cfg(tmp_path)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    convdir = sessions / "abc123"
    convdir.mkdir()
    (convdir / "workflow.json").write_text("{}")
    assert workflow_path(cfg, "abc123") == convdir / "workflow.json"


def test_path_sandboxed_against_traversal(tmp_path):
    cfg = _cfg(tmp_path)
    p = workflow_path(cfg, "../../etc/passwd")
    base = (tmp_path / "sessions").resolve()
    assert p.resolve().is_relative_to(base)


def test_empty_conv_id_falls_back(tmp_path):
    cfg = _cfg(tmp_path)
    p = workflow_path(cfg, "")
    assert p.name == "workflow.json"
    assert (tmp_path / "sessions").resolve() in p.resolve().parents


def test_single_dot_conv_id_falls_back(tmp_path):
    cfg = _cfg(tmp_path)
    p = workflow_path(cfg, ".")
    base = (tmp_path / "sessions").resolve()
    assert p.resolve().is_relative_to(base)
    # must land in a subdirectory, not directly in base
    assert p.resolve().parent != base
