from types import SimpleNamespace

import pytest

from decafclaw.workflow.journal import (
    Journal,
    JournalEntry,
    fingerprint,
    load_journal,
    save_journal,
)


def _cfg(tmp_path):
    return SimpleNamespace(workspace_path=tmp_path)


def test_fingerprint_is_stable_and_order_insensitive():
    a = fingerprint("llm_call", {"prompt": "hi", "schema": {"x": 1}})
    b = fingerprint("llm_call", {"schema": {"x": 1}, "prompt": "hi"})
    assert a == b
    assert a != fingerprint("llm_call", {"prompt": "bye", "schema": {"x": 1}})


def test_append_is_contiguous_and_get_by_seq():
    j = Journal(workflow_name="t")
    j.append(0, "llm_call", "fp0", {"a": 1})
    j.append(1, "user_input", "fp1", "answer")
    assert j.get(0).result == {"a": 1}
    assert j.get(1).result == "answer"
    assert j.get(2) is None


def test_append_rejects_non_contiguous_seq():
    j = Journal(workflow_name="t")
    with pytest.raises(ValueError):
        j.append(1, "llm_call", "fp", None)


def test_save_and_load_round_trip(tmp_path):
    cfg = _cfg(tmp_path)
    j = Journal(workflow_name="interview", status="suspended")
    j.append(0, "user_input", "fp0", "topic")
    save_journal(cfg, "conv1", j)

    loaded = load_journal(cfg, "conv1")
    assert loaded.workflow_name == "interview"
    assert loaded.status == "suspended"
    assert loaded.get(0).kind == "user_input"
    assert loaded.get(0).result == "topic"
    assert loaded.get(0).args_fingerprint == "fp0"


def test_load_missing_returns_none(tmp_path):
    assert load_journal(_cfg(tmp_path), "nope") is None
