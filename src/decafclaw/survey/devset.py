"""Deterministic dev-set sampling for the suggested-prompt survey (#1738).

The set is written by script, never hand-curated. Sampling is stratified by
conversation length and recency so the cases do not all come from the few
long conversations or one date range.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .corpus import TurnBoundary, parse_timestamp

DEFAULT_DEVSET_PATH = Path("evals/suggested_prompt/devset.jsonl")
DEFAULT_SEED = 1738


def length_bucket(user_turns: int) -> str:
    assert user_turns >= 2
    if user_turns == 2:
        return "2"
    if user_turns <= 5:
        return "3-5"
    if user_turns <= 9:
        return "6-9"
    return "10+"


def recency_bucket(boundaries: list[TurnBoundary]) -> dict[str, str]:
    """Assign each conversation a recency tercile by its latest boundary."""
    latest: dict[str, tuple] = {}
    for boundary in boundaries:
        stamp = parse_timestamp(boundary.timestamp)
        key = stamp.timestamp() if stamp is not None else float("-inf")
        current = latest.get(boundary.conv_id)
        if current is None or key > current[0]:
            latest[boundary.conv_id] = (key, boundary.conv_id)
    ordered = sorted(latest, key=lambda conv_id: latest[conv_id][0])
    if not ordered:
        return {}
    third = max(1, (len(ordered) + 2) // 3)
    mapping: dict[str, str] = {}
    for index, conv_id in enumerate(ordered):
        if index < third:
            mapping[conv_id] = "old"
        elif index < third * 2:
            mapping[conv_id] = "mid"
        else:
            mapping[conv_id] = "new"
    return mapping


def _sort_hash(seed: int, turn_id: str) -> str:
    return hashlib.sha256(f"{seed}:{turn_id}".encode()).hexdigest()


def sample_boundaries(
    boundaries: list[TurnBoundary],
    *,
    limit: int = 200,
    seed: int = DEFAULT_SEED,
) -> list[TurnBoundary]:
    """Round-robin across length x recency strata, then hash-order within."""
    if limit <= 0 or not boundaries:
        return []
    recency = recency_bucket(boundaries)
    strata: dict[tuple[str, str], list[TurnBoundary]] = {}
    for boundary in boundaries:
        key = (recency.get(boundary.conv_id, "unknown"), length_bucket(boundary.user_turns))
        strata.setdefault(key, []).append(boundary)
    for key in strata:
        strata[key].sort(key=lambda boundary: _sort_hash(seed, boundary.turn_id))
    ordered_keys = sorted(strata)
    chosen: list[TurnBoundary] = []
    positions = {key: 0 for key in ordered_keys}
    while len(chosen) < limit:
        progressed = False
        for key in ordered_keys:
            index = positions[key]
            if index >= len(strata[key]):
                continue
            chosen.append(strata[key][index])
            positions[key] = index + 1
            progressed = True
            if len(chosen) >= limit:
                break
        if not progressed:
            break
    return chosen


def build_devset(boundaries: list[TurnBoundary], *, limit: int = 200, seed: int = DEFAULT_SEED) -> list[dict]:
    recency = recency_bucket(boundaries)
    cases: list[dict] = []
    for boundary in sample_boundaries(boundaries, limit=limit, seed=seed):
        case = boundary.as_devcase()
        case["stratum"] = {
            "recency": recency.get(boundary.conv_id, "unknown"),
            "length": length_bucket(boundary.user_turns),
        }
        cases.append(case)
    return cases


def write_devset(path: Path, cases: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")


def load_devset(path: Path) -> list[dict]:
    cases: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
    return cases
