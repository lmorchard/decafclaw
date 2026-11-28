"""Tests for pre-execution security monitor for tool calls."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.security_monitor import (
    SYSTEM_EXEC_DIRS,
    SecurityStatus,
    evaluate_command,
    evaluate_command_llm,
    is_ambiguous_command,
    venv_executable_path,
)
from decafclaw.tools.shell_tools import check_shell_approval


def test_evaluates_dangerous_commands_as_block(tmp_path: Path):
    dangerous_commands = [
        "rm -rf /",
        "rm -rf ~",
        "rm -rf $HOME",
        "rm -rf ~/",
        "rm -rf $HOME/",
        "rm -rf '$HOME'",
        'rm -rf "$HOME/"',
        'rm -rf "/"',
        "dd if=/dev/zero of=/dev/sda",
        "shutdown -h now",
    ]

    for cmd in dangerous_commands:
        decision = evaluate_command(cmd, workspace_path=tmp_path)
        assert decision.status == SecurityStatus.BLOCK, f"Expected BLOCK for command: {cmd}"
        assert decision.reason, f"Expected reason for blocked command: {cmd}"


def test_evaluates_out_of_workspace_operations_as_ask(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    out_of_workspace_commands = [
        "cat /etc/passwd",
        "ls /var/log",
        "mv file.txt ../outside.txt",
        "cat </etc/passwd",
        "/var/log/python script.py",
        "/opt/custom/venv/bin/python script.py",
        "touch /tmp/../etc/passwd",
    ]

    for cmd in out_of_workspace_commands:
        decision = evaluate_command(cmd, workspace_path=workspace)
        assert decision.status == SecurityStatus.ASK, f"Expected ASK for out-of-workspace command: {cmd}"
        assert decision.reason, f"Expected reason for ASK command: {cmd}"


def test_filter_expressions_are_not_paths(tmp_path: Path):
    """sed/awk/grep scripts that look like absolute paths are data, not file paths (#1690)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    commands = [
        "sed -n '/Failed Tests/,/2\\/2/p' notes.txt",
        "test-runner 2>&1 | sed -n '/Failed Tests/,/2\\/2/p' | head -40",
        "sed '/delete_pattern/d' input.txt",
        "sed -ne '/start_marker/,/end_marker/p' input.txt",
        "sed -e '/start_marker/,/end_marker/p' -e 's/a/b/' input.txt",
        "awk '/pattern/ {print $1}' data.txt",
        "awk -v limit=10 '/pattern/ {print}' data.txt",
        "grep -e '/pattern/' data.txt",
        "grep -ie '/pattern/' data.txt",
        "grep -m 1 '/pattern/' data.txt",
        "grep --include '*.py' '/pattern/' src/",
        "grep '/pattern/' data.txt",
        "perl -e 'print \"/etc/passwd\"'",
        "perl -pe '/pattern/' data.txt",
    ]

    for cmd in commands:
        decision = evaluate_command(cmd, workspace_path=workspace)
        assert decision.status == SecurityStatus.ALLOW, f"Expected ALLOW for expression command: {cmd}"


def test_filter_expressions_do_not_hide_file_arguments(tmp_path: Path):
    """Only the script/pattern argument is skipped; file arguments stay checked."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    commands = [
        "sed -n '/x/p' /etc/shadow",
        "sed -e '/x/p' /etc/shadow",
        "sed -f script.sed /etc/passwd",
        "sed --file script.sed /etc/passwd",
        "sed -nf script.sed /etc/passwd",
        "awk '/x/ {print}' /etc/passwd",
        "awk -f /etc/prog.awk data.txt",
        "grep -e '/x/' /etc/passwd",
        "grep '/x/' /etc/passwd",
        "grep -f /etc/patterns data.txt",
        "grep -if /etc/patterns data.txt",
        "grep -m 1 '/x/' /etc/passwd",
        "grep --include '*.py' '/x/' /etc/passwd",
    ]

    for cmd in commands:
        decision = evaluate_command(cmd, workspace_path=workspace)
        assert decision.status == SecurityStatus.ASK, f"Expected ASK for file argument: {cmd}"


def test_heredoc_bodies_are_not_paths(tmp_path: Path):
    """Heredoc body text is data for the consumer, not shell commands (#1796)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    commands = [
        # Exact shape from the report: a JavaScript regex literal in a heredoc.
        "timeout 45 agent-browser eval --stdin <<'EOF' 2>&1 | head -20\n"
        "(() => {\n"
        "  let t = main.innerText.replace(/\\s+/g,' ');\n"
        "  return JSON.stringify({tail: t.slice(-1200)});\n"
        "})()\n"
        "EOF",
        "cat > out.txt <<'EOF'\n/etc/passwd\nEOF",
        "cat > out.txt <<EOF\n/home/user\nEOF",
        'cat > out.txt <<"EOF"\n/var/log\nEOF',
        "cat > out.txt <<-EOF\n\t/etc/passwd\n\tEOF",
        # Bodies arrive in declaration order for multiple heredocs on one line.
        "cat <<A <<B\n/etc/passwd\n/var/log\nA\n/root\nB",
    ]

    for cmd in commands:
        decision = evaluate_command(cmd, workspace_path=workspace)
        assert decision.status == SecurityStatus.ALLOW, f"Expected ALLOW for heredoc body: {cmd!r} ({decision.reason})"


def test_heredoc_does_not_hide_out_of_workspace_paths(tmp_path: Path):
    """Paths outside the body, including redirects, stay subject to the path check."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    commands = [
        "cat /etc/passwd <<'EOF'\nbody\nEOF",
        "cat <<'EOF' > /etc/passwd\nbody\nEOF",
        "cat < /etc/passwd <<'EOF'\nbody\nEOF",
    ]

    for cmd in commands:
        decision = evaluate_command(cmd, workspace_path=workspace)
        assert decision.status == SecurityStatus.ASK, f"Expected ASK for path outside body: {cmd!r}"


def test_quoted_or_arithmetic_shift_is_not_a_heredoc(tmp_path: Path):
    """Only a real heredoc declaration masks following lines."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    commands = [
        'echo "a << b"\ncat /etc/passwd',
        "echo $((1 << 2))\ncat /etc/passwd",
        "echo hi # <<EOF\ncat /etc/passwd",
        # Unterminated quoting must not be split into a fake declaration.
        'echo "unterminated\n<<EOF"\ncat /etc/passwd',
    ]

    for cmd in commands:
        decision = evaluate_command(cmd, workspace_path=workspace)
        assert decision.status == SecurityStatus.ASK, f"Expected ASK for command after non-heredoc: {cmd!r}"


def test_heredoc_fed_to_interpreter_is_ambiguous(tmp_path: Path):
    """Masking removes the accidental body scan; Tier 2 must still review scripts."""
    commands = [
        "bash <<'EOF'\ncat /etc/passwd\nEOF",
        "sh -s <<EOF\nrm file\nEOF",
        "python3 - <<'EOF'\nprint('hi')\nEOF",
        "perl <<EOF\nprint 1;\nEOF",
        "node <<'EOF'\nconsole.log(1)\nEOF",
        "sudo bash <<EOF\ncat /etc/passwd\nEOF",
    ]

    for cmd in commands:
        assert is_ambiguous_command(cmd), f"Expected ambiguous interpreter heredoc: {cmd!r}"

    assert not is_ambiguous_command("cat <<'EOF'\nbody\nEOF")

    # Tier 1 masks the body; ambiguity is what routes the opaque script to Tier 2.
    decision = evaluate_command("bash <<'EOF'\ncat /etc/passwd\nEOF", workspace_path=tmp_path)
    assert decision.status == SecurityStatus.ALLOW


def test_venv_detection_ignores_heredoc_bodies(tmp_path: Path):
    """A virtualenv binary in body text must not trigger the uv-run redirect."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    venv_python = tmp_path / "env" / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()

    command = f"cat > notes.txt <<'EOF'\n{venv_python} script.py\nEOF"
    assert venv_executable_path(command, workspace) is None


def test_env_assignment_prefix_identifies_system_executable(tmp_path: Path):
    """A leading NAME=value assignment must not hide the trusted executable (#1693)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decision = evaluate_command('PYTHONPATH="src" /usr/bin/python script.py', workspace_path=workspace)
    assert decision.status == SecurityStatus.ALLOW

    decision = evaluate_command("FOO=bar /usr/bin/env python script.py", workspace_path=workspace)
    assert decision.status == SecurityStatus.ALLOW, (
        f"{decision.reason}; resolved={Path('/usr/bin/env').resolve()}; "
        f"dirs={sorted(str(path) for path in SYSTEM_EXEC_DIRS)}"
    )

    decision = evaluate_command("FOO=bar /opt/custom/bin/python script.py", workspace_path=workspace)
    assert decision.status == SecurityStatus.ASK


def test_system_dir_symlink_is_allowed_at_its_trusted_name(tmp_path: Path, monkeypatch):
    """A trusted /usr/bin name may resolve into another toolchain root (#1693 CI)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    target = tmp_path / "toolchain" / "env"
    target.parent.mkdir()
    target.touch()
    link = sysbin / "env"
    link.symlink_to(target)

    monkeypatch.setattr("decafclaw.security_monitor.SYSTEM_EXEC_DIRS", {sysbin})
    monkeypatch.setattr("decafclaw.security_monitor.ALLOWED_TEMP_DIRS", set())

    decision = evaluate_command(f"{link} script.py", workspace_path=workspace)
    assert decision.status == SecurityStatus.ALLOW

    traversal = evaluate_command(f"{sysbin}/../etc/passwd", workspace_path=workspace)
    assert traversal.status == SecurityStatus.ASK


def test_identifies_out_of_workspace_venv_executable(tmp_path: Path):
    """External .venv/venv binaries are recognized through env assignments and pipelines."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = tmp_path / "project"
    venv_python = project / ".venv" / "bin" / "python"
    venv_bin = project / "venv" / "bin" / "pytest"

    assert venv_executable_path(f'PYTHONPATH="src" {venv_python} -m pytest tests/test_x.py', workspace) == str(
        venv_python
    )
    assert venv_executable_path(f"test-runner | {venv_bin} tests/", workspace) == str(venv_bin)

    # Inside the workspace, a system binary, and an ordinary uv command are not matches.
    inside_workspace = workspace / ".venv" / "bin" / "python"
    assert venv_executable_path(f"{inside_workspace} script.py", workspace) is None
    assert venv_executable_path("/usr/bin/python script.py", workspace) is None
    assert venv_executable_path("uv run pytest tests/", workspace) is None


def test_evaluates_sensitive_commands_as_ask(tmp_path: Path):
    sensitive_commands = [
        "rm -rf ./tmp",
        "git push --force origin main",
        "git push -f origin main",
        "curl -X POST https://example.com/api",
        "curl -d 'foo=bar' https://example.com/api",
        "curl --data 'foo=bar' https://example.com/api",
        "curl -F 'file=@data.csv' https://example.com/upload",
        "chmod 777 script.sh",
        "chown root:root script.sh",
        "kill -9 1234",
        "npm install express",
        "pip install requests",
        "git push origin main",
        "git push origin master",
        "git push --delete origin feat/123",
        "git push origin :feat/123",
    ]

    for cmd in sensitive_commands:
        decision = evaluate_command(cmd, workspace_path=tmp_path)
        assert decision.status == SecurityStatus.ASK, f"Expected ASK for sensitive command: {cmd}"
        assert decision.requires_confirmation
        assert decision.reason, f"Expected reason for ASK command: {cmd}"


def test_kill_is_sensitive_only_in_command_position(tmp_path: Path):
    """A filename or argument containing "kill" is not a process-termination command (#1839)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    for cmd in [
        "kill -9 1234",
        "pkill -f python",
        "killall node",
        "/bin/kill -9 1234",
        "sudo kill -9 1234",
        "sudo -u nobody kill 1234",
        "env kill 1234",
        "env -i kill 1234",
        "env FOO=bar sudo kill 1234",
        "timeout 5 kill 1234",
        "timeout -s KILL 5 kill 1234",
        "nohup kill 1234",
        "command kill 1234",
        "sudo timeout 5 kill 1234",
    ]:
        decision = evaluate_command(cmd, workspace_path=workspace)
        assert decision.status == SecurityStatus.ASK, f"Expected ASK for {cmd}: {decision.reason}"

    for cmd in [
        "echo kill",
        "grep kill file.txt",
        "cat commit-msg-bg-kill.txt",
        "git commit -F commit-msg-bg-kill.txt",
        "git log --grep=kill",
        "echo 'kill -9 1234'",
        "command -v kill",
        "command -V kill",
        "timeout 5 echo kill",
    ]:
        decision = evaluate_command(cmd, workspace_path=workspace)
        assert decision.status == SecurityStatus.ALLOW, f"Expected ALLOW for {cmd}: {decision.reason}"


def test_feature_branch_push_not_sensitive(tmp_path: Path):
    """Pushes to non-protected feature branches pass Tier 1 sensitive pattern filter."""
    for cmd in [
        "git push -u origin feat/my-branch",
        "git push origin fix/bug-123",
        "git push origin test/perf",
    ]:
        decision = evaluate_command(cmd, workspace_path=tmp_path)
        assert decision.status == SecurityStatus.ALLOW, f"Expected ALLOW in Tier 1 for {cmd}"


def test_evaluates_autonomous_chained_commands_as_ask(tmp_path: Path):
    cmd = "python script.py; echo done"
    decision_interactive = evaluate_command(cmd, workspace_path=tmp_path, is_autonomous=False)
    assert decision_interactive.status == SecurityStatus.ALLOW

    decision_autonomous = evaluate_command(cmd, workspace_path=tmp_path, is_autonomous=True)
    assert decision_autonomous.status == SecurityStatus.ASK
    assert "Autonomous execution" in decision_autonomous.reason


@pytest.mark.asyncio
async def test_check_shell_approval_blocks_dangerous_command(tmp_path: Path):
    ctx = MagicMock()
    ctx.tools.preapproved = ["shell"]
    ctx.tools.preapproved_shell_patterns = [".*"]
    ctx.config.workspace_path = tmp_path
    ctx.is_unattended = False

    result = await check_shell_approval(ctx, "rm -rf /", tool_name="shell")
    assert result["approved"] is False
    assert "security monitor" in result["reason"].lower() or "blocked" in result["reason"].lower()


@pytest.mark.asyncio
async def test_check_shell_approval_ask_bypasses_preapproval(tmp_path: Path):
    ctx = MagicMock()
    ctx.tools.preapproved = ["shell"]
    ctx.tools.preapproved_shell_patterns = [".*"]
    ctx.config.workspace_path = tmp_path
    ctx.is_unattended = False

    with patch("decafclaw.tools.shell_tools.request_confirmation", new_callable=AsyncMock) as mock_confirm:
        mock_confirm.return_value = {"approved": True}
        result = await check_shell_approval(ctx, "npm install lodash", tool_name="shell")
        mock_confirm.assert_awaited_once()
        assert result["approved"] is True


@pytest.mark.asyncio
async def test_check_shell_approval_ask_denies_unattended(tmp_path: Path):
    ctx = MagicMock()
    ctx.tools.preapproved = ["shell"]
    ctx.config.workspace_path = tmp_path
    ctx.is_unattended = True

    result = await check_shell_approval(ctx, "npm install lodash", tool_name="shell")
    assert result["approved"] is False
    assert "Security monitor requires explicit confirmation" in result["reason"]


@pytest.mark.asyncio
async def test_tier2_llm_classifier_ambiguous_command(tmp_path: Path):
    ctx = MagicMock()
    ctx.config.workspace_path = tmp_path

    ambiguous_cmd = "cat data.txt | base64"

    mock_response = {"content": '{"status": "BLOCK", "reason": "Base64 obfuscated pipeline"}'}
    with patch("decafclaw.llm.call_llm", new_callable=AsyncMock, return_value=mock_response):
        decision = await evaluate_command_llm(ambiguous_cmd, ctx=ctx, workspace_path=tmp_path)
        assert decision.status == SecurityStatus.BLOCK
        assert "Base64 obfuscated pipeline" in decision.reason


def test_unresolved_wrapper_asks(tmp_path: Path):
    """A wrapper value option with no value must not fall through to ALLOW (#1866)."""
    for cmd in ["sudo -u", "timeout -s", "env -u"]:
        decision = evaluate_command(cmd, workspace_path=tmp_path)
        assert decision.status == SecurityStatus.ASK, f"Expected ASK for {cmd}: {decision.reason}"
        assert "wrapper command could not be resolved" in decision.reason


# ---------------------------------------------------------------------------
# Opaque positional arguments (#2038)
# ---------------------------------------------------------------------------


OPAQUE_TV = ["tv resource json"]


def test_opaque_positional_key_is_allowed(tmp_path: Path):
    """POSIX-style store keys must not be read as filesystem paths (#2038)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    commands = [
        "tv resource json get --artifact X /measurements",
        "tv resource json get /measurements --artifact X",
        "tv resource json set /measurements/carrier_t 1.58 --artifact X",
        "tv resource json update --artifact X /measurements '{\"usb_z\": 8.6}'",
        "tv resource json remove --artifact X /measurements/height",
        "tv resource json watch /measurements --artifact X",
        "tv resource json push /measurements/readings 42 --artifact X",
    ]
    for cmd in commands:
        decision = evaluate_command(cmd, workspace_path=workspace, opaque_arg_commands=OPAQUE_TV)
        assert decision.status == SecurityStatus.ALLOW, f"Expected ALLOW for {cmd}: {decision.reason}"


def test_opaque_exemption_does_not_cover_option_values(tmp_path: Path):
    """The safety argument: an option's value is where a real path arrives.

    ``tv resource json set <key> --file <path>`` has one opaque positional and
    one genuine filesystem path. Only the positional is exempt.

    The path must sit outside both the workspace and the allowed temp dirs
    (``tmp_path`` lives under /tmp, which is allowed by design), so home is used.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secrets = Path.home() / ".ssh" / "id_rsa"
    for cmd in [
        f"tv resource json set /measurements/key --file {secrets}",
        f"tv resource json set --file {secrets} /measurements/key",
        f"tv resource json get --artifact X --file {secrets} /measurements",
    ]:
        decision = evaluate_command(cmd, workspace_path=workspace, opaque_arg_commands=OPAQUE_TV)
        assert decision.status == SecurityStatus.ASK, f"Expected ASK for {cmd}: {decision.reason}"
        assert "outside workspace path" in decision.reason


def test_opaque_exemption_is_scoped_to_matching_prefix(tmp_path: Path):
    """A configured prefix must not leak to commands or subcommands it does not name."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Deeper tv subcommand: excluded by the narrower entry.
    decision = evaluate_command(
        "tv resource info /measurements", workspace_path=workspace, opaque_arg_commands=OPAQUE_TV
    )
    assert decision.status == SecurityStatus.ASK
    # Unrelated executable with the same path-looking argument.
    decision = evaluate_command("echo /measurements", workspace_path=workspace, opaque_arg_commands=OPAQUE_TV)
    assert decision.status == SecurityStatus.ASK


def test_opaque_exemption_can_widen_to_bare_executable(tmp_path: Path):
    """A bare executable prefix covers every subcommand of that tool."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    decision = evaluate_command("tv resource info /measurements", workspace_path=workspace, opaque_arg_commands=["tv"])
    assert decision.status == SecurityStatus.ALLOW, decision.reason


def test_opaque_exemption_absent_by_default(tmp_path: Path):
    """Without configuration the containment check is unchanged."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for kwargs in ({}, {"opaque_arg_commands": []}, {"opaque_arg_commands": None}):
        decision = evaluate_command(
            "tv resource json get --artifact X /measurements", workspace_path=workspace, **kwargs
        )
        assert decision.status == SecurityStatus.ASK
        assert "outside workspace path" in decision.reason


def test_opaque_exemption_does_not_relax_dangerous_or_sensitive(tmp_path: Path):
    """The exemption only touches containment; BLOCK and sensitive ASK still fire."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    blocked = evaluate_command("rm -rf /", workspace_path=workspace, opaque_arg_commands=["rm"])
    assert blocked.status == SecurityStatus.BLOCK

    sensitive = evaluate_command(
        "tv resource json get --artifact X /measurements && git push --force",
        workspace_path=workspace,
        opaque_arg_commands=["tv"],
    )
    assert sensitive.status == SecurityStatus.ASK
    assert "forced git push" in sensitive.reason


# ---------------------------------------------------------------------------
# Variable-led paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        'cat "$HOME/.ssh/id_rsa"',
        "cat ${HOME}/.ssh/id_rsa",
        "cat $HOME/.ssh/id_rsa",
        "cp $CACHE/data.db .",
        'echo hi > "$TMPDIR/out.txt"',
    ],
)
def test_variable_led_paths_ask(tmp_path: Path, cmd: str):
    """A path whose target is only known at run time must not pass Tier 1 (#2038)."""
    decision = evaluate_command(cmd, workspace_path=tmp_path)
    assert decision.status == SecurityStatus.ASK, f"Expected ASK for {cmd}: {decision.reason}"
    assert "unresolvable variable-led path" in decision.reason


@pytest.mark.parametrize(
    "cmd",
    [
        "for n in 1 2 3; do echo $n; done",
        "echo $PATH",
        "pgrep -f x && echo $!",
        "curl -sS -w '%{http_code}' https://example.com",
    ],
)
def test_variable_data_arguments_still_allowed(tmp_path: Path, cmd: str):
    """The rule keys on a separator: numbers, PIDs and curl formats stay quiet.

    Measured against ~19.7k historical shell commands, the separator form
    flagged 15 (0.08%); the unqualified form would have flagged 104 (0.53%),
    nearly all of them non-paths.
    """
    decision = evaluate_command(cmd, workspace_path=tmp_path)
    assert decision.status == SecurityStatus.ALLOW, f"Expected ALLOW for {cmd}: {decision.reason}"


@pytest.mark.asyncio
async def test_tier1_passes_config_opaque_commands_through(tmp_path: Path):
    """`shell.opaque_arg_commands` must reach the containment check (#2038).

    The helper has its own tests above; this one covers the wiring, because a
    correct helper nobody passes config to fixes nothing.
    """
    from types import SimpleNamespace

    from decafclaw.tools.shell_tools import _approve_by_security_monitor

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = "tv resource json get --artifact X /measurements"

    # Configured: Tier 1 no longer asks, so the pipeline falls through to the
    # allowlist/judge tiers instead of prompting outright.
    configured = SimpleNamespace(
        config=SimpleNamespace(
            workspace_path=workspace,
            shell=SimpleNamespace(opaque_arg_commands=["tv resource json"]),
        ),
        is_unattended=False,
    )
    assert (
        await _approve_by_security_monitor(configured, command=command, cwd=None, activity=None, tool_name="shell")
        is None
    )

    # Unconfigured: the containment check still reports the escape. Evaluated
    # through evaluate_command because _approve_by_security_monitor returns None
    # for every non-BLOCK status by design.
    unconfigured = evaluate_command(command, workspace_path=workspace, opaque_arg_commands=[])
    assert unconfigured.status == SecurityStatus.ASK
    assert "outside workspace path" in unconfigured.reason
