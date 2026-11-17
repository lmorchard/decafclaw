"""Tests for the improvement inbox JSONL helpers (#1482)."""

import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from decafclaw.improvement_inbox import (
    append_item,
    filter_items,
    get_item,
    inbox_path,
    new_item,
    read_items,
    remove_items,
    update_status,
)

_THIS_DIR = Path(__file__).parent
_SKILL_TOOLS = _THIS_DIR.parent / "contrib" / "skills" / "improvement-inbox" / "tools.py"
_spec = importlib.util.spec_from_file_location("decafclaw_contrib_improvement_inbox_tools", _SKILL_TOOLS)
assert _spec is not None and _spec.loader is not None
skill_tools = importlib.util.module_from_spec(_spec)
sys.modules["decafclaw_contrib_improvement_inbox_tools"] = skill_tools
_spec.loader.exec_module(skill_tools)


def _item(**overrides):
    fields = {
        "conversation_id": "conv-1",
        "trigger": "tool_error",
        "title": "SSH quoting fails",
        "observed_friction": "Nested quotes broke the remote command.",
        "suggested_improvement": "Pipe scripts through stdin.",
        "subsystem": "shell",
        "severity": "medium",
        "now": datetime(2026, 11, 17, 14, 25, 0, tzinfo=timezone.utc),
        "item_id": "imp_20261117_142500_a1b2",
    }
    fields.update(overrides)
    return new_item(**fields)


def test_append_and_read_round_trip(config):
    item = _item()
    path = append_item(config, item)

    assert path == inbox_path(config)
    assert path == config.workspace_path / "improvement_inbox.jsonl"
    assert read_items(config) == [item]
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == item["id"]


def test_read_missing_file_returns_empty(config):
    assert read_items(config) == []


def test_filter_items_by_status_subsystem_and_severity(config):
    append_item(config, _item(item_id="imp_a", severity="low"))
    append_item(
        config, _item(item_id="imp_b", severity="high", now=datetime(2026, 11, 17, 14, 26, tzinfo=timezone.utc))
    )
    update_status(config, ["imp_b"], "dismissed", reason="known")

    items = read_items(config)
    assert [item["id"] for item in filter_items(items)] == ["imp_a"]
    assert [item["id"] for item in filter_items(items, status=None)] == ["imp_a", "imp_b"]
    assert [item["id"] for item in filter_items(items, status="dismissed")] == ["imp_b"]
    assert [item["id"] for item in filter_items(items, status=None, severity="high")] == ["imp_b"]
    assert filter_items(items, status=None, subsystem="other") == []
    assert get_item(items, "imp_b")["dismissal_reason"] == "known"


def test_update_status_preserves_unrelated_and_malformed_lines(config):
    path = inbox_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# a hand-written note that is not JSON\n" + json.dumps(_item(item_id="imp_a")) + "\n",
        encoding="utf-8",
    )

    updated = update_status(
        config,
        ["imp_a"],
        "dismissed",
        reason="duplicate of #130",
        now=datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc),
    )

    assert updated == ["imp_a"]
    raw = path.read_text(encoding="utf-8").splitlines()
    assert raw[0] == "# a hand-written note that is not JSON"
    record = json.loads(raw[1])
    assert record["status"] == "dismissed"
    assert record["status_updated_at"] == "2026-11-17T15:00:00Z"
    assert record["dismissal_reason"] == "duplicate of #130"
    # Only one record, and no temp file left behind.
    assert read_items(config) == [record]
    assert list(path.parent.glob(".improvement_inbox.*.tmp")) == []


def test_update_status_unknown_id_is_a_noop(config):
    append_item(config, _item())
    before = inbox_path(config).read_text(encoding="utf-8")
    assert update_status(config, ["missing"], "dismissed") == []
    assert inbox_path(config).read_text(encoding="utf-8") == before


def test_update_status_rejects_unknown_status(config):
    with pytest.raises(ValueError):
        update_status(config, ["imp_a"], "bogus")


def test_atomic_rewrite_keeps_original_when_replace_fails(config, monkeypatch):
    append_item(config, _item())
    path = inbox_path(config)
    before = path.read_text(encoding="utf-8")

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("decafclaw.improvement_inbox.os.replace", _boom)
    with pytest.raises(OSError):
        update_status(config, ["imp_20261117_142500_a1b2"], "dismissed")

    assert path.read_text(encoding="utf-8") == before
    assert list(path.parent.glob(".improvement_inbox.*.tmp")) == []


def test_remove_items(config):
    append_item(config, _item(item_id="imp_a"))
    append_item(config, _item(item_id="imp_b"))
    assert remove_items(config, ["imp_a"]) == ["imp_a"]
    assert [item["id"] for item in read_items(config)] == ["imp_b"]
    assert remove_items(config, ["imp_a"]) == []


def test_concurrent_appends_keep_whole_lines(config):
    paths = [config.workspace_path]

    def _append(index: int):
        # Each call goes through the shared module lock and O_APPEND descriptor.
        append_item(config, _item(item_id=f"imp_{index:03d}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_append, range(40)))

    items = read_items(config)
    assert len(items) == 40
    assert {item["id"] for item in items} == {f"imp_{i:03d}" for i in range(40)}
    assert len(paths[0].joinpath("improvement_inbox.jsonl").read_text().splitlines()) == 40


def test_new_item_shape_and_generated_id(config):
    item = new_item(
        conversation_id="web-user-1",
        trigger="user_correction",
        title="t",
        observed_friction="f",
        suggested_improvement="s",
        now=datetime(2026, 11, 17, 14, 25, 0, tzinfo=timezone.utc),
    )
    assert item["id"].startswith("imp_20261117_142500_")
    assert item["status"] == "pending"
    assert item["status_updated_at"] is None
    assert item["dismissal_reason"] is None
    assert item["severity"] == "medium"


# ---------------------------------------------------------------------------
# Companion skill tools
# ---------------------------------------------------------------------------


def _seed_two(config):
    append_item(
        config,
        _item(item_id="imp_a", title="Shell quoting breaks", now=datetime.now(timezone.utc)),
    )
    append_item(
        config,
        _item(
            item_id="imp_b",
            title="Shell quoting breaks again",
            subsystem="tool",
            severity="high",
            now=datetime.now(timezone.utc) - timedelta(days=90),
        ),
    )


def test_skill_list_show_summary_and_dismiss(config, ctx):
    _seed_two(config)

    listed = skill_tools.improvement_inbox_list(ctx)
    assert "imp_a" in listed.text and "imp_b" in listed.text

    shown = skill_tools.improvement_inbox_show(ctx, "imp_a")
    assert "Nested quotes broke the remote command." in shown.text
    assert skill_tools.improvement_inbox_show(ctx, "nope").text.startswith("[error:")

    summary = skill_tools.improvement_inbox_summary(ctx)
    assert "2 improvement item(s)" in summary.text
    assert "shell: 1" in summary.text
    assert "quotes: 2 item(s)" in summary.text

    dismissed = skill_tools.improvement_inbox_dismiss(ctx, "imp_a", reason="known constraint")
    assert "imp_a" in dismissed.text
    record = get_item(read_items(config), "imp_a")
    assert record["status"] == "dismissed"
    assert record["dismissal_reason"] == "known constraint"


def test_skill_clear_archives_old_items(config, ctx):
    _seed_two(config)

    cleared = skill_tools.improvement_inbox_clear(ctx, older_than_days=30)
    assert "imp_b" in cleared.text
    assert "imp_a" not in cleared.text
    statuses = {item["id"]: item["status"] for item in read_items(config)}
    assert statuses == {"imp_a": "pending", "imp_b": "archived"}


def test_skill_export_writes_vault_page_once(config, ctx):
    _seed_two(config)
    ctx.config.vault.agent_folder = "agent"

    first = skill_tools.improvement_inbox_export(ctx)
    assert "Exported 2 item(s)" in first.text
    page = ctx.config.vault_agent_dir / "pages" / "decafclaw" / "improvement-inbox.md"
    text = page.read_text(encoding="utf-8")
    assert "<!-- imp:imp_a -->" in text and "<!-- imp:imp_b -->" in text
    assert {item["status"] for item in read_items(config)} == {"exported"}

    # Exporting again must not duplicate the page content.
    second = skill_tools.improvement_inbox_export(ctx)
    assert second.text == "No pending improvement items to export."
    assert page.read_text(encoding="utf-8").count("<!-- imp:imp_a -->") == 1


@pytest.mark.asyncio
async def test_skill_retro_uses_manager_history_and_buffers_item(config, ctx, stub_llm):
    from types import SimpleNamespace

    history = [
        {"role": "user", "content": "please run the migration"},
        {"role": "assistant", "content": "I will work around the lock by retrying."},
    ]
    ctx.manager = SimpleNamespace(load_history=lambda conv_id: list(history))
    ctx.config.default_model = "test-model"
    stub_llm.response = {
        "content": json.dumps(
            {
                "title": "Retry workaround hides a lock",
                "observed_friction": "The agent retried around a container lock.",
                "suggested_improvement": "Expose the lock state before retrying.",
                "subsystem": "workflow",
                "severity": "low",
            }
        ),
        "role": "assistant",
        "tool_calls": None,
        "usage": {},
    }

    result = await skill_tools.improvement_retro(ctx)

    assert result.text.startswith("Buffered imp_")
    items = read_items(config)
    assert len(items) == 1
    assert items[0]["trigger"] == "retro"
    assert items[0]["conversation_id"] == "test-conv"
    assert items[0]["subsystem"] == "workflow"
    assert len(stub_llm) == 1
    assert stub_llm[0]["model_name"] == "test-model"
