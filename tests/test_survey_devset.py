"""Tests for deterministic dev-set sampling (#1738)."""

from decafclaw.survey.corpus import TurnBoundary, analyze_offer
from decafclaw.survey.devset import (
    build_devset,
    length_bucket,
    load_devset,
    recency_bucket,
    sample_boundaries,
    write_devset,
)


def _boundary(conv_id, position, *, user_turns=2, timestamp="2026-01-01T00:00:00"):
    return TurnBoundary(
        conv_id=conv_id,
        turn_id=f"{conv_id}#u{position}",
        boundary_index=position,
        timestamp=timestamp,
        transcript_tail=f"tail {conv_id} {position}",
        actual_next=f"next {position}",
        previous_user="previous",
        assistant_before="assistant reply",
        active_model="model",
        user_turns=user_turns,
        offered=analyze_offer("assistant reply"),
    )


def _corpus():
    boundaries = []
    for index in range(6):
        boundaries.append(_boundary(f"conv-{index}", 2, user_turns=2, timestamp=f"2026-01-0{index + 1}T00:00:00"))
    for index in range(4):
        boundaries.append(_boundary(f"long-{index}", 7, user_turns=12, timestamp=f"2026-02-0{index + 1}T00:00:00"))
    return boundaries


def test_recency_buckets_follow_timestamps_not_conv_ids():
    boundaries = [
        _boundary("z-old", 2, timestamp="2026-01-01T00:00:00"),
        _boundary("a-mid", 2, timestamp="2026-02-01T00:00:00"),
        _boundary("m-new", 2, timestamp="2026-03-01T00:00:00"),
    ]
    assert recency_bucket(boundaries) == {"z-old": "old", "a-mid": "mid", "m-new": "new"}


def test_length_buckets():
    assert length_bucket(2) == "2"
    assert length_bucket(3) == "3-5"
    assert length_bucket(5) == "3-5"
    assert length_bucket(6) == "6-9"
    assert length_bucket(10) == "10+"


def test_sampling_is_deterministic_and_bounded():
    corpus = _corpus()
    first = sample_boundaries(corpus, limit=5, seed=1738)
    second = sample_boundaries(corpus, limit=5, seed=1738)
    assert [item.turn_id for item in first] == [item.turn_id for item in second]
    assert len(first) == 5
    assert len({item.turn_id for item in first}) == 5


def test_sampling_changes_with_seed():
    corpus = _corpus()
    first = [item.turn_id for item in sample_boundaries(corpus, limit=5, seed=1)]
    second = [item.turn_id for item in sample_boundaries(corpus, limit=5, seed=2)]
    assert first != second


def test_sampling_caps_at_corpus_size():
    corpus = _corpus()
    assert len(sample_boundaries(corpus, limit=100, seed=1738)) == len(corpus)


def test_build_devset_adds_trace_and_stratum():
    cases = build_devset(_corpus(), limit=4, seed=1738)
    assert len(cases) == 4
    for case in cases:
        assert case["source"] == "archive"
        assert case["turn_id"] and case["conv_id"]
        assert case["actual_next_user_message"]
        assert case["transcript_tail"]
        assert set(case["stratum"]) == {"recency", "length"}


def test_devset_round_trip(tmp_path):
    cases = build_devset(_corpus(), limit=3, seed=1738)
    path = tmp_path / "devset.jsonl"
    write_devset(path, cases)
    loaded = load_devset(path)
    assert loaded == cases
