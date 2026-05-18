"""Tests for tool_search — keyword search and exact selection.

After PR #547 tool_search distinguishes:
- Non-skill deferred tools (core demoted + MCP) — fetched and made callable.
- Skill-owned tools (hidden by default) — surface the OWNING skill so the
  agent calls activate_skill, not the individual tool.
- Skill-catalog matches (skill name or description) — surface the skill.
"""

import pytest

from decafclaw.skills import SkillInfo
from decafclaw.tools.search_tools import tool_search
from decafclaw.tools.tool_registry import get_fetched_tools


def _make_tool_def(name, description="A tool."):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


# Pool of plain (non-skill) deferred tools — fetched as today.
SAMPLE_POOL = [
    _make_tool_def("workspace_edit", "Edit a file by exact string replacement."),
    _make_tool_def("workspace_search", "Search for a regex pattern across files."),
    _make_tool_def("mcp__playwright__click", "Click an element on the page."),
    _make_tool_def("mcp__playwright__navigate", "Navigate to a URL."),
    _make_tool_def("checklist_create", "Create a checklist of steps."),
]


@pytest.fixture
def search_ctx(ctx):
    """Ctx with a deferred pool set and no skills."""
    ctx.tools.deferred_pool = list(SAMPLE_POOL)
    ctx.config.skill_tool_owners = {}
    ctx.config.discovered_skills = []
    return ctx


class TestKeywordSearchNonSkillTools:
    def test_matches_name(self, search_ctx):
        result = tool_search(search_ctx, "workspace")
        assert "workspace_edit" in result.text
        assert "workspace_search" in result.text

    def test_matches_description(self, search_ctx):
        result = tool_search(search_ctx, "regex")
        assert "workspace_search" in result.text

    def test_case_insensitive(self, search_ctx):
        result = tool_search(search_ctx, "WORKSPACE")
        assert "workspace_edit" in result.text

    def test_respects_max_results(self, search_ctx):
        result = tool_search(search_ctx, "workspace", max_results=1)
        assert "1 tool(s) loaded" in result.text

    def test_no_matches(self, search_ctx):
        result = tool_search(search_ctx, "xyznonexistent")
        assert "No matches" in result.text

    def test_fetched_tools_updated(self, search_ctx):
        tool_search(search_ctx, "workspace")
        fetched = get_fetched_tools(search_ctx)
        assert "workspace_edit" in fetched
        assert "workspace_search" in fetched


class TestExactSelectionNonSkillTools:
    def test_select_by_name(self, search_ctx):
        result = tool_search(search_ctx, "select:workspace_edit,checklist_create")
        assert "workspace_edit" in result.text
        assert "checklist_create" in result.text
        assert "2 tool(s) loaded" in result.text

    def test_select_single(self, search_ctx):
        result = tool_search(search_ctx, "select:workspace_edit")
        assert "workspace_edit" in result.text
        assert "1 tool(s) loaded" in result.text

    def test_select_unknown_name(self, search_ctx):
        result = tool_search(search_ctx, "select:workspace_edit,nonexistent_tool")
        assert "workspace_edit" in result.text
        assert "Not found: nonexistent_tool" in result.text

    def test_select_all_unknown(self, search_ctx):
        result = tool_search(search_ctx, "select:fake1,fake2")
        assert "No matches" in result.text

    def test_fetched_tools_updated(self, search_ctx):
        tool_search(search_ctx, "select:mcp__playwright__click")
        fetched = get_fetched_tools(search_ctx)
        assert "mcp__playwright__click" in fetched


class TestEmptyPool:
    def test_no_pool(self, ctx):
        ctx.config.skill_tool_owners = {}
        ctx.config.discovered_skills = []
        result = tool_search(ctx, "anything")
        assert "No matches" in result.text

    def test_empty_pool(self, ctx):
        ctx.tools.deferred_pool = []
        ctx.config.skill_tool_owners = {}
        ctx.config.discovered_skills = []
        result = tool_search(ctx, "anything")
        assert "No matches" in result.text


class TestSkillResults:
    """Skill catalog matches and hidden-tool-name guesses surface the
    owning skill rather than the individual tool."""

    @pytest.fixture
    def search_ctx_with_skill(self, ctx, tmp_path):
        skill = SkillInfo(
            name="writing-clearly",
            description="Edit prose for clarity using Strunk's Elements of Style.",
            location=tmp_path / "writing-clearly",
            trust_tier="extra",
        )
        skill.location.mkdir()
        ctx.config.discovered_skills = [skill]
        ctx.config.skill_tool_owners = {"edit_with_strunk": "writing-clearly"}
        ctx.tools.deferred_pool = [
            _make_tool_def("edit_with_strunk", "Revise a prose draft for clarity."),
            _make_tool_def("workspace_edit", "Edit a file."),
        ]
        return ctx

    def test_skill_name_match_returns_skill(self, search_ctx_with_skill):
        result = tool_search(search_ctx_with_skill, "writing-clearly")
        assert "writing-clearly" in result.text
        assert "skill(s) matched" in result.text
        assert "activate_skill" in result.text

    def test_skill_description_match_returns_skill(self, search_ctx_with_skill):
        result = tool_search(search_ctx_with_skill, "clarity")
        assert "writing-clearly" in result.text

    def test_hidden_tool_name_returns_owning_skill(
        self, search_ctx_with_skill,
    ):
        """An agent that recalls a hidden skill-tool name gets routed to
        the owning skill, not the tool — preserves the progressive-
        disclosure invariant."""
        result = tool_search(search_ctx_with_skill, "edit_with_strunk")
        assert "writing-clearly" in result.text
        assert "activate_skill" in result.text
        # The raw tool schema must NOT be in the result.
        assert '"name": "edit_with_strunk"' not in result.text
        # The tool must not have been added to fetched.
        assert "edit_with_strunk" not in get_fetched_tools(search_ctx_with_skill)

    def test_select_hidden_tool_name_returns_owning_skill(
        self, search_ctx_with_skill,
    ):
        result = tool_search(search_ctx_with_skill, "select:edit_with_strunk")
        assert "writing-clearly" in result.text
        assert "skill(s) matched" in result.text
        assert "edit_with_strunk" not in get_fetched_tools(search_ctx_with_skill)

    def test_mixed_results(self, search_ctx_with_skill):
        """A query matching both a skill and a non-skill tool surfaces both."""
        # "edit" matches edit_with_strunk's description (clarity)? No —
        # "edit" appears in workspace_edit's description ("Edit a file").
        # And it also appears in the writing-clearly description ("Edit prose").
        result = tool_search(search_ctx_with_skill, "edit")
        assert "writing-clearly" in result.text  # skill match
        assert "workspace_edit" in result.text  # plain tool match

    def test_already_activated_skill_not_returned(
        self, search_ctx_with_skill,
    ):
        """A skill already in ctx.skills.activated isn't surfaced — its
        tools are already in the active set, so re-activating is noise."""
        search_ctx_with_skill.skills.activated.add("writing-clearly")
        result = tool_search(search_ctx_with_skill, "writing-clearly")
        assert "No matches" in result.text
