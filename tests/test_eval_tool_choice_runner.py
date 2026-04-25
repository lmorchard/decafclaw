"""Tests for the tool-choice eval runner (#303)."""

from __future__ import annotations

from pathlib import Path

import pytest

from decafclaw.eval.tool_choice.case import Case, load_cases
from decafclaw.eval.tool_choice.runner import (
    NO_TOOL,
    CaseResult,
    run_case,
    run_cases,
)

# -- Case loader ---------------------------------------------------------------


class TestLoadCases:
    def test_parses_two_cases(self, tmp_path):
        yaml_file = tmp_path / "cases.yaml"
        yaml_file.write_text("""
- name: case-a
  scenario: pick A
  expected: tool_a
  near_miss: [tool_b]
  notes: case A explanation
- name: case-b
  scenario: pick B
  expected: tool_b
  near_miss: [tool_a, tool_c]
""")
        cases = load_cases(yaml_file)
        assert len(cases) == 2
        assert cases[0].name == "case-a"
        assert cases[0].expected == "tool_a"
        assert cases[0].near_miss == ["tool_b"]
        assert cases[0].notes == "case A explanation"
        assert cases[1].near_miss == ["tool_a", "tool_c"]
        assert cases[1].notes == ""

    def test_directory_loads_all_yamls(self, tmp_path):
        (tmp_path / "a.yaml").write_text(
            "- {name: a, scenario: x, expected: t, near_miss: [u]}\n"
        )
        (tmp_path / "b.yaml").write_text(
            "- {name: b, scenario: y, expected: t, near_miss: [u]}\n"
        )
        cases = load_cases(tmp_path)
        names = sorted(c.name for c in cases)
        assert names == ["a", "b"]

    def test_rejects_missing_required_field(self, tmp_path):
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text(
            "- {name: missing-expected, scenario: x, near_miss: [u]}\n"
        )
        with pytest.raises(ValueError, match="missing required field"):
            load_cases(yaml_file)

    def test_rejects_empty_near_miss(self, tmp_path):
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text(
            "- {name: empty-near-miss, scenario: x, expected: t, near_miss: []}\n"
        )
        with pytest.raises(ValueError, match="missing required field"):
            load_cases(yaml_file)

    def test_rejects_non_list_top_level(self, tmp_path):
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("name: not-a-list\nscenario: x\n")
        with pytest.raises(ValueError, match="must be a list"):
            load_cases(yaml_file)


# -- Runner with faked LLM -----------------------------------------------------


def _make_response(tool_names):
    """Shape a faked provider.complete() response with the named tool calls."""
    if tool_names is None:
        return {"content": "no tools needed", "tool_calls": None, "role": "assistant", "usage": {}}
    return {
        "content": None,
        "tool_calls": [
            {"id": f"c{i}", "function": {"name": n, "arguments": "{}"}}
            for i, n in enumerate(tool_names)
        ],
        "role": "assistant",
        "usage": {},
    }


@pytest.fixture
def patched_call_llm(monkeypatch):
    """Replace decafclaw.eval.tool_choice.runner.call_llm with a fake.

    Tests set ``fake.response = ...`` to control the returned dict.
    The fake also records each call's args for additional asserts.
    """
    class Fake:
        def __init__(self):
            self.response = _make_response(["vault_search"])
            self.calls: list[dict] = []

        async def __call__(self, config, messages, tools=None, model_name=None):
            self.calls.append({
                "messages": messages,
                "tools": tools,
                "model_name": model_name,
            })
            return self.response

    fake = Fake()
    monkeypatch.setattr("decafclaw.eval.tool_choice.runner.call_llm", fake)
    return fake


@pytest.mark.asyncio
async def test_run_case_passes_when_expected_picked(config, patched_call_llm):
    case = Case(
        name="t", scenario="find decisions", expected="vault_search",
        near_miss=["conversation_search"],
    )
    patched_call_llm.response = _make_response(["vault_search"])

    result = await run_case(
        case, model="m", config=config, tool_loadout=[{"function": {"name": "vault_search"}}],
    )
    assert result.picked == "vault_search"
    assert result.all_picks == ["vault_search"]
    assert result.passed is True


@pytest.mark.asyncio
async def test_run_case_fails_with_picked_neighbor(config, patched_call_llm):
    case = Case(
        name="t", scenario="...", expected="vault_search",
        near_miss=["conversation_search"],
    )
    patched_call_llm.response = _make_response(["conversation_search"])

    result = await run_case(case, model="m", config=config, tool_loadout=[])
    assert result.picked == "conversation_search"
    assert result.passed is False


@pytest.mark.asyncio
async def test_run_case_no_tool_response(config, patched_call_llm):
    """Empty tool_calls → picked is the NO_TOOL sentinel; passed false
    unless the case explicitly expected NO_TOOL."""
    case = Case(name="t", scenario="...", expected="vault_search", near_miss=["x"])
    patched_call_llm.response = _make_response(None)

    result = await run_case(case, model="m", config=config, tool_loadout=[])
    assert result.picked == NO_TOOL
    assert result.all_picks == []
    assert result.passed is False


@pytest.mark.asyncio
async def test_run_case_explicit_no_tool_pass(config, patched_call_llm):
    """A case can declare NO_TOOL as the expected outcome (the
    'conversational reply, no tool needed' negative-control case)."""
    case = Case(name="t", scenario="hi", expected=NO_TOOL, near_miss=["vault_search"])
    patched_call_llm.response = _make_response(None)

    result = await run_case(case, model="m", config=config, tool_loadout=[])
    assert result.picked == NO_TOOL
    assert result.passed is True


@pytest.mark.asyncio
async def test_run_case_multi_call_records_first(config, patched_call_llm):
    """Multiple parallel tool calls: ``picked`` is the first; full
    list is on ``all_picks``."""
    case = Case(
        name="t", scenario="...", expected="vault_search",
        near_miss=["workspace_read"],
    )
    patched_call_llm.response = _make_response(["vault_search", "workspace_read"])

    result = await run_case(case, model="m", config=config, tool_loadout=[])
    assert result.picked == "vault_search"
    assert result.all_picks == ["vault_search", "workspace_read"]
    assert result.passed is True


@pytest.mark.asyncio
async def test_run_case_passes_through_loadout_and_model(config, patched_call_llm):
    """The tool_loadout and model_name reach the LLM call unchanged."""
    case = Case(name="t", scenario="x", expected="t", near_miss=["u"])
    loadout = [{"function": {"name": "vault_search", "description": "..."}}]
    patched_call_llm.response = _make_response(["t"])

    await run_case(case, model="my-model", config=config, tool_loadout=loadout)

    assert len(patched_call_llm.calls) == 1
    sent = patched_call_llm.calls[0]
    assert sent["model_name"] == "my-model"
    assert sent["tools"] == loadout
    # System + user messages
    assert len(sent["messages"]) == 2
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][1]["role"] == "user"
    assert sent["messages"][1]["content"] == "x"


@pytest.mark.asyncio
async def test_run_cases_concurrency(config, patched_call_llm):
    """run_cases returns one result per case, in order."""
    cases = [
        Case(name=f"c{i}", scenario=str(i), expected="t", near_miss=["u"])
        for i in range(5)
    ]
    patched_call_llm.response = _make_response(["t"])

    results = await run_cases(
        cases, model="m", config=config, tool_loadout=[], concurrency=2,
    )
    assert isinstance(results, list)
    assert len(results) == 5
    assert [r.case.name for r in results] == [f"c{i}" for i in range(5)]
    assert all(r.passed for r in results)


@pytest.mark.asyncio
async def test_run_case_swallows_llm_error(config, monkeypatch):
    """A provider error returns a CaseResult with NO_TOOL + passed=False
    instead of bubbling. The eval shouldn't abort halfway through a
    batch because one case errored."""
    async def boom(config, messages, tools=None, model_name=None):
        raise RuntimeError("network exploded")

    monkeypatch.setattr("decafclaw.eval.tool_choice.runner.call_llm", boom)

    case = Case(name="t", scenario="x", expected="t", near_miss=["u"])
    result = await run_case(case, model="m", config=config, tool_loadout=[])
    assert isinstance(result, CaseResult)
    assert result.picked == NO_TOOL
    assert result.passed is False


# Quieten import-unused warning on Path
_ = Path
