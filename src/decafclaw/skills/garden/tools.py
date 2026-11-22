# No `from __future__ import annotations` here: the skill loader execs this
# module in a fresh object that can leave sys.modules, and postponed dataclass
# annotations then crash loading (see docs/skills.md). Quote them instead.

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from decafclaw import backlinks, retrieval_telemetry, tags
from decafclaw.frontmatter import get_frontmatter_field, parse_frontmatter
from decafclaw.media import ToolResult
from decafclaw.skills.vault.tools import tool_vault_update_frontmatter

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

# Rounding precision for written scores and the unchanged-skip comparison.
# Three decimal places is plenty of resolution for a 0..1 importance score
# and avoids rewriting a page for float noise between runs.
_SCORE_PRECISION = 3


@dataclass
class SkillConfig:
    dry_run: bool = False


_skill_config = SkillConfig()


def init(config, skill_config: SkillConfig):
    global _skill_config
    _skill_config = skill_config


def _iter_importance_candidates(config) -> list[tuple[str, Path]]:
    """List (vault-relative POSIX path, Path) for every scoreable agent page."""
    pages_dir = config.vault_agent_pages_dir
    if not pages_dir.is_dir():
        return []
    vault = config.vault_root
    return [(path.relative_to(vault).as_posix(), path) for path in sorted(pages_dir.rglob("*.md"))]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _norm(value: float, max_value: float) -> float:
    return value / max_value if max_value > 0 else 0.0


def compute_importance_signals(config) -> dict[str, tuple[int, int]]:
    pages = [rel for rel, _ in _iter_importance_candidates(config)]
    stats = retrieval_telemetry.aggregate(retrieval_telemetry.load_records(config))
    return {p: (stats.get(p, {}).get("retrieval_count", 0), backlinks.inbound_count(config, p)) for p in pages}


def compute_importance_scores(config) -> dict[str, float]:
    weights = config.importance
    signals = compute_importance_signals(config)
    retrieval_freq = {p: rc for p, (rc, _ic) in signals.items()}
    inbound_links = {p: ic for p, (_rc, ic) in signals.items()}

    max_retrieval = max(retrieval_freq.values(), default=0)
    max_inbound = max(inbound_links.values(), default=0)

    return {
        p: _clamp01(
            weights.w_retrieval * _norm(retrieval_freq[p], max_retrieval)
            + weights.w_inbound * _norm(inbound_links[p], max_inbound)
        )
        for p in signals
    }


async def tool_vault_recompute_importance(ctx: "Context", dry_run: bool = False) -> ToolResult:
    log.info(f"[tool:vault_recompute_importance] dry_run={dry_run}")
    scores = compute_importance_scores(ctx.config)
    signals = compute_importance_signals(ctx.config)

    deltas: list[dict] = []
    written = 0
    for rel, new_score in sorted(scores.items()):
        retrieval_count, inbound_count = signals.get(rel, (0, 0))
        if retrieval_count == 0 and inbound_count == 0:
            continue

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

    if dry_run:
        text = f"Dry run: {len(deltas)} of {len(scores)} scored page(s) would change importance."
    else:
        text = f"Recomputed importance for {len(scores)} page(s): {written} updated, {len(scores) - written} unchanged."
    return ToolResult(text=text, data={"deltas": deltas, "dry_run": dry_run})


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
