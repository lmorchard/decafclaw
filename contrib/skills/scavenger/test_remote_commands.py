"""Remote command construction: pure argv builders, shell quoting, env_id validation."""

import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from contrib.skills.scavenger import scavenger as sc
from contrib.skills.scavenger.scavenger import HostConfig, validate_env_id


def _host(**overrides: Any) -> HostConfig:
    return HostConfig(
        name=overrides.pop("name", "box"),
        ssh_alias=overrides.pop("ssh_alias", "scavenger-box"),
        engine=overrides.pop("engine", "docker"),
        **overrides,
    )


AWKWARD_COMMAND = "pytest -k 'a and b' \"$HOME\" ; echo done"


# ---------------------------------------------------------------------------
# argv builders
# ---------------------------------------------------------------------------


def test_container_run_args_keeps_command_as_one_token():
    argv = sc.container_run_args(_host(), "alpine:latest", ["sh", "-c", AWKWARD_COMMAND])
    assert argv[:2] == ["docker", "run"]
    assert argv[-3:] == ["sh", "-c", AWKWARD_COMMAND]
    assert "-c" in argv


def test_container_run_args_round_trips_through_the_shell():
    argv = sc.container_run_args(
        _host(),
        "my image:latest",
        ["sh", "-c", AWKWARD_COMMAND],
        volume="/tmp/job dir/workspace:/workspace",
        workdir="/workspace",
        mem_limit="2g",
        cpus=4,
    )
    parsed = shlex_split(sc.remote_argv(argv))
    assert parsed == argv
    assert parsed[-1] == AWKWARD_COMMAND


def test_container_run_args_carries_limits_labels_and_volume():
    argv = sc.container_run_args(
        _host(device_flags=["--device", "/dev/dri"], needs_selinux_z=True),
        "alpine",
        ["sleep", "infinity"],
        name="scavenger-env-e1",
        detached=True,
        remove=False,
        volume="scavenger-vol-e1:/workspace:Z",
        workdir="/workspace",
        mem_limit="3g",
        cpus=2,
        network="none",
        labels={"scavenger.env_id": "e1"},
    )
    assert argv[:2] == ["docker", "run"]
    assert "-d" in argv and "--rm" not in argv
    assert ["--name", "scavenger-env-e1"] == _pair(argv, "--name")
    assert "--memory=3g" in argv
    assert "--cpus=2" in argv
    assert "--network=none" in argv
    assert "--pids-limit=4096" in argv
    assert ["--device", "/dev/dri"] == _pair(argv, "--device")
    assert ["--label", "scavenger.env_id=e1"] == _pair(argv, "--label")
    assert ["-v", "scavenger-vol-e1:/workspace:Z"] == _pair(argv, "-v")
    assert ["-w", "/workspace"] == _pair(argv, "-w")
    assert argv[-2:] == ["sleep", "infinity"]


def test_container_exec_args_passes_command_through_one_shell_word():
    argv = sc.container_exec_args(_host(), "scavenger-env-e1", AWKWARD_COMMAND, interactive=True)
    assert argv[:3] == ["docker", "exec", "-i"]
    assert argv[3] == "scavenger-env-e1"
    assert argv[4:] == ["sh", "-c", AWKWARD_COMMAND]
    assert shlex_split(sc.remote_argv(argv))[-1] == AWKWARD_COMMAND


def test_container_exec_args_quotes_env_vars():
    argv = sc.container_exec_args(
        _host(),
        "scavenger-env-e1",
        "true",
        env_vars={"B": "two words", "A": "a'b"},
    )
    assert argv[2:] == [
        "-e",
        "A=a'b",
        "-e",
        "B=two words",
        "scavenger-env-e1",
        "sh",
        "-c",
        "true",
    ]
    parsed = shlex_split(sc.remote_argv(argv))
    assert "A=a'b" in parsed
    assert "B=two words" in parsed


def test_container_rm_args():
    assert sc.container_rm_args(_host(), "scavenger-env-e1") == ["docker", "rm", "-f", "scavenger-env-e1"]


def test_volume_create_args_labels_volume():
    argv = sc.volume_create_args(_host(engine="podman"), "scavenger-vol-e1", {"k": "v with space"})
    assert argv == ["podman", "volume", "create", "--label", "k=v with space", "scavenger-vol-e1"]
    assert shlex_split(sc.remote_argv(argv)) == argv


def test_volume_rm_args():
    assert sc.volume_rm_args(_host(), "scavenger-vol-e1") == ["docker", "volume", "rm", "-f", "scavenger-vol-e1"]


def test_image_exists_args_per_engine():
    assert sc.image_exists_args(_host(engine="podman"), "img:1") == ["podman", "image", "exists", "img:1"]
    assert sc.image_exists_args(_host(engine="docker"), "img:1") == ["docker", "image", "inspect", "img:1"]


def test_image_build_args():
    argv = sc.image_build_args(_host(), "scavenger-recipe-x:abc")
    assert argv == ["docker", "build", "-t", "scavenger-recipe-x:abc", "-f", "-", "/tmp"]


def test_list_args():
    assert sc.list_containers_args(_host(engine="podman")) == [
        "podman",
        "ps",
        "--all",
        "--format",
        "json",
    ]
    assert sc.list_volumes_args(_host()) == ["docker", "volume", "ls", "--format", "json"]


def test_list_host_script_uses_quoted_engine_commands():
    script = sc.list_host_script(_host(engine="podman"))
    assert "podman ps --all --format json" in script
    assert "podman volume ls --format json" in script
    assert 'for f in "$HOME"/workspaces/*/' in script


# ---------------------------------------------------------------------------
# remote scripts built from argv
# ---------------------------------------------------------------------------


def test_job_remote_script_quotes_the_command():
    script = sc.job_remote_script(
        _host(),
        AWKWARD_COMMAND,
        "/tmp/scavenger-job-test",
        image="my image:latest",
        mem_limit="2g",
        cpus=2,
    )
    run_line = next(line for line in script.splitlines() if " run " in line)
    argv = shlex_split(run_line)
    assert argv[-1] == AWKWARD_COMMAND
    assert argv[-3:-1] == ["sh", "-c"]
    assert "my image:latest" in argv
    assert "2g" in " ".join(argv)


def test_job_remote_script_cleans_a_literal_job_dir():
    script = sc.job_remote_script(_host(), "true", "/tmp/scavenger-job-test")
    assert "/tmp/scavenger-job-test" in script
    assert "mktemp" not in script
    assert "trap" in script


def test_execute_job_quotes_command_in_remote_script():
    with patch.object(sc.subprocess, "run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="", stderr="")
        sc.execute_job(_host(engine="podman"), command=AWKWARD_COMMAND)
    script = mock_run.call_args[0][0][-1]
    run_line = next(line for line in script.splitlines() if " run " in line)
    assert shlex_split(run_line)[-1] == AWKWARD_COMMAND


def test_env_exec_quotes_command_for_container():
    seen = {}

    def fake_run_ssh(alias, command, **kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    fleet = {"box": _host()}
    lease = sc.EnvReservation(
        env_id="e1",
        host="box",
        kind="container",
        created_at=0.0,
        expires_at=10**11,
        ttl_seconds=60,
        container_name="scavenger-env-e1",
    )
    with (
        patch.object(sc, "run_ssh", fake_run_ssh),
        patch.object(sc, "load_env_vars", return_value={}),
    ):
        sc.env_exec(fleet, lease, AWKWARD_COMMAND)
    argv = shlex_split(seen["command"])
    assert argv[-1] == AWKWARD_COMMAND
    assert argv[-3:-1] == ["sh", "-c"]


def test_env_create_quotes_image_and_volume_names():
    recorded = []

    def fake_run_ssh(alias, command, **kwargs):
        recorded.append(command)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    fleet = {"box": _host()}
    with (
        patch.object(sc, "env_reap", lambda f: []),
        patch.object(sc, "find_lease", lambda f, e: None),
        patch.object(sc, "probe_host", return_value=sc.HostStatus(name="box", eligible=True)),
        patch.object(sc, "run_ssh", fake_run_ssh),
        patch.object(sc, "update_leases", lambda fn: None),
    ):
        sc.env_create(fleet, "e1", host_name="box", image="my image:latest", mem_limit="3g")

    create_line = next(cmd for cmd in recorded if " run -d " in cmd)
    argv = shlex_split(create_line)
    assert "my image:latest" in argv
    assert "--memory=3g" in argv
    assert "scavenger-vol-e1:/workspace" in argv
    assert "scavenger-env-e1" in argv


# ---------------------------------------------------------------------------
# env_id validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_id", ["e1", "my_env.1", "a" * 63, "A"])
def test_valid_env_ids_are_accepted(env_id):
    assert validate_env_id(env_id) == env_id


@pytest.mark.parametrize(
    "env_id",
    ["", "-leading", ".dot", "with space", "semi;colon", "dollar$var", "a/b", "ümlaut", "a" * 64],
)
def test_invalid_env_ids_are_refused(env_id):
    with pytest.raises(ValueError, match="Invalid env_id"):
        validate_env_id(env_id)


def test_env_create_core_refuses_invalid_env_id():
    with pytest.raises(ValueError, match="Invalid env_id"):
        sc.env_create({"box": _host()}, "bad;id", host_name="box")


def test_scavenger_env_create_tool_refuses_invalid_env_id():
    from contrib.skills.scavenger.tools import scavenger_env_create

    ctx = MagicMock()
    with patch("contrib.skills.scavenger.tools.load_fleet_config", return_value={"box": _host()}):
        result = scavenger_env_create(ctx, env_id="bad;id", host="box")
    assert result.startswith("Error:")
    assert "Invalid env_id" in result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def shlex_split(text):
    import shlex

    return shlex.split(text)


def _pair(argv, flag):
    index = argv.index(flag)
    return argv[index : index + 2]
