"""Tests for the standalone scavenger CLI: recipe dir order, label names, JSON output."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from unittest.mock import patch

import pytest

from contrib.skills.scavenger import scavenger as sc
from contrib.skills.scavenger.scavenger import EnvReservation, HostConfig, HostStatus

HOST = HostConfig(name="box", ssh_alias="scavenger-box", engine="docker", wol_mac="AA:BB:CC:DD:EE:01")


def _write_fleet(path, **extra):
    data = {"hosts": [{"name": "box", "ssh_alias": "scavenger-box"}], **extra}
    path.write_text(json.dumps(data))
    return str(path)


def _lease(env_id="e1", expires_at=None, kind="container"):
    return EnvReservation(
        env_id=env_id,
        host="box",
        kind=kind,
        created_at=100.0,
        expires_at=expires_at if expires_at is not None else 10**11,
        ttl_seconds=60,
        container_name=f"scavenger-env-{env_id}",
        workspace_path=f"workspaces/{env_id}",
    )


@pytest.fixture
def leases_file(tmp_path, monkeypatch):
    path = tmp_path / "leases.json"
    monkeypatch.setattr(sc, "LEASES_FILE", str(path))
    return path


def run_cli(capsys, *argv):
    code = sc.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


# --- recipes dir order -------------------------------------------------------


def test_recipes_dir_flag_beats_config_and_default(tmp_path):
    cfg = _write_fleet(tmp_path / "fleet.json", recipes_dir="from-config")
    assert sc.resolve_recipes_dir(str(tmp_path / "flag"), cfg, default="dflt") == str(tmp_path / "flag")


def test_recipes_dir_config_beats_default_and_is_relative_to_config(tmp_path):
    cfg = _write_fleet(tmp_path / "fleet.json", recipes_dir="recipes")
    assert sc.resolve_recipes_dir(None, cfg, default="dflt") == str(tmp_path / "recipes")


def test_recipes_dir_absolute_config(tmp_path):
    cfg = _write_fleet(tmp_path / "fleet.json", recipes_dir=str(tmp_path / "abs"))
    assert sc.resolve_recipes_dir(None, cfg) == str(tmp_path / "abs")


def test_recipes_dir_falls_back_to_cwd_dockerfiles(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = _write_fleet(tmp_path / "fleet.json")
    assert sc.resolve_recipes_dir(None, cfg) == str(tmp_path / "dockerfiles")


def test_resolve_recipe_uses_recipes_dir_without_hardcoded_path(tmp_path):
    (tmp_path / "mine.Dockerfile").write_text("FROM alpine\n")
    resolved = sc.resolve_recipe("mine", str(tmp_path))
    assert resolved is not None
    path, kind = resolved
    assert path == str(tmp_path / "mine.Dockerfile")
    assert kind == "dockerfile"
    assert sc.resolve_recipe("does-not-exist", str(tmp_path)) is None


def test_resolve_recipe_finds_bundled_recipe_from_any_cwd(tmp_path):
    resolved = sc.resolve_recipe("node-dev", str(tmp_path / "empty"))
    assert resolved is not None
    path, kind = resolved
    assert path.endswith("node-dev.Dockerfile")
    assert kind == "dockerfile"


def test_bundled_decafclaw_test_recipe_resolves(tmp_path):
    """The test-gate recipe must stay reachable from the bundled dockerfiles dir.

    It is only discovered through the SCRIPT_DIR fallback (no fleet
    `recipes_dir`), so a rename or a move out of this directory makes it
    silently unreachable rather than loudly broken.
    """
    resolved = sc.resolve_recipe("decafclaw-test", str(tmp_path / "empty"))
    assert resolved is not None
    path, kind = resolved
    assert path.endswith("decafclaw-test.Dockerfile")
    assert kind == "dockerfile"


def test_decafclaw_test_recipe_keeps_its_load_bearing_packages():
    """Pin the packages that only exist to stop false test failures.

    Each of these was added because its absence produced failures that looked
    like code bugs (see the Dockerfile header). Dropping one is invisible until
    a remote run reports 4-30 red tests, so guard them here where it is cheap.

    Scoped to the apt-get install line on purpose: a whole-file substring search
    also matches the explanatory header and the ENTRYPOINT path, so it would
    pass with the packages removed. That false pass was observed, not assumed.
    """
    resolved = sc.resolve_recipe("decafclaw-test", "")
    assert resolved is not None
    body = open(resolved[0], encoding="utf-8").read()

    # Join continuation lines and drop comments, so the install command reads as
    # one string instead of being matched against prose in the header.
    code = re.sub(r"\\\n\s*", " ", body)
    code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("#"))

    install = re.search(r"apt-get install[^\n]*", code)
    assert install is not None, "no apt-get install line found"
    installed = install.group(0)

    for package in ("git", "tini", "openssh-client"):
        assert package in installed, f"{package} missing from the apt-get install line"

    # tini must actually be PID 1, not merely installed.
    assert 'ENTRYPOINT ["/usr/bin/tini", "--"]' in body
    # A uv-managed interpreter is required for a SQLite that sqlite-vec accepts.
    assert "uv python install 3.13" in body


def test_no_hardcoded_home_path_in_scavenger_sources():
    for name in ("scavenger.py", "tools.py"):
        with open(f"{sc.SCRIPT_DIR}/{name}", encoding="utf-8") as f:
            assert "/home/lmorchard" not in f.read()


def test_env_create_unknown_recipe_errors(tmp_path, leases_file):
    with patch.object(sc, "probe_host", return_value=HostStatus(name="box", eligible=True)):
        with pytest.raises(ValueError, match="not found"):
            sc.env_create({"box": HOST}, "e1", host_name="box", recipe="nope", recipes_dir=str(tmp_path))


def test_cli_env_create_uses_fleet_recipes_dir(tmp_path, leases_file, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # outside any decafclaw workspace
    recipes = tmp_path / "my-recipes"
    recipes.mkdir()
    (recipes / "custom.Dockerfile").write_text("FROM alpine\n")
    cfg = _write_fleet(tmp_path / "fleet.json", recipes_dir="my-recipes")

    built = []
    with (
        patch.object(sc, "probe_host", return_value=HostStatus(name="box", eligible=True)),
        patch.object(sc, "ensure_remote_image", side_effect=lambda h, tag, p: built.append((tag, p))),
        patch.object(
            sc, "run_ssh", return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        ) as ssh,
    ):
        code, out, _ = run_cli(capsys, "--config", cfg, "env-create", "e1", "--recipe", "custom")

    assert code == 0
    # The tag is content-addressed, so an edited recipe builds instead of
    # silently reusing a stale image (the bug that kept this image on the
    # legacy 1.x OpenCode package).
    image = json.loads(out)["image"]
    assert image.startswith("scavenger-recipe-custom:")
    assert image != "scavenger-recipe-custom:latest"
    assert built == [(image, str(recipes / "custom.Dockerfile"))]
    create_script = ssh.call_args_list[-1].args[1]
    assert "scavenger.env_id=e1" in create_script
    assert "scavenger.expires_at=" in create_script
    assert "decafclaw." not in create_script


def test_recipe_image_tag_changes_when_the_dockerfile_changes(tmp_path):
    """Editing a recipe must produce a new tag, or the edit never reaches a container."""
    recipe = tmp_path / "open-code.Dockerfile"
    recipe.write_text("FROM alpine\n", encoding="utf-8")
    first = sc.recipe_image_tag(str(recipe))

    recipe.write_text("FROM alpine\nRUN true\n", encoding="utf-8")
    second = sc.recipe_image_tag(str(recipe))

    assert first != second
    assert not first.endswith(":latest")
    assert first.startswith("scavenger-recipe-open-code:")


def test_recipe_image_tag_is_stable_for_identical_content(tmp_path):
    """Re-creating an unchanged recipe reuses the cached image instead of rebuilding."""
    one = tmp_path / "a" / "same.Dockerfile"
    two = tmp_path / "b" / "same.Dockerfile"
    one.parent.mkdir()
    two.parent.mkdir()
    one.write_text("FROM alpine\n", encoding="utf-8")
    two.write_text("FROM alpine\n", encoding="utf-8")

    assert sc.recipe_image_tag(str(one)) == sc.recipe_image_tag(str(two))


# --- labels ------------------------------------------------------------------


def test_read_env_labels_new_names():
    assert sc.read_env_labels({"scavenger.env_id": "a", "scavenger.expires_at": "123"}) == ("a", 123)


def test_read_env_labels_legacy_names():
    assert sc.read_env_labels({"decafclaw.env_id": "a", "decafclaw.expires_at": "123"}) == ("a", 123)


def test_read_env_labels_new_wins_and_bad_values_tolerated():
    labels = {"scavenger.env_id": "new", "decafclaw.env_id": "old", "scavenger.expires_at": "x"}
    assert sc.read_env_labels(labels) == ("new", None)
    assert sc.read_env_labels({}) == (None, None)


# --- JSON output / exit codes ------------------------------------------------


def test_probe_json(tmp_path, capsys):
    cfg = _write_fleet(tmp_path / "fleet.json")
    with patch.object(sc, "probe_host", return_value=HostStatus(name="box", online=True, eligible=True)):
        code, out, _ = run_cli(capsys, "--config", cfg, "probe", "--json", "--no-wake")
    assert code == 0
    assert json.loads(out)[0]["name"] == "box"


def test_probe_unknown_host_fails_with_json_error(tmp_path, capsys):
    cfg = _write_fleet(tmp_path / "fleet.json")
    code, out, _ = run_cli(capsys, "--config", cfg, "probe", "--host", "ghost", "--json")
    assert code == 1
    assert "ghost" in json.loads(out)["error"]


def test_missing_fleet_config_fails_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sc.os.path, "expanduser", lambda p: str(tmp_path / "nope" / p.lstrip("~/")))
    monkeypatch.chdir(tmp_path)
    code, _, err = run_cli(capsys, "probe")
    assert code == 1
    assert "No fleet configuration" in err


@pytest.mark.parametrize("awake,expected_code", [(True, 0), (False, 1)])
def test_wake_json(tmp_path, capsys, awake, expected_code):
    cfg = _write_fleet(tmp_path / "fleet.json")
    result = {"host": "box", "awake": awake, "message": "m"}
    with patch.object(sc, "wake_host", return_value=result):
        code, out, _ = run_cli(capsys, "--config", cfg, "wake", "box", "--json")
    assert code == expected_code
    assert json.loads(out) == result


def test_wake_host_without_mac_is_not_awake():
    res = sc.wake_host(HostConfig(name="x", ssh_alias="x"))
    assert res["awake"] is False
    assert "wol_mac" in res["message"]


def test_wake_host_reports_awake():
    with (
        patch.object(sc, "send_magic_packet") as send,
        patch.object(sc, "is_ssh_open", return_value=True),
        patch.object(sc.time, "sleep"),
    ):
        res = sc.wake_host(HOST)
    send.assert_called_once()
    assert res["awake"] is True


def test_exec_native_json_and_exit_code(tmp_path, capsys):
    cfg = _write_fleet(tmp_path / "fleet.json")
    proc = subprocess.CompletedProcess(args=[], returncode=3, stdout="o", stderr="e")
    with patch.object(sc, "run_ssh", return_value=proc):
        code, out, _ = run_cli(capsys, "--config", cfg, "exec-native", "box", "uname", "--json")
    assert code == 3
    assert json.loads(out) == {"host": "box", "exit_code": 3, "stdout": "o", "stderr": "e"}


def test_exec_native_unknown_host(tmp_path, capsys):
    cfg = _write_fleet(tmp_path / "fleet.json")
    code, _, err = run_cli(capsys, "--config", cfg, "exec-native", "ghost", "uname")
    assert code == 1
    assert "ghost" in err


def test_run_json_captures_output(tmp_path, capsys):
    cfg = _write_fleet(tmp_path / "fleet.json")

    def fake_job(host, command, stdout, stderr, **kw):
        stdout.write("hello\n")
        stderr.write("warn\n")
        return 7

    with (
        patch.object(sc, "probe_host", return_value=HostStatus(name="box", eligible=True)),
        patch.object(sc, "execute_job", side_effect=fake_job),
    ):
        code, out, _ = run_cli(capsys, "--config", cfg, "run", "echo hi", "--host", "box", "--json")
    assert code == 7
    assert json.loads(out) == {"host": "box", "exit_code": 7, "stdout": "hello\n", "stderr": "warn\n"}


def test_run_ineligible_host_fails(tmp_path, capsys):
    cfg = _write_fleet(tmp_path / "fleet.json")
    with patch.object(sc, "probe_host", return_value=HostStatus(name="box", eligible=False, reason="busy")):
        code, _, err = run_cli(capsys, "--config", cfg, "run", "x", "--host", "box")
    assert code == 1
    assert "busy" in err


def test_env_info_json_and_missing(tmp_path, leases_file, capsys):
    sc.save_leases({"e1": _lease()})
    code, out, _ = run_cli(capsys, "env-info", "e1", "--json")
    assert code == 0
    info = json.loads(out)
    assert info["env_id"] == "e1"
    assert info["expires_in_seconds"] > 0

    code, out, _ = run_cli(capsys, "env-info", "ghost", "--json")
    assert code == 1
    assert "ghost" in json.loads(out)["error"]


def test_env_list_json_and_text(leases_file, capsys):
    sc.save_leases({"e1": _lease()})
    code, out, _ = run_cli(capsys, "env-list", "--json")
    assert code == 0 and "e1" in json.loads(out)
    code, out, _ = run_cli(capsys, "env-list")
    assert code == 0 and "ENV_ID" in out


def test_env_exec_json_and_exit_code(tmp_path, leases_file, capsys):
    cfg = _write_fleet(tmp_path / "fleet.json")
    sc.save_leases({"e1": _lease()})
    proc = subprocess.CompletedProcess(args=[], returncode=2, stdout="out", stderr="err")
    with patch.object(sc, "env_exec", return_value=proc):
        code, out, _ = run_cli(capsys, "--config", cfg, "env-exec", "e1", "ls", "--json")
    assert code == 2
    assert json.loads(out) == {"env_id": "e1", "exit_code": 2, "stdout": "out", "stderr": "err"}


def test_env_exec_missing_lease_fails(tmp_path, leases_file, capsys):
    cfg = _write_fleet(tmp_path / "fleet.json")
    code, _, err = run_cli(capsys, "--config", cfg, "env-exec", "ghost", "ls")
    assert code == 1
    assert "ghost" in err


def test_env_sync_json(tmp_path, leases_file, capsys):
    cfg = _write_fleet(tmp_path / "fleet.json")
    sc.save_leases({"e1": _lease()})
    with patch.object(sc, "env_sync") as sync:
        code, out, _ = run_cli(capsys, "--config", cfg, "env-sync", "e1", "up", str(tmp_path), "--json")
    assert code == 0
    sync.assert_called_once()
    assert json.loads(out)["ok"] is True


def test_env_destroy_json_and_failure(tmp_path, leases_file, capsys):
    cfg = _write_fleet(tmp_path / "fleet.json")
    sc.save_leases({"e1": _lease()})
    success = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch.object(sc, "run_ssh", return_value=success):
        code, out, _ = run_cli(capsys, "--config", cfg, "env-destroy", "e1", "--json")
    assert code == 0
    assert json.loads(out) == {"env_id": "e1", "destroyed": True}

    code, out, _ = run_cli(capsys, "--config", cfg, "env-destroy", "e1", "--json")
    assert code == 1
    assert "error" in json.loads(out)


def test_reap_json(tmp_path, leases_file, capsys):
    cfg = _write_fleet(tmp_path / "fleet.json")
    sc.save_leases({"old": _lease("old", expires_at=1.0), "new": _lease("new")})
    rows = [
        {
            "Names": ["scavenger-env-old"],
            "Image": "alpine",
            "Labels": {
                "scavenger.env_id": "old",
                "scavenger.expires_at": "1",
                "scavenger.created_at": "100",
                "scavenger.ttl_seconds": "60",
            },
        },
        {
            "Names": ["scavenger-env-new"],
            "Image": "alpine",
            "Labels": {
                "scavenger.env_id": "new",
                "scavenger.expires_at": str(10**11),
                "scavenger.created_at": "100",
                "scavenger.ttl_seconds": "60",
            },
        },
    ]
    listing = (
        "---CONTAINERS---\n"
        + "\n".join(json.dumps(row) for row in rows)
        + "\n---VOLUMES---\n\n---NATIVE---\n\n---END---\n"
    )

    def fake_ssh(alias, command, timeout=15, capture=True):
        if "---CONTAINERS---" in command:
            return subprocess.CompletedProcess([], 0, listing, "")
        return subprocess.CompletedProcess([], 0, "", "")

    with patch.object(sc, "run_ssh", side_effect=fake_ssh):
        code, out, _ = run_cli(capsys, "--config", cfg, "reap", "--json")
    assert code == 0
    assert json.loads(out) == {"reaped": ["old"]}


def test_every_subcommand_accepts_json():
    parser = sc.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for name, p in sub.choices.items():
        assert any("--json" in a.option_strings for a in p._actions), name
