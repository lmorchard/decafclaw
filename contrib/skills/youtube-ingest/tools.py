"""YouTube contrib skill — on-demand and scheduled watch history transcript ingest."""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from youtube_transcript_api import YouTubeTranscriptApi

from decafclaw.context import TaskMode
from decafclaw.frontmatter import serialize_frontmatter
from decafclaw.media import ToolResult
from decafclaw.skills.vault.tools import (
    tool_vault_journal_append,
    tool_vault_read,
    tool_vault_write,
)

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from decafclaw.context import Context

_config = None
_skill_config: "SkillConfig | None" = None


@dataclass
class SkillConfig:
    enabled: bool = field(default=False, metadata={"env_alias": "YOUTUBE_INGEST_ENABLED"})
    cdp_url: str = field(
        default="http://myriad-docker:9222",
        metadata={"env_alias": "YOUTUBE_CDP_URL"},
    )
    vault_subfolder: str = field(
        default="agent/pages/youtube",
        metadata={"env_alias": "YOUTUBE_VAULT_SUBFOLDER"},
    )
    min_watch_percent: float = field(
        default=0.5,
        metadata={"env_alias": "YOUTUBE_MIN_WATCH_PERCENT"},
    )
    max_videos_per_sync: int = field(
        default=10,
        metadata={"env_alias": "YOUTUBE_MAX_PER_SYNC"},
    )
    default_languages: tuple[str, ...] = field(
        default=("en",),
        metadata={"env_alias": "YOUTUBE_LANGUAGES"},
    )


def init(config, skill_config: SkillConfig) -> None:
    """Initialize the youtube-ingest skill."""
    global _config, _skill_config
    _config = config
    _skill_config = skill_config


# TOOLS and TOOL_DEFINITIONS are populated at the bottom of this module
TOOLS: dict = {}
TOOL_DEFINITIONS: list = []


@dataclass
class VideoEntry:
    video_id: str
    title: str
    channel: str
    url: str
    watch_percent: float


def parse_video_id(url_or_id: str) -> str:
    """Extract an 11-character YouTube video ID from various URL formats or bare ID."""
    text = url_or_id.strip()
    if re.fullmatch(r"[\w-]{11}", text):
        return text
    patterns = [
        r"(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/)([\w-]{11})",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1)
    raise ValueError(f"Invalid or unrecognized YouTube video URL/ID: {url_or_id}")


def format_timestamp(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS string."""
    total_sec = max(0, int(seconds))
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _slug(text: str) -> str:
    """Convert title to a clean URL/filesystem slug."""
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:60] if text else "video"


def _video_page_path(subfolder: str, video_id: str, title: str) -> str:
    folder = subfolder.rstrip("/")
    return f"{folder}/{video_id}-{_slug(title)}.md"


async def _fetch_history_from_cdp(cdp_url: str, min_watch_percent: float = 0.5, limit: int = 15) -> list[VideoEntry]:
    """Connect over CDP via HTTP and WebSocket to extract recent watch history from YouTube."""
    if not cdp_url:
        raise RuntimeError("No CDP URL configured for YouTube history extraction")

    import json

    import httpx
    import websockets

    base_url = cdp_url.rstrip("/")
    # 1. Inspect existing open tabs via /json/list
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{base_url}/json/list")
        resp.raise_for_status()
        tabs = resp.json()

    # Find open watch history tab or open a new one
    target_tab = next((t for t in tabs if "youtube.com/feed/history" in t.get("url", "")), None)
    opened_tab_id = None
    if not target_tab:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(f"{base_url}/json/new?https://www.youtube.com/feed/history")
            resp.raise_for_status()
            target_tab = resp.json()
            opened_tab_id = target_tab.get("id")
            await asyncio.sleep(3.0)  # give it a moment to load DOM

    ws_url = target_tab.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("Target tab has no webSocketDebuggerUrl")

    js = """
    (() => {
        const results = [];
        // 1. Modern YouTube desktop UI: yt-lockup-view-model
        for (const vm of document.querySelectorAll("yt-lockup-view-model")) {
            const a = vm.querySelector('a[href*="/watch"], a[href*="/shorts"]');
            if (!a) continue;
            const href = a.getAttribute("href") || "";
            const lines = vm.innerText.split("\\n").map(l => l.trim()).filter(Boolean);
            const progress = vm.querySelector('.ytThumbnailOverlayProgressBarHostProgressBar, [class*="ProgressBar"], [style*="width"]');
            let wp = 0.0;
            if (progress && progress.style && progress.style.width) {
                const w = progress.style.width;
                if (w.endsWith("%")) {
                    wp = parseFloat(w) / 100.0;
                } else if (w.endsWith("px")) {
                    wp = Math.min(1.0, parseFloat(w) / 246.0);
                }
            }
            results.push({
                title: lines.length > 1 ? lines[1] : (lines[0] || ""),
                channel: lines.length > 2 ? lines[2] : "",
                url: href,
                watch_percent: wp
            });
        }
        // 2. Legacy / fallback: ytd-video-renderer
        if (results.length === 0) {
            for (const n of document.querySelectorAll("ytd-video-renderer")) {
                const titleEl = n.querySelector("#video-title");
                const channelEl = n.querySelector("#channel-name, .ytd-channel-name");
                const progressEl = n.querySelector("#progress");
                let wp = 0.0;
                if (progressEl && progressEl.style && progressEl.style.width) {
                    const m = progressEl.style.width.match(/([0-9.]+)%/);
                    if (m) wp = parseFloat(m[1]) / 100.0;
                }
                results.push({
                    title: titleEl ? titleEl.textContent.trim() : "",
                    url: titleEl ? titleEl.getAttribute("href") : "",
                    channel: channelEl ? channelEl.textContent.trim().split("\\n")[0].trim() : "",
                    watch_percent: wp
                });
            }
        }
        return results;
    })()
    """

    async with websockets.connect(ws_url) as ws:
        msg = {
            "id": 101,
            "method": "Runtime.evaluate",
            "params": {"expression": js, "returnByValue": True},
        }
        await ws.send(json.dumps(msg))
        raw_res = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
        raw_entries = raw_res.get("result", {}).get("result", {}).get("value", [])

    if opened_tab_id:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.get(f"{base_url}/json/close/{opened_tab_id}")
        except Exception as close_exc:
            log.debug("Failed to close temporary tab: %s", close_exc)

    entries: list[VideoEntry] = []
    for item in raw_entries:
        raw_url = item.get("url") or ""
        try:
            vid = parse_video_id(raw_url)
        except ValueError:
            continue
        wp = float(item.get("watch_percent", 0.0))
        if min_watch_percent > 0 and wp < min_watch_percent:
            continue
        full_url = (
            f"https://www.youtube.com/watch?v={vid}"
            if raw_url.startswith(("/watch", "/shorts"))
            else f"https://www.youtube.com{raw_url}"
        )
        entries.append(
            VideoEntry(
                video_id=vid,
                title=item.get("title") or vid,
                channel=item.get("channel") or "Unknown Channel",
                url=full_url,
                watch_percent=wp,
            )
        )
        if len(entries) >= limit:
            break

    return entries


def _fetch_transcript(video_id: str, languages: tuple[str, ...] = ("en",)) -> list[dict[str, Any]]:
    """Fetch timestamped transcript segments for a video using youtube-transcript-api."""
    api = YouTubeTranscriptApi()
    try:
        transcript = api.fetch(video_id, languages=list(languages))
        results = []
        for snippet in transcript:
            results.append(
                {
                    "text": snippet.text,
                    "start": snippet.start,
                    "duration": snippet.duration,
                }
            )
        return results
    except Exception as exc:
        log.warning("Failed to fetch transcript for video %s: %s", video_id, exc)
        raise


def _render_youtube_page(
    video: VideoEntry,
    snippets: list[dict[str, Any]],
    now: datetime,
) -> str:
    """Format frontmatter and markdown body for an ingested video transcript."""
    metadata: dict[str, Any] = {
        "video_id": video.video_id,
        "title": video.title,
        "channel": video.channel,
        "url": video.url,
        "watch_percent": round(video.watch_percent, 2),
        "tags": ["ingested", "youtube"],
        "summary": f"YouTube video transcript from '{video.title}' by {video.channel}",
        "importance": 0.5,
        "keywords": [],
        "last_synced": now.replace(microsecond=0).isoformat(),
    }

    body_lines: list[str] = [
        f"**Channel:** [[{video.channel}]] · [Watch on YouTube]({video.url})",
        "",
        "## Full Transcript",
        "",
    ]

    for s in snippets:
        ts = format_timestamp(s.get("start", 0))
        text = s.get("text", "").replace("\n", " ").strip()
        body_lines.append(f"> **{ts}** {text}")
        body_lines.append("")

    return serialize_frontmatter(metadata, "\n".join(body_lines).rstrip() + "\n")


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def youtube_ingest_video(ctx: "Context", url_or_id: str) -> ToolResult:
    """Fetch the transcript for a YouTube video and save it into the vault."""
    if _skill_config is None:
        return ToolResult(text="[error: youtube-ingest skill not initialized]")

    try:
        video_id = parse_video_id(url_or_id)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]")

    try:
        loop = asyncio.get_running_loop()
        snippets = await loop.run_in_executor(None, _fetch_transcript, video_id, _skill_config.default_languages)
    except Exception as exc:
        return ToolResult(text=f"[error: transcript unavailable for {video_id}: {exc}]")

    video = VideoEntry(
        video_id=video_id,
        title=f"YouTube Video {video_id}",
        channel="YouTube",
        url=f"https://www.youtube.com/watch?v={video_id}",
        watch_percent=1.0,
    )

    page_path = _video_page_path(_skill_config.vault_subfolder, video.video_id, video.title)
    now = datetime.now(timezone.utc)
    md_content = _render_youtube_page(video, snippets, now)

    write_result = await tool_vault_write(ctx, page=page_path, content=md_content)
    if isinstance(write_result, ToolResult) and write_result.text.startswith("[error:"):
        return write_result

    return ToolResult(
        text=f"Ingested transcript for '{video.title}' ({video_id}) into [[{page_path}]].",
        data={
            "video_id": video_id,
            "page": page_path,
            "snippet_count": len(snippets),
        },
    )


async def youtube_sync_history(
    ctx: "Context", max_count: int | None = None, min_watch_percent: float | None = None
) -> ToolResult:
    """Sync videos from YouTube watch history (via agent browser over CDP) into the vault."""
    if _skill_config is None:
        return ToolResult(text="[error: youtube-ingest skill not initialized]")

    if ctx.task_mode is TaskMode.SCHEDULED and not _skill_config.enabled:
        return ToolResult(
            text="youtube-ingest skill disabled; skipping (set skills.youtube_ingest.enabled=true to opt in)"
        )

    limit = max_count or _skill_config.max_videos_per_sync
    threshold = min_watch_percent if min_watch_percent is not None else _skill_config.min_watch_percent

    try:
        entries = await _fetch_history_from_cdp(_skill_config.cdp_url, min_watch_percent=threshold, limit=limit)
    except Exception as exc:
        log.warning("CDP watch history fetch failed: %s", exc)
        return ToolResult(text=f"[error: failed to fetch YouTube history over CDP: {exc}]")

    ingested = []
    skipped = 0
    failures = []

    now = datetime.now(timezone.utc)
    for video in entries:
        page_path = _video_page_path(_skill_config.vault_subfolder, video.video_id, video.title)

        # Check deduplication
        existing = await tool_vault_read(ctx, page_path)
        if isinstance(existing, ToolResult) and not existing.text.startswith("[error:"):
            skipped += 1
            continue

        try:
            loop = asyncio.get_running_loop()
            snippets = await loop.run_in_executor(
                None, _fetch_transcript, video.video_id, _skill_config.default_languages
            )
            md_content = _render_youtube_page(video, snippets, now)
            w_res = await tool_vault_write(ctx, page=page_path, content=md_content)
            if isinstance(w_res, ToolResult) and w_res.text.startswith("[error:"):
                failures.append((video.video_id, w_res.text))
            else:
                ingested.append(video)
        except Exception as exc:
            failures.append((video.video_id, str(exc)))

    journal_body = (
        f"**YouTube watch history ingest**\n\n"
        f"- Entries scanned: {len(entries)}\n"
        f"- Ingested: {len(ingested)}\n"
        f"- Already in vault (skipped): {skipped}\n"
        f"- Failures: {len(failures)}\n"
    )
    if failures:
        journal_body += "\n**Failures:**\n"
        for vid, msg in failures:
            journal_body += f"- {vid}: {msg}\n"

    await tool_vault_journal_append(ctx, tags=["ingested", "youtube"], content=journal_body)

    summary = (
        f"YouTube history sync complete: {len(ingested)} ingested, {skipped} already present, {len(failures)} failures."
    )
    return ToolResult(
        text=summary,
        data={
            "scanned": len(entries),
            "ingested_count": len(ingested),
            "skipped_count": skipped,
            "failures": failures,
        },
    )


TOOLS = {
    "youtube_sync_history": youtube_sync_history,
    "youtube_ingest_video": youtube_ingest_video,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "youtube_sync_history",
            "description": (
                "Sync recently watched videos from YouTube watch history (via agent browser on CDP) "
                "into per-video vault pages under agent/pages/youtube/."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_count": {
                        "type": "integer",
                        "description": "Maximum number of videos to inspect/ingest (default: 10).",
                    },
                    "min_watch_percent": {
                        "type": "number",
                        "description": "Minimum watch progress fraction to qualify (default: 0.5 for 50%).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "youtube_ingest_video",
            "description": "Fetch the transcript for a YouTube video URL or ID and save it to the vault.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url_or_id": {
                        "type": "string",
                        "description": "YouTube video URL (watch, youtu.be, shorts, embed) or bare 11-char video ID.",
                    },
                },
                "required": ["url_or_id"],
            },
        },
    },
]
