"""Retrieval-event telemetry (#197 Phase 0).

Subscriber consumes ``retrieval_event`` events (published once per
interactive turn from ``ContextComposer._compose_vault_retrieval``) and
appends one JSONL record per event, capturing the full scored candidate
list plus each candidate's include/drop verdict. Fail-open. A report
aggregates per-page retrieval/include/drop counts and a vault-health
snapshot (frontmatter coverage).
"""

import json

import pytest

from decafclaw.retrieval_telemetry import (
    _retrieval_path,
    aggregate,
    build_report,
    format_report,
    make_retrieval_telemetry_subscriber,
    record_from_event,
    vault_health,
)


def _event():
    return {
        "type": "retrieval_event", "conv_id": "c1",
        "candidates": [
            {"file_path": "pages/a.md", "source_type": "page", "similarity": 0.9,
             "recency": 0.8, "importance": 0.5, "composite_score": 0.77,
             "included": True, "drop_reason": None},
            {"file_path": "pages/b.md", "source_type": "page", "similarity": 0.2,
             "recency": 0.5, "importance": 0.5, "composite_score": 0.3,
             "included": False, "drop_reason": "score"},
        ],
    }


# -- subscriber ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscriber_writes_one_record_per_event(config):
    handler = make_retrieval_telemetry_subscriber(config)
    await handler(_event())
    await handler({"type": "tool_end"})  # ignored
    lines = _retrieval_path(config).read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["conv_id"] == "c1"
    assert len(rec["candidates"]) == 2
    assert rec["candidates"][0]["included"] is True
    assert "timestamp" in rec


@pytest.mark.asyncio
async def test_subscriber_fail_open_on_bad_event(config):
    handler = make_retrieval_telemetry_subscriber(config)
    await handler({"type": "retrieval_event"})  # no candidates key — must not raise


@pytest.mark.asyncio
async def test_subscriber_fail_open_on_bad_path(config, tmp_path):
    # Point the workspace at a location that can't be created (a file as a
    # dir parent), same fail-open shape as tool_telemetry's coverage.
    blocker = config.workspace_path
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("i am a file, not a dir")
    handler = make_retrieval_telemetry_subscriber(config)
    await handler(_event())  # must not raise


def test_record_from_event_shape():
    rec = record_from_event(_event())
    assert rec["conv_id"] == "c1"
    assert rec["candidates"] == _event()["candidates"]
    assert "timestamp" in rec


# -- aggregation -----------------------------------------------------------


def test_aggregate_counts_retrieval_include_and_drop():
    records = [
        {
            "conv_id": "c1",
            "candidates": [
                {"file_path": "pages/a.md", "source_type": "page",
                 "included": True, "drop_reason": None},
                {"file_path": "pages/b.md", "source_type": "page",
                 "included": False, "drop_reason": "score"},
            ],
        },
        {
            "conv_id": "c2",
            "candidates": [
                {"file_path": "pages/a.md", "source_type": "page",
                 "included": False, "drop_reason": "budget"},
            ],
        },
    ]
    stats = aggregate(records)
    a = stats["pages/a.md"]
    assert a["retrieval_count"] == 2
    assert a["include_count"] == 1
    assert a["drop_score"] == 0
    assert a["drop_budget"] == 1
    assert a["source_type"] == "page"

    b = stats["pages/b.md"]
    assert b["retrieval_count"] == 1
    assert b["include_count"] == 0
    assert b["drop_score"] == 1
    assert b["drop_budget"] == 0


def test_format_report_lists_pages_and_health():
    stats = {
        "pages/a.md": {
            "retrieval_count": 2, "include_count": 1, "include_rate": 0.5,
            "drop_score": 0, "drop_budget": 1, "source_type": "page",
        },
    }
    health = {
        "total_pages": 4, "with_importance": 2, "coverage_pct": 50.0,
        "missing_importance": 2, "graph_orphans": 1,
    }
    report = format_report(stats, health)
    assert "pages/a.md" in report
    assert "Vault health" in report
    assert "50" in report
    assert "Graph orphans" in report


# -- vault_health -----------------------------------------------------------


def test_vault_health_not_a_dir_returns_zeros(config):
    # vault_root doesn't exist in a fresh tmp config — short-circuit path.
    health = vault_health(config)
    assert health == {
        "total_pages": 0, "with_importance": 0, "coverage_pct": 0.0,
        "missing_importance": 0, "graph_orphans": 0,
    }


def test_vault_health_counts_pages_with_and_without_importance(config):
    pages_dir = config.vault_agent_pages_dir
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "with_importance.md").write_text(
        "---\nimportance: 0.5\n---\nBody.\n", encoding="utf-8",
    )
    (pages_dir / "without_importance.md").write_text("Body only.\n", encoding="utf-8")

    journal_dir = config.vault_agent_journal_dir
    journal_dir.mkdir(parents=True, exist_ok=True)
    (journal_dir / "2026-07-23.md").write_text("## Entry\nSome text.\n", encoding="utf-8")

    health = vault_health(config)

    # Journal entry excluded from the count entirely.
    assert health["total_pages"] == 2
    assert health["with_importance"] == 1
    assert health["missing_importance"] == 1
    assert health["coverage_pct"] == 50.0
    # Neither page links to the other — both are graph orphans (#197 P0-M2:
    # this is the genuine zero-inbound-links metric, distinct from
    # missing_importance).
    assert health["graph_orphans"] == 2


def test_vault_health_graph_orphans_excludes_linked_pages(config):
    pages_dir = config.vault_agent_pages_dir
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "linker.md").write_text("Links to [[target]].\n", encoding="utf-8")
    (pages_dir / "target.md").write_text("Linked-to page.\n", encoding="utf-8")
    (pages_dir / "lonely.md").write_text("Nobody links here.\n", encoding="utf-8")

    health = vault_health(config)

    assert health["total_pages"] == 3
    # target.md has one inbound link (from linker.md); linker.md and
    # lonely.md have zero.
    assert health["graph_orphans"] == 2


def test_build_report_end_to_end(config):
    path = _retrieval_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "conv_id": "c1",
        "candidates": [
            {"file_path": "pages/a.md", "source_type": "page",
             "included": True, "drop_reason": None},
        ],
    }) + "\n")
    report = build_report(config)
    assert "pages/a.md" in report
    assert "Vault health" in report
