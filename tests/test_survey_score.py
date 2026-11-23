"""Tests for the Stage 2 baseline scorer (#1738)."""

import json

import pytest

from decafclaw.config_types import ModelConfig
from decafclaw.survey.score import (
    classify_failure,
    estimate_spend,
    parse_suggestion,
    run_score,
)


class StubRetriever:
    name = "stub"
    _top_k = 5

    def __init__(self, mapping):
        self.mapping = mapping

    async def pages_for(self, text):
        return tuple(self.mapping.get(text, ()))


def _cases():
    return [
        {
            "turn_id": "t1",
            "conv_id": "c",
            "timestamp": "2026-01-01T00:00:00",
            "transcript_tail": "tail t1",
            "actual_next_user_message": "commit it",
            "previous_user_message": "prev",
            "assistant_before": "reply",
            "provenance_pages": [],
            "active_model": "primary",
        },
        {
            "turn_id": "t2",
            "conv_id": "c",
            "timestamp": "2026-01-01T00:00:00",
            "transcript_tail": "tail t2",
            "actual_next_user_message": "open a pull request",
            "previous_user_message": "prev",
            "assistant_before": "reply",
            "provenance_pages": [],
            "active_model": "primary",
        },
        {
            "turn_id": "t3",
            "conv_id": "c",
            "timestamp": "2026-01-01T00:00:00",
            "transcript_tail": "tail t3",
            "actual_next_user_message": "please stop",
            "previous_user_message": "prev",
            "assistant_before": "reply",
            "provenance_pages": [],
            "active_model": "primary",
        },
        {
            "turn_id": "t4",
            "conv_id": "c",
            "timestamp": "2026-01-01T00:00:00",
            "transcript_tail": "tail t4",
            "actual_next_user_message": "please stop",
            "previous_user_message": "prev",
            "assistant_before": "reply",
            "provenance_pages": [],
            "active_model": "primary",
        },
    ]


def _config(config):
    config.model_configs = {
        "primary": ModelConfig(provider="p", model="m"),
        "aux": ModelConfig(provider="p", model="m"),
    }
    config.default_model = "primary"
    config.auxiliary_model = "aux"
    return config


_RESPONSES = {
    "tail t1": "commit it",
    "tail t2": "open the pull request",
    "tail t3": "NONE",
    "tail t4": "what is the meaning of life",
}


def _patch_llm(monkeypatch):
    calls = []

    async def fake_call(_config, messages, model_name=None, **kwargs):
        tail = messages[-1]["content"].split("Conversation so far:\n\n", 1)[1].split("\n\nHuman's", 1)[0]
        calls.append({"model": model_name, "tail": tail})
        return {"content": _RESPONSES[tail], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}

    monkeypatch.setattr("decafclaw.survey.score.call_llm", fake_call)
    return calls


async def test_run_score_aggregates_all_granularities(config, monkeypatch, tmp_path):
    _config(config)
    calls = _patch_llm(monkeypatch)
    retriever = StubRetriever(
        {
            "commit it": ("a", "b"),
            "open a pull request": ("a", "b"),
            "open the pull request": ("a", "c"),
            "please stop": ("d",),
            "what is the meaning of life": ("f",),
        }
    )
    summary = await run_score(
        config,
        _cases(),
        retriever=retriever,
        arms=["active"],
        out_dir=tmp_path,
        top_k=5,
    )
    active = summary["results"]["active"]
    assert active["cases"] == 4
    assert active["exact"] == 1
    assert active["jaccard_hits"] == 2
    assert active["abstained"] == 1
    assert active["direction_cases"] == 3
    assert active["direction_mean"] == pytest.approx(4 / 9, abs=1e-3)
    assert active["failure_taxonomy"]["abstained"] == 1
    assert len(calls) == 4

    scores = (tmp_path / "scores.jsonl").read_text().splitlines()
    assert len(scores) == 4
    assert json.loads(scores[0])["turn_id"] == "t1"
    assert "Operator sheet" in (tmp_path / "spot-check.md").read_text()


async def test_run_score_reuses_cache(config, monkeypatch, tmp_path):
    _config(config)
    calls = _patch_llm(monkeypatch)
    retriever = StubRetriever({})
    await run_score(config, _cases(), retriever=retriever, arms=["active"], out_dir=tmp_path, top_k=5)
    assert len(calls) == 4
    await run_score(config, _cases(), retriever=retriever, arms=["active"], out_dir=tmp_path, top_k=5)
    assert len(calls) == 4


async def test_run_score_dedupes_collapsed_arms(config, monkeypatch, tmp_path):
    _config(config)
    config.auxiliary_model = ""
    calls = _patch_llm(monkeypatch)
    retriever = StubRetriever({})
    summary = await run_score(
        config,
        _cases(),
        retriever=retriever,
        arms=["aux", "active"],
        out_dir=tmp_path,
        top_k=5,
    )
    assert len(calls) == 4
    assert summary["results"]["aux"]["models"] == ["primary"]
    assert summary["results"]["active"]["models"] == ["primary"]


async def test_run_score_max_calls_refuses(config, monkeypatch, tmp_path):
    _config(config)
    _patch_llm(monkeypatch)
    with pytest.raises(RuntimeError, match="--max-calls"):
        await run_score(
            config,
            _cases(),
            retriever=StubRetriever({}),
            arms=["aux", "active"],
            out_dir=tmp_path,
            max_calls=2,
        )


async def test_run_score_dry_run_makes_no_calls(config, monkeypatch, tmp_path):
    _config(config)
    calls = _patch_llm(monkeypatch)
    summary = await run_score(
        config,
        _cases(),
        retriever=StubRetriever({}),
        arms=["aux", "active"],
        out_dir=tmp_path,
        dry_run=True,
    )
    assert calls == []
    assert summary["dry_run"] is True
    assert summary["estimate"]["calls"] == 8


def test_estimate_spend_dedupes_identical_arms(config):
    _config(config)
    config.auxiliary_model = ""
    estimate = estimate_spend(_cases(), config, ["aux", "active"])
    assert estimate["calls"] == 4
    assert estimate["unpriced_models"] == ["primary"]


def test_parse_suggestion_variants():
    assert parse_suggestion("commit it") == "commit it"
    assert parse_suggestion("```\ncommit it\n```") == "commit it"
    assert parse_suggestion("Suggestion: commit it") == "commit it"
    assert parse_suggestion('"commit it"') == "commit it"
    assert parse_suggestion("NONE") == ""
    assert parse_suggestion("none") == ""
    assert parse_suggestion("") == ""


def test_classify_failure_taxonomy():
    case = {
        "transcript_tail": "we discussed the file",
        "assistant_before": "What would you like to do?",
        "actual_next_user_message": "open the file",
    }
    assert classify_failure(case, "", 0.0, False) == "abstained"
    long_suggestion = " ".join(["word"] * 30)
    assert classify_failure(case, long_suggestion, 0.0, False) == "too_long"
    assert classify_failure(case, "Let me know if you need anything else", 0.0, False) == "generic_filler"
    assert classify_failure(case, "open src/secret.py", 0.0, False) == "hallucinated_specific"
    assert classify_failure(case, "open the file", 1.0, True) is None


def test_classify_missed_offered_option():
    case = {
        "transcript_tail": "tail",
        "assistant_before": "Next steps:\n- Commit the change\n- Open a PR",
        "actual_next_user_message": "Commit the change",
    }
    assert classify_failure(case, "Something unrelated", 0.0, False) == "missed_offered_option"
