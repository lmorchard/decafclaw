"""Unit tests for the blog-ideas contrib skill's ISO-week helper.

Loads tools.py via importlib — mirroring the production skill loader — because
the skill directory (`blog-ideas`) is not an importable Python package.
"""

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

_THIS_DIR = Path(__file__).parent
_tools_spec = importlib.util.spec_from_file_location(
    "decafclaw_contrib_blog_ideas_tools", _THIS_DIR / "tools.py"
)
assert _tools_spec is not None and _tools_spec.loader is not None
blog_ideas_tools = importlib.util.module_from_spec(_tools_spec)
sys.modules["decafclaw_contrib_blog_ideas_tools"] = blog_ideas_tools
_tools_spec.loader.exec_module(blog_ideas_tools)

compute_week = blog_ideas_tools.compute_week


def test_midweek_thursday():
    # 2026-06-25 is a Thursday in ISO week 2026-W26 (Monday 2026-06-22).
    info = compute_week(datetime(2026, 6, 25, 15, 0, 0))
    assert info["week"] == "2026-W26"
    assert info["monday"] == "2026-06-22"
    assert info["days_so_far"] == 4
    assert info["page_path"] == "agent/pages/blog-ideas/2026-W26.md"


def test_monday_is_day_one():
    info = compute_week(datetime(2026, 6, 22, 6, 0, 0))
    assert info["week"] == "2026-W26"
    assert info["monday"] == "2026-06-22"
    assert info["days_so_far"] == 1


def test_sunday_is_day_seven():
    info = compute_week(datetime(2026, 6, 28, 23, 0, 0))
    assert info["week"] == "2026-W26"
    assert info["monday"] == "2026-06-22"
    assert info["days_so_far"] == 7


def test_prior_week_offset():
    # offset_weeks=-1 from a Thursday → previous ISO week, its Monday and path.
    info = compute_week(datetime(2026, 6, 25, 15, 0, 0), offset_weeks=-1)
    assert info["week"] == "2026-W25"
    assert info["monday"] == "2026-06-15"
    assert info["page_path"] == "agent/pages/blog-ideas/2026-W25.md"
    # days_so_far still reflects "now" (Thursday), independent of offset.
    assert info["days_so_far"] == 4


def test_iso_year_boundary():
    # 2021-01-01 is a Friday belonging to ISO week 2020-W53 (Monday 2020-12-28).
    # The key must use the ISO year (2020), not the calendar year (2021).
    info = compute_week(datetime(2021, 1, 1, 9, 0, 0))
    assert info["week"] == "2020-W53"
    assert info["monday"] == "2020-12-28"
    assert info["days_so_far"] == 5
    assert info["page_path"] == "agent/pages/blog-ideas/2020-W53.md"


def test_tool_registered():
    # The skill exposes blog_ideas_week through both registries.
    tool_names = {td["function"]["name"] for td in blog_ideas_tools.TOOL_DEFINITIONS}
    assert "blog_ideas_week" in tool_names, f"got {sorted(tool_names)}"
    assert "blog_ideas_week" in blog_ideas_tools.TOOLS
