"""Persistent backlink index (#197 Phase 4 — self-improving vault arc).

Replaces the brute-force ``rglob``-and-regex-scan that used to live inline
in ``tool_vault_backlinks``: a JSON index at ``{workspace}/backlinks.json``
mapping ``page -> [pages that link to it]``. Rebuilt lazily on first read,
kept current via the ``vault_changed`` EventBus subscriber wired in
``runner.py`` (see ``make_backlinks_subscriber``), and consumed directly
by ``inbound_count`` — the raw signal Phase 5's importance formula folds
into its score.

Link resolution matches the pre-Phase-4 semantics: a raw ``[[link]]`` (or
``[[link|display]]``) is resolved to an existing vault page case-
insensitively, first by full relative path and falling back to bare
filename (stem). Links that don't resolve to any existing page (dangling
links) and self-links are not recorded — a backlink index only makes
sense for edges between real pages.

Incremental vs. full rebuild: ``update_for_page`` only rescans the
*changed page's own outbound links* and reconciles other pages' inbound
lists accordingly. That's correct for CREATE/UPDATE (and other events that
don't change a page's identity, e.g. SECTION/JOURNAL/MOVE-between-
sections) — the page's own rel-path key never moves. It's NOT sufficient
for DELETE or RENAME: those change (or remove) the page's own key in the
index, which nothing in ``update_for_page`` ever scrubs or creates, and a
rename event only carries the new path (no old path to diff against). So
``make_backlinks_subscriber`` runs a full ``rebuild_index`` on DELETE/
RENAME and only takes the incremental path otherwise. DELETE/RENAME are
rare compared to writes, so the full-scan cost is a non-issue — a
correct-but-occasionally-full-rebuild index beats a fast-but-wrong one.
There is no separate periodic self-heal; DELETE/RENAME rebuilds are it.

Fail-open throughout: any I/O or parse error is logged at debug level and
falls back to an empty/rebuilt result rather than propagating into a tool
call or event subscriber.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Awaitable, Callable

from decafclaw.skills.vault._events import KIND_DELETE, KIND_RENAME

log = logging.getLogger(__name__)


def _index_path(config) -> Path:
    return config.workspace_path / "backlinks.json"


def _save_index(config, index: dict[str, list[str]]) -> None:
    """Persist the index as human-readable JSON via tmp-file-then-rename.

    Fail-open: I/O errors are logged at debug level and swallowed.
    """
    path = _index_path(config)
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.debug("backlinks: failed to persist index: %s", exc)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError as cleanup_exc:
            log.debug("backlinks: tmp cleanup failed: %s", cleanup_exc)


def _build_page_lookup(
    pages: list[Path], vault: Path
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Build case-insensitive lookup maps for resolving raw link text.

    Returns (full_lower -> rel_path, stem_lower -> [rel_path, ...]). Both
    keys are derived from each existing page's vault-relative path (POSIX,
    ``.md`` stripped for the "full" form).
    """
    full_lower_map: dict[str, str] = {}
    stem_lower_map: dict[str, list[str]] = {}
    for p in pages:
        rel = p.relative_to(vault).as_posix()
        rel_no_ext = rel[:-3] if rel.endswith(".md") else rel
        full_lower_map.setdefault(rel_no_ext.lower(), rel)
        stem_lower_map.setdefault(p.stem.lower(), []).append(rel)
    return full_lower_map, stem_lower_map


def _resolve_link_target(
    raw_link: str,
    full_lower_map: dict[str, str],
    stem_lower_map: dict[str, list[str]],
) -> str | None:
    """Resolve raw [[link]] text to an existing page's rel path, or None."""
    target = raw_link.split("|")[0].strip()
    if target.endswith(".md"):
        target = target[:-3]
    if not target:
        return None
    hit = full_lower_map.get(target.lower())
    if hit is not None:
        return hit
    candidates = stem_lower_map.get(Path(target).stem.lower())
    if candidates:
        return sorted(candidates)[0]
    return None


def _extract_outbound_targets(
    text: str,
    source_rel: str,
    full_lower_map: dict[str, str],
    stem_lower_map: dict[str, list[str]],
) -> set[str]:
    """Distinct existing pages that `text` (the body of `source_rel`) links to."""
    from decafclaw.skills.vault.tools import _WIKI_LINK_RE

    targets: set[str] = set()
    for match in _WIKI_LINK_RE.finditer(text):
        target_rel = _resolve_link_target(
            match.group(1), full_lower_map, stem_lower_map)
        if target_rel is not None and target_rel != source_rel:
            targets.add(target_rel)
    return targets


def rebuild_index(config) -> dict[str, list[str]]:
    """Full rebuild: scan every vault page's outbound links.

    Returns (and persists) ``{page_path: [pages linking to it]}``, sorted
    for determinism. Fail-open — any error yields (and persists, best
    effort) an empty index rather than raising.
    """
    try:
        vault = config.vault_root
        if not vault.is_dir():
            _save_index(config, {})
            return {}

        pages = sorted(vault.rglob("*.md"))
        full_lower_map, stem_lower_map = _build_page_lookup(pages, vault)

        inbound: dict[str, set[str]] = {}
        for p in pages:
            source_rel = p.relative_to(vault).as_posix()
            try:
                text = p.read_text(encoding="utf-8")
            except OSError as exc:
                log.debug("backlinks: failed reading %s: %s", p, exc)
                continue
            for target_rel in _extract_outbound_targets(
                text, source_rel, full_lower_map, stem_lower_map
            ):
                inbound.setdefault(target_rel, set()).add(source_rel)

        result = {k: sorted(v) for k, v in sorted(inbound.items())}
        _save_index(config, result)
        return result
    except Exception as exc:  # fail-open
        log.debug("backlinks: rebuild_index failed: %s", exc)
        return {}


def load_index(config) -> dict[str, list[str]]:
    """Read the persisted index, rebuilding lazily if missing or corrupt.

    Fail-open: any error falls back to a fresh rebuild, and if that also
    fails, an empty index.
    """
    path = _index_path(config)
    try:
        if not path.exists():
            return rebuild_index(config)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            log.debug("backlinks: index at %s was not a dict, rebuilding", path)
            return rebuild_index(config)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        log.debug("backlinks: load_index failed (%s), rebuilding", exc)
        return rebuild_index(config)


def inbound_count(config, page: str) -> int:
    """Number of distinct pages linking to `page`. Fail-open (returns 0)."""
    try:
        from decafclaw.skills.vault.tools import resolve_page

        resolved = resolve_page(config, page)
        if resolved is None:
            return 0
        # resolve_page() returns a fully-resolved path (symlinks collapsed),
        # so the vault root side of relative_to() must be resolved too, or
        # a symlinked vault_root component (e.g. /tmp -> /private/tmp on
        # macOS) makes relative_to() raise and this silently returns 0.
        vault_resolved = config.vault_root.resolve()
        rel = resolved.relative_to(vault_resolved).as_posix()
        return len(load_index(config).get(rel, []))
    except Exception as exc:  # fail-open
        log.debug("backlinks: inbound_count failed for %r: %s", page, exc)
        return 0


def _resolve_source_rel(config, page: str) -> tuple[str, str]:
    """Resolve `page` to (canonical rel path, current body text).

    If the page no longer exists on disk (e.g. just deleted), the rel path
    is best-effort normalized from the raw `page` string and the body is
    treated as empty (no outbound links) so stale entries get purged.
    """
    from decafclaw.skills.vault.tools import resolve_page

    resolved = resolve_page(config, page)
    if resolved is not None and resolved.exists():
        # Same resolved/unresolved consistency requirement as inbound_count.
        vault_resolved = config.vault_root.resolve()
        rel = resolved.relative_to(vault_resolved).as_posix()
        return rel, resolved.read_text(encoding="utf-8")

    norm = page[:-3] if page.endswith(".md") else page
    norm = norm.strip("/")
    return f"{norm}.md", ""


def update_for_page(config, page: str) -> None:
    """Incrementally update the index after a single page's content changed.

    Re-scans only `page`'s current outbound links (not the whole vault's
    file contents — the per-page lookup maps only need filenames) and
    updates the index's inbound entries: drops this page from targets it
    no longer links to, adds it to new ones. Fail-open — never raises.

    Only correct for events that don't change `page`'s own identity (its
    own rel-path key in the index). Callers must NOT use this for
    delete/rename — see the module docstring and `make_backlinks_subscriber`,
    which routes those to a full `rebuild_index` instead.
    """
    try:
        vault = config.vault_root
        if not vault.is_dir():
            return
        source_rel, text = _resolve_source_rel(config, page)

        pages = sorted(vault.rglob("*.md"))
        full_lower_map, stem_lower_map = _build_page_lookup(pages, vault)
        new_targets = _extract_outbound_targets(
            text, source_rel, full_lower_map, stem_lower_map)

        index = {k: set(v) for k, v in load_index(config).items()}
        for target_rel, linkers in list(index.items()):
            if source_rel in linkers and target_rel not in new_targets:
                linkers.discard(source_rel)
            if not linkers:
                del index[target_rel]
        for target_rel in new_targets:
            index.setdefault(target_rel, set()).add(source_rel)

        _save_index(config, {k: sorted(v) for k, v in sorted(index.items())})
    except Exception as exc:  # fail-open
        log.debug("backlinks: update_for_page failed for %r: %s", page, exc)


def make_backlinks_subscriber(config) -> Callable[[dict], Awaitable[None]]:
    """EventBus subscriber: keeps the index current on `vault_changed`.

    CREATE/UPDATE (and other same-identity events like SECTION/JOURNAL/MOVE)
    take the fast incremental path (`update_for_page`) since only the
    changed page's outbound links can have moved. DELETE/RENAME change a
    page's identity (its own rel-path key disappears, appears, or both),
    which `update_for_page` cannot correct — see module docstring — so
    those trigger a full `rebuild_index` instead. Rare enough in practice
    that the cost is a non-issue, and correctness beats speed here.

    Fail-open — never propagates into the publishing turn.
    """
    async def handle(event: dict) -> None:
        try:
            if event.get("type") != "vault_changed":
                return
            kind = event.get("kind") or ""
            if kind in (KIND_DELETE, KIND_RENAME):
                rebuild_index(config)
                return
            path = event.get("path") or ""
            if not path:
                return
            update_for_page(config, path)
        except Exception as exc:  # fail-open
            log.debug("backlinks subscriber error: %s", exc)

    return handle
