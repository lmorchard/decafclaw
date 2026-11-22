"""Golden-corpus replay and denial gate (#1661).

Fixtures are synthetic archives: the operator's live corpus is not in the
repository (and its commands carry local paths). These tests pin the replay
semantics and, most importantly, that the gate fails when a real human denial
would now be silently allowed.
"""

import json
import os
from pathlib import Path

import pytest

from decafclaw.approval_replay import (
    ReplaySettings,
    check_denials,
    iter_archives,
    replay_archives,
)


def _write_archive(root: Path, conv_id: str, messages: list[dict]) -> Path:
    directory = root / "conversations" / conv_id
    directory.mkdir(parents=True)
    path = directory / "archive.jsonl"
    path.write_text("\n".join(json.dumps(message) for message in messages) + "\n", encoding="utf-8")
    return path


def _shell_call(call_id: str, command: str, cwd: str | None = None) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "shell",
                    "arguments": json.dumps({"command": command, "cwd": cwd}),
                },
            }
        ],
    }


def _confirmation(call_id: str, confirmation_id: str, command: str, approved: bool | None = None) -> list[dict]:
    messages = [
        {
            "role": "confirmation_request",
            "confirmation_id": confirmation_id,
            "action_type": "run_shell_command",
            "action_data": {"command": command},
            "tool_call_id": call_id,
        }
    ]
    if approved is not None:
        messages.append({"role": "confirmation_response", "confirmation_id": confirmation_id, "approved": approved})
    return messages


def _settings(root: Path, **overrides) -> ReplaySettings:
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    values = {
        "workspace": workspace,
        "agent_path": root,
        "activity_window": 50,
        "read_only_enabled": True,
        "aux_approval_enabled": True,
        "allow_patterns": (),
    }
    values.update(overrides)
    return ReplaySettings(**values)


def test_read_only_command_is_silently_allowed_and_new_activity_prompts(tmp_path):
    root = tmp_path / "agent"
    (root / "workspace" / "lmorchard-homelab").mkdir(parents=True)
    (root / "workspace" / "blog.lmorchard.com").mkdir()
    _write_archive(
        root,
        "conv-1",
        [_shell_call("call-1", "ls -la", cwd="lmorchard-homelab")]
        + _confirmation("call-1", "c1", "ls -la", approved=False)
        + [_shell_call("call-2", "touch new-file.txt", cwd="blog.lmorchard.com")]
        + _confirmation("call-2", "c2", "touch new-file.txt", approved=False),
    )
    report = replay_archives(root, _settings(root), all_=True)

    assert report.commands == 2
    outcomes = [(result.command, result.tier, result.decision) for result in report.results]
    assert outcomes == [
        ("ls -la", "read_only", "allow"),
        ("touch new-file.txt", "activity_new", "prompt"),
    ]
    assert report.baseline_prompts == 2
    assert report.baseline_denied == 2
    assert [result.command for result in check_denials(report)] == ["ls -la"]


def test_established_activity_defers_to_the_judge(tmp_path):
    root = tmp_path / "agent"
    _write_archive(
        root,
        "conv-2",
        [
            _shell_call("call-1", "touch one.txt"),
            _shell_call("call-2", "touch two.txt"),
        ],
    )
    report = replay_archives(root, _settings(root), all_=True)
    tiers = [result.tier for result in report.results]
    decisions = [result.decision for result in report.results]
    assert tiers == ["activity_new", "aux_judge"]
    assert decisions == ["prompt", "judge"]


def test_unattended_new_activity_defers_instead_of_prompting(tmp_path):
    root = tmp_path / "agent"
    _write_archive(
        root,
        "schedule-demo-20260101",
        [_shell_call("call-1", "touch new-file.txt")],
    )
    report = replay_archives(root, _settings(root), all_=True)
    assert report.results[0].tier == "activity_new"
    assert report.results[0].decision == "judge"
    assert report.summary()["replay_prompts"] == 0


def test_denials_pair_by_tool_call_id_not_message_order(tmp_path):
    """A judge record between request and response must not shift the pairing."""
    root = tmp_path / "agent"
    _write_archive(
        root,
        "conv-3",
        [_shell_call("call-1", "touch mutate.txt")]
        + [
            {
                "role": "shell_approval",
                "tool": "shell auto-approval: ALLOWED",
                "content": json.dumps({"command": "touch mutate.txt"}),
            }
        ]
        + _confirmation("call-1", "c1", "touch mutate.txt", approved=False),
    )
    report = replay_archives(root, _settings(root), all_=True)
    assert report.results[0].human_approved is False
    assert report.results[0].historical_judge == "ALLOWED"
    assert report.baseline_denied == 1


def test_sampling_defaults_to_a_bounded_set(tmp_path):
    root = tmp_path / "agent"
    for index in range(5):
        _write_archive(root, f"conv-{index}", [_shell_call(f"call-{index}", "ls -la")])
    assert len(iter_archives(root)) == 5
    report = replay_archives(root, _settings(root), limit=2)
    assert report.archives_total == 5
    assert report.archives_replayed == 2


def test_tier1_block_prevents_execution_and_is_not_a_denial_regression(tmp_path):
    root = tmp_path / "agent"
    _write_archive(
        root,
        "conv-4",
        [_shell_call("call-1", "rm -rf /")] + _confirmation("call-1", "c1", "rm -rf /", approved=False),
    )
    report = replay_archives(root, _settings(root), all_=True)
    assert report.results[0].tier == "tier1_block"
    assert report.results[0].decision == "block"
    assert check_denials(report) == []
    assert report.denial_results[0].decision == "block"


@pytest.mark.skipif(
    not os.environ.get("DECAFCLAW_REPLAY_ROOT"),
    reason="set DECAFCLAW_REPLAY_ROOT to a live archive workspace to run the real denial gate",
)
def test_configured_corpus_denials_are_not_silently_allowed():
    """The acceptance gate for the real corpus (#1641/#1661).

    Point ``DECAFCLAW_REPLAY_ROOT`` at the live workspace and, when the corpus
    was recorded under an older path, ``DECAFCLAW_REPLAY_WORKSPACE`` at the
    workspace the commands actually ran in.
    """
    root = Path(os.environ["DECAFCLAW_REPLAY_ROOT"])
    workspace = Path(os.environ.get("DECAFCLAW_REPLAY_WORKSPACE", str(root))).resolve()
    settings = ReplaySettings(
        workspace=workspace,
        agent_path=workspace.parent,
        activity_window=50,
        read_only_enabled=True,
        aux_approval_enabled=True,
        allow_patterns=(),
    )
    report = replay_archives(root, settings, all_=True)
    assert report.denial_results, "no paired human denials found — check the replay root"
    assert check_denials(report) == []
