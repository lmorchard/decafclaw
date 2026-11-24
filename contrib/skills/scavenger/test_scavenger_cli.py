"""Tests for the standalone scavenger CLI: recipe directory, label names, JSON output, exit codes."""

from __future__ import annotations

import json
import subprocess
import time
from unittest.mock import patch

import pytest

from contrib.skills.scavenger import scavenger
from contrib.skills.scavenger.scavenger import (
    EnvReservation,
    HostStatus,
    configured_recipes_dir,
    main,
    parse_env_labels,
    resolve_recipe,
)

FLEET = {
    "hosts": [
        {
            "name": "desktop",
            "ssh_alias": "scavenger-desktop",
            "arch": "x86_64",
            "container": {"engine": "podman"},
            "power": {"wol_mac": "AA:BB:CC:DD:EE:01", "wol_wait_seconds": 1},
        }
    ]
}


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def fleet_file(tmp_path):
    path = tmp_path / "fleet.json"
    path.write_text(json.dumps(FLEET))
    return path


@pytest.fixture(autouse=True)
def isolated_leases(tmp_path, monkeypatch):
    monkeypatch.setattr(scavenger, "LEASES_FILE", str(tmp_path / "leases.json"))


def add_lease(env_id="env1", expires_in=3600, kind="container"):
    leases = scavenger.load_leases()
    now = time.time()
    leases[env_id] = EnvReservation(
        env_id=env_id,
        host="desktop",
        kind=kind,
        created_at=now,
        expires_at=now + expires_in,
        ttl_seconds=3600,
        container_name=f"scavenger-env-{env_id}" if kind == "container" else None,
        workspace_path=f"workspaces/{env_id}" if kind == "native" else None,
        image="alpine:latest",
    )
    scavenger.save_leases(leases)


def run_cli(capsys, fleet_file, *argv):
    """Run the CLI, returning (exit_code, stdout, stderr)."""
    with pytest.raises(SystemExit) as exc:
        main(["--config", str(fleet_file), *argv])
    out = capsys.readouterr()
    return exc.value.code, out.out, out.err


# --- recipe directory ------------------------------------------------------


def make_recipe(directory, name="node-dev"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.Dockerfile"
    path.write_text("FROM alpine\n")
    return path


def test_recipes_dir_flag_beats_fleet_json(tmp_path, fleet_file):
    fleet_file.write_text(json.dumps({**FLEET, "recipes_dir": str(tmp_path / "from-config")}))
    flag_dir = tmp_path / "from-flag"
    assert configured_recipes_dir(str(flag_dir), str(fleet_file)) == str(flag_dir)


def test_recipes_dir_from_fleet_json(tmp_path, fleet_file):
    fleet_file.write_text(json.dumps({**FLEET, "recipes_dir": str(tmp_path / "from-config")}))
    assert configured_recipes_dir(None, str(fleet_file)) == str(tmp_path / "from-config")


def test_recipes_dir_relative_to_fleet_json(tmp_path, fleet_file):
    fleet_file.write_text(json.dumps({**FLEET, "recipes_dir": "recipes"}))
    assert configured_recipes_dir(None, str(fleet_file)) == str(tmp_path / "recipes")


def test_recipes_dir_unset_returns_none(fleet_file):
    assert configured_recipes_dir(None, str(fleet_file)) is None


def test_resolve_recipe_defaults_to_cwd_dockerfiles(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    recipe = make_recipe(tmp_path / "dockerfiles", "my-recipe")
    assert resolve_recipe("my-recipe") == (str(recipe), "dockerfile")


def test_resolve_recipe_prefers_recipes_dir_over_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_recipe(tmp_path / "dockerfiles", "node-dev")
    chosen = make_recipe(tmp_path / "elsewhere", "node-dev")
    assert resolve_recipe("node-dev", recipes_dir=str(tmp_path / "elsewhere")) == (str(chosen), "dockerfile")


def test_resolve_recipe_falls_back_to_bundled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    found = resolve_recipe("node-dev")
    assert found is not None
    assert found[0].endswith("scavenger/dockerfiles/node-dev.Dockerfile")


def test_resolve_recipe_unknown_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_recipe("no-such-recipe") is None


def test_resolve_recipe_has_no_hardcoded_home_path():
    source = open(scavenger.__file__, encoding="utf-8").read()
    assert "/home/lmorchard" not in source


def test_env_create_uses_recipes_dir_from_fleet_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # outside any decafclaw workspace
    recipes = tmp_path / "my-recipes"
    make_recipe(recipes, "node-dev")
    fleet_file = tmp_path / "fleet.json"
    fleet_file.write_text(json.dumps({**FLEET, "recipes_dir": str(recipes)}))

    built = []
    with (
        patch.object(scavenger, "probe_host", return_value=HostStatus(name="desktop", eligible=True)),
        patch.object(scavenger, "ensure_remote_image", side_effect=lambda h, tag, path: built.append(path)),
        patch.object(scavenger, "run_ssh", return_value=completed()),
    ):
        code, out, _ = run_cli(capsys, fleet_file, "env-create", "e1", "--recipe", "node-dev")

    assert code == 0
    assert built == [str(recipes / "node-dev.Dockerfile")]
    assert json.loads(out)["image"] == "scavenger-recipe-node-dev:latest"


def test_env_create_recipes_dir_flag(tmp_path, monkeypatch, capsys, fleet_file):
    monkeypatch.chdir(tmp_path)
    recipes = tmp_path / "flag-recipes"
    make_recipe(recipes, "custom")
    built = []
    with (
        patch.object(scavenger, "probe_host", return_value=HostStatus(name="desktop", eligible=True)),
        patch.object(scavenger, "ensure_remote_image", side_effect=lambda h, tag, path: built.append(path)),
        patch.object(scavenger, "run_ssh", return_value=completed()),
    ):
        code, _, _ = run_cli(
            capsys, fleet_file, "env-create", "e1", "--recipe", "custom", "--recipes-dir", str(recipes)
        )
    assert code == 0
    assert built == [str(recipes / "custom.Dockerfile")]


def test_env_create_unknown_recipe_fails_with_json_error(tmp_path, monkeypatch, capsys, fleet_file):
    monkeypatch.chdir(tmp_path)
    with patch.object(scavenger, "probe_host", return_value=HostStatus(name="desktop", eligible=True)):
        code, out, _ = run_cli(capsys, fleet_file, "env-create", "e1", "--recipe", "nope", "--json")
    assert code == 1
    assert "Recipe not found: nope" in json.loads(out)["error"]


# --- labels ----------------------------------------------------------------


def test_parse_env_labels_new_names():
    assert parse_env_labels({"scavenger.env_id": "a", "scavenger.expires_at": "100"}) == ("a", 100)


def test_parse_env_labels_legacy_names():
    assert parse_env_labels({"decafclaw.env_id": "a", "decafclaw.expires_at": "100"}) == ("a", 100)


def test_parse_env_labels_prefers_new_names():
    labels = {
        "scavenger.env_id": "new",
        "scavenger.expires_at": "200",
        "decafclaw.env_id": "old",
        "decafclaw.expires_at": "100",
    }
    assert parse_env_labels(labels) == ("new", 200)


def test_parse_env_labels_from_engine_string():
    assert parse_env_labels("foo=bar,decafclaw.env_id=a,decafclaw.expires_at=7") == ("a", 7)


@pytest.mark.parametrize(
    "labels",
    [{}, {"scavenger.env_id": "a"}, {"scavenger.env_id": "a", "scavenger.expires_at": "soon"}, "other=1"],
)
def test_parse_env_labels_rejects_incomplete(labels):
    assert parse_env_labels(labels) is None


def test_env_create_writes_scavenger_labels(capsys, fleet_file):
    scripts = []

    def fake_ssh(alias, command, **kwargs):
        scripts.append(command)
        return completed()

    with (
        patch.object(scavenger, "probe_host", return_value=HostStatus(name="desktop", eligible=True)),
        patch.object(scavenger, "run_ssh", side_effect=fake_ssh),
    ):
        code, _, _ = run_cli(capsys, fleet_file, "env-create", "e1", "--host", "desktop")

    assert code == 0
    script = scripts[0]
    assert '--label scavenger.env_id="e1"' in script
    assert "--label scavenger.expires_at=" in script
    assert "decafclaw." not in script


# --- JSON output and exit codes --------------------------------------------


def test_probe_json(capsys, fleet_file):
    status = HostStatus(name="desktop", online=True, eligible=True, reason="Ready")
    with patch.object(scavenger, "probe_host", return_value=status):
        code, out, _ = run_cli(capsys, fleet_file, "probe", "--json")
    assert code == 0
    assert json.loads(out)[0]["name"] == "desktop"


def test_probe_unknown_host_json_error(capsys, fleet_file):
    code, out, _ = run_cli(capsys, fleet_file, "probe", "--host", "ghost", "--json")
    assert code == 1
    assert json.loads(out) == {"error": "Unknown host: ghost"}


def test_missing_fleet_config_fails(capsys, tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--config", str(tmp_path / "missing.json"), "probe", "--json"])
    assert exc.value.code == 1
    assert "No fleet configuration" in json.loads(capsys.readouterr().out)["error"]


def test_error_without_json_goes_to_stderr(capsys, fleet_file):
    code, out, err = run_cli(capsys, fleet_file, "env-info", "ghost")
    assert code == 1
    assert out == ""
    assert "No active lease for environment 'ghost'" in err


def test_wake_json_success(capsys, fleet_file):
    with (
        patch.object(scavenger, "send_magic_packet"),
        patch.object(scavenger, "is_ssh_open", return_value=True),
        patch.object(scavenger.time, "sleep"),
    ):
        code, out, _ = run_cli(capsys, fleet_file, "wake", "desktop", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["host"] == "desktop"
    assert data["awake"] is True


def test_wake_json_failure_exit_code(capsys, fleet_file):
    with (
        patch.object(scavenger, "send_magic_packet"),
        patch.object(scavenger, "is_ssh_open", return_value=False),
        patch.object(scavenger.time, "sleep"),
        patch.object(scavenger.time, "time", side_effect=[0, 0, 5]),
    ):
        code, out, _ = run_cli(capsys, fleet_file, "wake", "desktop", "--json")
    assert code == 1
    assert json.loads(out)["awake"] is False


def test_wake_without_mac_fails(capsys, tmp_path):
    cfg = tmp_path / "f.json"
    cfg.write_text(json.dumps({"hosts": [{"name": "nomac", "ssh_alias": "x"}]}))
    code, out, _ = run_cli(capsys, cfg, "wake", "nomac", "--json")
    assert code == 1
    assert json.loads(out)["awake"] is False


def test_run_json_captures_output_and_exit_code(capsys, fleet_file):
    def fake_execute_job(*, stdout, stderr, **kwargs):
        stdout.write("hello\n")
        stderr.write("warn\n")
        return 3

    with (
        patch.object(scavenger, "probe_host", return_value=HostStatus(name="desktop", eligible=True)),
        patch.object(scavenger, "execute_job", side_effect=fake_execute_job),
    ):
        code, out, _ = run_cli(capsys, fleet_file, "run", "echo hello", "--host", "desktop", "--json")
    assert code == 3
    assert json.loads(out) == {"host": "desktop", "exit_code": 3, "stdout": "hello\n", "stderr": "warn\n"}


def test_run_ineligible_host_fails(capsys, fleet_file):
    status = HostStatus(name="desktop", eligible=False, reason="Laptop running on battery power")
    with patch.object(scavenger, "probe_host", return_value=status):
        code, out, _ = run_cli(capsys, fleet_file, "run", "true", "--host", "desktop", "--json")
    assert code == 1
    assert "battery" in json.loads(out)["error"]


def test_exec_native_json(capsys, fleet_file):
    with patch.object(scavenger, "run_ssh", return_value=completed(stdout="arm64\n", stderr="e", returncode=2)):
        code, out, _ = run_cli(capsys, fleet_file, "exec-native", "desktop", "uname -m", "--json")
    assert code == 2
    assert json.loads(out) == {"host": "desktop", "exit_code": 2, "stdout": "arm64\n", "stderr": "e"}


def test_exec_native_text_passes_output_and_exit_code(capsys, fleet_file):
    with patch.object(scavenger, "run_ssh", return_value=completed(stdout="ok\n", returncode=0)):
        code, out, _ = run_cli(capsys, fleet_file, "exec-native", "desktop", "true")
    assert code == 0
    assert out == "ok\n"


def test_exec_native_timeout(capsys, fleet_file):
    with patch.object(scavenger, "run_ssh", side_effect=subprocess.TimeoutExpired("ssh", 1)):
        code, out, _ = run_cli(capsys, fleet_file, "exec-native", "desktop", "sleep 9", "--timeout", "1", "--json")
    assert code == 124
    assert "timed out" in json.loads(out)["error"]


def test_env_exec_json(capsys, fleet_file):
    add_lease("env1")
    with patch.object(scavenger, "run_ssh", return_value=completed(stdout="built\n")):
        code, out, _ = run_cli(capsys, fleet_file, "env-exec", "env1", "make", "--json")
    assert code == 0
    assert json.loads(out) == {
        "env_id": "env1",
        "host": "desktop",
        "exit_code": 0,
        "stdout": "built\n",
        "stderr": "",
    }


def test_env_exec_propagates_command_failure(capsys, fleet_file):
    add_lease("env1")
    with patch.object(scavenger, "run_ssh", return_value=completed(stderr="boom", returncode=5)):
        code, out, err = run_cli(capsys, fleet_file, "env-exec", "env1", "false")
    assert code == 5
    assert err == "boom"


def test_env_exec_unknown_env_json_error(capsys, fleet_file):
    code, out, _ = run_cli(capsys, fleet_file, "env-exec", "ghost", "true", "--json")
    assert code == 1
    assert "ghost" in json.loads(out)["error"]


def test_env_info_json(capsys, fleet_file):
    add_lease("env1", expires_in=600)
    code, out, _ = run_cli(capsys, fleet_file, "env-info", "env1", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["env_id"] == "env1"
    assert data["host"] == "desktop"
    assert data["expired"] is False
    assert 0 < data["expires_in_seconds"] <= 600


def test_env_info_json_expired(capsys, fleet_file):
    add_lease("env1", expires_in=-10)
    _, out, _ = run_cli(capsys, fleet_file, "env-info", "env1", "--json")
    assert json.loads(out)["expired"] is True


def test_env_info_text(capsys, fleet_file):
    add_lease("env1")
    code, out, _ = run_cli(capsys, fleet_file, "env-info", "env1")
    assert code == 0
    assert "Environment: env1" in out
    assert "scavenger-env-env1" in out


def test_env_sync_json(capsys, fleet_file, tmp_path):
    add_lease("env1")
    with patch.object(scavenger, "env_sync") as sync:
        code, out, _ = run_cli(capsys, fleet_file, "env-sync", "env1", "up", str(tmp_path), "--json")
    assert code == 0
    sync.assert_called_once()
    assert json.loads(out) == {"env_id": "env1", "direction": "up", "local_path": str(tmp_path), "ok": True}


def test_env_sync_failure_exit_code(capsys, fleet_file, tmp_path):
    add_lease("env1")
    with patch.object(scavenger, "env_sync", side_effect=RuntimeError("Sync-up failed: nope")):
        code, out, _ = run_cli(capsys, fleet_file, "env-sync", "env1", "up", str(tmp_path), "--json")
    assert code == 1
    assert "Sync-up failed" in json.loads(out)["error"]


def test_env_destroy_json(capsys, fleet_file):
    add_lease("env1")
    with patch.object(scavenger, "run_ssh", return_value=completed()):
        code, out, _ = run_cli(capsys, fleet_file, "env-destroy", "env1", "--json")
    assert code == 0
    assert json.loads(out) == {"env_id": "env1", "destroyed": True}
    assert scavenger.load_leases() == {}


def test_env_destroy_missing_exit_code(capsys, fleet_file):
    code, out, _ = run_cli(capsys, fleet_file, "env-destroy", "ghost", "--json")
    assert code == 1
    assert json.loads(out) == {"env_id": "ghost", "destroyed": False}


def test_env_list_json(capsys, fleet_file):
    add_lease("env1")
    code, out, _ = run_cli(capsys, fleet_file, "env-list", "--json")
    assert code == 0
    assert list(json.loads(out)) == ["env1"]


def test_reap_json(capsys, fleet_file):
    add_lease("old", expires_in=-10)
    add_lease("fresh", expires_in=3600)
    with patch.object(scavenger, "run_ssh", return_value=completed()):
        code, out, _ = run_cli(capsys, fleet_file, "reap", "--json")
    assert code == 0
    assert json.loads(out) == {"reaped": ["old"]}
    assert list(scavenger.load_leases()) == ["fresh"]


def test_reap_text_nothing_to_do(capsys, fleet_file):
    code, out, _ = run_cli(capsys, fleet_file, "reap")
    assert code == 0
    assert "No expired" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["probe"],
        ["wake", "h"],
        ["run", "c"],
        ["exec-native", "h", "c"],
        ["env-create", "e"],
        ["env-exec", "e", "c"],
        ["env-info", "e"],
        ["env-sync", "e", "up", "d"],
        ["env-destroy", "e"],
        ["env-list"],
        ["reap"],
    ],
)
def test_every_command_accepts_json_flag(argv):
    args = scavenger.build_parser().parse_args([*argv, "--json"])
    assert args.json is True
