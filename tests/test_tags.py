"""Tests for decafclaw.tags: extraction, normalization, on-demand scan."""

import pytest

from decafclaw.tags import (
    collect_all_tags,
    extract_tags,
    normalize_tag,
    pages_with_tags,
    parse_inline_tags,
)


@pytest.fixture
def agent_pages(config):
    """Create the agent pages directory."""
    d = config.vault_agent_pages_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def agent_journal(config):
    """Create the agent journal directory."""
    d = config.vault_agent_journal_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_normalize():
    assert normalize_tag("#Rust") == "rust"
    assert normalize_tag("  async ") == "async"
    assert normalize_tag("rust-lang") == "rust-lang"  # hyphen preserved, distinct from "rust"


def test_parse_inline_basic():
    assert parse_inline_tags("working on #rust and #async-io today") == {"rust", "async-io"}


def test_parse_inline_start_of_line_and_slash():
    assert parse_inline_tags("#project/alpha notes") == {"project/alpha"}


def test_parse_inline_rejects_digit_start_and_midword():
    # "#42" (digit start) is not a tag; "a#b" (not preceded by whitespace/SOL) is not
    assert parse_inline_tags("issue #42 and foo a#b") == set()


def test_parse_inline_ignores_code():
    body = "text #real\n```\n#fenced-not-tag\n```\ninline `#inline-not-tag` end"
    assert parse_inline_tags(body) == {"real"}


def test_parse_inline_atx_heading_not_a_tag():
    # "# Heading" (hash + space at SOL) is a markdown heading, not a tag
    assert parse_inline_tags("# Heading\n#realtag") == {"realtag"}


def test_extract_page_frontmatter_plus_inline():
    content = "---\ntags: [Rust, Async]\n---\nbody with #extra tag"
    assert extract_tags(content, "page") == {"rust", "async", "extra"}


def test_extract_journal_bullet_plus_inline():
    content = "## 2026-07-24 10:00\n\n- **tags:** rust, async\n\nsome #extra note"
    assert extract_tags(content, "journal") == {"rust", "async", "extra"}


class TestCollectAllTags:
    def test_counts_display_and_pages(self, config, agent_pages, agent_journal):
        (agent_pages / "Rust.md").write_text("---\ntags: [Rust]\n---\nabout rust")
        (agent_pages / "Async.md").write_text("body mentions #Rust and #async")
        journal_day_dir = agent_journal / "2026"
        journal_day_dir.mkdir(parents=True, exist_ok=True)
        (journal_day_dir / "2026-07-24.md").write_text(
            "## 2026-07-24 10:00\n\n- **tags:** rust\n\nnotes"
        )

        result = collect_all_tags(config)

        assert set(result.keys()) >= {"rust", "async"}
        assert result["rust"]["count"] == 3
        assert result["rust"]["display"] == "Rust"
        assert sorted(result["rust"]["pages"]) == sorted(
            [
                "agent/pages/Rust.md",
                "agent/pages/Async.md",
                "agent/journal/2026/2026-07-24.md",
            ]
        )

        assert result["async"]["count"] == 1
        assert result["async"]["display"] == "async"
        assert result["async"]["pages"] == ["agent/pages/Async.md"]

    def test_first_seen_display_casing_preserved(self, config, agent_pages):
        (agent_pages / "A.md").write_text("#RustLang note")
        (agent_pages / "B.md").write_text("#rustlang note")

        result = collect_all_tags(config)

        assert result["rustlang"]["display"] == "RustLang"
        assert result["rustlang"]["count"] == 2

    def test_empty_vault_returns_empty_dict(self, config):
        assert collect_all_tags(config) == {}


class TestPagesWithTags:
    def test_and_default_requires_all_tags(self, config, agent_pages):
        (agent_pages / "Both.md").write_text("#rust #async here")
        (agent_pages / "OnlyRust.md").write_text("#rust only")

        result = pages_with_tags(config, ["rust", "async"])

        assert result == ["agent/pages/Both.md"]

    def test_any_tag_true_is_or(self, config, agent_pages):
        (agent_pages / "Both.md").write_text("#rust #async here")
        (agent_pages / "OnlyRust.md").write_text("#rust only")
        (agent_pages / "Neither.md").write_text("no tags here")

        result = pages_with_tags(config, ["rust", "async"], any_tag=True)

        assert sorted(result) == sorted(["agent/pages/Both.md", "agent/pages/OnlyRust.md"])

    def test_normalizes_requested_tags(self, config, agent_pages):
        (agent_pages / "Rust.md").write_text("#rust")

        assert pages_with_tags(config, ["#Rust"]) == ["agent/pages/Rust.md"]

    def test_no_matches_returns_empty_list(self, config, agent_pages):
        (agent_pages / "Rust.md").write_text("#rust")

        assert pages_with_tags(config, ["nonexistent"]) == []
