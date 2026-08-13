from __future__ import annotations

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
    return [
        (path.relative_to(vault).as_posix(), path)
        for path in sorted(pages_dir.rglob("*.md"))
    ]

def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))

def _norm(value: float, max_value: float) -> float:
    return value / max_value if max_value > 0 else 0.0

def compute_importance_signals(config) -> dict[str, tuple[int, int]]:
    pages = [rel for rel, _ in _iter_importance_candidates(config)]
    stats = retrieval_telemetry.aggregate(retrieval_telemetry.load_records(config))
    return {
        p: (stats.get(p, {}).get("retrieval_count", 0), backlinks.inbound_count(config, p))
        for p in pages
    }

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
            ctx, rel, {"importance": new_rounded}, overwrite=True,
        )
        if result.text.startswith("[error:"):
            log.warning(
                f"vault_recompute_importance: failed to write {rel}: {result.text}"
            )
            continue
        written += 1

    if dry_run:
        text = (
            f"Dry run: {len(deltas)} of {len(scores)} scored page(s) "
            f"would change importance."
        )
    else:
        text = (
            f"Recomputed importance for {len(scores)} page(s): "
            f"{written} updated, {len(scores) - written} unchanged."
        )
    return ToolResult(text=text, data={"deltas": deltas, "dry_run": dry_run})

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

            rel_to_pages = p[len(agent_pages_prefix):]
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
        return ToolResult(text=f"[Dry run] Proposed {len(proposed_moves)} page moves into clusters.", data={"moves": proposed_moves})
    else:
        return ToolResult(text=f"Reorganized pages. Moved {executed} pages to new cluster folders.", data={"moves": proposed_moves})


# -- Registry ---------------------------------------------------------------

TOOLS = {
    "vault_recompute_importance": tool_vault_recompute_importance,
    "vault_reorganize_folders": tool_vault_reorganize_folders,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "vault_recompute_importance",
            "description": "Deterministically recompute every agent page's `importance` frontmatter...",
            "parameters": {
                "type": "object",
                "properties": {
                    "dry_run": {"type": "boolean"}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_reorganize_folders",
            "description": "Detect clusters of 3+ related agent pages and move them into dedicated folders. Resolves and updates wiki-links.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dry_run": {"type": "boolean"}
                },
            },
        },
    },
]
