"""Model completion — the llm_query tool (#1258).

Runs completions against a named model with no tool schemas and no agent
scaffolding. A call carries system text plus either a single prompt or a
multi-turn ``messages`` history, so small local models (Ollama/Otari
endpoints) can do extraction, summarization, and classification without
tool hallucination, and skills can drive dialogue without duplicating
provider plumbing.

For a subtask that needs tools or its own context, use ``delegate_task``
instead.
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


_MESSAGE_ROLES = frozenset({"system", "user", "assistant"})


def _validate_messages(messages: Any) -> list[dict[str, str]] | ToolResult:
    """Validate a caller-supplied chat history.

    Only ``role``/``content`` string pairs are accepted: the tool has no
    tool-call protocol, so extra keys would be silently dropped.
    """
    if not isinstance(messages, list) or not messages:
        return ToolResult(text="[error: messages must be a non-empty list of {'role', 'content'} objects]")
    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            return ToolResult(text=f"[error: messages[{i}] must be an object]")
        extra = sorted(set(message) - {"role", "content"})
        if extra:
            return ToolResult(text=f"[error: messages[{i}] has unsupported keys: {', '.join(extra)}]")
        if message.get("role") not in _MESSAGE_ROLES:
            return ToolResult(text=f"[error: messages[{i}].role must be one of system, user, assistant]")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return ToolResult(text=f"[error: messages[{i}].content must be a non-empty string]")
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def _assemble_messages(
    system: str,
    base: list[dict[str, str]] | None,
    prompt: str | None,
) -> list[dict[str, str]]:
    """Combine system text, an optional history, and an optional final user turn.

    ``system`` merges into a leading system message or is prepended;
    ``prompt`` is appended as the final user turn.
    """
    messages = [dict(m) for m in base] if base else []
    if system:
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] = f"{system}\n\n{messages[0]['content']}"
        else:
            messages.insert(0, {"role": "system", "content": system})
    if prompt is not None:
        messages.append({"role": "user", "content": prompt})
    return messages


def _apply_schema_instruction(
    messages: list[dict[str, Any]],
    return_schema: dict | None,
) -> list[dict[str, Any]]:
    """Add the structured-output instruction to the system message."""
    if return_schema is None:
        return messages
    addendum = _render_schema_addendum(return_schema)
    if not addendum:
        return messages
    if messages and messages[0]["role"] == "system":
        messages[0]["content"] = f"{messages[0]['content']}\n\n{addendum}"
    else:
        messages.insert(0, {"role": "system", "content": addendum})
    return messages


def _validate_sampler(
    temperature: Any,
    top_p: Any,
    max_tokens: Any,
    stop: Any,
) -> dict[str, Any] | ToolResult:
    """Validate per-call sampler overrides and return provider kwargs.

    The result merges over the model configuration's completion options,
    so a caller can vary sampling per call without duplicating a model
    entry. An empty ``stop`` list means no stop sequences.
    """
    options: dict[str, Any] = {}
    if temperature is not None:
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0.0 <= temperature <= 2.0:
            return ToolResult(text="[error: temperature must be a number between 0.0 and 2.0]")
        options["temperature"] = float(temperature)
    if top_p is not None:
        if isinstance(top_p, bool) or not isinstance(top_p, (int, float)) or not 0.0 < top_p <= 1.0:
            return ToolResult(text="[error: top_p must be a number greater than 0.0 and at most 1.0]")
        options["top_p"] = float(top_p)
    if max_tokens is not None:
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            return ToolResult(text="[error: max_tokens must be a positive integer]")
        options["max_tokens"] = max_tokens
    if stop is not None:
        if not isinstance(stop, list) or any(not isinstance(s, str) or not s for s in stop):
            return ToolResult(text="[error: stop must be a list of non-empty strings]")
        if stop:
            options["stop"] = list(stop)
    return options


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
    messages: list[dict[str, Any]],
    idx: int | None,
    provider: Any,
    model_id: str,
    return_schema: dict | None,
    strict: bool,
    timeout: int,
    completion_options: dict[str, Any],
    semaphore: asyncio.Semaphore | None = None,
    progress: dict | None = None,
    progress_lock: asyncio.Lock | None = None,
    total: int | None = None,
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
        request_messages = _apply_schema_instruction([dict(m) for m in messages], return_schema)

        response = await provider.complete(
            model_id,
            request_messages,
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

    async def _finished(entry: dict) -> dict:
        """Emit the aggregate progress event (batch calls only)."""
        if progress is None or progress_lock is None or total is None:
            return entry
        async with progress_lock:
            progress["done"] += 1
            done = progress["done"]
        try:
            await ctx.publish(
                "tool_status",
                tool="llm_query",
                message=f"{done}/{total} completions",
            )
        except Exception:
            log.debug(
                "llm_query: failed to publish progress event (item %s)",
                idx,
                exc_info=True,
            )
        return entry

    if semaphore is not None:
        async with semaphore:
            return await _finished(await _guarded())
    return await _finished(await _guarded())


async def tool_llm_query(
    ctx: "Context",
    prompt: str | list[str] | None = None,
    model: str = "",
    system: str = "",
    messages: list[dict[str, str]] | None = None,
    return_schema: dict | None = None,
    strict: bool = False,
    timeout: int = 60,
    temperature: float | None = None,
    top_p: float | None = None,
    max_tokens: int | None = None,
    stop: list[str] | None = None,
) -> ToolResult:
    """Run one or more completions against a named model.

    No tool schemas: the model sees ``system`` (when non-empty), an
    optional ``messages`` history, and an optional final ``prompt`` user
    turn. A list prompt batches independent items concurrently under an
    internal semaphore (max 25 per call); ``messages`` is a single
    conversation and cannot be combined with a batch.

    Per-call sampler overrides (``temperature``, ``top_p``,
    ``max_tokens``, ``stop``) merge over the model configuration's
    completion options for this call only.
    """

    if isinstance(prompt, list):
        if messages is not None:
            return ToolResult(text="[error: messages cannot be combined with a prompt list]")
        if not prompt:
            return ToolResult(text="[error: prompt must be a non-empty string or list]")
        if len(prompt) > MAX_PROMPTS_PER_CALL:
            return ToolResult(
                text=(
                    f"[error: too many prompts ({len(prompt)}); cap is "
                    f"{MAX_PROMPTS_PER_CALL} per call. Split the batch.]"
                )
            )
        for i, p in enumerate(prompt):
            if not isinstance(p, str) or not p.strip():
                return ToolResult(text=f"[error: prompt[{i}] must be a non-empty string]")
    elif prompt is not None:
        if not isinstance(prompt, str) or not prompt.strip():
            return ToolResult(text="[error: prompt must be a non-empty string]")

    if messages is not None:
        validated = _validate_messages(messages)
        if isinstance(validated, ToolResult):
            return validated
        messages = validated

    if prompt is None and messages is None:
        return ToolResult(text="[error: prompt or messages is required]")

    if not isinstance(timeout, int) or timeout <= 0:
        return ToolResult(text="[error: timeout must be a positive integer number of seconds]")

    sampler = _validate_sampler(temperature, top_p, max_tokens, stop)
    if isinstance(sampler, ToolResult):
        return sampler

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
    completion_options.update(sampler)

    if not isinstance(prompt, list):
        entry = await _run_one(
            ctx,
            messages=_assemble_messages(system, messages, prompt),
            idx=None,
            provider=target.provider,
            model_id=target.model,
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
    progress: dict = {"done": 0}
    progress_lock = asyncio.Lock()
    coros = [
        _run_one(
            ctx,
            messages=_assemble_messages(system, None, p),
            idx=i,
            provider=target.provider,
            model_id=target.model,
            return_schema=return_schema,
            strict=strict,
            timeout=timeout,
            completion_options=completion_options,
            semaphore=semaphore,
            progress=progress,
            progress_lock=progress_lock,
            total=len(prompt),
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


async def tool_llm_models(
    ctx: "Context",
    provider: str = "",
    query: str = "",
) -> ToolResult:
    """List the models configured in ``config.model_configs``.

    Discovery companion to ``llm_query``/``delegate_task(model=...)``:
    agents otherwise have to guess a model name or read the error an
    unknown name produces. Read-only and local — no network probe, no
    remote provider check.

    ``provider`` matches the provider key (exact or case-insensitive
    substring); ``query`` matches the config name or the underlying
    model id. The default is the effective default model (#1211: the catalog pointer
    when it names a configured model, else ``config.default_model``) —
    what an empty ``model`` argument falls back to when the conversation
    has no active model. The active model is the conversation's valid
    selection; a stale name that is no longer configured is not reported
    as active, mirroring ``_resolve_target``.
    """
    from ..model_catalog import effective_default_model  # deferred: llm pkg import cycle

    config = ctx.config
    default_name = effective_default_model(config)
    active_name = ctx.active_model if ctx.active_model in config.model_configs else ""

    provider_needle = provider.strip().lower()
    query_needle = query.strip().lower()

    models: list[dict[str, Any]] = []
    for name in sorted(config.model_configs):
        mc = config.model_configs[name]
        if provider_needle and provider_needle not in mc.provider.lower():
            continue
        if query_needle and query_needle not in name.lower() and query_needle not in mc.model.lower():
            continue
        models.append(
            {
                "name": name,
                "provider": mc.provider,
                "model": mc.model,
                "is_default": name == default_name,
                "is_active": name == active_name,
            }
        )

    configured_total = len(config.model_configs)
    total = len(models)

    if not configured_total:
        text = "No models configured in config.model_configs."
    elif not models:
        filters = ", ".join(
            part
            for part in (
                f"provider='{provider}'" if provider.strip() else "",
                f"query='{query}'" if query.strip() else "",
            )
            if part
        )
        text = (
            f"No configured models match {filters} "
            f"({configured_total} configured: {', '.join(sorted(config.model_configs))})."
        )
    else:
        scope = (
            f"{total} of {configured_total} configured models"
            if total != configured_total
            else f"{total} configured model{'s' if total != 1 else ''}"
        )
        status = []
        if default_name:
            status.append(f"default: {default_name}")
        if active_name:
            status.append(f"active: {active_name}")
        lines = [f"{scope} ({'; '.join(status)})" if status else scope]
        for entry in models:
            flags = [
                label for label, flag in (("default", entry["is_default"]), ("active", entry["is_active"])) if flag
            ]
            line = f"- {entry['name']} [provider={entry['provider']}, model={entry['model']}]"
            if flags:
                line += f" ({', '.join(flags)})"
            lines.append(line)
        text = "\n".join(lines)

    return ToolResult(
        text=text,
        data={
            "default_model": default_name,
            "active_model": active_name,
            "total": total,
            "models": models,
        },
    )


LLM_TOOLS = {
    "llm_query": tool_llm_query,
    "llm_models": tool_llm_models,
}

LLM_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        # Bounded by per-item timeout; batch duration scales with item count.
        "timeout": None,
        # No side effects beyond the LLM call itself: runs concurrently.
        "writes": False,
        "summary": "Run a completion on a named model, with no tools.",
        "function": {
            "name": "llm_query",
            "description": (
                "Run one or more completions against a named model with no tools and no agent scaffolding. "
                "Use for narrow text transformation: extraction, summarization, classification, reformatting — especially against a small local model "
                "(a config.model_configs entry pointing at an Ollama or Otari endpoint). The model cannot call tools. "
                "For a subtask that needs tools or its own context, use delegate_task instead.\n\n"
                "prompt accepts a single string or a list of strings (batched concurrently, max 25 per call). "
                "messages supplies a real multi-turn history ([{'role': 'system'|'user'|'assistant', 'content': '...'}]) instead of a single prompt; "
                "prompt may still be given to append a final user turn. system merges into a leading system message or is prepended. "
                "messages cannot be combined with a prompt list. "
                "model names an entry in config.model_configs (call llm_models to list them); empty inherits the conversation's active model. An unknown name is an error, never a silent fallback. "
                "Per-call sampler overrides (temperature 0.0-2.0, top_p >0.0-1.0, max_tokens, stop) apply to this call only and override the model configuration. "
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
                                "description": "Single prompt text for one completion; appended as the final user turn when messages is also given.",
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
                    "messages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string", "enum": ["system", "user", "assistant"]},
                                "content": {"type": "string"},
                            },
                            "required": ["role", "content"],
                        },
                        "description": (
                            "Multi-turn chat history, in order. Used as the base message list; system merges into a leading "
                            "system message and prompt appends a final user turn. Cannot be combined with a prompt list."
                        ),
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
                    "temperature": {
                        "type": "number",
                        "description": "Sampling temperature for this call (0.0-2.0). Overrides the model configuration.",
                    },
                    "top_p": {
                        "type": "number",
                        "description": "Nucleus sampling probability for this call (>0.0-1.0). Overrides the model configuration.",
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "Completion token cap for this call. Overrides the model configuration.",
                    },
                    "stop": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Stop sequences: generation ends when one is produced. Not supported by openai-responses providers."
                        ),
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
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        # Read-only: concurrent with other shared calls in a batch.
        "execution": "shared",
        "writes": False,
        "summary": "List configured models accepted by llm_query and delegate_task.",
        "function": {
            "name": "llm_models",
            "description": (
                "List the models configured in config.model_configs — the names llm_query(model=...) and delegate_task(model=...) accept. "
                "Each entry shows the config name, the underlying model identifier, the provider that backs it, and whether it is the conversation's active model or the default. "
                "Call this before llm_query or delegate_task when you are unsure which model name to use, instead of guessing or waiting for an unknown-model error. "
                "Optional filters: provider (provider key, exact or substring) and query (case-insensitive substring of the name or underlying model). "
                "Read-only and local: no network probe and no remote provider ping. Results arrive as readable text plus ToolResult.data "
                "{'default_model', 'active_model', 'total', 'models': [{'name', 'provider', 'model', 'is_default', 'is_active'}]}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "description": (
                            "Only models backed by this provider key, matched exactly or as a case-insensitive substring. Empty lists every provider."
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Only models whose config name or underlying model id contains this substring, case-insensitive. Empty lists every model."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
]
