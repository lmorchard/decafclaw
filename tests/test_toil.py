"""Tests for the Three Strikes procedural-toil audit (#1531)."""

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from decafclaw import toil
from decafclaw.archive import append_message
from decafclaw.improvement_inbox import read_items

_THIS_DIR = Path(__file__).parent
_SKILL_TOOLS = _THIS_DIR.parent / "contrib" / "skills" / "improvement-inbox" / "tools.py"
_spec = importlib.util.spec_from_file_location("decafclaw_contrib_improvement_inbox_tools_toil", _SKILL_TOOLS)
assert _spec is not None and _spec.loader is not None
skill_tools = importlib.util.module_from_spec(_spec)
sys.modules["decafclaw_contrib_improvement_inbox_tools_toil"] = skill_tools
_spec.loader.exec_module(skill_tools)


def _message(name, **args):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"call_{name}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def _calls(*specs):
    messages = [_message(name, **args) for name, args in specs]
    return toil.extract_tool_calls(messages)


def _write_conversation(config, conv_id, calls, timestamp=None):
    for name, args in calls:
        message = _message(name, **args)
        if timestamp is not None:
            message["timestamp"] = timestamp
        append_message(config, conv_id, message)


def _defensive_calls():
    return [
        ("shell", {"command": "make check > /tmp/out.log 2>&1", "purpose": "run tests"}),
        ("shell", {"command": "tail -n 40 /tmp/out.log", "purpose": "read failures"}),
    ]


def _mine(conversations):
    return toil.mine_sequences({conv_id: _calls(*calls) for conv_id, calls in conversations.items()})


# --- normalization ----------------------------------------------------------


def test_shell_normalization_masks_variable_arguments():
    first = toil._signature("shell", {"command": "make check > /tmp/a.log 2>&1", "purpose": "run"})
    second = toil._signature("shell", {"command": "make check > /tmp/b.log 2>&1", "purpose": "check"})

    assert first == second
    assert first == 'shell {"command":"make check > <tmp> 2>&1"}'
    assert "a.log" not in first


def test_normalize_text_masks_paths_shas_urls_and_numbers():
    text = toil.normalize_text("git show 3cfa0019 -- src/decafclaw/toil.py; curl https://example.com/1")

    assert text == "git show <sha> -- <path>; curl <url>"


def test_tool_path_arguments_normalize_to_a_placeholder():
    calls = _calls(
        ("workspace_write", {"path": "blog/post.md", "content": "secret body"}),
        ("workspace_read", {"path": "blog/post.md"}),
    )

    assert calls[0].signature == 'workspace_write {"path":"<path>"}'
    assert calls[0].path_key == "blog/post.md"
    assert calls[1].path_key == "blog/post.md"


def test_free_text_search_values_collapse_and_subsystem_skips_unknown_tools():
    calls = _calls(
        ("current_time", {}),
        ("vault_search", {"query": "alpha", "days": 1}),
        ("vault_search", {"query": "beta", "days": 3}),
    )

    assert calls[1].signature == calls[2].signature
    assert calls[1].signature == 'vault_search {"days":"<n>","query":"<text>"}'
    assert toil._sequence_subsystem(calls) == "vault"


def test_extract_tool_calls_skips_non_assistant_and_bad_arguments():
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "shell", "arguments": "{not json"}},
                {"function": {"name": "", "arguments": "{}"}},
            ],
        },
    ]

    calls = toil.extract_tool_calls(messages)

    assert len(calls) == 1
    assert calls[0].signature.startswith("shell ")
    assert "{not json" in calls[0].signature


# --- mining -----------------------------------------------------------------


def test_mining_counts_distinct_conversations_not_repeated_occurrences():
    conversations = {
        "conv-a": _defensive_calls() * 2,
        "conv-b": _defensive_calls(),
        "conv-c": _defensive_calls(),
    }

    candidates = _mine(conversations)

    assert len(candidates) == 1
    assert candidates[0].strikes == 3
    assert candidates[0].conversation_ids == ("conv-a", "conv-b", "conv-c")


def test_min_strikes_filters_candidates_below_threshold():
    conversations = {"conv-a": _defensive_calls(), "conv-b": _defensive_calls()}

    assert toil.mine_sequences({k: _calls(*v) for k, v in conversations.items()}, min_strikes=3) == []
    assert len(toil.mine_sequences({k: _calls(*v) for k, v in conversations.items()}, min_strikes=2)) == 1


def test_subsequence_dominance_keeps_only_the_longest_pattern():
    calls = [
        ("workspace_write", {"path": "notes/a.md", "content": "a"}),
        ("workspace_read", {"path": "notes/a.md"}),
        ("workspace_write", {"path": "notes/b.md", "content": "b"}),
        ("workspace_read", {"path": "notes/b.md"}),
    ]
    candidates = _mine({"conv-a": calls, "conv-b": calls, "conv-c": calls})

    assert [(candidate.strikes, len(candidate.sequence)) for candidate in candidates] == [(3, 4)]


def test_defensive_shell_sequence_maps_to_template_a():
    candidates = _mine({"conv-a": _defensive_calls(), "conv-b": _defensive_calls(), "conv-c": _defensive_calls()})

    candidate = candidates[0]
    assert candidate.kind == "defensive_shell"
    assert candidate.template_id == "A"
    assert candidate.subsystem == "shell"
    assert candidate.severity == "medium"
    assert "Template A" in candidate.suggested_improvement


def test_read_after_write_requires_the_same_path():
    same_path = [
        ("workspace_write", {"path": "blog/post.md", "content": "v1"}),
        ("workspace_read", {"path": "blog/post.md"}),
    ]
    different_path = [
        ("workspace_write", {"path": "blog/post.md", "content": "v1"}),
        ("workspace_read", {"path": "blog/other.md"}),
    ]

    candidates = _mine({"conv-a": same_path, "conv-b": same_path, "conv-c": same_path})
    assert len(candidates) == 1
    assert candidates[0].kind == "read_after_write"
    assert candidates[0].template_id == "C"
    assert candidates[0].subsystem == "workspace"

    assert _mine({"conv-a": different_path, "conv-b": different_path, "conv-c": different_path}) == []


def test_mixed_paths_only_count_matching_conversations():
    same_path = [
        ("workspace_write", {"path": "blog/post.md", "content": "v1"}),
        ("workspace_read", {"path": "blog/post.md"}),
    ]
    different_path = [
        ("workspace_write", {"path": "blog/post.md", "content": "v1"}),
        ("workspace_read", {"path": "blog/other.md"}),
    ]

    candidates = _mine({"conv-a": same_path, "conv-b": same_path, "conv-c": same_path, "conv-d": different_path})

    assert len(candidates) == 1
    assert candidates[0].conversation_ids == ("conv-a", "conv-b", "conv-c")


def test_discovery_ceremony_maps_to_template_b():
    calls = [
        ("workspace_list", {}),
        ("workspace_glob", {"pattern": "**/*.py"}),
    ]

    candidates = _mine({"conv-a": calls, "conv-b": calls, "conv-c": calls})

    assert len(candidates) == 1
    assert candidates[0].kind == "discovery_ceremony"
    assert candidates[0].template_id == "B"
    assert candidates[0].subsystem == "workspace"


def test_discovery_shell_commands_count_toward_ceremony():
    calls = [("shell", {"command": "ls src"}), ("shell", {"command": "uv sync"})]
    assert toil.is_discovery_shell("cd repo && uv sync")
    assert not toil.is_discovery_shell("make check")
    assert toil.classify_sequence(_calls(*calls)) == "discovery_ceremony"


# --- archive scan -----------------------------------------------------------


def test_scan_conversations_respects_lookback_window(config):
    recent = datetime.now() - timedelta(days=1)
    old = datetime.now() - timedelta(days=30)
    for index in range(3):
        _write_conversation(config, f"recent-{index}", _defensive_calls(), timestamp=recent.isoformat())
        _write_conversation(config, f"old-{index}", _defensive_calls(), timestamp=old.isoformat())

    recent_report = toil.audit_toil(config, days=7)
    all_report = toil.audit_toil(config, days=None)

    assert recent_report.scanned == 3
    assert recent_report.candidates[0].strikes == 3
    assert all_report.scanned == 6
    assert all_report.candidates[0].strikes == 6


def test_audit_reports_every_canonical_signature_once(config):
    for index in range(3):
        conv_id = f"conv-{index}"
        _write_conversation(config, conv_id, _defensive_calls())
        _write_conversation(
            config,
            conv_id,
            [
                ("workspace_write", {"path": f"notes/{index}.md", "content": "x"}),
                ("workspace_read", {"path": f"notes/{index}.md"}),
            ],
        )
        _write_conversation(
            config,
            conv_id,
            [("workspace_list", {}), ("workspace_glob", {"pattern": "**/*.md"})],
        )

    report = toil.audit_toil(config, days=None)

    assert {candidate.kind for candidate in report.candidates} == {
        "defensive_shell",
        "read_after_write",
        "discovery_ceremony",
    }


# --- export and tool --------------------------------------------------------


def test_export_candidates_buffers_strike_items_and_skips_duplicates(config):
    candidates = _mine({"conv-a": _defensive_calls(), "conv-b": _defensive_calls(), "conv-c": _defensive_calls()})

    exported, skipped = toil.export_candidates(config, candidates)
    again, skipped_again = toil.export_candidates(config, candidates)

    assert len(exported) == 1
    assert skipped == []
    assert again == []
    assert skipped_again == candidates
    items = read_items(config)
    assert len(items) == 1
    item = items[0]
    assert item["trigger"] == "toil_strike_3"
    assert item["status"] == "pending"
    assert item["subsystem"] == "shell"
    assert item["conversation_id"] == "conv-a, conv-b, conv-c"
    assert item["strike_count"] == 3
    assert item["toil_sequence"] == list(candidates[0].sequence)
    assert "conv-a, conv-b, conv-c" in item["observed_friction"]


def test_cli_json_output_scans_archives(config, monkeypatch, capsys):
    for index in range(3):
        _write_conversation(config, f"conv-{index}", _defensive_calls())
    monkeypatch.setattr("decafclaw.config.load_config", lambda: config)

    assert toil.main(["audit", "--days", "0", "--json", "--export"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["scanned_conversations"] == 3
    assert payload["min_strikes"] == 3
    assert payload["candidates"][0]["kind"] == "defensive_shell"
    assert len(payload["exported"]) == 1
    assert read_items(config)[0]["trigger"] == "toil_strike_3"


def test_skill_toil_audit_tool_exports_candidates(ctx):
    for index in range(3):
        _write_conversation(ctx.config, f"conv-{index}", _defensive_calls())

    result = skill_tools.toil_audit(ctx, days=0, min_strikes=3, export=True)

    assert "Buffered 1 item(s)" in result.text
    assert result.data["candidates"][0]["kind"] == "defensive_shell"
    assert result.data["exported"]
    assert read_items(ctx.config)[0]["trigger"] == "toil_strike_3"
