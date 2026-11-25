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

from contrib.skills.scavenger import scavenger as sc
from contrib.skills.scavenger.scavenger import (
    EnvReservation,
    HostConfig,
    HostStatus,
    SelfcheckOps,
    container_limit_flags,
    env_create,
    env_destroy,
    env_reap,
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
