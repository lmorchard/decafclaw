"""Unit tests for Scavenger skill tools."""

from __future__ import annotations

import json
import os
import subprocess
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contrib.skills.scavenger import scavenger as scav
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


def _ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout=stdout, stderr="")


def _listing(containers: str = "[]", volumes: str = "[]", native: str = "") -> str:
    return f"---CONTAINERS---\n{containers}\n---VOLUMES---\n{volumes}\n---NATIVE---\n{native}\n"


class FakeHost:
    """Fake SSH runner standing in for one host; the listing it returns is the host's record."""

    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode
        self.commands: list[str] = []

    def __call__(self, alias, command, timeout=15, capture=True):
        self.commands.append(command)
        return subprocess.CompletedProcess(args=["ssh"], returncode=self.returncode, stdout=self.stdout, stderr="")


@pytest.fixture
def leases_file(tmp_path, monkeypatch):
    path = str(tmp_path / "scavenger" / "leases.json")
    monkeypatch.setattr(scav, "LEASES_FILE", path)
    return path


PODMAN_PS = json.dumps(
    [
        {
            "Names": ["scavenger-env-build1"],
            "Image": "alpine:latest",
            "Labels": {
                "decafclaw.env_id": "build1",
                "decafclaw.created_at": "1000",
                "decafclaw.expires_at": "4600",
            },
        },
        {"Names": ["unrelated"], "Labels": {"other": "x"}},
    ]
)
DOCKER_PS_LINES = (
    '{"Names":"scavenger-env-b2","Image":"img","Labels":"foo=bar,decafclaw.env_id=b2,decafclaw.expires_at=5000"}\n'
)


def test_parse_labels_podman_dict_and_docker_string():
    assert scav.parse_labels({"a": "1"}) == {"a": "1"}
    assert scav.parse_labels("a=1,b=x=y") == {"a": "1", "b": "x=y"}
    assert scav.parse_labels(None) == {}


def test_parse_container_records_podman_array():
    found = scav.parse_container_records("h", PODMAN_PS)
    assert list(found) == ["build1"]
    rec = found["build1"]
    assert (rec.host, rec.kind, rec.container_name, rec.image) == (
        "h",
        "container",
        "scavenger-env-build1",
        "alpine:latest",
    )
    assert (rec.created_at, rec.expires_at, rec.ttl_seconds) == (1000.0, 4600.0, 3600)


def test_parse_container_records_docker_json_lines():
    found = scav.parse_container_records("h", DOCKER_PS_LINES)
    assert found["b2"].expires_at == 5000.0
    assert found["b2"].container_name == "scavenger-env-b2"


def test_parse_container_records_skips_bad_labels():
    text = json.dumps(
        [
            {"Names": ["x"], "Labels": {"decafclaw.env_id": "a;rm -rf /", "decafclaw.expires_at": "1"}},
            {"Names": ["y"], "Labels": {"decafclaw.env_id": "ok", "decafclaw.expires_at": "soon"}},
        ]
    )
    assert scav.parse_container_records("h", text) == {}


def test_parse_native_leases():
    good = '{"env_id": "ws1", "created_at": 10, "expires_at": 99}'
    text = f"ws1\t{good}\nws2\t{good}\nws3\tnot json\n"
    found = scav.parse_native_leases("mac", text)
    assert list(found) == ["ws1"]  # ws2's file names another env; ws3 is unreadable
    assert found["ws1"].kind == "native"
    assert found["ws1"].workspace_path == "workspaces/ws1"
    assert found["ws1"].expires_at == 99.0


def test_list_host_envs_combines_containers_volumes_and_native(mock_fleet):
    vols = json.dumps(
        [{"Name": "scavenger-vol-build1", "Labels": {"decafclaw.env_id": "build1", "decafclaw.expires_at": "4600"}}]
    )
    native = '{"env_id": "ws1", "created_at": 1, "expires_at": 50}'
    runner = FakeHost(_listing(PODMAN_PS, vols, f"ws1\t{native}"))
    envs = {e.env_id: e for e in (scav.list_host_envs(mock_fleet["desktop-linux"], runner) or [])}
    assert set(envs) == {"build1", "ws1"}
    assert envs["build1"].volume_name == "scavenger-vol-build1"
    assert envs["ws1"].kind == "native"
    assert "podman ps --all --filter label=decafclaw.env_id --format json" in runner.commands[0]


def test_list_host_envs_none_when_host_cannot_answer(mock_fleet):
    host = mock_fleet["desktop-linux"]
    assert scav.list_host_envs(host, FakeHost(returncode=255)) is None
    assert scav.list_host_envs(host, FakeHost("---CONTAINERS---\n---ERROR---\n---VOLUMES---\n---NATIVE---\n")) is None
    assert scav.list_host_envs(host, FakeHost("garbage")) is None


def _res(env_id, host, expires_at=9999.0, **kw):
    return EnvReservation(
        env_id=env_id, host=host, kind="container", created_at=1.0, expires_at=expires_at, ttl_seconds=10, **kw
    )


def test_reconcile_host_is_the_record():
    cache = {
        "gone": _res("gone", "a"),  # host answered without it: destroyed out of band
        "kept": _res("kept", "a", container_name="scavenger-env-kept", volume_name="v"),
        "offline": _res("offline", "b"),  # host b did not answer: cache is all we have
        "retired": _res("retired", "c"),  # host c left the fleet: cache is all we have
    }
    listings = {
        "a": [_res("kept", "a", expires_at=77.0, container_name="scavenger-env-kept"), _res("new", "a")],
        "b": None,
    }
    merged = scav.reconcile_leases(cache, listings)
    assert set(merged) == {"kept", "new", "offline", "retired"}
    assert merged["kept"].expires_at == 77.0  # host label wins
    assert merged["kept"].volume_name == "v"  # field the host did not report comes from the cache


def test_corrupt_cache_is_rebuilt_from_hosts_not_dropped(leases_file, mock_fleet, caplog):
    os.makedirs(os.path.dirname(leases_file))
    with open(leases_file, "w") as f:
        f.write("{not json")
    runner = FakeHost(_listing(PODMAN_PS))

    with caplog.at_level("WARNING"):
        merged = scav.sync_leases({"desktop-linux": mock_fleet["desktop-linux"]}, runner)

    assert "build1" in merged
    assert "corrupt" in caplog.text
    assert [n for n in os.listdir(os.path.dirname(leases_file)) if ".corrupt-" in n]  # evidence kept
    with open(leases_file) as f:
        assert "build1" in json.load(f)


def test_load_leases_rebuilds_missing_cache(leases_file, mock_fleet):
    fleet = {"desktop-linux": mock_fleet["desktop-linux"]}
    with patch.object(scav, "run_ssh", FakeHost(_listing(PODMAN_PS))):
        assert "build1" in scav.load_leases(fleet)
    assert os.path.isfile(leases_file)


def test_corrupt_cache_without_fleet_is_logged_and_set_aside(leases_file, caplog):
    os.makedirs(os.path.dirname(leases_file))
    with open(leases_file, "w") as f:
        f.write("[]garbage")
    with caplog.at_level("WARNING"):
        assert scav.load_leases() == {}
    assert "corrupt" in caplog.text
    assert not os.path.exists(leases_file)


def test_update_lease_keeps_concurrent_writers_records(leases_file):
    scav.update_lease(_res("one", "a"))
    scav.update_lease(_res("two", "a"))
    assert set(scav.load_leases()) == {"one", "two"}
    assert scav.remove_lease("one") is not None
    assert set(scav.load_leases()) == {"two"}


def test_lease_lock_is_exclusive_across_file_descriptors(leases_file):
    import fcntl

    with scav.lease_lock():
        with open(leases_file + ".lock", "a+") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with open(leases_file + ".lock", "a+") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released


def test_reap_finds_expired_env_on_host_after_cache_deleted(leases_file, mock_fleet):
    fleet = {"desktop-linux": mock_fleet["desktop-linux"]}
    expired = PODMAN_PS.replace("4600", "2000")  # expires long before now
    runner = FakeHost(_listing(expired))
    with patch.object(scav, "run_ssh", runner):
        assert scav.env_reap(fleet) == ["build1"]
    assert any("rm -f scavenger-env-build1" in c for c in runner.commands)
    assert "build1" not in scav.load_leases()


def test_reap_leaves_unexpired_env_alone(leases_file, mock_fleet):
    fleet = {"desktop-linux": mock_fleet["desktop-linux"]}
    live = PODMAN_PS.replace("4600", "99999999999")
    with patch.object(scav, "run_ssh", FakeHost(_listing(live))):
        assert scav.env_reap(fleet) == []
        assert "build1" in scav.load_leases()


def test_reap_keeps_record_when_teardown_fails(leases_file, mock_fleet):
    fleet = {"desktop-linux": mock_fleet["desktop-linux"]}
    expired = PODMAN_PS.replace("4600", "2000")

    def runner(alias, command, timeout=15, capture=True):
        if " ps --all" in command:
            return _ok(_listing(expired))
        return subprocess.CompletedProcess(args=["ssh"], returncode=255, stdout="", stderr="boom")

    with patch.object(scav, "run_ssh", runner):
        assert scav.env_reap(fleet) == []
        assert "build1" in scav.load_leases()


def test_native_single_occupancy_reads_host_lease_files(leases_file, mock_fleet):
    fleet = {"laptop-mac": mock_fleet["laptop-mac"]}
    other = json.dumps({"env_id": "other", "created_at": 1, "expires_at": time.time() + 600})
    runner = FakeHost(_listing(native=f"other\t{other}"))
    # The cache is empty: only the host knows about "other".
    with (
        patch.object(scav, "run_ssh", runner),
        patch.object(scav, "probe_host", return_value=HostStatus(name="laptop-mac", eligible=True)),
    ):
        with pytest.raises(RuntimeError, match="leased to other"):
            scav.env_create(fleet, "mine", host_name="laptop-mac", kind="native")


def test_native_create_writes_lease_file_on_host(leases_file, mock_fleet):
    fleet = {"laptop-mac": mock_fleet["laptop-mac"]}
    runner = FakeHost(_listing())
    with (
        patch.object(scav, "run_ssh", runner),
        patch.object(scav, "probe_host", return_value=HostStatus(name="laptop-mac", eligible=True)),
    ):
        res = scav.env_create(fleet, "mine", host_name="laptop-mac", kind="native")
    claim = [c for c in runner.commands if "mkdir -p ~/workspaces/mine" in c]
    assert claim and ".scavenger-lease.json" in claim[0] and '"env_id": "mine"' in claim[0]
    assert scav.load_leases()["mine"].expires_at == res.expires_at


def test_native_create_fails_closed_when_host_cannot_be_checked(leases_file, mock_fleet):
    fleet = {"laptop-mac": mock_fleet["laptop-mac"]}
    with (
        patch.object(scav, "run_ssh", FakeHost(returncode=255)),
        patch.object(scav, "probe_host", return_value=HostStatus(name="laptop-mac", eligible=True)),
    ):
        with pytest.raises(RuntimeError, match="Could not verify"):
            scav.env_create(fleet, "mine", host_name="laptop-mac", kind="native")


def test_env_create_labels_container_and_volume(leases_file, mock_fleet):
    fleet = {"desktop-linux": mock_fleet["desktop-linux"]}
    runner = FakeHost(_listing())
    with (
        patch.object(scav, "run_ssh", runner),
        patch.object(scav, "probe_host", return_value=HostStatus(name="desktop-linux", eligible=True)),
    ):
        scav.env_create(fleet, "e1", host_name="desktop-linux")
    script = [c for c in runner.commands if "run -d" in c][0]
    assert script.count("decafclaw.env_id=") == 2  # container and volume
    assert script.count("decafclaw.expires_at=") == 2


def test_env_create_rejects_unsafe_env_id(leases_file, mock_fleet):
    with pytest.raises(ValueError, match="Invalid env_id"):
        scav.env_create({"desktop-linux": mock_fleet["desktop-linux"]}, "x; rm -rf ~", host_name="desktop-linux")


def test_scavenger_env_lifecycle(mock_fleet, leases_file, tmp_path):
    ctx = MagicMock()
    ctx.config.workspace_path = str(tmp_path)
    fleet = {"desktop-linux": mock_fleet["desktop-linux"]}
    state = {"created": False}

    def runner(alias, command, timeout=15, capture=True):
        if " ps --all" in command:
            return _ok(_listing(PODMAN_PS.replace("build1", "test-env") if state["created"] else "[]"))
        state["created"] = "run -d" in command  # create script starts with a defensive rm -f; destroy has no run
        return _ok()

    with (
        patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=fleet),
        patch.object(scav, "run_ssh", runner),
        patch.object(scav, "probe_host", return_value=HostStatus(name="desktop-linux", eligible=True)),
    ):
        assert "reserved on desktop-linux" in scavenger_env_create(ctx, env_id="test-env", host="desktop-linux")

        res_info = scavenger_env_info(ctx, env_id="test-env")
        assert "Environment: test-env" in res_info
        assert "desktop-linux" in res_info
        assert "test-env" in scavenger_env_list(ctx)

        assert "destroyed and lease released" in scavenger_env_destroy(ctx, env_id="test-env")
        assert "No active environment reservations" in scavenger_env_list(ctx)
