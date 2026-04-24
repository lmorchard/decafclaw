# Newsletter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Phase 1 periodic newsletter — a bundled scheduled skill that summarizes autonomous agent activity the user didn't participate in, with email + vault-page delivery and a `!newsletter` on-demand peek.

**Architecture:** Entire feature lives in `src/decafclaw/skills/newsletter/`. Pull/observer model: the publisher mines `workspace/conversations/schedule-*.jsonl` and vault page changes within a window, composes a narrative via the agent's LLM turn, and delivers inline from its own tool. No new core subsystems, no new event bus events.

**Tech Stack:** Python 3 dataclasses, existing `mail.py` async SMTP core, skill `SkillConfig` pattern, filesystem JSONL parsing. TDD with pytest + pytest-asyncio.

**Reference files:**
- Spec: [`docs/dev-sessions/2026-04-24-1044-newsletter-283/spec.md`](spec.md)
- Skill reference: `src/decafclaw/skills/claude_code/tools.py` (SkillConfig + init + tools pattern)
- Mail core: `src/decafclaw/mail.py` (`send_mail()`)
- Tool result: `src/decafclaw/media.py` (`ToolResult`)
- Archive format: `src/decafclaw/archive.py`
- Test fixtures: `tests/conftest.py` (`config`, `ctx` fixtures)

**Conversation filtering:** Scheduled-task conversations are identifiable by `conv_id` prefix `schedule-{task_name}-{YYYYMMDD-HHMMSS}` (from `src/decafclaw/schedules.py:224`). No core change needed.

**Key invariants:**
- Newsletter skill is itself a scheduled task, so its own conversations (prefix `schedule-newsletter-*`) MUST be excluded from `newsletter_list_scheduled_activity` output to avoid self-reference.
- Interactive mode detected via `ctx.task_mode` — `"scheduled"` for scheduled runs, `""` (empty string) for interactive/user-invocable.
- All timestamps UTC.

---

## File Structure

**Create:**
- `src/decafclaw/skills/newsletter/__init__.py` — empty package marker
- `src/decafclaw/skills/newsletter/SKILL.md` — frontmatter + editorial composition prompt
- `src/decafclaw/skills/newsletter/tools.py` — `SkillConfig`, `init()`, three tools, helpers
- `tests/test_newsletter_skill.py` — unit tests for tools + config
- `docs/newsletter.md` — feature documentation

**Modify:**
- `docs/index.md` — link the new `newsletter.md`
- `CLAUDE.md` — add `skills/newsletter/` to the Key Files list under the Skills section

**Implicit surfaces (written at runtime, not committed):**
- `workspace/newsletter/archive/YYYY-MM-DD.md` — local newsletter archive
- `workspace/newsletter/last_run.json` — publisher state
- `{vault_root}/{agent_folder}/journal/newsletters/YYYY-MM-DD.md` — vault-page deliveries

---

## Task 1: Scaffold skill package + SkillConfig + init()

**Files:**
- Create: `src/decafclaw/skills/newsletter/__init__.py`
- Create: `src/decafclaw/skills/newsletter/SKILL.md`
- Create: `src/decafclaw/skills/newsletter/tools.py`
- Test:   `tests/test_newsletter_skill.py`

- [ ] **Step 1: Write failing test for SkillConfig defaults and loader**

Create `tests/test_newsletter_skill.py`:

```python
"""Tests for the newsletter bundled skill."""

from decafclaw.skills.newsletter.tools import SkillConfig


def test_skill_config_defaults():
    cfg = SkillConfig()
    assert cfg.window_hours == 24
    assert cfg.email_enabled is False
    assert cfg.email_recipients == []
    assert cfg.email_subject_prefix == "[decafclaw newsletter]"
    assert cfg.vault_page_enabled is True
    assert cfg.vault_folder == "agent/journal/newsletters"
```

- [ ] **Step 2: Run test, confirm failure**

```bash
uv run pytest tests/test_newsletter_skill.py -v
```

Expected: `ModuleNotFoundError: No module named 'decafclaw.skills.newsletter'`

- [ ] **Step 3: Create empty package marker**

Create `src/decafclaw/skills/newsletter/__init__.py` as an empty file.

- [ ] **Step 4: Create minimal SKILL.md**

Create `src/decafclaw/skills/newsletter/SKILL.md`:

```markdown
---
name: newsletter
description: Compose and deliver a narrative newsletter summarizing autonomous agent activity in the window.
schedule: "0 7 * * *"
user-invocable: true
allowed-tools: newsletter_list_scheduled_activity, newsletter_list_vault_changes, newsletter_publish, current_time
---

(composition prompt body — filled in Task 9)
```

- [ ] **Step 5: Create tools.py skeleton with SkillConfig + init**

Create `src/decafclaw/skills/newsletter/tools.py`:

```python
"""Newsletter bundled skill — composes and delivers periodic activity digests."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_config = None
_skill_config: "SkillConfig | None" = None


@dataclass
class SkillConfig:
    window_hours: int = field(
        default=24, metadata={"env_alias": "NEWSLETTER_WINDOW_HOURS"}
    )
    email_enabled: bool = field(
        default=False, metadata={"env_alias": "NEWSLETTER_EMAIL_ENABLED"}
    )
    email_recipients: list[str] = field(
        default_factory=list,
        metadata={"env_alias": "NEWSLETTER_EMAIL_RECIPIENTS"},
    )
    email_subject_prefix: str = field(
        default="[decafclaw newsletter]",
        metadata={"env_alias": "NEWSLETTER_EMAIL_SUBJECT_PREFIX"},
    )
    vault_page_enabled: bool = field(
        default=True, metadata={"env_alias": "NEWSLETTER_VAULT_PAGE_ENABLED"}
    )
    vault_folder: str = field(
        default="agent/journal/newsletters",
        metadata={"env_alias": "NEWSLETTER_VAULT_FOLDER"},
    )


def init(config, skill_config: SkillConfig) -> None:
    """Initialize the newsletter skill. Called by the skill loader on activation."""
    global _config, _skill_config
    _config = config
    _skill_config = skill_config
```

- [ ] **Step 6: Run tests, confirm pass**

```bash
uv run pytest tests/test_newsletter_skill.py -v
```

Expected: PASS

- [ ] **Step 7: Run full lint + typecheck**

```bash
make lint && make typecheck
```

Expected: PASS (no errors).

- [ ] **Step 8: Commit**

```bash
git add src/decafclaw/skills/newsletter/ tests/test_newsletter_skill.py
git commit -m "feat(newsletter): scaffold skill package with SkillConfig (#283)"
```

---

## Task 2: `newsletter_list_scheduled_activity` tool

Filters `workspace/conversations/schedule-*.jsonl`, excludes self (newsletter's own runs), parses final assistant message + touched vault pages from each matched archive.

**Files:**
- Modify: `src/decafclaw/skills/newsletter/tools.py` (add helper + tool)
- Test:   `tests/test_newsletter_skill.py` (add tests + fixture)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_newsletter_skill.py`:

```python
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from decafclaw.skills.newsletter.tools import (
    _parse_conv_id,
    newsletter_list_scheduled_activity,
)


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


def test_parse_conv_id_basic():
    name, ts = _parse_conv_id("schedule-dream-20260424-073015")
    assert name == "dream"
    assert ts == datetime(2026, 4, 24, 7, 30, 15, tzinfo=timezone.utc)


def test_parse_conv_id_hyphenated_name():
    name, ts = _parse_conv_id("schedule-linkding-ingest-20260424-073015")
    assert name == "linkding-ingest"
    assert ts == datetime(2026, 4, 24, 7, 30, 15, tzinfo=timezone.utc)


def test_parse_conv_id_invalid():
    assert _parse_conv_id("chat-abc-def") is None
    assert _parse_conv_id("schedule-only") is None


@pytest.mark.asyncio
async def test_list_scheduled_activity_window_filter(ctx, tmp_path):
    ctx.config.workspace_path = tmp_path
    now = datetime.now(timezone.utc)
    in_window = now - timedelta(hours=2)
    out_of_window = now - timedelta(hours=48)

    _write_sched_conv(tmp_path, "dream", in_window, [
        {"role": "assistant", "content": "Dream complete. Noted 3 patterns."},
    ])
    _write_sched_conv(tmp_path, "garden", out_of_window, [
        {"role": "assistant", "content": "Gardened vault."},
    ])

    result = await newsletter_list_scheduled_activity(ctx, hours=24)

    assert len(result) == 1
    assert result[0]["skill_name"] == "dream"
    assert result[0]["final_message"] == "Dream complete. Noted 3 patterns."


@pytest.mark.asyncio
async def test_list_scheduled_activity_extracts_vault_pages(ctx, tmp_path):
    ctx.config.workspace_path = tmp_path
    now = datetime.now(timezone.utc)

    _write_sched_conv(tmp_path, "dream", now - timedelta(hours=1), [
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

    result = await newsletter_list_scheduled_activity(ctx, hours=24)
    assert result[0]["vault_pages_touched"] == ["Insight A"]
    assert result[0]["final_message"] == "Done."


@pytest.mark.asyncio
async def test_list_scheduled_activity_excludes_self(ctx, tmp_path):
    ctx.config.workspace_path = tmp_path
    now = datetime.now(timezone.utc)
    _write_sched_conv(tmp_path, "newsletter", now - timedelta(hours=3),
                      [{"role": "assistant", "content": "prior newsletter"}])
    _write_sched_conv(tmp_path, "dream", now - timedelta(hours=2),
                      [{"role": "assistant", "content": "dreamed"}])

    result = await newsletter_list_scheduled_activity(ctx, hours=24)
    skills = {r["skill_name"] for r in result}
    assert "newsletter" not in skills
    assert "dream" in skills
```

- [ ] **Step 2: Run tests, confirm failure**

```bash
uv run pytest tests/test_newsletter_skill.py -v
```

Expected: FAIL (`_parse_conv_id` and `newsletter_list_scheduled_activity` not defined).

- [ ] **Step 3: Implement `_parse_conv_id` helper and the tool**

Append to `src/decafclaw/skills/newsletter/tools.py`:

```python
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_CONV_ID_RE = re.compile(r"^schedule-(.+)-(\d{8}-\d{6})$")
_VAULT_WRITE_TOOLS = {"vault_write"}
_SELF_SKILL_NAME = "newsletter"


def _parse_conv_id(conv_id: str) -> tuple[str, datetime] | None:
    """Parse `schedule-{name}-{YYYYMMDD-HHMMSS}`. Returns None if not scheduled."""
    match = _CONV_ID_RE.match(conv_id)
    if not match:
        return None
    name = match.group(1)
    try:
        ts = datetime.strptime(match.group(2), "%Y%m%d-%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
    return name, ts


def _extract_activity(path: Path) -> tuple[str, list[str]]:
    """Read a conversation JSONL; return (final_assistant_text, vault_pages_touched)."""
    final_text = ""
    touched: list[str] = []
    try:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("role") != "assistant":
                    continue
                content = rec.get("content") or ""
                if content and not rec.get("tool_calls"):
                    final_text = content
                for call in rec.get("tool_calls") or []:
                    fn = (call.get("function") or {}).get("name")
                    if fn in _VAULT_WRITE_TOOLS:
                        try:
                            args = json.loads(
                                (call.get("function") or {}).get("arguments") or "{}"
                            )
                        except json.JSONDecodeError:
                            continue
                        page = args.get("page")
                        if page:
                            touched.append(page)
    except OSError as exc:
        log.debug("Failed to read %s: %s", path, exc)
    return final_text, touched


async def newsletter_list_scheduled_activity(ctx, hours: int = 24) -> list[dict]:
    """List scheduled-task activity within the last `hours`.

    Returns one entry per scheduled-task conversation (excluding newsletter's own),
    with skill_name, conv_id, started_at, final_message, and vault_pages_touched.
    """
    workspace = Path(ctx.config.workspace_path)
    conv_dir = workspace / "conversations"
    if not conv_dir.is_dir():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: list[dict] = []
    for path in sorted(conv_dir.glob("schedule-*.jsonl")):
        parsed = _parse_conv_id(path.stem)
        if parsed is None:
            continue
        skill_name, ts = parsed
        if skill_name == _SELF_SKILL_NAME:
            continue
        if ts < cutoff:
            continue
        final, touched = _extract_activity(path)
        out.append({
            "skill_name": skill_name,
            "conv_id": path.stem,
            "started_at": ts.isoformat(),
            "final_message": final,
            "vault_pages_touched": touched,
        })
    return out
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
uv run pytest tests/test_newsletter_skill.py -v
```

Expected: all PASS.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint && make typecheck
git add src/decafclaw/skills/newsletter/tools.py tests/test_newsletter_skill.py
git commit -m "feat(newsletter): list_scheduled_activity tool (#283)"
```

---

## Task 3: `newsletter_list_vault_changes` tool

Mtime-based scan of the vault root — returns vault pages added or modified in the window. Phase 1 uses mtime; a future PR may switch to `git log` if desired.

**Files:**
- Modify: `src/decafclaw/skills/newsletter/tools.py`
- Test:   `tests/test_newsletter_skill.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_newsletter_skill.py`:

```python
import os
import time

from decafclaw.skills.newsletter.tools import newsletter_list_vault_changes


@pytest.mark.asyncio
async def test_list_vault_changes_window(ctx, tmp_path):
    vault = tmp_path / "vault"
    (vault / "agent").mkdir(parents=True)
    ctx.config.vault_root = vault

    recent = vault / "agent" / "recent.md"
    recent.write_text("hello")

    old = vault / "agent" / "old.md"
    old.write_text("world")
    old_ts = time.time() - (48 * 3600)
    os.utime(old, (old_ts, old_ts))

    result = await newsletter_list_vault_changes(ctx, hours=24)
    paths = {r["path"] for r in result}
    assert "agent/recent.md" in paths
    assert "agent/old.md" not in paths


@pytest.mark.asyncio
async def test_list_vault_changes_empty_vault(ctx, tmp_path):
    ctx.config.vault_root = tmp_path / "nonexistent"
    result = await newsletter_list_vault_changes(ctx, hours=24)
    assert result == []
```

- [ ] **Step 2: Run tests, confirm failure**

```bash
uv run pytest tests/test_newsletter_skill.py -v -k list_vault_changes
```

Expected: FAIL (`newsletter_list_vault_changes` not defined).

- [ ] **Step 3: Implement the tool**

Append to `src/decafclaw/skills/newsletter/tools.py`:

```python
async def newsletter_list_vault_changes(ctx, hours: int = 24) -> list[dict]:
    """List vault markdown files modified in the last `hours` (mtime-based).

    Returns a list of {path (str, relative to vault root), mtime (ISO-8601), size (int)}.
    Excludes files under the newsletter output folder (self-references).
    """
    vault_root_raw = getattr(ctx.config, "vault_root", None)
    if not vault_root_raw:
        return []
    vault_root = Path(vault_root_raw)
    if not vault_root.is_dir():
        return []

    cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
    newsletter_folder = None
    if _skill_config is not None:
        newsletter_folder = _skill_config.vault_folder

    out: list[dict] = []
    for path in vault_root.rglob("*.md"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff_ts:
            continue
        rel = path.relative_to(vault_root).as_posix()
        if newsletter_folder and rel.startswith(newsletter_folder.rstrip("/") + "/"):
            continue
        out.append({
            "path": rel,
            "mtime": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
            "size": path.stat().st_size,
        })
    out.sort(key=lambda r: r["mtime"])
    return out
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
uv run pytest tests/test_newsletter_skill.py -v -k list_vault_changes
```

Expected: PASS.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint && make typecheck
git add src/decafclaw/skills/newsletter/tools.py tests/test_newsletter_skill.py
git commit -m "feat(newsletter): list_vault_changes tool (#283)"
```

---

## Task 4: `newsletter_publish` — interactive branch

The tool has two modes, split by `ctx.task_mode`. Start with the interactive path (simpler: return markdown as tool result, zero side effects).

**Files:**
- Modify: `src/decafclaw/skills/newsletter/tools.py`
- Test:   `tests/test_newsletter_skill.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_newsletter_skill.py`:

```python
from decafclaw.media import ToolResult
from decafclaw.skills.newsletter.tools import newsletter_publish


@pytest.mark.asyncio
async def test_publish_interactive_returns_markdown(ctx, tmp_path):
    ctx.config.workspace_path = tmp_path
    ctx.config.vault_root = tmp_path / "vault"
    ctx.task_mode = ""  # interactive

    result = await newsletter_publish(ctx, markdown="# hello\n\nbody")
    assert isinstance(result, ToolResult)
    assert result.text == "# hello\n\nbody"
    # No side effects
    assert not (tmp_path / "newsletter" / "archive").exists()
    assert not (tmp_path / "newsletter" / "last_run.json").exists()
```

- [ ] **Step 2: Run test, confirm failure**

```bash
uv run pytest tests/test_newsletter_skill.py -v -k publish_interactive
```

Expected: FAIL (`newsletter_publish` not defined).

- [ ] **Step 3: Implement stub tool with only the interactive branch**

Append to `src/decafclaw/skills/newsletter/tools.py`:

```python
from decafclaw.media import ToolResult


async def newsletter_publish(
    ctx,
    markdown: str,
    subject_hint: str | None = None,
    has_content: bool = True,
) -> ToolResult:
    """Publish the composed newsletter.

    If invoked interactively (`ctx.task_mode != "scheduled"`), returns the markdown
    verbatim as the tool result — no archive, no delivery, no state change.

    If invoked under a scheduled run, archives locally, delivers to all enabled
    targets, and advances `last_run.json`. See scheduled-branch tasks for detail.
    """
    if ctx.task_mode != "scheduled":
        return ToolResult(text=markdown)

    # Scheduled branch — filled in by Tasks 5–8.
    raise NotImplementedError("scheduled branch not yet implemented")
```

- [ ] **Step 4: Run test, confirm pass**

```bash
uv run pytest tests/test_newsletter_skill.py -v -k publish_interactive
```

Expected: PASS.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint && make typecheck
git add src/decafclaw/skills/newsletter/tools.py tests/test_newsletter_skill.py
git commit -m "feat(newsletter): publish tool — interactive branch (#283)"
```

---

## Task 5: `newsletter_publish` — scheduled archive + last_run

Scheduled runs always write an archive file (even for `has_content=False`) and advance `last_run.json`. Delivery (email, vault page) is added in Tasks 6–7 and gated by `has_content=True`.

**Files:**
- Modify: `src/decafclaw/skills/newsletter/tools.py`
- Test:   `tests/test_newsletter_skill.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_newsletter_skill.py`:

```python
@pytest.mark.asyncio
async def test_publish_scheduled_writes_archive_and_advances_state(ctx, tmp_path):
    ctx.config.workspace_path = tmp_path
    ctx.config.vault_root = tmp_path / "vault"
    ctx.task_mode = "scheduled"

    # No email, no vault target — just verify archive + last_run
    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(email_enabled=False, vault_page_enabled=False)

    result = await newsletter_publish(ctx, markdown="# hi\n\nnews", subject_hint="today")
    assert isinstance(result, ToolResult)

    archive_dir = tmp_path / "newsletter" / "archive"
    archive_files = list(archive_dir.glob("*.md"))
    assert len(archive_files) == 1
    assert archive_files[0].read_text() == "# hi\n\nnews"

    last_run = (tmp_path / "newsletter" / "last_run.json")
    assert last_run.exists()
    data = json.loads(last_run.read_text())
    assert "last_run_utc" in data
    assert "window_end_utc" in data


@pytest.mark.asyncio
async def test_publish_scheduled_empty_stub(ctx, tmp_path):
    ctx.config.workspace_path = tmp_path
    ctx.config.vault_root = tmp_path / "vault"
    ctx.task_mode = "scheduled"

    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(email_enabled=False, vault_page_enabled=False)

    result = await newsletter_publish(ctx, markdown="", has_content=False)
    assert isinstance(result, ToolResult)

    archive_dir = tmp_path / "newsletter" / "archive"
    archive_files = list(archive_dir.glob("*.md"))
    assert len(archive_files) == 1
    assert "nothing to report" in archive_files[0].read_text().lower()

    last_run = (tmp_path / "newsletter" / "last_run.json")
    assert last_run.exists()


@pytest.mark.asyncio
async def test_publish_scheduled_archive_suffix_on_conflict(ctx, tmp_path):
    ctx.config.workspace_path = tmp_path
    ctx.config.vault_root = tmp_path / "vault"
    ctx.task_mode = "scheduled"
    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(email_enabled=False, vault_page_enabled=False)

    await newsletter_publish(ctx, markdown="a")
    await newsletter_publish(ctx, markdown="b")

    archive_dir = tmp_path / "newsletter" / "archive"
    names = sorted(p.name for p in archive_dir.glob("*.md"))
    assert len(names) == 2
    # Second write should be suffixed -1
    assert any("-1.md" in n for n in names)
```

- [ ] **Step 2: Run tests, confirm failure**

```bash
uv run pytest tests/test_newsletter_skill.py -v -k publish_scheduled
```

Expected: FAIL (`NotImplementedError`).

- [ ] **Step 3: Implement the scheduled branch (archive + last_run)**

Replace the scheduled-branch `raise NotImplementedError(...)` in `newsletter_publish` with the full implementation:

```python
import os

# ... inside newsletter_publish, after `if ctx.task_mode != "scheduled":` block:
    now = datetime.now(timezone.utc)
    workspace = Path(ctx.config.workspace_path)
    newsletter_dir = workspace / "newsletter"
    archive_dir = newsletter_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    if has_content:
        archive_body = markdown
    else:
        archive_body = f"# {now.strftime('%Y-%m-%d')}\n\n_Nothing to report._\n"

    # Date-suffixed filename with collision handling
    date_str = now.strftime("%Y-%m-%d")
    archive_path = archive_dir / f"{date_str}.md"
    suffix = 0
    while archive_path.exists():
        suffix += 1
        archive_path = archive_dir / f"{date_str}-{suffix}.md"
    archive_path.write_text(archive_body)

    # Advance state
    last_run_path = newsletter_dir / "last_run.json"
    last_run_path.write_text(json.dumps({
        "last_run_utc": now.isoformat(),
        "window_end_utc": now.isoformat(),
    }))

    # Delivery (Tasks 6–7) — stub for now
    delivered_targets: list[str] = []

    summary_text = (
        f"Newsletter archived to {archive_path.name}. "
        f"Delivered to: {', '.join(delivered_targets) if delivered_targets else 'none'}."
    )
    return ToolResult(text=summary_text, data={
        "archive_path": str(archive_path),
        "has_content": has_content,
        "delivered_targets": delivered_targets,
    })
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
uv run pytest tests/test_newsletter_skill.py -v -k publish
```

Expected: all PASS.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint && make typecheck
git add src/decafclaw/skills/newsletter/tools.py tests/test_newsletter_skill.py
git commit -m "feat(newsletter): publish tool — scheduled archive + state (#283)"
```

---

## Task 6: Email delivery

**Files:**
- Modify: `src/decafclaw/skills/newsletter/tools.py`
- Test:   `tests/test_newsletter_skill.py`

- [ ] **Step 1: Write failing test with monkey-patched send_mail**

Append to `tests/test_newsletter_skill.py`:

```python
@pytest.mark.asyncio
async def test_publish_scheduled_email_delivery(ctx, tmp_path, monkeypatch):
    ctx.config.workspace_path = tmp_path
    ctx.config.vault_root = tmp_path / "vault"
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
    ctx.config.workspace_path = tmp_path
    ctx.config.vault_root = tmp_path / "vault"
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
```

- [ ] **Step 2: Run tests, confirm failure**

```bash
uv run pytest tests/test_newsletter_skill.py -v -k "publish_scheduled_email"
```

Expected: FAIL (email delivery not implemented; tests expect `calls` populated).

- [ ] **Step 3: Import send_mail and implement email delivery**

Add import at the top of `src/decafclaw/skills/newsletter/tools.py`:

```python
from decafclaw.mail import send_mail
```

Modify `newsletter_publish` — replace the `delivered_targets: list[str] = []` stub with:

```python
    delivered_targets: list[str] = []
    if has_content and _skill_config is not None:
        # Email delivery
        if _skill_config.email_enabled and _skill_config.email_recipients:
            subject_suffix = subject_hint or now.strftime("%Y-%m-%d")
            subject = f"{_skill_config.email_subject_prefix} {subject_suffix}".strip()
            try:
                await send_mail(
                    ctx.config,
                    to=list(_skill_config.email_recipients),
                    subject=subject,
                    body=markdown,
                )
                delivered_targets.append("email")
            except Exception as exc:  # noqa: BLE001 — isolation boundary
                log.warning("Newsletter email delivery failed: %s", exc)
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
uv run pytest tests/test_newsletter_skill.py -v -k publish
```

Expected: all PASS.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint && make typecheck
git add src/decafclaw/skills/newsletter/tools.py tests/test_newsletter_skill.py
git commit -m "feat(newsletter): email delivery target (#283)"
```

---

## Task 7: Vault-page delivery

Writes `{vault_root}/{vault_folder}/YYYY-MM-DD.md` directly (no embedding index — newsletters are a rolling log, not reference material, per the same rationale as the notification vault-page channel).

**Files:**
- Modify: `src/decafclaw/skills/newsletter/tools.py`
- Test:   `tests/test_newsletter_skill.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_newsletter_skill.py`:

```python
@pytest.mark.asyncio
async def test_publish_scheduled_vault_page_delivery(ctx, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ctx.config.workspace_path = tmp_path
    ctx.config.vault_root = vault
    ctx.task_mode = "scheduled"

    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(
        email_enabled=False,
        vault_page_enabled=True,
        vault_folder="agent/journal/newsletters",
    )

    result = await newsletter_publish(ctx, markdown="# today\n\nstuff")

    vault_files = list((vault / "agent" / "journal" / "newsletters").glob("*.md"))
    assert len(vault_files) == 1
    assert vault_files[0].read_text() == "# today\n\nstuff"
    assert "vault_page" in result.data["delivered_targets"]


@pytest.mark.asyncio
async def test_publish_scheduled_vault_page_conflict_suffix(ctx, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ctx.config.workspace_path = tmp_path
    ctx.config.vault_root = vault
    ctx.task_mode = "scheduled"

    from decafclaw.skills.newsletter import tools as m
    m._skill_config = SkillConfig(
        email_enabled=False,
        vault_page_enabled=True,
        vault_folder="agent/journal/newsletters",
    )
    await newsletter_publish(ctx, markdown="a")
    await newsletter_publish(ctx, markdown="b")

    vault_files = sorted(p.name for p in
                         (vault / "agent" / "journal" / "newsletters").glob("*.md"))
    assert len(vault_files) == 2
    assert any("-1.md" in n for n in vault_files)
```

- [ ] **Step 2: Run tests, confirm failure**

```bash
uv run pytest tests/test_newsletter_skill.py -v -k publish_scheduled_vault
```

Expected: FAIL (vault_files list is empty or vault_page not in delivered_targets).

- [ ] **Step 3: Implement vault page delivery**

Inside `newsletter_publish`, append to the `if has_content and _skill_config is not None:` block (after email):

```python
        # Vault page delivery
        if _skill_config.vault_page_enabled:
            vault_root_raw = getattr(ctx.config, "vault_root", None)
            if vault_root_raw:
                try:
                    vault_root = Path(vault_root_raw)
                    target_dir = vault_root / _skill_config.vault_folder
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target_path = target_dir / f"{date_str}.md"
                    suffix = 0
                    while target_path.exists():
                        suffix += 1
                        target_path = target_dir / f"{date_str}-{suffix}.md"
                    target_path.write_text(markdown)
                    delivered_targets.append("vault_page")
                except Exception as exc:  # noqa: BLE001
                    log.warning("Newsletter vault-page delivery failed: %s", exc)
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
uv run pytest tests/test_newsletter_skill.py -v -k publish
```

Expected: all PASS.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
make lint && make typecheck
git add src/decafclaw/skills/newsletter/tools.py tests/test_newsletter_skill.py
git commit -m "feat(newsletter): vault-page delivery target (#283)"
```

---

## Task 8: Per-target failure isolation

We already wrapped each target in `try/except`. Now add explicit test coverage that a failure in one target does NOT block the other target or the archive / state advance.

**Files:**
- Test: `tests/test_newsletter_skill.py`

- [ ] **Step 1: Write failing (or already-passing) tests asserting isolation**

Append to `tests/test_newsletter_skill.py`:

```python
@pytest.mark.asyncio
async def test_publish_target_failure_isolation(ctx, tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    ctx.config.workspace_path = tmp_path
    ctx.config.vault_root = vault
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

    # Archive still happened
    archive_files = list((tmp_path / "newsletter" / "archive").glob("*.md"))
    assert len(archive_files) == 1
    # Vault page still delivered
    vault_files = list((vault / "agent" / "journal" / "newsletters").glob("*.md"))
    assert len(vault_files) == 1
    # Email NOT in delivered_targets
    assert "email" not in result.data["delivered_targets"]
    assert "vault_page" in result.data["delivered_targets"]
    # last_run still advanced
    assert (tmp_path / "newsletter" / "last_run.json").exists()
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/test_newsletter_skill.py -v -k target_failure_isolation
```

Expected: PASS (the `try/except` blocks added in Tasks 6–7 should already satisfy this). If it FAILS, fix the isolation logic in `tools.py` until it passes. Do not proceed with a broken isolation path.

- [ ] **Step 3: Commit**

```bash
git add tests/test_newsletter_skill.py
git commit -m "test(newsletter): per-target failure isolation (#283)"
```

---

## Task 9: SKILL.md composition prompt body

Fill in the editorial prompt. This is the skill's core "editorial voice" — it tells the agent how to compose the newsletter on both scheduled and interactive runs.

**Files:**
- Modify: `src/decafclaw/skills/newsletter/SKILL.md`

- [ ] **Step 1: Replace the placeholder body**

Replace the body of `src/decafclaw/skills/newsletter/SKILL.md` (everything below the frontmatter) with:

```markdown
# Newsletter

You are composing the periodic newsletter — a narrative recap of what I got up to on my own, without direct user involvement, during the last 24 hours. This is NOT a status report; it's a conversational retelling of the autonomous threads I was pulling on. It gets delivered by email and/or filed into the vault.

## How to compose

1. Call `newsletter_list_scheduled_activity(hours=24)` to see what my scheduled tasks did. Each entry gives you the skill name, when it ran, what it reported at the end, and which vault pages it wrote. Skip entries with empty final messages — they didn't have anything coherent to say.

2. Call `newsletter_list_vault_changes(hours=24)` to see which vault pages moved (new or modified). Use this to enrich the narrative ("while gardening, I noticed X and rewrote [[Some Page]]") and to surface interesting activity the scheduled reports didn't themselves mention.

3. Group related entries into a flowing narrative. A single `dream` cycle plus the pages it touched is ONE story, not two bullet items. Prune things that would be boring to read — "heartbeat OK" class updates don't belong here.

4. Apply the SOUL voice — conversational, curious, reflective. Use first person. Not corporate. Not bullet-point-heavy. A couple of sections with real paragraphs is better than 15 bullets.

5. Link to vault pages using Obsidian `[[wiki-link]]` syntax when referring to pages I touched. They'll render correctly when the newsletter is filed to the vault; email readers will see the raw `[[...]]` text, which is fine — it signals a reference without needing a URL.

6. Include a stats line at the bottom: "Pages created/modified: N. Scheduled tasks that ran: M." Plain and brief.

7. Derive a short `subject_hint` — a single-line highlight of the period ("dream woke up early; 3 new vault notes on foo"). This becomes part of the email subject.

## How to finish

- If the window had real activity worth narrating, call `newsletter_publish(markdown=<your_composed_markdown>, subject_hint=<your_hint>)` — default `has_content=True`.

- If the gathered activity is empty or trivial (no final messages worth surfacing, no notable vault changes), call `newsletter_publish(markdown="", has_content=False)`. This records a "ran and found nothing" stub without dispatching delivery.

- Only ONE `newsletter_publish` call per run. It's the final step.

## Notes

- When this skill is invoked as `!newsletter` / `/newsletter` (interactive, not scheduled), `newsletter_publish` automatically short-circuits — it just returns your composed markdown as the tool result, with no delivery or archive side effects. The user sees it inline. You still compose the same way; nothing changes in your process.

- Do not include raw tool traces, conversation IDs, or internal plumbing detail. This is a human-facing report.

- Do not mention yourself summarizing — write the narrative, not commentary on writing it.
```

- [ ] **Step 2: Verify skill still loads and parses frontmatter**

```bash
uv run pytest tests/test_newsletter_skill.py -v
```

Expected: all tests still PASS (body change doesn't affect tool behavior).

- [ ] **Step 3: Commit**

```bash
git add src/decafclaw/skills/newsletter/SKILL.md
git commit -m "feat(newsletter): editorial composition prompt (#283)"
```

---

## Task 10: End-to-end smoke test

A minimal integration test: given fake scheduled-task archives and vault changes, invoke the three tools in sequence as an LLM would and verify the complete scheduled flow.

**Files:**
- Test: `tests/test_newsletter_skill.py`

- [ ] **Step 1: Write the end-to-end test**

Append to `tests/test_newsletter_skill.py`:

```python
@pytest.mark.asyncio
async def test_end_to_end_scheduled_publish(ctx, tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "agent").mkdir(parents=True)
    ctx.config.workspace_path = tmp_path
    ctx.config.vault_root = vault
    ctx.task_mode = "scheduled"

    # Seed: one scheduled-task conversation and one recent vault page
    now = datetime.now(timezone.utc)
    _write_sched_conv(tmp_path, "dream", now - timedelta(hours=2), [
        {"role": "assistant", "content": "Dreamed. 2 patterns surfaced.",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "vault_write",
                                      "arguments": json.dumps({"page": "Pattern A"})}}]},
        {"role": "assistant", "content": "Dreamed. 2 patterns surfaced."},
    ])
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
    activity = await newsletter_list_scheduled_activity(ctx, hours=24)
    changes = await newsletter_list_vault_changes(ctx, hours=24)
    assert len(activity) == 1
    assert len(changes) >= 1

    composed = (
        f"# Newsletter\n\nDream surfaced 2 patterns including [[Pattern A]].\n"
        f"\nPages modified: {len(changes)}. Tasks run: {len(activity)}.\n"
    )
    result = await newsletter_publish(ctx, markdown=composed, subject_hint="dreams")

    # Archive written
    assert len(list((tmp_path / "newsletter" / "archive").glob("*.md"))) == 1
    # Vault page delivered
    assert len(list((vault / "agent" / "journal" / "newsletters").glob("*.md"))) == 1
    # Email sent
    assert len(sent) == 1
    assert "dreams" in sent[0]["subject"]
    # State advanced
    assert (tmp_path / "newsletter" / "last_run.json").exists()
    # Result data accurate
    assert set(result.data["delivered_targets"]) == {"email", "vault_page"}
```

- [ ] **Step 2: Run the integration test**

```bash
uv run pytest tests/test_newsletter_skill.py::test_end_to_end_scheduled_publish -v
```

Expected: PASS.

- [ ] **Step 3: Run the full test file + the whole suite**

```bash
uv run pytest tests/test_newsletter_skill.py -v
make test
```

Expected: all PASS; no regressions in other tests.

- [ ] **Step 4: Commit**

```bash
git add tests/test_newsletter_skill.py
git commit -m "test(newsletter): end-to-end scheduled publish (#283)"
```

---

## Task 11: Docs + CLAUDE.md + docs/index.md

**Files:**
- Create: `docs/newsletter.md`
- Modify: `docs/index.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write `docs/newsletter.md`**

Create `docs/newsletter.md`:

```markdown
# Newsletter

The newsletter is a periodic narrative recap of autonomous agent activity — work the agent did on its own, via scheduled skills, without direct user involvement in the conversation.

## How it works

A bundled scheduled skill (`newsletter`) runs daily at 7am (cron `0 7 * * *`). It:

1. Lists scheduled-task conversations from the last 24 hours by reading `workspace/conversations/schedule-*.jsonl` and filtering out interactive, heartbeat, and child-agent conversations — plus its own runs.
2. Lists vault pages added or modified in the same window.
3. Composes a conversational narrative in SOUL voice using those two inputs.
4. Writes a local archive to `workspace/newsletter/archive/YYYY-MM-DD.md`.
5. Delivers to each enabled channel: email and/or a dated vault page at `{vault_root}/agent/journal/newsletters/YYYY-MM-DD.md`.
6. Advances `workspace/newsletter/last_run.json`.

## Configuration

Under `config.skills.newsletter` (or via `NEWSLETTER_*` env vars):

| Field | Default | Description |
| --- | --- | --- |
| `window_hours` | `24` | How far back to look |
| `email_enabled` | `false` | Dispatch by email |
| `email_recipients` | `[]` | Destination addresses |
| `email_subject_prefix` | `"[decafclaw newsletter]"` | Prepended to the subject line |
| `vault_page_enabled` | `true` | Write a dated page under the vault |
| `vault_folder` | `"agent/journal/newsletters"` | Relative to vault root |

Email uses `mail.py` directly (bypasses the `send_email` tool's confirmation gate) — the `email_recipients` list is the trust boundary.

## `!newsletter` / `/newsletter`

Invoke interactively in any chat to peek at what a newsletter *would* look like right now, without disturbing the scheduled cadence. The same composition path runs, but the `newsletter_publish` tool short-circuits: no archive, no email, no vault page, no state advance. The markdown is returned as the tool result and shown in the conversation.

## Relationship to other subsystems

- **Notifications** are small, typed, per-event records. Newsletters are narrative multi-paragraph recaps. They share nothing in code — different subsystems, different semantics.
- **Heartbeat** reports operational status ("is everything OK?"). Newsletters report on what the agent *did* on its own. Complementary.
- **Dream consolidation** is itself an input to the newsletter — dream's own scheduled runs get summarized alongside other scheduled activity.

## Phase 2+ (future)

- Mattermost channel delivery (lands with a reusable `mattermost_channel` notification adapter).
- Hourly / weekly cadences.
- Time-range arguments on `!newsletter` (e.g., `!newsletter since yesterday`).
```

- [ ] **Step 2: Link from docs/index.md**

Add a line to `docs/index.md` in the appropriate section (alphabetical if the page uses that order — check the file first):

```markdown
- [Newsletter](newsletter.md) — periodic narrative recap of autonomous agent activity
```

- [ ] **Step 3: Add newsletter skill to CLAUDE.md key files**

In `CLAUDE.md`, find the Skills section under `### Skills` in Key files. Insert (in roughly the order of the other skill entries):

```markdown
- `src/decafclaw/skills/newsletter/` — Periodic newsletter publisher: scheduled + `!newsletter` on-demand narrative recap of autonomous activity
```

- [ ] **Step 4: Lint + commit**

```bash
make lint
git add docs/newsletter.md docs/index.md CLAUDE.md
git commit -m "docs(newsletter): feature documentation + index + CLAUDE.md (#283)"
```

---

## Done checklist

After Task 11, verify:

- [ ] `make lint && make typecheck && make test` all green
- [ ] No regressions — run `uv run pytest` across the whole suite one more time
- [ ] `docs/newsletter.md` reflects the implemented behavior exactly (no aspirational text)
- [ ] Each commit compiles + tests pass at HEAD of that commit
- [ ] Branch is still rebased onto latest `origin/main`

Ready for branch self-review and PR.
