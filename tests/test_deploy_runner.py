"""Tests for the external deploy runner (#1647).

The runner is a standalone bootstrap script: it must not import the
``decafclaw`` runtime package, and its guard, timeout, backup, rollback, and
quarantine decisions must be testable without git, systemd, or a live server.
"""

from __future__ import annotations

import fcntl
import importlib.util
import pathlib
import re
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCRIPT = REPO_ROOT / "scripts" / "deploy_runner.py"

_spec = importlib.util.spec_from_file_location("deploy_runner", _SCRIPT)
assert _spec and _spec.loader
dr = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = dr
_spec.loader.exec_module(dr)

CANDIDATE = "b" * 40
HEAD = "a" * 40


def _health(*, commit: str, status: str = "ok", servers=("alpha",), tools: int = 2, busy: bool = False):
    return {
        "status": status,
        "commit": commit,
        "busy": {
            "known": True,
            "active": busy,
            "conversations": ["conv-1"] if busy else [],
        },
        "mcp_servers": {
            "configured": 2,
            "connected": len(servers),
            "connected_servers": list(servers),
            "tool_count": tools,
            "servers": {},
        },
    }


class FakeWorld:
    def __init__(
        self,
        *,
        candidate: str = CANDIDATE,
        head: str = HEAD,
        ancestor: bool = True,
        health_script: list[dict | None] | None = None,
        fail_prefixes: list[list[str]] | None = None,
    ):
        self.candidate = candidate
        self.head = head
        self.ancestor = ancestor
        self.health_script = health_script or []
        self.health_index = 0
        self.time = 0.0
        self.calls: list[list[str]] = []
        self.notifications: list[dict] = []
        self.fail_prefixes = fail_prefixes or []

    def run(self, cmd, cwd=None):
        self.calls.append(list(cmd))
        for prefix in self.fail_prefixes:
            if cmd[: len(prefix)] == prefix:
                return dr.CommandResult(1, "", f"{' '.join(prefix)} failed")
        if cmd[:2] == ["git", "fetch"]:
            return dr.CommandResult(0)
        if cmd[:2] == ["git", "rev-parse"]:
            return dr.CommandResult(0, (self.head if cmd[2] == "HEAD" else self.candidate) + "\n")
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return dr.CommandResult(0 if self.ancestor else 1)
        return dr.CommandResult(0)

    def health(self):
        if not self.health_script:
            return None
        index = min(self.health_index, len(self.health_script) - 1)
        self.health_index += 1
        return self.health_script[index]

    def sleep(self, seconds):
        self.time += seconds

    def clock(self):
        return self.time

    def notify(self, **kwargs):
        self.notifications.append(kwargs)


def make_config(tmp_path, **overrides):
    values = {
        "repo_dir": tmp_path,
        "service": "decafclaw",
        "state_file": tmp_path / "state.json",
        "lock_file": tmp_path / "deploy.lock",
        "health_url": "http://example.invalid/health",
        "backup_cmd": ["true"],
        "max_attempts": 2,
        "idle_timeout": 1.0,
        "verify_timeout": 1.0,
        "poll_interval": 1.0,
    }
    values.update(overrides)
    return dr.RunnerConfig(**values)


def make_runner(tmp_path, world, **overrides):
    return dr.DeployRunner(
        make_config(tmp_path, **overrides),
        run=world.run,
        health=world.health,
        sleep=world.sleep,
        clock=world.clock,
        notify=world.notify,
    )


def _commands(world):
    return [" ".join(cmd) for cmd in world.calls]


# -- Guard -------------------------------------------------------------------


def test_guard_candidate_ahead_is_deployable(tmp_path):
    world = FakeWorld(ancestor=True)
    runner = make_runner(tmp_path, world)
    result = runner.guard(dr.DeployState(), CANDIDATE, HEAD)
    assert result.action == "deploy"


def test_guard_already_at_candidate(tmp_path):
    runner = make_runner(tmp_path, FakeWorld())
    result = runner.guard(dr.DeployState(), HEAD, HEAD)
    assert result.action == "already"


def test_guard_behind_candidate_is_refused(tmp_path):
    runner = make_runner(tmp_path, FakeWorld(ancestor=False))
    result = runner.guard(dr.DeployState(), CANDIDATE, HEAD)
    assert result.action == "refuse"
    assert "not ahead" in result.reason


def test_guard_quarantined_candidate_is_blocked(tmp_path):
    runner = make_runner(tmp_path, FakeWorld())
    state = dr.DeployState(quarantined={CANDIDATE: {"at": "now", "reason": "import error"}})
    result = runner.guard(state, CANDIDATE, HEAD)
    assert result.action == "blocked"
    assert "import error" in result.reason


def test_guard_attempt_limit_blocks(tmp_path):
    runner = make_runner(tmp_path, FakeWorld(), max_attempts=3)
    state = dr.DeployState(last_attempt={"sha": CANDIDATE, "at": "now", "attempts": 3})
    result = runner.guard(state, CANDIDATE, HEAD)
    assert result.action == "blocked"
    assert "failed 3 time(s)" in result.reason


# -- End-to-end decisions ----------------------------------------------------


def test_success_updates_last_good(tmp_path):
    world = FakeWorld(
        health_script=[
            _health(commit=HEAD),
            _health(commit=CANDIDATE),
        ]
    )
    runner = make_runner(tmp_path, world)

    assert runner.run() == dr.EXIT_OK

    state = dr.DeployState.load(tmp_path / "state.json")
    assert state.last_good == CANDIDATE
    assert state.last_attempt is None
    commands = _commands(world)
    assert "systemctl --user stop decafclaw" in commands
    assert f"git merge --ff-only {CANDIDATE}" in commands
    assert "uv sync" in commands
    assert "systemctl --user start --no-block decafclaw" in commands
    assert world.notifications[-1]["title"] == "Deploy succeeded"


def test_pre_deploy_no_answer_refuses_before_stopping(tmp_path):
    world = FakeWorld(health_script=[None])
    runner = make_runner(tmp_path, world)

    assert runner.run() == dr.EXIT_REFUSED

    commands = _commands(world)
    assert not any("systemctl --user stop" in command for command in commands)
    assert not any("git merge --ff-only" in command for command in commands)


def test_idle_timeout_refuses_before_stopping(tmp_path):
    world = FakeWorld(health_script=[_health(commit=HEAD, busy=True)])
    runner = make_runner(tmp_path, world)

    assert runner.run() == dr.EXIT_REFUSED

    assert not any("systemctl --user stop" in command for command in _commands(world))


def test_backup_failure_aborts_before_stopping(tmp_path):
    world = FakeWorld(
        health_script=[_health(commit=HEAD)],
        fail_prefixes=[["backup.sh"]],
    )
    runner = make_runner(tmp_path, world, backup_cmd=["backup.sh"])

    assert runner.run() == dr.EXIT_REFUSED

    commands = _commands(world)
    assert "backup.sh" in commands
    assert not any("systemctl --user stop" in command for command in commands)


def test_post_deploy_no_answer_rolls_back(tmp_path):
    world = FakeWorld(
        health_script=[
            _health(commit=HEAD),  # pre-deploy
            None,  # forward verify, first poll
            None,  # forward verify, deadline
            _health(commit=HEAD),  # rollback verify
        ]
    )
    runner = make_runner(tmp_path, world)

    assert runner.run() == dr.EXIT_ROLLED_BACK

    commands = _commands(world)
    assert f"git reset --hard {HEAD}" in commands
    state = dr.DeployState.load(tmp_path / "state.json")
    assert state.last_attempt == {"sha": CANDIDATE, "at": state.last_attempt["at"], "attempts": 1}
    assert CANDIDATE not in state.quarantined
    assert world.notifications[-1]["priority"] == "high"


def test_mcp_regression_rolls_back(tmp_path):
    world = FakeWorld(
        health_script=[
            _health(commit=HEAD, servers=("alpha", "playwright"), tools=30),
            _health(commit=CANDIDATE, servers=("alpha",), tools=5),
            _health(commit=CANDIDATE, servers=("alpha",), tools=5),
            _health(commit=HEAD, servers=("alpha", "playwright"), tools=30),
        ]
    )
    runner = make_runner(tmp_path, world)

    assert runner.run() == dr.EXIT_ROLLED_BACK

    assert "MCP servers no longer connected: playwright" in world.notifications[-1]["body"]


def test_repeated_failures_quarantine_the_sha(tmp_path):
    def failing_world():
        return FakeWorld(
            health_script=[
                _health(commit=HEAD),
                _health(commit=CANDIDATE, status="degraded"),
                _health(commit=CANDIDATE, status="degraded"),
                _health(commit=HEAD),
            ]
        )

    first = failing_world()
    assert make_runner(tmp_path, first).run() == dr.EXIT_ROLLED_BACK
    state = dr.DeployState.load(tmp_path / "state.json")
    assert state.last_attempt["attempts"] == 1
    assert CANDIDATE not in state.quarantined

    second = failing_world()
    assert make_runner(tmp_path, second).run() == dr.EXIT_ROLLED_BACK
    state = dr.DeployState.load(tmp_path / "state.json")
    assert state.last_attempt["attempts"] == 2
    assert CANDIDATE in state.quarantined

    third = failing_world()
    assert make_runner(tmp_path, third).run() == dr.EXIT_BLOCKED
    assert not any("systemctl --user stop" in command for command in _commands(third))


def test_rollback_failure_is_reported(tmp_path):
    world = FakeWorld(
        health_script=[
            _health(commit=HEAD),
            _health(commit=CANDIDATE, status="degraded"),
            _health(commit=CANDIDATE, status="degraded"),
            None,  # rollback verify never answers
            None,
        ]
    )
    runner = make_runner(tmp_path, world)

    assert runner.run() == dr.EXIT_ROLLBACK_FAILED

    assert world.notifications[-1]["title"] == "Deploy and rollback both failed"


def test_rollback_accepts_target_without_commit_field(tmp_path):
    world = FakeWorld(
        health_script=[
            _health(commit=HEAD),
            None,
            None,
            {"status": "ok", "commit": None},
        ]
    )
    runner = make_runner(tmp_path, world)

    assert runner.run() == dr.EXIT_ROLLED_BACK


# -- Lock and boundary -------------------------------------------------------


def test_lock_prevents_concurrent_run(tmp_path):
    lock_path = tmp_path / "deploy.lock"
    held = lock_path.open("a+")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        world = FakeWorld(health_script=[_health(commit=HEAD)])
        runner = make_runner(tmp_path, world)
        assert runner.run() == dr.EXIT_BLOCKED
        assert world.calls == []
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()


def test_runner_does_not_import_decafclaw():
    text = _SCRIPT.read_text(encoding="utf-8")
    import_line_re = re.compile(r"^\s*(?:import|from)\s+.*\bdecafclaw\b", re.MULTILINE)
    assert not import_line_re.search(text), "deploy_runner.py must not import the runtime package"


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = dr.DeployState(
        last_good=HEAD,
        quarantined={CANDIDATE: {"at": "2026-01-01T00:00:00Z", "reason": "boom"}},
        last_attempt={"sha": CANDIDATE, "at": "2026-01-01T00:00:00Z", "attempts": 2},
    )
    state.save(path)
    loaded = dr.DeployState.load(path)
    assert loaded == state


@pytest.mark.parametrize("bad", ["not json", "[]", "null"])
def test_state_load_tolerates_bad_input(tmp_path, bad):
    path = tmp_path / "state.json"
    path.write_text(bad)
    assert dr.DeployState.load(path) == dr.DeployState()
