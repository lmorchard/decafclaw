# Eval Loop — Plan

## Architecture

The eval system is a standalone CLI that imports and exercises the agent.
No mocking — real LLM calls. Produces result bundles for comparison.

```
evals/                          # YAML test cases (committed)
  memory.yaml
evals/results/                  # result bundles (gitignored)
  2026-03-14-1130-gemini-2.5-flash/
    results.json
    workspace/
    reflections/
src/decafclaw/eval/
  __init__.py                   # empty
  __main__.py                   # CLI entry point
  runner.py                     # test execution logic
  reflect.py                    # failure reflection via judge model
```

## Steps

### Step 1: Token accumulation on context

Small agent code change: accumulate total tokens on the context so
the eval runner (and future tools) can read them.

#### Prompt

```
Update src/decafclaw/agent.py:

In run_agent_turn, after `ctx.history = history`, initialize:
    ctx.total_prompt_tokens = 0
    ctx.total_completion_tokens = 0

After each `usage = response.get("usage")` block, accumulate:
    if usage:
        prompt_tokens = usage.get("prompt_tokens", 0)
        ctx.total_prompt_tokens += usage.get("prompt_tokens", 0)
        ctx.total_completion_tokens += usage.get("completion_tokens", 0)
```

---

### Step 2: Add pyyaml dependency

#### Prompt

```
uv add --dev pyyaml
```

---

### Step 3: Eval runner module

The core execution logic.

#### Prompt

```
Create src/decafclaw/eval/__init__.py (empty).

Create src/decafclaw/eval/runner.py with:

import asyncio
import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

from ..config import Config, load_config
from ..context import Context
from ..events import EventBus
from ..memory import save_entry
from ..agent import run_agent_turn

log = logging.getLogger(__name__)


def _setup_workspace(config, test_case: dict, workspace_dir: Path):
    """Create fixture data in the temp workspace."""
    setup = test_case.get("setup", {})
    memories = setup.get("memories", [])
    for mem in memories:
        save_entry(
            config,
            channel_name="eval",
            channel_id="eval",
            thread_id="",
            tags=mem.get("tags", []),
            content=mem["content"],
        )


def _count_tool_calls(history: list) -> int:
    """Count tool result messages in history."""
    return sum(1 for msg in history if msg.get("role") == "tool")


def _check_assertions(test_case: dict, response: str, tool_calls: int) -> tuple[bool, str]:
    """Check test assertions. Returns (passed, failure_reason)."""
    expect = test_case.get("expect", {})

    contains = expect.get("response_contains")
    if contains and contains.lower() not in response.lower():
        return False, f"Expected '{contains}' in response"

    max_tools = expect.get("max_tool_calls")
    if max_tools is not None and tool_calls > max_tools:
        return False, f"Too many tool calls: {tool_calls} > {max_tools}"

    return True, ""


async def run_test(config: Config, test_case: dict) -> dict:
    """Run a single eval test case. Returns a result dict."""
    bus = EventBus()
    ctx = Context(config=config, event_bus=bus)
    ctx.conv_id = "eval"
    ctx.channel_id = "eval"
    ctx.channel_name = "eval"
    ctx.thread_id = ""
    ctx.user_id = config.agent_user_id

    # Setup fixtures
    _setup_workspace(config, test_case, Path(config.data_home))

    # Run the agent
    history = []
    start = time.monotonic()
    response = await run_agent_turn(ctx, test_case["input"], history)
    duration = time.monotonic() - start

    # Gather metrics
    tool_calls = _count_tool_calls(history)
    prompt_tokens = getattr(ctx, "total_prompt_tokens", 0)
    completion_tokens = getattr(ctx, "total_completion_tokens", 0)

    # Check assertions
    passed, failure_reason = _check_assertions(test_case, response, tool_calls)

    return {
        "name": test_case["name"],
        "status": "pass" if passed else "fail",
        "duration_sec": round(duration, 1),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "tool_calls": tool_calls,
        "response": response,
        "failure_reason": failure_reason if not passed else None,
    }


async def run_eval(yaml_data: list[dict], config: Config,
                   model: str | None = None,
                   judge_model: str | None = None,
                   verbose: bool = False) -> dict:
    """Run all test cases and return full results."""
    if model:
        config.llm_model = model

    effective_model = config.llm_model
    effective_judge = judge_model or effective_model
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")

    results = {
        "timestamp": datetime.now().isoformat(),
        "model": effective_model,
        "judge_model": effective_judge,
        "tests": [],
        "summary": {"total": 0, "passed": 0, "failed": 0,
                     "duration_sec": 0, "total_tokens": 0},
    }

    total = len(yaml_data)
    for i, test_case in enumerate(yaml_data):
        name = test_case["name"]

        # Each test gets its own temp workspace
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            test_config = Config(
                llm_url=config.llm_url,
                llm_model=config.llm_model,
                llm_api_key=config.llm_api_key,
                data_home=tmp,
                agent_id="eval",
                agent_user_id=config.agent_user_id,
                system_prompt=config.system_prompt,
                max_tool_iterations=config.max_tool_iterations,
                # Compaction settings (use defaults, unlikely to trigger in evals)
                compaction_max_tokens=config.compaction_max_tokens,
            )

            result = await run_test(test_config, test_case)

        # Print terse output
        status = "PASS" if result["status"] == "pass" else "FAIL"
        tokens = result["prompt_tokens"] + result["completion_tokens"]
        tools = result["tool_calls"]
        dur = result["duration_sec"]
        pad = "." * max(1, 50 - len(name))
        print(f"[{i+1}/{total}] {name} {pad} {status}  ({dur}s, {tokens} tokens, {tools} tools)")

        if verbose and result["response"]:
            print(f"         Response: {result['response'][:200]}")

        if result["status"] == "fail":
            print(f"         {result['failure_reason']}")

        results["tests"].append(result)
        results["summary"]["total"] += 1
        results["summary"]["passed"] += 1 if result["status"] == "pass" else 0
        results["summary"]["failed"] += 1 if result["status"] == "fail" else 0
        results["summary"]["duration_sec"] += dur
        results["summary"]["total_tokens"] += tokens

    results["summary"]["duration_sec"] = round(results["summary"]["duration_sec"], 1)
    return results, timestamp, effective_model
```

---

### Step 4: Failure reflection module

#### Prompt

```
Create src/decafclaw/eval/reflect.py:

import logging
from pathlib import Path
from ..llm import call_llm

log = logging.getLogger(__name__)

REFLECTION_PROMPT = """You are analyzing why an AI agent failed a test case.

Test case:
- Name: {name}
- Input: {input}
- Expected: response should contain "{expected}"
- Setup memories: {setup}

Agent's response: {response}

Tool calls made: {tool_calls}

Why did the agent fail this test? What specific changes to the system
prompt, tool descriptions, or tool behavior would fix it? Be concrete
and actionable."""


async def reflect_on_failure(config, test_case: dict, result: dict,
                              judge_model: str, output_dir: Path) -> str | None:
    """Ask the judge model to analyze a test failure.
    Returns the path to the reflection file, or None on error."""
    expected = test_case.get("expect", {}).get("response_contains", "?")
    setup_memories = test_case.get("setup", {}).get("memories", [])

    prompt = REFLECTION_PROMPT.format(
        name=test_case["name"],
        input=test_case["input"],
        expected=expected,
        setup=[m.get("content", "") for m in setup_memories],
        response=result["response"],
        tool_calls=result["tool_calls"],
    )

    messages = [
        {"role": "system", "content": "You are a helpful AI testing analyst."},
        {"role": "user", "content": prompt},
    ]

    try:
        response = await call_llm(
            config, messages,
            llm_model=judge_model,
        )
        reflection = response.get("content", "")
        if not reflection:
            return None

        # Write reflection file
        output_dir.mkdir(parents=True, exist_ok=True)
        slug = test_case["name"].lower().replace(" ", "-")[:50]
        filepath = output_dir / f"{slug}.md"
        filepath.write_text(f"# Reflection: {test_case['name']}\n\n{reflection}\n")

        return str(filepath)
    except Exception as e:
        log.error(f"Reflection failed: {e}")
        return None
```

---

### Step 5: CLI entry point

#### Prompt

```
Create src/decafclaw/eval/__main__.py:

import argparse
import asyncio
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

from ..config import load_config
from .runner import run_eval
from .reflect import reflect_on_failure


def main():
    parser = argparse.ArgumentParser(description="DecafClaw eval runner")
    parser.add_argument("path", help="YAML file or directory of YAML files")
    parser.add_argument("--model", help="Override LLM model")
    parser.add_argument("--judge-model", help="Model for failure reflection (default: same as --model)")
    parser.add_argument("--verbose", action="store_true", help="Show full responses")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s: %(message)s")

    # Load test cases
    path = Path(args.path)
    if path.is_dir():
        yaml_files = sorted(path.glob("*.yaml"))
    else:
        yaml_files = [path]

    all_cases = []
    for yf in yaml_files:
        with open(yf) as f:
            cases = yaml.safe_load(f)
        if isinstance(cases, list):
            all_cases.extend(cases)

    if not all_cases:
        print("No test cases found.")
        sys.exit(1)

    config = load_config()
    model_name = args.model or config.llm_model
    judge_model = args.judge_model or model_name

    print(f"\ndecafclaw eval — {model_name} — {args.path}\n")

    # Run evals
    results, timestamp, effective_model = asyncio.run(
        run_eval(all_cases, config, model=args.model,
                 judge_model=judge_model, verbose=args.verbose)
    )

    # Create result bundle
    safe_model = effective_model.replace("/", "-")
    bundle_dir = Path("evals/results") / f"{timestamp}-{safe_model}"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # Run reflections for failures
    failed_tests = [(i, t) for i, t in enumerate(results["tests"]) if t["status"] == "fail"]
    if failed_tests:
        reflect_dir = bundle_dir / "reflections"
        for i, test_result in failed_tests:
            test_case = all_cases[i]
            ref_path = asyncio.run(
                reflect_on_failure(config, test_case, test_result,
                                    judge_model, reflect_dir)
            )
            if ref_path:
                test_result["reflection_file"] = str(Path(ref_path).relative_to(bundle_dir))

    # Save results.json
    results["source"] = str(args.path)
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
```

---

### Step 6: Initial eval YAML files

#### Prompt

```
Create evals/memory.yaml with 3-5 test cases exercising memory tools:

1. Finds a saved preference by direct term
2. Finds a saved preference by related term (tests search expansion)
3. Recalls recent memories when asked
4. Responds correctly when no memories exist for a topic
5. Saves a memory when told to remember something

Add evals/results/ to .gitignore.
```

---

### Step 7: Lint, test, smoke run, commit

#### Prompt

```
1. Add eval module to make lint (already glob-based, should auto-detect)
2. make lint && make test
3. Do a real eval run: uv run python -m decafclaw.eval evals/memory.yaml
4. Check the result bundle looks correct
5. Commit everything
```

---

## Implementation Order Summary

| Step | Files Changed | What It Does |
|------|--------------|--------------|
| 1 | `agent.py` | Token accumulation on context |
| 2 | `pyproject.toml`, `uv.lock` | Add pyyaml dependency |
| 3 | `eval/__init__.py`, `eval/runner.py` (new) | Test execution engine |
| 4 | `eval/reflect.py` (new) | Failure reflection via judge model |
| 5 | `eval/__main__.py` (new) | CLI entry point |
| 6 | `evals/memory.yaml` (new), `.gitignore` | Test cases and gitignore |
| 7 | `Makefile`, all | Lint, test, smoke run |

## Risk Notes

- **Real LLM calls = real cost.** Each eval run costs tokens. Keep test
  cases short (single turn). The memory.yaml file should have ~5 cases,
  each costing maybe 500-1000 tokens = very cheap per run.
- **Non-deterministic.** LLM responses vary. A test might pass 9/10 times.
  This is expected — the eval is for catching systematic regressions, not
  guaranteeing 100% pass rate.
- **Reflection adds cost.** Each failed test gets an extra LLM call for
  the judge. With --judge-model you can use a cheaper model for this.
