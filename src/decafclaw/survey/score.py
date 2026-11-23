"""Stage 2: score a baseline next-message predictor (#1738).

One LLM call per case and arm, no execution. Scores four granularities:
normalized exact match, token-set Jaccard, vault-direction page overlap, and
an operator spot-check sheet. The aux and active model arms run on the same
dev set so the cheap-arm question has a number.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..llm import call_llm
from ..model_catalog import effective_active_model, effective_aux_model
from .corpus import (
    analyze_offer,
    jaccard,
    normalize_text,
    recapitulation_kind,
    token_set,
)
from .pages import PageCase, PageRetriever, calibrate_pages, connected_page_keys, page_set, set_jaccard

log = logging.getLogger(__name__)

ABSTAIN_TOKEN = "NONE"
DEFAULT_MAX_CALLS = 1200
JACCARD_THRESHOLD = 0.6

PREDICTOR_SYSTEM_PROMPT = """\
You replay an archived assistant conversation and predict the human's next message.

You see the conversation so far, ending with the assistant's last reply.
Write the single message the human most likely sends next, exactly as the human would write it.

Rules:
- One short message, at most 20 words.
- Do not answer the assistant's question yourself. Write the human's reply.
- If the assistant offered explicit options, pick the one the human would choose.
- If there is no obvious next step, output exactly: NONE
- Output only the message text. No quotes, labels, role prefixes, or explanation.
"""

# USD per million tokens, input/output. Unknown models are reported as unpriced.
_PRICING = {
    "vertex-gemini-flash": (0.30, 2.50),
    "litellm-gemini-flash": (0.30, 2.50),
    "vertex-gemini-pro": (1.25, 10.00),
    "litellm-gemini-pro": (1.25, 10.00),
    "gemini-pro": (1.25, 10.00),
    "claude-haiku-4.5": (1.00, 5.00),
    "claude-sonnet-4.5": (3.00, 15.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}
_AVG_OUTPUT_TOKENS = 40
_SPECIFIC = re.compile(r"https?://\S+|`[^`]+`|\b\S+\.(?:py|md|json|js|ts|ya?ml)\b|\b\d{2,}\b")
_GENERIC = re.compile(
    r"^(what would you like|let me know|how can i help|anything else|is there anything|"
    r"sure,|okay,|i can help)",
    re.I,
)
_ECHO_STOP = re.compile(r"^(let me know|would you like|do you want|should i|shall i)", re.I)


@dataclass
class Arm:
    name: str
    model: str


@dataclass
class CaseResult:
    turn_id: str
    arm: str
    model: str
    suggestion: str
    raw: str
    error: str
    elapsed_ms: int
    usage: dict
    exact: bool
    token_jaccard: float
    jaccard_hit: bool
    direction_jaccard: float | None
    abstained: bool
    failure: str | None


def build_messages(case: dict) -> list[dict]:
    tail = str(case.get("transcript_tail") or "").strip()
    return [
        {"role": "system", "content": PREDICTOR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Conversation so far:\n\n{tail}\n\nHuman's next message:",
        },
    ]


def parse_suggestion(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^(?:Suggestion|Next message|Human|User)\s*:\s*", "", line, flags=re.I)
        line = line.strip().strip("\"'`")
        if normalize_text(line) in {"none", "no suggestion", "n a", "nothing"}:
            return ""
        return line
    return ""


def resolve_model(config, arm: str, case: dict, aux_override: str = "", active_override: str = "") -> str:
    active = active_override or effective_active_model(config, str(case.get("active_model") or ""))
    if arm == "active":
        return active
    if arm == "aux":
        return aux_override or effective_aux_model(config, active)
    raise ValueError(f"unknown arm: {arm}")


def _price(model: str) -> tuple[float, float] | None:
    return _PRICING.get(model)


def estimate_spend(
    cases: list[dict], config, arms: list[str], aux_override: str = "", active_override: str = ""
) -> dict:
    unique: set[tuple[str, str]] = set()
    prompt_chars = 0
    for case in cases:
        chars = len(PREDICTOR_SYSTEM_PROMPT) + len(str(case.get("transcript_tail") or "")) + 40
        for arm in arms:
            key = (resolve_model(config, arm, case, aux_override, active_override), case["turn_id"])
            if key in unique:
                continue
            unique.add(key)
            prompt_chars += chars
    input_tokens = prompt_chars // 4
    output_tokens = len(unique) * _AVG_OUTPUT_TOKENS
    cost = 0.0
    unpriced: set[str] = set()
    input_by_model: dict[str, int] = {}
    for case in cases:
        for arm in arms:
            model = resolve_model(config, arm, case, aux_override, active_override)
            input_by_model[model] = input_by_model.get(model, 0) + 1
    for model, calls in input_by_model.items():
        price = _price(model)
        if price is None:
            unpriced.add(model)
            continue
        cost += calls * (len(PREDICTOR_SYSTEM_PROMPT) + 2000) / 4 / 1_000_000 * price[0]
        cost += calls * _AVG_OUTPUT_TOKENS / 1_000_000 * price[1]
    return {
        "calls": len(unique),
        "input_tokens_estimate": input_tokens,
        "output_tokens_estimate": output_tokens,
        "cost_usd_estimate": round(cost, 3),
        "unpriced_models": sorted(unpriced),
        "assumption": "~4 chars/token, 40 output tokens/call, average 2000-char prompt",
    }


def load_cache(path: Path) -> dict[tuple[str, str], dict]:
    cache: dict[tuple[str, str], dict] = {}
    if not path.exists():
        return cache
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                log.warning("Skipping corrupt cache line %d in %s", lineno, path)
                continue
            if record.get("error"):
                continue
            cache[(record["model"], record["turn_id"])] = record
    return cache


def append_cache(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


async def _call_one(config, case: dict, model: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        started = time.monotonic()
        try:
            response = await call_llm(config, build_messages(case), model_name=model)
            raw = str(response.get("content") or "")
            usage = response.get("usage") or {}
            error = ""
        except Exception as exc:  # network/provider errors belong in the result
            raw = ""
            usage = {}
            error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "turn_id": case["turn_id"],
        "model": model,
        "raw": raw,
        "usage": usage,
        "error": error,
        "elapsed_ms": elapsed_ms,
    }


def classify_failure(case: dict, suggestion: str, token_jaccard: float, exact: bool) -> str | None:
    if exact:
        return None
    if not suggestion:
        return "abstained"
    if len(suggestion.split()) > 25:
        return "too_long"
    if _GENERIC.search(suggestion) or _ECHO_STOP.search(suggestion):
        return "generic_filler"
    offer = analyze_offer(str(case.get("assistant_before") or ""))
    if offer.offers_choice:
        actual_kind = recapitulation_kind(str(case.get("actual_next_user_message") or ""), offer)
        if actual_kind and not recapitulation_kind(suggestion, offer):
            return "missed_offered_option"
    tail = normalize_text(str(case.get("transcript_tail") or ""))
    for match in _SPECIFIC.findall(suggestion):
        if normalize_text(match) not in tail:
            return "hallucinated_specific"
    assistant = str(case.get("assistant_before") or "")
    if suggestion.strip().endswith("?") and assistant.strip().endswith("?"):
        if jaccard(token_set(suggestion), token_set(assistant)) >= 0.35:
            return "echoed_question"
    if token_jaccard >= 0.3:
        return "near_miss_wording"
    return "unrelated"


async def _direction_pages(
    retriever: PageRetriever,
    suggestion: str,
    truth_pages: list[str],
    memo: dict[str, tuple[str, ...]],
) -> float | None:
    if not truth_pages or not suggestion:
        return None
    if suggestion not in memo:
        memo[suggestion] = await retriever.pages_for(suggestion)
    truth = frozenset(truth_pages)
    predicted = page_set(memo[suggestion])
    if not predicted:
        return 0.0
    return set_jaccard(predicted, truth)


def _aggregate(rows: list[CaseResult]) -> dict:
    if not rows:
        return {"cases": 0}
    exact = sum(1 for row in rows if row.exact)
    jaccard_hits = sum(1 for row in rows if row.jaccard_hit)
    abstained = sum(1 for row in rows if row.abstained)
    errors = sum(1 for row in rows if row.error)
    directions = [row.direction_jaccard for row in rows if row.direction_jaccard is not None]
    taxonomy: dict[str, int] = {}
    for row in rows:
        if row.failure:
            taxonomy[row.failure] = taxonomy.get(row.failure, 0) + 1
    token_values = [row.token_jaccard for row in rows]
    return {
        "cases": len(rows),
        "models": sorted({row.model for row in rows}),
        "errors": errors,
        "exact": exact,
        "exact_rate": round(exact / len(rows), 4),
        "jaccard_hits": jaccard_hits,
        "jaccard_hit_rate": round(jaccard_hits / len(rows), 4),
        "mean_token_jaccard": round(sum(token_values) / len(rows), 4),
        "abstained": abstained,
        "abstention_rate": round(abstained / len(rows), 4),
        "direction_cases": len(directions),
        "direction_mean": round(sum(directions) / len(directions), 4) if directions else 0.0,
        "direction_median": round(sorted(directions)[len(directions) // 2], 4) if directions else 0.0,
        "failure_taxonomy": dict(sorted(taxonomy.items())),
    }


def _write_scores_jsonl(path: Path, rows: list[CaseResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.__dict__, ensure_ascii=False, sort_keys=True) + "\n")


def _md_cell(value: str, limit: int = 110) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace("|", "\\|")


def _select_spot_check(
    rows: list[CaseResult],
    limit: int,
    *,
    conv_by_turn: dict[str, str],
    per_conv: int = 3,
) -> list[CaseResult]:
    """Pick a spread of outcomes, capped per conversation.

    Long debug conversations otherwise dominate the sheet and hide whether
    failures are systemic or one loop's local pathology.
    """
    buckets: dict[str, list[CaseResult]] = {"exact": [], "jaccard": [], "abstained": [], "miss": []}
    for row in rows:
        if row.error:
            continue
        if row.exact:
            buckets["exact"].append(row)
        elif row.jaccard_hit:
            buckets["jaccard"].append(row)
        elif row.abstained:
            buckets["abstained"].append(row)
        else:
            buckets["miss"].append(row)
    for name in buckets:
        buckets[name].sort(key=lambda row: row.turn_id)
    chosen: list[CaseResult] = []
    counts: dict[str, int] = {}
    order = ("exact", "jaccard", "abstained", "miss")
    while len(chosen) < limit:
        progressed = False
        for name in order:
            queue = buckets[name]
            index = 0
            while index < len(queue):
                row = queue[index]
                conv = conv_by_turn.get(row.turn_id, "")
                if counts.get(conv, 0) >= per_conv and any(
                    counts.get(conv_by_turn.get(other.turn_id, ""), 0) < per_conv
                    for bucket in buckets.values()
                    for other in bucket
                ):
                    index += 1
                    continue
                queue.pop(index)
                counts[conv] = counts.get(conv, 0) + 1
                chosen.append(row)
                progressed = True
                break
            if len(chosen) >= limit:
                break
        if not progressed:
            break
    return chosen[:limit]


def write_spot_check(
    path: Path,
    cases: list[dict],
    rows_by_arm: dict[str, list[CaseResult]],
    *,
    active_arm: str,
    aux_arm: str,
    limit: int = 40,
) -> None:
    cases_by_id = {case["turn_id"]: case for case in cases}
    active_rows = rows_by_arm.get(active_arm, [])
    aux_by_id = {row.turn_id: row for row in rows_by_arm.get(aux_arm, [])}
    conv_by_turn = {case["turn_id"]: case.get("conv_id", "") for case in cases}
    selected = _select_spot_check(active_rows, limit, conv_by_turn=conv_by_turn)
    lines = [
        "# Suggested-prompt spot check",
        "",
        "Operator sheet: read each row and judge whether the suggestion would have been",
        "useful, wrong-but-plausible, or obviously off. Page J is vault-direction overlap.",
        "",
        f"Arms: `{aux_arm}` (aux) vs `{active_arm}` (active).",
        "",
        "| turn | actual next | aux | active | token J | page J | failure | judged |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in selected:
        case = cases_by_id.get(row.turn_id, {})
        aux = aux_by_id.get(row.turn_id)
        lines.append(
            "| {turn} | {actual} | {aux} | {active} | {token:.2f} | {page} | {failure} | |".format(
                turn=_md_cell(row.turn_id, 40),
                actual=_md_cell(str(case.get("actual_next_user_message") or "")),
                aux=_md_cell(aux.suggestion if aux else ""),
                active=_md_cell(row.suggestion),
                token=row.token_jaccard,
                page="—" if row.direction_jaccard is None else f"{row.direction_jaccard:.2f}",
                failure=row.failure or "",
            )
        )
    path.write_text("\n".join(lines) + "\n")


async def run_score(
    config,
    cases: list[dict],
    *,
    retriever: PageRetriever,
    arms: list[str],
    aux_model: str = "",
    active_model: str = "",
    out_dir: Path,
    top_k: int = 5,
    concurrency: int = 4,
    max_calls: int = DEFAULT_MAX_CALLS,
    no_cache: bool = False,
    spot_check: int = 40,
    dry_run: bool = False,
    progress=None,
) -> dict:
    estimate = estimate_spend(cases, config, arms, aux_model, active_model)
    if estimate["calls"] > max_calls:
        raise RuntimeError(
            f"planned {estimate['calls']} model calls exceeds --max-calls {max_calls}; "
            "lower the case count, pick one arm, or raise the cap deliberately"
        )
    if dry_run:
        return {
            "dry_run": True,
            "cases": len(cases),
            "arms": arms,
            "estimate": estimate,
            "calibration": {"retriever": retriever.name},
        }

    connected = connected_page_keys(config)
    page_cases = [
        PageCase(
            turn_id=case["turn_id"],
            actual_next=str(case.get("actual_next_user_message") or ""),
            previous_user=str(case.get("previous_user_message") or ""),
            assistant_before=str(case.get("assistant_before") or ""),
            provenance_pages=tuple(case.get("provenance_pages") or ()),
        )
        for case in cases
    ]
    calibration, calibration_details = await calibrate_pages(
        page_cases,
        retriever,
        top_k=top_k,
        connected=connected,
    )

    cache_path = out_dir / "model-cache.jsonl"
    cache = {} if no_cache else load_cache(cache_path)
    sem = asyncio.Semaphore(max(1, concurrency))
    jobs: list[tuple[dict, str, str]] = []
    for case in cases:
        for arm in arms:
            jobs.append((case, arm, resolve_model(config, arm, case, aux_model, active_model)))

    seen_jobs: set[tuple[str, str]] = set()
    new_jobs: list[tuple[dict, str, str]] = []
    for case, arm, model in jobs:
        key = (model, case["turn_id"])
        if key in cache or key in seen_jobs:
            continue
        seen_jobs.add(key)
        new_jobs.append((case, arm, model))
    if progress:
        progress(f"Model calls: {len(jobs)} total, {len(new_jobs)} new, {len(jobs) - len(new_jobs)} cached")
    results = await asyncio.gather(*(_call_one(config, case, model, sem) for case, _arm, model in new_jobs))
    for record in results:
        if record["error"]:
            continue
        cache[(record["model"], record["turn_id"])] = record
        append_cache(cache_path, record)

    direction_memo: dict[str, tuple[str, ...]] = {}
    rows_by_arm: dict[str, list[CaseResult]] = {arm: [] for arm in arms}
    for case, arm, model in jobs:
        record = cache.get((model, case["turn_id"]))
        if record is None:
            record = {
                "raw": "",
                "usage": {},
                "error": "missing model response after run",
                "elapsed_ms": 0,
            }
        suggestion = parse_suggestion(record.get("raw", ""))
        actual = str(case.get("actual_next_user_message") or "")
        actual_tokens = token_set(actual)
        suggestion_tokens = token_set(suggestion)
        token_jaccard = jaccard(actual_tokens, suggestion_tokens) if suggestion else 0.0
        exact = bool(suggestion) and normalize_text(suggestion) == normalize_text(actual)
        truth_pages = calibration_details.get(case["turn_id"], {}).get("truth_pages", [])
        direction = await _direction_pages(retriever, suggestion, truth_pages, direction_memo)
        failure = None
        if record.get("error"):
            failure = "error"
        else:
            failure = classify_failure(case, suggestion, token_jaccard, exact)
        rows_by_arm[arm].append(
            CaseResult(
                turn_id=case["turn_id"],
                arm=arm,
                model=model,
                suggestion=suggestion,
                raw=str(record.get("raw") or ""),
                error=str(record.get("error") or ""),
                elapsed_ms=int(record.get("elapsed_ms") or 0),
                usage=record.get("usage") or {},
                exact=exact,
                token_jaccard=round(token_jaccard, 4),
                jaccard_hit=token_jaccard >= JACCARD_THRESHOLD,
                direction_jaccard=direction,
                abstained=not suggestion,
                failure=failure,
            )
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    all_rows = [row for arm in arms for row in rows_by_arm[arm]]
    _write_scores_jsonl(out_dir / "scores.jsonl", all_rows)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases": len(cases),
        "arms": arms,
        "aux_override": aux_model or None,
        "active_override": active_model or None,
        "estimate": estimate,
        "calibration": calibration,
        "results": {arm: _aggregate(rows) for arm, rows in rows_by_arm.items()},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if spot_check and "active" in rows_by_arm:
        aux_arm = "aux" if "aux" in rows_by_arm else "active"
        write_spot_check(
            out_dir / "spot-check.md",
            cases,
            rows_by_arm,
            active_arm="active",
            aux_arm=aux_arm,
            limit=spot_check,
        )
    return summary


def format_score_summary(summary: dict) -> str:
    if summary.get("dry_run"):
        estimate = summary["estimate"]
        return (
            f"Dry run: {summary['cases']} cases x {summary['arms']} arms\n"
            f"  calls: {estimate['calls']}\n"
            f"  input tokens (est): {estimate['input_tokens_estimate']}\n"
            f"  output tokens (est): {estimate['output_tokens_estimate']}\n"
            f"  cost (est): ${estimate['cost_usd_estimate']}"
            + (f"  unpriced: {estimate['unpriced_models']}" if estimate["unpriced_models"] else "")
        )
    lines = [f"Suggested-prompt baseline — {summary['cases']} cases"]
    for arm, stats in summary["results"].items():
        lines.extend(
            [
                f"\n{arm} ({', '.join(stats['models'])})",
                f"  exact:      {stats['exact']}/{stats['cases']} ({stats['exact_rate']:.1%})",
                f"  Jaccard≥{JACCARD_THRESHOLD}: {stats['jaccard_hits']}/{stats['cases']} "
                f"({stats['jaccard_hit_rate']:.1%})",
                f"  abstained:  {stats['abstained']}/{stats['cases']} ({stats['abstention_rate']:.1%})",
                f"  direction:  {stats['direction_cases']} cases, mean {stats['direction_mean']:.3f}, "
                f"median {stats['direction_median']:.3f}",
                f"  failures:   {stats['failure_taxonomy']}",
            ]
        )
    calibration = summary["calibration"]
    lines.extend(
        [
            "",
            f"Direction calibration ({calibration['retriever']}, top_k={calibration['top_k']}):",
            f"  ground truth {calibration['truth_sources']}, random floor {calibration['analytic_random_floor']}",
        ]
    )
    for name, stats in calibration["baselines"].items():
        lines.append(f"  {name:24s} mean {stats['mean']:.3f}  median {stats['median']:.3f}")
    return "\n".join(lines)
