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


def test_scavenger_wake(mock_fleet):
    ctx = MagicMock()
    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        with patch("contrib.skills.scavenger.scavenger.send_magic_packet") as mock_send:
            with patch("contrib.skills.scavenger.scavenger.is_ssh_open", return_value=True):
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
