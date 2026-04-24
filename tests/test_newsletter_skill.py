"""Tests for the newsletter bundled skill."""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from decafclaw.media import ToolResult
from decafclaw.skills.newsletter.tools import (
    SkillConfig,
    _collect_scheduled_activity,
    _collect_vault_changes,
    _parse_conv_id,
    newsletter_list_scheduled_activity,
    newsletter_list_vault_changes,
    newsletter_publish,
)

# ---------------------------------------------------------------------------
# Blocker 3: integration test — skill-loader pipeline registers all tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_newsletter_tools_register_via_skill_loader(ctx):
    """Self-reference guard: newsletter's SKILL.md + tools.py must expose
    the three tools via the skill-registration pipeline.

    This is the regression test for the missing TOOLS/TOOL_DEFINITIONS bug:
    without those dicts the tools are invisible at runtime even though the
    skill activates without errors.
    """
    from decafclaw.skills import discover_skills
    from decafclaw.tools.skill_tools import activate_skill_internal

    skills = discover_skills(ctx.config)
    newsletter_info = next((s for s in skills if s.name == "newsletter"), None)
    assert newsletter_info is not None, "newsletter skill not found in bundled skills"
    assert newsletter_info.has_native_tools, "newsletter skill should have native tools"

    await activate_skill_internal(ctx, newsletter_info)

    assert "newsletter_list_scheduled_activity" in ctx.tools.extra
    assert "newsletter_list_vault_changes" in ctx.tools.extra
    assert "newsletter_publish" in ctx.tools.extra

    # All three should also have definitions
    def_names = {d["function"]["name"] for d in ctx.tools.extra_definitions}
    assert "newsletter_list_scheduled_activity" in def_names
    assert "newsletter_list_vault_changes" in def_names
    assert "newsletter_publish" in def_names


def test_skill_config_defaults():
    cfg = SkillConfig()
    assert cfg.window_hours == 24
    assert cfg.email_enabled is False
    assert cfg.email_recipients == []
    assert cfg.email_subject_prefix == "[decafclaw newsletter]"
    assert cfg.vault_page_enabled is True
    assert cfg.vault_folder == "agent/journal/newsletters"


def _write_sched_conv(ws: Path, skill: str, ts: datetime, records: list[dict]) -> str:
    """Helper: write a synthetic scheduled-task conversation archive."""
    conv_id = f"schedule-{skill}-{ts.strftime('%Y%m%d-%H%M%S')}"
    conv_dir = ws / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    path = conv_dir / f"{conv_id}.jsonl"
    with path.open("w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return conv_id


def _local_naive_to_utc(naive: datetime) -> datetime:
    """Convert a naive local datetime to UTC — mirrors the _parse_conv_id logic."""
    local_tz = datetime.now().astimezone().tzinfo or timezone.utc
    return naive.replace(tzinfo=local_tz).astimezone(timezone.utc)


def test_parse_conv_id_basic():
    name, ts = _parse_conv_id("schedule-dream-20260424-073015")
    assert name == "dream"
    # Timestamp is naive local time in the conv_id; compare against expected UTC.
    expected_utc = _local_naive_to_utc(datetime(2026, 4, 24, 7, 30, 15))
    assert ts == expected_utc


def test_parse_conv_id_hyphenated_name():
    name, ts = _parse_conv_id("schedule-linkding-ingest-20260424-073015")
    assert name == "linkding-ingest"
    expected_utc = _local_naive_to_utc(datetime(2026, 4, 24, 7, 30, 15))
    assert ts == expected_utc


def test_parse_conv_id_timezone_correctness():
    """Timestamp in conv_id is naive local time; parsed result must be UTC.

    Write a synthetic conv_id using ``datetime.now()`` (naive local, exactly
    as ``schedules.py`` does), then assert the parsed UTC datetime is within
    a few seconds of the expected UTC equivalent.  Works regardless of the
    host timezone — no hard-coded offset.
    """
    one_hour_ago_local = datetime.now() - timedelta(hours=1)
    ts_str = one_hour_ago_local.strftime("%Y%m%d-%H%M%S")
    conv_id = f"schedule-test-{ts_str}"

    parsed = _parse_conv_id(conv_id)
    assert parsed is not None
    _name, ts_utc = parsed

    expected_utc = datetime.now(timezone.utc) - timedelta(hours=1)
    # Allow a 5-second tolerance for execution time between datetime.now() calls.
    assert abs((ts_utc - expected_utc).total_seconds()) < 5


def test_parse_conv_id_invalid():
    assert _parse_conv_id("chat-abc-def") is None
    assert _parse_conv_id("schedule-only") is None


def test_list_scheduled_activity_window_filter(ctx):
    ws = ctx.config.workspace_path
    now = datetime.now(timezone.utc)
    in_window = now - timedelta(hours=2)
    out_of_window = now - timedelta(hours=48)

    _write_sched_conv(ws, "dream", in_window, [
        {"role": "assistant", "content": "Dream complete. Noted 3 patterns."},
    ])
    _write_sched_conv(ws, "garden", out_of_window, [
        {"role": "assistant", "content": "Gardened vault."},
    ])

    result = _collect_scheduled_activity(ctx, hours=24)

    assert len(result) == 1
    assert result[0]["skill_name"] == "dream"
    assert result[0]["final_message"] == "Dream complete. Noted 3 patterns."


def test_list_scheduled_activity_extracts_vault_pages(ctx):
    ws = ctx.config.workspace_path
    now = datetime.now(timezone.utc)

    _write_sched_conv(ws, "dream", now - timedelta(hours=1), [
        {"role": "assistant", "content": "Writing pages.", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "vault_write",
                          "arguments": json.dumps({"page": "Insight A", "content": "..."})}},
            {"id": "c2", "type": "function",
             "function": {"name": "vault_journal_append",
                          "arguments": json.dumps({"content": "note"})}},
        ]},
        {"role": "assistant", "content": "Done."},
    ])

    result = _collect_scheduled_activity(ctx, hours=24)
    assert result[0]["vault_pages_touched"] == ["Insight A"]
    assert result[0]["final_message"] == "Done."


def test_list_scheduled_activity_excludes_self(ctx):
    ws = ctx.config.workspace_path
    now = datetime.now(timezone.utc)
    _write_sched_conv(ws, "newsletter", now - timedelta(hours=3),
                      [{"role": "assistant", "content": "prior newsletter"}])
    _write_sched_conv(ws, "dream", now - timedelta(hours=2),
                      [{"role": "assistant", "content": "dreamed"}])

    result = _collect_scheduled_activity(ctx, hours=24)
    skills = {r["skill_name"] for r in result}
    assert "newsletter" not in skills
    assert "dream" in skills


@pytest.mark.asyncio
async def test_list_scheduled_activity_returns_tool_result(ctx):
    ws = ctx.config.workspace_path
    now = datetime.now(timezone.utc)
    _write_sched_conv(ws, "dream", now - timedelta(hours=1), [
        {"role": "assistant", "content": "Dream done."},
    ])
    _write_sched_conv(ws, "garden", now - timedelta(hours=2), [
        {"role": "assistant", "content": "Garden done."},
    ])

    result = await newsletter_list_scheduled_activity(ctx, hours=24)

    assert isinstance(result, ToolResult)
    assert "2" in result.text
    assert isinstance(result.data["activity"], list)
    assert len(result.data["activity"]) == 2


def test_list_scheduled_activity_final_with_tool_calls(ctx):
    """An assistant record with BOTH content and tool_calls still contributes
    to final_message — this is the shape real archives take most of the time."""
    now = datetime.now(timezone.utc)
    _write_sched_conv(ctx.config.workspace_path, "garden", now - timedelta(hours=1), [
        {"role": "assistant", "content": "Starting garden sweep."},
        {"role": "assistant", "content": "Pruning old pages.",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "vault_write",
                                      "arguments": json.dumps({"page": "P"})}}]},
    ])
    activity = _collect_scheduled_activity(ctx, hours=24)
    assert activity[0]["final_message"] == "Pruning old pages."


# ---------------------------------------------------------------------------
# newsletter_list_vault_changes tests
# ---------------------------------------------------------------------------


def test_list_vault_changes_window(ctx, tmp_path):
    vault = tmp_path / "vault"
    (vault / "agent").mkdir(parents=True)
    ctx.config.vault.vault_path = str(vault)

    recent = vault / "agent" / "recent.md"
    recent.write_text("hello")

    old = vault / "agent" / "old.md"
    old.write_text("world")
    old_ts = time.time() - (48 * 3600)
    os.utime(old, (old_ts, old_ts))

    result = _collect_vault_changes(ctx, hours=24)
    paths = {r["path"] for r in result}
    assert "agent/recent.md" in paths
    assert "agent/old.md" not in paths


def test_list_vault_changes_empty_vault(ctx, tmp_path):
    ctx.config.vault.vault_path = str(tmp_path / "nonexistent")
    result = _collect_vault_changes(ctx, hours=24)
    assert result == []


def test_list_vault_changes_excludes_newsletter_folder(ctx, tmp_path):
    """The tool's own output folder should not show up as 'vault changes'
    to avoid self-reference in subsequent newsletters."""
    from decafclaw.skills.newsletter import tools as m
    vault = tmp_path / "vault"
    (vault / "agent" / "journal" / "newsletters").mkdir(parents=True)
    ctx.config.vault.vault_path = str(vault)

    # Default SkillConfig has vault_folder = "agent/journal/newsletters"
    m._skill_config = SkillConfig()

    newsletter_file = vault / "agent" / "journal" / "newsletters" / "2026-04-24.md"
    newsletter_file.write_text("prior newsletter")

    other = vault / "agent" / "Note.md"
    other.write_text("real note")

    result = _collect_vault_changes(ctx, hours=24)
    paths = {r["path"] for r in result}
    assert "agent/Note.md" in paths
    assert not any("newsletters" in p for p in paths)


@pytest.mark.asyncio
async def test_list_vault_changes_returns_tool_result(ctx, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ctx.config.vault.vault_path = str(vault)
    (vault / "p.md").write_text("x")

    result = await newsletter_list_vault_changes(ctx, hours=24)
    assert isinstance(result, ToolResult)
    assert "1" in result.text
    assert result.data["changes"][0]["path"] == "p.md"


# ---------------------------------------------------------------------------
# newsletter_publish tests — Task 4: interactive branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_interactive_returns_markdown(ctx, tmp_path):
    # task_mode empty string = interactive / !newsletter
    ctx.task_mode = ""

    result = await newsletter_publish(ctx, markdown="# hello\n\nbody")
    assert isinstance(result, ToolResult)
    assert result.text == "# hello\n\nbody"

    # No side effects
    assert not (Path(ctx.config.workspace_path) / "newsletter" / "archive").exists()
    assert not (Path(ctx.config.workspace_path) / "newsletter" / "last_run.json").exists()


# ---------------------------------------------------------------------------
# newsletter_publish tests — Task 5: scheduled branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_scheduled_writes_archive_and_advances_state(ctx, tmp_path):
    ctx.task_mode = "scheduled"

    # No email, no vault target — just verify archive + last_run
    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(email_enabled=False, vault_page_enabled=False)

    result = await newsletter_publish(ctx, markdown="# hi\n\nnews", subject_hint="today")
    assert isinstance(result, ToolResult)

    workspace = Path(ctx.config.workspace_path)
    archive_dir = workspace / "newsletter" / "archive"
    archive_files = list(archive_dir.glob("*.md"))
    assert len(archive_files) == 1
    assert archive_files[0].read_text() == "# hi\n\nnews"

    last_run = workspace / "newsletter" / "last_run.json"
    assert last_run.exists()
    data = json.loads(last_run.read_text())
    assert "last_run_utc" in data
    assert "window_end_utc" in data


@pytest.mark.asyncio
async def test_publish_scheduled_empty_stub(ctx, tmp_path):
    ctx.task_mode = "scheduled"

    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(email_enabled=False, vault_page_enabled=False)

    result = await newsletter_publish(ctx, markdown="", has_content=False)
    assert isinstance(result, ToolResult)

    workspace = Path(ctx.config.workspace_path)
    archive_dir = workspace / "newsletter" / "archive"
    archive_files = list(archive_dir.glob("*.md"))
    assert len(archive_files) == 1
    assert "nothing to report" in archive_files[0].read_text().lower()

    last_run = workspace / "newsletter" / "last_run.json"
    assert last_run.exists()


@pytest.mark.asyncio
async def test_publish_scheduled_archive_suffix_on_conflict(ctx, tmp_path):
    ctx.task_mode = "scheduled"
    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(email_enabled=False, vault_page_enabled=False)

    await newsletter_publish(ctx, markdown="a")
    await newsletter_publish(ctx, markdown="b")

    workspace = Path(ctx.config.workspace_path)
    archive_dir = workspace / "newsletter" / "archive"
    names = sorted(p.name for p in archive_dir.glob("*.md"))
    assert len(names) == 2
    # Second write should be suffixed -1
    assert any("-1.md" in n for n in names)


# ---------------------------------------------------------------------------
# newsletter_publish tests — Task 6: email delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_scheduled_email_delivery(ctx, tmp_path, monkeypatch):
    ctx.task_mode = "scheduled"

    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(
        email_enabled=True,
        email_recipients=["les@example.com", "alt@example.com"],
        email_subject_prefix="[nlx]",
        vault_page_enabled=False,
    )

    calls = []

    async def fake_send_mail(config, *, to, subject, body, **kwargs):
        calls.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr(m, "send_mail", fake_send_mail)

    result = await newsletter_publish(ctx, markdown="# hi\n\nstuff", subject_hint="two things")

    assert len(calls) == 1
    assert calls[0]["to"] == ["les@example.com", "alt@example.com"]
    assert "[nlx]" in calls[0]["subject"]
    assert "two things" in calls[0]["subject"]
    assert calls[0]["body"] == "# hi\n\nstuff"

    assert "email" in result.data["delivered_targets"]


@pytest.mark.asyncio
async def test_publish_scheduled_email_skipped_when_empty(ctx, tmp_path, monkeypatch):
    ctx.task_mode = "scheduled"

    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(
        email_enabled=True, email_recipients=["x@y.z"], vault_page_enabled=False,
    )
    calls = []

    async def fake_send_mail(config, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(m, "send_mail", fake_send_mail)

    await newsletter_publish(ctx, markdown="", has_content=False)
    assert calls == []


# ---------------------------------------------------------------------------
# newsletter_publish tests — Task 7: vault-page delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_scheduled_vault_page_delivery(ctx, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ctx.config.vault.vault_path = str(vault)

    ctx.task_mode = "scheduled"

    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(
        email_enabled=False,
        vault_page_enabled=True,
        vault_folder="agent/journal/newsletters",
    )

    result = await newsletter_publish(ctx, markdown="# today\n\nstuff")

    vault_root = Path(ctx.config.vault_root)
    vault_files = list((vault_root / "agent" / "journal" / "newsletters").glob("*.md"))
    assert len(vault_files) == 1
    assert vault_files[0].read_text() == "# today\n\nstuff"
    assert "vault_page" in result.data["delivered_targets"]


@pytest.mark.asyncio
async def test_publish_scheduled_vault_page_conflict_suffix(ctx, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ctx.config.vault.vault_path = str(vault)
    ctx.task_mode = "scheduled"
    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(
        email_enabled=False,
        vault_page_enabled=True,
        vault_folder="agent/journal/newsletters",
    )
    await newsletter_publish(ctx, markdown="a")
    await newsletter_publish(ctx, markdown="b")

    vault_root = Path(ctx.config.vault_root)
    vault_files = sorted(p.name for p in
                         (vault_root / "agent" / "journal" / "newsletters").glob("*.md"))
    assert len(vault_files) == 2
    assert any("-1.md" in n for n in vault_files)


# ---------------------------------------------------------------------------
# newsletter_publish tests — Task 8: per-target failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_target_failure_isolation(ctx, tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    ctx.config.vault.vault_path = str(vault)
    ctx.task_mode = "scheduled"

    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(
        email_enabled=True,
        email_recipients=["x@y.z"],
        vault_page_enabled=True,
    )

    async def boom(*args, **kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(m, "send_mail", boom)

    result = await newsletter_publish(ctx, markdown="# alive\n\nstill going")

    workspace = Path(ctx.config.workspace_path)
    vault_root = Path(ctx.config.vault_root)

    # Archive still happened
    archive_files = list((workspace / "newsletter" / "archive").glob("*.md"))
    assert len(archive_files) == 1
    # Vault page still delivered
    vault_files = list((vault_root / "agent" / "journal" / "newsletters").glob("*.md"))
    assert len(vault_files) == 1
    # Email NOT in delivered_targets
    assert "email" not in result.data["delivered_targets"]
    assert "vault_page" in result.data["delivered_targets"]
    # last_run still advanced
    assert (workspace / "newsletter" / "last_run.json").exists()


# ---------------------------------------------------------------------------
# Task 10: end-to-end scheduled publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_scheduled_publish(ctx, tmp_path, monkeypatch):
    vault = tmp_path / "e2e_vault"
    vault.mkdir()
    ctx.config.vault.vault_path = str(vault)
    ctx.task_mode = "scheduled"

    # Seed: one scheduled-task conversation and one recent vault page
    now = datetime.now(timezone.utc)
    workspace = Path(ctx.config.workspace_path)
    _write_sched_conv(workspace, "dream", now - timedelta(hours=2), [
        {"role": "assistant", "content": "Writing pages.",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "vault_write",
                                      "arguments": json.dumps({"page": "Pattern A"})}}]},
        {"role": "assistant", "content": "Dreamed. 2 patterns surfaced."},
    ])
    (vault / "agent").mkdir(parents=True, exist_ok=True)
    (vault / "agent" / "Pattern A.md").write_text("body")

    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(
        email_enabled=True, email_recipients=["u@x.y"],
        vault_page_enabled=True,
    )
    sent = []

    async def fake_send_mail(config, **kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(m, "send_mail", fake_send_mail)

    # Simulate the agent's tool-call sequence
    activity_result = await newsletter_list_scheduled_activity(ctx, hours=24)
    changes_result = await newsletter_list_vault_changes(ctx, hours=24)
    activity = activity_result.data["activity"]
    changes = changes_result.data["changes"]
    assert len(activity) == 1
    assert len(changes) >= 1

    composed = (
        f"# Newsletter\n\nDream surfaced 2 patterns including [[Pattern A]].\n"
        f"\nPages modified: {len(changes)}. Tasks run: {len(activity)}.\n"
    )
    result = await newsletter_publish(ctx, markdown=composed, subject_hint="dreams")

    # Archive written
    assert len(list((workspace / "newsletter" / "archive").glob("*.md"))) == 1
    # Vault page delivered
    assert len(list((vault / "agent" / "journal" / "newsletters").glob("*.md"))) == 1
    # Email sent
    assert len(sent) == 1
    assert "dreams" in sent[0]["subject"]
    # State advanced
    assert (workspace / "newsletter" / "last_run.json").exists()
    # Result data accurate
    assert set(result.data["delivered_targets"]) == {"email", "vault_page"}


# ---------------------------------------------------------------------------
# Fix 3: vault_pages_touched deduplication
# ---------------------------------------------------------------------------


def test_extract_activity_deduplicates_vault_pages(ctx):
    """If the same page is written twice in one run, vault_pages_touched
    should contain it only once (insertion order preserved)."""
    now = datetime.now(timezone.utc)
    _write_sched_conv(ctx.config.workspace_path, "dream", now - timedelta(hours=1), [
        {"role": "assistant", "content": "Writing.", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "vault_write",
                          "arguments": json.dumps({"page": "Page X", "content": "v1"})}},
            {"id": "c2", "type": "function",
             "function": {"name": "vault_write",
                          "arguments": json.dumps({"page": "Page X", "content": "v2"})}},
            {"id": "c3", "type": "function",
             "function": {"name": "vault_write",
                          "arguments": json.dumps({"page": "Page Y", "content": "y"})}},
        ]},
    ])
    result = _collect_scheduled_activity(ctx, hours=24)
    touched = result[0]["vault_pages_touched"]
    assert touched == ["Page X", "Page Y"], (
        f"Expected deduped list preserving order, got {touched!r}"
    )
    assert len(touched) == 2


def test_is_status_token_classification():
    """Status-token heuristic: short ALL_CAPS_UNDERSCORE strings are tokens;
    narrative text (lowercase, mixed case, multi-line, long) is not."""
    from decafclaw.skills.newsletter.tools import _is_status_token

    # Tokens
    assert _is_status_token("HEARTBEAT_OK")
    assert _is_status_token("OK")
    assert _is_status_token("DONE")
    assert _is_status_token("STATUS_OK")
    assert _is_status_token("")
    assert _is_status_token("   ")

    # Not tokens — real narrative
    assert not _is_status_token("I have completed the scheduled task.")
    assert not _is_status_token("dream cycle complete")
    assert not _is_status_token("HEARTBEAT_OK\n\nExtra detail")
    assert not _is_status_token("Status: OK, processed 5 items")
    # Too long to be a token
    assert not _is_status_token("A" * 60)


def test_extract_activity_skips_status_tokens(ctx):
    """If the last assistant content is a status token like HEARTBEAT_OK,
    final_message should fall back to the previous narrative content."""
    now = datetime.now(timezone.utc)
    _write_sched_conv(ctx.config.workspace_path, "mastodon-ingest",
                      now - timedelta(hours=1), [
        {"role": "assistant",
         "content": "I fetched 5 posts and ingested them into the vault."},
        {"role": "tool", "content": "tool result"},
        {"role": "assistant", "content": "HEARTBEAT_OK"},
    ])
    result = _collect_scheduled_activity(ctx, hours=24)
    assert len(result) == 1
    assert result[0]["final_message"] == (
        "I fetched 5 posts and ingested them into the vault."
    )


def test_extract_activity_only_status_token_yields_empty_final(ctx):
    """If the ONLY assistant text in a run is a status token, final_message
    should be empty — not the status token."""
    now = datetime.now(timezone.utc)
    _write_sched_conv(ctx.config.workspace_path, "dream",
                      now - timedelta(hours=1), [
        {"role": "assistant", "content": "HEARTBEAT_OK"},
    ])
    result = _collect_scheduled_activity(ctx, hours=24)
    assert len(result) == 1
    assert result[0]["final_message"] == ""


# ---------------------------------------------------------------------------
# Window argument: `!newsletter 7d` etc. — compact time-range spec
# ---------------------------------------------------------------------------

def test_parse_window_basic_units():
    from decafclaw.skills.newsletter.tools import _parse_window
    assert _parse_window("24h") == 24
    assert _parse_window("1h") == 1
    assert _parse_window("7d") == 7 * 24
    assert _parse_window("1d") == 24
    assert _parse_window("2w") == 2 * 7 * 24
    assert _parse_window("1w") == 7 * 24


def test_parse_window_tolerates_case_and_whitespace():
    from decafclaw.skills.newsletter.tools import _parse_window
    assert _parse_window(" 7D ") == 7 * 24
    assert _parse_window("48H") == 48


def test_parse_window_rejects_malformed():
    import pytest

    from decafclaw.skills.newsletter.tools import _parse_window
    for bad in ["", "7", "7days", "abc", "-5d", "0d", "7m"]:
        with pytest.raises(ValueError):
            _parse_window(bad)


@pytest.mark.asyncio
async def test_list_scheduled_activity_window_arg(ctx):
    """When `window` is passed to the tool, it overrides `hours` and the
    tool summary reflects the window string."""
    now = datetime.now(timezone.utc)
    # Activity 3 days ago — outside 24h window, inside 7d
    _write_sched_conv(ctx.config.workspace_path, "dream",
                      now - timedelta(days=3), [
        {"role": "assistant", "content": "Dream three days back."},
    ])
    # Default 24h: should miss
    default_result = await newsletter_list_scheduled_activity(ctx)
    assert len(default_result.data["activity"]) == 0
    # window=7d: should hit
    wide_result = await newsletter_list_scheduled_activity(ctx, window="7d")
    assert len(wide_result.data["activity"]) == 1
    assert wide_result.data["hours"] == 7 * 24
    assert wide_result.data["window"] == "7d"
    assert "7d" in wide_result.text


@pytest.mark.asyncio
async def test_list_scheduled_activity_window_precedence_over_hours(ctx):
    """`window` takes precedence when both are supplied."""
    now = datetime.now(timezone.utc)
    _write_sched_conv(ctx.config.workspace_path, "dream",
                      now - timedelta(days=3), [
        {"role": "assistant", "content": "old."},
    ])
    result = await newsletter_list_scheduled_activity(
        ctx, hours=1, window="7d"
    )
    # window=7d wins over hours=1 → should include the 3-day-old record
    assert len(result.data["activity"]) == 1
    assert result.data["hours"] == 7 * 24


@pytest.mark.asyncio
async def test_list_scheduled_activity_invalid_window_returns_error(ctx):
    result = await newsletter_list_scheduled_activity(ctx, window="7years")
    assert "[error:" in result.text
    assert result.data is None or "activity" not in (result.data or {})


@pytest.mark.asyncio
async def test_list_vault_changes_window_arg(ctx, tmp_path):
    """Same window semantics apply to the vault-changes tool."""
    import os
    vault = tmp_path / "vault"
    vault.mkdir()
    ctx.config.vault.vault_path = str(vault)
    # A file touched 3 days ago
    old = vault / "old.md"
    old.write_text("x")
    old_ts = time.time() - (3 * 24 * 3600)
    os.utime(old, (old_ts, old_ts))
    # Default 24h: miss
    default = await newsletter_list_vault_changes(ctx)
    assert len(default.data["changes"]) == 0
    # window=7d: hit
    wide = await newsletter_list_vault_changes(ctx, window="7d")
    assert len(wide.data["changes"]) == 1
    assert wide.data["hours"] == 7 * 24
    assert "7d" in wide.text


# ---------------------------------------------------------------------------
# Fix 2: vault_folder path-traversal sandboxing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_vault_folder_traversal_blocked(ctx, tmp_path):
    """A malicious vault_folder must not write outside the vault root."""
    vault = tmp_path / "vault"
    vault.mkdir()
    escape_target = tmp_path / "escape"
    ctx.config.vault.vault_path = str(vault)
    ctx.task_mode = "scheduled"

    from decafclaw.skills.newsletter import tools as m

    for bad_folder in ["../escape", "../../tmp/evil"]:
        m._skill_config = SkillConfig(
            email_enabled=False,
            vault_page_enabled=True,
            vault_folder=bad_folder,
        )
        result = await newsletter_publish(ctx, markdown="# bad\n\nevil content")
        # vault_page must NOT be in delivered_targets
        assert "vault_page" not in result.data["delivered_targets"], (
            f"vault_folder={bad_folder!r} should have been blocked"
        )
        # No files should be written outside the vault root
        assert not escape_target.exists(), (
            f"vault_folder={bad_folder!r} escaped the vault root"
        )


@pytest.mark.asyncio
async def test_publish_vault_folder_absolute_blocked(ctx, tmp_path):
    """An absolute vault_folder must be rejected."""
    vault = tmp_path / "vault"
    vault.mkdir()
    ctx.config.vault.vault_path = str(vault)
    ctx.task_mode = "scheduled"

    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(
        email_enabled=False,
        vault_page_enabled=True,
        vault_folder=str(tmp_path / "evil"),
    )
    result = await newsletter_publish(ctx, markdown="# bad\n\nevil content")
    assert "vault_page" not in result.data["delivered_targets"]
    assert not (tmp_path / "evil").exists()
