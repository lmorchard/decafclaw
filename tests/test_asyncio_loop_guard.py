"""The autouse guard that fails tests leaking asyncio's running-loop mark (#947).

A deliberately leaking test must fail with a message that names it, and the
leak must be cleared so later tests on the worker are unaffected. The leak
here is wrapped so it expects the failure: the guard's check runs inside
``pytest.raises`` rather than at this test's own teardown.
"""

import asyncio
import re
from types import SimpleNamespace

import pytest
from _pytest.outcomes import Failed

from tests.conftest import check_asyncio_loop_leak

NODEID = "tests/test_probe.py::test_deliberately_leaks_loop"


def _request(nodeid: str):
    return SimpleNamespace(node=SimpleNamespace(nodeid=nodeid))


def test_guard_fails_leaking_test_and_names_it():
    request = _request(NODEID)
    loop = asyncio.new_event_loop()
    asyncio._set_running_loop(loop)
    try:
        with pytest.raises(Failed, match=re.escape(NODEID)):
            check_asyncio_loop_leak(request)
    finally:
        asyncio._set_running_loop(None)
        loop.close()


def test_guard_clears_leaked_mark_before_failing():
    request = _request(NODEID)
    loop = asyncio.new_event_loop()
    asyncio._set_running_loop(loop)
    try:
        with pytest.raises(Failed):
            check_asyncio_loop_leak(request)
        # Assert the mark state after the raises block returns and before the
        # test-side cleanup below: this is the clear-before-fail property.
        assert asyncio._get_running_loop() is None
    finally:
        asyncio._set_running_loop(None)
        loop.close()


def test_guard_passes_clean_test():
    check_asyncio_loop_leak(_request("tests/test_probe.py::test_clean"))
    assert asyncio._get_running_loop() is None
