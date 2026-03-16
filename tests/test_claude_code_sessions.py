"""Tests for Claude Code SessionManager."""

import time

import pytest

from decafclaw.skills.claude_code.sessions import Session, SessionManager


@pytest.fixture
def manager():
    return SessionManager(timeout_sec=300, budget_default=2.0, budget_max=10.0)


def test_create_session(manager):
    session = manager.create("/tmp/myrepo", description="fix bugs")
    assert len(session.session_id) == 12
    assert session.cwd == "/tmp/myrepo"
    assert session.description == "fix bugs"
    assert session.budget_usd == 2.0  # default
    assert session.total_cost_usd == 0
    assert session.send_count == 0


def test_create_with_custom_budget(manager):
    session = manager.create("/tmp/repo", budget_usd=5.0)
    assert session.budget_usd == 5.0


def test_create_clamps_budget_to_max(manager):
    session = manager.create("/tmp/repo", budget_usd=100.0)
    assert session.budget_usd == 10.0  # clamped to max


def test_create_zero_budget_uses_default(manager):
    session = manager.create("/tmp/repo", budget_usd=0)
    assert session.budget_usd == 2.0


def test_create_rejects_duplicate_cwd(manager):
    manager.create("/tmp/repo")
    with pytest.raises(ValueError, match="session already exists"):
        manager.create("/tmp/repo")


def test_create_allows_different_cwds(manager):
    s1 = manager.create("/tmp/repo1")
    s2 = manager.create("/tmp/repo2")
    assert s1.session_id != s2.session_id


def test_get_returns_session(manager):
    session = manager.create("/tmp/repo")
    found = manager.get(session.session_id)
    assert found is session


def test_get_returns_none_for_unknown(manager):
    assert manager.get("nonexistent") is None


def test_get_returns_none_for_expired(manager):
    mgr = SessionManager(timeout_sec=0, budget_default=2.0, budget_max=10.0)
    session = mgr.create("/tmp/repo")
    # With timeout_sec=0, any session is immediately expired
    assert mgr.get(session.session_id) is None


def test_get_removes_expired_session(manager):
    mgr = SessionManager(timeout_sec=0, budget_default=2.0, budget_max=10.0)
    session = mgr.create("/tmp/repo")
    mgr.get(session.session_id)  # triggers removal
    assert session.session_id not in mgr.sessions
    assert "/tmp/repo" not in mgr.cwd_to_session


def test_touch_updates_last_active(manager):
    session = manager.create("/tmp/repo")
    original = session.last_active
    # Ensure some time passes
    time.sleep(0.01)
    manager.touch(session.session_id)
    assert session.last_active > original


def test_stop_removes_and_returns(manager):
    session = manager.create("/tmp/repo")
    stopped = manager.stop(session.session_id)
    assert stopped is session
    assert manager.get(session.session_id) is None


def test_stop_returns_none_for_unknown(manager):
    assert manager.stop("nonexistent") is None


def test_stop_allows_new_session_at_same_cwd(manager):
    session = manager.create("/tmp/repo")
    manager.stop(session.session_id)
    # Should be able to create a new session at the same cwd
    new_session = manager.create("/tmp/repo")
    assert new_session.session_id != session.session_id


def test_list_active(manager):
    s1 = manager.create("/tmp/repo1")
    s2 = manager.create("/tmp/repo2")
    active = manager.list_active()
    assert len(active) == 2
    assert s1 in active
    assert s2 in active


def test_list_active_excludes_expired():
    mgr = SessionManager(timeout_sec=0, budget_default=2.0, budget_max=10.0)
    mgr.create("/tmp/repo")
    active = mgr.list_active()
    assert len(active) == 0


def test_close_all(manager):
    manager.create("/tmp/repo1")
    manager.create("/tmp/repo2")
    removed = manager.close_all()
    assert len(removed) == 2
    assert len(manager.sessions) == 0
    assert len(manager.cwd_to_session) == 0


def test_cwd_trailing_slash_normalized(manager):
    session = manager.create("/tmp/repo/")
    assert session.cwd == "/tmp/repo"
    # Should conflict with the same path without trailing slash
    with pytest.raises(ValueError):
        manager.create("/tmp/repo")
