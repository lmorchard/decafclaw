"""One-shot model completion — the llm_query tool (#1258).

Runs single-shot completions against a named model with no tool schemas
and no agent scaffolding. The request carries only the system and prompt
text, so small local models (Ollama/Otari endpoints) can do extraction,
summarization, and classification without tool hallucination.

For a subtask that needs tools or multi-turn reasoning, use
``delegate_task`` instead.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ..media import ToolResult
from .delegate import (
    ERROR_TEXT_PREFIX,
    _parse_structured_output,
    _render_schema_addendum,
    _validate_return_schema,
)

if TYPE_CHECKING:
    from decafclaw.context import Context
    from decafclaw.llm import ResolvedModel

log = logging.getLogger(__name__)

# Batch bounds: worst case is 25 items at concurrency 5, each under its
# own per-item timeout. The tool opts out of the global watchdog for the
# same reason delegate_task does (batch duration scales with item count).
MAX_PROMPTS_PER_CALL = 25
BATCH_CONCURRENCY = 5


def _resolve_target(ctx: "Context", model: str) -> "ResolvedModel | ToolResult":
    """Resolve the provider and model for a one-shot call.

    Returns a ``ResolvedModel`` on success or an error ToolResult. An
    explicit (non-empty) ``model`` must be a key in
    ``config.model_configs``; an unknown name is a hard error that lists
    the configured models — the tool never falls back to another model,
    because silent fallback would route a cheap-model request to an
    expensive one and defeat the cost-control purpose.

    An empty ``model`` inherits the conversation's active model. A stale
    inherited name (removed from config since selection) warns and falls
    back to the default, mirroring ``_resolve_model_override`` in the
    agent loop: inherited state comes from unvalidated sources, while a
    tool argument is an explicit choice.
    """
    from ..llm import _resolve  # deferred: llm package import cycle

    config = ctx.config
    known = ", ".join(sorted(config.model_configs)) or "(none configured)"

    if model:
        if model not in config.model_configs:
            return ToolResult(text=f"[error: unknown model '{model}'. Configured models: {known}]")
        try:
            return _resolve(config, model_name=model)
        except Exception as exc:
            return ToolResult(text=f"[error: model resolution failed for '{model}': {exc}]")

    name = ctx.active_model
    if name and name not in config.model_configs:
        log.warning(
            "llm_query: inherited model %r not in config.model_configs — falling back to default. Configured models: %s",
            name,
            known,
        )
        name = ""
    try:
        return _resolve(config, model_name=name or None)
    except Exception as exc:
        return ToolResult(text=f"[error: no model available for llm_query: {exc}]")


def _record_usage(ctx: "Context", usage: dict | None) -> None:
    """Accumulate a completion's reported usage into the token counters.

    Records on the per-call counter and, when this ctx is a tool-call
    fork, on the turn's counters as well — same accumulation agent LLM
    calls get (``agent.py``), so the usage shows up in turn-completion
    reporting. Never touches ``last_prompt`` or the compaction estimate:
    those track the conversation's main model, and this completion is a
    separate request that does not grow the conversation history.
    """
    if not usage:
        return
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    cached_tokens = int(usage.get("cached_tokens", 0) or 0)

    ctx.tokens.total_prompt += prompt_tokens
    ctx.tokens.total_completion += completion_tokens
    ctx.tokens.total_cached_prompt += cached_tokens

    parent = getattr(ctx, "_parent_ctx", None)
    if parent is not None:
        parent.tokens.total_prompt += prompt_tokens
        parent.tokens.total_completion += completion_tokens
        parent.tokens.total_cached_prompt += cached_tokens


async def _run_one(
    ctx: "Context",
    *,
    prompt_text: str,
    idx: int | None,
    provider: Any,
    model_id: str,
    system: str,
    return_schema: dict | None,
    strict: bool,
    timeout: int,
    completion_options: dict[str, Any],
    semaphore: asyncio.Semaphore | None = None,
) -> dict:
    """Run one completion and return a per-item result entry.

    The entry carries ``index`` (None for single calls), ``ok``, and
    either ``text``/``data`` (success) or ``error`` (failure), matching
    the delegate batch shape. The call runs under a per-item
    ``asyncio.wait_for`` so the timeout is enforced at tool level for
    every provider type (the scripted test provider does not enforce its
    ``timeout`` argument), and the same value is passed to
    ``complete()`` for providers that enforce it internally.
    """

    async def _call() -> dict:
        system_text = system
        if return_schema is not None:
            addendum = _render_schema_addendum(return_schema)
            if addendum:
                system_text = f"{system}\n\n{addendum}" if system else addendum

        messages: list[dict[str, Any]] = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.append({"role": "user", "content": prompt_text})

        response = await provider.complete(
            model_id,
            messages,
            tools=None,
            timeout=timeout,
            **completion_options,
        )

        _record_usage(ctx, response.get("usage"))

        content = response.get("content") or ""
        text = content
        data: Any = None

        if return_schema is not None and content:
            parsed, prose = _parse_structured_output(content)
            if parsed is not None:
                errors = _validate_return_schema(return_schema, parsed)
                if not errors:
                    data = parsed
                    text = prose or content
                elif strict:
                    return {
                        "index": idx,
                        "ok": False,
                        "error": (f"{ERROR_TEXT_PREFIX} structured return validation failed: " + "; ".join(errors)),
                    }
                # Permissive: non-compliant block falls back to raw text, no data.

        entry: dict = {"index": idx, "ok": True, "text": text}
        if data is not None:
            entry["data"] = data
        return entry

    async def _guarded() -> dict:
        try:
            # asyncio.TimeoutError is the built-in TimeoutError on 3.11+.
            return await asyncio.wait_for(_call(), timeout=timeout)
        except TimeoutError:
            log.warning("llm_query: item %s timed out after %ss", idx if idx is not None else "single", timeout)
            return {
                "index": idx,
                "ok": False,
                "error": f"{ERROR_TEXT_PREFIX} llm_query timed out after {timeout}s",
            }
        except Exception as exc:
            log.warning(
                "llm_query: item %s raised unexpectedly: %s",
                idx if idx is not None else "single",
                exc,
            )
            return {
                "index": idx,
                "ok": False,
                "error": f"{ERROR_TEXT_PREFIX} llm_query call failed: {exc}",
            }

    if semaphore is not None:
        async with semaphore:
            return await _guarded()
    return await _guarded()


async def tool_llm_query(
    ctx: "Context",
    prompt: str | list[str],
    model: str = "",
    system: str = "",
    return_schema: dict | None = None,
    strict: bool = False,
    timeout: int = 60,
) -> ToolResult:
    """Run one or more single-shot completions against a named model.

    No tool schemas, no conversation history: the model sees only
    ``system`` (when non-empty) and ``prompt``. A list prompt batches the
    items concurrently under an internal semaphore (max 25 per call).
    """

    if isinstance(prompt, list):
        if not prompt:
            return ToolResult(text="[error: prompt must be a non-empty string or list]")
        if len(prompt) > MAX_PROMPTS_PER_CALL:
            return ToolResult(
                text=(
                    f"[error: too many prompts ({len(prompt)}); cap is "
                    f"{MAX_PROMPTS_PER_CALL} per call. Split the batch.] "
                )
            )
        for i, p in enumerate(prompt):
            if not isinstance(p, str) or not p.strip():
                return ToolResult(text=f"[error: prompt[{i}] must be a non-empty string]")
    elif not isinstance(prompt, str) or not prompt.strip():
        return ToolResult(text="[error: prompt must be a non-empty string]")

    if not isinstance(timeout, int) or timeout <= 0:
        return ToolResult(text="[error: timeout must be a positive integer number of seconds]")

    target = _resolve_target(ctx, model)
    if isinstance(target, ToolResult):
        return target

    log.info(
        "[tool:llm_query] items=%d model=%s schema=%s strict=%s",
        len(prompt) if isinstance(prompt, list) else 1,
        model or "inherit",
        "yes" if return_schema is not None else "no",
        strict,
    )

    completion_options = target.completion_options()

    if isinstance(prompt, str):
        entry = await _run_one(
            ctx,
            prompt_text=prompt,
            idx=None,
            provider=target.provider,
            model_id=target.model,
            system=system,
            return_schema=return_schema,
            strict=strict,
            timeout=timeout,
            completion_options=completion_options,
        )
        if not entry["ok"]:
            return ToolResult(text=entry["error"])
        result = ToolResult(text=entry["text"])
        if "data" in entry:
            result.data = entry["data"]
        return result

    semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)
    coros = [
        _run_one(
            ctx,
            prompt_text=p,
            idx=i,
            provider=target.provider,
            model_id=target.model,
            system=system,
            return_schema=return_schema,
            strict=strict,
            timeout=timeout,
            completion_options=completion_options,
            semaphore=semaphore,
        )
        for i, p in enumerate(prompt)
    ]
    raw_results = await asyncio.gather(*coros, return_exceptions=True)

    results: list[dict] = []
    for i, r in enumerate(raw_results):
        if isinstance(r, BaseException):
            log.warning("llm_query: gather slot %d raised unexpectedly: %s", i, r)
            results.append(
                {
                    "index": i,
                    "ok": False,
                    "error": f"{ERROR_TEXT_PREFIX} llm_query internal error: {r}",
                }
            )
        else:
            results.append(r)

    results.sort(key=lambda e: e["index"])
    total = len(prompt)
    ok_count = sum(1 for e in results if e["ok"])
    fail_count = total - ok_count
    summary_line = f"{total} completions: {ok_count} succeeded, {fail_count} failed"
    return ToolResult(
        text=summary_line,
        data={
            "summary": {
                "total": total,
                "ok": ok_count,
                "failed": fail_count,
            },
            "results": results,
        },
    )


LLM_TOOLS = {
    "llm_query": tool_llm_query,
}

LLM_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        # Bounded by per-item timeout; batch duration scales with item count.
        "timeout": None,
        # No side effects beyond the LLM call itself: runs concurrently.
        "writes": False,
        "function": {
            "name": "llm_query",
            "description": (
                "Run one or more single-shot completions against a named model with no tools and no agent scaffolding. "
                "Use for narrow text transformation: extraction, summarization, classification, reformatting — especially against a small local model "
                "(a config.model_configs entry pointing at an Ollama or Otari endpoint). The model sees only your system and prompt text; it cannot call tools. "
                "For a subtask that needs tools, multi-turn reasoning, or its own context, use delegate_task instead.\n\n"
                "prompt accepts a single string or a list of strings (batched concurrently, max 25 per call). "
                "model names an entry in config.model_configs; empty inherits the conversation's active model. An unknown name is an error, never a silent fallback. "
                "With return_schema (a flat field-to-type map), a compliant fenced JSON block in the response parses to ToolResult.data; "
                "strict=true turns non-compliant output into an error with field-level feedback, while strict=false falls back to the raw text. "
                "Batch results arrive on ToolResult.data as {'summary': {'total', 'ok', 'failed'}, 'results': [{'index', 'ok', ...}]}, ordered by input index."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "anyOf": [
                            {
                                "type": "string",
                                "description": "Single prompt text for one completion.",
                            },
                            {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "List of prompts to run concurrently (max 25). Each item gets its own completion."
                                ),
                            },
                        ],
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "Named model from config.model_configs. Empty inherits the conversation's active model."
                        ),
                    },
                    "system": {
                        "type": "string",
                        "description": ("System prompt for the completion. Empty sends none."),
                    },
                    "return_schema": {
                        "type": "object",
                        "description": (
                            'Optional flat field-to-type map (for example {"title": "string", "score": "number"}) '
                            "requesting a fenced JSON block in the response. The parsed object arrives on ToolResult.data."
                        ),
                    },
                    "strict": {
                        "type": "boolean",
                        "description": (
                            "Require a schema-compliant JSON block. Non-compliant output returns an error with field-level feedback. "
                            "Default false: non-compliant output falls back to the raw text with no data."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": ("Per-item timeout in seconds. Default 60."),
                    },
                },
                "required": ["prompt"],
            },
        },
    }
]
