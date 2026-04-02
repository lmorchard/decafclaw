"""YAML frontmatter parsing and serialization for vault pages.

Supports Jekyll/Obsidian-compatible frontmatter: YAML between `---` delimiters
at the start of a markdown file. Pure utility functions, no codebase dependencies.
"""

from __future__ import annotations

import logging
import re

import yaml

log = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split markdown text into (frontmatter_dict, body_content).

    Frontmatter must be at the very start of the file, between ``---`` delimiters.
    Returns ({}, text) if no frontmatter found or on parse error.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    yaml_text = match.group(1)
    body = text[match.end():]

    if not yaml_text.strip():
        return {}, body

    try:
        metadata = yaml.safe_load(yaml_text)
        if not isinstance(metadata, dict):
            log.warning("Frontmatter YAML is not a dict, ignoring")
            return {}, body
        return metadata, body
    except yaml.YAMLError as e:
        log.warning("Malformed YAML frontmatter: %s", e)
        return {}, body


def serialize_frontmatter(metadata: dict, body: str) -> str:
    """Combine a metadata dict and body text into frontmatter + markdown.

    Omits the frontmatter block entirely if metadata is empty.
    """
    if not metadata:
        return body

    yaml_text = yaml.dump(metadata, default_flow_style=False, allow_unicode=True)
    return f"---\n{yaml_text}---\n{body}"


def get_frontmatter_field(metadata: dict, field: str, default=None):
    """Type-safe getter for frontmatter fields.

    - ``importance``: clamped to [0, 1] float.
    - ``keywords``, ``tags``: ensured to be list of strings.
    - ``summary``: ensured to be a string.
    """
    value = metadata.get(field, default)
    if value is None:
        return default

    if field == "importance":
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return default if default is not None else 0.5

    if field in ("keywords", "tags"):
        if isinstance(value, list):
            return [str(v) for v in value]
        if isinstance(value, str):
            return [value]
        return default if default is not None else []

    if field == "summary":
        return str(value)

    return value


def build_composite_text(metadata: dict, body: str) -> str:
    """Build composite text for embedding indexing.

    Prepends summary, keywords, and tags to body content for richer embeddings.
    Returns body as-is if no relevant frontmatter fields are present.
    """
    parts: list[str] = []

    summary = metadata.get("summary")
    if summary:
        parts.append(str(summary))

    keywords = get_frontmatter_field(metadata, "keywords", [])
    if isinstance(keywords, list) and keywords:
        parts.append(", ".join(str(k) for k in keywords))

    tags = get_frontmatter_field(metadata, "tags", [])
    if isinstance(tags, list) and tags:
        parts.append(", ".join(str(t) for t in tags))

    if not parts:
        return body

    parts.append(body)
    return "\n".join(parts)
