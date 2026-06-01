"""Direct unit tests for `_check_assertions` and `_collect_tool_names`.

Covers existing assertion fields (regression) and the three new tool-name
assertions added for issue #349: `expect_tool`, `expect_no_tool`, and
`expect_tool_count_by_name`.
"""

import json

from decafclaw.eval.runner import (
    _check_assertions,
    _collect_tool_calls,
    _collect_tool_names,
)


def _assistant(tool_names):
    """Build a synthetic assistant message with the given tool calls."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": f"call_{i}", "function": {"name": n, "arguments": "{}"}}
            for i, n in enumerate(tool_names)
        ],
    }


def _assistant_with_args(calls):
    """Build a synthetic assistant message from (name, args_dict) tuples."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": f"call_{i}",
             "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(calls)
        ],
    }


def _tool_result(call_id="call_0", content="ok"):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _check(test_case, history, response="response text", tool_calls=0,
           tool_errors=0):
    """Convenience wrapper: derive tool names/args from history and call
    _check_assertions."""
    return _check_assertions(
        test_case, response, tool_calls,
        tool_errors=tool_errors,
        tool_names=_collect_tool_names(history),
        tool_calls_detail=_collect_tool_calls(history),
    )


# --- _collect_tool_names sanity ---

def test_collect_tool_names_empty_history():
    assert _collect_tool_names([]) == []


def test_collect_tool_names_collects_in_call_order():
    history = [
        {"role": "user", "content": "hi"},
        _assistant(["a", "b"]),
        _tool_result("call_0"),
        _tool_result("call_1"),
        _assistant(["c"]),
        _tool_result("call_0"),
    ]
    assert _collect_tool_names(history) == ["a", "b", "c"]


def test_collect_tool_names_skips_non_assistant_and_handles_missing():
    history = [
        {"role": "user", "content": "hi"},
        # Assistant with no tool_calls key
        {"role": "assistant", "content": "thinking"},
        # Assistant with tool_calls=None
        {"role": "assistant", "content": "", "tool_calls": None},
        # Tool message — should be skipped even if it had a function name
        {"role": "tool", "tool_call_id": "x", "content": "y"},
        _assistant(["a"]),
    ]
    assert _collect_tool_names(history) == ["a"]


# --- expect_tool ---

def test_expect_tool_string_match_passes():
    test_case = {"expect": {"expect_tool": "vault_search"}}
    history = [_assistant(["vault_search"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert passed, reason


def test_expect_tool_string_no_match_fails():
    test_case = {"expect": {"expect_tool": "vault_search"}}
    history = [_assistant(["web_fetch"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert not passed
    # Message should name the expected list and the called tools
    assert "vault_search" in reason
    assert "web_fetch" in reason


def test_expect_tool_list_or_semantics_passes():
    test_case = {"expect": {"expect_tool": ["a", "b"]}}
    history = [_assistant(["b"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert passed, reason


def test_expect_tool_list_no_match_fails():
    test_case = {"expect": {"expect_tool": ["a", "b"]}}
    history = [_assistant(["c"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert not passed
    assert "a" in reason and "b" in reason
    assert "c" in reason


def test_expect_tool_no_tools_called_fails():
    test_case = {"expect": {"expect_tool": "x"}}
    passed, reason = _check(test_case, [])
    assert not passed
    assert "no tools were called" in reason


# --- expect_no_tool ---

def test_expect_no_tool_string_blocks_match():
    test_case = {"expect": {"expect_no_tool": "web_fetch"}}
    history = [_assistant(["web_fetch"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert not passed
    assert "web_fetch" in reason


def test_expect_no_tool_string_passes_when_absent():
    test_case = {"expect": {"expect_no_tool": "web_fetch"}}
    history = [_assistant(["vault_search"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert passed, reason


def test_expect_no_tool_list_and_semantics_passes_when_none_called():
    test_case = {"expect": {"expect_no_tool": ["a", "b"]}}
    history = [_assistant(["c"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert passed, reason


def test_expect_no_tool_list_and_semantics_fails_when_one_called():
    test_case = {"expect": {"expect_no_tool": ["a", "b"]}}
    history = [_assistant(["b"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert not passed
    assert "b" in reason


# --- _collect_tool_calls + expect_tool_args ---

def test_collect_tool_calls_parses_args():
    history = [
        _assistant_with_args([
            ("canvas_new_tab", {"widget_type": "map", "data": {"markers": []}}),
            ("workspace_read", {"path": "x"}),
        ]),
    ]
    detail = _collect_tool_calls(history)
    assert detail == [
        ("canvas_new_tab", {"widget_type": "map", "data": {"markers": []}}),
        ("workspace_read", {"path": "x"}),
    ]


def test_collect_tool_calls_tolerates_bad_json():
    history = [{
        "role": "assistant", "content": "",
        "tool_calls": [
            {"id": "c0", "function": {"name": "t", "arguments": "{not json"}},
        ],
    }]
    # Unparseable args degrade to {} rather than raising.
    assert _collect_tool_calls(history) == [("t", {})]


def test_expect_tool_args_subset_match_passes():
    test_case = {"expect": {
        "expect_tool_args": [{"tool": "canvas_new_tab",
                              "args": {"widget_type": "map"}}]}}
    history = [_assistant_with_args([
        ("canvas_new_tab", {"widget_type": "map", "data": {"markers": []}}),
    ])]
    passed, reason = _check(test_case, history)
    assert passed, reason


def test_expect_tool_args_wrong_value_fails():
    test_case = {"expect": {
        "expect_tool_args": [{"tool": "canvas_new_tab",
                              "args": {"widget_type": "map"}}]}}
    history = [_assistant_with_args([
        ("canvas_new_tab", {"widget_type": "iframe_sandbox", "data": {}}),
    ])]
    passed, reason = _check(test_case, history)
    assert not passed
    assert "widget_type" in reason


def test_expect_tool_args_tool_not_called_fails():
    test_case = {"expect": {
        "expect_tool_args": [{"tool": "canvas_new_tab",
                              "args": {"widget_type": "map"}}]}}
    history = [_assistant(["workspace_write"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert not passed
    assert "canvas_new_tab" in reason


# --- expect_tool_count_by_name ---

def test_count_exact_match_passes():
    test_case = {"expect": {"expect_tool_count_by_name": {"a": 2, "b": 1}}}
    history = [_assistant(["a", "b", "a"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert passed, reason


def test_count_too_few_fails():
    test_case = {"expect": {"expect_tool_count_by_name": {"a": 2}}}
    history = [_assistant(["a"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert not passed
    assert "a" in reason
    assert "2" in reason  # expected
    assert "1" in reason  # got


def test_count_too_many_fails():
    test_case = {"expect": {"expect_tool_count_by_name": {"a": 1}}}
    history = [_assistant(["a", "a"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert not passed
    assert "a" in reason
    assert "1" in reason  # expected
    assert "2" in reason  # got


def test_count_zero_means_not_called_passes_when_absent():
    test_case = {"expect": {"expect_tool_count_by_name": {"web_fetch": 0}}}
    history = [_assistant(["vault_search"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert passed, reason


def test_count_zero_means_not_called_fails_when_called():
    test_case = {"expect": {"expect_tool_count_by_name": {"web_fetch": 0}}}
    history = [_assistant(["web_fetch"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert not passed
    assert "web_fetch" in reason


def test_count_unlisted_tool_unconstrained():
    test_case = {"expect": {"expect_tool_count_by_name": {"a": 1}}}
    history = [_assistant(["a", "b", "b", "b"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert passed, reason


def test_count_with_other_assertions_combine_passes():
    test_case = {
        "expect": {
            "expect_tool": "x",
            "expect_tool_count_by_name": {"x": 2},
        }
    }
    history = [_assistant(["x", "x"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert passed, reason


def test_count_with_other_assertions_combine_fails_on_count():
    test_case = {
        "expect": {
            "expect_tool": "x",
            "expect_tool_count_by_name": {"x": 2},
        }
    }
    # expect_tool passes (x was called), but count is wrong
    history = [_assistant(["x"]), _tool_result()]
    passed, reason = _check(test_case, history)
    assert not passed
    assert "x" in reason
    assert "2" in reason
    assert "1" in reason


# --- Regression tests for existing fields with new kwarg threading ---

def test_response_contains_regression():
    test_case = {"expect": {"response_contains": "hello"}}
    passed, reason = _check(test_case, [], response="well, Hello there")
    assert passed, reason

    test_case = {"expect": {"response_contains": "hello"}}
    passed, reason = _check(test_case, [], response="goodbye")
    assert not passed
    assert "hello" in reason


def test_response_not_contains_regression():
    test_case = {"expect": {"response_not_contains": "secret"}}
    passed, reason = _check(test_case, [], response="all clear here")
    assert passed, reason

    test_case = {"expect": {"response_not_contains": "secret"}}
    passed, reason = _check(test_case, [], response="The Secret password is...")
    assert not passed
    assert "secret" in reason.lower()


def test_max_tool_calls_regression():
    test_case = {"expect": {"max_tool_calls": 2}}
    # Within budget
    passed, _ = _check(test_case, [], tool_calls=2)
    assert passed
    # Over budget
    passed, reason = _check(test_case, [], tool_calls=3)
    assert not passed
    assert "3" in reason and "2" in reason


def test_max_tool_errors_regression():
    test_case = {"expect": {"max_tool_errors": 1}}
    passed, _ = _check(test_case, [], tool_errors=1)
    assert passed
    passed, reason = _check(test_case, [], tool_errors=2)
    assert not passed
    assert "2" in reason and "1" in reason


# --- response_contains_all (AND semantics) ---

def test_response_contains_all_string_passes():
    test_case = {"expect": {"response_contains_all": "hello"}}
    passed, _ = _check(test_case, [], response="hello world")
    assert passed


def test_response_contains_all_list_passes_when_all_present():
    test_case = {"expect": {"response_contains_all": ["foo", "bar"]}}
    passed, _ = _check(test_case, [], response="foo and bar walk into")
    assert passed


def test_response_contains_all_list_fails_when_any_missing():
    test_case = {"expect": {"response_contains_all": ["foo", "bar"]}}
    passed, reason = _check(test_case, [], response="foo alone")
    assert not passed
    # Reason should mention the missing item, not the matched one
    assert "bar" in reason
    assert "missing" in reason.lower()


def test_response_contains_all_is_case_insensitive():
    test_case = {"expect": {"response_contains_all": ["foo", "BAR"]}}
    passed, _ = _check(test_case, [], response="FOO and bar")
    assert passed


def test_response_contains_all_regex_supported():
    test_case = {"expect": {"response_contains_all": ["re:f.+o", "re:b.+r"]}}
    passed, _ = _check(test_case, [], response="foo bar")
    assert passed

    test_case = {"expect": {"response_contains_all": ["re:f.+o", "re:b.+r"]}}
    passed, reason = _check(test_case, [], response="just foo")
    assert not passed
    assert "missing pattern" in reason


def test_response_contains_all_mixed_regex_and_literal():
    test_case = {"expect": {"response_contains_all": ["re:\\d+", "items"]}}
    passed, _ = _check(test_case, [], response="42 items found")
    assert passed


def test_response_contains_all_combines_with_other_assertions():
    """Pass on contains_all, but fail on expect_tool — the combined return value should be the expect_tool failure."""
    test_case = {
        "expect": {
            "response_contains_all": ["foo", "bar"],
            "expect_tool": "x",
        }
    }
    passed, reason = _check(test_case, [], response="foo and bar are here")
    assert not passed
    assert "expected one of ['x']" in reason.lower() or "no tools were called" in reason


# --- Default kwarg behavior (call without tool_names) ---

def test_check_assertions_defaults_when_tool_names_omitted():
    """`tool_names=None` default should still let non-tool-name asserts work."""
    test_case = {"expect": {"response_contains": "ok"}}
    passed, _ = _check_assertions(test_case, "ok", 0)
    assert passed

    # And expect_tool with no tool_names → fails with "no tools were called"
    test_case = {"expect": {"expect_tool": "x"}}
    passed, reason = _check_assertions(test_case, "ok", 0)
    assert not passed
    assert "no tools were called" in reason
