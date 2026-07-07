import json
from types import SimpleNamespace

import pytest

from decafclaw.workflow.journal import (
    Journal,
    JournalEntry,
    fingerprint,
    load_journal,
    save_journal,
)
from decafclaw.workflow.paths import workflow_dir, workflow_path


def _cfg(tmp_path):
    return SimpleNamespace(workspace_path=tmp_path)


def test_fingerprint_is_stable_and_order_insensitive():
    a = fingerprint("llm_call", {"prompt": "hi", "schema": {"x": 1}})
    b = fingerprint("llm_call", {"schema": {"x": 1}, "prompt": "hi"})
    assert a == b
    assert a != fingerprint("llm_call", {"prompt": "bye", "schema": {"x": 1}})


def test_append_and_get_by_tuple_seq():
    j = Journal(workflow_name="t")
    j.append((0,), "llm_call", "fp0", {"a": 1})
    j.append((1,), "user_input", "fp1", "answer")
    assert j.get((0,)).result == {"a": 1}
    assert j.get((1,)).result == "answer"
    assert j.get((2,)) is None


def test_append_rejects_duplicate_seq():
    j = Journal(workflow_name="t")
    j.append((0,), "llm_call", "fp", None)
    with pytest.raises(ValueError):
        j.append((0,), "llm_call", "fp", None)


def test_append_accepts_non_contiguous_tuple_paths():
    """Tuple paths can land out of order (parallel thunks finish in any
    order); the journal only enforces no-duplicates, not contiguity."""
    j = Journal(workflow_name="t")
    j.append((1, 0), "llm_call", "fp10", "a")
    j.append((0,), "user_input", "fp0", "b")  # earlier seq, lands later
    j.append((1, 2), "llm_call", "fp12", "c")  # gap at (1, 1) is fine
    assert j.get((1, 0)).result == "a"
    assert j.get((0,)).result == "b"
    assert j.get((1, 2)).result == "c"


def test_save_and_load_round_trip_tuple_seq(tmp_path):
    cfg = _cfg(tmp_path)
    j = Journal(workflow_name="interview", status="suspended")
    j.append((0,), "user_input", "fp0", "topic")
    j.append((1, 2), "llm_call", "fp12", {"a": 1})
    save_journal(cfg, "conv1", j)

    loaded = load_journal(cfg, "conv1")
    assert loaded.workflow_name == "interview"
    assert loaded.status == "suspended"
    assert loaded.get((0,)).kind == "user_input"
    assert loaded.get((0,)).result == "topic"
    assert loaded.get((0,)).args_fingerprint == "fp0"
    assert loaded.get((1, 2)).result == {"a": 1}


def test_on_disk_format_uses_dotted_string_seq(tmp_path):
    """The persisted JSON encodes seq as a dotted string for human
    readability and forward compatibility (per #574 spec)."""
    cfg = _cfg(tmp_path)
    j = Journal(workflow_name="t")
    j.append((0,), "user_input", "fp0", "x")
    j.append((1, 2, 3), "llm_call", "fp123", {"y": 1})
    save_journal(cfg, "convDotted", j)

    raw = json.loads(workflow_path(cfg, "convDotted").read_text())
    seqs = [e["seq"] for e in raw["entries"]]
    assert seqs == ["0", "1.2.3"]


def test_to_dict_sorts_entries_by_seq():
    """Parallel branches can land in any insertion order; serialize sorted
    so on-disk diffs are stable and manual inspection is straightforward."""
    j = Journal(workflow_name="t")
    # Insert out of order: (1, 0, 0) before (0, 1, 0), etc.
    j.append((1, 0, 0), "tool_call", "fpA", "a")
    j.append((0, 1, 0), "tool_call", "fpB", "b")
    j.append((0, 0, 0), "tool_call", "fpC", "c")
    j.append((2,), "llm_call", "fpD", "d")
    d = j.to_dict()
    seqs = [e["seq"] for e in d["entries"]]
    assert seqs == ["0.0.0", "0.1.0", "1.0.0", "2"]


def test_legacy_int_seq_upgrades_to_one_tuple(tmp_path):
    """Existing on-disk journals (pre-#574) wrote integer seq values.
    from_dict must transparently upgrade those to 1-tuples so we don't
    require a migration step."""
    cfg = _cfg(tmp_path)
    # Hand-build a legacy-format journal file (integer seq).
    workflow_dir(cfg, "convLegacy", create=True)
    path = workflow_path(cfg, "convLegacy")
    path.write_text(json.dumps({
        "workflow_name": "interview",
        "status": "suspended",
        "entries": [
            {"seq": 0, "kind": "user_input",
             "args_fingerprint": "fp0", "result": "topic"},
            {"seq": 1, "kind": "llm_call",
             "args_fingerprint": "fp1", "result": {"a": 1}},
        ],
    }))

    loaded = load_journal(cfg, "convLegacy")
    assert loaded is not None
    assert loaded.get((0,)).result == "topic"
    assert loaded.get((1,)).result == {"a": 1}
    # And after a round-trip save, the on-disk format is the new dotted form.
    save_journal(cfg, "convLegacy", loaded)
    raw = json.loads(path.read_text())
    assert [e["seq"] for e in raw["entries"]] == ["0", "1"]


def test_load_missing_returns_none(tmp_path):
    assert load_journal(_cfg(tmp_path), "nope") is None


def test_journal_attempts_defaults_to_zero():
    """A freshly-constructed Journal starts with attempts=0 so the
    auto-resume counter has a well-defined baseline."""
    j = Journal(workflow_name="t")
    assert j.attempts == 0


def test_journal_attempts_round_trip(tmp_path):
    """attempts must survive to_dict / from_dict so the counter persists
    across process restarts (that's the whole point — bounding replay
    storms across crashes)."""
    j = Journal(workflow_name="t")
    j.attempts = 2
    d = j.to_dict()
    assert d["attempts"] == 2
    restored = Journal.from_dict(d)
    assert restored.attempts == 2


def test_journal_backward_compatible_missing_attempts():
    """Journal files written before this field existed load with
    attempts=0 rather than raising a KeyError."""
    d = {
        "workflow_name": "t",
        "status": "running",
        "entries": [],
    }
    j = Journal.from_dict(d)
    assert j.attempts == 0


def test_from_dict_rejects_duplicate_seq(tmp_path):
    """A corrupted journal file with two entries that resolve to the same
    tuple-path seq must raise rather than silently overwrite — symmetric
    with Journal.append's duplicate guard. Mixing an int and a dotted-str
    form of the same seq exercises the path normalization too."""
    cfg = _cfg(tmp_path)
    workflow_dir(cfg, "convDup", create=True)
    path = workflow_path(cfg, "convDup")
    path.write_text(json.dumps({
        "workflow_name": "interview",
        "status": "running",
        "entries": [
            {"seq": 0, "kind": "user_input",
             "args_fingerprint": "fp0", "result": "first"},
            {"seq": "0", "kind": "user_input",
             "args_fingerprint": "fp0", "result": "second"},
        ],
    }))

    with pytest.raises(ValueError, match="duplicate seq"):
        load_journal(cfg, "convDup")
