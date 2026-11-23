"""Stage 0: free archive signals before any model call (#1738).

Two measurements:

* Offered-option recapitulation: how often the assistant listed choices and
  the next user message picked one. This is the no-model ceiling band.
* Vault-direction calibration: the page-overlap metric the Stage 2 predictor
  will be scored with, run against trivial predictors so floor and ceiling are
  known. Ground truth is the turn's archived ``provenance`` pages when the
  corpus has them; otherwise the pages retrieved by the real next message,
  recorded explicitly as ``retrieval_proxy``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from .corpus import CorpusScan, TurnBoundary, recapitulation_kind, scan_corpus
from .pages import PageCase, PageRetriever, calibrate_pages, connected_page_keys


@dataclass
class Stage0Result:
    summary: dict
    details: list[dict]


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


async def run_stage0(
    config,
    retriever: PageRetriever,
    *,
    default_model: str = "",
    tail_chars: int = 6000,
    limit: int | None = None,
    top_k: int = 5,
) -> Stage0Result:
    scan: CorpusScan = scan_corpus(
        config,
        default_model=default_model,
        tail_chars=tail_chars,
        limit=limit,
    )
    connected = connected_page_keys(config)

    offered = [boundary for boundary in scan.boundaries if boundary.offered and boundary.offered.offers_choice]
    kinds: dict[str, int] = {}
    recapitulated = 0
    repeats = 0
    for boundary in scan.boundaries:
        kind = recapitulation_kind(boundary.actual_next, boundary.offered)
        if kind:
            recapitulated += 1
            kinds[kind] = kinds.get(kind, 0) + 1
        if boundary.assistant_before and _is_whole_repeat(boundary):
            repeats += 1

    page_cases = [
        PageCase(
            turn_id=boundary.turn_id,
            actual_next=boundary.actual_next,
            previous_user=boundary.previous_user,
            assistant_before=boundary.assistant_before,
            provenance_pages=boundary.provenance_pages,
        )
        for boundary in scan.boundaries
    ]
    page_summary, details_by_turn = await calibrate_pages(
        page_cases,
        retriever,
        top_k=top_k,
        connected=connected,
    )
    details = list(details_by_turn.values())
    page_summary["mean_truth_pages"] = page_summary.get("mean_truth_pages", 0.0)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": {
            "conversations": scan.conversations,
            "conversations_with_boundaries": scan.conversations_with_boundaries,
            "boundaries": len(scan.boundaries),
            "archive_roots": scan.archive_roots,
            "tail_chars": tail_chars,
        },
        "offered_options": {
            "offered_turns": len(offered),
            "offered_rate": _rate(len(offered), len(scan.boundaries)),
            "recapitulated": recapitulated,
            "recapitulation_rate_of_all": _rate(recapitulated, len(scan.boundaries)),
            "recapitulation_rate_when_offered": _rate(recapitulated, len(offered)),
            "kinds": kinds,
            "whole_message_repeat": repeats,
            "whole_message_repeat_rate": _rate(repeats, len(scan.boundaries)),
        },
        "final_turns": {
            "count": scan.final_user_turns,
            "stop_like": scan.final_turns_that_stop,
            "stop_rate": _rate(scan.final_turns_that_stop, scan.final_user_turns),
        },
        "pages": page_summary,
    }
    return Stage0Result(summary=summary, details=details)


def _is_whole_repeat(boundary: TurnBoundary) -> bool:
    from .corpus import is_whole_repeat

    return is_whole_repeat(boundary.actual_next, boundary.assistant_before)


def format_stage0(summary: dict) -> str:
    corpus = summary["corpus"]
    offered = summary["offered_options"]
    final = summary["final_turns"]
    pages = summary["pages"]
    lines = [
        "Stage 0 — free archive signals",
        f"  corpus: {corpus['conversations']} conversations, "
        f"{corpus['conversations_with_boundaries']} with boundaries, "
        f"{corpus['boundaries']} settled boundaries",
        "",
        "Offered-option recapitulation",
        f"  assistant offered a choice: {offered['offered_turns']} ({offered['offered_rate']:.1%} of boundaries)",
        f"  next message picked one:    {offered['recapitulated']} "
        f"({offered['recapitulation_rate_when_offered']:.1%} when offered, "
        f"{offered['recapitulation_rate_of_all']:.1%} of all)",
        f"  kinds: {offered['kinds']}",
        f"  whole-message repeat:       {offered['whole_message_repeat']} "
        f"({offered['whole_message_repeat_rate']:.1%}) — not a choice; reported separately",
        "",
        "Final turns",
        f"  stop-like last user message: {final['stop_like']}/{final['count']} ({final['stop_rate']:.1%})",
        "",
        f"Vault-direction calibration — {pages['retriever']}, top_k={pages['top_k']}, "
        f"{pages['vault_pages']} vault entries",
        f"  ground truth: {pages['truth_sources']}; empty truth: {pages['empty_ground_truth']}; "
        f"mean truth pages: {pages['mean_truth_pages']}",
        f"  analytic random floor: {pages['analytic_random_floor']:.3f}",
    ]
    for name, stats in pages["baselines"].items():
        lines.append(f"  {name:24s} mean {stats['mean']:.3f}  median {stats['median']:.3f}")
    return "\n".join(lines)


def write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
