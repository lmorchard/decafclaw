"""Unit tests for `decafclaw.eval.history`."""

import json
from pathlib import Path

from decafclaw.eval.history import (
    append_run,
    build_run_record,
    read_history,
    render_table,
)


def test_read_history_empty(tmp_path: Path):
    assert read_history(tmp_path / "missing.jsonl") == []


def test_append_then_read_roundtrips(tmp_path: Path):
    path = tmp_path / "history.jsonl"
    rec1 = {"timestamp": "2026-05-16-1130", "model": "m1", "passed": 10, "total": 12}
    rec2 = {"timestamp": "2026-05-16-1256", "model": "m1", "passed": 11, "total": 12}
    append_run(rec1, path)
    append_run(rec2, path)
    out = read_history(path)
    assert out == [rec1, rec2]


def test_append_creates_parent_directory(tmp_path: Path):
    path = tmp_path / "deeply" / "nested" / "history.jsonl"
    append_run({"timestamp": "x", "model": "y"}, path)
    assert path.exists()


def test_read_history_skips_corrupt_lines(tmp_path: Path):
    path = tmp_path / "history.jsonl"
    path.write_text(
        json.dumps({"a": 1}) + "\n"
        "not valid json\n"
        + json.dumps({"a": 2}) + "\n"
    )
    out = read_history(path)
    assert out == [{"a": 1}, {"a": 2}]


def _make_yaml(path: Path, names: list[str]) -> None:
    """Write a YAML eval file with N synthetic test cases."""
    import yaml
    cases = [{"name": n, "input": "x", "expect": {"response_contains": "x"}}
             for n in names]
    path.write_text(yaml.safe_dump(cases))


def test_build_run_record_aggregates_per_file(tmp_path: Path):
    f1 = tmp_path / "vault.yaml"
    f2 = tmp_path / "shell.yaml"
    _make_yaml(f1, ["v1", "v2", "v3"])
    _make_yaml(f2, ["s1", "s2"])

    cases = [{"name": n} for n in ("v1", "v2", "v3", "s1", "s2")]
    test_results = [
        {"name": "v1", "status": "pass"},
        {"name": "v2", "status": "fail"},
        {"name": "v3", "status": "pass"},
        {"name": "s1", "status": "pass"},
        {"name": "s2", "status": "pass"},
    ]

    record = build_run_record(
        timestamp="2026-05-16-1300",
        model="vertex-gemini-flash",
        judge_model="vertex-gemini-flash",
        sources=[str(f1), str(f2)],
        test_results=test_results,
        cases=cases,
        duration_sec=42.5,
        total_tokens=12345,
    )

    assert record["timestamp"] == "2026-05-16-1300"
    assert record["model"] == "vertex-gemini-flash"
    assert record["total"] == 5
    assert record["passed"] == 4
    assert record["failed"] == 1
    assert record["pass_rate"] == 0.8
    assert record["duration_sec"] == 42.5
    assert record["total_tokens"] == 12345
    assert record["per_file"] == {
        "vault.yaml": {"passed": 2, "total": 3},
        "shell.yaml": {"passed": 2, "total": 2},
    }


def test_build_run_record_handles_string_sources(tmp_path: Path):
    """``run_eval`` stores sources as a comma-joined string by the time we
    see it; the helper should accept both shapes."""
    f1 = tmp_path / "a.yaml"
    _make_yaml(f1, ["a1", "a2"])
    record = build_run_record(
        timestamp="x",
        model="m",
        judge_model="m",
        sources=str(f1),  # bare string, not list
        test_results=[
            {"name": "a1", "status": "pass"},
            {"name": "a2", "status": "fail"},
        ],
        cases=[{"name": "a1"}, {"name": "a2"}],
        duration_sec=1.0,
        total_tokens=100,
    )
    assert record["per_file"] == {"a.yaml": {"passed": 1, "total": 2}}


def test_build_run_record_handles_zero_total():
    record = build_run_record(
        timestamp="t",
        model="m",
        judge_model="m",
        sources=[],
        test_results=[],
        cases=[],
        duration_sec=0.0,
        total_tokens=0,
    )
    assert record["total"] == 0
    assert record["pass_rate"] == 0.0


def test_render_table_empty():
    s = render_table([])
    assert "No history" in s


def test_render_table_single_record_marks_delta_as_dashes():
    out = render_table([
        {"timestamp": "2026-05-16-1300", "model": "m1",
         "passed": 10, "total": 12, "pass_rate": 0.833,
         "duration_sec": 30, "total_tokens": 5000},
    ])
    assert "2026-05-16-1300" in out
    assert "m1" in out
    assert "10 /" in out
    assert "83.3%" in out
    assert "--" in out


def test_render_table_computes_delta_for_subsequent_rows():
    out = render_table([
        {"timestamp": "t1", "model": "m", "passed": 8, "total": 10,
         "pass_rate": 0.80, "duration_sec": 30, "total_tokens": 5000},
        {"timestamp": "t2", "model": "m", "passed": 9, "total": 10,
         "pass_rate": 0.90, "duration_sec": 30, "total_tokens": 5000},
    ])
    # Second row should have a positive delta
    assert "+10" in out  # "+10.0%"


def test_render_table_respects_limit():
    records = [
        {"timestamp": f"t{i}", "model": "m", "passed": 1, "total": 1,
         "pass_rate": 1.0, "duration_sec": 1, "total_tokens": 1}
        for i in range(10)
    ]
    out = render_table(records, limit=3)
    # Only the LAST 3 records render
    for i in (7, 8, 9):
        assert f"t{i}" in out
    for i in (0, 1, 2, 3):
        assert f"t{i}" not in out


def test_render_table_formats_large_token_counts():
    out = render_table([
        {"timestamp": "t", "model": "m", "passed": 1, "total": 1,
         "pass_rate": 1.0, "duration_sec": 1, "total_tokens": 1_234_567},
    ])
    assert "1.23M" in out
