"""Unit tests for `decafclaw.eval.inventory` (#1834).

The inventory is derived at read time — these tests pin the derivations
(parsed YAML, history join, git date) rather than a checked-in artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from decafclaw.eval.inventory import (
    DASH,
    build_inventory,
    git_last_touched,
    render_json,
    render_table,
)


def _case(name: str, **extra) -> dict:
    case = {"name": name, "input": "x", "expect": {"response_contains": "x"}}
    case.update(extra)
    return case


def _write_suite(root: Path, rel: str, cases: list[dict]) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cases), encoding="utf-8")
    return path


def _build(root: Path, records: list[dict] | None = None, touched: str = "2026-01-02"):
    history = root / "history.jsonl"
    if records is not None:
        history.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return build_inventory(
        root,
        history_path=history,
        last_touched=lambda _path: touched,
    )


def test_derives_tiers_and_case_counts_from_parsed_yaml(tmp_path: Path):
    _write_suite(tmp_path, "tool_choice/overlaps.yaml", [_case("c1"), _case("c2")])
    _write_suite(tmp_path, "dream.yaml", [_case("only")])
    _write_suite(tmp_path, "memory.yaml", [_case("m1"), _case("m2"), _case("m3")])

    suites = _build(tmp_path)
    by_file = {s.file: s for s in suites}

    assert by_file["tool_choice/overlaps.yaml"].tier == "choice"
    assert by_file["dream.yaml"].tier == "smoke"
    assert by_file["memory.yaml"].tier == "behavioral"
    assert by_file["memory.yaml"].cases == 3
    assert [s.file for s in suites] == sorted(by_file)


def test_axis_tags_come_from_parsed_yaml_not_raw_text(tmp_path: Path):
    """A comment or a case *name* mentioning ``tests:`` must not be counted."""
    path = tmp_path / "suite.yaml"
    path.write_text(
        "# tests: routing (a comment, not a tag)\n"
        '- name: "mentions tests: routing in its own name"\n'
        "  input: x\n"
        "  tests: [answer_quality, retrieval]\n"
        "- name: second\n"
        "  input: y\n"
        "  tests: answer_quality\n",
        encoding="utf-8",
    )

    suite = _build(tmp_path)[0]

    assert suite.axes == ("answer_quality", "retrieval")
    assert suite.untagged is False


def test_untagged_suite_is_called_out_explicitly(tmp_path: Path):
    _write_suite(tmp_path, "dream.yaml", [_case("only")])

    suite = _build(tmp_path)[0]

    assert suite.axes == ()
    assert suite.untagged is True
    assert "untagged" in render_table([suite])


def test_model_gated_counts_only_model_and_provider_overrides(tmp_path: Path):
    _write_suite(
        tmp_path,
        "suite.yaml",
        [
            _case("plain"),
            _case("reflection", setup={"config_overrides": {"reflection.enabled": False}}),
            _case("model", setup={"config_overrides": {"llm.model": "other"}}),
            _case("provider", setup={"config_overrides": {"providers.vertex.project": "x"}}),
            _case("model-config", setup={"config_overrides": {"model_configs.fast": {}}}),
            _case("default", setup={"config_overrides": {"default_model": "fast"}}),
        ],
    )

    suite = _build(tmp_path)[0]

    assert suite.model_gated == 4


def test_history_columns_use_latest_matching_record(tmp_path: Path):
    _write_suite(tmp_path, "memory.yaml", [_case("m1"), _case("m2")])
    _write_suite(tmp_path, "dream.yaml", [_case("d1")])
    records = [
        {"timestamp": "2026-01-01-0000", "per_file": {"memory.yaml": {"passed": 1, "total": 2}}},
        {"timestamp": "2026-02-02-0000", "per_file": {"memory.yaml": {"passed": 2, "total": 2}}},
    ]

    by_file = {s.file: s for s in _build(tmp_path, records)}

    assert by_file["memory.yaml"].last_run == "2026-02-02-0000"
    assert by_file["memory.yaml"].pass_rate == 1.0
    assert by_file["dream.yaml"].last_run is None
    assert by_file["dream.yaml"].pass_rate is None


def test_table_shows_dashes_for_never_run_suites(tmp_path: Path):
    _write_suite(tmp_path, "dream.yaml", [_case("d1")])
    _write_suite(tmp_path, "memory.yaml", [_case("m1")])
    records = [
        {"timestamp": "2026-02-02-0000", "per_file": {"memory.yaml": {"passed": 1, "total": 1}}},
    ]

    out = render_table(_build(tmp_path, records))

    for header in ("File", "Tier", "Cases", "Axes", "Model-gated", "Last touched", "Last run", "Pass rate"):
        assert header in out
    assert "untagged" in out
    assert DASH in out
    assert "100.0%" in out
    assert "2 suites, 2 cases" in out


def test_json_output_is_stable_and_machine_readable(tmp_path: Path):
    _write_suite(tmp_path, "b.yaml", [_case("b1"), _case("b2")])
    _write_suite(tmp_path, "a.yaml", [_case("a1", tests="routing")])
    records = [
        {"timestamp": "2026-02-02-0000", "per_file": {"b.yaml": {"passed": 1, "total": 2}}},
    ]

    first = render_json(_build(tmp_path, records))
    second = render_json(_build(tmp_path, records))
    payload = json.loads(first)

    assert first == second
    assert [s["file"] for s in payload["suites"]] == ["a.yaml", "b.yaml"]
    assert payload["totals"] == {"suites": 2, "cases": 3}
    b_suite = payload["suites"][1]
    assert b_suite == {
        "file": "b.yaml",
        "tier": "behavioral",
        "cases": 2,
        "axes": [],
        "untagged": True,
        "model_gated": 0,
        "last_touched": "2026-01-02",
        "last_run": "2026-02-02-0000",
        "pass_rate": 0.5,
    }


def test_single_file_path_is_accepted(tmp_path: Path):
    path = _write_suite(tmp_path, "one.yaml", [_case("only")])

    suites = build_inventory(
        path,
        history_path=tmp_path / "history.jsonl",
        last_touched=lambda _path: "2026-01-02",
    )

    assert [(s.file, s.tier, s.cases) for s in suites] == [("one.yaml", "smoke", 1)]


def test_non_list_yaml_raises(tmp_path: Path):
    (tmp_path / "bad.yaml").write_text("name: not-a-list\n", encoding="utf-8")

    with pytest.raises(ValueError, match="top-level"):
        _build(tmp_path)


def test_git_last_touched_degrades_to_dash_outside_a_repo(tmp_path: Path):
    assert git_last_touched(tmp_path / "absent.yaml") == DASH


def test_cli_inventory_prints_table(tmp_path: Path, monkeypatch, capsys):
    from decafclaw.eval import __main__ as cli

    _write_suite(tmp_path, "evals/dream.yaml", [_case("only")])
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc:
        cli.main(["--inventory", "evals"])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "File" in out
    assert "dream.yaml" in out


def test_cli_inventory_json_is_parseable(tmp_path: Path, monkeypatch, capsys):
    from decafclaw.eval import __main__ as cli

    _write_suite(tmp_path, "evals/memory.yaml", [_case("m1"), _case("m2")])
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc:
        cli.main(["--inventory", "--json", "evals"])

    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"] == {"suites": 1, "cases": 2}
