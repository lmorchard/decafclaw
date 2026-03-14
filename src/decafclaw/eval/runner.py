"""Eval runner — execute test cases against the agent."""

import logging
import time
from datetime import datetime
from pathlib import Path

from ..config import Config, load_config
from ..context import Context
from ..events import EventBus
from ..memory import save_entry
from ..agent import run_agent_turn

log = logging.getLogger(__name__)


async def _setup_workspace(config, test_case: dict):
    """Create fixture data in the temp workspace."""
    import shutil
    setup = test_case.get("setup", {})

    # Copy pre-built embeddings fixture if specified
    fixture_db = setup.get("embeddings_fixture")
    if fixture_db:
        fixture_path = Path(fixture_db)
        if fixture_path.exists():
            dest = Path(config.data_home) / "workspace" / config.agent_id / "embeddings.db"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fixture_path, dest)
            log.info(f"Copied embeddings fixture from {fixture_path}")

    # Save memory entries
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

    # Index memories for semantic search if strategy is semantic
    if config.memory_search_strategy == "semantic" and memories:
        from ..embeddings import index_entry
        for mem in memories:
            tag_str = ", ".join(mem.get("tags", []))
            # Format like actual markdown entries for consistent embeddings
            entry_text = (
                f"## 2026-01-01 00:00\n\n"
                f"- **channel:** eval (eval)\n"
                f"- **tags:** {tag_str}\n\n"
                f"{mem['content']}"
            )
            await index_entry(config, "eval-fixture", entry_text)


def _count_tool_calls(history: list) -> int:
    """Count tool result messages in history."""
    return sum(1 for msg in history if msg.get("role") == "tool")


def _check_assertions(test_case: dict, response: str, tool_calls: int) -> tuple[bool, str]:
    """Check test assertions. Returns (passed, failure_reason)."""
    import re

    expect = test_case.get("expect", {})
    response_lower = response.lower()

    # response_contains: string, list (any match), or regex (prefix with "re:")
    contains = expect.get("response_contains")
    if contains:
        if isinstance(contains, str):
            contains = [contains]
        matched = False
        for c in contains:
            if c.startswith("re:"):
                if re.search(c[3:], response, re.IGNORECASE):
                    matched = True
                    break
            elif c.lower() in response_lower:
                matched = True
                break
        if not matched:
            return False, f"Expected one of {contains} in response"

    # response_not_contains: string or list (all must be absent)
    not_contains = expect.get("response_not_contains")
    if not_contains:
        if isinstance(not_contains, str):
            not_contains = [not_contains]
        for nc in not_contains:
            if nc.lower() in response_lower:
                return False, f"Response should not contain '{nc}'"

    max_tools = expect.get("max_tool_calls")
    if max_tools is not None and tool_calls > max_tools:
        return False, f"Too many tool calls: {tool_calls} > {max_tools}"

    return True, ""


async def run_test(config: Config, test_case: dict) -> dict:
    """Run a single eval test case. Returns a result dict.

    Supports two formats:
    - Single turn: { input: "...", expect: {...} }
    - Multi turn: { turns: [ {input: "...", expect: {...}}, ... ] }

    Multi-turn tests share history across turns (same conversation).
    All turns must pass for the test to pass.
    """
    bus = EventBus()
    ctx = Context(config=config, event_bus=bus)
    ctx.conv_id = "eval"
    ctx.channel_id = "eval"
    ctx.channel_name = "eval"
    ctx.thread_id = ""
    ctx.user_id = config.agent_user_id

    # Set allowed tools if specified (disallowed tools return an error)
    allowed_tools = test_case.get("allowed_tools")
    if allowed_tools:
        ctx.allowed_tools = set(allowed_tools)

    # Setup fixtures
    await _setup_workspace(config, test_case)

    # Determine turns
    if "turns" in test_case:
        turns = test_case["turns"]
    else:
        turns = [{"input": test_case["input"], "expect": test_case.get("expect", {})}]

    history = []
    total_duration = 0
    all_responses = []
    overall_passed = True
    failure_reason = None

    for turn_idx, turn in enumerate(turns):
        # Reset per-turn token counters
        ctx.total_prompt_tokens = 0
        ctx.total_completion_tokens = 0

        start = time.monotonic()
        response = await run_agent_turn(ctx, turn["input"], history)
        duration = time.monotonic() - start
        total_duration += duration

        tool_calls = _count_tool_calls(history)
        all_responses.append({
            "turn": turn_idx + 1,
            "input": turn["input"],
            "response": response,
            "duration_sec": round(duration, 1),
            "tool_calls": tool_calls,
        })

        # Check assertions for this turn
        expect = turn.get("expect", {})
        if expect:
            passed, reason = _check_assertions(turn, response, tool_calls)
            if not passed:
                overall_passed = False
                failure_reason = f"Turn {turn_idx + 1}: {reason}"
                break

    # Gather cumulative metrics
    prompt_tokens = getattr(ctx, "total_prompt_tokens", 0)
    completion_tokens = getattr(ctx, "total_completion_tokens", 0)
    total_tool_calls = _count_tool_calls(history)

    # For single-turn, keep flat response; for multi-turn, show last response
    final_response = all_responses[-1]["response"] if all_responses else ""

    result = {
        "name": test_case["name"],
        "status": "pass" if overall_passed else "fail",
        "duration_sec": round(total_duration, 1),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "tool_calls": total_tool_calls,
        "response": final_response,
        "failure_reason": failure_reason,
    }

    # Include turn details for multi-turn tests
    if len(turns) > 1:
        result["turns"] = all_responses

    return result


async def run_eval(yaml_data: list[dict], config: Config,
                   model: str | None = None,
                   verbose: bool = False) -> tuple[dict, str, str]:
    """Run all test cases and return (results, timestamp, model_name)."""
    import tempfile

    if model:
        config.llm_model = model

    effective_model = config.llm_model
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")

    results = {
        "timestamp": datetime.now().isoformat(),
        "model": effective_model,
        "tests": [],
        "summary": {"total": 0, "passed": 0, "failed": 0,
                     "duration_sec": 0, "total_tokens": 0},
    }

    total = len(yaml_data)
    for i, test_case in enumerate(yaml_data):
        name = test_case["name"]

        # Each test gets its own temp workspace
        with tempfile.TemporaryDirectory() as tmp:
            from dataclasses import replace
            test_config = replace(config,
                data_home=tmp,
                agent_id="eval",
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
