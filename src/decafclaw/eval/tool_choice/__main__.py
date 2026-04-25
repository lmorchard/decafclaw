"""CLI entry point for the tool-choice eval.

Usage:

    uv run python -m decafclaw.eval.tool_choice <file_or_dir> [options]

See ``docs/eval-loop.md`` for the case YAML format and authoring
guidance.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from ...config import load_config
from ...llm import init_providers
from .case import load_cases
from .loadout import build_full_tool_loadout
from .report import (
    compute_confusion_matrix,
    compute_pair_overlap,
    format_case_lines,
    format_confusion_matrix,
    format_pair_overlap,
    format_summary,
)
from .runner import run_cases


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Tool-choice disambiguation eval — measures which tool the "
                    "model picks given engineered ambiguity scenarios.",
    )
    p.add_argument("path", help="YAML file or directory of YAMLs")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument(
        "--model", help="Single model name (default: config.default_model)",
    )
    grp.add_argument(
        "--models",
        help="Comma-separated list of model names for a sweep run",
    )
    p.add_argument(
        "--include-mcp", action="store_true",
        help="Include MCP server tools in the loadout (off by default — "
             "deployment-specific noise)",
    )
    p.add_argument(
        "--matrix", action="store_true",
        help="Print full confusion matrix in addition to pair overlap",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Print full tool_calls list per case (when more than one was emitted)",
    )
    p.add_argument(
        "--concurrency", type=int, default=4,
        help="Max concurrent cases per model (default: 4)",
    )
    return p


def _resolve_models(args, config) -> list[str]:
    if args.models:
        return [m.strip() for m in args.models.split(",") if m.strip()]
    if args.model:
        return [args.model]
    if not config.default_model:
        raise SystemExit(
            "No model specified and config.default_model is empty — "
            "pass --model or --models, or configure a default."
        )
    return [config.default_model]


async def _run_for_model(
    model: str, cases, *, config, tool_loadout, args, sweep: bool,
) -> bool:
    """Run a single model's pass and print its block. Returns True on
    pass, False on any failure."""
    if sweep:
        print(f"\n=== {model} ===\n")
    else:
        print()

    results = await run_cases(
        cases,
        model=model,
        config=config,
        tool_loadout=tool_loadout,
        concurrency=args.concurrency,
    )

    for line in format_case_lines(results):
        print(line)
    if args.verbose:
        for r in results:
            if len(r.all_picks) > 1:
                print(f"  {r.case.name} all_picks: {r.all_picks}")

    print()
    print(format_summary(results))

    print()
    for line in format_pair_overlap(compute_pair_overlap(results)):
        print(line)

    if args.matrix:
        print()
        for line in format_confusion_matrix(compute_confusion_matrix(results)):
            print(line)

    return all(r.passed for r in results)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    config = load_config()
    init_providers(config)

    cases = load_cases(Path(args.path))
    if not cases:
        print(f"No cases found at {args.path}")
        return 1

    models = _resolve_models(args, config)
    sweep = len(models) > 1
    tool_loadout = build_full_tool_loadout(config, include_mcp=args.include_mcp)

    print(f"tool-choice eval — {len(cases)} case(s), "
          f"{len(tool_loadout)} tool(s) loaded, "
          f"models: {', '.join(models)}")

    all_passed = True
    for m in models:
        passed = asyncio.run(_run_for_model(
            m, cases, config=config, tool_loadout=tool_loadout,
            args=args, sweep=sweep,
        ))
        all_passed = all_passed and passed

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
