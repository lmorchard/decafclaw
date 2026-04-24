"""Failure reflection — ask a judge model to analyze test failures."""

import logging
from pathlib import Path

from ..llm import call_llm

log = logging.getLogger(__name__)

REFLECTION_PROMPT = """\
You are analyzing why an AI agent failed a test case.

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

    Returns the relative path to the reflection file, or None on error.
    """
    expected = test_case.get("expect", {}).get("response_contains", "?")
    setup_memories = test_case.get("setup", {}).get("memories", [])

    # Multi-turn tests use "turns" instead of "input"
    if "turns" in test_case:
        input_text = "\n".join(
            f"[turn {i + 1}] {t.get('input', '')}"
            for i, t in enumerate(test_case["turns"])
        )
    else:
        input_text = test_case.get("input", "")

    prompt = REFLECTION_PROMPT.format(
        name=test_case["name"],
        input=input_text,
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
        # Route through named model config if available, else legacy override
        if judge_model in config.model_configs:
            response = await call_llm(config, messages, model_name=judge_model)
        else:
            response = await call_llm(config, messages, llm_model=judge_model)
        reflection = response.get("content", "")
        if not reflection:
            return None

        # Write reflection file
        output_dir.mkdir(parents=True, exist_ok=True)
        slug = test_case["name"].lower().replace(" ", "-")[:50]
        filepath = output_dir / f"{slug}.md"
        filepath.write_text(f"# Reflection: {test_case['name']}\n\n{reflection}\n")

        return str(filepath.name)
    except Exception as e:
        log.error(f"Reflection failed: {e}")
        return None
