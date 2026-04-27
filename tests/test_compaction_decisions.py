"""Tests for the structured decision slice (#302)."""

from __future__ import annotations

import json

from decafclaw.compaction_decisions import (
    DecisionEntry,
    DecisionSlice,
    _slice_path,
    format_slice,
    load_slice,
    merge_slice,
    parse_slice_from_response,
    save_slice,
    strip_json_block,
)


def _entry(text, when="2026-04-25T12:00:00Z"):
    return DecisionEntry(text=text, created_at=when)


# -- DecisionSlice -------------------------------------------------------------


class TestDecisionSlice:
    def test_empty_default(self):
        s = DecisionSlice()
        assert s.is_empty()
        assert s.decisions == []
        assert s.open_questions == []
        assert s.artifacts == []

    def test_to_from_dict_roundtrip(self):
        s = DecisionSlice(
            decisions=[_entry("use vertex")],
            open_questions=[_entry("when to add openai?")],
            artifacts=[_entry("vault://decisions/llm")],
        )
        d = s.to_dict()
        assert d == {
            "decisions": [{"text": "use vertex", "created_at": "2026-04-25T12:00:00Z"}],
            "open_questions": [
                {"text": "when to add openai?", "created_at": "2026-04-25T12:00:00Z"},
            ],
            "artifacts": [
                {"text": "vault://decisions/llm", "created_at": "2026-04-25T12:00:00Z"},
            ],
        }
        roundtripped = DecisionSlice.from_dict(d)
        assert roundtripped == s

    def test_from_dict_robust_to_garbage(self):
        # Missing keys, wrong types, partial entries — all silently ignored.
        s = DecisionSlice.from_dict({
            "decisions": [{"text": "ok", "created_at": "2026-01-01T00:00:00Z"}],
            "open_questions": "should-be-list",  # wrong type
            "artifacts": [
                {"text": "ok"},  # missing created_at
                "not-a-dict",
                {"text": 5, "created_at": "x"},  # wrong text type
                {"text": "good", "created_at": "2026-01-02T00:00:00Z"},
            ],
            "stray_key": "ignored",
        })
        assert [e.text for e in s.decisions] == ["ok"]
        assert s.open_questions == []  # wrong type → empty
        assert [e.text for e in s.artifacts] == ["good"]


# -- Persistence ---------------------------------------------------------------


class TestPersistence:
    def test_load_missing_returns_empty(self, config):
        s = load_slice(config, "nonexistent")
        assert s.is_empty()

    def test_save_and_load_roundtrip(self, config):
        config.workspace_path.mkdir(parents=True, exist_ok=True)
        original = DecisionSlice(
            decisions=[_entry("decision-A")],
            open_questions=[_entry("q-1"), _entry("q-2")],
        )
        save_slice(config, "conv1", original)
        path = _slice_path(config, "conv1")
        assert path.exists()
        # File contents are valid JSON with the expected structure.
        raw = json.loads(path.read_text())
        assert "decisions" in raw and "open_questions" in raw and "artifacts" in raw

        roundtripped = load_slice(config, "conv1")
        assert roundtripped == original

    def test_load_invalid_json_returns_empty(self, config):
        config.workspace_path.mkdir(parents=True, exist_ok=True)
        path = _slice_path(config, "broken")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json at all {")
        s = load_slice(config, "broken")
        assert s.is_empty()

    def test_slice_path_sandboxes_traversal(self, config):
        """Path traversal characters get stripped and the resolved
        path is verified to stay within the conversations directory.
        Mirrors `_context_sidecar_path`'s defense-in-depth."""
        base = (config.workspace_path / "conversations").resolve()

        # Empty after sanitization → sentinel.
        path_empty = _slice_path(config, "")
        assert path_empty == base / "_invalid.decisions.json"

        # Pure-traversal input → sentinel.
        path_traversal = _slice_path(config, "../..")
        assert path_traversal == base / "_invalid.decisions.json"

        # Slashes are stripped, the remainder lands inside the dir.
        path_slashed = _slice_path(config, "foo/bar")
        assert path_slashed == base / "foobar.decisions.json"
        assert path_slashed.is_relative_to(base)


# -- Parse from LLM response ---------------------------------------------------


class TestParseSliceFromResponse:
    def test_valid_extraction(self):
        text = """Some prose summary blah blah.

```json
{
  "decisions": ["use vertex", "skip openai for now"],
  "open_questions": ["when to add openai?"],
  "artifacts": []
}
```
"""
        parsed = parse_slice_from_response(text)
        assert parsed == {
            "decisions": ["use vertex", "skip openai for now"],
            "open_questions": ["when to add openai?"],
            "artifacts": [],
        }

    def test_no_fenced_block(self):
        text = "Just prose, no JSON. No code blocks here."
        assert parse_slice_from_response(text) is None

    def test_malformed_json(self):
        text = "prose\n```json\n{not valid json\n```"
        assert parse_slice_from_response(text) is None

    def test_missing_required_key(self):
        text = """```json
{
  "decisions": ["x"]
}
```"""
        # Missing keys are tolerated by populating with empty lists,
        # NOT rejected — the LLM may legitimately have nothing in a
        # category. Verified explicitly below.
        parsed = parse_slice_from_response(text)
        assert parsed == {
            "decisions": ["x"],
            "open_questions": [],
            "artifacts": [],
        }

    def test_wrong_value_type_rejects(self):
        text = """```json
{
  "decisions": "should be a list",
  "open_questions": [],
  "artifacts": []
}
```"""
        assert parse_slice_from_response(text) is None

    def test_drops_non_string_items(self):
        text = """```json
{
  "decisions": ["ok", 42, null, "another"],
  "open_questions": [],
  "artifacts": []
}
```"""
        parsed = parse_slice_from_response(text)
        assert parsed == {
            "decisions": ["ok", "another"],
            "open_questions": [],
            "artifacts": [],
        }

    def test_drops_empty_and_whitespace(self):
        text = """```json
{
  "decisions": ["   ", "real one", ""],
  "open_questions": [],
  "artifacts": []
}
```"""
        parsed = parse_slice_from_response(text)
        assert parsed == {
            "decisions": ["real one"],
            "open_questions": [],
            "artifacts": [],
        }

    def test_empty_input(self):
        assert parse_slice_from_response("") is None


class TestStripJsonBlock:
    def test_removes_fenced_block(self):
        text = "Prose here.\n\n```json\n{\"x\": 1}\n```\n\nMore prose."
        assert strip_json_block(text) == "Prose here.\n\n\n\nMore prose.".strip()

    def test_no_block_unchanged(self):
        assert strip_json_block("Just prose.") == "Just prose."


# -- Merge ---------------------------------------------------------------------


class TestMergeSlice:
    def test_new_entries_get_now_timestamp(self):
        old = DecisionSlice()
        merged = merge_slice(
            old,
            {"decisions": ["new-A", "new-B"], "open_questions": [], "artifacts": []},
            max_per_category=10,
            now="2026-05-01T00:00:00Z",
        )
        assert [e.text for e in merged.decisions] == ["new-A", "new-B"]
        assert all(e.created_at == "2026-05-01T00:00:00Z" for e in merged.decisions)

    def test_existing_entries_preserve_created_at(self):
        old = DecisionSlice(decisions=[_entry("keep me", "2026-01-01T00:00:00Z")])
        merged = merge_slice(
            old,
            {"decisions": ["keep me"], "open_questions": [], "artifacts": []},
            max_per_category=10,
            now="2026-05-01T00:00:00Z",
        )
        assert len(merged.decisions) == 1
        assert merged.decisions[0].created_at == "2026-01-01T00:00:00Z"

    def test_drop_obsoleted_entries(self):
        old = DecisionSlice(decisions=[_entry("ancient")])
        merged = merge_slice(
            old,
            {"decisions": ["new"], "open_questions": [], "artifacts": []},
            max_per_category=10,
            now="2026-05-01T00:00:00Z",
        )
        assert [e.text for e in merged.decisions] == ["new"]

    def test_dedup_within_new_lists(self):
        """Repeated text in the LLM output dedupes."""
        merged = merge_slice(
            DecisionSlice(),
            {"decisions": ["dup", "dup", "unique"], "open_questions": [], "artifacts": []},
            max_per_category=10,
            now="2026-05-01T00:00:00Z",
        )
        assert [e.text for e in merged.decisions] == ["dup", "unique"]

    def test_cap_fifo_drops_oldest(self):
        """Cap = 2; merged set has 3 entries; oldest (by created_at)
        is dropped."""
        old = DecisionSlice(decisions=[
            _entry("aged", "2026-01-01T00:00:00Z"),
            _entry("middle", "2026-03-01T00:00:00Z"),
        ])
        merged = merge_slice(
            old,
            {"decisions": ["aged", "middle", "fresh"], "open_questions": [], "artifacts": []},
            max_per_category=2,
            now="2026-05-01T00:00:00Z",
        )
        texts = [e.text for e in merged.decisions]
        assert texts == ["middle", "fresh"]
        # "fresh" is new, so it has the now timestamp.
        assert merged.decisions[1].created_at == "2026-05-01T00:00:00Z"
        # "middle" preserved its original.
        assert merged.decisions[0].created_at == "2026-03-01T00:00:00Z"

    def test_cap_zero_disables_cap(self):
        """max_per_category=0 means no cap (sentinel)."""
        old = DecisionSlice()
        merged = merge_slice(
            old,
            {"decisions": [f"d{i}" for i in range(50)],
             "open_questions": [], "artifacts": []},
            max_per_category=0,
            now="2026-05-01T00:00:00Z",
        )
        assert len(merged.decisions) == 50

    def test_separate_categories_independent(self):
        old = DecisionSlice(
            decisions=[_entry("d1")],
            open_questions=[_entry("q1")],
        )
        merged = merge_slice(
            old,
            {
                "decisions": ["d1", "d2"],
                "open_questions": [],  # drop q1
                "artifacts": ["a1"],   # introduce
            },
            max_per_category=10,
            now="2026-05-01T00:00:00Z",
        )
        assert [e.text for e in merged.decisions] == ["d1", "d2"]
        assert merged.open_questions == []
        assert [e.text for e in merged.artifacts] == ["a1"]


# -- Format --------------------------------------------------------------------


class TestFormatSlice:
    def test_empty_returns_empty_string(self):
        assert format_slice(DecisionSlice()) == ""

    def test_full_slice_renders_all_sections(self):
        s = DecisionSlice(
            decisions=[_entry("decision-1")],
            open_questions=[_entry("q-1"), _entry("q-2")],
            artifacts=[_entry("vault://x")],
        )
        out = format_slice(s)
        # XML envelope (matches the system-prompt-section convention from #304).
        assert out.startswith("<decision_slice>\n")
        assert out.rstrip().endswith("</decision_slice>")
        assert "### Decisions" in out
        assert "- decision-1" in out
        assert "### Open Questions" in out
        assert "- q-1" in out
        assert "- q-2" in out
        assert "### Artifacts" in out
        assert "- vault://x" in out

    def test_partial_slice_omits_empty_subsections(self):
        s = DecisionSlice(decisions=[_entry("only this")])
        out = format_slice(s)
        assert "<decision_slice>" in out
        assert "### Decisions" in out
        assert "### Open Questions" not in out
        assert "### Artifacts" not in out
