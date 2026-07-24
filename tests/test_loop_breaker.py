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
