"""Unit tests for Scavenger skill tools."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contrib.skills.scavenger.scavenger import (
    EnvReservation,
    HostConfig,
    HostStatus,
    SelfcheckOps,
    container_limit_flags,
    env_create,
    execute_job,
    load_fleet_config,
    parse_ttl,
    resolve_recipe,
    run_selfcheck,
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


# =============================================================================
# Container limit argument builder (network / pids-limit / log size)
# =============================================================================


def _host(**overrides: Any) -> HostConfig:
    return HostConfig(name="test-host", ssh_alias="scavenger-test", **overrides)


def test_container_limit_flags_network_none():
    flags = container_limit_flags(_host(), network="none")
    assert "--network=none" in flags


def test_container_limit_flags_network_default():
    flags = container_limit_flags(_host(), network="default")
    assert "--network=default" in flags


def test_container_limit_flags_rejects_unknown_network():
    with pytest.raises(ValueError):
        container_limit_flags(_host(), network="bridge")


def test_container_limit_flags_pids_default():
    flags = container_limit_flags(_host())
    assert "--pids-limit=4096" in flags


def test_container_limit_flags_pids_custom():
    flags = container_limit_flags(_host(pids_limit=1024))
    assert "--pids-limit=1024" in flags


def test_container_limit_flags_log_size_docker():
    flags = container_limit_flags(_host(engine="docker"))
    assert "max-size=10m" in flags
    assert "max-file=3" in flags


def test_container_limit_flags_log_size_podman():
    flags = container_limit_flags(_host(engine="podman"))
    assert "max-size=10m" in flags


def test_container_limit_flags_log_size_custom():
    flags = container_limit_flags(_host(engine="docker", log_max_size="5m", log_max_file=1))
    assert "max-size=5m" in flags
    assert "max-file=1" in flags


def test_container_limit_flags_log_skipped_unsupported_engine():
    flags = container_limit_flags(_host(engine="containerd"))
    assert not any("log-opt" in f for f in flags)


def test_load_fleet_config_limits(tmp_path):
    cfg_file = tmp_path / "fleet.json"
    cfg_file.write_text(
        json.dumps(
            {
                "hosts": [
                    {
                        "name": "h1",
                        "ssh_alias": "scavenger-h1",
                        "limits": {"pids_limit": 2048, "log_max_size": "5m", "log_max_file": 1},
                    }
                ]
            }
        )
    )
    h = load_fleet_config(str(cfg_file))["h1"]
    assert h.pids_limit == 2048
    assert h.log_max_size == "5m"
    assert h.log_max_file == 1


def test_load_fleet_config_limits_defaults(tmp_path):
    cfg_file = tmp_path / "fleet.json"
    cfg_file.write_text(json.dumps({"hosts": [{"name": "h1", "ssh_alias": "scavenger-h1"}]}))
    h = load_fleet_config(str(cfg_file))["h1"]
    assert h.pids_limit == 4096
    assert h.log_max_size == "10m"


def test_execute_job_network_none_in_remote_script():
    host = _host(engine="docker")
    with patch("contrib.skills.scavenger.scavenger.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="", stderr="")
        execute_job(host, command="true", network="none")
    script = mock_run.call_args[0][0][-1]
    assert "--network=none" in script
    assert "--pids-limit=4096" in script


def test_execute_job_default_network_in_remote_script():
    host = _host(engine="podman")
    with patch("contrib.skills.scavenger.scavenger.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="", stderr="")
        execute_job(host, command="true")
    script = mock_run.call_args[0][0][-1]
    assert "--network=default" in script


def test_env_create_container_limit_flags_in_script(mock_fleet):
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
                    env_create(
                        fleet=mock_fleet,
                        env_id="limit-env",
                        host_name="desktop-linux",
                        network="none",
                    )
    create_script = mock_ssh.call_args[0][1]
    assert "--network=none" in create_script
    assert "--pids-limit=4096" in create_script


def test_scavenger_run_tool_network_none(mock_fleet):
    ctx = MagicMock()
    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        with patch(
            "contrib.skills.scavenger.tools.probe_host", return_value=HostStatus(name="desktop-linux", eligible=True)
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="", stderr="")
                scavenger_run(ctx, command="pytest", host="desktop-linux", network="none")
    script = mock_run.call_args[0][0][-1]
    assert "--network=none" in script


def test_scavenger_run_tool_invalid_network(mock_fleet):
    ctx = MagicMock()
    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        res = scavenger_run(ctx, command="pytest", host="desktop-linux", network="bridge")
    assert "Error" in res


def test_scavenger_env_create_tool_invalid_network(mock_fleet):
    ctx = MagicMock()
    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet):
        res = scavenger_env_create(ctx, env_id="x", host="desktop-linux", network="bridge")
    assert "Error" in res


# ---------------------------------------------------------------------------
# selfcheck step sequencing, driven by a fake host
# ---------------------------------------------------------------------------


def _cp(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeHost:
    """In-memory host: a remote workspace dir plus a record of calls and leftovers."""

    def __init__(self, tmp_path, eligible=True, online=True):
        self.remote = tmp_path / "remote"
        self.remote.mkdir()
        self.calls = []
        self.eligible = eligible
        self.online = online
        self.leftover = ""
        self.fail_at: str | None = None
        self.corrupt_up = False
        self.created = False
        self.destroyed = False
        self.run_output: str | None = None

    def ops(self) -> SelfcheckOps:
        return SelfcheckOps(
            probe=self.probe,
            run_job=self.run_job,
            env_create=self.env_create,
            env_exec=self.env_exec,
            env_sync=self.env_sync,
            env_destroy=self.env_destroy,
            ssh=self.ssh,
        )

    def probe(self, host):
        self.calls.append("probe")
        return HostStatus(
            name=host.name,
            online=self.online,
            eligible=self.eligible,
            engine=host.engine,
            arch=host.arch,
            reason="" if self.eligible else "Gaming / game streaming currently active",
        )

    def run_job(self, host, command, repo_path, stdout, **kw):
        self.calls.append("run")
        if self.fail_at == "run":
            return 3
        if self.run_output is not None:
            stdout.write(self.run_output)
            return 0
        with open(os.path.join(repo_path, "marker.txt"), encoding="utf-8") as f:
            stdout.write(f.read())
        return 0

    def env_create(self, env_id, **kw):
        self.calls.append("env-create")
        if self.fail_at == "env-create":
            raise RuntimeError("engine exploded")
        self.created = True
        return EnvReservation(
            env_id=env_id, host=kw["host_name"], kind="container", created_at=0, expires_at=1, ttl_seconds=1
        )

    def env_exec(self, res, command):
        self.calls.append("env-exec")
        if self.fail_at == "env-exec":
            return _cp(returncode=1, stderr="no such container")
        if command.startswith("echo ") and ">" not in command:
            return _cp(stdout=command[len("echo ") :] + "\n")
        if command.startswith("sha256sum "):
            data = (self.remote / command.split()[1]).read_bytes()
            return _cp(stdout=f"{hashlib.sha256(data).hexdigest()}  payload.bin\n")
        m = re.match(r"echo (\S+) > (\S+)$", command)
        if m:
            (self.remote / m.group(2)).write_text(m.group(1) + "\n")
            return _cp()
        raise AssertionError(f"unexpected command {command}")

    def env_sync(self, res, direction, path):
        self.calls.append(f"sync-{direction}")
        if direction == "up":
            for name in os.listdir(path):
                shutil.copy(os.path.join(path, name), self.remote / name)
            if self.corrupt_up:
                (self.remote / "payload.bin").write_bytes(b"corrupt")
        else:
            os.makedirs(path, exist_ok=True)
            for item in self.remote.iterdir():
                shutil.copy(item, os.path.join(path, item.name))

    def env_destroy(self, env_id):
        self.calls.append("env-destroy")
        self.destroyed = True
        return True

    def ssh(self, host, command):
        self.calls.append("ssh-leftovers")
        return _cp(stdout=f"container={self.leftover}\nvolume=\nworkspace=\n")


def _run(fake, mock_fleet, host="desktop-linux"):
    lines = []
    ok = run_selfcheck(mock_fleet[host], fake.ops(), emit=lines.append, env_id="selfcheck-test")
    return ok, lines


def test_selfcheck_all_steps_pass_in_order(tmp_path, mock_fleet):
    fake = FakeHost(tmp_path)
    ok, lines = _run(fake, mock_fleet)

    assert ok is True
    assert [ln.split(":")[0] for ln in lines] == [
        "PASS probe",
        "PASS run",
        "PASS env-create",
        "PASS env-exec",
        "PASS env-sync-up",
        "PASS env-sync-down",
        "PASS env-destroy",
        "PASS leftovers",
    ]
    assert fake.calls.index("env-destroy") < fake.calls.index("ssh-leftovers")


def test_selfcheck_ineligible_host_stops_before_any_work(tmp_path, mock_fleet):
    fake = FakeHost(tmp_path, eligible=False)
    ok, lines = _run(fake, mock_fleet)

    assert ok is False
    assert len(lines) == 1 and lines[0].startswith("FAIL probe") and "not eligible" in lines[0]
    assert fake.calls == ["probe"]


def test_selfcheck_offline_host_fails_probe(tmp_path, mock_fleet):
    fake = FakeHost(tmp_path, online=False)
    ok, lines = _run(fake, mock_fleet)

    assert ok is False
    assert lines[0].startswith("FAIL probe")
    assert fake.calls == ["probe"]


def test_selfcheck_run_failure_stops_and_creates_nothing(tmp_path, mock_fleet):
    fake = FakeHost(tmp_path)
    fake.fail_at = "run"
    ok, lines = _run(fake, mock_fleet)

    assert ok is False
    assert lines[-1].startswith("FAIL run") and "exited 3" in lines[-1]
    assert "env-create" not in fake.calls
    assert not any(ln.startswith("NOTE") for ln in lines)


def test_selfcheck_run_output_without_marker_fails(tmp_path, mock_fleet):
    fake = FakeHost(tmp_path)
    fake.run_output = "something else\n"
    ok, lines = _run(fake, mock_fleet)

    assert ok is False
    assert lines[-1].startswith("FAIL run") and "marker missing" in lines[-1]


def test_selfcheck_checksum_mismatch_fails_and_destroys_environment(tmp_path, mock_fleet):
    fake = FakeHost(tmp_path)
    fake.corrupt_up = True
    ok, lines = _run(fake, mock_fleet)

    assert ok is False
    assert any(ln.startswith("FAIL env-sync-up") and "checksum mismatch" in ln for ln in lines)
    assert "sync-down" not in fake.calls
    assert fake.destroyed is True
    assert any(ln.startswith("NOTE cleaned up") for ln in lines)


def test_selfcheck_env_create_exception_is_a_failure_without_cleanup(tmp_path, mock_fleet):
    fake = FakeHost(tmp_path)
    fake.fail_at = "env-create"
    ok, lines = _run(fake, mock_fleet)

    assert ok is False
    assert lines[-1].startswith("FAIL env-create") and "engine exploded" in lines[-1]
    assert fake.destroyed is False


def test_selfcheck_exec_failure_destroys_environment(tmp_path, mock_fleet):
    fake = FakeHost(tmp_path)
    fake.fail_at = "env-exec"
    ok, lines = _run(fake, mock_fleet)

    assert ok is False
    assert any(ln.startswith("FAIL env-exec") for ln in lines)
    assert fake.destroyed is True


def test_selfcheck_leftover_container_fails_final_step(tmp_path, mock_fleet):
    fake = FakeHost(tmp_path)
    fake.leftover = "abc123"
    ok, lines = _run(fake, mock_fleet)

    assert ok is False
    assert lines[-1].startswith("FAIL leftovers") and "container=abc123" in lines[-1]
    assert fake.calls.count("env-destroy") == 1


def test_selfcheck_cli_exit_codes(tmp_path, mock_fleet):
    from contrib.skills.scavenger import scavenger

    args = MagicMock(config=None, host="desktop-linux", image="alpine:latest", wake=False)
    for result, code in ((True, 0), (False, 1)):
        with (
            patch.object(scavenger, "load_fleet_config", return_value=mock_fleet),
            patch.object(scavenger, "run_selfcheck", return_value=result),
        ):
            with pytest.raises(SystemExit) as exc:
                scavenger.cmd_selfcheck(args)
        assert exc.value.code == code

    args.host = "nope"
    with patch.object(scavenger, "load_fleet_config", return_value=mock_fleet):
        with pytest.raises(SystemExit) as exc:
            scavenger.cmd_selfcheck(args)
    assert exc.value.code != 0
