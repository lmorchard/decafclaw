"""Tests for the shared batch-concurrency engine in ``tools/_batch_common.py`` (#1336).

Covers ``run_concurrent`` directly — the semaphore cap, ordered results,
exception normalization, and fail-open progress publishing — plus a wiring
check that both batch tools import the shared helper. The check that each
tool actually routes its batch through it lives in ``test_delegate.py`` and
``test_llm_tools.py``, where the tool fixtures exist.
"""

import asyncio

import pytest

from decafclaw.tools import _batch_common, delegate, llm_tools
from decafclaw.tools._batch_common import run_concurrent


class TestSharedHelperWiring:
    """Both batch tools must use the one shared engine, not local copies."""

    def test_delegate_uses_shared_run_concurrent(self):
        assert delegate.run_concurrent is _batch_common.run_concurrent

    def test_llm_query_uses_shared_run_concurrent(self):
        assert llm_tools.run_concurrent is _batch_common.run_concurrent


class TestRunConcurrent:
    @pytest.mark.asyncio
    async def test_concurrency_cap_honored(self):
        """With cap=2 and 4 items, never more than 2 workers are in-flight
        simultaneously."""
        state = {"in_flight": 0, "max_observed": 0}
        lock = asyncio.Lock()
        cap_full = asyncio.Event()

        async def worker(idx, item):
            async with lock:
                state["in_flight"] += 1
                if state["in_flight"] > state["max_observed"]:
                    state["max_observed"] = state["in_flight"]
                if state["in_flight"] >= 2:
                    cap_full.set()
            # Wait until both slots are occupied before any one releases, so a
            # buggy implementation that allowed 3+ would visibly exceed the cap.
            await cap_full.wait()
            async with lock:
                state["in_flight"] -= 1
            return {"index": idx, "ok": True, "text": f"done {item}"}

        published: list[tuple[int, int]] = []

        async def publish(done, total):
            published.append((done, total))

        results, summary = await run_concurrent(worker, ["a", "b", "c", "d"], 2, publish)

        assert state["max_observed"] == 2
        assert [r["index"] for r in results] == [0, 1, 2, 3]
        assert summary == {"total": 4, "ok": 4, "failed": 0}
        # One progress event per completion; order is scheduler-dependent.
        assert sorted(published) == [(1, 4), (2, 4), (3, 4), (4, 4)]

    @pytest.mark.asyncio
    async def test_results_ordered_by_input_index(self):
        """Items may complete out of order; results come back in input order."""
        started = [asyncio.Event() for _ in range(3)]
        released = [asyncio.Event() for _ in range(3)]
        done = [asyncio.Event() for _ in range(3)]
        completed: list[int] = []

        async def worker(idx, item):
            started[idx].set()
            if idx < 2:
                await released[idx].wait()
            completed.append(idx)
            done[idx].set()
            return {"index": idx, "ok": True, "text": f"done {item}"}

        async def publish(done_count, total):
            pass

        task = asyncio.create_task(run_concurrent(worker, ["a", "b", "c"], 3, publish))
        await asyncio.gather(*(s.wait() for s in started))
        released[1].set()
        await done[1].wait()
        released[0].set()

        results, summary = await task

        # Completion order was 2, then 1, then 0 — results are re-sorted.
        assert completed == [2, 1, 0]
        assert [r["index"] for r in results] == [0, 1, 2]
        assert [r["text"] for r in results] == ["done a", "done b", "done c"]
        assert summary == {"total": 3, "ok": 3, "failed": 0}

    @pytest.mark.asyncio
    async def test_worker_exception_becomes_failure_entry(self):
        """A worker that raises is normalized into a failure entry; siblings
        still complete and the summary counts the failure."""

        async def worker(idx, item):
            if idx == 1:
                raise RuntimeError("kaboom")
            return {"index": idx, "ok": True, "text": f"done {item}"}

        async def publish(done, total):
            pass

        results, summary = await run_concurrent(
            worker,
            ["a", "b", "c"],
            2,
            publish,
            failure_text=lambda i, exc: f"boom {i}: {exc}",
        )

        assert summary == {"total": 3, "ok": 2, "failed": 1}
        assert results[0] == {"index": 0, "ok": True, "text": "done a"}
        assert results[1] == {"index": 1, "ok": False, "error": "boom 1: kaboom"}
        assert results[2] == {"index": 2, "ok": True, "text": "done c"}

    @pytest.mark.asyncio
    async def test_default_failure_text(self):
        """Without a custom failure_text, leaked exceptions use the default wording."""

        async def worker(idx, item):
            raise ValueError("nope")

        async def publish(done, total):
            pass

        results, summary = await run_concurrent(worker, ["a"], 1, publish)

        assert results[0] == {"index": 0, "ok": False, "error": "internal error: nope"}
        assert summary == {"total": 1, "ok": 0, "failed": 1}

    @pytest.mark.asyncio
    async def test_publish_failure_is_fail_open(self):
        """A publisher that raises is swallowed; items still complete and the
        summary is unaffected."""

        async def worker(idx, item):
            return {"index": idx, "ok": True}

        calls: list[int] = []

        async def publish(done, total):
            calls.append(done)
            raise RuntimeError("bus down")

        results, summary = await run_concurrent(worker, ["a", "b"], 2, publish)

        assert summary == {"total": 2, "ok": 2, "failed": 0}
        assert sorted(calls) == [1, 2]

    @pytest.mark.asyncio
    async def test_nonpositive_concurrency_clamped_to_one(self):
        """A non-positive cap clamps to 1 instead of deadlocking (Semaphore(0))."""

        async def worker(idx, item):
            return {"index": idx, "ok": True}

        async def publish(done, total):
            pass

        results, summary = await run_concurrent(worker, ["a", "b"], 0, publish)

        assert summary == {"total": 2, "ok": 2, "failed": 0}
        assert [r["index"] for r in results] == [0, 1]
