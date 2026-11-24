"""Unit tests for Scavenger skill tools."""

from __future__ import annotations

import json
import os
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contrib.skills.scavenger.scavenger import (
    EnvReservation,
    HostConfig,
    HostStatus,
    env_destroy,
    env_reap,
    parse_ttl,
    resolve_recipe,
)
from contrib.skills.scavenger.tools import (
    scavenger_env_create,
    scavenger_env_destroy,
    scavenger_env_info,
    scavenger_env_list,
    scavenger_exec_native,
    scavenger_probe,
    scavenger_reap,
    scavenger_run,
    scavenger_wake,
)


@pytest.fixture
def mock_fleet():
    return {
        "desktop-linux": HostConfig(
            name="desktop-linux",
            ssh_alias="scavenger-desktop",
            os="linux",
            engine="podman",
            arch="x86_64",
            wol_mac="AA:BB:CC:DD:EE:01",
            priority=10,
        ),
        "laptop-mac": HostConfig(
            name="laptop-mac",
            ssh_alias="scavenger-laptop",
            os="darwin",
            engine="docker",
            arch="arm64",
            battery_check=True,
            priority=20,
        ),
    }


def test_parse_ttl():
    assert parse_ttl("30s") == 30
    assert parse_ttl("15m") == 900
    assert parse_ttl("2h") == 7200
    assert parse_ttl("1d") == 86400
    assert parse_ttl("invalid") == 3600


def test_scavenger_probe(mock_fleet):
    ctx = MagicMock()
    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        with patch("contrib.skills.scavenger.tools.env_reap", return_value=[]):
            with patch("contrib.skills.scavenger.tools.probe_host") as mock_probe:
                mock_probe.side_effect = [
                    HostStatus(
                        name="desktop-linux", online=True, arch="x86_64", engine="podman", eligible=True, reason="Ready"
                    ),
                    HostStatus(
                        name="laptop-mac",
                        online=True,
                        arch="arm64",
                        engine="docker",
                        on_ac_power=True,
                        eligible=True,
                        reason="Ready",
                    ),
                ]
                res = scavenger_probe(ctx)
                assert "desktop-linux" in res
                assert "laptop-mac" in res
                assert "ELIGIBLE" in res


def _expired_lease(env_id: str, host: str) -> EnvReservation:
    """A container lease whose TTL has long since passed (1970 timestamps)."""
    return EnvReservation(
        env_id=env_id,
        host=host,
        kind="container",
        created_at=100.0,
        expires_at=200.0,
        ttl_seconds=100,
        container_name=f"scavenger-env-{env_id}",
        volume_name=f"scavenger-vol-{env_id}",
    )


def test_scavenger_probe_calls_reaper(mock_fleet):
    """probe triggers the expired-environment reaper before reporting."""
    ctx = MagicMock()
    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        with patch("contrib.skills.scavenger.tools.env_reap", return_value=[]) as mock_reap:
            with patch(
                "contrib.skills.scavenger.tools.probe_host",
                return_value=HostStatus(name="desktop-linux", online=True, eligible=True, reason="Ready"),
            ):
                res = scavenger_probe(ctx)

    mock_reap.assert_called_once_with(mock_fleet)
    assert "desktop-linux" in res


def test_scavenger_probe_reaps_expired_environments(mock_fleet):
    """An expired lease is destroyed and its lease released when probe runs."""
    ctx = MagicMock()
    expired = _expired_lease("old-env", "desktop-linux")
    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        with patch("contrib.skills.scavenger.scavenger.load_leases", return_value={"old-env": expired}):
            with patch("contrib.skills.scavenger.scavenger.save_leases") as mock_save:
                with patch("contrib.skills.scavenger.scavenger.run_ssh") as mock_ssh:
                    mock_ssh.return_value = subprocess.CompletedProcess(
                        args=["ssh"], returncode=0, stdout="", stderr=""
                    )
                    with patch(
                        "contrib.skills.scavenger.tools.probe_host",
                        return_value=HostStatus(name="desktop-linux", online=True, eligible=True, reason="Ready"),
                    ):
                        res = scavenger_probe(ctx)

    # The destroy command was dispatched to the host...
    assert any("scavenger-env-old-env" in str(c.args) for c in mock_ssh.call_args_list)
    # ...and the lease was released.
    saved = mock_save.call_args[0][0]
    assert "old-env" not in saved
    assert "Reaped 1 expired environment(s): old-env" in res


def test_scavenger_env_create_calls_reaper(mock_fleet):
    """env_create triggers the expired-environment reaper before reserving."""
    ctx = MagicMock()
    ctx.config.workspace_path = "/tmp"
    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        with patch("contrib.skills.scavenger.scavenger.env_reap", return_value=[]) as mock_reap:
            with patch("contrib.skills.scavenger.scavenger.load_leases", return_value={}):
                with patch("contrib.skills.scavenger.scavenger.save_leases"):
                    with patch(
                        "contrib.skills.scavenger.scavenger.probe_host",
                        return_value=HostStatus(name="desktop-linux", eligible=True),
                    ):
                        with patch("contrib.skills.scavenger.scavenger.run_ssh") as mock_ssh:
                            mock_ssh.return_value = subprocess.CompletedProcess(
                                args=["ssh"], returncode=0, stdout="", stderr=""
                            )
                            res = scavenger_env_create(ctx, env_id="new-env", host="desktop-linux")

    mock_reap.assert_called_once_with(mock_fleet)
    assert "reserved on desktop-linux" in res


def test_reap_failure_does_not_block_probe(mock_fleet):
    """A host that fails to reap is logged, and probe still completes."""
    ctx = MagicMock()
    expired = _expired_lease("old-env", "desktop-linux")
    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        with patch("contrib.skills.scavenger.scavenger.load_leases", return_value={"old-env": expired}):
            with patch("contrib.skills.scavenger.scavenger.save_leases") as mock_save:
                with patch(
                    "contrib.skills.scavenger.scavenger.run_ssh",
                    return_value=subprocess.CompletedProcess(
                        args=["ssh"], returncode=255, stdout="", stderr="ssh: connect to host: No route to host"
                    ),
                ):
                    with patch(
                        "contrib.skills.scavenger.tools.probe_host",
                        return_value=HostStatus(
                            name="desktop-linux",
                            online=False,
                            eligible=False,
                            reason="Unreachable (offline or sleeping)",
                        ),
                    ):
                        res = scavenger_probe(ctx)

    # Probe completed and reported the host...
    assert "desktop-linux" in res
    # ...and the failed lease was retained (lease file untouched) for a later retry.
    mock_save.assert_not_called()


def test_reap_failure_does_not_block_env_create(mock_fleet):
    """A failing reap on one host does not stop creation on another."""
    ctx = MagicMock()
    ctx.config.workspace_path = "/tmp"
    expired = _expired_lease("old-env", "desktop-linux")  # on the failing host

    def fake_ssh(alias: str, command: str, timeout: int = 15, capture: bool = True) -> subprocess.CompletedProcess[str]:
        if "scavenger-env-old-env" in command:  # the reap's destroy on the failing host
            return subprocess.CompletedProcess(args=["ssh"], returncode=255, stdout="", stderr="No route to host")
        return subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="", stderr="")

    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        with patch("contrib.skills.scavenger.scavenger.load_leases", return_value={"old-env": expired}):
            with patch("contrib.skills.scavenger.scavenger.save_leases") as mock_save:
                with patch("contrib.skills.scavenger.scavenger.run_ssh", side_effect=fake_ssh):
                    with patch(
                        "contrib.skills.scavenger.scavenger.probe_host",
                        return_value=HostStatus(name="laptop-mac", eligible=True),
                    ):
                        res = scavenger_env_create(ctx, env_id="new-env", host="laptop-mac")

    assert "reserved on laptop-mac" in res
    # The final lease save retains the failed lease for a later retry...
    saved = mock_save.call_args[0][0]
    assert "old-env" in saved and "new-env" in saved


def test_env_destroy_releases_lease_on_success(mock_fleet):
    lease = _expired_lease("e1", "desktop-linux")
    with patch("contrib.skills.scavenger.scavenger.load_leases", return_value={"e1": lease}):
        with patch("contrib.skills.scavenger.scavenger.save_leases") as mock_save:
            with patch(
                "contrib.skills.scavenger.scavenger.run_ssh",
                return_value=subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="", stderr=""),
            ):
                assert env_destroy(mock_fleet, "e1") is True

    saved = mock_save.call_args[0][0]
    assert "e1" not in saved


def test_env_destroy_keeps_lease_when_host_unreachable(mock_fleet):
    """A failed remote teardown keeps the lease so a later sweep can retry."""
    lease = _expired_lease("e1", "desktop-linux")
    with patch("contrib.skills.scavenger.scavenger.load_leases", return_value={"e1": lease}):
        with patch("contrib.skills.scavenger.scavenger.save_leases") as mock_save:
            with patch(
                "contrib.skills.scavenger.scavenger.run_ssh",
                return_value=subprocess.CompletedProcess(
                    args=["ssh"], returncode=255, stdout="", stderr="No route to host"
                ),
            ):
                assert env_destroy(mock_fleet, "e1") is False

    mock_save.assert_not_called()  # lease file untouched -> retained for retry


def test_env_destroy_missing_lease(mock_fleet):
    with patch("contrib.skills.scavenger.scavenger.load_leases", return_value={}):
        assert env_destroy(mock_fleet, "nope") is False


def test_env_reap_continues_after_host_failure(mock_fleet):
    """env_reap reaps what it can and keeps leases whose hosts fail."""
    bad = _expired_lease("bad-env", "desktop-linux")
    good = _expired_lease("good-env", "laptop-mac")

    def fake_ssh(alias: str, command: str, timeout: int = 15, capture: bool = True) -> subprocess.CompletedProcess[str]:
        if "scavenger-desktop" in alias:  # the failing host's ssh fails fast
            return subprocess.CompletedProcess(args=["ssh"], returncode=255, stdout="", stderr="No route to host")
        return subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="", stderr="")

    with patch("contrib.skills.scavenger.scavenger.load_leases", return_value={"bad-env": bad, "good-env": good}):
        with patch("contrib.skills.scavenger.scavenger.save_leases") as mock_save:
            with patch("contrib.skills.scavenger.scavenger.run_ssh", side_effect=fake_ssh):
                reaped = env_reap(mock_fleet)

    assert reaped == ["good-env"]
    saved = mock_save.call_args[0][0]
    assert "bad-env" in saved and "good-env" not in saved


def test_env_reap_continues_after_destroy_exception(mock_fleet):
    """An unexpected exception from one destroy does not abort the sweep."""
    bad = _expired_lease("bad-env", "desktop-linux")
    good = _expired_lease("good-env", "laptop-mac")

    def fake_destroy(fleet: dict, env_id: str) -> bool:
        if env_id == "bad-env":
            raise RuntimeError("boom")
        return True

    with patch("contrib.skills.scavenger.scavenger.load_leases", return_value={"bad-env": bad, "good-env": good}):
        with patch("contrib.skills.scavenger.scavenger.save_leases"):
            with patch("contrib.skills.scavenger.scavenger.env_destroy", side_effect=fake_destroy):
                reaped = env_reap(mock_fleet)

    assert reaped == ["good-env"]


def test_scavenger_wake(mock_fleet):
    ctx = MagicMock()
    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        with patch("contrib.skills.scavenger.tools.send_magic_packet") as mock_send:
            with patch("contrib.skills.scavenger.tools.is_ssh_open", return_value=True):
                res = scavenger_wake(ctx, "desktop-linux")
                assert "awake and answering SSH" in res
                mock_send.assert_called_once()


def test_scavenger_run(mock_fleet):
    ctx = MagicMock()
    ctx.workspace_path = "/tmp"
    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        with patch(
            "contrib.skills.scavenger.tools.probe_host", return_value=HostStatus(name="desktop-linux", eligible=True)
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["ssh"], returncode=0, stdout="test success\n", stderr=""
                )
                res = scavenger_run(ctx, command="pytest", host="desktop-linux")
                assert "Exit Code: 0" in res
                assert "test success" in res


def test_scavenger_exec_native(mock_fleet):
    ctx = MagicMock()
    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        with patch("contrib.skills.scavenger.tools.run_ssh") as mock_ssh:
            mock_ssh.return_value = subprocess.CompletedProcess(
                args=["ssh"], returncode=0, stdout="Apple Silicon\n", stderr=""
            )
            res = scavenger_exec_native(ctx, host="laptop-mac", command="uname -m")
            assert "Host: laptop-mac" in res
            assert "Exit Code: 0" in res
            assert "Apple Silicon" in res


def test_scavenger_env_lifecycle(mock_fleet, tmp_path):
    ctx = MagicMock()
    ctx.config.workspace_path = str(tmp_path)
    fake_leases = {}

    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        with patch("contrib.skills.scavenger.scavenger.load_leases", return_value=fake_leases):
            with patch("contrib.skills.scavenger.scavenger.save_leases"):
                with patch(
                    "contrib.skills.scavenger.scavenger.probe_host",
                    return_value=HostStatus(name="desktop-linux", eligible=True),
                ):
                    with patch("contrib.skills.scavenger.scavenger.run_ssh") as mock_ssh:
                        mock_ssh.return_value = subprocess.CompletedProcess(
                            args=["ssh"], returncode=0, stdout="", stderr=""
                        )

                        # 1. Create
                        res_create = scavenger_env_create(ctx, env_id="test-env", host="desktop-linux")
                        assert "reserved on desktop-linux" in res_create

                        fake_leases["test-env"] = EnvReservation(
                            env_id="test-env",
                            host="desktop-linux",
                            kind="container",
                            created_at=100.0,
                            expires_at=200.0,
                            ttl_seconds=100,
                            container_name="scavenger-env-test-env",
                        )

                        # 2. Info
                        with patch("contrib.skills.scavenger.tools.load_leases", return_value=fake_leases):
                            res_info = scavenger_env_info(ctx, env_id="test-env")
                            assert "Environment: test-env" in res_info
                            assert "desktop-linux" in res_info

                            res_list = scavenger_env_list(ctx)
                            assert "test-env" in res_list

                        # 3. Destroy
                        res_destroy = scavenger_env_destroy(ctx, env_id="test-env")
                        assert "destroyed and lease released" in res_destroy
