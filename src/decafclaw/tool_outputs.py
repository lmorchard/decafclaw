"""Stable read references for saved tool output (#1128).

A saved tool output gets a **handle** — a typed, conversation-scoped address
that survives compaction, reload, and in-memory stubbing:

    tool_output://{conv_id}/{filename}

Files stay where they already live (`{conv_dir}/tool_outputs/`), so the handle
wraps the existing materialization rather than introducing a new store. A
per-file metadata sidecar (`{filename}.meta.json`) records whether the artifact
holds the complete body or only the captured prefix, which a reader cannot tell
from the file alone.

Five retrieval states are reported distinctly:

- ``complete`` — the full body was captured; paging serves all of it.
- ``capped`` — capture hit the size cap; only the stored prefix exists.
- ``failed`` — persistence errored at capture; the reference records it.
- ``missing`` — the handle is valid but the file is gone (deleted, or never
  materialized).
- ``denied`` — the handle names another conversation's content.

Handles are conversation-scoped: two conversations with identical local
identifiers resolve to their own files, and a handle from a different
conversation is refused rather than followed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .conversation_paths import conversation_dir

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

HANDLE_SCHEME = "tool_output://"
OUTPUTS_SUBDIR = "tool_outputs"
META_SUFFIX = ".meta.json"
DEFAULT_PAGE_BYTES = 16 * 1024

# States a reader can see.
COMPLETE = "complete"
CAPPED = "capped"
FAILED = "failed"
MISSING = "missing"
DENIED = "denied"

_FILENAME_RE = re.compile(r"^[A-Za-z0-9%][A-Za-z0-9._%-]*$")


@dataclass(frozen=True)
class CaptureResult:
    """Outcome of persisting one tool output."""

    handle: str
    state: str  # complete | capped | failed
    path: Path | None
    stored_bytes: int
    total_bytes: int
    message: str = ""


@dataclass(frozen=True)
class OutputPage:
    """One paged read of a saved output."""

    state: str  # complete | capped | failed | missing | denied
    content: str = ""
    offset: int = 0
    next_offset: int | None = None
    total_bytes: int = 0
    stored_bytes: int = 0
    path: Path | None = None
    message: str = ""


# ---------------------------------------------------------------------------
# Handles
# ---------------------------------------------------------------------------


def outputs_dir(config: "Config", conv_id: str, *, create: bool = False) -> Path:
    """The conversation's tool-output directory."""
    path = conversation_dir(config, conv_id, create=create) / OUTPUTS_SUBDIR
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def format_handle(conv_id: str, filename: str) -> str:
    """Build the handle for one artifact file."""
    return f"{HANDLE_SCHEME}{conv_id}/{filename}"


def parse_handle(handle: str) -> tuple[str, str] | None:
    """Split a handle into ``(conv_id, filename)``, or None when malformed.

    Deliberately strict: a handle is data that reaches the model through tool
    output and can come back as an argument, so it must never be able to name a
    path outside the conversation's ``tool_outputs`` directory.
    """
    if not isinstance(handle, str) or not handle.startswith(HANDLE_SCHEME):
        return None
    rest = handle[len(HANDLE_SCHEME) :].strip()
    if rest.endswith("/"):
        rest = rest[:-1]
    conv_id, sep, filename = rest.partition("/")
    if not sep or not conv_id or not filename:
        return None
    if "/" in filename or "\\" in filename:
        return None
    if filename in (".", "..") or conv_id in (".", ".."):
        return None
    if not _FILENAME_RE.match(filename) or not _FILENAME_RE.match(conv_id):
        return None
    return conv_id, filename


def meta_path(path: Path) -> Path:
    """Sidecar holding the capture state for one artifact."""
    return path.with_name(path.name + META_SUFFIX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def capture(
    config: "Config",
    conv_id: str,
    tool_call_id: str,
    content: bytes,
    *,
    max_bytes: int | None = None,
    tool: str = "",
) -> CaptureResult:
    """Persist one tool output and return its handle.

    ``max_bytes`` caps what is stored: a longer body is truncated to its prefix
    and reported as ``capped``. A caller that only wants to materialize an
    oversized body passes the configured cap; a caller that materializes every
    body omits it and gets ``complete``.

    Persistence failure is recorded, not raised: the handle stays valid, and a
    later read reports ``failed`` instead of a silently empty body.
    """
    filename = f"{tool_call_id}.txt"
    handle = format_handle(conv_id, filename)
    total_bytes = len(content)
    stored = content if max_bytes is None else content[:max_bytes]
    state = COMPLETE if len(stored) == total_bytes else CAPPED

    path = outputs_dir(config, conv_id, create=True) / filename
    try:
        path.write_bytes(stored)
    except OSError as exc:
        log.warning("tool output capture failed for %s (%s)", handle, exc)
        _write_meta(
            path,
            {
                "state": FAILED,
                "error": str(exc),
                "total_bytes": total_bytes,
                "stored_bytes": 0,
                "tool": tool,
                "tool_call_id": tool_call_id,
                "ts": _now_iso(),
            },
        )
        return CaptureResult(
            handle=handle,
            state=FAILED,
            path=None,
            stored_bytes=0,
            total_bytes=total_bytes,
            message=str(exc),
        )

    stored_bytes = len(stored)
    _write_meta(
        path,
        {
            "state": state,
            "total_bytes": total_bytes,
            "stored_bytes": stored_bytes,
            "tool": tool,
            "tool_call_id": tool_call_id,
            "ts": _now_iso(),
        },
    )
    return CaptureResult(
        handle=handle,
        state=state,
        path=path,
        stored_bytes=stored_bytes,
        total_bytes=total_bytes,
    )


def _write_meta(path: Path, meta: dict) -> None:
    try:
        meta_path(path).write_text(json.dumps(meta, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        # Observability only: a read of a legacy artifact without metadata
        # reports "complete" rather than failing the tool call.
        log.warning("tool output metadata write failed for %s (%s)", path, exc)


def record_artifact(
    config: "Config",
    conv_id: str,
    filename: str,
    *,
    state: str,
    total_bytes: int,
    stored_bytes: int,
    tool: str = "",
    tool_call_id: str = "",
) -> None:
    """Record the state of an artifact another writer already materialized.

    Shell's streamed ``.stdout.txt`` / ``.stderr.txt`` artifacts (#922) are
    written by a collector, not by ``capture``; this records whether the file
    holds the whole stream or stopped at the disk cap, so a handle read reports
    the honest state instead of assuming "complete".
    """
    path = outputs_dir(config, conv_id) / filename
    _write_meta(
        path,
        {
            "state": state,
            "total_bytes": total_bytes,
            "stored_bytes": stored_bytes,
            "tool": tool,
            "tool_call_id": tool_call_id,
            "ts": _now_iso(),
        },
    )


def _read_meta(path: Path) -> dict:
    try:
        raw = json.loads(meta_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def resolve(config: "Config", conv_id: str, handle: str) -> Path | None:
    """Resolve a handle to this conversation's file path, or None when denied.

    Raises ``ValueError`` for a malformed handle so the tool layer can answer
    with a correction instead of pretending an artifact is missing.
    """
    parsed = parse_handle(handle)
    if parsed is None:
        raise ValueError(f"invalid tool-output handle: {handle!r}")
    handle_conv, filename = parsed
    if conv_id and handle_conv != conv_id:
        return None
    return outputs_dir(config, handle_conv, create=False) / filename


def read_page(
    config: "Config",
    conv_id: str,
    handle: str,
    *,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_BYTES,
) -> OutputPage:
    """Read one page of a saved output.

    ``offset`` is a byte offset into the *stored* artifact; ``limit`` bounds the
    page, decoded on a UTF-8 boundary so a page never ends with a broken
    character. Returns ``next_offset=None`` at the end of the stored content.
    """
    if offset < 0:
        raise ValueError("offset must be zero or greater")
    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    path = resolve(config, conv_id, handle)
    if path is None:
        return OutputPage(
            state=DENIED,
            message=(
                "this handle belongs to a different conversation; conversation-scoped content is not readable from here"
            ),
        )

    meta = _read_meta(path)
    state = str(meta.get("state") or COMPLETE)
    total_bytes = int(meta.get("total_bytes") or 0)
    if state == FAILED:
        return OutputPage(
            state=FAILED,
            path=path,
            total_bytes=total_bytes,
            message=str(meta.get("error") or "capture failed"),
        )
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return OutputPage(state=MISSING, path=path, message="no saved output for this handle")
    except OSError as exc:
        log.warning("tool output read failed for %s (%s)", handle, exc)
        return OutputPage(state=FAILED, path=path, message=str(exc))

    if not total_bytes:
        total_bytes = len(data)
    if offset >= len(data):
        return OutputPage(
            state=state,
            offset=offset,
            next_offset=None,
            total_bytes=total_bytes,
            stored_bytes=len(data),
            path=path,
            message="offset is at or past the end of the stored output",
        )
    end = min(len(data), offset + limit)
    text, end = _decode_page(data, offset, end)
    return OutputPage(
        state=state,
        content=text,
        offset=offset,
        next_offset=end if end < len(data) else None,
        total_bytes=total_bytes,
        stored_bytes=len(data),
        path=path,
    )


def _decode_page(data: bytes, start: int, end: int) -> tuple[str, int]:
    """Decode ``data[start:end]`` without splitting a multi-byte character."""
    for back in range(0, 4):
        candidate = data[start : end - back]
        if not candidate:
            return "", start
        try:
            return candidate.decode("utf-8"), end - back
        except UnicodeDecodeError:
            continue
    # Not valid UTF-8 anywhere near the boundary (a binary artifact):
    # replacement characters are better than refusing to page.
    return data[start:end].decode("utf-8", errors="replace"), end


def delete(config: "Config", conv_id: str, handle: str) -> str:
    """Remove one saved output. Returns ``deleted``, ``missing``, or ``denied``."""
    path = resolve(config, conv_id, handle)
    if path is None:
        return DENIED
    removed = False
    for target in (path, meta_path(path)):
        try:
            target.unlink()
            removed = True
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.warning("tool output delete failed for %s (%s)", target, exc)
    return "deleted" if removed else MISSING


def handle_note(capture_result: CaptureResult) -> str:
    """Pointer appended to a truncated preview, carrying the stable handle."""
    return (
        f"\n\n[Output truncated. Full output saved as {capture_result.handle}. "
        f"Use tool_output_read with that handle to page through it.]"
    )
