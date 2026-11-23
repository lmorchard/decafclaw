"""Unit tests for the youtube-ingest contrib skill."""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.context import Context, TaskMode
from decafclaw.media import ToolResult

_THIS_DIR = Path(__file__).parent
_tools_spec = importlib.util.spec_from_file_location("decafclaw_contrib_youtube_ingest_tools", _THIS_DIR / "tools.py")
assert _tools_spec is not None and _tools_spec.loader is not None
youtube_tools = importlib.util.module_from_spec(_tools_spec)
_tools_spec.loader.exec_module(youtube_tools)

SkillConfig = youtube_tools.SkillConfig
VideoEntry = youtube_tools.VideoEntry
_render_youtube_page = youtube_tools._render_youtube_page
format_timestamp = youtube_tools.format_timestamp
init = youtube_tools.init
parse_video_id = youtube_tools.parse_video_id
youtube_ingest_video = youtube_tools.youtube_ingest_video
youtube_sync_history = youtube_tools.youtube_sync_history


def test_parse_video_id():
    # Various valid formats
    assert parse_video_id("https://www.youtube.com/watch?v=Kqgz4m5Kags") == "Kqgz4m5Kags"
    assert parse_video_id("https://youtu.be/Kqgz4m5Kags") == "Kqgz4m5Kags"
    assert parse_video_id("https://www.youtube.com/shorts/Kqgz4m5Kags") == "Kqgz4m5Kags"
    assert parse_video_id("https://www.youtube.com/embed/Kqgz4m5Kags") == "Kqgz4m5Kags"
    assert parse_video_id("Kqgz4m5Kags") == "Kqgz4m5Kags"
    assert parse_video_id("https://www.youtube.com/watch?v=Kqgz4m5Kags&t=120s") == "Kqgz4m5Kags"

    with pytest.raises(ValueError, match="Invalid or unrecognized"):
        parse_video_id("https://example.com/not-a-video")


def test_format_timestamp():
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(5) == "00:05"
    assert format_timestamp(65) == "01:05"
    assert format_timestamp(3665) == "01:01:05"


def test_render_youtube_page():
    from decafclaw.frontmatter import parse_frontmatter

    video = VideoEntry(
        video_id="TESTVID1234",
        title="Test Video Title",
        channel="Test Channel",
        url="https://www.youtube.com/watch?v=TESTVID1234",
        watch_percent=0.85,
    )
    snippets = [
        {"text": "Hello world", "start": 0.0, "duration": 2.0},
        {"text": "Second line", "start": 65.5, "duration": 3.0},
    ]
    now = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
    md = _render_youtube_page(video, snippets, now)

    meta, body = parse_frontmatter(md)
    assert meta["video_id"] == "TESTVID1234"
    assert meta["title"] == "Test Video Title"
    assert meta["channel"] == "Test Channel"
    assert meta["watch_percent"] == 0.85
    assert "ingested" in meta["tags"]
    assert "youtube" in meta["tags"]

    assert "**Channel:** [[Test Channel]]" in body
    assert "[Watch on YouTube](https://www.youtube.com/watch?v=TESTVID1234)" in body
    assert "## Full Transcript" in body
    assert "> **00:00** Hello world" in body
    assert "> **01:05** Second line" in body


@pytest.fixture
def mock_ctx(tmp_path):
    ctx = MagicMock(spec=Context)
    ctx.task_mode = TaskMode.INTERACTIVE
    ctx.config = MagicMock()
    ctx.config.agent_path = tmp_path
    return ctx


@pytest.mark.asyncio
async def test_youtube_ingest_video_success(mock_ctx, monkeypatch):
    cfg = SkillConfig(enabled=True)
    init(mock_ctx.config, cfg)

    mock_snippets = [{"text": "Sample text", "start": 10.0, "duration": 2.0}]
    monkeypatch.setattr(youtube_tools, "_fetch_transcript", lambda vid, langs: mock_snippets)
    monkeypatch.setattr(youtube_tools, "tool_vault_write", AsyncMock(return_value=ToolResult(text="written")))

    res = await youtube_ingest_video(mock_ctx, "https://youtu.be/Kqgz4m5Kags")
    assert not res.text.startswith("[error:")
    assert "Ingested transcript for" in res.text
    assert res.data["video_id"] == "Kqgz4m5Kags"


@pytest.mark.asyncio
async def test_youtube_sync_history_gate(mock_ctx):
    cfg = SkillConfig(enabled=False)
    init(mock_ctx.config, cfg)

    mock_ctx.task_mode = TaskMode.SCHEDULED
    res = await youtube_sync_history(mock_ctx)
    assert "youtube-ingest skill disabled; skipping" in res.text


@pytest.mark.asyncio
async def test_youtube_sync_history_success(mock_ctx, monkeypatch):
    cfg = SkillConfig(enabled=True, min_watch_percent=0.5)
    init(mock_ctx.config, cfg)

    videos = [
        VideoEntry(
            video_id="VID11111111",
            title="Video 1",
            channel="Channel 1",
            url="https://www.youtube.com/watch?v=VID11111111",
            watch_percent=0.75,
        ),
        VideoEntry(
            video_id="VID22222222",
            title="Video 2",
            channel="Channel 2",
            url="https://www.youtube.com/watch?v=VID22222222",
            watch_percent=0.90,
        ),
    ]

    monkeypatch.setattr(youtube_tools, "_fetch_history_from_cdp", AsyncMock(return_value=videos))
    monkeypatch.setattr(
        youtube_tools,
        "_fetch_transcript",
        lambda vid, langs: [{"text": "Transcript", "start": 0.0, "duration": 5.0}],
    )

    # First video is new; second video already exists in vault
    async def mock_read(ctx, page):
        if "VID22222222" in page:
            return ToolResult(text="existing content")
        return ToolResult(text="[error: file not found]")

    monkeypatch.setattr(youtube_tools, "tool_vault_read", mock_read)
    monkeypatch.setattr(youtube_tools, "tool_vault_write", AsyncMock(return_value=ToolResult(text="ok")))
    monkeypatch.setattr(youtube_tools, "tool_vault_journal_append", AsyncMock(return_value=ToolResult(text="ok")))

    res = await youtube_sync_history(mock_ctx)
    assert not res.text.startswith("[error:")
    assert res.data["ingested_count"] == 1
    assert res.data["skipped_count"] == 1
    assert "YouTube history sync complete: 1 ingested, 1 already present" in res.text
