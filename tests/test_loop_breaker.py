from decafclaw.config_types import LoopBreakerConfig
from decafclaw.loop_breaker import (
    CallSignature,
    LoopBreaker,
    LoopVerdict,
    fingerprint,
    summarize_args,
    summarize_error,
)


def _lb(**kw):
    cfg = LoopBreakerConfig(**kw)
    return LoopBreaker(cfg)


def test_fingerprint_stable_and_arg_sensitive():
    assert fingerprint("edit", {"a": 1, "b": 2}) == fingerprint("edit", {"b": 2, "a": 1})
    assert fingerprint("edit", {"a": 1}) != fingerprint("edit", {"a": 2})
    assert fingerprint("edit", {"a": 1}) != fingerprint("read", {"a": 1})


def test_error_window_trips():
    lb = _lb(repeat_threshold=99, error_threshold=4, error_window=6)
    # 3 distinct erroring calls, then a 4th → 4 errors in window
    for i in range(3):
        lb.record([CallSignature(f"t{i}", fingerprint(f"t{i}", {}), True)])
        assert lb.verdict() is LoopVerdict.NONE
    lb.record([CallSignature("t3", fingerprint("t3", {}), True)])
    assert lb.verdict() is LoopVerdict.NUDGE


def test_errors_outside_window_do_not_trip():
    lb = _lb(repeat_threshold=99, error_threshold=3, error_window=3)
    lb.record([CallSignature("a", "fa", True)])
    lb.verdict()
    lb.record([CallSignature("b", "fb", False)])
    lb.verdict()
    lb.record([CallSignature("c", "fc", False)])
    lb.verdict()
    lb.record([CallSignature("d", "fd", True)])  # window now [b?,c,d] errors=1 (a aged out)
    assert lb.verdict() is LoopVerdict.NONE


def test_disabled_never_trips():
    lb = _lb(enabled=False, repeat_threshold=1, error_threshold=1, error_window=1)
    lb.record([CallSignature("edit", "fp", True)])
    assert lb.verdict() is LoopVerdict.NONE


def test_offense_reason_describes_the_repeated_call():
    lb = _lb(repeat_threshold=2, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"p": "x"})
    lb.record([CallSignature("edit", fp, False)])
    lb.verdict()
    lb.record([CallSignature("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE
    assert "edit" in lb.offense().reason


def test_offense_carries_args_and_error_text():
    """The redirect can only name the real failure if the detector kept it."""
    lb = _lb(repeat_threshold=2, error_threshold=99, error_window=6)
    fp = fingerprint("workspace_write", {"path": "skills/foo/tools.py"})
    sig = CallSignature(
        tool_name="workspace_write",
        fingerprint=fp,
        is_error=True,
        args_text='{"path": "skills/foo/tools.py"}',
        error_text="[error: ImportError: attempted relative import]",
    )
    lb.record([sig])
    assert lb.verdict() is LoopVerdict.NONE
    lb.record([sig])
    assert lb.verdict() is LoopVerdict.NUDGE

    off = lb.offense()
    assert off.tool_name == "workspace_write"
    assert "skills/foo/tools.py" in off.args_text
    assert "ImportError" in off.error_text
    assert "workspace_write" in off.reason


def test_summarize_args_truncates_and_flattens():
    long_args = {"body": "x" * 5000}
    out = summarize_args(long_args)
    assert len(out) <= 401          # _MAX_ARG_CHARS + the ellipsis
    assert out.endswith("…")
    # Newline-free, though trivially so: json.dumps already escapes a real
    # newline to a two-character "\\n" before _truncate ever sees it. The
    # genuine newline-collapsing path is summarize_error() below.
    assert "\n" not in summarize_args({"body": "a\nb"})


def test_summarize_error_truncates_and_collapses_newlines():
    """Error bodies are raw multi-line tracebacks, and they get interpolated
    into a single-sentence prompt ("The error every time: ..."), so an
    embedded newline would visibly mangle the diagnostic."""
    assert "\n" not in summarize_error("Traceback:\n  File x\nImportError: boom")
    assert summarize_error("e" * 5000).endswith("…")
    assert len(summarize_error("e" * 5000)) <= 301  # _MAX_ERROR_CHARS + ellipsis


def test_error_surge_offense_has_no_tool_name_but_keeps_error_text():
    """An error surge has no single offending call, so tool_name is empty —
    but the most recent error body is still worth naming."""
    lb = _lb(repeat_threshold=99, error_threshold=2, error_window=6)
    lb.record([CallSignature("a", "fa", True, "{}", "[error: boom A]")])
    assert lb.verdict() is LoopVerdict.NONE
    lb.record([CallSignature("b", "fb", True, "{}", "[error: boom B]")])
    assert lb.verdict() is LoopVerdict.NUDGE
    off = lb.offense()
    assert off.tool_name == ""
    assert "boom B" in off.error_text


def test_compliance_after_nudge_does_not_trip_again():
    """#707: the round after a NUDGE must not trip when the agent obeyed.

    The old detector tested a standing condition (count >= threshold) that
    stayed true forever once crossed, so this round always escalated to STOP
    regardless of what the agent did — the agent was never given a round in
    which changing course could pay off.
    """
    lb = _lb(repeat_threshold=3, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"path": "x"})
    lb.record([CallSignature("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NONE
    lb.record([CallSignature("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NONE
    lb.record([CallSignature("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE
    # The agent obeys: a different call, not the offending one.
    lb.record([CallSignature("read", fingerprint("read", {"path": "y"}), False)])
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
        lb.record([CallSignature(f"t{i}", fingerprint(f"t{i}", {}), True)])
        verdict = lb.verdict()
    assert verdict is LoopVerdict.NUDGE
    # The agent recovers: one clean call, no new errors.
    lb.record([CallSignature("ok", fingerprint("ok", {}), False)])
    assert lb.verdict() is LoopVerdict.NONE


def test_repeating_the_same_call_after_nudge_advances_a_rung():
    """The reprieve is conditional: re-offending still advances the ladder."""
    lb = _lb(repeat_threshold=3, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"path": "x"})
    for _ in range(2):
        lb.record([CallSignature("edit", fp, False)])
        assert lb.verdict() is LoopVerdict.NONE
    lb.record([CallSignature("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE
    lb.record([CallSignature("edit", fp, False)])  # count 3 -> 4: a fresh offense
    assert lb.verdict() is LoopVerdict.REDIRECT


def test_compliance_after_a_multi_call_batch_does_not_trip_again():
    """#707 review: EVERY fresh offender's watermark must advance, not just the
    worst one's.

    Tool calls run concurrently (asyncio.gather), so a repeated multi-call
    batch is the canonical thrash shape — and it pushes several fingerprints
    over threshold in the same round. Advancing only the worst one's watermark
    left the others permanently "fresh", so they re-tripped on later rounds
    with counts that had stopped growing three rounds earlier: a fully
    compliant agent got walked REDIRECT -> STOP anyway, quoting stale counts.
    Every other repeat test records exactly ONE signature per round, which is
    why this survived.
    """
    lb = _lb(repeat_threshold=3, error_threshold=99, error_window=50)
    batch = [
        CallSignature("shell", fingerprint("shell", {"c": "make test"}), False),
        CallSignature("read", fingerprint("read", {"p": "a.py"}), False),
        CallSignature("edit", fingerprint("edit", {"p": "b.py"}), False),
    ]
    verdict = None
    for _ in range(3):
        lb.record(batch)
        verdict = lb.verdict()
    assert verdict is LoopVerdict.NUDGE
    # The agent obeys completely: brand-new, non-repeating, non-erroring calls.
    for i in range(3):
        lb.record([CallSignature(f"new{i}", fingerprint(f"new{i}", {"n": i}), False)])
        assert lb.verdict() is LoopVerdict.NONE, (
            "a compliant round escalated off a co-offender's stale count"
        )


def test_compliant_but_erroring_round_after_a_nudge_does_not_escalate():
    """#707 review: the error signal must not enter its first round loaded.

    The redirect *instructs* the model to "take exactly one read-only action",
    and in the broken environments where the breaker fires that diagnostic read
    frequently errors. Requiring only "any new error" on top of a window still
    full of pre-trip failures meant the mechanism escalated on the very
    behavior it had just demanded.
    """
    lb = _lb(repeat_threshold=3, error_threshold=4, error_window=6)
    fp = fingerprint("edit", {"p": "b.py"})
    verdict = None
    for _ in range(3):
        lb.record([CallSignature("edit", fp, True, '{"p": "b.py"}', "[error: boom]")])
        verdict = lb.verdict()
    assert verdict is LoopVerdict.NUDGE
    # The agent obeys: distinct read-only calls — which happen to error.
    for i in range(3):
        lb.record([CallSignature(
            f"read{i}", fingerprint("read", {"p": f"log{i}"}), True,
            "{}", "[error: no such file]")])
        assert lb.verdict() is LoopVerdict.NONE, (
            "the ladder punished the diagnostic read it asked for"
        )


def test_a_genuine_new_error_surge_after_a_nudge_still_escalates():
    """The counterpart to the test above: fixing the false positive must not
    disable the signal. A full fresh threshold of errors still advances."""
    lb = _lb(repeat_threshold=99, error_threshold=3, error_window=6)
    verdict = None
    for i in range(3):
        lb.record([CallSignature(f"a{i}", fingerprint(f"a{i}", {}), True)])
        verdict = lb.verdict()
    assert verdict is LoopVerdict.NUDGE
    for i in range(3):
        lb.record([CallSignature(f"b{i}", fingerprint(f"b{i}", {}), True)])
        verdict = lb.verdict()
    assert verdict is LoopVerdict.REDIRECT


def test_error_text_clears_when_the_call_starts_succeeding():
    """The nudge says "the failure each time" — so a fingerprint that failed
    early and then succeeded must not keep quoting the stale error. Repeated
    *successful* identical calls trip the repeat signal too, so this is
    reachable."""
    lb = _lb(repeat_threshold=3, error_threshold=99, error_window=50)
    fp = fingerprint("read", {"p": "x"})
    lb.record([CallSignature("read", fp, True, "{}", "[error: transient]")])
    assert lb.verdict() is LoopVerdict.NONE
    for _ in range(2):
        lb.record([CallSignature("read", fp, False, "{}", "")])
        verdict = lb.verdict()
    assert verdict is LoopVerdict.NUDGE
    assert lb.offense().error_text == ""


def test_ladder_is_nudge_then_redirect_then_stop():
    """Three rungs: reaching STOP now requires re-offending twice after being
    told twice, not merely one elapsed round (#707)."""
    lb = _lb(repeat_threshold=3, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"path": "x"})
    for _ in range(2):
        lb.record([CallSignature("edit", fp, False)])
        assert lb.verdict() is LoopVerdict.NONE
    lb.record([CallSignature("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE
    lb.record([CallSignature("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.REDIRECT
    lb.record([CallSignature("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.STOP
    lb.record([CallSignature("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.STOP
