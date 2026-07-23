"""Tests for the rss-ingest fetch_feeds pure logic (importlib-loaded)."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_THIS_DIR = Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "decafclaw_contrib_rss_fetch_feeds", _THIS_DIR / "fetch_feeds.py"
)
assert _spec is not None and _spec.loader is not None
ff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ff)

UTC = timezone.utc


def _entry(guid, published, feed_name="Blog", title="t", link="http://x/1", summary="s"):
    return {"guid": guid, "title": title, "link": link,
            "published": published, "summary": summary, "feed_name": feed_name}


def test_parse_feeds_txt_handles_comments_blanks_and_names():
    text = "\n".join([
        "# a comment",
        "",
        "https://a.example/feed.xml",
        "Simon|https://b.example/atom.xml",
        "   ",
    ])
    assert ff.parse_feeds_txt(text) == [
        ("https://a.example/feed.xml", None),
        ("https://b.example/atom.xml", "Simon"),
    ]


def test_select_new_entries_first_run_uses_24h_window():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    old = _entry("g-old", now - timedelta(hours=48))
    fresh = _entry("g-new", now - timedelta(hours=2))
    out = ff.select_new_entries([old, fresh], {}, now)
    assert [e["guid"] for e in out] == ["g-new"]


def test_select_new_entries_filters_by_last_published():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    state = {"last_published": "2026-07-23T06:00:00+00:00", "seen_guids": []}
    older = _entry("g1", now - timedelta(hours=12))   # before last_published
    newer = _entry("g2", now - timedelta(hours=1))
    out = ff.select_new_entries([older, newer], state, now)
    assert [e["guid"] for e in out] == ["g2"]


def test_select_new_entries_dedupes_seen_guids():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    state = {"last_published": "2026-07-23T00:00:00+00:00", "seen_guids": ["g2"]}
    e = _entry("g2", now - timedelta(hours=1))
    assert ff.select_new_entries([e], state, now) == []


def test_render_markdown_groups_by_feed_and_shows_fields():
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    md = ff.render_markdown({"Blog": [_entry("g", now, title="Hello", link="http://x/p")]})
    assert "## Blog" in md
    assert "Hello" in md
    assert "http://x/p" in md
    assert "2026-07-23" in md


def test_save_state_caps_seen_guids(tmp_path):
    p = tmp_path / "state.json"
    guids = [f"g{i}" for i in range(300)]
    ff.save_state(p, {"feed": {"last_published": "2026-07-23T00:00:00+00:00",
                               "seen_guids": guids}}, guid_cap=200)
    loaded = ff.load_state(p)
    assert len(loaded["feed"]["seen_guids"]) == 200
    assert loaded["feed"]["seen_guids"][-1] == "g299"  # keeps most recent


def test_feeds_add_is_idempotent_and_remove_works():
    text = "https://a.example/feed.xml\n"
    added = ff.feeds_add(text, "https://b.example/x.xml", "B")
    assert "B|https://b.example/x.xml" in added
    assert ff.feeds_add(added, "https://b.example/x.xml", "B") == added  # idempotent
    removed = ff.feeds_remove(added, "https://b.example/x.xml")
    assert "b.example" not in removed
    assert "a.example" in removed


import pytest


def test_parse_feed_rss_fixture():
    pytest.importorskip("feedparser")  # per-test: absent in the project env
    raw = (_THIS_DIR / "fixtures" / "sample_rss.xml").read_text()
    entries = ff.parse_feed(raw, "Sample Blog")
    assert len(entries) == 2
    first = entries[0]
    assert first["title"] == "First Post"
    assert first["link"] == "https://blog.example/first"
    assert first["guid"] == "https://blog.example/first"
    assert first["feed_name"] == "Sample Blog"
    assert first["published"].year == 2026
    assert "summary" in first["summary"].lower()


def test_parse_feed_atom_fixture_uses_id_and_updated():
    pytest.importorskip("feedparser")  # per-test: absent in the project env
    raw = (_THIS_DIR / "fixtures" / "sample_atom.xml").read_text()
    entries = ff.parse_feed(raw, "Sample Atom")
    assert len(entries) == 1
    assert entries[0]["guid"] == "urn:atom:e1"
    assert entries[0]["link"] == "https://atom.example/e1"
    assert entries[0]["published"] is not None
