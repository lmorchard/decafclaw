"""Claude Code skill — delegate coding tasks to Claude Code as a subagent."""

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from decafclaw.media import ToolResult
from decafclaw.skills.claude_code.output import SessionLogger
from decafclaw.skills.claude_code.sessions import SessionManager

log = logging.getLogger(__name__)


@dataclass
class SkillConfig:
    model: str = field(
        default="", metadata={"env_alias": "CLAUDE_CODE_MODEL"})
    budget_default: float = field(
        default=2.0, metadata={"env_alias": "CLAUDE_CODE_BUDGET_DEFAULT"})
    budget_max: float = field(
        default=10.0, metadata={"env_alias": "CLAUDE_CODE_BUDGET_MAX"})
    session_timeout: str = field(
        default="30m", metadata={"env_alias": "CLAUDE_CODE_SESSION_TIMEOUT"})


# Module state, populated by init()
_config = None
_skill_config: SkillConfig | None = None
_session_manager: SessionManager | None = None


def init(config, skill_config: SkillConfig):
    """Initialize the Claude Code skill. Called by the skill loader on activation."""
    global _config, _skill_config, _session_manager
    _config = config
    _skill_config = skill_config

    # Parse timeout
    from decafclaw.heartbeat import parse_interval
    timeout_sec = parse_interval(skill_config.session_timeout) or 1800

    _session_manager = SessionManager(
        timeout_sec=timeout_sec,
        budget_default=skill_config.budget_default,
        budget_max=skill_config.budget_max,
    )
    log.info(f"Claude Code skill initialized (timeout={timeout_sec}s, "
             f"budget={skill_config.budget_default}/{skill_config.budget_max})")


def _get_manager() -> SessionManager:
    if _session_manager is None:
        raise RuntimeError("Claude Code skill not initialized")
    return _session_manager


_PROBE_TOOLS = ["python3", "node", "go", "uv", "pip", "npm", "pnpm", "make", "git", "cargo", "rustc"]
_PROBE_FILES = ["Makefile", "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
                "CLAUDE.md", "README.md", ".env"]


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
                f"Budget warning: exceeded {pct}% threshold "
                f"({actual_pct:.0f}% used, ${cost:.2f} of ${budget:.2f})"
            )
            fired.add(threshold)
    return warnings


async def _probe_environment(cwd: str) -> dict:
    """Run a quick environment probe in cwd. Best-effort, 5s timeout."""
    result: dict = {"tools_available": [], "project_files": [], "git": None}

    async def _do_probe():
        # Batch check tools on PATH
        script = "; ".join(
            f"which {cmd} >/dev/null 2>&1 && echo {cmd}" for cmd in _PROBE_TOOLS
        )
        proc = await asyncio.create_subprocess_shell(
            script, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        result["tools_available"] = [
            line for line in stdout.decode().strip().splitlines() if line
        ]

        # Check project files
        cwd_path = Path(cwd)
        result["project_files"] = [
            name for name in _PROBE_FILES if (cwd_path / name).exists()
        ]

        # Git info
        if (cwd_path / ".git").exists():
            branch_proc = await asyncio.create_subprocess_exec(
                "git", "-C", cwd, "branch", "--show-current",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            branch_out, _ = await branch_proc.communicate()
            status_proc = await asyncio.create_subprocess_exec(
                "git", "-C", cwd, "status", "--porcelain",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            status_out, _ = await status_proc.communicate()
            result["git"] = {
                "branch": branch_out.decode().strip(),
                "clean": len(status_out.decode().strip()) == 0,
            }

    try:
        await asyncio.wait_for(_do_probe(), timeout=5.0)
    except asyncio.TimeoutError:
        log.warning("Environment probe timed out after 5s, returning partial results")
    except Exception as e:
        log.warning(f"Environment probe error: {e}")

    return result


async def _run_setup_command(cwd: str, command: str, timeout: float = 30.0) -> dict:
    """Run a setup command in cwd and return structured result."""
    proc = await asyncio.create_subprocess_shell(
        command, cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        log.warning(f"Setup command timed out after {timeout}s: {command}")
        proc.kill()
        await proc.wait()
        return {
            "command": command,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "status": "timeout",
        }
    return {
        "command": command,
        "exit_code": proc.returncode,
        "stdout": stdout.decode(errors="replace"),
        "stderr": stderr.decode(errors="replace"),
        "status": "success" if proc.returncode == 0 else "error",
    }


async def _get_git_head(cwd: str) -> str | None:
    """Get the current git HEAD hash, or None if not a git repo / empty repo."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", cwd, "rev-parse", "HEAD",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode().strip()
        return None
    except Exception:
        return None


async def _capture_git_diff(cwd: str, baseline_ref: str | None) -> str | None:
    """Capture git diff since baseline. Returns None if not applicable, "" if no changes."""
    if baseline_ref is None:
        return None

    try:
        parts = []

        # 1. Committed changes since baseline (baseline..HEAD, excludes working tree)
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", cwd, "diff", f"{baseline_ref}..HEAD",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        committed_diff = stdout.decode(errors="replace").strip() if proc.returncode == 0 else ""
        if committed_diff:
            parts.append(committed_diff)

        # 2. Unstaged changes to tracked files (working tree vs index)
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", cwd, "diff",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        unstaged_diff = stdout.decode(errors="replace").strip() if proc.returncode == 0 else ""
        if unstaged_diff:
            parts.append(unstaged_diff)

        # 3. New untracked files
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", cwd, "ls-files", "--others", "--exclude-standard",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
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


async def tool_claude_code_start(ctx, cwd: str, description: str = "",
                                  model: str = "", budget_usd: float = 0,
                                  setup_command: str = "",
                                  instructions: str = "") -> ToolResult:
    """Start a new Claude Code session for a working directory within the workspace."""
    log.info(f"[tool:claude_code_start] cwd={cwd}")
    manager = _get_manager()

    # Resolve cwd relative to workspace and enforce sandbox
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

    # Environment probe
    env_info = await _probe_environment(cwd)

    # Optional setup command
    setup_result = None
    if setup_command:
        from decafclaw.skills.claude_code.permissions import load_allowlist, matches_allowlist
        from decafclaw.tools.confirmation import request_confirmation

        patterns = load_allowlist(_config) if _config else []
        run_setup = False
        if matches_allowlist("claude_code_setup", patterns):
            session.approved = True
            run_setup = True
        else:
            confirm = await request_confirmation(
                ctx,
                tool_name="claude_code_setup",
                command=f"Setup: {setup_command}",
                message=(
                    f"**Claude Code** wants to run a setup command in `{cwd}`:\n"
                    f"```\n{setup_command}\n```"
                ),
            )
            if confirm.get("approved"):
                session.approved = True
                run_setup = True
                if confirm.get("always"):
                    from decafclaw.skills.claude_code.permissions import save_allowlist_entry
                    save_allowlist_entry(_config, "claude_code_setup")

        if run_setup:
            try:
                setup_result = await _run_setup_command(cwd, setup_command)
            except Exception as e:
                log.error(f"Setup command failed: {e}", exc_info=True)
                setup_result = {"command": setup_command, "status": "error",
                                "stdout": "", "stderr": str(e), "exit_code": None}
        else:
            setup_result = {"command": setup_command, "status": "skipped",
                            "stdout": "", "stderr": "", "exit_code": None}

    # Build model string
    model_str = session.model or (_skill_config.model if _skill_config else "") or "(SDK default)"

    # Structured data
    data = {
        "session_id": session.session_id,
        "cwd": session.cwd,
        "model": model_str,
        "budget_usd": session.budget_usd,
        "environment": env_info,
        "setup": setup_result,
    }

    # Text summary
    parts = [
        "Claude Code session started.",
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
    parts.append("\nUse `claude_code_send` with this session ID to send tasks.")

    return ToolResult(text="\n".join(parts), data=data)


def _send_error_data(exit_status: str, **extra) -> dict:
    """Build a consistent structured data dict for send error paths."""
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


async def tool_claude_code_send(ctx, session_id: str, prompt: str,
                                context: str = "",
                                include_diff: bool = True) -> ToolResult:
    """Send a prompt to an active Claude Code session."""
    log.info(f"[tool:claude_code_send] session={session_id}")
    manager = _get_manager()

    session = manager.get(session_id)
    if session is None:
        return ToolResult(
            text=(f"[error: session '{session_id}' not found or expired. "
                  f"Start a new session with claude_code_start.]"),
            data=_send_error_data("error"),
        )

    # Upfront confirmation — one approval per send, not per tool.
    # The SDK's can_use_tool callback has reliability issues with repeated
    # calls, so we confirm before sending to the SDK instead.
    from decafclaw.skills.claude_code.permissions import load_allowlist, matches_allowlist
    from decafclaw.tools.confirmation import request_confirmation

    # Skip confirmation if "claude_code_send" is in the allowlist
    patterns = load_allowlist(_config) if _config else []
    if matches_allowlist("claude_code_send", patterns):
        session.approved = True
    else:
        prompt_preview = prompt[:200] + ("..." if len(prompt) > 200 else "")
        confirm = await request_confirmation(
            ctx,
            tool_name="claude_code_send",
            command=f"Send to Claude Code: {prompt_preview}",
            message=(
                f"**Claude Code** wants to execute a task in `{session.cwd}`:\n"
                f"```\n{prompt_preview}\n```\n"
                f"This may read, edit, and create files, and run shell commands."
            ),
        )
        if not confirm.get("approved"):
            return ToolResult(
                text="[error: Claude Code task was denied by user]",
                data=_send_error_data("cancelled"),
            )
        if confirm.get("always"):
            from decafclaw.skills.claude_code.permissions import save_allowlist_entry
            save_allowlist_entry(_config, "claude_code_send")
        session.approved = True

    # Check budget
    if session.total_cost_usd >= session.budget_usd:
        return ToolResult(
            text=(
                f"[error: session budget exhausted (${session.total_cost_usd:.2f} / "
                f"${session.budget_usd:.2f}). Stop this session and start a new one "
                f"with a higher budget if needed.]"
            ),
            data=_send_error_data("budget_exhausted",
                                  cost_usd=session.total_cost_usd),
        )

    # Build options
    model = session.model or (_skill_config.model if _skill_config else None) or None
    log_dir = _config.workspace_path / "claude-code-logs" if _config else Path("claude-code-logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    stderr_path = log_dir / f"{session.session_id}.stderr.log"
    stderr_file = open(stderr_path, "a")
    # bypassPermissions at CLI level — the SDK's can_use_tool callback
    # has a known bug where the CLI's control protocol closes the stream
    # before permission responses arrive, even with the documented
    # PreToolUse hook workaround (github.com/anthropics/claude-code/issues/24607).
    # Instead, we use upfront confirmation per claude_code_send (above)
    # and bypassPermissions for the CLI subprocess.
    options = ClaudeCodeOptions(
        cwd=session.cwd,
        model=model,
        permission_mode="bypassPermissions",
        debug_stderr=stderr_file,
    )

    # Resume existing session if we have an SDK session ID from a previous send
    if session.sdk_session_id:
        options.resume = session.sdk_session_id
        options.continue_conversation = True

    # Set up logger (log_dir already created above for stderr)
    logger = SessionLogger(log_dir, session.session_id)

    # Per-send progress tracking
    tool_call_count = 0
    tool_id_to_name: dict[str, str] = {}
    warnings_fired: set[float] = set()

    # Capture git baseline for diff (before any changes)
    baseline_ref = None
    if include_diff:
        baseline_ref = await _get_git_head(session.cwd)

    # Assemble full prompt with session instructions and per-send context
    full_prompt = _assemble_prompt(prompt, session.instructions, context)

    # Wrap prompt as async iterable (required when can_use_tool is set)
    # Format per SDK docs: type, message, parent_tool_use_id, session_id
    async def prompt_stream():
        yield {
            "type": "user",
            "message": {"role": "user", "content": full_prompt},
            "parent_tool_use_id": None,
            "session_id": session.sdk_session_id or "default",
        }

    # Stream messages from the SDK
    await ctx.publish("tool_status", tool="claude_code",
                      message=f"Sending to Claude Code ({session.cwd})...")

    try:
        async for message in query(prompt=prompt_stream(), options=options):
            log.debug(f"Claude Code message: {type(message).__name__}")
            logger.log_message(message)

            # Publish progress for Mattermost display
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text:
                        # Don't publish every text chunk — just tool usage
                        pass
                    elif isinstance(block, ToolUseBlock):
                        tool_call_count += 1
                        tool_id_to_name[block.id] = block.name
                        await ctx.publish(
                            "tool_status", tool="claude_code",
                            message=f"Tool call {tool_call_count}: Using {block.name}..."
                        )

            elif isinstance(message, UserMessage):
                for block in getattr(message, "content", []):
                    if isinstance(block, ToolResultBlock) and block.is_error:
                        tool_name = tool_id_to_name.get(
                            block.tool_use_id, "unknown tool")
                        snippet = (block.content if isinstance(block.content, str)
                                   else str(block.content))[:100]
                        await ctx.publish(
                            "tool_status", tool="claude_code",
                            message=f"{tool_name} failed — {snippet}"
                        )

            elif isinstance(message, ResultMessage):
                # Capture the SDK session ID for resume
                if message.session_id:
                    session.sdk_session_id = message.session_id
                # Update cost tracking
                if message.total_cost_usd is not None:
                    session.total_cost_usd = message.total_cost_usd
                    await ctx.publish(
                        "tool_status", tool="claude_code",
                        message=(f"Session cost: ${session.total_cost_usd:.2f} "
                                 f"of ${session.budget_usd:.2f} budget")
                    )
                    for warning in _check_budget_warnings(
                        session.total_cost_usd, session.budget_usd,
                        warnings_fired
                    ):
                        await ctx.publish(
                            "tool_status", tool="claude_code",
                            message=warning
                        )

    except Exception as e:
        log.error(f"Claude Code SDK error: {e}", exc_info=True)
        return ToolResult(
            text=f"[error: Claude Code failed: {e}]",
            data=_send_error_data("error", errors=[{"message": str(e)}]),
        )
    finally:
        stderr_file.close()

    # Update session state
    session.send_count += 1
    manager.touch(session_id)

    # Capture git diff of changes made during this send
    diff = None
    if include_diff:
        diff = await _capture_git_diff(session.cwd, baseline_ref)

    summary = logger.build_summary(session_id)
    data = logger.build_data(
        session_id=session_id,
        exit_status="error" if logger.errors else "success",
        sdk_session_id=session.sdk_session_id,
        send_count=session.send_count,
        diff=diff,
    )
    return ToolResult(text=summary, data=data)


async def tool_claude_code_exec(ctx, session_id: str, command: str,
                                timeout: int = 30) -> ToolResult:
    """Run a shell command in a session's cwd without an LLM turn."""
    log.info(f"[tool:claude_code_exec] session={session_id} command={command[:80]}")
    manager = _get_manager()

    session = manager.get(session_id)
    if session is None:
        return ToolResult(
            text=(f"[error: session '{session_id}' not found or expired. "
                  f"Start a new session with claude_code_start.]"),
            data={"status": "error", "exit_code": None, "stdout": "",
                  "stderr": "", "duration_ms": 0, "command": command},
        )

    # Confirmation — inherit from session if already approved
    if not session.approved:
        from decafclaw.skills.claude_code.permissions import load_allowlist, matches_allowlist
        from decafclaw.tools.confirmation import request_confirmation

        patterns = load_allowlist(_config) if _config else []
        if matches_allowlist("claude_code_exec", patterns):
            session.approved = True
        else:
            confirm = await request_confirmation(
                ctx,
                tool_name="claude_code_exec",
                command=f"Exec: {command}",
                message=(
                    f"**Claude Code** wants to run a command in `{session.cwd}`:\n"
                    f"```\n{command}\n```"
                ),
            )
            if not confirm.get("approved"):
                return ToolResult(
                    text="[error: command was denied by user]",
                    data={"status": "cancelled", "exit_code": None, "stdout": "",
                          "stderr": "", "duration_ms": 0, "command": command},
                )
            if confirm.get("always"):
                from decafclaw.skills.claude_code.permissions import save_allowlist_entry
                save_allowlist_entry(_config, "claude_code_exec")
            session.approved = True

    # Clamp timeout
    timeout = max(1, min(timeout, 120))

    start = time.monotonic()
    exit_code = None
    stdout_str = ""
    stderr_str = ""
    status = "success"

    try:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=session.cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
        exit_code = proc.returncode
        stdout_str = stdout_bytes.decode(errors="replace")
        stderr_str = stderr_bytes.decode(errors="replace")
        status = "success" if exit_code == 0 else "error"
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        # Try to capture partial output
        if proc.stdout:
            partial = await proc.stdout.read()
            stdout_str = partial.decode(errors="replace")
        if proc.stderr:
            partial = await proc.stderr.read()
            stderr_str = partial.decode(errors="replace")
        status = "timeout"
    except Exception as e:
        log.error(f"claude_code_exec subprocess error: {e}", exc_info=True)
        stderr_str = str(e)
        status = "error"

    duration_ms = int((time.monotonic() - start) * 1000)

    # Log to session JSONL
    log_dir = _config.workspace_path / "claude-code-logs" if _config else Path("claude-code-logs")
    logger = SessionLogger(log_dir, session.session_id)
    logger.log_exec(command, exit_code, stdout_str, stderr_str, duration_ms)

    # Touch session
    manager.touch(session_id)

    # Build text summary
    parts = [f"**Exec** in `{session.cwd}`"]
    parts.append(f"```\n$ {command}\n```")
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


async def tool_claude_code_push_file(ctx, session_id: str, source_path: str,
                                     dest_name: str = "") -> ToolResult:
    """Copy a file from the parent's workspace into the session's cwd."""
    log.info(f"[tool:claude_code_push_file] session={session_id} source={source_path}")
    manager = _get_manager()

    session = manager.get(session_id)
    if session is None:
        return ToolResult(
            text=(f"[error: session '{session_id}' not found or expired.]"),
            data={"status": "error"},
        )

    # Resolve source relative to workspace, enforce sandbox
    workspace = _config.workspace_path if _config else Path(".")
    source = (workspace / source_path).resolve()
    if not source.is_relative_to(workspace.resolve()):
        return ToolResult(
            text=f"[error: source path must be within the workspace ({workspace})]",
            data={"status": "error"},
        )
    if not source.exists():
        return ToolResult(
            text=f"[error: source file not found: {source_path}]",
            data={"status": "error"},
        )
    if not source.is_file():
        return ToolResult(
            text=f"[error: source is not a file (directories not supported): {source_path}]",
            data={"status": "error"},
        )

    # Resolve dest relative to session cwd, enforce sandbox
    if not dest_name:
        dest_name = source.name
    dest = Path(session.cwd, dest_name).resolve()
    if not dest.is_relative_to(Path(session.cwd).resolve()):
        return ToolResult(
            text=f"[error: dest path must be within the session cwd ({session.cwd})]",
            data={"status": "error"},
        )

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    except Exception as e:
        return ToolResult(
            text=f"[error: copy failed: {e}]",
            data={"status": "error"},
        )

    manager.touch(session_id)
    size = dest.stat().st_size

    return ToolResult(
        text=f"Pushed `{source_path}` → `{dest_name}` ({size} bytes)",
        data={"status": "success", "source": str(source), "dest": str(dest),
              "size_bytes": size},
    )


async def tool_claude_code_pull_file(ctx, session_id: str, source_name: str,
                                     dest_path: str = "") -> ToolResult:
    """Copy a file from the session's cwd to the parent's workspace."""
    log.info(f"[tool:claude_code_pull_file] session={session_id} source={source_name}")
    manager = _get_manager()

    session = manager.get(session_id)
    if session is None:
        return ToolResult(
            text=(f"[error: session '{session_id}' not found or expired.]"),
            data={"status": "error"},
        )

    # Resolve source relative to session cwd, enforce sandbox
    source = Path(session.cwd, source_name).resolve()
    if not source.is_relative_to(Path(session.cwd).resolve()):
        return ToolResult(
            text=f"[error: source path must be within the session cwd ({session.cwd})]",
            data={"status": "error"},
        )
    if not source.exists():
        return ToolResult(
            text=f"[error: source file not found: {source_name}]",
            data={"status": "error"},
        )
    if not source.is_file():
        return ToolResult(
            text=f"[error: source is not a file (directories not supported): {source_name}]",
            data={"status": "error"},
        )

    # Resolve dest relative to workspace, enforce sandbox
    workspace = _config.workspace_path if _config else Path(".")
    if not dest_path:
        dest_path = source.name
    dest = (workspace / dest_path).resolve()
    if not dest.is_relative_to(workspace.resolve()):
        return ToolResult(
            text=f"[error: dest path must be within the workspace ({workspace})]",
            data={"status": "error"},
        )

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    except Exception as e:
        return ToolResult(
            text=f"[error: copy failed: {e}]",
            data={"status": "error"},
        )

    manager.touch(session_id)
    size = dest.stat().st_size

    return ToolResult(
        text=f"Pulled `{source_name}` → `{dest_path}` ({size} bytes)",
        data={"status": "success", "source": str(source), "dest": str(dest),
              "size_bytes": size},
    )


async def tool_claude_code_stop(ctx, session_id: str) -> str:
    """Stop a Claude Code session and clean up."""
    log.info(f"[tool:claude_code_stop] session={session_id}")
    manager = _get_manager()

    session = manager.stop(session_id)
    if session is None:
        return f"[error: session '{session_id}' not found]"

    elapsed = time.monotonic() - session.created_at
    return (
        f"Claude Code session stopped.\n"
        f"- **Session:** `{session_id[:8]}`\n"
        f"- **Working directory:** {session.cwd}\n"
        f"- **Duration:** {elapsed:.0f}s\n"
        f"- **Sends:** {session.send_count}\n"
        f"- **Total cost:** ${session.total_cost_usd:.2f}"
    )


async def tool_claude_code_sessions(ctx) -> str:
    """List active Claude Code sessions."""
    log.info("[tool:claude_code_sessions]")
    manager = _get_manager()

    sessions = manager.list_active()
    if not sessions:
        return "No active Claude Code sessions."

    lines = [f"**Active Claude Code sessions:** ({len(sessions)})\n"]
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
    "claude_code_start": tool_claude_code_start,
    "claude_code_send": tool_claude_code_send,
    "claude_code_exec": tool_claude_code_exec,
    "claude_code_push_file": tool_claude_code_push_file,
    "claude_code_pull_file": tool_claude_code_pull_file,
    "claude_code_stop": tool_claude_code_stop,
    "claude_code_sessions": tool_claude_code_sessions,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "claude_code_start",
            "description": (
                "Start a new Claude Code session for a working directory. "
                "Only one session per directory. Returns a session ID for use "
                "with claude_code_send."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": "Path to the project/repository to work in",
                    },
                    "description": {
                        "type": "string",
                        "description": "What this session is for (optional)",
                    },
                    "model": {
                        "type": "string",
                        "description": "Override the Claude model (optional, empty = default)",
                    },
                    "budget_usd": {
                        "type": "number",
                        "description": "Per-session cost limit in USD (optional, 0 = default)",
                    },
                    "setup_command": {
                        "type": "string",
                        "description": (
                            "Shell command to run for environment setup before the session "
                            "is ready (e.g., 'uv sync', 'npm install'). Requires confirmation."
                        ),
                    },
                    "instructions": {
                        "type": "string",
                        "description": (
                            "Persistent instructions prepended to every send in this session. "
                            "Use for project conventions, coding style, constraints."
                        ),
                    },
                },
                "required": ["cwd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claude_code_send",
            "description": (
                "Send a coding task or follow-up to an active Claude Code session. "
                "The session maintains context across sends. Returns a summary of "
                "what Claude Code did."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from claude_code_start",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The coding task or follow-up message",
                    },
                    "context": {
                        "type": "string",
                        "description": (
                            "Per-task context prepended to this send only. "
                            "Use for relevant specs, vault pages, conversation excerpts."
                        ),
                    },
                    "include_diff": {
                        "type": "boolean",
                        "description": (
                            "Capture git diff of changes made during this send (default true). "
                            "Requires the cwd to be a git repo."
                        ),
                    },
                },
                "required": ["session_id", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claude_code_exec",
            "description": (
                "Run a shell command in an active Claude Code session's working directory. "
                "No LLM turn — direct subprocess execution. Use for quick verification "
                "(make test, git status, etc.) between claude_code_send calls."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from claude_code_start",
                    },
                    "command": {
                        "type": "string",
                        "description": "Shell command to run",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 30, max 120)",
                    },
                },
                "required": ["session_id", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claude_code_push_file",
            "description": (
                "Copy a file from the parent's workspace into a Claude Code session's "
                "working directory. Use to provide specs, configs, or other files to "
                "the coding session."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from claude_code_start",
                    },
                    "source_path": {
                        "type": "string",
                        "description": "Path to the file in the workspace (relative to workspace root)",
                    },
                    "dest_name": {
                        "type": "string",
                        "description": (
                            "Filename or relative path within the session's cwd. "
                            "Defaults to the basename of source_path."
                        ),
                    },
                },
                "required": ["session_id", "source_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claude_code_pull_file",
            "description": (
                "Copy a file from a Claude Code session's working directory to the "
                "parent's workspace. Use to retrieve build artifacts, generated code, "
                "or test results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from claude_code_start",
                    },
                    "source_name": {
                        "type": "string",
                        "description": "Filename or relative path within the session's cwd",
                    },
                    "dest_path": {
                        "type": "string",
                        "description": (
                            "Path in the workspace to copy to (relative to workspace root). "
                            "Defaults to the basename of source_name."
                        ),
                    },
                },
                "required": ["session_id", "source_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claude_code_stop",
            "description": "Stop a Claude Code session and free resources. Reports final cost.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID to stop",
                    },
                },
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claude_code_sessions",
            "description": "List all active Claude Code sessions with their IDs, working directories, and cost so far.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
