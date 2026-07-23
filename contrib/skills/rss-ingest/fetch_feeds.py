#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["feedparser"]
# ///
"""Fetch new RSS/Atom feed items as markdown for the rss-ingest skill.

feedparser is used ONLY inside parse_feed() (lazy import) so the rest of this
module imports cleanly in the project test env, where feedparser is absent.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
FIRST_RUN_WINDOW = timedelta(hours=24)
DEFAULT_GUID_CAP = 200


def parse_feeds_txt(text: str) -> list[tuple[str, str | None]]:
    """Parse feeds.txt: one feed per line, `#` comments + blanks ignored,
    optional `name|url`."""
    feeds: list[tuple[str, str | None]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            name, url = line.split("|", 1)
            feeds.append((url.strip(), name.strip() or None))
        else:
            feeds.append((line, None))
    return feeds


def _as_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def select_new_entries(entries: list[dict], feed_state: dict, now: datetime) -> list[dict]:
    """Return entries newer than feed_state['last_published'] (or the 24h
    window on first run), excluding already-seen guids. Sorted oldest-first."""
    last_pub = _as_dt(feed_state.get("last_published"))
    cutoff = last_pub if last_pub is not None else now - FIRST_RUN_WINDOW
    seen = set(feed_state.get("seen_guids", []))
    out = []
    for e in entries:
        pub = e.get("published")
        if e["guid"] in seen:
            continue
        if pub is not None and pub <= cutoff:
            continue
        out.append(e)
    out.sort(key=lambda e: (e["published"] or now))
    return out


def render_markdown(entries_by_feed: dict[str, list[dict]]) -> str:
    """Render selected entries to markdown grouped by feed name."""
    blocks: list[str] = []
    for feed_name, entries in entries_by_feed.items():
        if not entries:
            continue
        lines = [f"## {feed_name}", ""]
        for e in entries:
            pub = e.get("published")
            date_str = pub.strftime("%Y-%m-%d") if pub else "(no date)"
            lines.append(f"### {e['title']}")
            lines.append(f"- {date_str} — {e['link']}")
            if e.get("summary"):
                lines.append("")
                lines.append(e["summary"].strip())
            lines.append("")
        blocks.append("\n".join(lines))
    return "\n".join(blocks).strip() + ("\n" if blocks else "")


def parse_feed(raw: str, feed_name: str) -> list[dict]:
    """Parse raw RSS/Atom text into normalized Entry dicts.

    The ONLY feedparser-dependent function — imported lazily so the module
    stays importable where feedparser is absent (project test env).
    """
    import feedparser  # lazy: confined to this adapter

    parsed = feedparser.parse(raw)
    entries: list[dict] = []
    for e in parsed.entries:
        struct = e.get("published_parsed") or e.get("updated_parsed")
        published = datetime(*struct[:6], tzinfo=UTC) if struct is not None else None
        guid = e.get("id") or e.get("guid") or e.get("link") or ""
        entries.append({
            "guid": guid,
            "title": e.get("title", "(untitled)"),
            "link": e.get("link", ""),
            "published": published,
            "summary": e.get("summary", ""),
            "feed_name": feed_name,
        })
    return entries


def load_state(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(path: str | Path, state: dict, *, guid_cap: int = DEFAULT_GUID_CAP) -> None:
    capped = {}
    for feed_url, fs in state.items():
        guids = list(fs.get("seen_guids", []))[-guid_cap:]
        capped[feed_url] = {"last_published": fs.get("last_published"), "seen_guids": guids}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(capped, indent=2))


def feeds_add(text: str, url: str, name: str | None = None) -> str:
    """Append a feed to feeds.txt text if its URL isn't already present."""
    existing = {u for u, _ in parse_feeds_txt(text)}
    if url in existing:
        return text
    line = f"{name}|{url}" if name else url
    sep = "" if text.endswith("\n") or not text else "\n"
    return f"{text}{sep}{line}\n"


def feeds_remove(text: str, url: str) -> str:
    """Remove any line referencing url from feeds.txt text."""
    kept = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            kept.append(raw)
            continue
        this_url = line.split("|", 1)[1].strip() if "|" in line else line
        if this_url == url:
            continue
        kept.append(raw)
    return "\n".join(kept) + ("\n" if kept else "")
