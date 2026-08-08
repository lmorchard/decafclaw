"""Integration tests for the tool-choice eval CLI (#303)."""

from __future__ import annotations

from decafclaw.eval.tool_choice.__main__ import main


def _write_case(tmp_path, name, expected, near_miss):
    yaml = (
        "- name: " + name + "\n"
        "  scenario: x\n"
        "  expected: " + expected + "\n"
        "  near_miss: [" + ", ".join(near_miss) + "]\n"
    )
    f = tmp_path / "cases.yaml"
    f.write_text(yaml)
    return f


def _patch_runtime(monkeypatch, *, picked_tool: str):
    """Stub config + provider init + LLM so the CLI can run end-to-end
    without network access."""
    from decafclaw.config_types import ModelConfig, ProviderConfig

    class StubConfig:
        agent_path = None
        providers = {"vertex": ProviderConfig(type="vertex", project="test")}
        model_configs = {
            "fake-model": ModelConfig(provider="vertex", model="fake"),
        }
        default_model = "fake-model"

    monkeypatch.setattr(
        "decafclaw.eval.tool_choice.__main__.load_config", lambda: StubConfig(),
    )
    monkeypatch.setattr(
        "decafclaw.eval.tool_choice.__main__.init_providers", lambda config: None,
    )
    # Skip the real loadout assembly — we don't need it for the integration
    # test, and we want to avoid the heavy skill discovery here.
    monkeypatch.setattr(
        "decafclaw.eval.tool_choice.__main__.build_full_tool_loadout",
        lambda config, *, include_mcp=False: [
            {"function": {"name": "vault_search", "description": "x"}},
            {"function": {"name": "conversation_search", "description": "x"}},
        ],
    )
    # Skip system prompt loading — needed by run_case but unused for the
    # decision under test.
    monkeypatch.setattr(
        "decafclaw.eval.tool_choice.runner.load_system_prompt",
        lambda config: ("system prompt", []),
    )

    async def fake_call_llm(config, messages, tools=None, model_name=None):
        return {
            "content": None,
            "tool_calls": [
                {"id": "c0", "function": {"name": picked_tool, "arguments": "{}"}}
            ],
            "role": "assistant",
            "usage": {},
        }
    monkeypatch.setattr(
        "decafclaw.eval.tool_choice.runner.call_llm", fake_call_llm,
    )


def test_cli_passing_case_exits_zero(tmp_path, monkeypatch, capsys):
    case_file = _write_case(tmp_path, "case-a", "vault_search", ["conversation_search"])
    _patch_runtime(monkeypatch, picked_tool="vault_search")

    rc = main([str(case_file)])
    assert rc == 0

    out = capsys.readouterr().out
    assert "PASS  case-a" in out
    assert "Summary: 1/1 passed (100%)" in out
    assert "Pair overlap" in out


def test_cli_failing_case_exits_one(tmp_path, monkeypatch, capsys):
    case_file = _write_case(tmp_path, "case-a", "vault_search", ["conversation_search"])
    _patch_runtime(monkeypatch, picked_tool="conversation_search")

    rc = main([str(case_file)])
    assert rc == 1

    out = capsys.readouterr().out
    assert "FAIL  case-a" in out
    assert "picked conversation_search" in out
    assert "tighten" in out  # 100% swap → marker fires


def test_cli_sweep_groups_per_model(tmp_path, monkeypatch, capsys):
    case_file = _write_case(tmp_path, "case-a", "vault_search", ["conversation_search"])
    _patch_runtime(monkeypatch, picked_tool="vault_search")

    rc = main([str(case_file), "--models", "alpha,beta"])
    assert rc == 0

    out = capsys.readouterr().out
    # Two grouped sections — one header per model.
    assert "=== alpha ===" in out
    assert "=== beta ===" in out
    # Each model produces its own summary line.
    assert out.count("Summary: 1/1 passed") == 2


def test_cli_matrix_flag_prints_confusion_matrix(tmp_path, monkeypatch, capsys):
    case_file = _write_case(tmp_path, "case-a", "vault_search", ["conversation_search"])
    _patch_runtime(monkeypatch, picked_tool="conversation_search")

    main([str(case_file), "--matrix"])

    out = capsys.readouterr().out
    assert "Confusion matrix" in out


def test_cli_filter_runs_only_matching_case(tmp_path, monkeypatch, capsys):
    """CRITERION 3: Only matching cases SHALL be run."""
    yaml = (
        "- name: alpha\n  scenario: x\n  expected: t\n  near_miss: [u]\n"
        "- name: beta\n  scenario: x\n  expected: t\n  near_miss: [u]\n"
    )
    case_file = tmp_path / "cases.yaml"
    case_file.write_text(yaml)
    _patch_runtime(monkeypatch, picked_tool="t")

    rc = main([str(case_file), "--filter", "alph"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "alpha" in out
    assert "beta" not in out
    assert "1/1 passed" in out


def test_cli_filter_matching_nothing_exits_nonzero(tmp_path, monkeypatch, capsys):
    """CRITERION 3: A filter matching nothing SHALL exit non-zero with a message naming the filter."""
    case_file = _write_case(tmp_path, "case-a", "vault_search", ["conversation_search"])
    _patch_runtime(monkeypatch, picked_tool="vault_search")

    rc = main([str(case_file), "--filter", "nope"])

    assert rc != 0
    out = capsys.readouterr().out
    assert "Filter 'nope' matched no cases." in out


def test_cli_invalid_reps_exits_nonzero(tmp_path, monkeypatch, capsys):
    """Test that --reps 0 fails fast."""
    case_file = _write_case(tmp_path, "case-a", "vault_search", ["conversation_search"])
    _patch_runtime(monkeypatch, picked_tool="vault_search")

    rc = main([str(case_file), "--reps", "0"])

    assert rc != 0
    out = capsys.readouterr().out
    assert "--reps must be >= 1" in out
