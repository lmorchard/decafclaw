"""Naive-local timestamp writers that bypass archive.now_iso (#2026).

#1837 normalized archive stamping and the readers that compared timestamps as
strings. It missed writers that supply their own `timestamp` before the row
reaches `append_message`, which only stamps rows that arrive without one:

    if "timestamp" not in message:
        message = {**message, "timestamp": now_iso()}

`ConfirmationResponse` was the dominant offender — every approval the user
clicked wrote a naive local row, so the archive re-dirtied itself immediately
after migration. These tests pin the writer fix and guard against the class
of bug returning.
"""

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from decafclaw.archive import parse_timestamp
from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest, ConfirmationResponse

SRC = Path(__file__).resolve().parent.parent / "src" / "decafclaw"


def _assert_aware_utc(value: str):
    parsed = parse_timestamp(value)
    assert parsed is not None, f"unparseable timestamp: {value!r}"
    assert parsed.utcoffset() == timedelta(0), f"not UTC: {value!r}"
    # Reject the naive local shape outright: it must carry an explicit offset.
    assert value.endswith(("+00:00", "Z")), f"naive local timestamp: {value!r}"


# -- ConfirmationResponse ----------------------------------------------------


def test_confirmation_response_timestamp_is_aware_utc():
    """The row written on every approval carries an offset (#2026)."""
    resp = ConfirmationResponse(confirmation_id="abc123", approved=True)
    _assert_aware_utc(resp.timestamp)


def test_confirmation_response_archive_message_is_aware_utc():
    """to_archive_message is what lands in the archive; check that boundary."""
    resp = ConfirmationResponse(confirmation_id="abc123", approved=True)
    msg = resp.to_archive_message()
    assert msg["role"] == "confirmation_response"
    _assert_aware_utc(msg["timestamp"])


def test_confirmation_request_timestamp_is_aware_utc():
    req = ConfirmationRequest(action_type=ConfirmationAction.RUN_SHELL_COMMAND)
    _assert_aware_utc(req.timestamp)


# -- confirmation staleness comparison ---------------------------------------


def test_staleness_comparison_keeps_a_fresh_request():
    """A request stamped moments ago must not read as stale (#2026).

    Both sides are parsed instants here; the old code compared an aware-UTC
    string against a naive-local cutoff, which is wrong by the host offset.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=2)
    fresh = (now - timedelta(minutes=1)).isoformat()

    ts_parsed = parse_timestamp(fresh)
    assert ts_parsed is not None
    assert ts_parsed >= cutoff


def test_staleness_comparison_drops_a_stale_request():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=2)
    old = (now - timedelta(hours=3)).isoformat()

    ts_parsed = parse_timestamp(old)
    assert ts_parsed is not None
    assert ts_parsed < cutoff


# -- structural guard --------------------------------------------------------


def _naive_now_timestamp_uses(path: Path):
    """Yield line numbers where `datetime.now()` is used as a timestamp.

    Looks for the two shapes that produce a naive local timestamp:
    `datetime.now().isoformat()` and `datetime.now().strftime(...)` used as a
    persisted timestamp field. Await/compare uses are allowed through.
    """
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # datetime.now(...)
        if not (isinstance(func, ast.Attribute) and func.attr == "now"):
            continue
        # A tz argument means it is aware — fine.
        if node.args or node.keywords:
            continue
        # Only flag when the result is immediately rendered to a string.
        parent_ok = False
        for outer in ast.walk(tree):
            if isinstance(outer, ast.Attribute) and isinstance(outer.value, ast.Call):
                if outer.value is node and outer.attr in ("isoformat", "strftime"):
                    parent_ok = True
                    break
        if parent_ok:
            yield node.lineno


@pytest.mark.parametrize(
    "module",
    [
        "confirmations.py",
        "archive.py",
    ],
)
def test_no_naive_timestamp_writers_in_archive_modules(module):
    """Guard: modules that write archive rows must not stamp naive local time."""
    offenders = sorted(_naive_now_timestamp_uses(SRC / module))
    assert offenders == [], f"{module} has naive datetime.now() timestamp use at lines {offenders}"
