"""Startup process-root coverage for the single-server design (#1149).

Normal `decafclaw` startup always enters the server process root
(`runner.run_all`); there is no interactive-terminal fallback. These tests
prove the branch that used to choose between the server and the REPL is gone
and that the server root is entered unconditionally.
"""

from unittest.mock import AsyncMock, MagicMock

from decafclaw import start_app


def test_start_app_enters_server_process_root_when_no_transports_configured(config, monkeypatch):
    """Normal startup enters `runner.run_all` even with HTTP and Mattermost off (#1149).

    The legacy stdin/stdout REPL used to be the process root when
    `mattermost.url` was empty and `http.enabled` was false. The server root
    is now unconditional, so the test mocks the process root and proves the
    server path is the one taken — without starting real services.
    """
    # The fixture config has no inbound chat transports configured.
    assert not config.http.enabled
    assert not config.mattermost.url

    run_all = AsyncMock()
    exit_process = MagicMock()
    monkeypatch.setattr("decafclaw.runner.run_all", run_all)
    monkeypatch.setattr("decafclaw.runner.exit_process", exit_process)
    monkeypatch.setattr("decafclaw.load_config", lambda: config)

    start_app()

    run_all.assert_awaited_once()
    exit_process.assert_called_once_with("shutdown complete")


def test_start_app_exits_nonzero_when_server_root_crashes(config, monkeypatch):
    """A crashed server root exits non-zero so systemd's Restart=on-failure applies."""
    run_all = AsyncMock(side_effect=RuntimeError("boom"))
    exit_process = MagicMock()
    monkeypatch.setattr("decafclaw.runner.run_all", run_all)
    monkeypatch.setattr("decafclaw.runner.exit_process", exit_process)
    monkeypatch.setattr("decafclaw.load_config", lambda: config)

    start_app()

    exit_process.assert_called_once_with("crash", 1)
