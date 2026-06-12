"""WorkflowHandle — the `wf` object an orchestrator drives.

Exposes the two journaled primitives. Positional cursor advances once per
journaled call. Replay returns cached results (after a determinism check);
new llm_calls run live and journal; new user_inputs raise WorkflowSuspended.

Replay invariants the orchestrator author must respect:
  * The engine re-runs the orchestrator from the top on every resume; the
    cursor is reconstructed from 0 each run (it is NOT persisted). Re-running
    after a failed live llm_call is therefore safe — nothing was journaled, so
    the next run reaches the same step and runs it live again.
  * Orchestrators MUST NOT catch WorkflowSuspended. Swallowing it (e.g. a broad
    `except Exception`) lets the cursor advance past a step the journal never
    recorded, desynchronizing every later positional key. (Same constraint as
    Temporal's "don't catch workflow control-flow exceptions" rule.)
  * Any per-call `model=` override passed to `llm_call` must be deterministic
    (it is not part of the journal fingerprint).
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable, Coroutine

from decafclaw.tools import delegate as _delegate
from decafclaw.tools import execute_tool

from . import llm as wf_llm
from .errors import (
    WorkflowNonDeterministic,
    WorkflowSuspended,
    WorkflowToolNotAllowed,
)
from .journal import fingerprint, save_journal

log = logging.getLogger(__name__)


async def _default_llm_call(ctx, **kw):
    return await wf_llm.call_structured(ctx, **kw)


class WorkflowHandle:
    def __init__(self, ctx, journal, *, llm_caller=None,
                 model: str = "vertex-gemini-flash",
                 _key_prefix: tuple[int, ...] = ()):
        # `_key_prefix` is engine-internal: leading underscore signals it's
        # not for orchestrator authors. The top-level handle uses the default
        # `()`; sub-handles (Phase 2+) supply a path-shaped prefix so their
        # journaled calls land at hierarchical seqs like (outer, idx, ...).
        self.ctx = ctx
        self.journal = journal
        self._cursor = 0
        self._llm_caller = llm_caller or _default_llm_call
        self._model = model
        self._key_prefix = _key_prefix

    def _next_seq(self) -> tuple[int, ...]:
        """Advance the cursor and return the next journal key for this handle.

        Composes `_key_prefix` with the current cursor so a top-level handle
        yields `(0,)`, `(1,)`, ... while a sub-handle at prefix `(5, 2)`
        yields `(5, 2, 0)`, `(5, 2, 1)`, ...
        """
        seq = self._key_prefix + (self._cursor,)
        self._cursor += 1
        return seq

    def _make_subhandle_at(self, outer_seq: tuple[int, ...],
                           idx: int) -> "WorkflowHandle":
        """Create a sub-handle whose journal-key prefix is `outer_seq + (idx,)`.

        Used by Phase 5/6 primitives (`parallel`, `pipeline`) to give each
        thunk / pipeline stage its own key namespace. The caller is
        responsible for passing the already-computed `outer_seq` (typically
        the result of the parent's own `_next_seq()` for the outer entry) —
        this method does NOT advance the parent's cursor, so the parent's
        cursor flow stays legible at the call site:

            seq = self._next_seq()                # outer entry
            subs = [self._make_subhandle_at(seq, i) for i in range(n)]

        Sub-handles share `ctx`, `journal`, `llm_caller`, and `model` with
        the parent; each starts with `_cursor = 0`.
        """
        sub_prefix = outer_seq + (idx,)
        return WorkflowHandle(self.ctx, self.journal,
                              llm_caller=self._llm_caller,
                              model=self._model,
                              _key_prefix=sub_prefix)

    def _check_or_none(self, seq: tuple[int, ...], kind: str, fp: str):
        """Return (cached_result, True) for an already-journaled call at seq,
        else (None, False). Raises WorkflowNonDeterministic on a mismatch.
        """
        existing = self.journal.get(seq)
        if existing is None:
            return None, False
        if existing.kind != kind or existing.args_fingerprint != fp:
            raise WorkflowNonDeterministic(
                seq, existing.kind, existing.args_fingerprint, kind, fp)
        return existing.result, True

    async def llm_call(self, *, prompt: str, schema: dict, system: str = "",
                       tool_name: str = "submit", model: str | None = None):
        seq = self._next_seq()
        # tool_name and the per-call `model` override are intentionally NOT
        # part of the fingerprint: tool_name is a cosmetic label for the single
        # forced tool, and model is an execution detail. prompt + schema +
        # system fully determine the call's logical identity. CONSTRAINT: a
        # per-call model override therefore MUST be deterministic across
        # replays — computing it from nondeterministic state would let replay
        # reuse a result produced under a different model without the
        # determinism guard firing. (v1 orchestrators pass no per-call model.)
        fp = fingerprint("llm_call",
                         {"prompt": prompt, "schema": schema, "system": system})
        cached, hit = self._check_or_none(seq, "llm_call", fp)
        if hit:
            return cached
        result = await self._llm_caller(
            self.ctx, system=system, user_msg=prompt, schema=schema,
            tool_name=tool_name, model=model or self._model)
        self.journal.append(seq, "llm_call", fp, result)
        save_journal(self.ctx.config, self.ctx.conv_id, self.journal)
        return result

    async def tool_call(self, name: str, **args) -> dict:
        """Invoke a decafclaw tool by name. Journaled for replay.

        The tool is gated by `ctx.tools.allowed`: a name missing from the
        allowlist raises `WorkflowToolNotAllowed` before the journal is
        consulted, so orchestrator bugs fail loud rather than burning a
        journal slot. The gate also fires during replay — if the allowlist
        shrinks between turns, the caller hears about it.

        The result is captured as `{"text": str, "data": Any|None}`. Media
        attachments on the underlying `ToolResult` are intentionally
        stripped: media bytes can be large and are rebuildable from
        upstream sources; the journal exists for replay correctness, not
        full reconstruction. Both live and replay paths return this dict
        shape so orchestrators see a single result schema.
        """
        # Mirror `execute_tool`'s semantics: `allowed is None` means "no
        # restriction" (the orchestrator's parent agent could invoke any
        # registered tool). A non-empty set narrows; an empty set means
        # nothing is allowed. Check BEFORE consuming a cursor slot so the
        # docstring's "doesn't burn a journal slot" claim holds even if an
        # orchestrator catches `WorkflowToolNotAllowed` and continues.
        allowed = self.ctx.tools.allowed
        if allowed is not None and name not in allowed:
            raise WorkflowToolNotAllowed(
                f"tool {name!r} not in workflow's tool allowlist")
        seq = self._next_seq()
        fp = fingerprint("tool_call", {"name": name, "args": args})
        cached, hit = self._check_or_none(seq, "tool_call", fp)
        if hit:
            # Narrow for the type checker: a hit always carries the dict the
            # live path journaled (see `serialized` below).
            assert isinstance(cached, dict)
            return cached
        result = await execute_tool(self.ctx, name, args)
        serialized = {"text": result.text, "data": result.data}
        self.journal.append(seq, "tool_call", fp, serialized)
        save_journal(self.ctx.config, self.ctx.conv_id, self.journal)
        return serialized

    async def subagent(self, prompt: str, *,
                       schema: dict | None = None,
                       allowed_tools: list[str] | None = None,
                       allow_vault_retrieval: bool = False,
                       allow_vault_read: bool = False,
                       model: str | None = None) -> dict | str:
        """Dispatch a child agent loop as a journaled boundary crossing.

        Reuses the existing `delegate.run_child_turn` infrastructure: the
        child inherits the parent's tools and skills (minus the standard
        excludes — no further delegation, no skill activation, no vault
        writes); `allowed_tools` may narrow that further. The child runs as
        its own forked agent turn with its own conv_id.

        Returns the child's final text when `schema` is None. With `schema`
        supplied, the child is prompted to emit a fenced JSON block matching
        the shape; the parsed dict is returned (NOT the text). On a parse
        failure the call silently falls back to returning the raw text.

        Like `llm_call`'s model override, the per-call `model` is an
        execution detail and is NOT part of the journal fingerprint —
        callers may swap models between runs without busting the cache.
        """
        seq = self._next_seq()
        fp = fingerprint("subagent", {
            "prompt": prompt,
            "schema": schema,
            "allowed_tools": sorted(allowed_tools) if allowed_tools else None,
            "allow_vault_retrieval": allow_vault_retrieval,
            "allow_vault_read": allow_vault_read,
        })
        cached, hit = self._check_or_none(seq, "subagent", fp)
        if hit:
            # On replay, the cache is whatever the live path journaled: a
            # dict (when schema produced parseable JSON) or str (text or
            # error). Narrow for the type checker.
            assert isinstance(cached, (dict, str))
            return cached
        text, data = await _delegate.run_child_turn(
            self.ctx,
            task=prompt,
            # Default to the workflow handle's configured model (parallel to
            # `llm_call`). Without this, `run_child_turn` falls through to
            # `ctx.active_model`, which can differ from the workflow's intent.
            model=model or self._model,
            allowed_tools=allowed_tools,
            allow_vault_retrieval=allow_vault_retrieval,
            allow_vault_read=allow_vault_read,
            return_schema=schema,
        )
        result: dict | str = data if schema is not None and data is not None else text
        self.journal.append(seq, "subagent", fp, result)
        save_journal(self.ctx.config, self.ctx.conv_id, self.journal)
        return result

    async def parallel(
        self,
        thunks: list[Callable[["WorkflowHandle"], Coroutine[Any, Any, Any]]],
    ) -> list[Any]:
        """Run N thunks concurrently under sub-handles. Returns results in
        thunk-index order. Errors propagate — wrap individual thunks in
        try/except for tolerant collection.

        Each thunk receives a sub-handle keyed at (outer_seq, thunk_idx) so
        its own journaled calls land at hierarchical seqs. Sub-handles share
        ctx, journal, llm_caller, and model with the parent.

        The outer journal entry caches the assembled result list. On replay:
          * full-cache hit → return the list, no thunks run.
          * mid-fan-out crash (outer entry missing) → re-dispatch every
            thunk; each thunk's sub-handle hits its existing journal entries
            (cached) and resumes from the first non-cached call.

        Fingerprint includes only `count` — callables aren't JSON-serializable
        and the child entries provide per-thunk replay safety.

        Cooperative cancel: if `ctx.cancelled` is set during the fan-out, all
        in-flight thunks are cancelled and `asyncio.CancelledError` is raised.
        """
        seq = self._next_seq()
        fp = fingerprint("parallel", {"count": len(thunks)})
        cached, hit = self._check_or_none(seq, "parallel", fp)
        if hit:
            assert isinstance(cached, list)
            return cached

        sub_handles = [self._make_subhandle_at(seq, idx)
                       for idx in range(len(thunks))]
        tasks: list[asyncio.Task] = [
            asyncio.create_task(thunks[i](sub_handles[i]))
            for i in range(len(thunks))
        ]

        # Cancel watcher: if ctx.cancelled fires, race it against the fan-out
        # so we can preemptively cancel in-flight thunks. `ctx.cancelled is
        # None` in contexts that never wire the event (e.g. unit tests that
        # don't exercise cancellation) — skip the watcher there.
        #
        # We need the watcher to interrupt `asyncio.wait(..., FIRST_EXCEPTION)`
        # so it raises after the event fires (FIRST_EXCEPTION ignores tasks
        # that complete normally). The watcher raises an internal sentinel
        # rather than CancelledError so the task transitions to "has exception"
        # (not "cancelled"), which is what FIRST_EXCEPTION reacts to.
        cancel_event = self.ctx.cancelled

        class _CancelSignal(Exception):
            """Internal sentinel: cancel_event fired. Never escapes."""

        async def _cancel_watcher_body():
            assert cancel_event is not None
            await cancel_event.wait()
            raise _CancelSignal()

        cancel_watcher: asyncio.Task | None = None
        if cancel_event is not None:
            cancel_watcher = asyncio.create_task(_cancel_watcher_body())

        try:
            if not tasks:
                # Zero thunks: nothing to await. (We must not wait on just
                # the cancel watcher — it would block forever in the common
                # case where cancellation never fires.)
                results: list[Any] = []
            else:
                wait_set = list(tasks)
                if cancel_watcher is not None:
                    wait_set.append(cancel_watcher)
                done, pending = await asyncio.wait(
                    wait_set, return_when=asyncio.FIRST_EXCEPTION)

                cancel_fired = (
                    cancel_watcher is not None
                    and cancel_watcher in done
                    and not cancel_watcher.cancelled()
                    and isinstance(cancel_watcher.exception(), _CancelSignal)
                )
                if cancel_fired:
                    # Cancellation fired: tear down any in-flight thunks and
                    # surface CancelledError to the caller.
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    # `return_exceptions=True` swallows the CancelledErrors
                    # raised by the cancelled tasks — we re-raise our own
                    # below after cleanup.
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise asyncio.CancelledError()

                # No cancellation: either all tasks completed, or one raised
                # and `pending` may still hold others (plus the cancel_watcher
                # if no exception came from it). Cancel straggler thunks,
                # drain them, then surface the first REAL exception (in
                # thunk-index order). The cancel_watcher is cleaned up in
                # the `finally` block, not gathered here.
                stragglers = [t for t in pending if t is not cancel_watcher]
                for t in stragglers:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*stragglers, return_exceptions=True)
                # Locate the first task with a real (non-CancelledError)
                # exception. Cancellation of pending tasks happens via our
                # own cleanup gather above and must not mask the underlying
                # failure: a naive `tasks[i].result()` in index order would
                # raise the cleanup-induced CancelledError of a lower-index
                # straggler instead of the higher-index thunk's real error.
                first_exc: BaseException | None = None
                for t in tasks:
                    if t.done() and not t.cancelled():
                        exc = t.exception()
                        if exc is not None and not isinstance(
                                exc, asyncio.CancelledError):
                            first_exc = exc
                            break
                if first_exc is not None:
                    raise first_exc
                results = [t.result() for t in tasks]
        finally:
            if cancel_watcher is not None:
                if not cancel_watcher.done():
                    cancel_watcher.cancel()
                    try:
                        await cancel_watcher
                    except asyncio.CancelledError:
                        pass  # expected: we just cancelled it
                    except Exception as exc:  # noqa: BLE001
                        log.debug(
                            "parallel cancel-watcher cleanup error: %r", exc)
                else:
                    # Watcher already done — retrieve its exception (the
                    # _CancelSignal sentinel) so asyncio doesn't log a
                    # "Task exception was never retrieved" warning.
                    if not cancel_watcher.cancelled():
                        cancel_watcher.exception()

        self.journal.append(seq, "parallel", fp, results)
        save_journal(self.ctx.config, self.ctx.conv_id, self.journal)
        return results

    async def pipeline(
        self,
        items: list,
        *stages: Callable[[Any, Any, int, "WorkflowHandle"], Awaitable[Any]],
    ) -> list:
        """Per-item run of stage1 → stage2 → … → stageN. No barrier between
        stages: each item flows through every stage independently of the
        other items. Returns the final-stage result per item in item-index
        order.

        Each stage receives `(prev, item, idx, sub)` where `prev` is the
        previous stage's return (or `item` itself for the first stage),
        `item` is `items[idx]`, and `sub` is the per-item sub-handle that
        stages MUST use for any journaled call so keys land under
        `(outer_seq, idx, ...)`.

        Fingerprint includes `items` and `stage_count` (stages aren't
        callable-serializable). On a mid-fan-out crash the outer entry is
        NOT written, so replay re-dispatches every item; each item's
        sub-handle hits its already-journaled stage results and resumes
        from the first non-cached call.

        Cooperative cancel: if `ctx.cancelled` is set during the fan-out,
        all in-flight items are cancelled and `asyncio.CancelledError` is
        raised.
        """
        seq = self._next_seq()
        fp = fingerprint(
            "pipeline", {"items": items, "stage_count": len(stages)})
        cached, hit = self._check_or_none(seq, "pipeline", fp)
        if hit:
            assert isinstance(cached, list)
            return cached

        if not items:
            results: list = []
            self.journal.append(seq, "pipeline", fp, results)
            save_journal(self.ctx.config, self.ctx.conv_id, self.journal)
            return results

        async def _run_one(item, idx):
            sub = self._make_subhandle_at(seq, idx)
            prev = item
            for stage in stages:
                prev = await stage(prev, item, idx, sub)
            return prev

        tasks: list[asyncio.Task] = [
            asyncio.create_task(_run_one(items[i], i))
            for i in range(len(items))
        ]

        # Cancel watcher mirrors `parallel`'s pattern. The watcher raises a
        # private sentinel (not CancelledError) so the task transitions to
        # "has exception", which is what FIRST_EXCEPTION reacts to.
        cancel_event = self.ctx.cancelled

        class _CancelSignal(Exception):
            """Internal sentinel: cancel_event fired. Never escapes."""

        async def _cancel_watcher_body():
            assert cancel_event is not None
            await cancel_event.wait()
            raise _CancelSignal()

        cancel_watcher: asyncio.Task | None = None
        if cancel_event is not None:
            cancel_watcher = asyncio.create_task(_cancel_watcher_body())

        try:
            wait_set = list(tasks)
            if cancel_watcher is not None:
                wait_set.append(cancel_watcher)
            done, pending = await asyncio.wait(
                wait_set, return_when=asyncio.FIRST_EXCEPTION)

            cancel_fired = (
                cancel_watcher is not None
                and cancel_watcher in done
                and not cancel_watcher.cancelled()
                and isinstance(cancel_watcher.exception(), _CancelSignal)
            )
            if cancel_fired:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise asyncio.CancelledError()

            # No cancellation: cancel any pending stragglers and surface
            # the first REAL exception in item-index order. Naive
            # `tasks[i].result()` would raise the cleanup-induced
            # CancelledError of a lower-index straggler and mask a
            # higher-index item's actual failure — see `parallel`'s
            # post-fix shape for the regression guard.
            stragglers = [t for t in pending if t is not cancel_watcher]
            for t in stragglers:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*stragglers, return_exceptions=True)
            first_exc: BaseException | None = None
            for t in tasks:
                if t.done() and not t.cancelled():
                    exc = t.exception()
                    if exc is not None and not isinstance(
                            exc, asyncio.CancelledError):
                        first_exc = exc
                        break
            if first_exc is not None:
                raise first_exc
            results = [t.result() for t in tasks]
        finally:
            if cancel_watcher is not None:
                if not cancel_watcher.done():
                    cancel_watcher.cancel()
                    try:
                        await cancel_watcher
                    except asyncio.CancelledError:
                        pass  # expected: we just cancelled it
                    except Exception as exc:  # noqa: BLE001
                        log.debug(
                            "pipeline cancel-watcher cleanup error: %r", exc)
                else:
                    # Watcher already done — retrieve its exception (the
                    # _CancelSignal sentinel) so asyncio doesn't log a
                    # "Task exception was never retrieved" warning.
                    if not cancel_watcher.cancelled():
                        cancel_watcher.exception()

        self.journal.append(seq, "pipeline", fp, results)
        save_journal(self.ctx.config, self.ctx.conv_id, self.journal)
        return results

    async def user_input(self, prompt: str, *, choices: list[str] | None = None):
        seq = self._next_seq()
        fp = fingerprint("user_input", {"prompt": prompt, "choices": choices})
        cached, hit = self._check_or_none(seq, "user_input", fp)
        if hit:
            return cached
        # New, unanswered → suspend. The journal (entries prior to seq) is
        # already persisted by the preceding live call (or empty on a fresh
        # start).
        raise WorkflowSuspended(seq=seq, args_fingerprint=fp, prompt=prompt,
                                choices=choices)
