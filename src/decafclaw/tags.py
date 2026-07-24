"""First-class vault tags (#318): extraction, normalization, on-demand scan.

Tags come from three sources, unioned at the query layer (not stored
uniformly on disk): page frontmatter ``tags:``, the journal
``- **tags:**`` bullet, and Obsidian-style inline ``#tags`` in body prose.
All comparisons use the lowercased canonical form; display casing is the
first seen (across a full ``collect_all_tags`` scan, in page-then-journal,
file-then-occurrence order).
"""
from __future__ import annotations

import logging
import re

from decafclaw.frontmatter import get_frontmatter_field, parse_frontmatter

log = logging.getLogger(__name__)

# Inline #tag: preceded by whitespace/SOL, starts with letter/_/ '/', then
# word chars / - / /. Digit-start excluded so "#42" isn't a tag.
_INLINE_TAG_RE = re.compile(r"(?:(?<=\s)|^)#([A-Za-z_/][\w\-/]*)", re.MULTILINE)
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_JOURNAL_TAGS_BULLET_RE = re.compile(r"^- \*\*tags:\*\* (.+)$", re.MULTILINE)


def normalize_tag(tag: str) -> str:
    """Canonicalize a tag: strip leading '#', trim whitespace, lowercase.

    Hyphen/underscore variants are NOT merged (``rust-lang`` stays distinct
    from ``rust_lang``) — only case is folded.
    """
    return tag.lstrip("#").strip().lower()


def _strip_code(body: str) -> str:
    """Blank out fenced and inline code spans so tag-like text inside code
    (e.g. a literal ``#fenced-not-tag``) is never mistaken for a real tag."""
    body = _FENCED_CODE_RE.sub(" ", body)
    return _INLINE_CODE_RE.sub(" ", body)


def _raw_inline_tag_matches(body: str) -> list[str]:
    """Return inline #tag matches with their original casing intact.

    Shared by ``parse_inline_tags`` (normalizes) and ``collect_all_tags``
    (needs the pre-normalization form to capture first-seen display casing).
    """
    stripped = _strip_code(body)
    return [m.group(1) for m in _INLINE_TAG_RE.finditer(stripped)]


def parse_inline_tags(body: str) -> set[str]:
    """Extract normalized Obsidian-style inline #tags from markdown body text.

    Excludes tags inside fenced or inline code, digit-led hashes (``#42``),
    and ATX headings (``# Heading``, since a heading requires a space after
    the hash and the tag pattern requires a non-space character immediately
    following it).
    """
    return {normalize_tag(t) for t in _raw_inline_tag_matches(body)}


def _extract_tag_pairs(content: str, source_type: str) -> list[tuple[str, str]]:
    """Return ``(normalized, display)`` pairs for every tag occurrence in one
    file's raw content, in encounter order: frontmatter ``tags:`` (all
    source types), the journal ``- **tags:**`` bullet (``source_type ==
    "journal"`` only), then inline ``#tags`` in the body.

    Single shared extraction path for both ``extract_tags`` (per-file
    union, normalized) and ``collect_all_tags`` (vault-wide aggregate,
    needs display casing) — keeps the parsing rules and the journal
    "untagged" placeholder guard defined exactly once.
    """
    metadata, body = parse_frontmatter(content)
    pairs: list[tuple[str, str]] = []

    fm_tags = get_frontmatter_field(metadata, "tags", [])
    if isinstance(fm_tags, list):
        for t in fm_tags:
            raw = str(t)
            norm = normalize_tag(raw)
            if norm:
                pairs.append((norm, raw.lstrip("#").strip()))

    if source_type == "journal":
        for m in _JOURNAL_TAGS_BULLET_RE.finditer(content):
            bullet_value = m.group(1).strip()
            # "untagged" is vault_journal_append's display placeholder for
            # the no-tags case (see skills/vault/tools.py), not a real tag —
            # skip it. Only the whole-value sentinel is excluded; a
            # legitimate multi-tag bullet that merely contains the word
            # elsewhere is untouched.
            if bullet_value.lower() == "untagged":
                continue
            for part in bullet_value.split(","):
                raw = part.strip()
                if raw:
                    norm = normalize_tag(raw)
                    if norm:
                        pairs.append((norm, raw.lstrip("#").strip()))

    for raw in _raw_inline_tag_matches(body):
        norm = normalize_tag(raw)
        if norm:
            pairs.append((norm, raw.lstrip("#").strip()))

    return pairs


def extract_tags(content: str, source_type: str) -> set[str]:
    """Union of all tag sources for one file's raw content, normalized.

    Sources: frontmatter ``tags:`` (all source types), the journal
    ``- **tags:**`` bullet (``source_type == "journal"`` only), and inline
    ``#tags`` in the body.
    """
    return {norm for norm, _ in _extract_tag_pairs(content, source_type)}


def _iter_tag_source_files(config):
    """Yield (rel_path, content, source_type) for every vault file that can
    carry tags: pages (agent + user, journal excluded — mirrors
    ``embeddings._iter_vault_pages``) plus journal daily files explicitly.

    Paths are vault-relative POSIX strings, matching embeddings/backlinks
    path keying. Fails open per-file: an unreadable file is skipped with a
    debug log, never raised.
    """
    vault = config.vault_root
    journal_dir = config.vault_agent_journal_dir

    if vault.is_dir():
        for filepath in sorted(vault.rglob("*.md")):
            try:
                if filepath.resolve().is_relative_to(journal_dir.resolve()):
                    continue
            except (ValueError, OSError):
                pass
            try:
                content = filepath.read_text()
            except Exception as exc:
                log.debug("tags: failed reading page %s: %s", filepath, exc)
                continue
            rel_path = filepath.relative_to(vault).as_posix()
            yield rel_path, content, "page"

    if journal_dir.is_dir():
        for filepath in sorted(journal_dir.rglob("*.md")):
            try:
                content = filepath.read_text()
            except Exception as exc:
                log.debug("tags: failed reading journal entry %s: %s", filepath, exc)
                continue
            rel_path = filepath.relative_to(vault).as_posix()
            yield rel_path, content, "journal"


def collect_all_tags(config) -> dict[str, dict]:
    """Scan the vault (pages + journal) and aggregate tags across all files.

    Returns ``{normalized_tag: {"count": int, "display": str, "pages": [str]}}``
    where ``count`` is the number of distinct files carrying the tag,
    ``display`` is the original casing as first encountered, and ``pages``
    lists vault-relative POSIX paths of files carrying the tag.
    """
    result: dict[str, dict] = {}

    for rel_path, content, source_type in _iter_tag_source_files(config):
        try:
            pairs = _extract_tag_pairs(content, source_type)
        except Exception as exc:
            log.debug("tags: failed extracting tags from %s: %s", rel_path, exc)
            continue

        seen_norm_in_file: set[str] = set()
        for norm, display in pairs:
            entry = result.setdefault(norm, {"count": 0, "display": display, "pages": []})
            if norm not in seen_norm_in_file:
                seen_norm_in_file.add(norm)
                entry["count"] += 1
                entry["pages"].append(rel_path)

    return result


def pages_with_tags(config, tags: list[str], any_tag: bool = False) -> list[str]:
    """Vault-relative paths of files whose extracted tags match the request.

    Default is AND (file's tags must be a superset of the requested tags);
    ``any_tag=True`` switches to OR (file's tags intersect the requested set).
    Requested tags are normalized before comparison.
    """
    wanted = {normalize_tag(t) for t in tags}
    matches: list[str] = []

    for rel_path, content, source_type in _iter_tag_source_files(config):
        try:
            file_tags = extract_tags(content, source_type)
        except Exception as exc:
            log.debug("tags: failed extracting tags from %s: %s", rel_path, exc)
            continue
        if any_tag:
            if file_tags & wanted:
                matches.append(rel_path)
        else:
            if wanted <= file_tags:
                matches.append(rel_path)

    return matches
