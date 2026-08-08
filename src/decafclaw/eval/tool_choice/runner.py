"""Tool-choice eval runner — one LLM call per case, no execution.

For each case, build a single chat completion mirroring the production
first turn (real system prompt, real tool schema, real descriptions).
Pull ``tool_calls`` off the assistant response and record what the
model reached for. No tool execution, no agent loop iteration — the
overlap signal we care about lives in the *first* decision.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ...llm import call_llm
from ...prompts import load_system_prompt
from .case import Case

log = logging.getLogger(__name__)

# Sentinel used when the model emits zero tool calls (chose to respond
# in plain text). Angle brackets make it un-confusable with any real
# tool name.
NO_TOOL = "<no_tool>"


@dataclass(frozen=True)
class CaseResult:
    case: Case
    model: str
    picked: str            # first tool name, or NO_TOOL
    all_picks: list[str] = field(default_factory=list)
    passed: bool = False
    pass_count: int = 0
    reps: int = 1
    rep_picks: list[str] = field(default_factory=list)


def _extract_picks(tool_calls: list | None) -> tuple[str, list[str]]:
    """Return (picked, all_picks) from a provider response's tool_calls.

    Provider responses normalize to a list of dicts with a ``function``
    sub-dict carrying ``name``. An empty/None list maps to ``NO_TOOL``.
    """
    if not tool_calls:
        return NO_TOOL, []
    names: list[str] = []
    for tc in tool_calls:
        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
        name = fn.get("name") if isinstance(fn, dict) else None
        if name:
            names.append(name)
    if not names:
        return NO_TOOL, []
    return names[0], names


async def run_case(
    case: Case,
    *,
    model: str,
    config,
    tool_loadout: list[dict],
    reps: int = 1,
    sem: asyncio.Semaphore | None = None,
) -> CaseResult:
    """Run one case and return its CaseResult.

    Builds a two-message conversation (system prompt + user scenario),
    calls the LLM with the full tool schema, captures the first tool
    name from ``tool_calls``. No execution of tools, no follow-up
    turns.
    """
    system_prompt, _ = load_system_prompt(config)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": case.scenario},
    ]

    async def _do_rep(i: int):
        if sem:
            await sem.acquire()
        try:
            response = await call_llm(
                config, messages, tools=tool_loadout, model_name=model,
            )
            return _extract_picks(response.get("tool_calls"))
        except Exception as exc:
            log.error("case %s on model %s (rep %d): LLM call failed: %s", case.name, model, i + 1, exc)
            return NO_TOOL, []
        finally:
            if sem:
                sem.release()

    results = await asyncio.gather(*(_do_rep(i) for i in range(reps)))

    pass_count = 0
    rep_picks = []

    for picked, all_picks in results:
        rep_picks.append(picked)
        if picked == case.expected:
            pass_count += 1

    first_picked, first_all_picks = results[0] if results else (NO_TOOL, [])

    return CaseResult(
        case=case,
        model=model,
        picked=first_picked,
        all_picks=first_all_picks,
        passed=(pass_count == reps),
        pass_count=pass_count,
        reps=reps,
        rep_picks=rep_picks,
    )


async def run_cases(
    cases: list[Case],
    *,
    model: str,
    config,
    tool_loadout: list[dict],
    concurrency: int = 4,
    reps: int = 1,
) -> list[CaseResult]:
    """Run a list of cases against a single model with bounded concurrency."""
    sem = asyncio.Semaphore(max(1, concurrency))

    return await asyncio.gather(*(
        run_case(c, model=model, config=config, tool_loadout=tool_loadout, reps=reps, sem=sem)
        for c in cases
    ))
