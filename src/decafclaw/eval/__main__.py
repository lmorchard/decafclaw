"""CLI entry point for the eval runner.

Usage: uv run python -m decafclaw.eval <file_or_dir> [options]
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import yaml

from ..config import load_config
from .history import HISTORY_PATH, append_run, build_run_record, read_history, render_table
from .reflect import reflect_on_failure
from .runner import run_eval


def main():
    parser = argparse.ArgumentParser(
        description="DecafClaw eval runner — test prompts and tools with real LLM calls"
    )
    parser.add_argument("path", nargs="?", help="YAML file or directory of YAML files")
    parser.add_argument("--model", help="Override LLM model")
    parser.add_argument("--judge-model", help="Model for failure reflection (default: same as --model)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show a truncated response snippet (first 200 chars) per test")
    parser.add_argument("--concurrency", type=int, default=4, help="Max concurrent tests (default: 4)")
    parser.add_argument("--history", action="store_true",
                        help="Print the eval-run history table and exit (no eval run)")
    parser.add_argument("--history-limit", type=int, default=20,
                        help="With --history, show this many most-recent runs (default: 20)")
    args = parser.parse_args()

    # --history reads evals/history.jsonl and prints the trend table; no eval run.
    if args.history:
        records = read_history()
        print(render_table(records, limit=args.history_limit))
        sys.exit(0)

    if not args.path:
        parser.error("the following arguments are required: path (or use --history)")

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    # Silence three loggers that fire on every LLM call in eval context but
    # are noise rather than signal here. All three remain WARNING-level in
    # production where they carry useful information.
    #   - tool_registry "Critical tool set exceeds budget" — fires per
    #     `_build_tool_list`; informational ("Critical tools are included
    #     anyway"). Tracked separately if we want to downgrade globally.
    #   - tools.confirmation "No ConversationManager" — evals never wire one
    #     up; the legacy event-bus path is the intended eval behavior.
    #   - tool_execution "widget registry is not initialized" — evals don't
    #     render widgets; stripping is the intended eval behavior.
    for name in (
        "decafclaw.tools.tool_registry",
        "decafclaw.tools.confirmation",
        "decafclaw.tool_execution",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)

    # Load test cases
    path = Path(args.path)
    if path.is_dir():
        yaml_files = sorted(path.glob("*.yaml"))
    else:
        yaml_files = [path]

    all_cases = []
    sources = []
    for yf in yaml_files:
        with open(yf) as f:
            cases = yaml.safe_load(f)
        if isinstance(cases, list):
            all_cases.extend(cases)
            sources.append(str(yf))

    if not all_cases:
        print("No test cases found.")
        sys.exit(1)

    config = load_config()

    # Initialize provider registry
    from ..llm import init_providers
    init_providers(config)

    # Resolve model: check model_configs first, then fall back to raw name
    model_name = args.model or config.default_model or config.llm.model
    judge_model = args.judge_model or model_name

    print(f"\ndecafclaw eval — {model_name} — {args.path}\n")

    # Run evals
    results, timestamp, effective_model = asyncio.run(
        run_eval(all_cases, config, model=args.model, verbose=args.verbose,
                 concurrency=args.concurrency)
    )

    # Create result bundle
    safe_model = effective_model.replace("/", "-")
    bundle_dir = Path("evals/results") / f"{timestamp}-{safe_model}"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # Run reflections for failures
    failed_tests = [(i, t) for i, t in enumerate(results["tests"]) if t["status"] == "fail"]
    if failed_tests:
        print(f"\nReflecting on {len(failed_tests)} failure(s)...")
        reflect_dir = bundle_dir / "reflections"
        for i, test_result in failed_tests:
            test_case = all_cases[i]
            ref_path = asyncio.run(
                reflect_on_failure(config, test_case, test_result,
                                    judge_model, reflect_dir)
            )
            if ref_path:
                test_result["reflection_file"] = f"reflections/{ref_path}"

    # Save results
    results["source"] = ", ".join(sources)
    results["judge_model"] = judge_model
    with open(bundle_dir / "results.json", "w") as f:
        json.dump(results, f, indent=2)

    # Append per-run summary to evals/history.jsonl (committed to git) so
    # pass-rate trends are visible across runs without re-running.
    s = results["summary"]
    record = build_run_record(
        timestamp=timestamp,
        model=effective_model,
        judge_model=judge_model,
        sources=sources,
        test_results=results["tests"],
        cases=all_cases,
        duration_sec=s["duration_sec"],
        total_tokens=s["total_tokens"],
    )
    try:
        append_run(record)
    except OSError as exc:
        # Don't fail the eval run if history write hits a filesystem issue.
        print(f"warning: could not append to {HISTORY_PATH}: {exc}", file=sys.stderr)

    # Print summary
    print(f"\n{s['total']} tests, {s['passed']} passed, {s['failed']} failed "
          f"({s['duration_sec']}s, {s['total_tokens']} tokens)")
    print(f"Results: {bundle_dir}/")
    print(f"History: {HISTORY_PATH} (use `make eval-history` to view the trend)\n")

    sys.exit(1 if s["failed"] > 0 else 0)


if __name__ == "__main__":
    main()
