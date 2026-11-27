"""Tests for per-environment variable storage and propagation."""

import json
import os
import shlex
import stat

import pytest

from contrib.skills.scavenger import scavenger as sc
from contrib.skills.scavenger.scavenger import EnvReservation, HostConfig, HostStatus


@pytest.fixture
def env_store(tmp_path, monkeypatch):
    path = tmp_path / "env-vars.json"
    monkeypatch.setattr(sc, "ENV_VARS_FILE", str(path))
    return path


def _lease(env_id="e1", kind="container"):
    return EnvReservation(
        env_id=env_id,
        host="box",
        kind=kind,
        created_at=100.0,
        expires_at=10**11,
        ttl_seconds=60,
        container_name=f"scavenger-env-{env_id}",
        workspace_path=f"workspaces/{env_id}",
    )


# --- store -------------------------------------------------------------------


def test_save_and_load_roundtrip(env_store):
    sc.save_env_vars("e1", {"OPENAI_API_KEY": "sk-1", "OPENAI_BASE_URL": "http://gw/v1"})
    assert sc.load_env_vars("e1") == {"OPENAI_API_KEY": "sk-1", "OPENAI_BASE_URL": "http://gw/v1"}


def test_store_file_is_mode_0600(env_store):
    sc.save_env_vars("e1", {"SECRET": "value"})
    mode = stat.S_IMODE(os.stat(env_store).st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_load_missing_env_returns_empty(env_store):
    assert sc.load_env_vars("nope") == {}


def test_load_tolerates_corrupt_file(env_store):
    env_store.write_text("{not json", encoding="utf-8")
    assert sc.load_env_vars("e1") == {}


def test_save_empty_mapping_clears_entry(env_store):
    sc.save_env_vars("e1", {"A": "1"})
    sc.save_env_vars("e1", {})
    assert sc.load_env_vars("e1") == {}
    assert json.loads(env_store.read_text(encoding="utf-8")) == {}


def test_drop_env_vars_removes_only_target(env_store):
    sc.save_env_vars("e1", {"A": "1"})
    sc.save_env_vars("e2", {"B": "2"})
    sc.drop_env_vars("e1")
    assert sc.load_env_vars("e1") == {}
    assert sc.load_env_vars("e2") == {"B": "2"}


def test_drop_env_vars_is_safe_when_absent(env_store):
    sc.drop_env_vars("ghost")


# --- parsing -----------------------------------------------------------------


def test_parse_env_assignments_accepts_repeated_pairs():
    assert sc.parse_env_assignments(["A=1", "B=two"]) == {"A": "1", "B": "two"}


def test_parse_env_assignments_keeps_equals_in_value():
    assert sc.parse_env_assignments(["TOKEN=a=b=c"]) == {"TOKEN": "a=b=c"}


def test_parse_env_assignments_allows_empty_value():
    assert sc.parse_env_assignments(["EMPTY="]) == {"EMPTY": ""}


def test_parse_env_assignments_none_is_empty():
    assert sc.parse_env_assignments(None) == {}


@pytest.mark.parametrize("bad", ["NOEQUALS", "=value", "1BAD=x", "A-B=x"])
def test_parse_env_assignments_rejects_malformed(bad):
    with pytest.raises(ValueError):
        sc.parse_env_assignments([bad])


# --- propagation -------------------------------------------------------------


def test_env_exec_passes_flags_into_container(monkeypatch, env_store):
    sc.save_env_vars("e1", {"OPENAI_API_KEY": "sk-1"})
    seen = {}

    def fake_run_ssh(alias, cmd, **kwargs):
        seen["cmd"] = cmd
        return sc.subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sc, "run_ssh", fake_run_ssh)
    fleet = {"box": HostConfig(name="box", ssh_alias="scavenger-box", engine="docker")}
    sc.env_exec(fleet, _lease(), "printenv OPENAI_API_KEY")

    assert "-e OPENAI_API_KEY=sk-1" in seen["cmd"]
    assert "docker exec -i" in seen["cmd"]


def test_env_exec_container_without_vars_has_no_flags(monkeypatch, env_store):
    seen = {}

    def fake_run_ssh(alias, cmd, **kwargs):
        seen["cmd"] = cmd
        return sc.subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sc, "run_ssh", fake_run_ssh)
    fleet = {"box": HostConfig(name="box", ssh_alias="scavenger-box", engine="docker")}
    sc.env_exec(fleet, _lease(), "true")

    assert "-e " not in seen["cmd"]


def test_env_exec_native_exports_vars(monkeypatch, env_store):
    sc.save_env_vars("n1", {"A": "one"})
    seen = {}

    def fake_run_ssh(alias, cmd, **kwargs):
        seen["cmd"] = cmd
        return sc.subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sc, "run_ssh", fake_run_ssh)
    fleet = {"box": HostConfig(name="box", ssh_alias="scavenger-box", os="darwin")}
    sc.env_exec(fleet, _lease("n1", kind="native"), "printenv A")

    assert "export A=one &&" in seen["cmd"]


def test_env_exec_quotes_values_with_spaces(monkeypatch, env_store):
    sc.save_env_vars("e1", {"MSG": "hello world"})
    seen = {}

    def fake_run_ssh(alias, cmd, **kwargs):
        seen["cmd"] = cmd
        return sc.subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sc, "run_ssh", fake_run_ssh)
    fleet = {"box": HostConfig(name="box", ssh_alias="scavenger-box", engine="docker")}
    sc.env_exec(fleet, _lease(), "true")

    assert "-e 'MSG=hello world'" in seen["cmd"]


def test_env_exec_survives_embedded_single_quotes(monkeypatch, env_store):
    """A command containing single quotes must reach the remote shell intact.

    Regression: env_exec wrapped the command in literal single quotes with no
    escaping, so `python3 -c "print('x')"` or `awk '{print $1}'` arrived
    truncated and failed with "unterminated quoted string". The error pointed
    at the caller's quoting, not at this builder.
    """
    seen = {}

    def fake_run_ssh(alias, cmd, **kwargs):
        seen["cmd"] = cmd
        return sc.subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sc, "run_ssh", fake_run_ssh)
    fleet = {"box": HostConfig(name="box", ssh_alias="scavenger-box", engine="docker")}
    command = "awk '{print $1}' /tmp/x && python3 -c \"print('ok')\""
    sc.env_exec(fleet, _lease(), command)

    # The remote shell must receive exactly one shell-quoted argument, so
    # evaluating it reproduces the original command byte for byte.
    prefix = "docker exec -i scavenger-env-e1 sh -c "
    assert seen["cmd"].startswith(prefix)
    quoted = seen["cmd"][len(prefix) :]
    assert shlex.split(quoted) == [command]


def test_env_exec_cmd_is_not_double_wrapped(monkeypatch, env_store):
    """A plain command must not be quoted in a way that changes its meaning."""
    seen = {}

    def fake_run_ssh(alias, cmd, **kwargs):
        seen["cmd"] = cmd
        return sc.subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sc, "run_ssh", fake_run_ssh)
    fleet = {"box": HostConfig(name="box", ssh_alias="scavenger-box", engine="docker")}
    sc.env_exec(fleet, _lease(), "true")

    prefix = "docker exec -i scavenger-env-e1 sh -c "
    assert shlex.split(seen["cmd"][len(prefix) :]) == ["true"]


def test_env_exec_bg_carries_vars_for_native(monkeypatch, env_store):
    sc.save_env_vars("n1", {"A": "one"})
    seen = {}

    def fake_run_ssh(alias, cmd, **kwargs):
        seen["cmd"] = cmd
        return sc.subprocess.CompletedProcess(args=[], returncode=0, stdout="123\n", stderr="")

    monkeypatch.setattr(sc, "run_ssh", fake_run_ssh)
    fleet = {"box": HostConfig(name="box", ssh_alias="scavenger-box", os="darwin")}
    sc.env_exec_bg(fleet, _lease("n1", kind="native"), "printenv A")

    # The exports ride inside the base64-wrapped script for native environments.
    import base64
    import shlex

    b64 = next(token for token in shlex.split(seen["cmd"]) if "base64 -d" in token).split()[1]
    inner = base64.b64decode(b64).decode()
    assert "export A=one" in inner


# --- lifecycle ---------------------------------------------------------------


def test_env_create_persists_vars(monkeypatch, env_store):
    fleet = {"box": HostConfig(name="box", ssh_alias="scavenger-box", engine="docker")}
    monkeypatch.setattr(sc, "env_reap", lambda f: [])
    monkeypatch.setattr(sc, "find_lease", lambda f, e: None)
    monkeypatch.setattr(sc, "probe_host", lambda h, try_wake=True: HostStatus(name="box", eligible=True))
    monkeypatch.setattr(sc, "run_ssh", lambda *a, **k: sc.subprocess.CompletedProcess([], 0, "", ""))

    sc.env_create(fleet, "e1", host_name="box", env_vars={"A": "1"})

    assert sc.load_env_vars("e1") == {"A": "1"}


def test_env_create_does_not_put_vars_in_labels(monkeypatch, env_store):
    fleet = {"box": HostConfig(name="box", ssh_alias="scavenger-box", engine="docker")}
    seen = {}

    def fake_run_ssh(alias, cmd, **kwargs):
        seen.setdefault("cmds", []).append(cmd)
        return sc.subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(sc, "env_reap", lambda f: [])
    monkeypatch.setattr(sc, "find_lease", lambda f, e: None)
    monkeypatch.setattr(sc, "probe_host", lambda h, try_wake=True: HostStatus(name="box", eligible=True))
    monkeypatch.setattr(sc, "run_ssh", fake_run_ssh)

    sc.env_create(fleet, "e1", host_name="box", env_vars={"SECRET_TOKEN": "sk-do-not-label"})

    joined = "\n".join(seen["cmds"])
    assert "sk-do-not-label" not in joined, "secret leaked into a remote command or label"


def test_env_create_existing_lease_updates_vars(monkeypatch, env_store):
    fleet = {"box": HostConfig(name="box", ssh_alias="scavenger-box", engine="docker")}
    existing = _lease("e1")
    monkeypatch.setattr(sc, "env_reap", lambda f: [])
    monkeypatch.setattr(sc, "find_lease", lambda f, e: existing)

    res = sc.env_create(fleet, "e1", env_vars={"NEW": "value"})

    assert res is existing
    assert sc.load_env_vars("e1") == {"NEW": "value"}


def test_env_destroy_drops_vars(monkeypatch, env_store):
    sc.save_env_vars("e1", {"A": "1"})
    fleet = {"box": HostConfig(name="box", ssh_alias="scavenger-box", engine="docker")}
    monkeypatch.setattr(sc, "find_lease", lambda f, e: _lease("e1"))
    monkeypatch.setattr(sc, "run_ssh", lambda *a, **k: sc.subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(sc, "update_leases", lambda fn: None)

    assert sc.env_destroy(fleet, "e1") is True
    assert sc.load_env_vars("e1") == {}
