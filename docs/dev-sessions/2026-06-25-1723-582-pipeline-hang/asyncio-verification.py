"""Verify the asyncio semantics the #582 fix depends on.

If any of these assertions fail, the fix shape in spec.md is wrong and
we need to revisit before touching production code.
"""
import asyncio


async def quick(i):
    await asyncio.sleep(0.01)
    return i


async def boom():
    await asyncio.sleep(0.01)
    raise ValueError("boom")


async def never_fires():
    ev = asyncio.Event()
    await ev.wait()
    raise RuntimeError("unreachable")


async def main():
    # (1) gather(return_exceptions=True) returns BaseException in place of failures.
    tasks = [
        asyncio.create_task(quick(0)),
        asyncio.create_task(boom()),
        asyncio.create_task(quick(2)),
    ]
    result = await asyncio.gather(*tasks, return_exceptions=True)
    assert result[0] == 0, f"got {result[0]!r}"
    assert isinstance(result[1], ValueError), f"got {result[1]!r}"
    assert result[2] == 2, f"got {result[2]!r}"
    print("(1) gather return_exceptions: OK")

    # (2) Cancelling gather_future cancels all inner tasks.
    # NOTE: asyncio.gather() returns a _GatheringFuture, NOT a coroutine.
    # Python 3.11+ rejects asyncio.create_task(gather(...)). Use the gather
    # return value directly as the Future — same shape used in production.
    inner_tasks = [
        asyncio.create_task(asyncio.sleep(10, result=i)) for i in range(3)
    ]
    gather_future = asyncio.gather(*inner_tasks, return_exceptions=True)
    await asyncio.sleep(0)  # let everything start
    gather_future.cancel()
    try:
        await gather_future
    except asyncio.CancelledError:
        pass
    for i, t in enumerate(inner_tasks):
        assert t.cancelled(), f"inner task {i} not cancelled: done={t.done()}"
    print("(2) gather.cancel propagates: OK")

    # (3) FIRST_COMPLETED race: returns when gather completes, watcher pending.
    real_tasks = [asyncio.create_task(quick(i)) for i in range(3)]
    gather_future = asyncio.gather(*real_tasks, return_exceptions=True)
    watcher = asyncio.create_task(never_fires())
    try:
        done, pending = await asyncio.wait_for(
            asyncio.wait([gather_future, watcher],
                         return_when=asyncio.FIRST_COMPLETED),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        raise AssertionError("FIRST_COMPLETED race hung — fix shape is wrong")
    assert gather_future in done, "gather should be done"
    assert watcher in pending, "watcher should be pending"
    print("(3) FIRST_COMPLETED race: OK")
    watcher.cancel()
    try:
        await watcher
    except asyncio.CancelledError:
        pass

    # (4) gather_future.result() returns list on normal completion.
    assert gather_future.result() == [0, 1, 2]
    print("(4) gather_future.result() returns list: OK")


if __name__ == "__main__":
    asyncio.run(main())
    print("\nAll assertions passed. Fix shape from spec.md is sound.")
