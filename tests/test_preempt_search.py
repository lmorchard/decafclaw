"""Tests for the pre-emptive tool search module."""

from decafclaw.preempt_search import (
    STOPWORDS,
    extract_last_assistant_text,
    match_tools,
    tokenize,
)


def _td(name: str, description: str = "") -> dict:
    """Build a minimal tool definition dict."""
    return {
        "type": "function",
        "function": {"name": name, "description": description},
    }


# -- tokenize -----------------------------------------------------------------


class TestTokenize:
    def test_empty_input(self):
        assert tokenize("") == set()

    def test_basic_splitting(self):
        assert tokenize("hello world") == {"hello", "world"}

    def test_lowercasing(self):
        assert tokenize("Hello WORLD") == {"hello", "world"}

    def test_non_alphanumeric_separators(self):
        # Hyphens, underscores, punctuation all split.
        assert tokenize("foo-bar_baz.qux") == {"foo", "bar", "baz", "qux"}

    def test_multi_char_separators(self):
        # Runs of non-alphanumeric chars collapse to one split.
        assert tokenize("foo!!!bar???baz") == {"foo", "bar", "baz"}

    def test_drops_short_tokens(self):
        # Tokens < 3 chars are dropped.
        assert tokenize("a bb ccc dddd") == {"ccc", "dddd"}

    def test_drops_stopwords(self):
        # Only non-stopwords survive.
        result = tokenize("the quick brown fox")
        assert "the" not in result
        assert "quick" in result
        assert "brown" in result
        assert "fox" in result

    def test_idempotent(self):
        """Tokenizing the same input twice yields the same result."""
        text = "The quick-brown_fox jumps OVER the lazy dog!"
        assert tokenize(text) == tokenize(text)

    def test_deduplicates(self):
        # Repeated words collapse into the set.
        assert tokenize("vault vault vault search") == {"vault", "search"}

    def test_mcp_tool_name_tokenizes(self):
        # Confirms the mcp__server__tool pattern splits as expected.
        # ("get" is in the stopword list — common verb, no discriminating
        # power when many tools have get_* naming.)
        assert tokenize("mcp__oblique_strategies__get_strategy") == {
            "mcp", "oblique", "strategies", "strategy",
        }

    def test_stopword_list_non_empty(self):
        # Sanity: stopword filter is actually populated.
        assert len(STOPWORDS) > 10
        assert "the" in STOPWORDS


# -- match_tools --------------------------------------------------------------


class TestMatchTools:
    def test_empty_input_tokens(self):
        assert match_tools(set(), [_td("vault_read")], 10) == []

    def test_empty_candidates(self):
        assert match_tools({"vault"}, [], 10) == []

    def test_zero_max_matches(self):
        assert match_tools({"vault"}, [_td("vault_read")], 0) == []

    def test_single_match(self):
        result = match_tools({"vault"}, [_td("vault_read", "Read a vault page")], 10)
        assert len(result) == 1
        assert result[0]["name"] == "vault_read"
        assert result[0]["score"] == 1
        assert result[0]["matched_tokens"] == ["vault"]

    def test_multiple_matches_sorted_by_score(self):
        tools = [
            _td("low_match", "Does something with vault"),
            _td("high_match", "vault backlinks search vault"),
            _td("no_match", "Totally unrelated functionality"),
        ]
        # "backlinks" and "vault" match; "backlinks" appears once, "vault" once
        # in the description; the name "high_match" adds nothing new.
        result = match_tools({"vault", "backlinks"}, tools, 10)
        names = [r["name"] for r in result]
        assert "no_match" not in names
        # high_match should score 2 (vault + backlinks), low_match scores 1
        assert names[0] == "high_match"
        assert result[0]["score"] == 2
        assert result[1]["name"] == "low_match"
        assert result[1]["score"] == 1

    def test_tie_break_alphabetical(self):
        tools = [
            _td("zebra_tool", "Uses vault"),
            _td("alpha_tool", "Uses vault"),
            _td("mango_tool", "Uses vault"),
        ]
        result = match_tools({"vault"}, tools, 10)
        assert [r["name"] for r in result] == ["alpha_tool", "mango_tool", "zebra_tool"]

    def test_max_matches_cap(self):
        tools = [_td(f"tool_{i}", "vault") for i in range(20)]
        result = match_tools({"vault"}, tools, 5)
        assert len(result) == 5

    def test_matched_tokens_sorted(self):
        """matched_tokens is sorted for stable output."""
        result = match_tools(
            {"zebra", "alpha", "mango"},
            [_td("x", "alpha mango zebra")],
            10,
        )
        assert result[0]["matched_tokens"] == ["alpha", "mango", "zebra"]

    def test_name_tokens_count(self):
        """Tokens in the tool name participate in the match."""
        # Description is empty; only the name tokenizes.
        result = match_tools({"backlinks"}, [_td("vault_backlinks", "")], 10)
        assert len(result) == 1
        assert result[0]["name"] == "vault_backlinks"

    def test_skips_candidates_without_name(self):
        # Malformed defs with no function.name are ignored, not crashed on.
        tools = [
            {"function": {"description": "no name here"}},
            _td("vault_read", "vault page"),
        ]
        result = match_tools({"vault"}, tools, 10)
        assert len(result) == 1
        assert result[0]["name"] == "vault_read"


# -- extract_last_assistant_text ---------------------------------------------


class TestExtractLastAssistantText:
    def test_empty_history(self):
        assert extract_last_assistant_text([]) == ""

    def test_no_assistant_messages(self):
        history = [
            {"role": "user", "content": "hello"},
            {"role": "tool", "content": "some result"},
        ]
        assert extract_last_assistant_text(history) == ""

    def test_returns_most_recent_assistant(self):
        history = [
            {"role": "assistant", "content": "First response"},
            {"role": "user", "content": "follow up"},
            {"role": "assistant", "content": "Second response"},
        ]
        assert extract_last_assistant_text(history) == "Second response"

    def test_skips_tool_roles(self):
        history = [
            {"role": "assistant", "content": "Text response"},
            {"role": "tool", "content": "tool result"},
            {"role": "user", "content": "question"},
        ]
        assert extract_last_assistant_text(history) == "Text response"

    def test_skips_cancelled_marker(self):
        history = [
            {"role": "assistant", "content": "Real response"},
            {"role": "user", "content": "stop"},
            {"role": "assistant", "content": "[cancelled]"},
        ]
        assert extract_last_assistant_text(history) == "Real response"

    def test_skips_empty_content(self):
        history = [
            {"role": "assistant", "content": "Real response"},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": None},
        ]
        assert extract_last_assistant_text(history) == "Real response"

    def test_strips_whitespace(self):
        history = [{"role": "assistant", "content": "   hello   "}]
        assert extract_last_assistant_text(history) == "hello"

    def test_non_string_content_skipped(self):
        # Some histories store list-of-parts content; we only match plain strings.
        history = [
            {"role": "assistant", "content": "Real text"},
            {"role": "assistant", "content": [{"type": "text", "text": "fancy"}]},
        ]
        assert extract_last_assistant_text(history) == "Real text"
