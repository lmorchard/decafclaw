"""Startup process-root coverage for the single-server design (#1149).

Normal `decafclaw` startup always enters the server process root
(`runner.run_all`); there is no interactive-terminal fallback. These tests
prove the branch that used to choose between the server and the REPL is gone
and that the server root is entered unconditionally.
"""

from unittest.mock import AsyncMock

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
    monkeypatch.setattr("decafclaw.runner.run_all", run_all)
    monkeypatch.setattr("decafclaw.load_config", lambda: config)

    start_app()

    run_all.assert_awaited_once()
