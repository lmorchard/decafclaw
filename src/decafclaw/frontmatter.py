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


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split off the raw frontmatter block *without* parsing its YAML.

    Returns ``(raw_yaml, body)``. *raw_yaml* is the text between the ``---``
    delimiters exactly as written, with no trailing newline; ``None`` means the
    file has no frontmatter block at all (distinct from ``""``, an empty one).

    Purely lexical, so malformed YAML round-trips byte-for-byte. That is what
    lets body-only writes preserve a block they cannot parse — reserializing
    through ``parse_frontmatter`` + ``serialize_frontmatter`` would silently
    delete malformed frontmatter, since the parser reports ``{}`` on error.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None, text
    return match.group(1), text[match.end():]


def join_frontmatter(raw_yaml: str | None, body: str) -> str:
    """Re-attach a raw block from :func:`split_frontmatter`.

    ``join_frontmatter(*split_frontmatter(t)) == t`` for any *t*.
    """
    if raw_yaml is None:
        return body
    return f"---\n{raw_yaml}\n---\n{body}"


def parse_frontmatter_block(raw_yaml: str | None) -> tuple[dict, str | None]:
    """Parse a raw block from :func:`split_frontmatter`.

    Returns ``(metadata, error)``. On success *error* is ``None``; on malformed
    YAML or a non-mapping document *metadata* is ``{}`` and *error* carries a
    human-readable message. Unlike :func:`parse_frontmatter`, which logs and
    swallows both cases, this reports them so callers can refuse to write.
    """
    if raw_yaml is None or not raw_yaml.strip():
        return {}, None
    try:
        parsed = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        return {}, str(exc)
    if not isinstance(parsed, dict):
        return {}, "frontmatter is not a mapping"
    return parsed, None


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


def merge_frontmatter(existing: dict, fields: dict, overwrite: bool) -> dict:
    """Merge coerced field values into existing frontmatter metadata.

    Pure function — no ctx, no I/O — so callers (dream generation, the
    backfill CLI, garden importance tuning, the vault REST API) can reuse the
    merge logic without a running agent context. Coercion goes through
    `get_frontmatter_field` (importance clamped to [0, 1]; tags/keywords to
    list[str]; summary to str) so the merged result and the parser agree on
    shape.

    When `overwrite` is False, only fields that are absent or empty in
    `existing` are filled. When True, every field in `fields` is set,
    replacing any existing value.

    Note there is no deletion path: a ``None`` value coerces to ``None`` and
    is *set*, producing ``field: null`` rather than removing the key. Callers
    that need deletion strip null values from the result themselves.
    """
    merged = dict(existing)
    for field, raw_value in fields.items():
        coerced = get_frontmatter_field({field: raw_value}, field)
        if not overwrite and merged.get(field) not in (None, "", []):
            continue
        merged[field] = coerced
    return merged


def build_composite_text(metadata: dict, body: str) -> str:
    """Build composite text for embedding indexing.

    Prepends summary, keywords, and tags (frontmatter ``tags:`` plus inline
    Obsidian-style ``#tags`` found in the body, #318) to body content for
    richer embeddings. Returns body as-is if no relevant frontmatter fields
    or inline tags are present.
    """
    parts: list[str] = []

    summary = metadata.get("summary")
    if summary:
        parts.append(str(summary))

    keywords = get_frontmatter_field(metadata, "keywords", [])
    if isinstance(keywords, list) and keywords:
        parts.append(", ".join(str(k) for k in keywords))

    # Function-level import: this breaks a module-level import cycle.
    # tags.py imports parse_frontmatter/get_frontmatter_field from this
    # module at module level, so importing tags.py here at module level
    # would cycle back into a partially-initialized frontmatter module.
    from decafclaw.tags import normalize_tag, parse_inline_tags

    fm_tags = get_frontmatter_field(metadata, "tags", [])
    seen_norm: set[str] = set()
    all_tags: list[str] = []
    if isinstance(fm_tags, list):
        for t in fm_tags:
            raw = str(t)
            norm = normalize_tag(raw)
            if norm and norm not in seen_norm:
                seen_norm.add(norm)
                all_tags.append(raw)
    for norm in sorted(parse_inline_tags(body)):
        if norm not in seen_norm:
            seen_norm.add(norm)
            all_tags.append(norm)

    if all_tags:
        parts.append(", ".join(all_tags))

    if not parts:
        return body

    parts.append(body)
    return "\n".join(parts)
