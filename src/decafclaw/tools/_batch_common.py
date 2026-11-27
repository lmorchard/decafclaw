"""Shared batch-concurrency engine for tools that fan out independent items.

``delegate_tasks`` (child agents) and ``llm_query`` (completions) both run a
batch of independent items under a semaphore, publish one aggregate
``tool_status`` event per completion, gather with
``return_exceptions=True``, and report ordered results plus a summary. This
module holds that engine once (#1336); each tool supplies its own worker and
status wording — per-item semantics stay in the tool module.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

log = logging.getLogger(__name__)


def _default_failure_text(index: int, exc: BaseException) -> str:
    return f"internal error: {exc}"


async def run_concurrent(
    worker: Callable[[int, Any], Awaitable[dict]],
    items: Sequence[Any],
    concurrency: int,
    publish_status: Callable[[int, int], Awaitable[None]],
    *,
    failure_text: Callable[[int, BaseException], str] | None = None,
) -> tuple[list[dict], dict]:
    """Run ``worker(idx, item)`` for every item under a concurrency cap.

    Args:
        worker: Per-item coroutine returning an entry dict carrying at least
            ``index`` and ``ok``, plus either success or error fields.
            Workers are expected to catch their own errors; a worker that
            raises is normalized into a failure entry.
        items: Batch input; item order defines result indices.
        concurrency: Maximum workers in flight at once (clamped to >= 1).
            The semaphore bounds the worker call only — progress publishing
            happens after the slot is released.
        publish_status: Called with ``(done, total)`` after each item
            completes (success or failure). Fail-open: a publisher that
            raises is logged and swallowed.
        failure_text: Formats the error text for a leaked worker exception,
            receiving ``(index, exc)``. Defaults to ``"internal error: {exc}"``.

    Returns ``(results, summary)``: results ordered by input index (each
    entry carries ``index``, ``ok``, and either success or error fields),
    plus ``{"total": N, "ok": M, "failed": K}``.
    """
    if failure_text is None:
        failure_text = _default_failure_text

    semaphore = asyncio.Semaphore(max(1, concurrency))
    progress: dict[str, int] = {"done": 0}
    progress_lock = asyncio.Lock()
    total = len(items)

    async def _run_one(idx: int, item: Any) -> dict:
        try:
            async with semaphore:
                entry = await worker(idx, item)
        except Exception as exc:
            # Workers are expected to catch their own errors; this is purely
            # defensive against bugs in the worker path.
            log.warning("run_concurrent: item %d raised unexpectedly: %s", idx, exc)
            return {"index": idx, "ok": False, "error": failure_text(idx, exc)}

        async with progress_lock:
            progress["done"] += 1
            done = progress["done"]
        try:
            await publish_status(done, total)
        except Exception:
            log.debug("run_concurrent: failed to publish progress (item %d)", idx, exc_info=True)
        return entry

    raw_results = await asyncio.gather(
        *(_run_one(i, item) for i, item in enumerate(items)),
        return_exceptions=True,
    )

    # Replace any leaked exception with a structured failure entry. Workers
    # catch their own errors, so this is purely defensive against bugs in
    # the gather/setup path (and non-Exception escapes such as cancellation).
    results: list[dict] = []
    for i, r in enumerate(raw_results):
        if isinstance(r, BaseException):
            log.warning("run_concurrent: gather slot %d raised unexpectedly: %s", i, r)
            results.append({"index": i, "ok": False, "error": failure_text(i, r)})
        else:
            results.append(r)

    results.sort(key=lambda e: e["index"])
    ok_count = sum(1 for e in results if e.get("ok"))
    return results, {"total": total, "ok": ok_count, "failed": total - ok_count}
