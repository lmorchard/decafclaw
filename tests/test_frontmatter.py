"""Tests for YAML frontmatter parsing and serialization."""

import pytest

from decafclaw.frontmatter import (
    build_composite_text,
    get_frontmatter_field,
    parse_frontmatter,
    serialize_frontmatter,
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
