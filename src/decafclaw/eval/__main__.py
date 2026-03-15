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
from .reflect import reflect_on_failure
from .runner import run_eval


def main():
    parser = argparse.ArgumentParser(
        description="DecafClaw eval runner — test prompts and tools with real LLM calls"
    )
    parser.add_argument("path", help="YAML file or directory of YAML files")
    parser.add_argument("--model", help="Override LLM model")
    parser.add_argument("--judge-model", help="Model for failure reflection (default: same as --model)")
    parser.add_argument("--verbose", action="store_true", help="Show full responses")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

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
    model_name = args.model or config.llm_model
    judge_model = args.judge_model or model_name

    print(f"\ndecafclaw eval — {model_name} — {args.path}\n")

    # Run evals
    results, timestamp, effective_model = asyncio.run(
        run_eval(all_cases, config, model=args.model, verbose=args.verbose)
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

    # Print summary
    s = results["summary"]
    print(f"\n{s['total']} tests, {s['passed']} passed, {s['failed']} failed "
          f"({s['duration_sec']}s, {s['total_tokens']} tokens)")
    print(f"Results: {bundle_dir}/\n")

    sys.exit(1 if s["failed"] > 0 else 0)


if __name__ == "__main__":
    main()
