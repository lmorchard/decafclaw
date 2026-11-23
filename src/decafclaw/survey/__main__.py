"""CLI for the suggested-prompt archive survey (#1738).

python -m decafclaw.survey stage0
python -m decafclaw.survey devset --limit 200
python -m decafclaw.survey score --devset evals/suggested_prompt/devset.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from ..config import load_config
from ..model_catalog import effective_default_model
from .devset import DEFAULT_DEVSET_PATH, DEFAULT_SEED, build_devset, load_devset, write_devset
from .pages import create_retriever
from .score import DEFAULT_MAX_CALLS, format_score_summary, run_score
from .stage0 import format_stage0, run_stage0, write_json

_RESULTS_DIR = Path("evals/results/suggested-prompt")


def _load_runtime_config():
    config = load_config()
    from ..llm import init_providers

    init_providers(config)
    return config


def _cmd_stage0(args) -> int:
    config = _load_runtime_config()
    default_model = effective_default_model(config)
    retriever = asyncio.run(
        create_retriever(config, backend=args.backend, top_k=args.top_k, local_model=args.local_model)
    )

    async def _run():
        return await run_stage0(
            config,
            retriever,
            default_model=default_model,
            tail_chars=args.tail_chars,
            limit=args.limit,
            top_k=args.top_k,
            page_sample=args.page_sample,
        )

    result = asyncio.run(_run())
    out = Path(args.out)
    write_json(out, result.summary)
    if args.details:
        details_path = Path(args.details)
        details_path.parent.mkdir(parents=True, exist_ok=True)
        with details_path.open("w", encoding="utf-8") as handle:
            for detail in result.details:
                handle.write(json.dumps(detail, sort_keys=True) + "\n")
    print(format_stage0(result.summary))
    print(f"\nWrote {out}")
    return 0


def _cmd_devset(args) -> int:
    config = load_config()
    default_model = effective_default_model(config)

    async def _run():
        from .corpus import scan_corpus

        return scan_corpus(
            config,
            default_model=default_model,
            tail_chars=args.tail_chars,
        )

    scan = asyncio.run(_run())
    cases = build_devset(scan.boundaries, limit=args.limit, seed=args.seed)
    out = Path(args.out)
    write_devset(out, cases)
    strata: dict[str, int] = {}
    for case in cases:
        key = f"{case['stratum']['recency']}/{case['stratum']['length']}"
        strata[key] = strata.get(key, 0) + 1
    print(
        f"Eligible boundaries: {len(scan.boundaries)} from "
        f"{scan.conversations_with_boundaries} conversations across {scan.conversations} archives"
    )
    print(f"Wrote {len(cases)} cases to {out}")
    print(f"Strata: {dict(sorted(strata.items()))}")
    if len(cases) < args.limit:
        print(f"NOTE: corpus supplied only {len(cases)} of the requested {args.limit} cases")
    return 0


def _cmd_score(args) -> int:
    config = _load_runtime_config()
    cases = load_devset(Path(args.devset))
    if args.limit:
        cases = cases[: args.limit]
    arms = [arm.strip() for arm in args.arms.split(",") if arm.strip()]
    for arm in arms:
        if arm not in ("aux", "active"):
            print(f"Unknown arm: {arm}", file=sys.stderr)
            return 2
    retriever = asyncio.run(
        create_retriever(config, backend=args.backend, top_k=args.top_k, local_model=args.local_model)
    )
    out_dir = Path(args.out) if args.out else _RESULTS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")

    async def _run():
        return await run_score(
            config,
            cases,
            retriever=retriever,
            arms=arms,
            aux_model=args.aux_model,
            active_model=args.active_model,
            out_dir=out_dir,
            top_k=args.top_k,
            concurrency=args.concurrency,
            max_calls=args.max_calls,
            no_cache=args.no_cache,
            spot_check=args.spot_check,
            dry_run=args.dry_run,
            progress=print,
        )

    try:
        summary = asyncio.run(_run())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(format_score_summary(summary))
    if not summary.get("dry_run"):
        print(f"\nWrote {out_dir}/summary.json, scores.jsonl, spot-check.md, model-cache.jsonl")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decafclaw survey",
        description="Archive-replay survey harness for suggested next prompts (#1738).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    stage0 = sub.add_parser("stage0", help="Free archive signals: offered-option rate + page calibration")
    stage0.add_argument("--out", default=str(_RESULTS_DIR / "stage0.json"))
    stage0.add_argument("--details", help="Optional JSONL path for per-boundary calibration rows")
    stage0.add_argument("--backend", choices=("auto", "embeddings", "local"), default="auto")
    stage0.add_argument("--local-model", default="BAAI/bge-small-en-v1.5")
    stage0.add_argument("--top-k", type=int, default=5)
    stage0.add_argument("--tail-chars", type=int, default=6000)
    stage0.add_argument("--limit", type=int, help="Scan at most this many archives")
    stage0.add_argument(
        "--page-sample",
        type=int,
        default=300,
        help="Calibrate page direction on this many boundaries (0 = all; default 300)",
    )
    stage0.set_defaults(func=_cmd_stage0)

    devset = sub.add_parser("devset", help="Sample a deterministic dev set")
    devset.add_argument("--out", default=str(DEFAULT_DEVSET_PATH))
    devset.add_argument("--limit", type=int, default=200)
    devset.add_argument("--seed", type=int, default=DEFAULT_SEED)
    devset.add_argument("--tail-chars", type=int, default=6000)
    devset.set_defaults(func=_cmd_devset)

    score = sub.add_parser("score", help="Score the baseline predictor on a dev set")
    score.add_argument("--devset", default=str(DEFAULT_DEVSET_PATH))
    score.add_argument("--out", help="Output directory (default evals/results/suggested-prompt/<stamp>)")
    score.add_argument("--arms", default="aux,active")
    score.add_argument("--aux-model", default="", help="Override the aux arm model")
    score.add_argument("--active-model", default="", help="Override the active arm model")
    score.add_argument("--backend", choices=("auto", "embeddings", "local"), default="auto")
    score.add_argument("--local-model", default="BAAI/bge-small-en-v1.5")
    score.add_argument("--top-k", type=int, default=5)
    score.add_argument("--concurrency", type=int, default=4)
    score.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    score.add_argument("--spot-check", type=int, default=40)
    score.add_argument("--no-cache", action="store_true")
    score.add_argument("--dry-run", action="store_true")
    score.add_argument("--limit", type=int, help="Score the first N cases only")
    score.set_defaults(func=_cmd_score)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
