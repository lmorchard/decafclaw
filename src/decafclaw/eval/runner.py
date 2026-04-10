"""Eval runner — execute test cases against the agent."""

import logging
import time
from datetime import datetime
from pathlib import Path

from ..agent import run_agent_turn
from ..config import Config
from ..context import Context
from ..events import EventBus

log = logging.getLogger(__name__)


async def _setup_skills(ctx, test_case: dict):
    """Pre-activate skills specified in setup.skills."""
    setup = test_case.get("setup", {})
    skill_names = setup.get("skills", [])
    if not skill_names:
        return

    from ..skills import discover_skills
    from ..tools.skill_tools import activate_skill_internal

    all_skills = discover_skills(ctx.config)
    skill_map = {s.name: s for s in all_skills}

    for name in skill_names:
        info = skill_map.get(name)
        if info:
            await activate_skill_internal(ctx, info)
            log.info(f"Pre-activated skill '{name}' for eval")
        else:
            log.warning(f"Skill '{name}' not found for eval pre-activation")


async def _setup_workspace(config, test_case: dict):
    """Create fixture data in the temp workspace."""
    import shutil
    setup = test_case.get("setup", {})

    # Copy pre-built embeddings fixture if specified
    fixture_db = setup.get("embeddings_fixture")
    if fixture_db:
        fixture_path = Path(fixture_db)
        if fixture_path.exists():
            dest = config.workspace_path / "embeddings.db"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fixture_path, dest)
            log.info(f"Copied embeddings fixture from {fixture_path}")

    # Save journal entries (replaces memories)
    memories = setup.get("memories", [])
    if memories:
        now = datetime.now()
        journal_dir = config.vault_agent_journal_dir / str(now.year)
        journal_dir.mkdir(parents=True, exist_ok=True)
        filepath = journal_dir / f"{now:%Y-%m-%d}.md"
        with open(filepath, "a", encoding="utf-8") as f:
            for mem in memories:
                tag_str = ", ".join(mem.get("tags", []))
                entry = (
                    f"\n## {now:%Y-%m-%d %H:%M}\n\n"
                    f"- **channel:** eval (eval)\n"
                    f"- **tags:** {tag_str}\n"
                    f"\n{mem['content']}\n"
                )
                f.write(entry)

    # Index journal entries for semantic search if strategy is semantic
    if config.embedding.search_strategy == "semantic" and memories:
        from ..embeddings import index_entry
        for mem in memories:
            tag_str = ", ".join(mem.get("tags", []))
            entry_text = (
                f"## 2026-01-01 00:00\n\n"
                f"- **channel:** eval (eval)\n"
                f"- **tags:** {tag_str}\n\n"
                f"{mem['content']}"
            )
            await index_entry(config, "eval-fixture", entry_text,
                              source_type="journal")


def _count_tool_calls(history: list) -> int:
    """Count tool result messages in history."""
    return sum(1 for msg in history if msg.get("role") == "tool")


def _count_tool_errors(history: list) -> int:
    """Count tool results that contain error indicators."""
    count = 0
    for msg in history:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", "")
        if isinstance(content, str) and "[error" in content.lower():
            count += 1
    return count


def _collect_tool_errors(history: list) -> list[str]:
    """Extract tool error messages from history."""
    errors = []
    for msg in history:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", "")
        if isinstance(content, str) and "[error" in content.lower():
            # Truncate long error messages
            errors.append(content[:200])
    return errors


def _check_assertions(test_case: dict, response: str, tool_calls: int,
                      tool_errors: int = 0) -> tuple[bool, str]:
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

    max_errors = expect.get("max_tool_errors")
    if max_errors is not None and tool_errors > max_errors:
        return False, f"Too many tool errors: {tool_errors} > {max_errors}"

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
    ctx.user_id = config.agent.user_id

    # Set allowed tools if specified (disallowed tools return an error)
    allowed_tools = test_case.get("allowed_tools")
    if allowed_tools:
        ctx.tools.allowed = set(allowed_tools)

    # Setup fixtures
    await _setup_workspace(config, test_case)

    # Pre-activate skills
    await _setup_skills(ctx, test_case)

    # Handle confirmation requests in evals.
    # setup.auto_confirm: true (default) = auto-approve, false = auto-deny
    auto_confirm = test_case.get("setup", {}).get("auto_confirm", True)

    import asyncio

    def _handle_confirm(event):
        if event.get("type") == "tool_confirm_request":
            asyncio.get_running_loop().create_task(bus.publish({
                "type": "tool_confirm_response",
                "context_id": event.get("context_id", ""),
                "tool": event.get("tool", ""),
                "tool_call_id": event.get("tool_call_id", ""),
                "approved": auto_confirm,
            }))
    bus.subscribe(_handle_confirm)

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
        ctx.tokens.total_prompt = 0
        ctx.tokens.total_completion = 0

        # Snapshot history length before this turn for per-turn counting
        pre_turn_history_len = len(history)
        pre_turn_tool_calls = _count_tool_calls(history)
        pre_turn_tool_errors = _count_tool_errors(history)

        start = time.monotonic()
        result = await run_agent_turn(ctx, turn["input"], history)
        response = result.text
        duration = time.monotonic() - start
        total_duration += duration

        # Per-turn counts (delta from pre-turn snapshot)
        tool_calls = _count_tool_calls(history) - pre_turn_tool_calls
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
            tool_errors = _count_tool_errors(history) - pre_turn_tool_errors
            passed, reason = _check_assertions(turn, response, tool_calls, tool_errors)
            if not passed:
                overall_passed = False
                # Collect errors from this turn's messages only
                error_details = _collect_tool_errors(history[pre_turn_history_len:])
                detail_str = "; ".join(error_details) if error_details else ""
                failure_reason = f"Turn {turn_idx + 1}: {reason}"
                if detail_str:
                    failure_reason += f"\n         Errors: {detail_str}"
                break

    # Gather cumulative metrics
    prompt_tokens = ctx.tokens.total_prompt
    completion_tokens = ctx.tokens.total_completion
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

    # Include full conversation history for debugging failed tests
    if not overall_passed:
        result["history"] = history

    return result


async def run_eval(yaml_data: list[dict], config: Config,
                   model: str | None = None,
                   verbose: bool = False,
                   concurrency: int = 4) -> tuple[dict, str, str]:
    """Run all test cases and return (results, timestamp, model_name).

    Tests run concurrently (up to `concurrency` at a time) but results
    are printed in test order after all complete.
    """
    import asyncio
    import tempfile
    from dataclasses import replace

    if model:
        # If model matches a model config, set it as the default
        if model in config.model_configs:
            config = replace(config, default_model=model)
        else:
            config = replace(config, llm=replace(config.llm, model=model))

    effective_model = config.default_model or config.llm.model
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")

    total = len(yaml_data)
    semaphore = asyncio.Semaphore(concurrency)

    completed = 0

    async def _run_one(i: int, test_case: dict) -> dict:
        nonlocal completed
        async with semaphore:
            with tempfile.TemporaryDirectory() as tmp:
                test_config = replace(config,
                    agent=replace(config.agent, data_home=tmp, id="eval"),
                )
                result = await run_test(test_config, test_case)
                completed += 1
                status = "✓" if result["status"] == "pass" else "✗"
                print(f"  {status} {completed}/{total} {test_case['name']}", flush=True)
                return result

    # Run all tests concurrently
    tasks = [_run_one(i, tc) for i, tc in enumerate(yaml_data)]
    test_results = await asyncio.gather(*tasks, return_exceptions=True)

    print()  # blank line before summary

    # Print results in order
    results = {
        "timestamp": datetime.now().isoformat(),
        "model": effective_model,
        "tests": [],
        "summary": {"total": 0, "passed": 0, "failed": 0,
                     "duration_sec": 0, "total_tokens": 0},
    }

    for i, result in enumerate(test_results):
        name = yaml_data[i]["name"]

        if isinstance(result, BaseException):
            result = {
                "name": name,
                "status": "fail",
                "duration_sec": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "tool_calls": 0,
                "response": "",
                "failure_reason": f"Exception: {result}",
            }

        status = "PASS" if result["status"] == "pass" else "FAIL"
        tokens = result["prompt_tokens"] + result["completion_tokens"]
        tools = result["tool_calls"]
        dur = result["duration_sec"]
        pad = "." * max(1, 50 - len(name))
        print(f"[{i+1}/{total}] {name} {pad} {status}  ({dur}s, {tokens} tokens, {tools} tools)")

        if verbose and result.get("response"):
            print(f"         Response: {result['response'][:200]}")

        if result["status"] == "fail":
            print(f"         {result.get('failure_reason', '')}")

        results["tests"].append(result)
        results["summary"]["total"] += 1
        results["summary"]["passed"] += 1 if result["status"] == "pass" else 0
        results["summary"]["failed"] += 1 if result["status"] == "fail" else 0
        results["summary"]["duration_sec"] += dur
        results["summary"]["total_tokens"] += tokens

    results["summary"]["duration_sec"] = round(results["summary"]["duration_sec"], 1)
    return results, timestamp, effective_model
