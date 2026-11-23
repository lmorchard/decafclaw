"""Tests for survey turn-boundary extraction and option analysis (#1738)."""

from decafclaw.archive import write_archive
from decafclaw.survey.corpus import (
    analyze_offer,
    boundaries_for_conversation,
    is_whole_repeat,
    looks_like_stop,
    message_text,
    normalize_text,
    page_key,
    recapitulation_kind,
    render_transcript_tail,
    scan_corpus,
)


def _user(text, **extra):
    return {"role": "user", "content": text, "timestamp": "2026-01-01T00:00:00", **extra}


def _assistant(text, **extra):
    return {"role": "assistant", "content": text, "timestamp": "2026-01-01T00:00:01", **extra}


def _tool(text="tool output"):
    return {"role": "tool", "content": text, "timestamp": "2026-01-01T00:00:02"}


def test_synthetic_user_messages_do_not_start_or_end_boundaries():
    messages = [
        _user("first real"),
        _assistant("reply one"),
        _user("[reflection] retry", source="reflection_retry"),
        _assistant("reflection reply"),
        _user("second real"),
        _assistant("reply two"),
        _user("widget pick", source="widget_response"),
    ]
    boundaries = boundaries_for_conversation("conv", messages, default_model="m")
    assert len(boundaries) == 1
    assert boundaries[0].actual_next == "second real"
    assert boundaries[0].previous_user == "first real"
    assert boundaries[0].assistant_before == "reflection reply"
    assert boundaries[0].user_turns == 2


def test_boundary_carries_tail_provenance_and_active_model():
    messages = [
        {"role": "model", "content": "conv-model", "timestamp": "2026-01-01T00:00:00"},
        _user("first"),
        _assistant("reply one"),
        _tool("ignored tool body"),
        _user("second"),
        _assistant(
            "answer",
            provenance={"vault": [{"page": "agent/pages/Alpha.md"}, {"page": "agent/pages/Beta.md"}]},
        ),
        _assistant(
            "follow-up",
            provenance={"vault": [{"page": "agent/pages/Beta.md"}, {"page": "agent/pages/Gamma.md"}]},
        ),
        _user("third"),
    ]
    boundaries = boundaries_for_conversation("conv", messages, default_model="default")
    second = boundaries[0]
    assert second.actual_next == "second"
    assert second.active_model == "conv-model"
    assert second.provenance_pages == ("agent/pages/Alpha.md", "agent/pages/Beta.md", "agent/pages/Gamma.md")
    assert "ignored tool body" not in second.transcript_tail
    assert "Human: first" in second.transcript_tail
    assert "Assistant: reply one" in second.transcript_tail


def test_active_model_falls_back_to_default():
    messages = [_user("first"), _assistant("a"), _user("second"), _assistant("b")]
    boundaries = boundaries_for_conversation("conv", messages, default_model="fallback")
    assert boundaries[0].active_model == "fallback"


def test_transcript_tail_truncates_from_the_front():
    messages = [_user("x" * 5000), _assistant("y" * 5000), _user("next")]
    tail = render_transcript_tail(messages, 2, max_chars=200, max_message_chars=150)
    assert len(tail) <= 201
    assert tail.startswith("…")
    assert "Assistant:" in tail


def test_message_text_skips_tool_call_only_messages():
    assert message_text({"role": "assistant", "content": None, "tool_calls": [{}]}) == ""
    assert message_text({"role": "assistant", "content": "None"}) == ""
    assert message_text({"role": "tool", "content": "x"}) == ""


def test_analyze_offer_checkbox_and_header():
    checkbox = analyze_offer("Which do you want?\n- [ ] Option A\n- [x] Option B")
    assert checkbox.offers_choice is True
    assert checkbox.checkbox_options == ("Option A", "Option B")

    header = analyze_offer("Next steps:\n1. Commit the change\n2. Open a PR\n")
    assert header.offers_choice is True
    assert header.options == ("Commit the change", "Open a PR")


def test_analyze_offer_requires_a_cue_for_plain_bullets():
    content = analyze_offer("Here is what I found:\n- The file is missing\n- The test passes\n")
    assert content.offers_choice is False
    assert content.options == ("The file is missing", "The test passes")


def test_analyze_offer_cue_plus_question():
    offer = analyze_offer(
        "I can do either. Would you like me to run the tests?\n- run the fast tests\n- run the full suite"
    )
    assert offer.offers_choice is True
    assert offer.ends_with_question is True


def test_recapitulation_kinds():
    offer = analyze_offer("Next steps:\n- Commit the change\n- Open a PR")
    assert recapitulation_kind("Commit the change", offer) == "option"
    assert recapitulation_kind("commit change", offer) == "option"
    assert recapitulation_kind("Yes please", offer) is None

    checkbox = analyze_offer("Pick one:\n- [ ] Retry the shell\n- [ ] Skip it")
    assert recapitulation_kind("Skip it", checkbox) == "checkbox"

    inline = analyze_offer("Should I use the local model or the cloud model?")
    assert inline.offers_choice is True
    assert recapitulation_kind("the cloud model", inline) == "inline"


def test_whole_repeat_and_stop_detection():
    assert is_whole_repeat("ls -al ~", "I ran `ls -al ~` again.") is True
    assert is_whole_repeat("yes", "yes") is False
    assert looks_like_stop("Thanks!") is True
    assert looks_like_stop("No thanks, that's all") is True
    assert looks_like_stop("Please open a PR") is False


def test_page_key_normalization():
    assert page_key("agent/pages/AI Agents.md") == "ai agents"
    assert page_key("AI Agents") == "ai agents"
    assert page_key("user/Notes.md") == "notes"


def test_scan_corpus_counts_final_stops(config):
    write_archive(
        config,
        "conv-a",
        [
            _user("do the thing"),
            _assistant("done"),
            _user("thanks"),
        ],
    )
    write_archive(
        config,
        "conv-b",
        [
            _user("first"),
            _assistant("reply"),
            _user("second"),
            _assistant("reply two"),
        ],
    )
    write_archive(config, "one-shot", [_user("scheduled prompt"), _assistant("ok")])
    scan = scan_corpus(config)
    assert scan.conversations == 3
    assert scan.conversations_with_boundaries == 2
    assert len(scan.boundaries) == 2
    assert scan.final_user_turns == 2
    assert scan.final_turns_that_stop == 1


def test_normalize_text():
    assert normalize_text("  **Hello,   WORLD!** ") == "hello world"
