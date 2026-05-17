"""Per-run summary history for the eval runner — append-only JSONL committed
to git so pass-rate trends are visible over time without re-running.

The detail bundles (results.json + reflections/) stay in the gitignored
``evals/results/`` directory; only the summary line goes here. One record
per `make eval` invocation. If a future matrix runner runs N models in one
invocation, write N records (one per model)."""

import json
from collections import Counter
from pathlib import Path

HISTORY_PATH = Path("evals/history.jsonl")


def build_run_record(
    *,
    timestamp: str,
    model: str,
    judge_model: str,
    sources: list[str] | str,
    test_results: list[dict],
    cases: list[dict],
    duration_sec: float,
    total_tokens: int,
) -> dict:
    """Build a single history record from the pieces ``run_eval`` has on hand.

    Per-file pass/total counts come from the case file paths each test was
    loaded from, which the runner already tracks in `sources`. Passing the
    parallel ``cases`` list lets us re-derive the (file, name) ownership;
    `sources` alone is just a flat list of source file paths.
    """
    per_file: dict[str, dict[str, int]] = {}
    # `cases` and `test_results` are parallel; map case index → its source
    # by replaying the same flattening order. The runner appends files in
    # alphabetical order, so per-file ownership can be reconstructed by
    # walking the cases and bumping the right counter.
    if isinstance(sources, str):
        source_list = [s.strip() for s in sources.split(",")]
    else:
        source_list = list(sources)
    # If we have the same number of source files as cases (rare; means each
    # file had exactly one case), the mapping is trivial. The common case
    # is N files → M cases. We instead trust the in-runner record format
    # where `cases` carries no file info; fall back to a flat per-case
    # mapping by reading the YAML again (cheap).
    case_to_file: dict[int, str] = {}
    flat_index = 0
    for source_path in source_list:
        p = Path(source_path)
        if not p.exists():
            continue
        try:
            import yaml
            with p.open() as f:
                file_cases = yaml.safe_load(f) or []
        except Exception:
            continue
        for _ in file_cases:
            if flat_index < len(cases):
                case_to_file[flat_index] = p.name
                flat_index += 1
    counters: dict[str, Counter] = {}
    for i, result in enumerate(test_results):
        file_key = case_to_file.get(i, "<unknown>")
        c = counters.setdefault(file_key, Counter())
        c["total"] += 1
        if result.get("status") == "pass":
            c["passed"] += 1
    per_file = {k: {"passed": v["passed"], "total": v["total"]}
                for k, v in counters.items()}

    passed = sum(1 for r in test_results if r.get("status") == "pass")
    total = len(test_results)

    return {
        "timestamp": timestamp,
        "model": model,
        "judge_model": judge_model,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "duration_sec": round(duration_sec, 1),
        "total_tokens": int(total_tokens),
        "per_file": per_file,
    }


def append_run(record: dict, path: Path = HISTORY_PATH) -> None:
    """Append one record as a JSONL line to ``evals/history.jsonl``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def read_history(path: Path = HISTORY_PATH) -> list[dict]:
    """Read all records, oldest-first. Corrupt lines are skipped silently."""
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def render_table(records: list[dict], limit: int = 20) -> str:
    """Render the last ``limit`` records as a text table with delta-vs-prev pass rate.

    Output shape:

    ::

      Timestamp         Model                  Pass    Total  Rate    Δ      Duration  Tokens
      2026-05-16-1130   vertex-gemini-flash    26 /   30      86.7%   --     370s       1.05M
      2026-05-16-1256   vertex-gemini-flash    25 /   29      86.2%   -0.5%  996s       1.32M

    Empty input renders an explanatory line, not a header-only table.
    """
    if not records:
        return "No history records yet — run `make eval` to create some.\n"

    recent = records[-limit:]
    lines = []
    lines.append(
        f"{'Timestamp':<17}  {'Model':<24}  "
        f"{'Pass':>6} / {'Total':>5}  {'Rate':>6}  {'Δ':>6}  "
        f"{'Duration':>9}  {'Tokens':>8}"
    )
    lines.append("-" * 97)
    prev_rate: float | None = None
    for r in recent:
        rate = r.get("pass_rate", 0.0)
        if prev_rate is None:
            delta = "  --  "
        else:
            d = rate - prev_rate
            delta = f"{d:+.1%}"
        prev_rate = rate
        tokens = r.get("total_tokens", 0)
        if tokens >= 1_000_000:
            tokens_disp = f"{tokens / 1_000_000:.2f}M"
        elif tokens >= 1_000:
            tokens_disp = f"{tokens / 1_000:.1f}k"
        else:
            tokens_disp = str(tokens)
        lines.append(
            f"{r.get('timestamp', '?'):<17}  "
            f"{r.get('model', '?'):<24}  "
            f"{r.get('passed', 0):>6} / {r.get('total', 0):>5}  "
            f"{rate:>6.1%}  "
            f"{delta:>6}  "
            f"{int(r.get('duration_sec', 0)):>8}s  "
            f"{tokens_disp:>8}"
        )
    return "\n".join(lines) + "\n"
