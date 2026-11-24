"""Unit tests for Scavenger skill tools."""

from __future__ import annotations

import json
import os
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contrib.skills.scavenger import scavenger as sc
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


def _container_row(env_id, expires_at, created_at=100, ttl=3600, engine="podman"):
    labels = {
        "decafclaw.env_id": env_id,
        "decafclaw.expires_at": str(expires_at),
        "decafclaw.created_at": str(created_at),
        "decafclaw.ttl_seconds": str(ttl),
    }
    if engine == "docker":
        labels = ",".join(f"{k}={v}" for k, v in labels.items())
    return {
        "Names": [f"scavenger-env-{env_id}"] if engine == "podman" else f"scavenger-env-{env_id}",
        "Image": "alpine",
        "Labels": labels,
    }


def _listing(containers=(), volumes=(), native=(), engine="podman"):
    rows = [_container_row(*c, engine=engine) if isinstance(c, tuple) else c for c in containers]
    if engine == "podman":
        ctr_out = json.dumps(rows)
        vol_out = json.dumps(list(volumes))
    else:
        ctr_out = "\n".join(json.dumps(r) for r in rows)
        vol_out = "\n".join(json.dumps(v) for v in volumes)
    native_out = "\n".join(json.dumps(n) for n in native)
    return f"---CONTAINERS---\n{ctr_out}\n---VOLUMES---\n{vol_out}\n---NATIVE---\n{native_out}\n---END---\n"


class FakeSSH:
    """Stands in for run_ssh: serves host listings and records every other command."""

    def __init__(self, listings):
        self.listings = listings  # ssh_alias -> listing text, or None for unreachable
        self.commands = []

    def __call__(self, alias, command, timeout=15, capture=True):
        if "---CONTAINERS---" in command:
            out = self.listings.get(alias)
            if out is None:
                return subprocess.CompletedProcess(["ssh"], 255, "", "unreachable")
            return subprocess.CompletedProcess(["ssh"], 0, out, "")
        self.commands.append((alias, command))
        return subprocess.CompletedProcess(["ssh"], 0, "", "")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = str(tmp_path / "scavenger" / "leases.json")
    monkeypatch.setattr(sc, "LEASES_FILE", path)
    return path


def test_parse_labels_podman_dict_and_docker_string():
    assert sc.parse_labels({"a": "1", "b": "2"}) == {"a": "1", "b": "2"}
    assert sc.parse_labels("a=1,b=x=y") == {"a": "1", "b": "x=y"}
    assert sc.parse_labels(None) == {}


def test_parse_engine_json_array_and_lines():
    assert sc.parse_engine_json('[{"a": 1}, {"a": 2}]') == [{"a": 1}, {"a": 2}]
    assert sc.parse_engine_json('{"a": 1}\n{"a": 2}\n') == [{"a": 1}, {"a": 2}]
    assert sc.parse_engine_json("  ") == []


@pytest.mark.parametrize("engine", ["podman", "docker"])
def test_parse_host_listing_containers_volumes_native(engine):
    out = _listing(
        containers=[("c1", 5000, 1000, 4000)],
        volumes=[{"Name": "scavenger-vol-v1", "Labels": {"decafclaw.env_id": "v1", "decafclaw.expires_at": "7000"}}],
        native=[{"env_id": "n1", "created_at": 10, "expires_at": 9000, "ttl_seconds": 100}],
        engine=engine,
    )
    parsed = sc.parse_host_listing("h", out)
    assert parsed is not None
    envs = {e.env_id: e for e in parsed}
    assert envs["c1"].kind == "container"
    assert envs["c1"].container_name == "scavenger-env-c1"
    assert envs["c1"].expires_at == 5000
    assert envs["c1"].ttl_seconds == 4000
    assert envs["c1"].created_at == 1000
    # A volume with no container (container died, volume left behind) is still an environment.
    assert envs["v1"].volume_name == "scavenger-vol-v1"
    assert envs["v1"].expires_at == 7000
    assert envs["n1"].kind == "native"
    assert envs["n1"].workspace_path == "workspaces/n1"


def test_parse_host_listing_skips_unlabelled_and_bad_records():
    out = _listing(
        containers=[
            {"Names": ["other"], "Labels": {}},
            {"Names": ["x"], "Labels": {"decafclaw.env_id": "no-expiry"}},
        ],
        native=[{"env_id": "n1"}],
    )
    assert sc.parse_host_listing("h", out) == []


def test_parse_host_listing_incomplete_is_untrusted():
    assert sc.parse_host_listing("h", "---CONTAINERS---\n[]\n") is None
    assert sc.parse_host_listing("h", "---CONTAINERS---\n---FAILED---\n") is None
    assert sc.parse_host_listing("h", "---CONTAINERS---\nnot json\n---VOLUMES---\n---NATIVE---\n---END---\n") is None


def test_list_host_envs_unreachable_returns_none(mock_fleet):
    ssh = FakeSSH({})
    with patch.object(sc, "run_ssh", ssh):
        assert sc.list_host_envs(mock_fleet["desktop-linux"]) is None


def test_reconcile_rebuilds_missing_cache_from_hosts(mock_fleet, cache):
    ssh = FakeSSH(
        {"scavenger-desktop": _listing(containers=[("e1", 5000, 1000, 4000)]), "scavenger-laptop": _listing()}
    )
    with patch.object(sc, "run_ssh", ssh):
        leases, unreachable = sc.reconcile_leases(mock_fleet)
    assert unreachable == []
    assert leases["e1"].host == "desktop-linux"
    assert "e1" in sc.load_leases()


def test_reconcile_drops_stale_cache_entries_and_keeps_unreachable_hosts(mock_fleet, cache):
    sc.save_leases(
        {
            "gone": EnvReservation("gone", "desktop-linux", "container", 1.0, 2.0, 1),
            "away": EnvReservation("away", "laptop-mac", "container", 1.0, 2.0, 1),
        }
    )
    ssh = FakeSSH({"scavenger-desktop": _listing(), "scavenger-laptop": None})
    with patch.object(sc, "run_ssh", ssh):
        leases, unreachable = sc.reconcile_leases(mock_fleet)
    assert unreachable == ["laptop-mac"]
    assert set(leases) == {"away"}


def test_reconcile_host_expiry_overrides_cache(mock_fleet, cache):
    sc.save_leases({"e1": EnvReservation("e1", "desktop-linux", "container", 1.0, 2.0, 1, image="img")})
    ssh = FakeSSH({"scavenger-desktop": _listing(containers=[("e1", 9999, 1000, 100)]), "scavenger-laptop": _listing()})
    with patch.object(sc, "run_ssh", ssh):
        leases, _ = sc.reconcile_leases(mock_fleet)
    assert leases["e1"].expires_at == 9999


def test_corrupt_cache_is_logged_and_rebuilt_not_dropped(mock_fleet, cache, caplog):
    os.makedirs(os.path.dirname(cache))
    with open(cache, "w") as f:
        f.write("{not json")
    ssh = FakeSSH(
        {"scavenger-desktop": _listing(containers=[("e1", 5000, 1000, 4000)]), "scavenger-laptop": _listing()}
    )
    with patch.object(sc, "run_ssh", ssh), caplog.at_level("ERROR"):
        leases, _ = sc.reconcile_leases(mock_fleet)
    assert "e1" in leases
    assert "unreadable" in caplog.text
    # The corrupt file is preserved for inspection, and the live cache is valid again.
    assert any(n.startswith("leases.json.corrupt-") for n in os.listdir(os.path.dirname(cache)))
    assert "e1" in json.load(open(cache))


def test_env_list_and_reap_work_with_deleted_cache(mock_fleet, cache, monkeypatch):
    """Manual check from the issue: delete leases.json, env-list still lists, reap still reaps."""
    now = 10_000.0
    monkeypatch.setattr(sc.time, "time", lambda: now)
    ssh = FakeSSH(
        {
            "scavenger-desktop": _listing(containers=[("live", now + 500, 1, 500), ("dead", now - 5, 1, 10)]),
            "scavenger-laptop": _listing(),
        }
    )
    with patch.object(sc, "run_ssh", ssh):
        leases, _ = sc.reconcile_leases(mock_fleet)
        assert set(leases) == {"live", "dead"}
        os.remove(cache)
        reaped = sc.env_reap(mock_fleet)
    assert reaped == ["dead"]
    teardown = [c for _, c in ssh.commands if "rm -f scavenger-env-dead" in c]
    assert teardown
    assert not any("scavenger-env-live" in c for _, c in ssh.commands)


def test_reap_skips_expired_envs_on_unreachable_host(mock_fleet, cache):
    sc.save_leases({"old": EnvReservation("old", "laptop-mac", "container", 1.0, 2.0, 1)})
    ssh = FakeSSH({"scavenger-desktop": _listing(), "scavenger-laptop": None})
    with patch.object(sc, "run_ssh", ssh):
        assert sc.env_reap(mock_fleet) == []
    assert "old" in sc.load_leases()


def test_failed_teardown_keeps_cache_entry(mock_fleet, cache):
    sc.save_leases({"e1": EnvReservation("e1", "desktop-linux", "container", 1.0, 2.0, 1)})

    def failing(alias, command, timeout=15, capture=True):
        return subprocess.CompletedProcess(["ssh"], 1, "", "boom")

    with patch.object(sc, "run_ssh", failing):
        assert sc.env_destroy(mock_fleet, "e1") is False
    assert "e1" in sc.load_leases()


def test_native_single_occupancy_reads_host_lease_files(mock_fleet, cache):
    """The cache is empty, but the host holds a live native lease: creating a second env must fail."""
    now = 10_000.0
    ssh = FakeSSH(
        {
            "scavenger-laptop": _listing(
                native=[{"env_id": "busy", "created_at": 1, "expires_at": now + 600, "ttl_seconds": 600}]
            ),
            "scavenger-desktop": _listing(),
        }
    )
    with (
        patch.object(sc, "run_ssh", ssh),
        patch.object(sc, "probe_host", return_value=HostStatus(name="laptop-mac", eligible=True)),
        patch.object(sc.time, "time", return_value=now),
    ):
        with pytest.raises(RuntimeError, match="currently leased to busy"):
            sc.env_create(mock_fleet, "second", host_name="laptop-mac", kind="native")


def test_native_create_writes_lease_file_on_host(mock_fleet, cache):
    ssh = FakeSSH({"scavenger-laptop": _listing(), "scavenger-desktop": _listing()})
    with (
        patch.object(sc, "run_ssh", ssh),
        patch.object(sc, "probe_host", return_value=HostStatus(name="laptop-mac", eligible=True)),
    ):
        res = sc.env_create(mock_fleet, "n1", host_name="laptop-mac", kind="native")
    assert res.kind == "native"
    mkdir_cmds = [c for _, c in ssh.commands if sc.NATIVE_LEASE_FILE in c]
    assert mkdir_cmds and "workspaces/n1" in mkdir_cmds[0]


def test_native_create_refuses_when_host_listing_fails(mock_fleet, cache):
    ssh = FakeSSH({"scavenger-desktop": _listing()})  # laptop unreachable
    with (
        patch.object(sc, "run_ssh", ssh),
        patch.object(sc, "probe_host", return_value=HostStatus(name="laptop-mac", eligible=True)),
    ):
        with pytest.raises(RuntimeError, match="Cannot verify native execution slot"):
            sc.env_create(mock_fleet, "n1", host_name="laptop-mac", kind="native")


def test_container_create_labels_container_and_volume(mock_fleet, cache):
    ssh = FakeSSH({"scavenger-desktop": _listing(), "scavenger-laptop": _listing()})
    with (
        patch.object(sc, "run_ssh", ssh),
        patch.object(sc, "probe_host", return_value=HostStatus(name="desktop-linux", eligible=True)),
    ):
        res = sc.env_create(mock_fleet, "c1", host_name="desktop-linux", ttl="1h")
    script = next(c for _, c in ssh.commands if "run -d" in c)
    assert script.count('decafclaw.env_id="c1"') == 2  # container and volume
    assert 'decafclaw.ttl_seconds="3600"' in script
    assert f'decafclaw.expires_at="{int(res.expires_at)}"' in script
    assert sc.load_leases()["c1"].container_name == "scavenger-env-c1"


def test_concurrent_cache_updates_do_not_lose_leases(cache):
    import threading

    def add(i):
        res = EnvReservation(f"e{i}", "h", "container", 1.0, 2.0, 1)
        sc.update_leases(lambda leases: leases.__setitem__(res.env_id, res))

    threads = [threading.Thread(target=add, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(sc.load_leases()) == 20


def test_cache_lock_is_exclusive_across_processes(cache):
    """A second process cannot take the lock while update_leases holds it."""
    import fcntl

    def probe(leases):
        with open(cache + ".lock", "a") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)

    sc.update_leases(probe)


def test_scavenger_env_list_tool_reports_host_state(mock_fleet, cache):
    ctx = MagicMock()
    ssh = FakeSSH({"scavenger-desktop": _listing(containers=[("e1", 5000, 1000, 4000)]), "scavenger-laptop": None})
    with (
        patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet),
        patch.object(sc, "run_ssh", ssh),
    ):
        out = scavenger_env_list(ctx)
    assert "e1" in out
    assert "laptop-mac" in out  # flagged unreachable


def test_scavenger_env_lifecycle(mock_fleet, cache):
    ctx = MagicMock()
    ctx.config.workspace_path = "/tmp"
    ssh = FakeSSH({"scavenger-desktop": _listing(), "scavenger-laptop": _listing()})
    with (
        patch("contrib.skills.scavenger.tools.load_fleet_config", return_value=mock_fleet),
        patch.object(sc, "run_ssh", ssh),
        patch.object(sc, "probe_host", return_value=HostStatus(name="desktop-linux", eligible=True)),
    ):
        assert "reserved on desktop-linux" in scavenger_env_create(ctx, env_id="test-env", host="desktop-linux")
        assert "Environment: test-env" in scavenger_env_info(ctx, env_id="test-env")
        assert "destroyed and lease released" in scavenger_env_destroy(ctx, env_id="test-env")
    assert "test-env" not in sc.load_leases()
