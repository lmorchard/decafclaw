"""Conversation activity derivation (#1641)."""

import json

from decafclaw.activity import UNKNOWN_ACTIVITY, derive_activity, derive_domains


def _shell_call(call_id: str, command: str, cwd: str | None = None) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "shell", "arguments": json.dumps({"command": command, "cwd": cwd})},
    }


def _history(*calls: dict) -> list[dict]:
    """Build one assistant message per shell call, oldest first."""
    return [{"role": "assistant", "content": None, "tool_calls": [call]} for call in calls]


def _ctx(config):
    from decafclaw.context import Context
    from decafclaw.events import EventBus

    ctx = Context(config=config, event_bus=EventBus())
    ctx.conv_id = "activity-test"
    return ctx


def _workspace(config, relative: str = "") -> str:
    path = config.workspace_path / relative
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


# -- domain derivation --------------------------------------------------------


def test_domain_from_cwd(config):
    workspace = config.workspace_path
    assert derive_domains("git status", _workspace(config, "decafclaw/src"), workspace) == ("decafclaw",)


def test_worktree_normalizes_to_parent_repo(config):
    workspace = config.workspace_path
    worktree = _workspace(config, "decafclaw/.claude/worktrees/feat-x/src")
    assert derive_domains("git status", worktree, workspace) == ("decafclaw",)


def test_workspace_root_does_not_match_agent_home(config):
    """The workspace path contains `decafclaw`; a bare `ls` there is unknown."""
    workspace = config.workspace_path
    _workspace(config)
    assert derive_domains("ls -la", str(workspace), workspace) == ()


def test_domain_falls_back_to_command_text(config):
    workspace = config.workspace_path
    _workspace(config)
    assert derive_domains("ls lmorchard-homelab", str(workspace), workspace) == ("homelab",)


def test_cwd_names_the_primary_domain(config):
    workspace = config.workspace_path
    cwd = _workspace(config, "blog-ssg")
    domains = derive_domains("ls ../lmorchard-homelab", cwd, workspace)
    assert domains[0] == "blog"
    assert "homelab" in domains


def test_path_outside_workspace_has_no_domain(config):
    workspace = config.workspace_path
    _workspace(config)
    assert derive_domains("ls", "/tmp/somewhere-else", workspace) == ()


# -- establishment ------------------------------------------------------------


def test_unknown_activity_reason_is_readable(config):
    ctx = _ctx(config)
    workspace = str(config.workspace_path)
    _workspace(config)
    ctx.history = []
    activity = derive_activity(ctx, "touch marker.txt", workspace)
    assert "first shell command in this conversation" in activity.reason()


def test_worktrees_normalization_needs_a_path_segment(config):
    """`myworktrees/` is not a git worktree directory; the repo name survives."""
    workspace = config.workspace_path
    cwd = _workspace(config, "myworktrees/decafclaw")
    assert derive_domains("ls", cwd, workspace) == ("decafclaw",)


def test_first_command_is_not_established(config):
    ctx = _ctx(config)
    ctx.history = []
    activity = derive_activity(ctx, "git status", _workspace(config, "decafclaw"))
    assert activity.domain == "decafclaw"
    assert activity.established is False
    assert "first `decafclaw` command" in activity.reason()


def test_earlier_same_domain_establishes_activity(config):
    ctx = _ctx(config)
    cwd = _workspace(config, "decafclaw")
    ctx.history = _history(_shell_call("call-1", "git status", cwd))
    activity = derive_activity(ctx, "git log", cwd)
    assert activity.domain == "decafclaw"
    assert activity.established is True
    assert "established" in activity.reason()


def test_drift_to_new_domain_is_not_established(config):
    ctx = _ctx(config)
    blog = _workspace(config, "blog-ssg")
    homelab = _workspace(config, "lmorchard-homelab")
    ctx.history = _history(_shell_call("call-1", "make build", blog))
    activity = derive_activity(ctx, "make deploy", homelab)
    assert activity.domain == "homelab"
    assert activity.established is False
    assert activity.prior_domains == ("blog",)
    assert "recent activity: `blog`" in activity.reason()


def test_command_touching_new_domain_is_not_established(config):
    ctx = _ctx(config)
    decafclaw = _workspace(config, "decafclaw")
    ctx.history = _history(_shell_call("call-1", "git status", decafclaw))
    activity = derive_activity(ctx, "ls lmorchard-homelab", decafclaw)
    assert activity.domain == "decafclaw"
    assert activity.established is False


def test_unknown_activity_becomes_established_after_one_command(config):
    ctx = _ctx(config)
    workspace = str(config.workspace_path)
    _workspace(config)
    ctx.history = _history(_shell_call("call-1", "ls -la", workspace))
    activity = derive_activity(ctx, "ls -l", workspace)
    assert activity.domain == UNKNOWN_ACTIVITY
    assert activity.established is True


def test_current_call_is_not_its_own_precedent_by_id(config):
    ctx = _ctx(config)
    cwd = _workspace(config, "decafclaw")
    ctx.history = _history(_shell_call("call-current", "git status", cwd))
    ctx.tools.current_call_id = "call-current"
    activity = derive_activity(ctx, "git status", cwd)
    assert activity.established is False


def test_current_call_is_not_its_own_precedent_by_fingerprint(config):
    ctx = _ctx(config)
    cwd = _workspace(config, "decafclaw")
    ctx.history = _history(
        _shell_call("call-old", "git log", cwd),
        _shell_call("call-current", "git status", cwd),
    )
    ctx.tools.current_call_id = ""
    activity = derive_activity(ctx, "git status", cwd)
    assert activity.established is True
    assert ctx.history  # the current entry is skipped, the older one still counts


def test_window_drops_stale_domains(config):
    ctx = _ctx(config)
    decafclaw = _workspace(config, "decafclaw")
    blog = _workspace(config, "blog-ssg")
    calls = [_shell_call("call-0", "git status", decafclaw)]
    calls += [_shell_call(f"call-{i}", "make build", blog) for i in range(1, 51)]
    ctx.history = _history(*calls)
    ctx.config.shell.activity_window = 50
    activity = derive_activity(ctx, "git log", decafclaw)
    assert activity.established is False


def test_compaction_reset_drops_direction(config):
    """A summarized history carries no shell calls, so direction is re-earned."""
    ctx = _ctx(config)
    cwd = _workspace(config, "decafclaw")
    ctx.history = [{"role": "user", "content": "summary of earlier work"}]
    activity = derive_activity(ctx, "git status", cwd)
    assert activity.established is False


def test_missing_history_is_not_established(config):
    ctx = _ctx(config)
    ctx.history = None
    activity = derive_activity(ctx, "git status", _workspace(config, "decafclaw"))
    assert activity.established is False


# -- external domain vocabulary (#1641 review) --------------------------------


def _write_domains(config, *lines: str):
    path = config.agent_path / "activity_domains.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_agent_domain_file_replaces_bundled_vocabulary(config):
    _write_domains(config, '{"domain": "garden", "pattern": "garden"}')
    ctx = _ctx(config)
    garden = _workspace(config, "garden")
    assert derive_activity(ctx, "touch seed", garden).domain == "garden"
    # The file is authoritative: the bundled decafclaw domain is gone.
    decafclaw = _workspace(config, "decafclaw")
    assert derive_activity(ctx, "touch x", decafclaw).domain == UNKNOWN_ACTIVITY


def test_malformed_domain_lines_are_skipped(config):
    _write_domains(
        config,
        "{ not json",
        '{"domain": "garden", "pattern": "garden"}',
        '{"domain": "", "pattern": "x"}',
        '{"domain": "bad", "pattern": "[unclosed"}',
    )
    ctx = _ctx(config)
    assert derive_activity(ctx, "touch seed", _workspace(config, "garden")).domain == "garden"


def test_unusable_domain_file_falls_back_to_bundled(config):
    _write_domains(config, "{ not json", '{"domain": "x", "pattern": "[bad"}')
    ctx = _ctx(config)
    activity = derive_activity(ctx, "touch x", _workspace(config, "decafclaw"))
    assert activity.domain == "decafclaw"


def test_domain_file_is_reread_each_call(config):
    path = _write_domains(config, '{"domain": "garden", "pattern": "garden"}')
    ctx = _ctx(config)
    garden = _workspace(config, "garden")
    assert derive_activity(ctx, "touch seed", garden).domain == "garden"

    path.write_text('{"domain": "woodwork", "pattern": "garden"}\n', encoding="utf-8")
    assert derive_activity(ctx, "touch seed", garden).domain == "woodwork"


def test_ensure_activity_domains_file_seeds_once(config):
    from decafclaw.activity import ensure_activity_domains_file

    assert ensure_activity_domains_file(config) is True
    path = config.agent_path / "activity_domains.jsonl"
    seeded = path.read_text()
    assert "homelab" in seeded

    path.write_text('{"domain": "custom", "pattern": "custom"}\n', encoding="utf-8")
    assert ensure_activity_domains_file(config) is False
    assert path.read_text() == '{"domain": "custom", "pattern": "custom"}\n'
