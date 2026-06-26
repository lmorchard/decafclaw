"""blog-ideas contrib skill — deterministic ISO-week helper for the living weekly page.

`current_time` returns a plain timestamp with no ISO-week number, and the
living-page model needs a correct, stable week key (a wrong key duplicates the
page instead of refining it). ISO-week math — especially around year boundaries
and "N weeks ago" — is exactly the deterministic mechanic that belongs in code,
so the skill exposes one helper and the LLM never computes weeks itself.

The week is computed in UTC to match the scheduler: `schedules.py` evaluates
cron via croniter on a UTC base, so a UTC `now` keeps the page key aligned with
when the daily run actually fires, independent of the host's local timezone.
"""

import logging
from datetime import datetime, timedelta, timezone

from decafclaw.media import ToolResult

log = logging.getLogger(__name__)

PAGE_FOLDER = "agent/pages/blog-ideas"


def compute_week(now: datetime, offset_weeks: int = 0) -> dict:
    """ISO-week identity for the blog-ideas page, offset by whole weeks.

    Returns a dict with:
      - ``week``:        ISO week key, e.g. ``"2026-W26"``. Uses the ISO year,
                         which differs from the calendar year near boundaries.
      - ``monday``:      the offset week's Monday, ``"YYYY-MM-DD"``.
      - ``days_so_far``: ISO weekday of ``now`` (1=Mon .. 7=Sun). Always reflects
                         ``now`` — it measures progress into the current week and
                         is meaningless for non-zero ``offset_weeks`` (used only
                         to size the gather window when offset is 0).
      - ``page_path``:   vault path of the offset week's page.
    """
    shifted = now + timedelta(weeks=offset_weeks)
    iso = shifted.isocalendar()  # (year, week, weekday)
    week_key = f"{iso.year}-W{iso.week:02d}"
    monday = shifted - timedelta(days=iso.weekday - 1)
    return {
        "week": week_key,
        "monday": monday.strftime("%Y-%m-%d"),
        "days_so_far": now.isocalendar().weekday,
        "page_path": f"{PAGE_FOLDER}/{week_key}.md",
    }


def blog_ideas_week(ctx, offset_weeks: int = 0) -> ToolResult:
    """Return the ISO-week identity + page path for the living weekly page."""
    # UTC, not local — the schedule fires on a UTC cron base; see module docstring.
    info = compute_week(datetime.now(timezone.utc), offset_weeks)
    return ToolResult(
        text=(
            f"{info['week']} (week of {info['monday']}, "
            f"day {info['days_so_far']}/7) -> {info['page_path']}"
        ),
        data=info,
    )


TOOLS = {"blog_ideas_week": blog_ideas_week}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "blog_ideas_week",
            "description": (
                "Return the ISO-week identity for the living weekly blog-ideas "
                "page: the week key (e.g. '2026-W26'), that week's Monday, how "
                "many days into the week it is now (1=Mon..7=Sun), and the vault "
                "page_path. Pass offset_weeks=-1 for last week, -2 for two weeks "
                "ago, etc. ALWAYS call this to get the page path — NEVER "
                "hand-compute ISO week numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "offset_weeks": {
                        "type": "integer",
                        "description": (
                            "Whole-week offset from the current week. 0 = this "
                            "week (default), -1 = last week, -2 = two weeks ago."
                        ),
                    },
                },
            },
        },
    },
]
