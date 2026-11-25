#!/usr/bin/env python3
"""External DecafClaw deploy runner (#1647).

Stops the service, takes a quiesced backup, fast-forwards to the target
commit, syncs dependencies, starts the service, verifies ``/health``, and
rolls back on failure. This program runs **outside** the agent process and
MUST NOT import the ``decafclaw`` runtime package: a broken deploy must not
take the rollback logic down with it.

Deploy steps::

    1  lock (flock)                 refuse if another run holds it
    2  read state                   last_good / quarantined / last_attempt
    3  fetch + resolve candidate    git rev-parse <ref>
    4  guard                        ahead of HEAD and SHA not quarantined
    5  pre-deploy health            no answer => refuse to deploy
    6  wait for idle                poll /health busy state, bounded
    7  backup                       abort if it fails, before stopping
    8  record attempt
    9  stop                         systemctl --user stop
    10 pull                         git merge --ff-only <sha>
    11 sync                         uv sync
    12 start                        systemctl --user start --no-block
    13 verify                       commit, status, MCP set and tool count
    14 success                      last_good = sha
       failure                      rollback, quarantine after N attempts

The timeout asymmetry is deliberate and expressed as separate code paths:
no answer *before* a deploy refuses to deploy; no answer *after* a deploy
rolls back. Inverting them produces either a rollback loop or a silently
dead service.

Exit codes::

    0  success, already at the candidate, or nothing to do
    1  usage or configuration error
    2  refused before stopping the service (guard, health, backup)
    3  deploy failed and the rollback succeeded
    4  deploy failed and the rollback also failed (service left for a human)
    5  blocked (another run holds the lock, or the SHA is quarantined)
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger("decafclaw-deploy")

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REFUSED = 2
EXIT_ROLLED_BACK = 3
EXIT_ROLLBACK_FAILED = 4
EXIT_BLOCKED = 5

DEFAULT_MAX_ATTEMPTS = 2


# -- Results -----------------------------------------------------------------


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class GuardResult:
    action: str  # "deploy" | "already" | "refuse" | "blocked"
    reason: str = ""
    candidate: str = ""
    head: str = ""


@dataclass
class VerifyResult:
    ok: bool
    reason: str = ""
    health: dict | None = None


# -- State -------------------------------------------------------------------


class StateError(Exception):
    """The deploy state file exists but cannot be trusted."""


@dataclass
class DeployState:
    """Durable deploy state.

    ``last_good`` is the last commit that passed the health gate and is the
    rollback target. ``quarantined`` maps a SHA to why it is refused, so a
    broken commit is not retried forever. ``last_attempt`` records the
    current candidate and its consecutive failure count. ``first_seen`` and
    ``deferral_notified`` implement the poll policy's starvation bound: a
    candidate that is busy for too long is surfaced once, not silently
    skipped forever.
    """

    last_good: str = ""
    quarantined: dict[str, dict] = field(default_factory=dict)
    last_attempt: dict | None = None
    first_seen: dict[str, str] = field(default_factory=dict)
    deferral_notified: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "DeployState":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            raise StateError(f"cannot read deploy state {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise StateError(f"deploy state {path} is not a JSON object")
        quarantined = raw.get("quarantined")
        if not isinstance(quarantined, dict):
            quarantined = {}
        last_attempt = raw.get("last_attempt")
        if not isinstance(last_attempt, dict):
            last_attempt = None
        first_seen = raw.get("first_seen")
        if not isinstance(first_seen, dict):
            first_seen = {}
        deferral_notified = raw.get("deferral_notified")
        if not isinstance(deferral_notified, dict):
            deferral_notified = {}
        return cls(
            last_good=str(raw.get("last_good") or ""),
            quarantined=dict(quarantined),
            last_attempt=last_attempt,
            first_seen={str(k): str(v) for k, v in first_seen.items()},
            deferral_notified={str(k): str(v) for k, v in deferral_notified.items()},
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)


# -- Configuration -----------------------------------------------------------


@dataclass
class RunnerConfig:
    repo_dir: Path
    service: str = "decafclaw"
    candidate_ref: str = "origin/production"
    state_file: Path = Path.home() / ".local/state/decafclaw-deploy/state.json"
    lock_file: Path = Path.home() / ".local/state/decafclaw-deploy/deploy.lock"
    health_url: str = "http://127.0.0.1:18880/health"
    backup_cmd: list[str] | None = None
    notify_file: Path | None = None
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    idle_timeout: float = 300.0
    verify_timeout: float = 90.0
    poll_interval: float = 3.0
    allow_no_backup: bool = False
    defer_busy: bool = False
    deferral_cap_hours: float = 6.0


# -- Default adapters --------------------------------------------------------


def _run_command(cmd: list[str], cwd: Path | None = None) -> CommandResult:
    env = os.environ.copy()
    env["PATH"] = f"{Path.home()}/.local/bin:{env.get('PATH', '')}"
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


def _fetch_health(url: str, timeout: float = 10.0) -> dict | None:
    """GET a health payload. A 503 body is still an answer, not a failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except OSError:
            return None
    except (OSError, ValueError):
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _write_notification(path: Path | None, *, title: str, body: str, priority: str = "normal") -> None:
    """Append a deploy record to the notification inbox, fail-open.

    Mirrors the record shape in docs/notifications.md without importing the
    runtime package.
    """
    if path is None:
        return
    record = {
        "id": uuid.uuid4().hex[:12],
        "timestamp": _now_iso(),
        "category": "deploy",
        "title": title,
        "priority": priority,
        "body": body,
        "link": None,
        "conv_id": None,
        "attended": False,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        log.warning("Could not write deploy notification to %s: %s", path, exc)


# -- Runner ------------------------------------------------------------------


class DeployRunner:
    def __init__(
        self,
        config: RunnerConfig,
        *,
        run: Callable[[list[str], Path | None], CommandResult] | None = None,
        health: Callable[[], dict | None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        notify: Callable[..., None] | None = None,
    ) -> None:
        self.config = config
        self._run = run or _run_command
        self._health = health or (lambda: _fetch_health(config.health_url))
        self._sleep = sleep
        self._clock = clock
        self._notify = notify or (lambda **kwargs: _write_notification(config.notify_file, **kwargs))

    # -- command helpers ----------------------------------------------------

    def _git(self, *args: str) -> CommandResult:
        return self._run(["git", *args], self.config.repo_dir)

    def _systemctl(self, *args: str) -> CommandResult:
        return self._run(["systemctl", "--user", *args, self.config.service], None)

    def _uv_sync(self) -> CommandResult:
        return self._run(["uv", "sync"], self.config.repo_dir)

    def _say(self, message: str) -> None:
        log.info("%s", message)

    def _notify_outcome(self, *, title: str, body: str, priority: str = "normal") -> None:
        try:
            self._notify(title=title, body=body, priority=priority)
        except Exception:  # notification is fail-open
            log.warning("Deploy notification failed", exc_info=True)

    # -- lock ---------------------------------------------------------------

    def acquire_lock(self):
        self.config.lock_file.parent.mkdir(parents=True, exist_ok=True)
        handle = self.config.lock_file.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        return handle

    # -- guard --------------------------------------------------------------

    def guard(self, state: DeployState, candidate: str, head: str) -> GuardResult:
        if not candidate or not head:
            return GuardResult("refuse", "could not resolve candidate or HEAD", candidate, head)
        if candidate == head:
            return GuardResult("already", "HEAD is already the candidate", candidate, head)
        quarantine = state.quarantined.get(candidate)
        if isinstance(quarantine, dict):
            reason = quarantine.get("reason") or "quarantined"
            return GuardResult("blocked", f"{candidate[:12]} is quarantined: {reason}", candidate, head)
        if quarantine:
            return GuardResult("blocked", f"{candidate[:12]} is quarantined", candidate, head)
        attempt = state.last_attempt or {}
        attempts = int(attempt.get("attempts") or 0)
        if attempt.get("sha") == candidate and attempts >= self.config.max_attempts:
            return GuardResult(
                "blocked",
                f"{candidate[:12]} failed {attempts} time(s); not retrying",
                candidate,
                head,
            )
        ancestor = self._git("merge-base", "--is-ancestor", head, candidate)
        if ancestor.returncode != 0:
            return GuardResult(
                "refuse",
                f"{candidate[:12]} is not ahead of HEAD {head[:12]}",
                candidate,
                head,
            )
        return GuardResult("deploy", "", candidate, head)

    # -- health -------------------------------------------------------------

    def wait_for_idle(self) -> tuple[dict | None, str, bool]:
        """Check pre-deploy health and wait or defer on a busy turn.

        Returns ``(payload, reason, deferred)``. ``deferred`` means a busy
        turn skipped this tick under ``--defer-busy`` (the timer retries).
        A ``None`` payload with ``deferred=False`` is the *pre-deploy*
        no-answer path: refuse rather than change code on an unknown state.
        """
        if self.config.defer_busy:
            payload = self._health()
            if payload is None:
                return None, "no answer before deploy; refusing to change code on an unknown state", False
            busy = payload.get("busy") or {}
            if busy.get("active"):
                names = ", ".join(busy.get("conversations") or []) or "unknown"
                return None, f"conversation busy: {names}", True
            return payload, "", False

        deadline = self._clock() + self.config.idle_timeout
        while True:
            payload = self._health()
            if payload is None:
                return None, "no answer before deploy; refusing to change code on an unknown state", False
            busy = payload.get("busy") or {}
            if not busy.get("active"):
                return payload, "", False
            if self._clock() >= deadline:
                names = ", ".join(busy.get("conversations") or []) or "unknown"
                return None, f"conversations still busy after {self.config.idle_timeout:.0f}s: {names}", False
            self._sleep(self.config.poll_interval)

    def verify(self, expected_commit: str, baseline: dict | None, *, require_commit: bool) -> VerifyResult:
        """Poll post-deploy health until the new code is serving.

        A ``None`` answer here is the *post-deploy* no-answer path: the caller
        rolls back. ``require_commit`` is true for the forward deploy; a
        rollback target that predates the commit field may answer without it.
        """
        deadline = self._clock() + self.config.verify_timeout
        last_reason = "no answer after deploy"
        last_payload: dict | None = None
        while True:
            payload = self._health()
            if payload is None:
                last_reason = "no answer after deploy"
            else:
                last_payload = payload
                commit = payload.get("commit")
                if require_commit and commit != expected_commit:
                    last_reason = f"serving {commit or 'unknown'} instead of {expected_commit[:12]}"
                elif not require_commit and commit is not None and commit != expected_commit:
                    last_reason = f"serving {commit} instead of rollback target {expected_commit[:12]}"
                elif payload.get("status") != "ok":
                    last_reason = f"health status is {payload.get('status')!r}"
                else:
                    regression = self._mcp_regression(baseline, payload)
                    if regression:
                        last_reason = regression
                    else:
                        return VerifyResult(True, "", payload)
            if self._clock() >= deadline:
                return VerifyResult(False, last_reason, last_payload)
            self._sleep(self.config.poll_interval)

    @staticmethod
    def _mcp_regression(baseline: dict | None, payload: dict) -> str:
        before = (baseline or {}).get("mcp_servers") or {}
        after = payload.get("mcp_servers") or {}
        if "connected_servers" not in after or "tool_count" not in after:
            return ""
        lost = sorted(set(before.get("connected_servers") or []) - set(after.get("connected_servers") or []))
        if lost:
            return f"MCP servers no longer connected: {', '.join(lost)}"
        before_tools = int(before.get("tool_count") or 0)
        after_tools = int(after.get("tool_count") or 0)
        if after_tools < before_tools:
            return f"MCP tool count regressed: {before_tools} -> {after_tools}"
        return ""

    # -- steps --------------------------------------------------------------

    def backup(self) -> tuple[bool, str]:
        if self.config.backup_cmd is None:
            if self.config.allow_no_backup:
                return True, "no backup command; continuing because --allow-no-backup was given"
            return False, "no backup command configured; refusing to stop the service"
        result = self._run(self.config.backup_cmd, self.config.repo_dir)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return False, f"backup failed (exit {result.returncode}): {detail}"
        return True, ""

    def deploy_code(self, candidate: str) -> tuple[bool, str]:
        stop = self._systemctl("stop")
        if stop.returncode != 0:
            return False, f"systemctl stop failed: {stop.stderr.strip()}"
        pull = self._git("merge", "--ff-only", candidate)
        if pull.returncode != 0:
            return False, f"git merge --ff-only failed: {pull.stderr.strip()}"
        sync = self._uv_sync()
        if sync.returncode != 0:
            return False, f"uv sync failed: {(sync.stderr or sync.stdout).strip()}"
        start = self._systemctl("start", "--no-block")
        if start.returncode != 0:
            return False, f"systemctl start failed: {start.stderr.strip()}"
        return True, ""

    def rollback(self, target: str, baseline: dict | None) -> tuple[bool, str]:
        stop = self._systemctl("stop")
        if stop.returncode != 0:
            return False, f"systemctl stop failed during rollback: {stop.stderr.strip()}"
        reset = self._git("reset", "--hard", target)
        if reset.returncode != 0:
            return False, f"git reset --hard {target[:12]} failed: {reset.stderr.strip()}"
        sync = self._uv_sync()
        if sync.returncode != 0:
            return False, f"uv sync failed during rollback: {(sync.stderr or sync.stdout).strip()}"
        start = self._systemctl("start", "--no-block")
        if start.returncode != 0:
            return False, f"systemctl start failed during rollback: {start.stderr.strip()}"
        verified = self.verify(target, baseline, require_commit=False)
        if not verified.ok:
            return False, f"rollback target did not become healthy: {verified.reason}"
        return True, ""

    # -- orchestration ------------------------------------------------------

    def run(self) -> int:
        lock = self.acquire_lock()
        if lock is None:
            self._say("Another deploy runner holds the lock; refusing.")
            return EXIT_BLOCKED
        try:
            return self._run_unlocked()
        finally:
            lock.close()

    def _run_unlocked(self) -> int:
        config = self.config
        try:
            state = DeployState.load(config.state_file)
        except StateError as exc:
            self._say(f"{exc}; refusing to deploy")
            self._notify_outcome(
                title="Deploy state unreadable; refusing to deploy",
                body=str(exc),
                priority="high",
            )
            return EXIT_REFUSED

        fetch = self._git("fetch", "--prune", "origin")
        if fetch.returncode != 0:
            self._say(f"git fetch failed: {fetch.stderr.strip()}")
            return EXIT_REFUSED
        candidate = self._git("rev-parse", config.candidate_ref).stdout.strip()
        head = self._git("rev-parse", "HEAD").stdout.strip()

        guard = self.guard(state, candidate, head)
        if guard.action == "already":
            self._say(f"Already at {candidate[:12]}.")
            return EXIT_OK
        if guard.action == "blocked":
            self._say(guard.reason)
            return EXIT_BLOCKED
        if guard.action == "refuse":
            self._say(guard.reason)
            return EXIT_REFUSED

        baseline, reason, deferred = self.wait_for_idle()
        if deferred:
            self._record_deferral(state, candidate, reason)
            state.save(config.state_file)
            self._say(reason)
            return EXIT_OK
        if baseline is None:
            self._say(reason)
            return EXIT_REFUSED

        # This candidate is being attempted; its starvation timer restarts.
        state.first_seen.pop(candidate, None)
        state.deferral_notified.pop(candidate, None)

        backed_up, reason = self.backup()
        if not backed_up:
            self._say(reason)
            self._notify_outcome(
                title="Deploy refused before stopping the service",
                body=reason,
                priority="high",
            )
            return EXIT_REFUSED

        attempts = self._record_attempt(state, candidate)
        state.save(config.state_file)
        self._say(f"Deploying {candidate[:12]} (attempt {attempts})")

        deployed, reason = self.deploy_code(candidate)
        if deployed:
            verified = self.verify(candidate, baseline, require_commit=True)
            if verified.ok:
                state.last_good = candidate
                state.last_attempt = None
                state.save(config.state_file)
                self._say(f"Deploy succeeded: {candidate[:12]} is serving.")
                self._notify_outcome(
                    title="Deploy succeeded",
                    body=f"{candidate[:12]} is serving after {attempts} attempt(s).",
                )
                return EXIT_OK
            reason = verified.reason

        self._say(f"Deploy failed: {reason}. Rolling back to {state.last_good or head}.")
        rolled_back, rollback_reason = self.rollback(state.last_good or head, baseline)
        if not rolled_back:
            state.save(config.state_file)
            self._say(f"Rollback failed: {rollback_reason}")
            self._notify_outcome(
                title="Deploy and rollback both failed",
                body=f"{reason}\nRollback: {rollback_reason}\nThe service needs a human.",
                priority="high",
            )
            return EXIT_ROLLBACK_FAILED

        if attempts >= config.max_attempts:
            state.quarantined[candidate] = {"at": _now_iso(), "reason": reason}
        state.save(config.state_file)
        self._say(f"Rolled back. {candidate[:12]} failed {attempts} time(s).")
        self._notify_outcome(
            title="Deploy failed and rolled back",
            body=f"{candidate[:12]}: {reason}\nAttempts: {attempts}",
            priority="high",
        )
        return EXIT_ROLLED_BACK

    def _record_deferral(self, state: DeployState, candidate: str, reason: str) -> None:
        """Track a busy-tick skip and notify once when it exceeds the cap."""
        now = _now_iso()
        first = state.first_seen.setdefault(candidate, now)
        if candidate in state.deferral_notified:
            return
        first_at = _parse_iso(first)
        if first_at is None:
            return
        age_hours = (datetime.now(timezone.utc) - first_at).total_seconds() / 3600
        if age_hours < self.config.deferral_cap_hours:
            return
        self._notify_outcome(
            title="Deploy candidate is waiting",
            body=(f"{candidate[:12]} has been waiting since {first} ({age_hours:.1f}h): {reason}"),
            priority="high",
        )
        state.deferral_notified[candidate] = now

    def _record_attempt(self, state: DeployState, candidate: str) -> int:
        previous = state.last_attempt or {}
        attempts = int(previous.get("attempts") or 0) + 1 if previous.get("sha") == candidate else 1
        state.last_attempt = {"sha": candidate, "at": _now_iso(), "attempts": attempts}
        return attempts


# -- CLI ---------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-dir", default=str(Path.home() / "decafclaw"))
    parser.add_argument("--service", default="decafclaw")
    parser.add_argument("--ref", default="origin/production", help="candidate ref (default: origin/production)")
    parser.add_argument("--state-file", default=str(Path.home() / ".local/state/decafclaw-deploy/state.json"))
    parser.add_argument("--lock-file", default=str(Path.home() / ".local/state/decafclaw-deploy/deploy.lock"))
    parser.add_argument("--health-url", default="http://127.0.0.1:18880/health")
    parser.add_argument("--backup-cmd", default=None, help="command run before stopping; abort if it fails")
    parser.add_argument("--notify-file", default=None, help="notification inbox JSONL to append deploy records")
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--idle-timeout", type=float, default=300.0)
    parser.add_argument("--verify-timeout", type=float, default=90.0)
    parser.add_argument("--poll-interval", type=float, default=3.0)
    parser.add_argument(
        "--allow-no-backup",
        action="store_true",
        help="proceed without a backup command (data restore is then impossible)",
    )
    parser.add_argument(
        "--defer-busy",
        action="store_true",
        help="skip this tick when a turn is active instead of waiting (timer policy)",
    )
    parser.add_argument(
        "--deferral-cap-hours",
        type=float,
        default=6.0,
        help="notify once when a busy candidate has waited this long (default 6)",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    repo_dir = Path(args.repo_dir).expanduser()
    if not repo_dir.is_dir():
        log.error("Repository directory does not exist: %s", repo_dir)
        return EXIT_USAGE
    config = RunnerConfig(
        repo_dir=repo_dir,
        service=args.service,
        candidate_ref=args.ref,
        state_file=Path(args.state_file).expanduser(),
        lock_file=Path(args.lock_file).expanduser(),
        health_url=args.health_url,
        backup_cmd=shlex.split(args.backup_cmd) if args.backup_cmd else None,
        notify_file=Path(args.notify_file).expanduser() if args.notify_file else None,
        max_attempts=args.max_attempts,
        idle_timeout=args.idle_timeout,
        verify_timeout=args.verify_timeout,
        poll_interval=args.poll_interval,
        allow_no_backup=args.allow_no_backup,
        defer_busy=args.defer_busy,
        deferral_cap_hours=args.deferral_cap_hours,
    )
    return DeployRunner(config).run()


if __name__ == "__main__":
    sys.exit(main())
