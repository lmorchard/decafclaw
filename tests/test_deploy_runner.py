"""Tests for the external deploy runner (#1647).

The runner is a standalone bootstrap script: it must not import the
``decafclaw`` runtime package, and its guard, timeout, backup, rollback, and
quarantine decisions must be testable without git, systemd, or a live server.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import pathlib
import re
import sys
import time

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
        git_outputs: dict[tuple[str, ...], str] | None = None,
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
        self.git_outputs = git_outputs or {}

    def run(self, cmd, cwd=None):
        self.calls.append(list(cmd))
        for prefix in self.fail_prefixes:
            if cmd[: len(prefix)] == prefix:
                return dr.CommandResult(1, "", f"{' '.join(prefix)} failed")
        if tuple(cmd) in self.git_outputs:
            return dr.CommandResult(0, self.git_outputs[tuple(cmd)])
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


def test_guard_manual_action_blocks_with_reason(tmp_path):
    git_outputs = {
        ("git", "log", f"{HEAD}..{CANDIDATE}", "--format=%(trailers:key=Deploy-Action,valueonly=true)"): "manual\n",
        (
            "git",
            "log",
            f"{HEAD}..{CANDIDATE}",
            "--format=%(trailers:key=Deploy-Note,valueonly=true)",
        ): "Run db migration\n",
    }
    world = FakeWorld(git_outputs=git_outputs)
    runner = make_runner(tmp_path, world)
    result = runner.guard(dr.DeployState(), CANDIDATE, HEAD)
    assert result.action == "blocked"
    assert result.manual_required is True
    assert "Run db migration" in result.reason


def test_guard_manual_action_allowed_with_ack_manual(tmp_path):
    git_outputs = {
        ("git", "log", f"{HEAD}..{CANDIDATE}", "--format=%(trailers:key=Deploy-Action,valueonly=true)"): "manual\n",
        (
            "git",
            "log",
            f"{HEAD}..{CANDIDATE}",
            "--format=%(trailers:key=Deploy-Note,valueonly=true)",
        ): "Run db migration\n",
    }
    world = FakeWorld(git_outputs=git_outputs)
    runner = make_runner(tmp_path, world, ack_manual=True)
    result = runner.guard(dr.DeployState(), CANDIDATE, HEAD)
    assert result.action == "deploy"
    assert result.manual_required is False


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
def test_state_load_rejects_bad_input(tmp_path, bad):
    """A corrupt state file must fail safe, not silently reset quarantine."""
    path = tmp_path / "state.json"
    path.write_text(bad)
    with pytest.raises(dr.StateError):
        dr.DeployState.load(path)


# -- Poll policy (#1648) -----------------------------------------------------


def test_no_candidate_is_quiet(tmp_path):
    world = FakeWorld(candidate=HEAD)
    runner = make_runner(tmp_path, world)

    assert runner.run() == dr.EXIT_OK

    assert not any("systemctl --user stop" in command for command in _commands(world))
    assert world.notifications == []


def test_corrupt_state_refuses_and_notifies(tmp_path):
    world = FakeWorld(health_script=[_health(commit=HEAD)])
    (tmp_path / "state.json").write_text("not json")
    runner = make_runner(tmp_path, world)

    assert runner.run() == dr.EXIT_REFUSED

    assert not any("systemctl --user stop" in command for command in _commands(world))
    assert world.notifications[-1]["title"] == "Deploy state unreadable; refusing to deploy"
    assert world.notifications[-1]["priority"] == "high"


def test_defer_busy_skips_without_stopping(tmp_path):
    world = FakeWorld(health_script=[_health(commit=HEAD, busy=True)])
    runner = make_runner(tmp_path, world, defer_busy=True)

    assert runner.run() == dr.EXIT_OK

    assert not any("systemctl --user stop" in command for command in _commands(world))
    assert world.notifications == []
    state = dr.DeployState.load(tmp_path / "state.json")
    assert state.first_seen[CANDIDATE]


def test_deferral_cap_notifies_exactly_once(tmp_path):
    world = FakeWorld(health_script=[_health(commit=HEAD, busy=True)])
    runner = make_runner(tmp_path, world, defer_busy=True, deferral_cap_hours=6)

    assert runner.run() == dr.EXIT_OK
    state = dr.DeployState.load(tmp_path / "state.json")
    state.first_seen[CANDIDATE] = "2026-01-01T00:00:00Z"
    state.save(tmp_path / "state.json")

    assert runner.run() == dr.EXIT_OK
    assert len(world.notifications) == 1
    assert world.notifications[0]["title"] == "Deploy candidate is waiting"

    assert runner.run() == dr.EXIT_OK
    assert len(world.notifications) == 1


def test_deploy_attempt_clears_deferral_state(tmp_path):
    world = FakeWorld(
        health_script=[
            _health(commit=HEAD),  # pre-deploy
            _health(commit=CANDIDATE),  # verify
        ]
    )
    (tmp_path / "state.json").write_text(json.dumps({"first_seen": {CANDIDATE: "2026-01-01T00:00:00Z"}}))
    runner = make_runner(tmp_path, world)

    assert runner.run() == dr.EXIT_OK

    state = dr.DeployState.load(tmp_path / "state.json")
    assert CANDIDATE not in state.first_seen
    assert CANDIDATE not in state.deferral_notified


def test_manual_action_notifies_once_and_blocks(tmp_path):
    changelog_content = (
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "### ⚠️ Deployment Notes\n"
        "- Run db migration manually with `uv run scripts/migrate.py`\n\n"
        "### Added\n- New feature\n"
    )
    git_outputs = {
        ("git", "log", f"{HEAD}..{CANDIDATE}", "--format=%(trailers:key=Deploy-Action,valueonly=true)"): "manual\n",
        (
            "git",
            "log",
            f"{HEAD}..{CANDIDATE}",
            "--format=%(trailers:key=Deploy-Note,valueonly=true)",
        ): "Run db migration\n",
        ("git", "show", f"{CANDIDATE}:CHANGELOG.md"): changelog_content,
    }
    world = FakeWorld(git_outputs=git_outputs)
    runner = make_runner(tmp_path, world)

    # First run: blocked, sends high-priority notification with notes
    assert runner.run() == dr.EXIT_BLOCKED
    assert len(world.notifications) == 1
    notif = world.notifications[0]
    assert "Deploy blocked: manual action required" in notif["title"]
    assert notif["priority"] == "high"
    assert "Run db migration" in notif["body"]
    assert "- Run db migration manually with `uv run scripts/migrate.py`" in notif["body"]

    # Verify state saved
    state = dr.DeployState.load(tmp_path / "state.json")
    assert state.manual_notified[CANDIDATE]

    # Second run: blocked, does NOT send duplicate notification
    assert runner.run() == dr.EXIT_BLOCKED
    assert len(world.notifications) == 1


def test_manual_action_proceeds_with_ack_manual(tmp_path):
    git_outputs = {
        ("git", "log", f"{HEAD}..{CANDIDATE}", "--format=%(trailers:key=Deploy-Action,valueonly=true)"): "manual\n",
        (
            "git",
            "log",
            f"{HEAD}..{CANDIDATE}",
            "--format=%(trailers:key=Deploy-Note,valueonly=true)",
        ): "Run db migration\n",
    }
    world = FakeWorld(
        git_outputs=git_outputs,
        health_script=[
            _health(commit=HEAD),
            _health(commit=CANDIDATE),
        ],
    )
    runner = make_runner(tmp_path, world, ack_manual=True)

    assert runner.run() == dr.EXIT_OK
    state = dr.DeployState.load(tmp_path / "state.json")
    assert state.last_good == CANDIDATE
    assert world.notifications[-1]["title"] == "Deploy succeeded"


# -- command watchdog -------------------------------------------------------


def test_command_watchdog_abandons_a_child_that_ignores_signals(monkeypatch, tmp_path):
    """The bound must work against a process that cannot be killed.

    This is the failure the watchdog exists for. A child wedged in a `hard` NFS
    mount sits in uninterruptible sleep: SIGTERM and even SIGKILL are deferred
    until the syscall returns. `subprocess.run(timeout=...)` would call kill()
    then wait(), and that wait() blocks forever -- presenting a bound that does
    not exist. Here the child traps SIGTERM and blocks; the runner must still
    return promptly with the timeout code.

    A plain `sleep` would not prove anything: it dies on SIGKILL, so even a naive
    implementation would appear to work.
    """
    monkeypatch.setenv("DECAFCLAW_DEPLOY_COMMAND_TIMEOUT", "1")

    script = tmp_path / "stubborn.py"
    script.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, lambda *a: None)\n"
        "print('started', flush=True)\n"
        "time.sleep(120)\n"
    )

    started = time.monotonic()
    result = dr._run_command([sys.executable, str(script)])
    elapsed = time.monotonic() - started

    assert result.returncode == dr.COMMAND_TIMEOUT_EXIT
    assert "watchdog" in result.stderr
    # Comfortably below the child's own 120s sleep, and above the 1s bound.
    assert elapsed < 30, f"watchdog did not return promptly (took {elapsed:.1f}s)"


def test_command_watchdog_passes_through_a_normal_exit(monkeypatch):
    """A fast, successful command must be unaffected by the watchdog."""
    monkeypatch.setenv("DECAFCLAW_DEPLOY_COMMAND_TIMEOUT", "30")

    result = dr._run_command([sys.executable, "-c", "print('hello')"])

    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_command_watchdog_can_be_disabled(monkeypatch):
    """A zero or negative value means 'no watchdog', for an operator debugging."""
    monkeypatch.setenv("DECAFCLAW_DEPLOY_COMMAND_TIMEOUT", "0")
    assert dr._command_timeout() is None

    monkeypatch.setenv("DECAFCLAW_DEPLOY_COMMAND_TIMEOUT", "-5")
    assert dr._command_timeout() is None


def test_command_watchdog_ignores_garbage_and_keeps_the_default(monkeypatch):
    """A typo must not silently disable the bound."""
    monkeypatch.setenv("DECAFCLAW_DEPLOY_COMMAND_TIMEOUT", "fifteen")
    assert dr._command_timeout() == dr.DEFAULT_COMMAND_TIMEOUT_SEC


def test_default_command_watchdog_allows_a_full_backup():
    """16 GB at the measured ~25 MB/s is ~11 minutes; the default must clear it.

    A bound that kills real backups would turn healthy deploys into refusals,
    which is why this is asserted rather than left to whoever edits the number.
    """
    assert dr.DEFAULT_COMMAND_TIMEOUT_SEC >= 600


def test_backup_watchdog_refuses_instead_of_stopping_the_service(tmp_path):
    """A watchdog hit on the backup must be treated as a refusal, not a failure.

    The backup step runs before `systemctl stop`. If the backup directory is
    unreachable, deploying anyway would stop a healthy service with no recovery
    point, so the runner must bail out here.
    """

    class WatchdogWorld(FakeWorld):
        def run(self, cmd, cwd=None):
            self.calls.append(list(cmd))
            if cmd[:1] == ["backup.sh"]:
                return dr.CommandResult(dr.COMMAND_TIMEOUT_EXIT, "", "watchdog tripped")
            return super().run(cmd, cwd)

    world = WatchdogWorld(health_script=[_health(commit=HEAD)])
    runner = make_runner(tmp_path, world, backup_cmd=["backup.sh"])

    assert runner.run() == dr.EXIT_REFUSED

    commands = _commands(world)
    assert not any("systemctl --user stop" in command for command in commands)
