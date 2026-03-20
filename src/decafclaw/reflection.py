"""Self-reflection — evaluate agent responses before delivery.

Uses the Reflexion pattern: a separate judge LLM call evaluates whether
the agent's response adequately addresses the user's request. If not,
a critique is returned for retry.

References:
  - Reflexion (Shinn et al., NeurIPS 2023): https://arxiv.org/abs/2303.11366
  - G-Eval chain-of-thought scoring: https://www.confident-ai.com/blog/g-eval
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .llm import call_llm

log = logging.getLogger(__name__)

_BUNDLED_PROMPT = Path(__file__).parent / "prompts" / "REFLECTION.md"

MAX_TOOL_RESULT_LEN = 500


@dataclass
class ReflectionResult:
    """Result of a reflection evaluation."""
    passed: bool
    critique: str = ""
    raw_response: str = ""  # full judge output for debug mode
    error: str = ""         # non-empty if the judge call failed


def load_reflection_prompt(config) -> str:
    """Load the judge prompt template.

    Priority: agent-level override > bundled default.
    Override file: data/{agent_id}/REFLECTION.md
    Bundled default: src/decafclaw/prompts/REFLECTION.md
    """
    override_path = config.agent_path / "REFLECTION.md"
    if override_path.exists():
        try:
            return override_path.read_text()
        except OSError as exc:
            log.warning("Failed to read %s: %s", override_path, exc)
    return _BUNDLED_PROMPT.read_text()


def build_tool_summary(history: list, turn_start_index: int) -> str:
    """Extract tool call/result pairs from this turn's history.

    Scans history from turn_start_index onward for assistant messages with
    tool_calls and their corresponding tool-role result messages.
    """
    tool_lines: list[str] = []

    for msg in history[turn_start_index:]:
        # Assistant messages with tool_calls
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                name = fn.get("name", "unknown")
                args_str = fn.get("arguments", "")
                # Summarize args
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                    if isinstance(args, dict):
                        key_args = ", ".join(
                            f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}"
                            for k, v in list(args.items())[:3]
                        )
                    else:
                        key_args = str(args)[:100]
                except (json.JSONDecodeError, TypeError):
                    key_args = str(args_str)[:100]
                tool_lines.append(f"Tool: {name}({key_args})")

        # Tool result messages
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if len(content) > MAX_TOOL_RESULT_LEN:
                content = content[:MAX_TOOL_RESULT_LEN] + "..."
            tool_lines.append(f"Result: {content}")

    if not tool_lines:
        return ""
    return "Tools used this turn:\n" + "\n".join(tool_lines)


def _coerce_bool(value) -> bool | None:
    """Coerce a JSON value to bool. Returns None if not interpretable."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in ("true", "1", "yes"):
            return True
        if lower in ("false", "0", "no"):
            return False
    return None


def _extract_verdict(data: dict) -> tuple[bool | None, str]:
    """Extract pass/fail from a parsed JSON dict."""
    if "pass" not in data:
        return (None, "")
    passed = _coerce_bool(data["pass"])
    return (passed, data.get("critique", ""))


def _parse_verdict(text: str) -> tuple[bool | None, str]:
    """Extract pass/fail verdict and critique from judge response.

    Returns (passed, critique). passed is None if parsing fails.
    """
    # Try parsing the whole response as JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            result = _extract_verdict(data)
            if result[0] is not None:
                return result
    except (json.JSONDecodeError, TypeError):
        pass

    # Search for JSON object in the response text
    for match in re.finditer(r'\{[^{}]*\}', text):
        try:
            data = json.loads(match.group())
            if isinstance(data, dict):
                result = _extract_verdict(data)
                if result[0] is not None:
                    return result
        except (json.JSONDecodeError, TypeError):
            continue

    return (None, "")


async def evaluate_response(
    config,
    user_message: str,
    agent_response: str,
    tool_summary: str,
) -> ReflectionResult:
    """Run the judge LLM to evaluate the agent's response.

    Returns ReflectionResult. On any error, returns passed=True (fail-open).
    """
    try:
        prompt_template = load_reflection_prompt(config)
        prompt = prompt_template.format(
            user_message=user_message,
            tool_results_summary=tool_summary or "(no tools used)",
            agent_response=agent_response,
        )

        rc = config.reflection.resolved(config)
        messages = [{"role": "user", "content": prompt}]

        response = await call_llm(
            config, messages,
            llm_url=rc.url,
            llm_model=rc.model,
            llm_api_key=rc.api_key,
        )

        raw = response.get("content", "")
        passed, critique = _parse_verdict(raw)

        if passed is None:
            log.warning("Reflection judge returned unparseable output: %s", raw[:200])
            return ReflectionResult(passed=True, raw_response=raw,
                                    error="unparseable judge output")

        return ReflectionResult(passed=passed, critique=critique, raw_response=raw)

    except Exception as exc:
        log.error("Reflection judge call failed: %s", exc)
        return ReflectionResult(passed=True, error=str(exc))
