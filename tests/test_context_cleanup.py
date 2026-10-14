"""Tests for tool-result clearing (#298)."""

from __future__ import annotations

import dataclasses

from decafclaw.config_types import CleanupConfig
from decafclaw.context_cleanup import (
    _STUB_PREFIX,
    _SUPERSEDED_STUB,
    ClearStats,
    _build_tool_name_index,
    _user_turn_boundary_indices,
    clear_old_tool_results,
)
from decafclaw.context_composer import ComposerMode


def _cfg(**overrides):
    """Config-shaped object exposing only `.cleanup` for the tests."""
    cleanup = dataclasses.replace(CleanupConfig(), **overrides) if overrides else CleanupConfig()

    class _C:
        pass

    c = _C()
    c.cleanup = cleanup
    return c


def _user(content):
    return {"role": "user", "content": content}


def _assistant(text="", tool_calls=None):
    msg = {"role": "assistant", "content": text}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return msg


def _tool_call(call_id, name):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}


def _tool(call_id, content):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


# -- helpers -------------------------------------------------------------------


class TestUserTurnBoundaryIndices:
    def test_finds_user_turn_indices(self):
        history = [
            _user("first"),
            _assistant("a"),
            _user("second"),
            _assistant("b"),
            _user("third"),
        ]
        assert _user_turn_boundary_indices(history) == [0, 2, 4]

    def test_empty_history(self):
        assert _user_turn_boundary_indices([]) == []


class TestBuildToolNameIndex:
    def test_resolves_via_assistant_tool_calls(self):
        history = [
            _user("hi"),
            _assistant(tool_calls=[_tool_call("c1", "vault_read")]),
            _tool("c1", "..."),
        ]
        index = _build_tool_name_index(history)
        assert index["c1"] == "vault_read"

    def test_missing_when_unresolvable(self):
        """If the assistant message has been compacted away, the call_id
        won't appear in the index. Callers should treat absence as 'name
        unknown.'"""
        history = [_tool("c-orphan", "...")]
        index = _build_tool_name_index(history)
        assert "c-orphan" not in index

    def test_one_pass_no_duplicate_scan(self):
        """Index covers every named call across multiple assistant turns
        in a single pass — no per-message rescan."""
        history = [
            _assistant(tool_calls=[_tool_call("c1", "vault_read")]),
            _tool("c1", "..."),
            _assistant(
                tool_calls=[
                    _tool_call("c2", "web_fetch"),
                    _tool_call("c3", "shell"),
                ]
            ),
            _tool("c2", "..."),
            _tool("c3", "..."),
        ]
        index = _build_tool_name_index(history)
        assert index == {"c1": "vault_read", "c2": "web_fetch", "c3": "shell"}


# -- core eligibility rules ----------------------------------------------------


class TestClearOldToolResults:
    def _scenario(
        self, *, large_content="x" * 2000, min_turn_age=2, min_size=1024, preserve=None, tool_name="vault_read"
    ):
        """Build a history with one old large tool result and the
        boundary structure required for clearing to be eligible."""
        history = [
            # turn 1
            _user("turn1"),
            _assistant(tool_calls=[_tool_call("c1", tool_name)]),
            _tool("c1", large_content),
            _assistant("synthesized that"),
            # turn 2
            _user("turn2"),
            _assistant("ok"),
            # turn 3 (current)
            _user("turn3"),
            _assistant("hi"),
        ]
        kwargs = {"min_turn_age": min_turn_age, "min_size_bytes": min_size}
        if preserve is not None:
            kwargs["preserve_tools"] = preserve
        cfg = _cfg(**kwargs)
        return history, cfg

    def test_clears_old_large_tool_result(self):
        history, cfg = self._scenario()
        stats = clear_old_tool_results(history, cfg)
        assert stats.cleared_count == 1
        assert stats.cleared_bytes > 0
        assert history[2]["content"].startswith(_STUB_PREFIX)
        assert "2000 bytes" in history[2]["content"]

    def test_preserves_recent_turn_results(self):
        """A tool result inside the protected window stays intact."""
        history = [
            _user("turn1"),
            _assistant("ok"),
            # turn 2 — tool call here is within the 2-turn protect window
            _user("turn2"),
            _assistant(tool_calls=[_tool_call("c1", "vault_read")]),
            _tool("c1", "x" * 5000),
            _assistant("done"),
            _user("turn3"),
        ]
        cfg = _cfg()
        stats = clear_old_tool_results(history, cfg)
        assert stats.cleared_count == 0
        assert "x" * 5000 in history[4]["content"]

    def test_skips_small_messages(self):
        history, cfg = self._scenario(large_content="tiny", min_size=1024)
        stats = clear_old_tool_results(history, cfg)
        assert stats.cleared_count == 0
        assert history[2]["content"] == "tiny"

    def test_skips_allowlisted_tool(self):
        history, cfg = self._scenario(
            tool_name="checklist_status",
            preserve=["checklist_status"],
        )
        stats = clear_old_tool_results(history, cfg)
        assert stats.cleared_count == 0
        assert history[2]["content"].startswith("x")

    def test_idempotent_on_already_cleared(self):
        history, cfg = self._scenario()
        first = clear_old_tool_results(history, cfg)
        assert first.cleared_count == 1
        # Re-run on the same history — should not double-count or
        # re-stub.
        second = clear_old_tool_results(history, cfg)
        assert second.cleared_count == 0
        assert second.cleared_bytes == 0

    def test_disabled_config_no_op(self):
        history, cfg = self._scenario()
        cfg.cleanup.enabled = False
        stats = clear_old_tool_results(history, cfg)
        assert stats.cleared_count == 0
        # Original content is untouched.
        assert history[2]["content"] == "x" * 2000

    def test_unresolvable_tool_name_still_eligible(self):
        """If the assistant message that originated the call has been
        compacted away, eligibility still holds — old + large is enough."""
        history = [
            # turn 1 — only the tool message survives (assistant compacted out)
            _user("turn1"),
            _tool("orphan", "x" * 2000),
            # turn 2
            _user("turn2"),
            _assistant("ok"),
            # turn 3
            _user("turn3"),
        ]
        cfg = _cfg()
        stats = clear_old_tool_results(history, cfg)
        assert stats.cleared_count == 1
        assert history[1]["content"].startswith(_STUB_PREFIX)

    def test_preserves_non_content_fields(self):
        """role, tool_call_id, display_short_text, widget all survive
        a clear pass."""
        history, cfg = self._scenario()
        history[2]["display_short_text"] = "fetched 2000-char page"
        history[2]["widget"] = {"type": "data_table", "data": {}}
        clear_old_tool_results(history, cfg)
        assert history[2]["role"] == "tool"
        assert history[2]["tool_call_id"] == "c1"
        assert history[2]["display_short_text"] == "fetched 2000-char page"
        assert history[2]["widget"] == {"type": "data_table", "data": {}}

    def test_does_not_touch_non_tool_messages(self):
        history, cfg = self._scenario()
        # assistant message before the tool call also has 2000 chars
        history[1]["content"] = "x" * 2000
        clear_old_tool_results(history, cfg)
        assert history[1]["content"] == "x" * 2000

    def test_not_enough_user_turns_no_op(self):
        """If we don't have at least min_turn_age user turns, nothing
        is old enough yet."""
        history = [
            _user("turn1"),
            _assistant(tool_calls=[_tool_call("c1", "vault_read")]),
            _tool("c1", "x" * 2000),
        ]
        cfg = _cfg(min_turn_age=2)
        stats = clear_old_tool_results(history, cfg)
        assert stats.cleared_count == 0

    def test_min_turn_age_zero_is_safe_no_op(self):
        """A misconfigured ``min_turn_age=0`` would otherwise index
        ``boundaries[-0]`` and silently clear everything past the first
        user message. Treat it as a no-op instead."""
        history, cfg = self._scenario(min_turn_age=0)
        stats = clear_old_tool_results(history, cfg)
        assert stats.cleared_count == 0
        assert history[2]["content"] == "x" * 2000

    def test_negative_min_turn_age_is_safe_no_op(self):
        history, cfg = self._scenario(min_turn_age=-1)
        stats = clear_old_tool_results(history, cfg)
        assert stats.cleared_count == 0

    def test_non_positive_min_size_bytes_is_safe_no_op(self):
        """``min_size_bytes <= 0`` can't legitimately mean 'clear
        everything,' so guard it as a no-op."""
        history, cfg = self._scenario(min_size=0)
        stats = clear_old_tool_results(history, cfg)
        assert stats.cleared_count == 0

    def test_skips_when_stub_is_no_smaller_than_original(self):
        """Pathological config: ``min_size_bytes`` set below the stub
        length means clearing wouldn't reclaim any bytes. Skip rather
        than mutate the message into something larger."""
        # The stub for a 30-byte original is "[tool output cleared: 30 bytes]"
        # which is itself >30 bytes. Set min_size below the stub length and
        # make the original just barely above min_size — clearing must skip.
        history, cfg = self._scenario(large_content="x" * 20, min_size=10)
        stats = clear_old_tool_results(history, cfg)
        assert stats.cleared_count == 0
        assert stats.cleared_bytes == 0
        assert history[2]["content"] == "x" * 20

    def test_counts_bytes_not_chars_for_non_ascii(self):
        """``min_size_bytes`` and ``cleared_bytes`` are measured in
        UTF-8 bytes, not characters. A multi-byte string counts as more
        bytes than chars."""
        # "🎉" encodes to 4 UTF-8 bytes; 300 of them is 1200 bytes (≥ 1024
        # default min) but only 300 characters.
        emoji_content = "🎉" * 300
        history, cfg = self._scenario(large_content=emoji_content)
        stats = clear_old_tool_results(history, cfg)
        assert stats.cleared_count == 1
        # Stub mentions byte count, not char count.
        assert "1200 bytes" in history[2]["content"]


# -- resolved-error pruning (#540) --------------------------------------------


class TestPruneResolvedErrors:
    """The second, more aggressive pass: a tool error a later same-tool
    success has resolved is stubbed, regardless of size or age."""

    def _tool_call_args(self, call_id, name, arguments):
        return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}

    def _resolved_history(self, *, tool_name="shell", error_args=None, success_args=None):
        """History where tool T errors (c1) then a later call to T succeeds
        (c2). The error body is deliberately small (under the 1024-byte
        ``min_size_bytes``) so only the pruning pass — not the size/age clear
        — can stub it, isolating the behavior under test."""
        err_args = "{}" if error_args is None else error_args
        ok_args = "{}" if success_args is None else success_args
        return [
            _user("turn1"),
            _assistant(tool_calls=[self._tool_call_args("c1", tool_name, err_args)]),
            _tool("c1", "[error: command failed: " + "detail " * 16 + "]"),
            _assistant(tool_calls=[self._tool_call_args("c2", tool_name, ok_args)]),
            _tool("c2", "OK: file read successfully"),
            _assistant("synthesized"),
            _user("turn2"),
            _assistant("ok"),
            _user("turn3"),
            _assistant("hi"),
        ]

    def test_prunes_resolved_error_results(self):
        history = self._resolved_history()
        stats = clear_old_tool_results(history, _cfg())
        # The resolved error is stubbed; the later success is untouched.
        assert history[2]["content"] == _SUPERSEDED_STUB
        assert history[4]["content"] == "OK: file read successfully"
        assert stats.cleared_count == 1
        assert stats.cleared_bytes > 0
        # The stub does not masquerade as an error on a later pass.
        assert not history[2]["content"].startswith("[error")

    def test_prune_resolved_errors_config(self):
        # Enabled (the default): the resolved error is stubbed.
        history = self._resolved_history()
        clear_old_tool_results(history, _cfg())
        assert history[2]["content"] == _SUPERSEDED_STUB

        # Disabled: the resolved error is left untouched.
        history = self._resolved_history()
        cfg = _cfg()
        cfg.cleanup.prune_resolved_errors = False
        clear_old_tool_results(history, cfg)
        assert history[2]["content"].startswith("[error")

    def test_prune_resolved_same_args_only(self):
        # Conservative mode: a later same-tool success with DIFFERENT args
        # does not resolve the error.
        history = self._resolved_history(error_args='{"path": "a"}', success_args='{"path": "b"}')
        cfg = _cfg()
        cfg.cleanup.prune_resolved_same_args_only = True
        clear_old_tool_results(history, cfg)
        assert history[2]["content"].startswith("[error")

        # A later same-tool success with the SAME args (even reordered /
        # re-spaced JSON) does resolve it.
        history = self._resolved_history(error_args='{"path": "a"}', success_args='{"path":"a"}')
        cfg = _cfg()
        cfg.cleanup.prune_resolved_same_args_only = True
        clear_old_tool_results(history, cfg)
        assert history[2]["content"] == _SUPERSEDED_STUB

        # Default (same-tool-only) ignores args entirely.
        history = self._resolved_history(error_args='{"path": "a"}', success_args='{"path": "b"}')
        clear_old_tool_results(history, _cfg())
        assert history[2]["content"] == _SUPERSEDED_STUB

    def test_unresolved_error_not_pruned(self):
        """An error with no later same-tool success stays as-is."""
        history = [
            _user("turn1"),
            _assistant(tool_calls=[self._tool_call_args("c1", "shell", "{}")]),
            _tool("c1", "[error: command failed: " + "detail " * 16 + "]"),
            _assistant("gave up"),
            _user("turn2"),
            _assistant("ok"),
        ]
        clear_old_tool_results(history, _cfg())
        assert history[2]["content"].startswith("[error")

    def test_tiny_resolved_error_not_pruned(self):
        """A resolved error smaller than the stub is left as-is — stubbing
        it would make the in-memory view *larger* (mirrors the size/age
        clear's pathological-case skip)."""
        history = [
            _user("turn1"),
            _assistant(tool_calls=[self._tool_call_args("c1", "shell", "{}")]),
            _tool("c1", "[error: denied]"),
            _assistant(tool_calls=[self._tool_call_args("c2", "shell", "{}")]),
            _tool("c2", "OK"),
            _assistant("done"),
            _user("turn2"),
            _assistant("ok"),
            _user("turn3"),
        ]
        clear_old_tool_results(history, _cfg())
        assert history[2]["content"] == "[error: denied]"

    def test_different_tools_not_cross_pruned(self):
        """A success of a different tool must not resolve an earlier error."""
        history = self._resolved_history(tool_name="shell")
        # c2 is a shell success; add a separate error for a DIFFERENT tool
        # (web_fetch) with no later web_fetch success — must stay.
        history.insert(1, _assistant(tool_calls=[self._tool_call_args("c0", "web_fetch", "{}")]))
        history.insert(2, _tool("c0", "[error: fetch failed: " + "detail " * 16 + "]"))
        clear_old_tool_results(history, _cfg())
        # web_fetch error (now at index 2) is unresolved; shell error (now
        # at index 4) is resolved by the later shell success.
        assert history[2]["content"].startswith("[error")
        assert history[4]["content"] == _SUPERSEDED_STUB

    def test_prune_skipped_in_non_interactive_modes(self):
        """Heartbeat / scheduled / child turns skip pruning, like memory
        retrieval. The size/age clear is unaffected by mode."""
        for mode in (ComposerMode.HEARTBEAT, ComposerMode.SCHEDULED, ComposerMode.CHILD_AGENT):
            history = self._resolved_history()
            clear_old_tool_results(history, _cfg(), mode=mode)
            assert history[2]["content"].startswith("[error"), f"expected no prune in {mode}"
        history = self._resolved_history()
        clear_old_tool_results(history, _cfg(), mode=ComposerMode.INTERACTIVE)
        assert history[2]["content"] == _SUPERSEDED_STUB

    def test_prune_idempotent(self):
        history = self._resolved_history()
        first = clear_old_tool_results(history, _cfg())
        assert first.cleared_count == 1
        second = clear_old_tool_results(history, _cfg())
        assert second.cleared_count == 0
        assert second.cleared_bytes == 0
        assert history[2]["content"] == _SUPERSEDED_STUB


class TestClearStats:
    def test_merge_accumulates(self):
        a = ClearStats(cleared_count=2, cleared_bytes=1000)
        b = ClearStats(cleared_count=3, cleared_bytes=2500)
        a.merge(b)
        assert a.cleared_count == 5
        assert a.cleared_bytes == 3500
