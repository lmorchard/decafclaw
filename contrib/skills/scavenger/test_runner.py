"""Tests for the OpenCode Scavenger runner adapter."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contrib.skills.scavenger.runners import opencode_scavenger_runner as runner
from contrib.skills.scavenger.tools import scavenger_runner_path


def test_collect_env_vars_defaults_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-anthropic-key")
    monkeypatch.setenv("GH_TOKEN", "env-gh-token")

    env = runner.collect_env_vars(config_path=tmp_path / "missing.json")
    assert env["OPENAI_API_KEY"] == "env-openai-key"
    assert env["ANTHROPIC_API_KEY"] == "env-anthropic-key"
    assert env["GH_TOKEN"] == "env-gh-token"
    assert env["GITHUB_TOKEN"] == "env-gh-token"


def test_collect_env_vars_reads_decafclaw_config(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "providers": {
                    "openai": {
                        "api_key": "cfg-openai-key",
                        "url": "https://us.api.openai.com/v1/responses",
                    },
                    "anthropic": {
                        "api_key": "cfg-anthropic-key",
                        "url": "https://api.anthropic.com",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    with patch.object(runner, "get_token", return_value="cfg-gh-token"):
        env = runner.collect_env_vars(config_path=cfg_file)

    assert env["OPENAI_API_KEY"] == "cfg-openai-key"
    assert env["OPENAI_BASE_URL"] == "https://us.api.openai.com/v1"
    assert env["ANTHROPIC_API_KEY"] == "cfg-anthropic-key"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"
    assert env["GH_TOKEN"] == "cfg-gh-token"


def test_build_exec_command_escapes_and_passes_flags():
    env_vars = {"KEY1": "val1", "KEY2": "val'with'quotes"}
    args = ["run", "hello world", "--auto", "--format", "json"]
    cmd = runner.build_exec_command("podman", "scavenger-env-test", args, env_vars)

    assert cmd[:3] == ["podman", "exec", "-i"]
    assert "-e" in cmd
    assert "KEY1=val1" in cmd
    assert "KEY2=val'with'quotes" in cmd
    assert "scavenger-env-test" in cmd
    assert cmd[-6:] == ["opencode", "run", "hello world", "--auto", "--format", "json"]


def test_run_remote_opencode_missing_lease(tmp_path):
    leases_file = tmp_path / "leases.json"
    leases_file.write_text("{}", encoding="utf-8")

    code = runner.run_remote_opencode(["--version"], env_id="ghost-env", leases_file=leases_file)
    assert code == 2


def test_run_remote_opencode_fast_path_session_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "dummy-token")
    leases_file = tmp_path / "leases.json"
    leases_file.write_text(
        json.dumps(
            {
                "my-env": {
                    "host": "my-host",
                    "container_name": "scavenger-env-my-env",
                }
            }
        ),
        encoding="utf-8",
    )
    fleet_file = tmp_path / "fleet.json"
    fleet_file.write_text(
        json.dumps(
            {
                "hosts": [
                    {
                        "name": "my-host",
                        "ssh_alias": "scavenger-my-host",
                        "container": {"engine": "podman"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with patch("subprocess.run") as mock_run:
        code = runner.run_remote_opencode(
            ["session", "delete", "ses_123"],
            env_id="my-env",
            leases_file=leases_file,
            fleet_file=fleet_file,
        )
        assert code == 0
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "ssh" in call_args
        assert "scavenger-my-host" in call_args
        assert "podman exec scavenger-env-my-env opencode session delete ses_123" in call_args[-1]


def test_run_remote_opencode_dispatches_ssh_popen(tmp_path):
    leases_file = tmp_path / "leases.json"
    leases_file.write_text(
        json.dumps(
            {
                "my-env": {
                    "host": "my-host",
                    "container_name": "scavenger-env-my-env",
                }
            }
        ),
        encoding="utf-8",
    )
    fleet_file = tmp_path / "fleet.json"
    fleet_file.write_text(
        json.dumps(
            {
                "hosts": [
                    {
                        "name": "my-host",
                        "ssh_alias": "scavenger-my-host",
                        "container": {"engine": "docker"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    fake_proc = MagicMock()
    fake_proc.stdout = ['{"type": "step_start"}\n', '{"type": "text", "text": "OK"}\n']
    fake_proc.returncode = 0
    fake_proc.wait.return_value = 0

    with (
        patch("subprocess.Popen", return_value=fake_proc) as mock_popen,
        patch.object(runner, "collect_env_vars", return_value={"OPENAI_API_KEY": "secret"}),
    ):
        code = runner.run_remote_opencode(
            ["run", "Say Hi", "--auto", "--format", "json"],
            env_id="my-env",
            leases_file=leases_file,
            fleet_file=fleet_file,
        )
        assert code == 0
        mock_popen.assert_called_once()
        ssh_call = mock_popen.call_args[0][0]
        assert ssh_call[0] == "ssh"
        assert "scavenger-my-host" in ssh_call
        assert "docker exec -i" in ssh_call[-1]
        assert "OPENAI_API_KEY=secret" in ssh_call[-1]
        assert "opencode run 'Say Hi' --auto --format json" in ssh_call[-1]


def test_scavenger_runner_path_tool():
    path = scavenger_runner_path(None)
    assert os.path.isabs(path)
    assert path.endswith("opencode_scavenger_runner.py")
    assert os.path.isfile(path)
