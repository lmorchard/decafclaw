"""Tests for tool-choice eval scoring + report formatters (#303)."""

from __future__ import annotations

from decafclaw.eval.tool_choice.case import Case
from decafclaw.eval.tool_choice.report import (
    compute_confusion_matrix,
    compute_pair_overlap,
    format_case_lines,
    format_confusion_matrix,
    format_pair_overlap,
    format_summary,
)
from decafclaw.eval.tool_choice.runner import NO_TOOL, CaseResult


def _result(name, expected, near_miss, picked):
    """Compact CaseResult constructor."""
    case = Case(name=name, scenario="x", expected=expected, near_miss=list(near_miss))
    return CaseResult(
        case=case,
        model="m",
        picked=picked,
        all_picks=[picked] if picked != NO_TOOL else [],
        passed=(picked == expected),
    )


# -- Per-case lines + summary --------------------------------------------------


class TestFormatCaseLines:
    def test_pass_line(self):
        results = [_result("vault-vs-conv", "vault_search", ["conv_search"], "vault_search")]
        assert format_case_lines(results) == ["PASS  vault-vs-conv"]

    def test_fail_line(self):
        results = [_result("vault-vs-conv", "vault_search", ["conv_search"], "conv_search")]
        assert format_case_lines(results) == [
            "FAIL  vault-vs-conv    picked conv_search; expected vault_search",
        ]


class TestFormatSummary:
    def test_all_passed(self):
        results = [
            _result("a", "t", ["u"], "t"),
            _result("b", "t", ["u"], "t"),
        ]
        assert format_summary(results) == "Summary: 2/2 passed (100%)"

    def test_partial(self):
        results = [
            _result("a", "t", ["u"], "t"),
            _result("b", "t", ["u"], "u"),
            _result("c", "t", ["u"], "u"),
        ]
        assert format_summary(results) == "Summary: 1/3 passed (33%)"

    def test_empty(self):
        assert format_summary([]) == "Summary: 0/0 passed"


# -- Pair overlap --------------------------------------------------------------


class TestComputePairOverlap:
    def test_one_pair_50_pct(self):
        results = [
            _result("a", "vault_search", ["conv_search"], "vault_search"),
            _result("b", "vault_search", ["conv_search"], "conv_search"),
        ]
        rows = compute_pair_overlap(results)
        assert len(rows) == 1
        r = rows[0]
        assert r.expected == "vault_search"
        assert r.near_miss == "conv_search"
        assert r.swapped == 1
        assert r.total == 2
        assert r.pct == 50.0

    def test_no_swap_zero_pct(self):
        results = [
            _result("a", "vault_search", ["conv_search"], "vault_search"),
            _result("b", "vault_search", ["conv_search"], "vault_search"),
        ]
        rows = compute_pair_overlap(results)
        assert rows[0].swapped == 0
        assert rows[0].pct == 0.0

    def test_multiple_near_miss_one_case_two_rows(self):
        """One case with two near_miss tools yields two pair rows."""
        results = [
            _result("a", "vault_search", ["conv_search", "vault_read"], "vault_search"),
        ]
        rows = compute_pair_overlap(results)
        pairs = {(r.expected, r.near_miss) for r in rows}
        assert pairs == {
            ("vault_search", "conv_search"),
            ("vault_search", "vault_read"),
        }

    def test_sorted_by_overlap_desc(self):
        """Higher swap pct ranks first; ties broken alphabetically."""
        results = [
            # Pair A↔B: 1/1 swapped (100%)
            _result("a", "A", ["B"], "B"),
            # Pair C↔D: 0/2 swapped (0%)
            _result("c1", "C", ["D"], "C"),
            _result("c2", "C", ["D"], "C"),
            # Pair E↔F: 1/2 swapped (50%)
            _result("e1", "E", ["F"], "E"),
            _result("e2", "E", ["F"], "F"),
        ]
        rows = compute_pair_overlap(results)
        ordered = [(r.expected, r.near_miss, r.pct) for r in rows]
        assert ordered == [
            ("A", "B", 100.0),
            ("E", "F", 50.0),
            ("C", "D", 0.0),
        ]


class TestFormatPairOverlap:
    def test_emits_table_with_tighten_marker(self):
        rows = compute_pair_overlap([
            _result("a", "vault_search", ["conv_search"], "conv_search"),
            _result("b", "vault_search", ["conv_search"], "conv_search"),
        ])
        out = format_pair_overlap(rows)
        joined = "\n".join(out)
        assert "Pair overlap" in joined
        assert "vault_search" in joined and "conv_search" in joined
        assert "2/2" in joined
        assert "(100%)" in joined
        assert "tighten" in joined

    def test_no_tighten_when_under_50(self):
        rows = compute_pair_overlap([
            _result("a", "X", ["Y"], "X"),
            _result("b", "X", ["Y"], "Y"),
            _result("c", "X", ["Y"], "X"),
        ])
        out = format_pair_overlap(rows)
        joined = "\n".join(out)
        assert "tighten" not in joined  # 1/3 = 33% < 50%

    def test_empty_rows(self):
        out = format_pair_overlap([])
        assert any("(no pairs)" in line for line in out)


# -- Confusion matrix ----------------------------------------------------------


class TestComputeConfusionMatrix:
    def test_aggregates_picks(self):
        results = [
            _result("a", "vault_search", ["x"], "vault_search"),
            _result("b", "vault_search", ["x"], "conv_search"),
            _result("c", "vault_search", ["x"], "conv_search"),
            _result("d", "workspace_read", ["y"], "vault_read"),
        ]
        matrix = compute_confusion_matrix(results)
        assert matrix == {
            "vault_search": {"vault_search": 1, "conv_search": 2},
            "workspace_read": {"vault_read": 1},
        }


class TestFormatConfusionMatrix:
    def test_off_diagonal_only(self):
        matrix = {
            "vault_search": {"vault_search": 1, "conv_search": 2},
            "workspace_read": {"workspace_read": 3},  # no off-diagonal — should be hidden
            "vault_read": {"workspace_read": 1, "vault_read": 1},
        }
        out = format_confusion_matrix(matrix)
        joined = "\n".join(out)
        # vault_search has off-diagonal: included
        assert "vault_search:" in joined
        assert "conv_search" in joined
        # workspace_read has only on-diagonal: excluded
        assert "workspace_read:" not in joined.split("vault_read")[0]
        # vault_read has off-diagonal: included
        assert "vault_read:" in joined

    def test_no_off_diagonal_at_all(self):
        matrix = {"vault_search": {"vault_search": 5}}
        out = format_confusion_matrix(matrix)
        joined = "\n".join(out)
        assert "no off-diagonal" in joined

    def test_empty_matrix(self):
        out = format_confusion_matrix({})
        assert any("empty" in line for line in out)
