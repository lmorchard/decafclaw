"""Unit tests for IterationBudget."""

from decafclaw.iteration_budget import IterationBudget


def test_initial_state():
    b = IterationBudget(remaining=3)
    assert b.remaining == 3
    assert b._grace_used is False


def test_consume_decrements_and_returns_true_while_budget_remains():
    b = IterationBudget(remaining=2)
    assert b.consume() is True
    assert b.remaining == 1
    assert b.consume() is True
    assert b.remaining == 0


def test_consume_returns_false_when_exhausted():
    b = IterationBudget(remaining=1)
    assert b.consume() is True
    assert b.consume() is False
    assert b.remaining == 0  # no decrement past zero


def test_consume_returns_false_from_zero_initial():
    b = IterationBudget(remaining=0)
    assert b.consume() is False
    assert b.remaining == 0


def test_refund_increments_remaining():
    b = IterationBudget(remaining=1)
    b.consume()
    assert b.remaining == 0
    b.refund()
    assert b.remaining == 1


def test_refund_after_exhaustion_restores_budget():
    """Refunding after consume returned False puts the budget back above zero
    and lets a subsequent consume succeed."""
    b = IterationBudget(remaining=1)
    assert b.consume() is True
    assert b.consume() is False
    b.refund()
    assert b.consume() is True


def test_grace_turn_fires_once():
    b = IterationBudget(remaining=0)
    assert b.grace_turn() is True
    assert b.grace_turn() is False
    assert b.grace_turn() is False


def test_grace_turn_independent_of_consume_state():
    """grace_turn() can be called whether budget was used up via consume
    or started at zero — it's only gated by its own _grace_used flag."""
    b = IterationBudget(remaining=2)
    b.consume()
    b.consume()
    assert b.consume() is False
    assert b.grace_turn() is True
    assert b.grace_turn() is False
