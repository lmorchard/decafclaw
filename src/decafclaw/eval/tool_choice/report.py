"""Scoring + report formatting for the tool-choice eval.

Pure functions consuming a list of ``CaseResult`` and producing
strings the CLI prints. Kept side-effect-free so the unit tests can
construct synthetic inputs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .runner import CaseResult


@dataclass(frozen=True)
class PairOverlap:
    """Aggregated overlap stats for one (expected, near_miss) pair."""
    expected: str
    near_miss: str
    swapped: int            # cases where the model picked near_miss instead
    total: int              # cases that contributed to this pair
    pct: float              # swapped / total in [0.0, 100.0]


def format_case_lines(results: list[CaseResult]) -> list[str]:
    """One line per case: ``PASS  name`` or ``FAIL  name  picked X; expected Y``."""
    lines: list[str] = []
    for r in results:
        if r.passed:
            lines.append(f"PASS  {r.case.name}")
        else:
            lines.append(
                f"FAIL  {r.case.name}    "
                f"picked {r.picked}; expected {r.case.expected}"
            )
    return lines


def format_summary(results: list[CaseResult]) -> str:
    """Single-line totals: ``Summary: X/Y passed (Z%)``."""
    total = len(results)
    if total == 0:
        return "Summary: 0/0 passed"
    passed = sum(1 for r in results if r.passed)
    pct = (passed / total) * 100
    return f"Summary: {passed}/{total} passed ({pct:.0f}%)"


def compute_pair_overlap(results: list[CaseResult]) -> list[PairOverlap]:
    """Aggregate per-(expected, near_miss) overlap.

    Each case contributes one row per ``near_miss`` tool. ``swapped``
    increments when ``picked == near_miss``. ``total`` is the count of
    cases that referenced this pair (i.e. had ``expected=A`` and
    ``near_miss`` containing ``B``). Sorted by overlap pct descending,
    ties broken by ``expected`` then ``near_miss`` for stable output.
    """
    counters: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"swapped": 0, "total": 0}
    )
    for r in results:
        for nm in r.case.near_miss:
            key = (r.case.expected, nm)
            counters[key]["total"] += 1
            if r.picked == nm:
                counters[key]["swapped"] += 1

    rows = []
    for (expected, nm), c in counters.items():
        total = c["total"]
        swapped = c["swapped"]
        pct = (swapped / total * 100) if total else 0.0
        rows.append(PairOverlap(
            expected=expected, near_miss=nm,
            swapped=swapped, total=total, pct=pct,
        ))

    rows.sort(key=lambda r: (-r.pct, r.expected, r.near_miss))
    return rows


def format_pair_overlap(rows: list[PairOverlap]) -> list[str]:
    """Human-readable pair-overlap table; appends ``← tighten`` on rows
    where the swap rate is at least 50%."""
    if not rows:
        return ["Pair overlap: (no pairs)"]
    lines = ["Pair overlap (sorted by overlap %):"]
    # Right-pad the pair string to the longest one for readable columns.
    pairs = [f"{r.expected} \u2194 {r.near_miss}" for r in rows]
    width = max(len(p) for p in pairs)
    for pair, r in zip(pairs, rows, strict=False):
        marker = "  \u2190 tighten" if r.pct >= 50 else ""
        lines.append(
            f"  {pair.ljust(width)}  {r.swapped}/{r.total} swapped ({r.pct:.0f}%){marker}"
        )
    return lines


def compute_confusion_matrix(
    results: list[CaseResult],
) -> dict[str, dict[str, int]]:
    """Return ``expected → picked → count`` across all results."""
    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in results:
        matrix[r.case.expected][r.picked] += 1
    # Convert defaultdicts to plain dicts for stable test comparisons.
    return {k: dict(v) for k, v in matrix.items()}


def format_confusion_matrix(
    matrix: dict[str, dict[str, int]],
) -> list[str]:
    """Flat readable form: ``<expected>: picked X (n), picked Y (n)``.

    Only emits rows where the model picked something other than the
    expected tool at least once — a row of pure expected-matches is
    boring noise.
    """
    if not matrix:
        return ["Confusion matrix: (empty)"]
    lines = ["Confusion matrix (off-diagonal only):"]
    any_off_diagonal = False
    for expected in sorted(matrix.keys()):
        picks = matrix[expected]
        off = {p: n for p, n in picks.items() if p != expected}
        if not off:
            continue
        any_off_diagonal = True
        on_diag = picks.get(expected, 0)
        off_str = ", ".join(
            f"picked {p} ({n})"
            for p, n in sorted(off.items(), key=lambda kv: -kv[1])
        )
        lines.append(
            f"  {expected}: correct ({on_diag}) — {off_str}"
        )
    if not any_off_diagonal:
        lines.append("  (no off-diagonal picks — all cases hit their expected tool)")
    return lines
