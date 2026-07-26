from decafclaw.config_types import LoopBreakerConfig
from decafclaw.loop_breaker import LoopBreaker, LoopVerdict, fingerprint


def _lb(**kw):
    cfg = LoopBreakerConfig(**kw)
    return LoopBreaker(cfg)


def test_fingerprint_stable_and_arg_sensitive():
    assert fingerprint("edit", {"a": 1, "b": 2}) == fingerprint("edit", {"b": 2, "a": 1})
    assert fingerprint("edit", {"a": 1}) != fingerprint("edit", {"a": 2})
    assert fingerprint("edit", {"a": 1}) != fingerprint("read", {"a": 1})


def test_repeat_threshold_trips_nudge_then_stop():
    lb = _lb(repeat_threshold=3, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"path": "x"})
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NONE          # 1 occurrence
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NONE           # 2
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE           # 3 → first trip
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.STOP            # trips again after nudge


def test_error_window_trips():
    lb = _lb(repeat_threshold=99, error_threshold=4, error_window=6)
    # 3 distinct erroring calls, then a 4th → 4 errors in window
    for i in range(3):
        lb.record([(f"t{i}", fingerprint(f"t{i}", {}), True)])
        assert lb.verdict() is LoopVerdict.NONE
    lb.record([("t3", fingerprint("t3", {}), True)])
    assert lb.verdict() is LoopVerdict.NUDGE


def test_errors_outside_window_do_not_trip():
    lb = _lb(repeat_threshold=99, error_threshold=3, error_window=3)
    lb.record([("a", "fa", True)])
    lb.verdict()
    lb.record([("b", "fb", False)])
    lb.verdict()
    lb.record([("c", "fc", False)])
    lb.verdict()
    lb.record([("d", "fd", True)])  # window now [b?,c,d] errors=1 (a aged out)
    assert lb.verdict() is LoopVerdict.NONE


def test_disabled_never_trips():
    lb = _lb(enabled=False, repeat_threshold=1, error_threshold=1, error_window=1)
    lb.record([("edit", "fp", True)])
    assert lb.verdict() is LoopVerdict.NONE


def test_last_signal_describes_reason():
    lb = _lb(repeat_threshold=2, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"p": "x"})
    lb.record([("edit", fp, False)])
    lb.verdict()
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE
    assert "edit" in lb.last_signal()


def test_compliance_after_nudge_does_not_trip_again():
    """#707: the round after a NUDGE must not trip when the agent obeyed.

    The old detector tested a standing condition (count >= threshold) that
    stayed true forever once crossed, so this round always escalated to STOP
    regardless of what the agent did — the agent was never given a round in
    which changing course could pay off.
    """
    lb = _lb(repeat_threshold=3, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"path": "x"})
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NONE
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NONE
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE
    # The agent obeys: a different call, not the offending one.
    lb.record([("read", fingerprint("read", {"path": "y"}), False)])
    assert lb.verdict() is LoopVerdict.NONE


def test_error_surge_does_not_retrip_without_new_errors():
    """#707: a window still holding old errors must not re-trip on its own.

    _recent_errors is a rolling window, so after 4 errors one clean call
    leaves 4 errors still in a 6-wide window — enough to satisfy the old
    standing-condition test and stop a recovering agent.
    """
    lb = _lb(repeat_threshold=99, error_threshold=4, error_window=6)
    verdict = None
    for i in range(4):
        lb.record([(f"t{i}", fingerprint(f"t{i}", {}), True)])
        verdict = lb.verdict()
    assert verdict is LoopVerdict.NUDGE
    # The agent recovers: one clean call, no new errors.
    lb.record([("ok", fingerprint("ok", {}), False)])
    assert lb.verdict() is LoopVerdict.NONE


def test_repeating_the_same_call_after_nudge_does_escalate():
    """The reprieve is conditional: re-offending still advances the ladder."""
    lb = _lb(repeat_threshold=3, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"path": "x"})
    for _ in range(2):
        lb.record([("edit", fp, False)])
        assert lb.verdict() is LoopVerdict.NONE
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE
    lb.record([("edit", fp, False)])  # count 3 -> 4: a fresh offense
    assert lb.verdict() is LoopVerdict.STOP
