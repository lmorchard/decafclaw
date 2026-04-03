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


def _format_tool_args(fn: dict) -> str:
    """Format a tool_call function dict into a concise arg string."""
    name = fn.get("name", "unknown")
    args_str = fn.get("arguments", "")
    try:
        args = json.loads(args_str) if isinstance(args_str, str) else args_str
        if isinstance(args, dict):
            parts = []
            for k, v in list(args.items())[:3]:
                if isinstance(v, str) and len(v) > 100:
                    v = v[:100] + "..."
                parts.append(f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}")
            key_args = ", ".join(parts)
        else:
            key_args = str(args)[:100]
    except (json.JSONDecodeError, TypeError):
        key_args = str(args_str)[:100]
    return f"Tool: {name}({key_args})"


def _summarize_tool_result(content: str, max_len: int) -> str:
    """Summarize a tool result, preserving key structure.

    Instead of blindly truncating, extract headings, tl;dr lines, and
    result counts so the reflection judge can verify the agent's response
    references real content. Non-structural body text fills remaining budget.
    """
    if len(content) <= max_len:
        return content

    lines = content.split("\n")

    # First pass: collect all structural lines (headings, tl;dr, separators)
    structural: list[str] = []
    body: list[str] = []
    for line in lines:
        stripped = line.strip()
        is_structural = (
            stripped.startswith("# ")
            or stripped.startswith("## ")
            or stripped.startswith("> tl;dr:")
            or (stripped.startswith("Found ") and "result" in stripped)
            or stripped.startswith("--- ")
        )
        if is_structural:
            structural.append(line)
        else:
            body.append(line)

    # Build result: all structural lines first, then fill with body
    # Reserve space for the omission note
    omission_reserve = 60  # "[... NNNNN chars of detail omitted]"
    budget = max_len - omission_reserve

    result_parts = list(structural)
    total_len = sum(len(s) + 1 for s in result_parts)

    if budget > total_len:
        for line in body:
            remaining = budget - total_len
            if remaining <= 0:
                break
            if len(line) + 1 <= remaining:
                result_parts.append(line)
                total_len += len(line) + 1
            else:
                # Truncate oversized body lines (e.g. minified JSON/HTML)
                snippet = line[:max(0, remaining - 1)]
                if snippet:
                    result_parts.append(snippet)
                    total_len += len(snippet) + 1
                break

    selected = "\n".join(result_parts)
    if len(selected) >= len(content):
        return selected

    omitted = len(content) - len(selected)
    return selected + f"\n[... {omitted} chars of detail omitted]"


def _extract_tool_lines(messages: list, max_result_len: int) -> list[str]:
    """Extract tool call/result lines from a slice of history messages."""
    tool_lines: list[str] = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_lines.append(_format_tool_args(tc.get("function", {})))
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            content = _summarize_tool_result(content, max_result_len)
            tool_lines.append(f"Result: {content}")
    return tool_lines


def build_tool_summary(history: list, turn_start_index: int, max_result_len: int = 2000) -> str:
    """Extract tool call/result pairs from this turn's history.

    Scans history from turn_start_index onward for assistant messages with
    tool_calls and their corresponding tool-role result messages.
    """
    tool_lines = _extract_tool_lines(history[turn_start_index:], max_result_len)
    if not tool_lines:
        return ""
    return "Tools used this turn:\n" + "\n".join(tool_lines)


def build_prior_turn_summary(
    history: list, turn_start_index: int,
    max_turns: int = 3, max_result_len: int = 200,
) -> str:
    """Extract tool call/result pairs from prior turns.

    Scans history before turn_start_index, finds the last max_turns turns
    (bounded by user-role messages), and summarizes their tool usage.
    Returns empty string if no prior tools or on first turn.
    """
    if turn_start_index <= 0:
        return ""

    prior = history[:turn_start_index]

    # Find turn boundaries (indices of real user messages, not reflection critiques)
    turn_starts = [
        i for i, msg in enumerate(prior)
        if msg.get("role") == "user"
        and not str(msg.get("content", "")).startswith("[reflection]")
    ]
    if not turn_starts:
        return ""

    # Take the last max_turns turns
    turn_starts = turn_starts[-max_turns:]

    # Extract tool lines from each turn's slice
    tool_lines: list[str] = []
    for idx, start in enumerate(turn_starts):
        end = turn_starts[idx + 1] if idx + 1 < len(turn_starts) else len(prior)
        tool_lines.extend(_extract_tool_lines(prior[start:end], max_result_len))

    if not tool_lines:
        return ""
    return "Tools used in prior turns:\n" + "\n".join(tool_lines)


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
    prior_turn_summary: str = "",
    retrieved_context: str = "",
) -> ReflectionResult:
    """Run the judge LLM to evaluate the agent's response.

    Returns ReflectionResult. On any error, returns passed=True (fail-open).
    """
    try:
        prompt_template = load_reflection_prompt(config)
        if retrieved_context:
            context_block = (
                "Retrieved context (automatically injected before the user's message):\n"
                + retrieved_context
            )
        else:
            context_block = ""
        prompt = prompt_template.format(
            user_message=user_message,
            tool_results_summary=tool_summary or "(no tools used)",
            agent_response=agent_response,
            retrieved_context=context_block,
            prior_turn_tools=prior_turn_summary,
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
