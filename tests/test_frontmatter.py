"""Tests for YAML frontmatter parsing and serialization."""

import pytest

from decafclaw.frontmatter import (
    build_composite_text,
    get_frontmatter_field,
    join_frontmatter,
    merge_frontmatter,
    parse_frontmatter,
    parse_frontmatter_block,
    serialize_frontmatter,
    split_frontmatter,
)

# -- parse_frontmatter ---------------------------------------------------------


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        text = "---\ntitle: Test\ntags: [a, b]\n---\n# Hello\nBody here."
        meta, body = parse_frontmatter(text)
        assert meta == {"title": "Test", "tags": ["a", "b"]}
        assert body == "# Hello\nBody here."

    def test_no_frontmatter(self):
        text = "# Hello\nJust plain markdown."
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_empty_frontmatter_block(self):
        text = "---\n\n---\n# Hello"
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == "# Hello"

    def test_malformed_yaml(self):
        text = "---\n[invalid: yaml: stuff\n---\nBody"
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == "Body"

    def test_non_dict_yaml(self):
        text = "---\n- just\n- a list\n---\nBody"
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == "Body"

    def test_frontmatter_not_at_start(self):
        text = "Some text\n---\ntitle: Test\n---\nBody"
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_full_frontmatter(self):
        text = (
            "---\n"
            "summary: A test page\n"
            "keywords: [foo, bar, baz]\n"
            "tags: [config, models]\n"
            "importance: 0.8\n"
            "---\n"
            "# Content\nHere."
        )
        meta, body = parse_frontmatter(text)
        assert meta["summary"] == "A test page"
        assert meta["keywords"] == ["foo", "bar", "baz"]
        assert meta["tags"] == ["config", "models"]
        assert meta["importance"] == 0.8
        assert body == "# Content\nHere."


# -- serialize_frontmatter -----------------------------------------------------


class TestSerializeFrontmatter:
    def test_round_trip(self):
        original_meta = {"summary": "Test", "tags": ["a", "b"]}
        original_body = "# Hello\nBody."
        text = serialize_frontmatter(original_meta, original_body)
        meta, body = parse_frontmatter(text)
        assert meta == original_meta
        assert body == original_body

    def test_empty_dict_omits_block(self):
        body = "# Hello\nNo frontmatter."
        text = serialize_frontmatter({}, body)
        assert text == body
        assert "---" not in text

    def test_preserves_body_exactly(self):
        body = "Line 1\nLine 2\n\nLine 4"
        text = serialize_frontmatter({"key": "val"}, body)
        _, parsed_body = parse_frontmatter(text)
        assert parsed_body == body


# -- get_frontmatter_field -----------------------------------------------------


class TestGetFrontmatterField:
    def test_importance_clamped_low(self):
        assert get_frontmatter_field({"importance": -0.5}, "importance") == 0.0

    def test_importance_clamped_high(self):
        assert get_frontmatter_field({"importance": 1.5}, "importance") == 1.0

    def test_importance_valid(self):
        assert get_frontmatter_field({"importance": 0.7}, "importance") == pytest.approx(0.7)

    def test_importance_string(self):
        assert get_frontmatter_field({"importance": "0.3"}, "importance") == pytest.approx(0.3)

    def test_importance_invalid(self):
        assert get_frontmatter_field({"importance": "not a number"}, "importance") == 0.5

    def test_importance_missing(self):
        assert get_frontmatter_field({}, "importance", 0.5) == 0.5

    def test_keywords_list(self):
        assert get_frontmatter_field({"keywords": ["a", "b"]}, "keywords") == ["a", "b"]

    def test_keywords_string(self):
        assert get_frontmatter_field({"keywords": "single"}, "keywords") == ["single"]

    def test_keywords_missing(self):
        assert get_frontmatter_field({}, "keywords", []) == []

    def test_tags_list(self):
        assert get_frontmatter_field({"tags": ["x", "y"]}, "tags") == ["x", "y"]

    def test_summary_string(self):
        assert get_frontmatter_field({"summary": "A page"}, "summary") == "A page"

    def test_summary_number(self):
        assert get_frontmatter_field({"summary": 42}, "summary") == "42"


# -- build_composite_text ------------------------------------------------------


class TestBuildCompositeText:
    def test_full_frontmatter(self):
        meta = {"summary": "A test", "keywords": ["foo", "bar"], "tags": ["config"]}
        body = "Content here."
        result = build_composite_text(meta, body)
        assert result == "A test\nfoo, bar\nconfig\nContent here."

    def test_no_frontmatter(self):
        body = "Just body content."
        result = build_composite_text({}, body)
        assert result == body

    def test_partial_frontmatter(self):
        meta = {"summary": "Just a summary"}
        body = "Body."
        result = build_composite_text(meta, body)
        assert result == "Just a summary\nBody."

    def test_keywords_only(self):
        meta = {"keywords": ["alpha", "beta"]}
        body = "Body."
        result = build_composite_text(meta, body)
        assert result == "alpha, beta\nBody."

    def test_composite_includes_inline_tags(self):
        out = build_composite_text({"tags": ["rust"]}, "body mentioning #async")
        # Assert on the tags line specifically, not just substring-anywhere-in-
        # `out` — the raw body text already contains "#async" as a substring,
        # so a weaker "in out" check would pass even without folding inline
        # tags into the tags portion.
        tags_line = out.splitlines()[0]
        assert "rust" in tags_line
        assert "async" in tags_line

    def test_composite_dedups_tag_in_both_frontmatter_and_inline(self):
        out = build_composite_text({"tags": ["rust"]}, "love #rust here")
        tags_line = out.splitlines()[0]
        assert tags_line.count("rust") == 1


# -- split_frontmatter / join_frontmatter ---------------------------------------


class TestSplitFrontmatter:
    def test_splits_block_without_parsing(self):
        text = "---\ntitle: Test\n---\n# Hello\nBody."
        raw, body = split_frontmatter(text)
        assert raw == "title: Test"
        assert body == "# Hello\nBody."

    def test_absent_block(self):
        text = "# Hello\nNo frontmatter."
        raw, body = split_frontmatter(text)
        assert raw is None
        assert body == text

    def test_empty_block_is_not_none(self):
        """An empty block existed; None means no block at all."""
        raw, body = split_frontmatter("---\n\n---\n# Hello")
        assert raw == ""
        assert body == "# Hello"

    def test_malformed_yaml_round_trips(self):
        text = "---\nthis: is: not: valid\n---\nBody."
        raw, body = split_frontmatter(text)
        assert raw == "this: is: not: valid"
        assert body == "Body."

    def test_body_starting_with_hr_is_not_swallowed(self):
        """The regex is non-greedy and anchored, so the real block wins."""
        text = "---\ntitle: T\n---\n---\nAn hr, not a delimiter.\n"
        raw, body = split_frontmatter(text)
        assert raw == "title: T"
        assert body == "---\nAn hr, not a delimiter.\n"

    @pytest.mark.parametrize("text", [
        "---\ntitle: Test\n---\n# Hello\nBody.",
        "# No frontmatter here.\n",
        "---\n\n---\nEmpty block.",
        "---\nbroken: : yaml\n---\n",
        "---\ntitle: T\n---\n---\nhr body\n",
    ])
    def test_round_trip_is_byte_identical(self, text):
        assert join_frontmatter(*split_frontmatter(text)) == text


class TestParseFrontmatterBlock:
    def test_valid_block(self):
        meta, error = parse_frontmatter_block("title: Test\ntags:\n- a")
        assert meta == {"title": "Test", "tags": ["a"]}
        assert error is None

    def test_none_block(self):
        assert parse_frontmatter_block(None) == ({}, None)

    def test_blank_block(self):
        assert parse_frontmatter_block("   \n") == ({}, None)

    def test_malformed_block_reports_error(self):
        meta, error = parse_frontmatter_block("this: is: not: valid")
        assert meta == {}
        assert error is not None

    def test_non_mapping_block_reports_error(self):
        meta, error = parse_frontmatter_block("- just\n- a\n- list")
        assert meta == {}
        assert error == "frontmatter is not a mapping"


# -- merge_frontmatter ---------------------------------------------------------


class TestMergeFrontmatter:
    """The shared merge primitive behind the REST patch path, the
    `vault_update_frontmatter` tool, and the backfill CLI."""

    def test_does_not_mutate_existing(self):
        existing = {"importance": 0.4}
        merged = merge_frontmatter(existing, {"summary": "S"}, overwrite=True)
        assert existing == {"importance": 0.4}
        assert merged == {"importance": 0.4, "summary": "S"}

    def test_overwrite_true_replaces(self):
        merged = merge_frontmatter(
            {"summary": "old", "importance": 0.2},
            {"summary": "new"},
            overwrite=True,
        )
        assert merged == {"summary": "new", "importance": 0.2}

    def test_overwrite_false_keeps_existing_value(self):
        merged = merge_frontmatter(
            {"summary": "old"}, {"summary": "new"}, overwrite=False,
        )
        assert merged == {"summary": "old"}

    @pytest.mark.parametrize("empty", [None, "", []])
    def test_overwrite_false_fills_absent_or_empty(self, empty):
        """None / "" / [] all count as unset, so they get filled."""
        merged = merge_frontmatter(
            {"summary": empty}, {"summary": "new"}, overwrite=False,
        )
        assert merged == {"summary": "new"}

    def test_overwrite_false_fills_missing_key(self):
        merged = merge_frontmatter({}, {"summary": "new"}, overwrite=False)
        assert merged == {"summary": "new"}

    def test_overwrite_false_does_not_fill_falsy_but_set_values(self):
        """0.0 and False are real values, not "empty" — they must survive."""
        merged = merge_frontmatter(
            {"importance": 0.0}, {"importance": 0.9}, overwrite=False,
        )
        assert merged == {"importance": 0.0}

    def test_coerces_importance_clamping_high(self):
        merged = merge_frontmatter({}, {"importance": 1.7}, overwrite=True)
        assert merged == {"importance": 1.0}

    def test_coerces_importance_clamping_low(self):
        merged = merge_frontmatter({}, {"importance": -3}, overwrite=True)
        assert merged == {"importance": 0.0}

    def test_coerces_importance_from_string(self):
        merged = merge_frontmatter({}, {"importance": "0.25"}, overwrite=True)
        assert merged == {"importance": 0.25}

    def test_coerces_unparseable_importance_to_default(self):
        merged = merge_frontmatter({}, {"importance": "nope"}, overwrite=True)
        assert merged == {"importance": 0.5}

    @pytest.mark.parametrize("field", ["tags", "keywords"])
    def test_coerces_scalar_to_list(self, field):
        merged = merge_frontmatter({}, {field: "solo"}, overwrite=True)
        assert merged == {field: ["solo"]}

    @pytest.mark.parametrize("field", ["tags", "keywords"])
    def test_coerces_list_members_to_str(self, field):
        merged = merge_frontmatter({}, {field: [1, 2.5]}, overwrite=True)
        assert merged == {field: ["1", "2.5"]}

    @pytest.mark.parametrize("field", ["tags", "keywords"])
    def test_coerces_non_list_non_str_to_empty_list(self, field):
        merged = merge_frontmatter({}, {field: 7}, overwrite=True)
        assert merged == {field: []}

    def test_coerces_summary_to_str(self):
        merged = merge_frontmatter({}, {"summary": 42}, overwrite=True)
        assert merged == {"summary": "42"}

    def test_unknown_fields_pass_through_uncoerced(self):
        merged = merge_frontmatter(
            {}, {"aliases": ["a", 1]}, overwrite=True,
        )
        assert merged == {"aliases": ["a", 1]}

    def test_none_is_set_not_deleted(self):
        """Documented: there is no deletion path. Callers strip nulls
        themselves, and must strip only the keys they nulled."""
        merged = merge_frontmatter(
            {"tags": ["keep"], "aliases": None},
            {"tags": None},
            overwrite=True,
        )
        assert merged == {"tags": None, "aliases": None}
        assert "tags" in merged

    def test_none_with_overwrite_false_is_a_no_op(self):
        """`existing` value is absent/empty, so the None fills it — and a None
        coerces to None, so the key still ends up present but null."""
        merged = merge_frontmatter({}, {"summary": None}, overwrite=False)
        assert merged == {"summary": None}
