"""Unit tests for `_summarize_expectations` — judge-prompt assertion coverage."""

from decafclaw.eval.reflect import _summarize_expectations


def test_single_response_contains():
    s = _summarize_expectations({"expect": {"response_contains": "Python"}})
    assert "response_contains" in s
    assert "Python" in s


def test_multiple_assertions_all_listed():
    """Without this fix, only response_contains made it into the judge prompt."""
    s = _summarize_expectations({
        "expect": {
            "response_contains": "ok",
            "max_tool_calls": 5,
            "expect_tool": "vault_search",
            "expect_no_tool": ["shell", "web_fetch"],
        }
    })
    assert "response_contains" in s
    assert "max_tool_calls" in s
    assert "5" in s
    assert "expect_tool" in s
    assert "vault_search" in s
    assert "expect_no_tool" in s
    assert "shell" in s
    assert "web_fetch" in s


def test_no_response_contains_still_renders():
    """Failures on max_tool_errors used to render `Expected: ?` — fixed."""
    s = _summarize_expectations({"expect": {"max_tool_errors": 0}})
    assert "max_tool_errors" in s
    assert "0" in s
    assert "?" not in s


def test_response_contains_all_appears():
    s = _summarize_expectations({"expect": {"response_contains_all": ["a", "b"]}})
    assert "response_contains_all" in s
    assert "a" in s and "b" in s


def test_count_by_name_renders():
    s = _summarize_expectations({
        "expect": {"expect_tool_count_by_name": {"tool_search": 2}}
    })
    assert "expect_tool_count_by_name" in s
    assert "tool_search" in s
    assert "2" in s


def test_empty_expect_renders_none_marker():
    s = _summarize_expectations({"expect": {}})
    assert "none set" in s


def test_no_expect_key_at_all_renders_none_marker():
    s = _summarize_expectations({})
    assert "none set" in s


def test_multi_turn_uses_last_turn_expect():
    """The failure typically happens on the last turn; summarize its expect."""
    s = _summarize_expectations({
        "turns": [
            {"input": "first", "expect": {"response_contains": "hello"}},
            {"input": "second", "expect": {"response_contains": "goodbye", "max_tool_calls": 3}},
        ]
    })
    assert "goodbye" in s
    assert "max_tool_calls" in s
    # First turn's expectation should NOT be summarized — it passed
    assert "hello" not in s


def test_multi_turn_with_empty_turns_list_falls_back_to_none():
    s = _summarize_expectations({"turns": []})
    assert "none set" in s
