"""Media handling — ToolResult, MediaHandler interface, workspace image scanning."""

import logging
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Matches ![alt text](workspace://path/to/file.ext)
_WORKSPACE_IMG_RE = re.compile(r"!\[([^\]]*)\]\(workspace://([^)]+)\)")


@dataclass
class ToolResult:
    """Result from a tool execution — text plus optional media attachments."""

    text: str
    media: list[dict] = field(default_factory=list)
    display_text: str | None = None
    display_short_text: str | None = None
    data: dict | None = None

    @classmethod
    def from_text(cls, text: str) -> "ToolResult":
        """Create a text-only ToolResult."""
        return cls(text=text)


@dataclass
class MediaSaveResult:
    """Result from saving a media item via a MediaHandler."""

    workspace_ref: str | None = None   # workspace:// path for text injection
    file_id: str | None = None         # platform file ID (Mattermost)
    saved_filename: str | None = None  # actual filename after dedup


class MediaHandler:
    """Interface for channel-specific media operations.

    Subclass for each channel type (Mattermost, terminal, web, etc.).
    """

    # Whether extract_workspace_media() should strip workspace:// refs from
    # the agent's final text. Mattermost sets True (needs extraction + upload).
    # Web and Terminal set False (refs render in-place).
    strips_workspace_refs: bool = True

    async def save_media(self, conv_id: str, filename: str,
                         data: bytes, content_type: str) -> MediaSaveResult:
        """Save media and return a result describing where it went.

        Subclasses implement channel-specific behavior:
        - Web/Terminal: save to conversation uploads, return workspace_ref
        - Mattermost: upload to API, return file_id
        """
        raise NotImplementedError

    async def upload_file(self, channel_id: str, filename: str,
                          data: bytes, content_type: str) -> str:
        """Upload raw bytes, return an opaque file reference."""
        raise NotImplementedError

    async def send_with_media(self, channel_id: str, message: str,
                              media_refs: list[str], root_id: str | None = None) -> str:
        """Send a message with attached media references."""
        raise NotImplementedError

    def format_image_url(self, url: str) -> str:
        """Format a URL for inline display."""
        return f"![image]({url})"

    def format_attachment_card(self, title: str, text: str,
                               image_url: str | None = None,
                               thumb_url: str | None = None) -> dict:
        """Build a rich attachment card structure."""
        card = {"title": title, "text": text}
        if image_url:
            card["image_url"] = image_url
        if thumb_url:
            card["thumb_url"] = thumb_url
        return card


def extract_workspace_media(text: str, workspace_path: Path) -> tuple[str, list[dict]]:
    """Scan text for workspace:// image references and extract them.

    Returns (cleaned_text, media_items) where workspace refs are stripped
    from the text and corresponding files are read into media items.
    Public URL images (https://, http://) are left untouched.
    """
    media = []
    missing = []

    def _replace(match):
        _alt = match.group(1)  # captured but not used (stripped from output)
        path = match.group(2)
        full_path = workspace_path / path

        if not full_path.exists():
            log.warning(f"Workspace image not found: {path}")
            missing.append(path)
            return ""  # strip the broken ref

        try:
            data = full_path.read_bytes()
            content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
            media.append({
                "type": "file",
                "filename": full_path.name,
                "data": data,
                "content_type": content_type,
            })
            return ""  # strip the ref (file will be attached)
        except OSError as e:
            log.warning(f"Cannot read workspace image {path}: {e}")
            return ""

    cleaned = _WORKSPACE_IMG_RE.sub(_replace, text)
    # Clean up any resulting blank lines from stripped refs
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    return cleaned, media


# -- Terminal media handler ----------------------------------------------------


class LocalFileMediaHandler(MediaHandler):
    """Media handler for terminal and web UI — saves to conversation uploads."""

    def __init__(self, config, strips_workspace_refs: bool = False):
        self.config = config
        self.strips_workspace_refs = strips_workspace_refs

    async def save_media(self, conv_id, filename, data, content_type):
        """Save media to conversation uploads, return workspace ref."""
        import asyncio

        from .attachments import save_attachment
        result = await asyncio.to_thread(
            save_attachment, self.config, conv_id, filename, data, content_type)
        return MediaSaveResult(
            workspace_ref="workspace://" + result["path"],
            saved_filename=result["filename"],
        )

    async def upload_file(self, channel_id, filename, data, content_type):
        """Legacy — not used with per-tool-call processing."""
        raise NotImplementedError

    async def send_with_media(self, channel_id, message, media_refs, root_id=None):
        """Not applicable in local file mode."""
        return ""


# -- Mattermost media handler -------------------------------------------------

MAX_FILES_PER_POST = 10


class MattermostMediaHandler(MediaHandler):
    """Media handler for Mattermost — uploads files via API, attaches to posts."""

    strips_workspace_refs = True

    def __init__(self, http_client, channel_id: str = ""):
        self._http = http_client
        self._channel_id = channel_id

    async def save_media(self, conv_id, filename, data, content_type):
        """Upload media to Mattermost, return file_id."""
        file_id = await self.upload_file(
            self._channel_id, filename, data, content_type)
        return MediaSaveResult(file_id=file_id)

    async def upload_file(self, channel_id, filename, data, content_type):
        """Upload a file to Mattermost, return the file_id."""
        import io
        resp = await self._http.post(
            f"/files?channel_id={channel_id}",
            files={"files": (filename, io.BytesIO(data), content_type)},
        )
        resp.raise_for_status()
        file_infos = resp.json().get("file_infos", [])
        if not file_infos:
            raise RuntimeError("Mattermost file upload returned no file_infos")
        return file_infos[0]["id"]

    async def send_with_media(self, channel_id, message, media_refs,
                              root_id=None) -> str:
        """Send a message with file_ids attached. Handles overflow (>10 files)."""
        if not media_refs:
            # No media — plain send
            resp = await self._http.post("/posts", json={
                "channel_id": channel_id,
                "message": message,
                **({"root_id": root_id} if root_id else {}),
            })
            resp.raise_for_status()
            return resp.json().get("id")

        # First batch: message + first 10 files
        first_batch = media_refs[:MAX_FILES_PER_POST]
        body = {
            "channel_id": channel_id,
            "message": message,
            "file_ids": first_batch,
        }
        if root_id:
            body["root_id"] = root_id
        resp = await self._http.post("/posts", json=body)
        resp.raise_for_status()
        first_post_id = resp.json().get("id")

        # Overflow: remaining files in batches, as thread replies
        remaining = media_refs[MAX_FILES_PER_POST:]
        thread_root = root_id or first_post_id
        while remaining:
            batch = remaining[:MAX_FILES_PER_POST]
            remaining = remaining[MAX_FILES_PER_POST:]
            resp = await self._http.post("/posts", json={
                "channel_id": channel_id,
                "message": "",
                "file_ids": batch,
                "root_id": thread_root,
            })
            resp.raise_for_status()

        return first_post_id

    def format_attachment_card(self, title, text, image_url=None, thumb_url=None):
        """Build a Mattermost/Slack-style attachment card."""
        card = {"title": title, "text": text}
        if image_url:
            card["image_url"] = image_url
        if thumb_url:
            card["thumb_url"] = thumb_url
        return card


async def upload_and_collect(handler: MediaHandler, channel_id: str,
                             media_items: list[dict]) -> list[str]:
    """Upload file media items via handler, return list of file_ids.

    URL-type items are skipped (handled differently by the caller).
    """
    file_ids = []
    for item in media_items:
        if item.get("type") == "file":
            try:
                file_id = await handler.upload_file(
                    channel_id,
                    item["filename"],
                    item["data"],
                    item["content_type"],
                )
                file_ids.append(file_id)
            except Exception as e:
                log.error(f"Failed to upload {item['filename']}: {e}")
    return file_ids
