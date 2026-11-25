# No `from __future__ import annotations` here: the skill loader execs this
# module in a fresh object that can leave sys.modules, and postponed dataclass
# annotations then crash loading (see docs/skills.md). Quote them instead.

import bisect
import difflib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from decafclaw import backlinks, tags
from decafclaw.frontmatter import (
    get_frontmatter_field,
    parse_frontmatter,
    parse_frontmatter_block,
    split_frontmatter,
)
from decafclaw.media import ToolResult
from decafclaw.skills.vault._events import KIND_UPDATE, publish_vault_changed
from decafclaw.skills.vault._sections import Document
from decafclaw.skills.vault.tools import (
    _WIKI_LINK_RE,
    _reindex_page,
    resolve_page,
    tool_vault_delete,
    tool_vault_update_frontmatter,
)

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

# Rounding precision for written scores and the unchanged-skip comparison.
# PageRank values are small (they sum to 1 across the graph; most pages sit
# near the floor), so three decimals would collapse too many distinct scores.
# Six keeps the tie-break meaningful without chasing float noise.
_SCORE_PRECISION = 6


@dataclass
class SkillConfig:
    dry_run: bool = False
    # Vault folders whose pages are machine-generated rather than curated.
    # They are excluded from graph metrics, broken-link scanning, and
    # importance scoring: linking them would be inventing structure for
    # content nobody authored, and they skew every count they appear in
    # (notifications/ alone was 216 pages at 69% isolate).
    excluded_folders: tuple[str, ...] = ("notifications", "kindle")


_DEFAULT_EXCLUDED_FOLDERS = ("notifications", "kindle")


def _excluded_folders() -> tuple[str, ...]:
    """Agent-pages-relative folder prefixes that are out of scope for gardening."""
    configured = getattr(_skill_config, "excluded_folders", None)
    return tuple(configured) if configured is not None else _DEFAULT_EXCLUDED_FOLDERS


def _is_excluded(rel: str) -> bool:
    """True when a vault-relative page path falls under an excluded folder."""
    parts = rel.split("/")
    # Compare only the folder portion beneath agent/pages/.
    folders = parts[2:-1] if len(parts) > 3 and parts[0] == "agent" and parts[1] == "pages" else parts[:-1]
    return any(folder in _excluded_folders() for folder in folders)


_skill_config = SkillConfig()


def init(config, skill_config: SkillConfig):
    global _skill_config
    _skill_config = skill_config


def _iter_importance_candidates(config) -> list[tuple[str, Path]]:
    """List (vault-relative POSIX path, Path) for every scoreable agent page.

    Pages under an excluded folder (machine-generated content — see
    ``SkillConfig.excluded_folders``) are omitted, so they never participate in
    importance scoring, graph metrics, or broken-link scanning.
    """
    pages_dir = config.vault_agent_pages_dir
    if not pages_dir.is_dir():
        return []
    vault = config.vault_root
    return [
        (rel, path)
        for path in sorted(pages_dir.rglob("*.md"))
        for rel in (path.relative_to(vault).as_posix(),)
        if not _is_excluded(rel)
    ]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_importance_signals(config) -> dict[str, float]:
    """PageRank for every scoreable agent page (#1713).

    PageRank is computed over the full backlink index (including journal and
    user-note sources) and persisted beside it, so this reads the cached
    scores rather than recomputing per page. Retrieval telemetry is
    deliberately absent — it made the score self-referential.
    """
    pages = [rel for rel, _ in _iter_importance_candidates(config)]
    ranks = backlinks.load_pagerank(config)
    return {p: ranks.get(p, 0.0) for p in pages}


def compute_importance_scores(
    config,
    signals: dict[str, float] | None = None,
) -> dict[str, float]:
    weights = config.importance
    if signals is None:
        signals = compute_importance_signals(config)
    return {p: _clamp01(weights.w_inbound * rank) for p, rank in signals.items()}


async def tool_vault_recompute_importance(ctx: "Context", dry_run: bool = False) -> ToolResult:
    log.info(f"[tool:vault_recompute_importance] dry_run={dry_run}")
    signals = compute_importance_signals(ctx.config)
    scores = compute_importance_scores(ctx.config, signals)

    deltas: list[dict] = []
    written = 0
    for rel, new_score in sorted(scores.items()):
        path = ctx.config.vault_root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.debug(f"vault_recompute_importance: could not read {rel}: {exc}")
            continue

        metadata, _ = parse_frontmatter(text)
        old_score = cast("float | None", get_frontmatter_field(metadata, "importance"))
        new_rounded = round(new_score, _SCORE_PRECISION)
        if old_score is not None and round(old_score, _SCORE_PRECISION) == new_rounded:
            continue

        deltas.append({"path": rel, "old": old_score, "new": new_rounded})
        if dry_run:
            continue

        result = await tool_vault_update_frontmatter(
            ctx,
            rel,
            {"importance": new_rounded},
            overwrite=True,
        )
        if result.text.startswith("[error:"):
            log.warning(f"vault_recompute_importance: failed to write {rel}: {result.text}")
            continue
        written += 1
        ctx.tools.progress_text = f"{written} of {len(scores)} page(s) written so far"

    if dry_run:
        text = f"Dry run: {len(deltas)} of {len(scores)} scored page(s) would change importance."
    else:
        text = f"Recomputed importance for {len(scores)} page(s): {written} updated, {len(scores) - written} unchanged."
    return ToolResult(text=text, data={"deltas": deltas, "dry_run": dry_run})


async def tool_vault_delete_plan(
    ctx: "Context",
    pages: list[str],
    dry_run: bool | None = None,
    allow_broken_links: bool = False,
) -> ToolResult:
    """Delete a batch of vault pages with per-item outcomes.

    ``dry_run`` defaults from the garden skill config and is forced on in
    unattended contexts (no ``request_confirmation``), mirroring the single
    delete tool's guardrail (#1717).
    """
    if not pages:
        return ToolResult(text="[error: pages list is required]")
    if dry_run is None:
        dry_run = _skill_config.dry_run or ctx.request_confirmation is None
    log.info(f"[tool:vault_delete_plan] pages={len(pages)} dry_run={dry_run}")

    requested = list(dict.fromkeys(pages))
    items: list[dict] = []
    for page in requested:
        result = await tool_vault_delete(
            ctx,
            page,
            allow_broken_links=allow_broken_links,
            dry_run=dry_run,
        )
        items.append(
            {
                "page": page,
                "ok": not result.text.startswith("[error:"),
                "text": result.text,
                "data": result.data,
            }
        )

    ok_count = sum(1 for item in items if item["ok"])
    failed = len(items) - ok_count
    deleted = sum(1 for item in items if item["ok"] and not item["data"].get("dry_run"))
    would_delete = sum(1 for item in items if item["ok"] and item["data"].get("would_delete"))
    blocked = sum(1 for item in items if item["ok"] and item["data"].get("blocked"))

    if dry_run:
        text = (
            f"[dry run] Delete plan: {len(items)} requested; "
            f"{would_delete} would delete, {blocked} blocked, {failed} failed."
        )
    else:
        text = f"Delete plan: {len(items)} requested; {deleted} deleted, {failed} failed/refused."
    return ToolResult(
        text=text,
        data={
            "items": items,
            "dry_run": dry_run,
            "requested": len(items),
            "deleted": deleted,
            "would_delete": would_delete,
            "blocked": blocked,
            "failed": failed,
        },
    )


_MIN_LINK_TITLE_LENGTH = 3


def _strip_fenced_code(text: str) -> str:
    """Blank out fenced code blocks while preserving line numbering."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


async def tool_vault_link_opportunities(
    ctx: "Context",
    min_occurrences: int = 1,
    limit: int = 50,
) -> ToolResult:
    """Find unlinked exact mentions of agent-page titles (#1715).

    Frontmatter, fenced code, and text already inside ``[[...]]`` are
    excluded. Pure string matching over agent pages; no LLM, embeddings, or
    network calls. Reports candidates only — selecting edges stays with the
    model.
    """
    log.info(f"[tool:vault_link_opportunities] min_occurrences={min_occurrences} limit={limit}")
    pages = dict(_iter_importance_candidates(ctx.config))
    if not pages:
        return ToolResult(
            text="No link opportunities: no agent pages found.",
            data={"opportunities": [], "counts": {"pages_scanned": 0, "candidates": 0}, "truncated": False},
        )

    vault = ctx.config.vault_root
    all_pages = sorted(vault.rglob("*.md"))
    full_lower_map, stem_lower_map = backlinks._build_page_lookup(all_pages, vault)

    title_to_rel: dict[str, str | None] = {}
    for rel in pages:
        title = Path(rel).stem.strip()
        if len(title) < _MIN_LINK_TITLE_LENGTH:
            continue
        key = title.lower()
        if key in title_to_rel and title_to_rel[key] != rel:
            title_to_rel[key] = None  # ambiguous title: skip rather than guess
        else:
            title_to_rel.setdefault(key, rel)
    usable = {title: rel for title, rel in title_to_rel.items() if rel is not None}
    titles = sorted(usable, key=lambda title: (-len(title), title))
    if not titles:
        return ToolResult(
            text="No link opportunities: no usable page titles.",
            data={"opportunities": [], "counts": {"pages_scanned": len(pages), "candidates": 0}, "truncated": False},
        )
    pattern = re.compile(
        r"(?<![\w])(?:" + "|".join(re.escape(title) for title in titles) + r")(?![\w])",
        re.IGNORECASE,
    )

    minimum = max(1, int(min_occurrences))
    opportunities: list[dict] = []
    for rel, path in sorted(pages.items()):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.debug("vault_link_opportunities: could not read %s: %s", rel, exc)
            continue
        _raw_yaml, body = split_frontmatter(text)
        body = _strip_fenced_code(body)

        linked_targets: set[str] = set()
        for match in _WIKI_LINK_RE.finditer(body):
            target = backlinks._resolve_link_target(match.group(1), full_lower_map, stem_lower_map)
            if target is not None:
                linked_targets.add(target)

        counts: dict[str, int] = {}
        context_by_target: dict[str, str] = {}
        for match in pattern.finditer(body):
            target = usable.get(match.group(0).lower())
            if target is None:
                continue
            counts[target] = counts.get(target, 0) + 1
            if target not in context_by_target:
                line_no = body[: match.start()].count("\n")
                lines = body.splitlines()
                context_by_target[target] = lines[line_no].strip()[:200] if line_no < len(lines) else ""

        for target, count in counts.items():
            if target == rel or target in linked_targets or count < minimum:
                continue
            opportunities.append(
                {
                    "page": rel,
                    "target": target,
                    "title": Path(target).stem,
                    "occurrences": count,
                    "sample_context": context_by_target.get(target, ""),
                }
            )

    opportunities.sort(key=lambda item: (-item["occurrences"], item["page"], item["target"]))
    total = len(opportunities)
    bounded = opportunities[: max(0, int(limit))]
    if bounded:
        lines = [
            f"- {entry['page']} -> {entry['target']} ({entry['occurrences']}x): {entry['sample_context']}"
            for entry in bounded[:10]
        ]
        text = f"{total} link opportunity(ies) across {len(pages)} agent page(s).\n" + "\n".join(lines)
    else:
        text = f"No unlinked title mentions found across {len(pages)} agent page(s)."
    return ToolResult(
        text=text,
        data={
            "opportunities": bounded,
            "counts": {"pages_scanned": len(pages), "candidates": total},
            "truncated": total > len(bounded),
        },
    )


async def tool_vault_apply_link_plan(
    ctx: "Context",
    items: list[dict],
    dry_run: bool | None = None,
) -> ToolResult:
    """Apply an approved link plan in one batch (#1715).

    Each item needs ``page``, ``text``, and either ``insert_in_section`` or
    ``insert_after_heading``. Re-applying the same text is a no-op. Returns
    per-item outcomes so a partial batch is observable.
    """
    if not items:
        return ToolResult(text="[error: items list is required]")
    if dry_run is None:
        dry_run = _skill_config.dry_run
    log.info(f"[tool:vault_apply_link_plan] items={len(items)} dry_run={dry_run}")

    agent_pages = ctx.config.vault_agent_pages_dir.resolve()
    outcomes: list[dict] = []
    for item in items:
        page = str(item.get("page") or "").strip()
        text = str(item.get("text") or "").strip()
        section = item.get("insert_in_section")
        after = item.get("insert_after_heading")
        section_target = str(section or after or "").strip()
        if not page or not text or not section_target:
            outcomes.append(
                {
                    "page": page,
                    "ok": False,
                    "error": "page, text, and insert_in_section or insert_after_heading are required",
                }
            )
            continue

        path = resolve_page(ctx.config, page)
        if path is None:
            outcomes.append({"page": page, "ok": False, "error": "page not found"})
            continue
        try:
            path.resolve().relative_to(agent_pages)
        except ValueError:
            outcomes.append({"page": page, "ok": False, "error": "page is outside agent/pages"})
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            outcomes.append({"page": page, "ok": False, "error": f"could not read page: {exc}"})
            continue
        if any(line.strip() == text for line in content.splitlines()):
            outcomes.append({"page": page, "ok": True, "skipped": True, "text": "already present"})
            continue

        doc = Document.from_text(content)
        target_section = doc.find_section(section_target)
        if target_section is None:
            candidates = doc.section_candidates(section_target)
            hint = f"; candidates: {', '.join(candidates)}" if candidates else ""
            outcomes.append({"page": page, "ok": False, "error": f"section not found: {section_target}{hint}"})
            continue

        if dry_run:
            outcomes.append(
                {
                    "page": page,
                    "ok": True,
                    "skipped": False,
                    "dry_run": True,
                    "text": f"would insert into '{target_section.title}'",
                }
            )
            continue

        if section:
            doc.append(target_section, text)
        else:
            doc.prepend(target_section, text)
        path.write_text(doc.to_text(), encoding="utf-8")
        await _reindex_page(ctx, path)
        await publish_vault_changed(ctx.event_bus, ctx.config, kind=KIND_UPDATE, path=path)
        outcomes.append({"page": page, "ok": True, "skipped": False, "text": f"inserted into '{target_section.title}'"})

    applied = sum(1 for item in outcomes if item["ok"] and not item.get("skipped") and not item.get("dry_run"))
    skipped = sum(1 for item in outcomes if item.get("skipped"))
    would_apply = sum(1 for item in outcomes if item.get("dry_run"))
    failed = sum(1 for item in outcomes if not item["ok"])
    if dry_run:
        text = f"[dry run] Link plan: {len(outcomes)} item(s); {would_apply} would apply, {skipped} skipped, {failed} failed."
    else:
        text = f"Link plan: {len(outcomes)} item(s); {applied} applied, {skipped} skipped, {failed} failed."
    return ToolResult(
        text=text,
        data={
            "items": outcomes,
            "dry_run": dry_run,
            "applied": applied,
            "skipped": skipped,
            "would_apply": would_apply,
            "failed": failed,
        },
    )


_GRAPH_SECTIONS = ("totals", "components", "isolates", "sinks", "toeholds", "pages")
_SINK_MIN_OUTBOUND = 10
_SINK_MAX_INBOUND = 2
_TOEHOLD_MIN_INBOUND = 10
_TOEHOLD_MAX_OUTBOUND = 1


def _weak_components(
    agent_pages: dict[str, Path],
    outbound: dict[str, set[str]],
) -> list[list[str]]:
    """Weakly-connected components over agent pages (undirected)."""
    adjacency = {rel: set() for rel in agent_pages}
    for source, targets in outbound.items():
        if source not in adjacency:
            continue
        for target in targets:
            if target in adjacency and target != source:
                adjacency[source].add(target)
                adjacency[target].add(source)

    seen: set[str] = set()
    components: list[list[str]] = []
    for rel in sorted(adjacency):
        if rel in seen:
            continue
        seen.add(rel)
        stack = [rel]
        component: list[str] = []
        while stack:
            node = stack.pop()
            component.append(node)
            for neighbor in adjacency[node]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component))
    return components


async def tool_vault_graph_report(
    ctx: "Context",
    sections: list[str] | None = None,
    top_n: int = 50,
) -> ToolResult:
    """Report the agent-page link graph: totals, components, isolates, sinks,
    toeholds, and a bounded degree/PageRank table. Pure arithmetic over the
    persisted backlink index (#1714)."""
    log.info(f"[tool:vault_graph_report] sections={sections} top_n={top_n}")
    requested = list(_GRAPH_SECTIONS) if sections is None else list(sections)
    unknown = sorted(set(requested) - set(_GRAPH_SECTIONS))
    if unknown:
        return ToolResult(
            text=(
                f"[error: unknown graph section(s): {', '.join(unknown)}. Valid sections: {', '.join(_GRAPH_SECTIONS)}]"
            )
        )
    page_limit = max(0, min(int(top_n), 1000))

    vault = ctx.config.vault_root
    agent_pages = dict(_iter_importance_candidates(ctx.config))
    if vault.is_dir():
        nodes = {path.relative_to(vault).as_posix() for path in vault.rglob("*.md")}
        index = backlinks.load_index(ctx.config)
    else:
        nodes = set()
        index = {}

    inbound: dict[str, set[str]] = {node: set() for node in nodes}
    outbound: dict[str, set[str]] = {node: set() for node in nodes}
    for target, sources in index.items():
        if target not in nodes:
            continue
        for source in sources:
            if source not in nodes or source == target:
                continue
            outbound[source].add(target)
            inbound[target].add(source)

    edges = sum(len(targets) for targets in outbound.values())
    degrees = {rel: (len(inbound.get(rel, ())), len(outbound.get(rel, ()))) for rel in agent_pages}
    isolates = sorted(rel for rel, (in_count, out_count) in degrees.items() if in_count == 0 and out_count == 0)
    orphans = sorted(rel for rel, (in_count, _out_count) in degrees.items() if in_count == 0)
    sink_tuples = sorted(
        (
            (out_count, rel, in_count)
            for rel, (in_count, out_count) in degrees.items()
            if out_count >= _SINK_MIN_OUTBOUND and in_count <= _SINK_MAX_INBOUND
        ),
        key=lambda item: (-item[0], item[1]),
    )
    sinks = [{"path": rel, "inbound": in_count, "outbound": out_count} for out_count, rel, in_count in sink_tuples]
    toehold_tuples = sorted(
        (
            (in_count, rel, out_count)
            for rel, (in_count, out_count) in degrees.items()
            if in_count >= _TOEHOLD_MIN_INBOUND and out_count <= _TOEHOLD_MAX_OUTBOUND
        ),
        key=lambda item: (-item[0], item[1]),
    )
    toeholds = [
        {"path": rel, "inbound": in_count, "outbound": out_count} for in_count, rel, out_count in toehold_tuples
    ]

    components = _weak_components(agent_pages, outbound)
    components.sort(key=lambda component: (-len(component), component[0] if component else ""))

    pagerank = backlinks.compute_pagerank(index, nodes=nodes) if nodes else {}
    page_tuples = sorted(
        ((pagerank.get(rel, 0.0), rel, in_count, out_count) for rel, (in_count, out_count) in degrees.items()),
        key=lambda item: (-item[0], item[1]),
    )[:page_limit]
    page_rows = [
        {
            "path": rel,
            "inbound": in_count,
            "outbound": out_count,
            "pagerank": round(rank, 6),
        }
        for rank, rel, in_count, out_count in page_tuples
    ]

    largest_component = len(components[0]) if components else 0
    full = {
        "totals": {
            "pages": len(agent_pages),
            "edges": edges,
            "isolates": len(isolates),
            "orphans": len(orphans),
            "components": len(components),
            "largest_component": largest_component,
        },
        "components": [{"size": len(component), "sample": component[:5]} for component in components],
        "isolates": isolates,
        "sinks": sinks,
        "toeholds": toeholds,
        "pages": page_rows,
    }
    data = {section: full[section] for section in _GRAPH_SECTIONS if section in requested}
    text = (
        f"Graph report: {len(agent_pages)} agent page(s), {edges} edge(s), "
        f"{len(isolates)} isolate(s), {len(orphans)} orphan(s), "
        f"{len(components)} component(s) (largest {largest_component}); "
        f"{len(sinks)} sink(s), {len(toeholds)} toehold(s)."
    )
    return ToolResult(text=text, data=data)


_AUDIT_FIELDS = ("summary", "keywords", "tags", "importance")
_TLDR_RE = re.compile(r"^\s*>\s*tl;dr\s*:", re.IGNORECASE | re.MULTILINE)
_WORD_RE = re.compile(r"[a-z0-9]+")
_STALE_SUMMARY_THRESHOLD = 0.25

# Broken-link suggestion tuning (#1744). ``difflib.get_close_matches`` is
# O(candidate count) per query, so the cost scales with vault size.
_SUGGESTION_CUTOFF = 0.6
_SUGGESTION_SCAN = 5
_SUGGESTION_KEEP = 3
# Cap on candidates compared per target. The pool is filled from the target's
# rarest trigrams first, so a full path's common directory trigrams do not
# crowd out the title trigrams that actually identify a near match.
_SUGGESTION_POOL = 550


def _char_trigrams(text: str) -> set[str]:
    if len(text) < 3:
        return {text} if text else set()
    return {text[index : index + 3] for index in range(len(text) - 2)}


def _summary_overlap(summary: str, body: str) -> float | None:
    """Fraction of significant summary words present in the body.

    Advisory only: a crude lexical hint that a summary may not describe the
    current body. Returns None when the summary has no significant words.
    """
    words = [word for word in _WORD_RE.findall(summary.lower()) if len(word) >= 4]
    if not words:
        return None
    body_words = set(_WORD_RE.findall(body.lower()))
    return sum(1 for word in words if word in body_words) / len(words)


class _SuggestionCandidates:
    """Nearest-match candidate pool for broken-link suggestions (#1744).

    ``difflib.get_close_matches`` compares every candidate per query, and the
    old code rebuilt and rescanned the full ~9k-key list for every broken
    link. This indexes the keys by character trigram once per tool call and
    compares only candidates that share a trigram with the target, bounded by
    ``_SUGGESTION_POOL``. Targets shorter than a trigram, and targets whose
    trigrams match no candidate, use the exact length band that
    ``SequenceMatcher`` itself uses to reject impossible lengths.
    """

    def __init__(self, keys: list[str]):
        self._keys = keys
        postings: dict[str, list[int]] = {}
        for index, key in enumerate(keys):
            for gram in _char_trigrams(key):
                postings.setdefault(gram, []).append(index)
        self._postings = postings
        # A short candidate can still clear the cutoff against a three- or
        # four-character target, but it is indexed under its whole text, not
        # under a trigram. Carry those into every pool.
        self._short = [index for index, key in enumerate(keys) if len(key) < 3]
        # Length band for the fallback: ratio <= 2*min(la, lb)/(la+lb).
        by_length = sorted(range(len(keys)), key=lambda index: len(keys[index]))
        self._by_length = by_length
        self._sorted_lengths = [len(keys[index]) for index in by_length]

    def _length_band(self, length: int, cutoff: float) -> list[str]:
        low = cutoff * length / (2.0 - cutoff)
        high = (2.0 - cutoff) * length / cutoff
        start = bisect.bisect_left(self._sorted_lengths, low)
        end = bisect.bisect_right(self._sorted_lengths, high)
        return [self._keys[index] for index in self._by_length[start:end]]

    def _pool(self, target: str, cutoff: float) -> list[str]:
        if len(target) < 3:
            return self._length_band(len(target), cutoff)
        indices = set(self._short)
        grams = sorted(_char_trigrams(target), key=lambda gram: len(self._postings.get(gram, ())))
        for gram in grams:
            postings = self._postings.get(gram)
            if not postings:
                continue
            indices.update(postings)
            if len(indices) >= _SUGGESTION_POOL:
                break
        if len(indices) == len(self._short):
            return self._length_band(len(target), cutoff)
        return [self._keys[index] for index in indices]

    def close_matches(self, target: str, *, n: int, cutoff: float) -> list[str]:
        return difflib.get_close_matches(target, self._pool(target, cutoff), n=n, cutoff=cutoff)


def _suggest_pages(
    target: str,
    candidates: dict[str, str],
    suggestion_candidates: _SuggestionCandidates,
) -> list[str]:
    """Resolve nearest-match candidate keys to up to three page paths."""
    suggestions: list[str] = []
    matches = suggestion_candidates.close_matches(target, n=_SUGGESTION_SCAN, cutoff=_SUGGESTION_CUTOFF)
    for candidate in matches:
        rel_hit = candidates[candidate]
        if rel_hit not in suggestions:
            suggestions.append(rel_hit)
        if len(suggestions) == _SUGGESTION_KEEP:
            break
    return suggestions


async def tool_vault_broken_links(ctx: "Context", limit: int = 50) -> ToolResult:
    """List unresolved [[wiki-links]] in agent pages, with nearest-match
    suggestions. Read-only and deterministic (#1716)."""
    log.info(f"[tool:vault_broken_links] limit={limit}")
    vault = ctx.config.vault_root
    agent_pages = dict(_iter_importance_candidates(ctx.config))
    if not vault.is_dir() or not agent_pages:
        return ToolResult(
            text="No broken links: no agent pages found.",
            data={"broken": [], "counts": {"pages": 0, "links": 0}, "truncated": False},
        )

    all_pages = sorted(vault.rglob("*.md"))
    full_lower_map, stem_lower_map = backlinks._build_page_lookup(all_pages, vault)
    candidates: dict[str, str] = {}
    for key, rel in full_lower_map.items():
        candidates.setdefault(key, rel)
    for key, rels in stem_lower_map.items():
        for rel in rels:
            candidates.setdefault(key, rel)
    suggestion_candidates = _SuggestionCandidates(list(candidates))

    broken: list[dict] = []
    broken_targets: list[str] = []
    affected_pages = 0
    for rel, path in sorted(agent_pages.items()):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.debug("vault_broken_links: could not read %s: %s", rel, exc)
            continue
        _raw_yaml, body = split_frontmatter(text)
        offset = len(text) - len(body)
        page_hits = 0
        for match in _WIKI_LINK_RE.finditer(body):
            raw = match.group(1)
            if backlinks._resolve_link_target(raw, full_lower_map, stem_lower_map) is not None:
                continue
            target = raw.split("|")[0].split("#")[0].strip()
            if not target:
                continue
            target = target[:-3] if target.endswith(".md") else target
            line = text[: offset + match.start()].count("\n") + 1
            broken.append(
                {
                    "page": rel,
                    "link": f"[[{raw}]]",
                    "line": line,
                    "suggestions": [],
                    "kind": "missing",
                }
            )
            broken_targets.append(target.lower())
            page_hits += 1
        if page_hits:
            affected_pages += 1

    bounded = broken[: max(0, int(limit))]
    # ``limit`` bounds the suggestion work, not just the reported slice: the
    # scan is cheap, while each difflib query is O(candidate count). Memoize
    # per distinct normalized target; repeated broken links share the answer.
    suggestion_cache: dict[str, list[str]] = {}
    for entry, target in zip(bounded, broken_targets):
        suggestions = suggestion_cache.get(target)
        if suggestions is None:
            suggestions = _suggest_pages(target, candidates, suggestion_candidates)
            suggestion_cache[target] = suggestions
        entry["suggestions"] = suggestions
        entry["kind"] = "possible_move" if suggestions else "missing"
    if broken:
        examples = []
        for entry in bounded[:10]:
            hint = f" -> {', '.join(entry['suggestions'])}" if entry["suggestions"] else ""
            examples.append(f"- {entry['page']}:{entry['line']} {entry['link']}{hint}")
        text = f"{len(broken)} broken link(s) across {affected_pages} agent page(s).\n" + "\n".join(examples)
    else:
        text = f"No broken links in {len(agent_pages)} agent page(s)."
    return ToolResult(
        text=text,
        data={
            "broken": bounded,
            "counts": {"pages": affected_pages, "links": len(broken)},
            "truncated": len(broken) > len(bounded),
        },
    )


async def tool_vault_frontmatter_audit(ctx: "Context", limit: int = 20) -> ToolResult:
    """Report malformed/missing frontmatter, oversized pages, missing tl;dr,
    and advisory stale summaries. Read-only and deterministic (#1716)."""
    log.info(f"[tool:vault_frontmatter_audit] limit={limit}")
    agent_pages = dict(_iter_importance_candidates(ctx.config))
    malformed: list[dict] = []
    missing: dict[str, list[str]] = {field: [] for field in _AUDIT_FIELDS}
    oversized: list[dict] = []
    missing_tldr: list[dict] = []
    stale_summaries: list[dict] = []

    for rel, path in sorted(agent_pages.items()):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            log.debug("vault_frontmatter_audit: could not read %s: %s", rel, exc)
            continue
        raw_yaml, body = split_frontmatter(text)
        metadata, error = parse_frontmatter_block(raw_yaml)
        if error:
            malformed.append({"path": rel, "error": error.splitlines()[0] if error else "malformed YAML"})
        for field in _AUDIT_FIELDS:
            if get_frontmatter_field(metadata, field) in (None, "", []):
                missing[field].append(rel)

        lines = len(text.splitlines())
        if lines > 100:
            oversized.append({"path": rel, "lines": lines})
        if lines > 20 and not _TLDR_RE.search(body):
            missing_tldr.append({"path": rel, "lines": lines})

        summary = metadata.get("summary")
        if isinstance(summary, str) and summary.strip():
            overlap = _summary_overlap(summary, body)
            if overlap is not None and overlap < _STALE_SUMMARY_THRESHOLD:
                stale_summaries.append({"path": rel, "overlap": round(overlap, 3), "advisory": True})

    bounded_limit = max(0, int(limit))
    counts = {
        "pages_scanned": len(agent_pages),
        "malformed_yaml": len(malformed),
        "missing": {field: len(paths) for field, paths in missing.items()},
        "oversized": len(oversized),
        "missing_tldr": len(missing_tldr),
        "stale_summary": len(stale_summaries),
    }
    data = {
        "counts": counts,
        "malformed_yaml": malformed[:bounded_limit],
        "missing": {field: paths[:bounded_limit] for field, paths in missing.items()},
        "oversized": oversized[:bounded_limit],
        "missing_tldr": missing_tldr[:bounded_limit],
        "stale_summary": stale_summaries[:bounded_limit],
        "truncated": any(
            len(items) > bounded_limit
            for items in (
                malformed,
                oversized,
                missing_tldr,
                stale_summaries,
                *(paths for paths in missing.values()),
            )
        ),
    }
    text = (
        f"Frontmatter audit: {len(agent_pages)} agent page(s); "
        f"malformed YAML={len(malformed)}; "
        + ", ".join(f"missing {field}={len(missing[field])}" for field in _AUDIT_FIELDS)
        + f"; oversized={len(oversized)}, missing tl;dr={len(missing_tldr)}, "
        f"stale summaries={len(stale_summaries)} (advisory)."
    )
    if malformed:
        text += "\nMalformed YAML:\n" + "\n".join(f"- {entry['path']}: {entry['error']}" for entry in malformed[:5])
    return ToolResult(text=text, data=data)


async def tool_vault_find_duplicate_candidates(
    ctx: "Context",
    threshold: float = 0.9,
    limit: int = 20,
) -> ToolResult:
    """Report high-similarity agent-page pairs from the existing embedding
    index. Read-only: never re-embeds or merges (#1716)."""
    from decafclaw.embeddings import find_similar_page_pairs

    bounded_threshold = max(0.0, min(1.0, float(threshold)))
    log.info(f"[tool:vault_find_duplicate_candidates] threshold={bounded_threshold} limit={limit}")
    pairs, indexed_pages = find_similar_page_pairs(
        ctx.config,
        threshold=bounded_threshold,
        limit=max(0, int(limit)),
        source_type="page",
        path_prefix="agent/pages/",
    )
    if indexed_pages == 0:
        text = "No agent page embeddings found; run `make reindex` to build the index."
    elif not pairs:
        text = f"No duplicate candidates above {bounded_threshold:.2f} among {indexed_pages} indexed agent page(s)."
    else:
        text = (
            f"{len(pairs)} duplicate candidate(s) above {bounded_threshold:.2f} "
            f"among {indexed_pages} indexed agent page(s):\n"
            + "\n".join(f"- {pair['a']} <-> {pair['b']} ({pair['similarity']:.3f})" for pair in pairs[:10])
        )
    return ToolResult(
        text=text,
        data={"pairs": pairs, "indexed_pages": indexed_pages, "threshold": bounded_threshold},
    )


async def tool_vault_reorganize_folders(ctx: "Context", dry_run: bool | None = None) -> ToolResult:
    """Detect clusters of 3+ related agent pages and move them into dedicated folders."""
    if dry_run is None:
        dry_run = _skill_config.dry_run

    log.info(f"[tool:vault_reorganize_folders] dry_run={dry_run}")

    all_tags = tags.collect_all_tags(ctx.config)
    vault = ctx.config.vault_root
    agent_pages_prefix = "agent/pages/"

    proposed_moves = []

    for tag_name, tag_info in all_tags.items():
        pages_to_move = []
        for p in tag_info["pages"]:
            if not p.startswith(agent_pages_prefix):
                continue

            rel_to_pages = p[len(agent_pages_prefix) :]
            if "/" not in rel_to_pages:
                pages_to_move.append(p)

        if len(pages_to_move) >= 3:
            dest_folder = agent_pages_prefix + tags.normalize_tag(tag_name) + "/"
            for p in pages_to_move:
                dest_path = dest_folder + Path(p).name
                proposed_moves.append({"src": p, "dest": dest_path})

    executed = 0

    for move in proposed_moves:
        src_path = vault / move["src"]
        dest_path = vault / move["dest"]

        if dry_run:
            log.info(f"Dry run: would move {move['src']} to {move['dest']}")
            continue

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        src_path.rename(dest_path)
        executed += 1

        old_stem = Path(move["src"]).stem
        new_link_target = move["dest"]
        if new_link_target.endswith(".md"):
            new_link_target = new_link_target[:-3]

        for md_file in vault.rglob("*.md"):
            content = md_file.read_text(encoding="utf-8")

            pattern1 = re.compile(rf"\[\[{re.escape(old_stem)}\]\]")
            pattern2 = re.compile(rf"\[\[{re.escape(move['src'])}(\|.*?)?\]\]")

            new_content = content
            new_content = pattern1.sub(f"[[{new_link_target}|{old_stem}]]", new_content)

            def replace_pattern2(m):
                alias = m.group(1) or f"|{old_stem}"
                return f"[[{new_link_target}{alias}]]"

            new_content = pattern2.sub(replace_pattern2, new_content)

            if new_content != content:
                md_file.write_text(new_content, encoding="utf-8")

    if dry_run:
        return ToolResult(
            text=f"[Dry run] Proposed {len(proposed_moves)} page moves into clusters.", data={"moves": proposed_moves}
        )
    else:
        return ToolResult(
            text=f"Reorganized pages. Moved {executed} pages to new cluster folders.", data={"moves": proposed_moves}
        )


# -- Registry ---------------------------------------------------------------

TOOLS = {
    "vault_recompute_importance": tool_vault_recompute_importance,
    "vault_graph_report": tool_vault_graph_report,
    "vault_broken_links": tool_vault_broken_links,
    "vault_frontmatter_audit": tool_vault_frontmatter_audit,
    "vault_find_duplicate_candidates": tool_vault_find_duplicate_candidates,
    "vault_link_opportunities": tool_vault_link_opportunities,
    "vault_apply_link_plan": tool_vault_apply_link_plan,
    "vault_delete_plan": tool_vault_delete_plan,
    "vault_reorganize_folders": tool_vault_reorganize_folders,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "writes": True,
        "function": {
            "name": "vault_recompute_importance",
            "description": "Deterministically recompute every agent page's `importance` frontmatter...",
            "parameters": {
                "type": "object",
                "properties": {"dry_run": {"type": "boolean"}},
            },
        },
    },
    {
        "type": "function",
        "writes": False,
        "priority": "normal",
        "function": {
            "name": "vault_graph_report",
            "description": (
                "Compute the whole agent-page link graph in one read-only call: "
                "totals (pages, edges, isolates, orphans, components, largest "
                "component), weakly-connected components, isolates, sinks (high "
                "outbound / negligible inbound), toeholds (high inbound / "
                "negligible outbound), and a bounded per-page inbound/outbound/"
                "PageRank table. Deterministic over the backlink index — no LLM, "
                "network, or embedding calls. Use this instead of repeatedly "
                "calling vault_backlinks to survey connectivity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": list(_GRAPH_SECTIONS),
                        },
                        "description": (
                            "Optional subset of sections to return (default: all). Use 'totals' for a cheap overview."
                        ),
                    },
                    "top_n": {
                        "type": "integer",
                        "description": (
                            "Maximum rows in the per-page table, sorted by PageRank descending (default 50)."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "writes": False,
        "priority": "normal",
        "function": {
            "name": "vault_link_opportunities",
            "description": (
                "Find agent pages that mention another agent page's exact title "
                "without linking to it. Excludes frontmatter, fenced code, and "
                "text already inside [[...]]; reports occurrence counts and a "
                "context line. Deterministic; proposes edges only, the model "
                "chooses which to accept."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "min_occurrences": {
                        "type": "integer",
                        "description": "Only report pages mentioned at least this many times (default 1).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum opportunities to return (default 50).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "writes": True,
        "priority": "normal",
        "function": {
            "name": "vault_apply_link_plan",
            "description": (
                "Apply an approved batch of link insertions. Each item needs "
                "page, text, and insert_in_section or insert_after_heading. "
                "Returns per-item success/failure; re-applying the same text is "
                "a no-op. dry_run defaults from the skill config — use a dry-run "
                "pass to review the plan before executing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "page": {"type": "string"},
                                "text": {"type": "string"},
                                "insert_in_section": {"type": "string"},
                                "insert_after_heading": {"type": "string"},
                            },
                            "required": ["page", "text"],
                        },
                    },
                    "dry_run": {"type": "boolean"},
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "writes": False,
        "priority": "normal",
        "function": {
            "name": "vault_broken_links",
            "description": (
                "List unresolved [[wiki-links]] in agent pages with line "
                "numbers and nearest-match suggestions. Read-only and "
                "deterministic; resolution matches the backlink index "
                "(aliases and heading anchors are stripped)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum broken-link entries to return (default 50).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "writes": False,
        "priority": "normal",
        "function": {
            "name": "vault_frontmatter_audit",
            "description": (
                "Audit agent-page frontmatter: malformed YAML, missing "
                "summary/keywords/tags/importance, oversized pages (>100 "
                "lines), missing `> tl;dr:` on pages over ~20 lines, and an "
                "advisory stale-summary token-overlap hint. Returns counts "
                "plus bounded examples. Read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum examples per category (default 20).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "writes": False,
        "priority": "normal",
        "function": {
            "name": "vault_find_duplicate_candidates",
            "description": (
                "Find high-similarity agent-page pairs in the existing local "
                "embedding index. Queries stored vectors; never re-embeds, "
                "never merges. Returns candidate pairs plus similarity. If a "
                "page is missing from the index it is skipped."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "threshold": {
                        "type": "number",
                        "description": "Minimum cosine similarity (default 0.9).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum pairs to return (default 20).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "writes": True,
        "priority": "normal",
        "function": {
            "name": "vault_delete_plan",
            "description": (
                "Delete a batch of vault pages and report per-item outcomes. "
                "The single-delete inbound-link guard applies to each page: a "
                "page with inbound links is refused unless allow_broken_links=true. "
                "dry_run defaults from the garden skill config and is forced on "
                "in unattended contexts; use a dry-run pass to review candidates "
                "before executing. Legitimate conflict-duplicate batches have no "
                "inbound links and pass without per-page approvals."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Page paths relative to the vault root.",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": (
                            "Report what would happen without deleting. Defaults "
                            "from the skill config, and true in unattended contexts."
                        ),
                    },
                    "allow_broken_links": {
                        "type": "boolean",
                        "description": (
                            "Allow deleting pages that still have inbound links, reporting which pages become dangling."
                        ),
                    },
                },
                "required": ["pages"],
            },
        },
    },
    {
        "type": "function",
        "writes": True,
        "function": {
            "name": "vault_reorganize_folders",
            "description": "Detect clusters of 3+ related agent pages and move them into dedicated folders. Resolves and updates wiki-links.",
            "parameters": {
                "type": "object",
                "properties": {"dry_run": {"type": "boolean"}},
            },
        },
    },
]
