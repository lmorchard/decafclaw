"""OpenCode skill — delegate coding tasks to OpenCode CLI as a subagent."""

import asyncio
import fnmatch
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from decafclaw.media import ToolResult

log = logging.getLogger(__name__)


class SessionLogger:
    """Writes OpenCode JSON output to a JSONL log file and tracks metrics."""

    def __init__(self, log_dir: Path, session_id: str):
        self.path = log_dir / f"{session_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.files_changed: list[str] = []
        self.tools_used: list[str] = []
        self.total_cost_usd: float = 0
        self.duration_ms: int = 0
        self.errors: list[str] = []
        self.result_text: str = ""
        self.num_turns: int = 0
        self._start_time = None

    def log_event(self, event: dict) -> None:
        """Parse an OpenCode JSON event and track metrics."""
        with open(self.path, "a") as f:
            f.write(json.dumps(event) + "\n")

        evt_type = event.get("type")
        part = event.get("part", {})

        if evt_type == "step_start":
            self.num_turns += 1
            if self._start_time is None:
                self._start_time = event.get("timestamp")

        elif evt_type == "tool_use":
            tool_name = part.get("tool", "unknown")
            self.tools_used.append(tool_name)
            tool_input = part.get("state", {}).get("input", {})
            self._track_file_change(tool_name, tool_input)

            # Check if there is an error in the tool state
            state_status = part.get("state", {}).get("status")
            if state_status == "error":
                err_msg = part.get("state", {}).get("output", "")
                if err_msg:
                    self.errors.append(str(err_msg)[:200])

        elif evt_type == "step_finish":
            cost = part.get("cost")
            if cost is not None:
                self.total_cost_usd += cost

            # Record duration
            end_time = event.get("timestamp")
            if self._start_time and end_time:
                self.duration_ms = max(self.duration_ms, int(end_time - self._start_time))

        elif evt_type == "text":
            text = part.get("text", "")
            if text:
                # Keep accumulating result text, or just track the last block
                # OpenCode usually outputs the final answer in a text block at the end
                self.result_text += text

    def build_summary(self, session_id: str = "") -> str:
        """Build a concise summary string for the LLM."""
        parts = []

        cost_str = f"${self.total_cost_usd:.2f}" if self.total_cost_usd else "no cost data"
        if session_id:
            parts.append(f"**OpenCode completed** (session {session_id[:8]}, {cost_str})")
        else:
            parts.append(f"**OpenCode completed** ({cost_str})")

        unique_files = list(dict.fromkeys(self.files_changed))
        if unique_files:
            parts.append(f"- Files changed: {', '.join(unique_files)}")

        if self.tools_used:
            tool_summary = ", ".join(self.tools_used)
            parts.append(f"- {len(self.tools_used)} tool call(s): {tool_summary}")
        else:
            parts.append("- No tool calls")

        if self.errors:
            parts.append(f"- Errors: {len(self.errors)}")
            for err in self.errors[:3]:
                parts.append(f"  - {err[:200]}")

        if self.duration_ms:
            secs = self.duration_ms / 1000
            parts.append(f"- Duration: {secs:.1f}s")

        parts.append(f"- Full log: {self.path}")

        if self.result_text and not self.errors:
            preview = self.result_text[:500]
            if len(self.result_text) > 500:
                preview += "..."
            parts.append(f"\n**Result:**\n{preview}")

        return "\n".join(parts)

    def build_data(
        self,
        session_id: str = "",
        exit_status: str = "success",
        sdk_session_id: str | None = None,
        send_count: int = 0,
        diff: str | None = None,
    ) -> dict:
        from collections import Counter

        tool_counts = dict(Counter(self.tools_used))
        unique_files = list(dict.fromkeys(self.files_changed))
        errors = [{"message": e} for e in self.errors[:10]]
        return {
            "exit_status": exit_status,
            "files_changed": unique_files,
            "tools_used": tool_counts,
            "errors": errors,
            "cost_usd": self.total_cost_usd,
            "duration_ms": self.duration_ms,
            "send_count": send_count,
            "num_turns": self.num_turns,
            "result_text": self.result_text[:500] if self.result_text else "",
            "result_text_truncated": len(self.result_text) > 500,
            "sdk_session_id": sdk_session_id or "",
            "log_path": str(self.path),
            "diff": diff,
        }

    def log_exec(self, command: str, exit_code: int | None, stdout: str, stderr: str, duration_ms: int) -> None:
        record = {
            "type": "exec",
            "command": command,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": duration_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def _track_file_change(self, tool_name: str, tool_input: dict) -> None:
        if tool_name in ("Edit", "Write", "NotebookEdit", "write", "edit", "replace"):
            # OpenCode might use different property names, e.g. "filePath" or "path"
            file_path = tool_input.get("filePath", tool_input.get("file_path", ""))
            if file_path and file_path not in self.files_changed:
                self.files_changed.append(file_path)


log = logging.getLogger(__name__)


@dataclass
class Session:
    """An OpenCode session tied to a working directory."""

    session_id: str
    cwd: str
    description: str = ""
    model: str | None = None
    budget_usd: float = 2.0
    sdk_session_id: str | None = None  # from OpenCode JSON sessionID
    created_at: float = field(default_factory=time.monotonic)
    last_active: float = field(default_factory=time.monotonic)
    total_cost_usd: float = 0
    send_count: int = 0
    approved: bool = False
    instructions: str = ""


class SessionManager:
    """Manages OpenCode session lifecycle with idle expiration."""

    def __init__(self, timeout_sec: int, budget_default: float, budget_max: float):
        self.sessions: dict[str, Session] = {}
        self.cwd_to_session: dict[str, str] = {}  # cwd -> session_id
        self.timeout_sec = timeout_sec
        self.budget_default = budget_default
        self.budget_max = budget_max

    def create(
        self,
        cwd: str,
        description: str = "",
        model: str | None = None,
        budget_usd: float | None = None,
        instructions: str = "",
    ) -> Session:
        """Create a new session. Raises ValueError if cwd already has an active session."""
        # Resolve and normalize path
        cwd = str(cwd).rstrip("/")

        # Check for existing active session at this cwd
        existing_id = self.cwd_to_session.get(cwd)
        if existing_id and self.get(existing_id) is not None:
            raise ValueError(
                f"A session already exists for {cwd} (id={existing_id[:8]}). "
                f"Use opencode_send to continue it, or opencode_stop to end it first."
            )

        # Clamp budget
        if budget_usd is None or budget_usd <= 0:
            budget = self.budget_default
        else:
            budget = min(budget_usd, self.budget_max)

        now = time.monotonic()
        session = Session(
            session_id=uuid4().hex[:12],
            cwd=cwd,
            description=description,
            model=model,
            budget_usd=budget,
            created_at=now,
            last_active=now,
            instructions=instructions,
        )
        self.sessions[session.session_id] = session
        self.cwd_to_session[cwd] = session.session_id
        log.info(f"Created OpenCode session {session.session_id} for {cwd}")
        return session

    def get(self, session_id: str) -> Session | None:
        """Get a session, or None if expired/not found. Lazy expiration check."""
        session = self.sessions.get(session_id)
        if session is None:
            return None

        # Check expiration
        if time.monotonic() - session.last_active > self.timeout_sec:
            log.info(f"Session {session_id} expired (idle > {self.timeout_sec}s)")
            self._remove(session_id)
            return None

        return session

    def touch(self, session_id: str) -> None:
        """Update last_active timestamp."""
        session = self.sessions.get(session_id)
        if session:
            session.last_active = time.monotonic()

    def stop(self, session_id: str) -> Session | None:
        """Remove and return a session. Returns None if not found."""
        session = self.sessions.get(session_id)
        if session is None:
            return None
        self._remove(session_id)
        return session

    def list_active(self) -> list[Session]:
        """Return all non-expired sessions."""
        now = time.monotonic()
        expired = [sid for sid, s in self.sessions.items() if now - s.last_active > self.timeout_sec]
        for sid in expired:
            self._remove(sid)
        return list(self.sessions.values())

    def close_all(self) -> list[Session]:
        """Remove all sessions. Returns the removed sessions."""
        removed = list(self.sessions.values())
        self.sessions.clear()
        self.cwd_to_session.clear()
        return removed

    def _remove(self, session_id: str) -> None:
        """Remove a session from both indexes."""
        session = self.sessions.pop(session_id, None)
        if session:
            self.cwd_to_session.pop(session.cwd, None)


if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def _allowlist_path(config) -> Path:
    """Path to the OpenCode allow patterns file (admin-managed)."""
    return config.agent_path / "opencode_allow_patterns.json"


def load_allowlist(config) -> list[str]:
    """Load allow patterns from disk. Returns [] if missing or corrupt."""
    path = _allowlist_path(config)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
        return data.get("patterns", [])
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Could not read OpenCode allow patterns: {e}")
        return []


def save_allowlist_entry(config, pattern: str) -> None:
    """Add a pattern to the allowlist."""
    path = _allowlist_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    patterns = load_allowlist(config)
    if pattern not in patterns:
        patterns.append(pattern)
        path.write_text(json.dumps(patterns, indent=2) + "\n")
        log.info(f"Added OpenCode allow pattern: {pattern}")


def matches_allowlist(tool_name: str, patterns: list[str]) -> bool:
    """Check if a tool name matches any allow pattern."""
    for pattern in patterns:
        if fnmatch.fnmatch(tool_name, pattern):
            return True
    return False


if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


@dataclass
class SkillConfig:
    model: str = field(default="", metadata={"env_alias": "OPENCODE_MODEL"})
    budget_default: float = field(default=2.0, metadata={"env_alias": "OPENCODE_BUDGET_DEFAULT"})
    budget_max: float = field(default=10.0, metadata={"env_alias": "OPENCODE_BUDGET_MAX"})
    session_timeout: str = field(default="30m", metadata={"env_alias": "OPENCODE_SESSION_TIMEOUT"})


# Module state, populated by init()
_config = None
_skill_config: SkillConfig | None = None
_session_manager: SessionManager | None = None


def init(config, skill_config: SkillConfig):
    """Initialize the OpenCode skill. Called by the skill loader on activation."""
    global _config, _skill_config, _session_manager
    _config = config
    _skill_config = skill_config

    from decafclaw.heartbeat import parse_interval

    timeout_sec = parse_interval(skill_config.session_timeout) or 1800

    _session_manager = SessionManager(
        timeout_sec=timeout_sec,
        budget_default=skill_config.budget_default,
        budget_max=skill_config.budget_max,
    )
    log.info(
        f"OpenCode skill initialized (timeout={timeout_sec}s, "
        f"budget={skill_config.budget_default}/{skill_config.budget_max})"
    )


def _get_manager() -> SessionManager:
    if _session_manager is None:
        raise RuntimeError("OpenCode skill not initialized")
    return _session_manager


_PROBE_TOOLS = ["python3", "node", "go", "uv", "pip", "npm", "pnpm", "make", "git", "cargo", "rustc"]
_PROBE_FILES = ["Makefile", "pyproject.toml", "package.json", "go.mod", "Cargo.toml", "CLAUDE.md", "README.md", ".env"]


def _assemble_prompt(prompt: str, instructions: str = "", context: str = "") -> str:
    """Build the full prompt with optional instructions and context preamble."""
    parts = []
    if instructions:
        parts.append(f"<instructions>\n{instructions}\n</instructions>")
    if context:
        parts.append(f"<context>\n{context}\n</context>")
    parts.append(prompt)
    return "\n\n".join(parts)


_BUDGET_THRESHOLDS = [0.5, 0.75, 0.9]


def _check_budget_warnings(cost: float, budget: float, fired: set[float]) -> list[str]:
    """Return list of budget warning messages for newly crossed thresholds."""
    warnings = []
    for threshold in _BUDGET_THRESHOLDS:
        if threshold not in fired and budget > 0 and cost >= budget * threshold:
            pct = int(threshold * 100)
            actual_pct = (cost / budget) * 100
            warnings.append(
                f"Budget warning: exceeded {pct}% threshold ({actual_pct:.0f}% used, ${cost:.2f} of ${budget:.2f})"
            )
            fired.add(threshold)
    return warnings


async def _probe_environment(cwd: str) -> dict:
    """Run a quick environment probe in cwd. Best-effort, 5s timeout."""
    result: dict = {"tools_available": [], "project_files": [], "git": None}

    async def _do_probe():
        # Batch check tools on PATH
        script = "; ".join(f"which {cmd} >/dev/null 2>&1 && echo {cmd}" for cmd in _PROBE_TOOLS)
        proc = await asyncio.create_subprocess_shell(
            script,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        result["tools_available"] = [line for line in stdout.decode().strip().splitlines() if line]

        cwd_path = Path(cwd)
        result["project_files"] = [name for name in _PROBE_FILES if (cwd_path / name).exists()]

        if (cwd_path / ".git").exists():
            branch_proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                cwd,
                "branch",
                "--show-current",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            branch_out, _ = await branch_proc.communicate()
            status_proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                cwd,
                "status",
                "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            status_out, _ = await status_proc.communicate()
            result["git"] = {
                "branch": branch_out.decode().strip(),
                "clean": len(status_out.decode().strip()) == 0,
            }

    try:
        await asyncio.wait_for(_do_probe(), timeout=5.0)
    except asyncio.TimeoutError:
        log.warning("Environment probe timed out after 5s")
    except Exception as e:
        log.warning(f"Environment probe error: {e}")

    return result


async def _run_setup_command(cwd: str, command: str, timeout: float = 30.0) -> dict:
    """Run a setup command in cwd and return structured result."""
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"command": command, "exit_code": None, "stdout": "", "stderr": "", "status": "timeout"}
    return {
        "command": command,
        "exit_code": proc.returncode,
        "stdout": stdout.decode(errors="replace"),
        "stderr": stderr.decode(errors="replace"),
        "status": "success" if proc.returncode == 0 else "error",
    }


async def _get_git_head(cwd: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            cwd,
            "rev-parse",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode().strip()
        return None
    except Exception:
        return None


async def _capture_git_diff(cwd: str, baseline_ref: str | None) -> str | None:
    if baseline_ref is None:
        return None
    try:
        parts = []
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            cwd,
            "diff",
            f"{baseline_ref}..HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        committed_diff = stdout.decode(errors="replace").strip() if proc.returncode == 0 else ""
        if committed_diff:
            parts.append(committed_diff)

        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            cwd,
            "diff",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        unstaged_diff = stdout.decode(errors="replace").strip() if proc.returncode == 0 else ""
        if unstaged_diff:
            parts.append(unstaged_diff)

        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            cwd,
            "ls-files",
            "--others",
            "--exclude-standard",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        untracked = stdout.decode(errors="replace").strip() if proc.returncode == 0 else ""
        if untracked:
            file_list = "\n".join(f"  {f}" for f in untracked.splitlines() if f)
            parts.append(f"New untracked files:\n{file_list}")

        return "\n".join(parts) if parts else ""
    except Exception as e:
        log.warning(f"Git diff capture failed: {e}")
        return None


async def tool_opencode_start(
    ctx: "Context",
    cwd: str,
    description: str = "",
    model: str = "",
    budget_usd: float = 0,
    setup_command: str = "",
    instructions: str = "",
) -> ToolResult:
    """Start a new OpenCode session for a working directory within the workspace."""
    log.info(f"[tool:opencode_start] cwd={cwd}")
    manager = _get_manager()

    workspace = _config.workspace_path if _config else Path(".")
    resolved = (workspace / cwd).resolve()
    workspace_resolved = workspace.resolve()
    if not resolved.is_relative_to(workspace_resolved):
        return ToolResult(text=f"[error: path must be within the workspace ({workspace})]")
    if not resolved.is_dir():
        resolved.mkdir(parents=True, exist_ok=True)
        log.info(f"Created workspace directory: {resolved}")
    cwd = str(resolved)

    try:
        session = manager.create(
            cwd=cwd,
            description=description,
            model=model or None,
            budget_usd=budget_usd if budget_usd > 0 else None,
            instructions=instructions,
        )
    except ValueError as e:
        return ToolResult(text=f"[error: {e}]")

    env_info = await _probe_environment(cwd)

    setup_result = None
    if setup_command:
        from decafclaw.tools.confirmation import request_confirmation

        patterns = load_allowlist(_config) if _config else []
        run_setup = False
        if matches_allowlist("opencode_setup", patterns):
            session.approved = True
            run_setup = True
        else:
            confirm = await request_confirmation(
                ctx,
                tool_name="opencode_setup",
                command=f"Setup: {setup_command}",
                message=(f"**OpenCode** wants to run a setup command in `{cwd}`:\n```\n{setup_command}\n```"),
            )
            if confirm.get("approved"):
                session.approved = True
                run_setup = True
                if confirm.get("always"):
                    save_allowlist_entry(_config, "opencode_setup")

        if run_setup:
            try:
                setup_result = await _run_setup_command(cwd, setup_command)
            except Exception as e:
                setup_result = {
                    "command": setup_command,
                    "status": "error",
                    "stdout": "",
                    "stderr": str(e),
                    "exit_code": None,
                }
        else:
            setup_result = {
                "command": setup_command,
                "status": "skipped",
                "stdout": "",
                "stderr": "",
                "exit_code": None,
            }

    model_str = session.model or (_skill_config.model if _skill_config else "") or "(CLI default)"

    data = {
        "session_id": session.session_id,
        "cwd": session.cwd,
        "model": model_str,
        "budget_usd": session.budget_usd,
        "environment": env_info,
        "setup": setup_result,
    }

    parts = [
        "OpenCode session started.",
        f"- **Session ID:** `{session.session_id}`",
        f"- **Working directory:** {session.cwd}",
        f"- **Budget:** ${session.budget_usd:.2f}",
        f"- **Model:** {model_str}",
    ]
    if env_info["tools_available"]:
        parts.append(f"- **Tools on PATH:** {', '.join(env_info['tools_available'])}")
    if env_info["project_files"]:
        parts.append(f"- **Project files:** {', '.join(env_info['project_files'])}")
    if env_info["git"]:
        git = env_info["git"]
        git_status = "clean" if git["clean"] else "dirty"
        parts.append(f"- **Git:** branch `{git['branch']}` ({git_status})")
    if setup_result:
        if setup_result["status"] == "success":
            parts.append(f"- **Setup:** `{setup_command}` succeeded")
        elif setup_result["status"] == "skipped":
            parts.append(f"- **Setup:** `{setup_command}` skipped (not approved)")
        elif setup_result["status"] == "timeout":
            parts.append(f"- **Setup:** `{setup_command}` timed out")
        else:
            parts.append(f"- **Setup:** `{setup_command}` failed (exit {setup_result['exit_code']})")
    parts.append("\nUse `opencode_send` with this session ID to send tasks.")

    return ToolResult(text="\n".join(parts), data=data)


def _send_error_data(exit_status: str, **extra) -> dict:
    data = {
        "exit_status": exit_status,
        "files_changed": [],
        "tools_used": {},
        "errors": [],
        "cost_usd": 0,
        "duration_ms": 0,
        "send_count": 0,
        "num_turns": 0,
        "result_text": "",
        "result_text_truncated": False,
        "sdk_session_id": "",
        "log_path": "",
        "diff": None,
    }
    data.update(extra)
    return data


def _build_short_text(exit_status: str, logger: SessionLogger) -> str:
    cost_str = f"${logger.total_cost_usd:.2f}" if logger.total_cost_usd else ""
    n_files = len(dict.fromkeys(logger.files_changed))
    short_parts = [exit_status]
    if cost_str:
        short_parts.append(cost_str)
    if n_files:
        short_parts.append(f"{n_files} file{'s' if n_files != 1 else ''} changed")
    if logger.errors:
        short_parts.append(f"{len(logger.errors)} error{'s' if len(logger.errors) != 1 else ''}")
    return " - ".join(short_parts)


def _summarize_tool_use(name: str, inp: dict) -> str:
    if name in ("Edit", "Write", "NotebookEdit", "write", "edit", "replace"):
        path = inp.get("filePath", inp.get("file_path", ""))
        return f"{name} {path}" if path else name
    if name in ("Read", "read"):
        path = inp.get("filePath", inp.get("file_path", ""))
        offset = inp.get("offset")
        limit = inp.get("limit")
        suffix = ""
        if offset is not None and limit is not None:
            suffix = f" (lines {offset}-{offset + limit})"
        elif offset is not None:
            suffix = f" (from line {offset})"
        return f"{name} {path}{suffix}" if path else name
    if name in ("Bash", "bash"):
        cmd = inp.get("command", "")
        first_line = cmd.split("\n", 1)[0]
        if len(first_line) > 80:
            first_line = first_line[:77] + "..."
        return f"{name} — {first_line}" if first_line else name

    if not inp:
        return name
    pairs = []
    for k, v in inp.items():
        s = str(v)
        if len(s) > 40:
            s = s[:37] + "..."
        pairs.append(f"{k}={s}")
    detail = ", ".join(pairs)
    if len(detail) > 120:
        detail = detail[:117] + "..."
    return f"{name} — {detail}"


async def tool_opencode_send(
    ctx: "Context", session_id: str, prompt: str, context: str = "", include_diff: bool = True
) -> ToolResult:
    """Send a prompt to an active OpenCode session."""
    log.info(f"[tool:opencode_send] session={session_id}")
    manager = _get_manager()

    session = manager.get(session_id)
    if session is None:
        return ToolResult(
            text=(f"[error: session '{session_id}' not found or expired. Start a new session with opencode_start.]"),
            data=_send_error_data("error"),
        )

    from decafclaw.tools.confirmation import request_confirmation

    patterns = load_allowlist(_config) if _config else []
    if matches_allowlist("opencode_send", patterns):
        session.approved = True
    else:
        prompt_preview = prompt[:200] + ("..." if len(prompt) > 200 else "")
        confirm = await request_confirmation(
            ctx,
            tool_name="opencode_send",
            command=f"Send to OpenCode: {prompt_preview}",
            message=(
                f"**OpenCode** wants to execute a task in `{session.cwd}`:\n"
                f"```\n{prompt_preview}\n```\n"
                f"This may read, edit, and create files, and run shell commands."
            ),
        )
        if not confirm.get("approved"):
            return ToolResult(
                text="[error: OpenCode task was denied by user]",
                data=_send_error_data("cancelled"),
            )
        if confirm.get("always"):
            save_allowlist_entry(_config, "opencode_send")
        session.approved = True

    if session.total_cost_usd >= session.budget_usd:
        return ToolResult(
            text=(
                f"[error: session budget exhausted (${session.total_cost_usd:.2f} / "
                f"${session.budget_usd:.2f}). Stop this session and start a new one "
                f"with a higher budget if needed.]"
            ),
            data=_send_error_data("budget_exhausted", cost_usd=session.total_cost_usd),
        )

    log_dir = _config.workspace_path / "opencode-logs" if _config else Path("opencode-logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = SessionLogger(log_dir, session.session_id)

    baseline_ref = None
    if include_diff:
        baseline_ref = await _get_git_head(session.cwd)

    full_prompt = _assemble_prompt(prompt, session.instructions, context)

    # Build the opencode run command arguments
    cmd_args = ["opencode", "run", full_prompt, "--dir", session.cwd, "--format", "json", "--auto"]
    if session.sdk_session_id:
        cmd_args.extend(["--continue", "--session", session.sdk_session_id])
    if session.model:
        cmd_args.extend(["--model", session.model])
    elif _skill_config and _skill_config.model:
        cmd_args.extend(["--model", _skill_config.model])

    tool_call_count = 0
    warnings_fired: set[float] = set()

    await ctx.publish("tool_status", tool="opencode_send", message=f"Sending to OpenCode ({session.cwd})...")

    proc = await asyncio.create_subprocess_exec(
        *cmd_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def read_stdout():
        nonlocal tool_call_count
        while True:
            line = await proc.stdout.readline() if proc.stdout else b""
            if not line:
                break
            try:
                event = json.loads(line)
                logger.log_event(event)

                evt_type = event.get("type")
                part = event.get("part", {})

                if evt_type == "step_start":
                    if part.get("sessionID") and not session.sdk_session_id:
                        session.sdk_session_id = part.get("sessionID")

                elif evt_type == "tool_use":
                    tool_call_count += 1
                    tool_name = part.get("tool", "unknown")
                    inp = part.get("state", {}).get("input", {})
                    summary = _summarize_tool_use(tool_name, inp)
                    await ctx.publish("tool_status", tool="opencode_send", message=f"[{tool_call_count}] {summary}")
                    # Check for quick error status
                    state_status = part.get("state", {}).get("status")
                    if state_status == "error":
                        err_out = part.get("state", {}).get("output", "")
                        snippet = str(err_out)[:100]
                        await ctx.publish(
                            "tool_status", tool="opencode_send", message=f"\u2192 {tool_name} failed — {snippet}"
                        )
                    else:
                        await ctx.publish("tool_status", tool="opencode_send", message=f"\u2192 {tool_name} — done")

                elif evt_type == "step_finish":
                    cost = part.get("cost")
                    if cost is not None:
                        session.total_cost_usd += cost
                        await ctx.publish(
                            "tool_status",
                            tool="opencode_send",
                            message=(
                                f"Session cost: ${session.total_cost_usd:.2f} of ${session.budget_usd:.2f} budget"
                            ),
                        )
                        for warning in _check_budget_warnings(
                            session.total_cost_usd, session.budget_usd, warnings_fired
                        ):
                            await ctx.publish("tool_status", tool="opencode_send", message=warning)

            except json.JSONDecodeError:
                log.debug(f"OpenCode stdout not json: {line.decode().strip()}")

    async def read_stderr():
        while True:
            line = await proc.stderr.readline() if proc.stderr else b""
            if not line:
                break
            log.warning(f"OpenCode stderr: {line.decode().strip()}")

    await asyncio.gather(read_stdout(), read_stderr())
    await proc.wait()

    if proc.returncode != 0:
        log.warning(f"OpenCode run exited with {proc.returncode}")

    session.send_count += 1
    manager.touch(session_id)

    diff = None
    if include_diff:
        diff = await _capture_git_diff(session.cwd, baseline_ref)

    summary = logger.build_summary(session_id)
    exit_status = "error" if logger.errors else "success"
    data = logger.build_data(
        session_id=session_id,
        exit_status=exit_status,
        sdk_session_id=session.sdk_session_id,
        send_count=session.send_count,
        diff=diff,
    )
    short_text = _build_short_text(exit_status, logger)
    return ToolResult(text=summary, data=data, display_short_text=short_text)


async def tool_opencode_exec(ctx: "Context", session_id: str, command: str, timeout: int = 30) -> ToolResult:
    """Run a shell command in a session's cwd without an LLM turn."""
    log.info(f"[tool:opencode_exec] session={session_id} command={command[:80]}")
    manager = _get_manager()

    session = manager.get(session_id)
    if session is None:
        return ToolResult(
            text=(f"[error: session '{session_id}' not found or expired.]"),
            data={
                "status": "error",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "duration_ms": 0,
                "command": command,
            },
        )

    if not session.approved:
        from decafclaw.tools.confirmation import request_confirmation

        patterns = load_allowlist(_config) if _config else []
        if matches_allowlist("opencode_exec", patterns):
            session.approved = True
        else:
            confirm = await request_confirmation(
                ctx,
                tool_name="opencode_exec",
                command=f"Exec: {command}",
                message=(f"**OpenCode** wants to run a command in `{session.cwd}`:\n```\n{command}\n```"),
            )
            if not confirm.get("approved"):
                return ToolResult(
                    text="[error: command was denied by user]",
                    data={
                        "status": "cancelled",
                        "exit_code": None,
                        "stdout": "",
                        "stderr": "",
                        "duration_ms": 0,
                        "command": command,
                    },
                )
            if confirm.get("always"):
                save_allowlist_entry(_config, "opencode_exec")
            session.approved = True

    timeout = max(1, min(timeout, 120))
    start = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=session.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
        exit_code = proc.returncode
        stdout_str = stdout_bytes.decode(errors="replace")
        stderr_str = stderr_bytes.decode(errors="replace")
        status = "success" if exit_code == 0 else "error"
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        stdout_str = (await proc.stdout.read()).decode(errors="replace") if proc.stdout else ""
        stderr_str = (await proc.stderr.read()).decode(errors="replace") if proc.stderr else ""
        status = "timeout"
        exit_code = None
    except Exception as e:
        log.error(f"opencode_exec error: {e}", exc_info=True)
        stderr_str = str(e)
        status = "error"
        exit_code = None

    duration_ms = int((time.monotonic() - start) * 1000)

    log_dir = _config.workspace_path / "opencode-logs" if _config else Path("opencode-logs")
    logger = SessionLogger(log_dir, session.session_id)
    logger.log_exec(command, exit_code, stdout_str, stderr_str, duration_ms)

    manager.touch(session_id)

    parts = [f"**Exec** in `{session.cwd}`", f"```\n$ {command}\n```"]
    if status == "timeout":
        parts.append(f"**Timed out** after {timeout}s")
    else:
        parts.append(f"Exit code: {exit_code}")
    if stdout_str:
        parts.append(f"**stdout:**\n```\n{stdout_str}\n```")
    if stderr_str:
        parts.append(f"**stderr:**\n```\n{stderr_str}\n```")

    data = {
        "exit_code": exit_code,
        "stdout": stdout_str,
        "stderr": stderr_str,
        "status": status,
        "duration_ms": duration_ms,
        "command": command,
    }
    return ToolResult(text="\n".join(parts), data=data)


async def tool_opencode_push_file(ctx: "Context", session_id: str, source_path: str, dest_name: str = "") -> ToolResult:
    log.info(f"[tool:opencode_push_file] session={session_id} source={source_path}")
    manager = _get_manager()
    session = manager.get(session_id)
    if session is None:
        return ToolResult(text=(f"[error: session '{session_id}' not found or expired.]"), data={"status": "error"})

    workspace = _config.workspace_path if _config else Path(".")
    source = (workspace / source_path).resolve()
    if not source.is_relative_to(workspace.resolve()):
        return ToolResult(
            text=f"[error: source path must be within the workspace ({workspace})]", data={"status": "error"}
        )
    if not source.exists() or not source.is_file():
        return ToolResult(text=f"[error: invalid source file: {source_path}]", data={"status": "error"})

    if not dest_name:
        dest_name = source.name
    dest = Path(session.cwd, dest_name).resolve()
    if not dest.is_relative_to(Path(session.cwd).resolve()):
        return ToolResult(
            text=f"[error: dest path must be within the session cwd ({session.cwd})]", data={"status": "error"}
        )

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    except Exception as e:
        return ToolResult(text=f"[error: copy failed: {e}]", data={"status": "error"})

    manager.touch(session_id)
    size = dest.stat().st_size
    return ToolResult(
        text=f"Pushed `{source_path}` → `{dest_name}` ({size} bytes)",
        data={"status": "success", "source": str(source), "dest": str(dest), "size_bytes": size},
    )


async def tool_opencode_pull_file(ctx: "Context", session_id: str, source_name: str, dest_path: str = "") -> ToolResult:
    log.info(f"[tool:opencode_pull_file] session={session_id} source={source_name}")
    manager = _get_manager()
    session = manager.get(session_id)
    if session is None:
        return ToolResult(text=(f"[error: session '{session_id}' not found or expired.]"), data={"status": "error"})

    source = Path(session.cwd, source_name).resolve()
    if not source.is_relative_to(Path(session.cwd).resolve()):
        return ToolResult(
            text=f"[error: source path must be within the session cwd ({session.cwd})]", data={"status": "error"}
        )
    if not source.exists() or not source.is_file():
        return ToolResult(text=f"[error: invalid source file: {source_name}]", data={"status": "error"})

    workspace = _config.workspace_path if _config else Path(".")
    if not dest_path:
        dest_path = source.name
    dest = (workspace / dest_path).resolve()
    if not dest.is_relative_to(workspace.resolve()):
        return ToolResult(
            text=f"[error: dest path must be within the workspace ({workspace})]", data={"status": "error"}
        )

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    except Exception as e:
        return ToolResult(text=f"[error: copy failed: {e}]", data={"status": "error"})

    manager.touch(session_id)
    size = dest.stat().st_size
    return ToolResult(
        text=f"Pulled `{source_name}` → `{dest_path}` ({size} bytes)",
        data={"status": "success", "source": str(source), "dest": str(dest), "size_bytes": size},
    )


async def tool_opencode_stop(ctx: "Context", session_id: str) -> str | ToolResult:
    log.info(f"[tool:opencode_stop] session={session_id}")
    manager = _get_manager()
    session = manager.stop(session_id)
    if session is None:
        return ToolResult(text=f"[error: session '{session_id}' not found]")
    elapsed = time.monotonic() - session.created_at
    return (
        f"OpenCode session stopped.\n"
        f"- **Session:** `{session_id[:8]}`\n"
        f"- **Working directory:** {session.cwd}\n"
        f"- **Duration:** {elapsed:.0f}s\n"
        f"- **Sends:** {session.send_count}\n"
        f"- **Total cost:** ${session.total_cost_usd:.2f}"
    )


async def tool_opencode_sessions(ctx: "Context") -> str:
    log.info("[tool:opencode_sessions]")
    manager = _get_manager()
    sessions = manager.list_active()
    if not sessions:
        return "No active OpenCode sessions."
    lines = [f"**Active OpenCode sessions:** ({len(sessions)})\n"]
    now = time.monotonic()
    for s in sessions:
        age = now - s.created_at
        idle = now - s.last_active
        lines.append(
            f"- `{s.session_id}` — {s.cwd}\n"
            f"  {s.description or '(no description)'} | "
            f"age: {age:.0f}s | idle: {idle:.0f}s | "
            f"sends: {s.send_count} | cost: ${s.total_cost_usd:.2f}"
        )
    return "\n".join(lines)


async def shutdown():
    """Close all sessions. Called on skill deactivation."""
    if _session_manager:
        _session_manager.close_all()


TOOLS = {
    "opencode_start": tool_opencode_start,
    "opencode_send": tool_opencode_send,
    "opencode_exec": tool_opencode_exec,
    "opencode_push_file": tool_opencode_push_file,
    "opencode_pull_file": tool_opencode_pull_file,
    "opencode_stop": tool_opencode_stop,
    "opencode_sessions": tool_opencode_sessions,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "opencode_start",
            "description": "Start a new OpenCode session for a working directory. Only one session per directory. Returns a session ID for use with opencode_send.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Path to the project/repository to work in"},
                    "description": {"type": "string", "description": "What this session is for (optional)"},
                    "model": {"type": "string", "description": "Override the OpenCode model (optional)"},
                    "budget_usd": {
                        "type": "number",
                        "description": "Per-session cost limit in USD (optional, 0 = default)",
                    },
                    "setup_command": {"type": "string", "description": "Shell command to run for environment setup"},
                    "instructions": {
                        "type": "string",
                        "description": "Persistent instructions prepended to every send",
                    },
                },
                "required": ["cwd"],
            },
        },
    },
    {
        "type": "function",
        "timeout": None,
        "function": {
            "name": "opencode_send",
            "description": "Send a coding task or follow-up to an active OpenCode session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session ID from opencode_start"},
                    "prompt": {"type": "string", "description": "The coding task or follow-up message"},
                    "context": {"type": "string", "description": "Per-task context prepended to this send only."},
                    "include_diff": {
                        "type": "boolean",
                        "description": "Capture git diff of changes made during this send (default true).",
                    },
                },
                "required": ["session_id", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "opencode_exec",
            "description": "Run a shell command in an active OpenCode session's working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session ID from opencode_start"},
                    "command": {"type": "string", "description": "Shell command to run"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 30, max 120)"},
                },
                "required": ["session_id", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "opencode_push_file",
            "description": "Copy a file from the parent's workspace into an OpenCode session's working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session ID"},
                    "source_path": {"type": "string", "description": "Path to the file in the workspace"},
                    "dest_name": {
                        "type": "string",
                        "description": "Filename or relative path within the session's cwd",
                    },
                },
                "required": ["session_id", "source_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "opencode_pull_file",
            "description": "Copy a file from an OpenCode session's working directory to the parent's workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session ID"},
                    "source_name": {
                        "type": "string",
                        "description": "Filename or relative path within the session's cwd",
                    },
                    "dest_path": {"type": "string", "description": "Path in the workspace to copy to"},
                },
                "required": ["session_id", "source_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "opencode_stop",
            "description": "Stop an OpenCode session and free resources. Reports final cost.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session ID to stop"},
                },
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "opencode_sessions",
            "description": "List all active OpenCode sessions with their IDs, working directories, and cost so far.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
