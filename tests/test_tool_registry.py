"""Tests for tool registry — priority classification, token estimation, catalog."""

from decafclaw.tools import TOOL_DEFINITIONS
from decafclaw.tools.tool_registry import (
    Priority,
    add_fetched_tools,
    build_deferred_list_text,
    classify_tools,
    estimate_tool_tokens,
    get_critical_names,
    get_description,
    get_fetched_tools,
    get_priority,
)


def _make_tool_def(name, description="A tool.", params=None, priority=None):
    td = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": params or {"type": "object", "properties": {}},
        },
    }
    if priority is not None:
        td["priority"] = priority
    return td


# -- Token estimation ---------------------------------------------------------


class TestEstimateToolTokens:
    def test_empty(self):
        assert estimate_tool_tokens([]) == 0

    def test_single_tool(self):
        td = _make_tool_def("test")
        tokens = estimate_tool_tokens([td])
        assert tokens > 0

    def test_proportional(self):
        small = _make_tool_def("s", "short")
        big = _make_tool_def("b", "a" * 400)
        assert estimate_tool_tokens([big]) > estimate_tool_tokens([small])


# -- get_critical_names (env override + always-loaded skill tools) -------


class TestGetCriticalNames:
    def test_default_empty(self, config):
        """With no env override and no always-loaded skills, returns empty set."""
        config.agent.critical_tools = []
        names = get_critical_names(config)
        assert names == set()

    def test_env_override(self, config):
        config.agent.critical_tools = ["my_custom_tool", "another_one"]
        names = get_critical_names(config)
        assert "my_custom_tool" in names
        assert "another_one" in names


# -- get_priority -------------------------------------------------------------


class TestGetPriority:
    def test_declared_priority_respected(self, config):
        td = _make_tool_def("custom", priority="low")
        assert get_priority(td, config, set()) == "low"

    def test_declared_priority_critical(self, config):
        td = _make_tool_def("custom", priority="critical")
        assert get_priority(td, config, set()) == "critical"

    def test_force_critical_overrides_declaration(self, config):
        """Env override / activation forces critical even if tool declared low."""
        td = _make_tool_def("custom", priority="low")
        assert get_priority(td, config, {"custom"}) == "critical"

    def test_default_normal(self, config):
        """Undeclared tools default to normal (e.g. MCP tools)."""
        td = _make_tool_def("random_tool")
        assert get_priority(td, config, set()) == "normal"

    def test_invalid_priority_falls_through(self, config):
        """Unknown priority value is ignored, falls through to default."""
        td = _make_tool_def("random_tool", priority="bogus")
        assert get_priority(td, config, set()) == "normal"


# -- classify_tools ------------------------------------------------------------


class TestClassifyTools:
    def test_under_budget_all_active(self, config):
        """When tools fit in budget, all are active, none deferred."""
        tools = [_make_tool_def(f"tool_{i}", priority="normal") for i in range(3)]
        config.compaction.max_tokens = 1000000  # huge budget
        active, deferred = classify_tools(tools, config)
        assert len(active) == 3
        assert len(deferred) == 0

    def test_critical_is_hard_floor(self, config):
        """Critical tools are included even when over budget."""
        critical_tools = [
            _make_tool_def(f"crit_{i}", "x" * 200, priority="critical")
            for i in range(5)
        ]
        normal_tools = [
            _make_tool_def(f"norm_{i}", "x" * 200, priority="normal")
            for i in range(5)
        ]
        config.compaction.max_tokens = 100  # tiny budget → 10 token budget

        active, deferred = classify_tools(critical_tools + normal_tools, config)
        active_names = {td["function"]["name"] for td in active}

        # All critical tools are in active set despite budget overrun
        for i in range(5):
            assert f"crit_{i}" in active_names
        # Normal tools are deferred
        for i in range(5):
            assert f"norm_{i}" not in active_names

    def test_normal_fills_budget(self, config):
        """Normal tools are added while under budget."""
        tools = [
            _make_tool_def(f"norm_{i}", "x" * 50, priority="normal")
            for i in range(10)
        ]
        config.compaction.max_tokens = 1000000  # huge budget
        config.agent.max_active_tools = 40

        active, deferred = classify_tools(tools, config)
        assert len(active) == 10
        assert len(deferred) == 0

    def test_low_only_if_room(self, config):
        """Low priority tools deferred when no room after normal."""
        normal_tools = [
            _make_tool_def(f"norm_{i}", "x" * 50, priority="normal")
            for i in range(5)
        ]
        low_tools = [
            _make_tool_def(f"low_{i}", "x" * 50, priority="low")
            for i in range(5)
        ]
        # Budget fits normal but not low
        config.agent.max_active_tools = 5

        active, deferred = classify_tools(normal_tools + low_tools, config)
        active_names = {td["function"]["name"] for td in active}
        deferred_names = {td["function"]["name"] for td in deferred}

        # All normal in active
        for i in range(5):
            assert f"norm_{i}" in active_names
        # All low deferred
        for i in range(5):
            assert f"low_{i}" in deferred_names

    def test_low_included_when_budget_allows(self, config):
        """Low priority tools do make it in if there's room."""
        normal_tools = [
            _make_tool_def(f"norm_{i}", "x" * 50, priority="normal")
            for i in range(2)
        ]
        low_tools = [
            _make_tool_def(f"low_{i}", "x" * 50, priority="low")
            for i in range(2)
        ]
        config.compaction.max_tokens = 1000000  # huge budget

        active, deferred = classify_tools(normal_tools + low_tools, config)
        active_names = {td["function"]["name"] for td in active}
        assert "low_0" in active_names
        assert "low_1" in active_names
        assert deferred == []

    def test_max_active_tools_cap(self, config):
        """max_active_tools limits active set even when under token budget."""
        tools = [
            _make_tool_def(f"norm_{i}", priority="normal") for i in range(50)
        ]
        config.compaction.max_tokens = 1000000  # huge budget
        config.agent.max_active_tools = 10

        active, deferred = classify_tools(tools, config)
        assert len(active) == 10
        assert len(deferred) == 40

    def test_fetched_tools_treated_critical(self, config):
        """Fetched tool names are promoted to critical regardless of declaration."""
        tools = [
            _make_tool_def(f"norm_{i}", "x" * 200, priority="normal")
            for i in range(20)
        ]
        config.compaction.max_tokens = 100  # tight

        active, deferred = classify_tools(
            tools, config, fetched_names={"norm_5"}
        )
        active_names = {td["function"]["name"] for td in active}
        assert "norm_5" in active_names

    def test_skill_tool_names_treated_critical(self, config):
        """Activated skill tools are promoted to critical."""
        tools = [
            _make_tool_def(f"norm_{i}", "x" * 200, priority="normal")
            for i in range(20)
        ]
        config.compaction.max_tokens = 100

        active, deferred = classify_tools(
            tools, config, skill_tool_names={"norm_3"}
        )
        active_names = {td["function"]["name"] for td in active}
        assert "norm_3" in active_names

    def test_env_override_treated_critical(self, config):
        """Env override (via critical_tools) promotes to critical."""
        tools = [
            _make_tool_def(f"norm_{i}", "x" * 200, priority="normal")
            for i in range(20)
        ]
        config.compaction.max_tokens = 100
        config.agent.critical_tools = ["norm_7"]

        active, deferred = classify_tools(tools, config)
        active_names = {td["function"]["name"] for td in active}
        assert "norm_7" in active_names

    def test_input_order_preserved_within_tier(self, config):
        """Within a priority tier, input order is preserved."""
        tools = [
            _make_tool_def("norm_a", priority="normal"),
            _make_tool_def("norm_b", priority="normal"),
            _make_tool_def("norm_c", priority="normal"),
        ]
        config.compaction.max_tokens = 1000000
        active, _ = classify_tools(tools, config)
        names = [td["function"]["name"] for td in active]
        assert names == ["norm_a", "norm_b", "norm_c"]


# -- get_description -----------------------------------------------------------


class TestGetDescription:
    def test_first_sentence(self):
        td = _make_tool_def("t", "First sentence. Second sentence.")
        assert get_description(td) == "First sentence."

    def test_long_description_truncated(self):
        td = _make_tool_def("t", "a" * 200)
        desc = get_description(td)
        assert len(desc) <= 80

    def test_short_description(self):
        td = _make_tool_def("t", "Short desc")
        assert get_description(td) == "Short desc"


# -- build_deferred_list_text --------------------------------------------------


class TestBuildDeferredListText:
    def test_empty(self):
        assert build_deferred_list_text([]) == ""

    def test_core_tools(self):
        core_names = {"workspace_edit", "checklist_create"}
        tools = [
            _make_tool_def("workspace_edit", "Edit a file"),
            _make_tool_def("checklist_create", "Create a checklist"),
        ]
        text = build_deferred_list_text(tools, core_names=core_names)
        assert "### Core" in text
        assert "workspace_edit" in text
        assert "checklist_create" in text

    def test_mcp_tools_grouped(self):
        tools = [
            _make_tool_def("mcp__playwright__click", "Click element"),
            _make_tool_def("mcp__playwright__navigate", "Navigate to URL"),
            _make_tool_def("mcp__slack__send", "Send a message"),
        ]
        text = build_deferred_list_text(tools, core_names=set())
        assert "### Tools from MCP server `playwright`" in text
        assert "### Tools from MCP server `slack`" in text

    def test_skill_tools(self):
        tools = [
            _make_tool_def("vault_read", "Read a file"),
        ]
        text = build_deferred_list_text(tools, core_names=set())
        assert "### Skills" in text
        assert "vault_read" in text

    def test_core_sorted_by_priority_desc_then_name(self):
        """Within Core, priority desc then alpha by name."""
        core_names = {"high_crit", "low_tool", "normal_tool"}
        tools = [
            _make_tool_def("low_tool", "low", priority="low"),
            _make_tool_def("normal_tool", "normal", priority="normal"),
            _make_tool_def("high_crit", "crit", priority="critical"),
        ]
        text = build_deferred_list_text(tools, core_names=core_names)
        core_section = text.split("### Core")[1].split("###")[0]
        # critical should appear before normal, normal before low
        crit_idx = core_section.index("high_crit")
        norm_idx = core_section.index("normal_tool")
        low_idx = core_section.index("low_tool")
        assert crit_idx < norm_idx < low_idx

    def test_mcp_tools_clustered_by_server_in_own_sections(self):
        """MCP tools get one section per server, sorted within by priority+name."""
        tools = [
            _make_tool_def("mcp__github__issue_b", "b", priority="normal"),
            _make_tool_def("mcp__github__issue_a", "a", priority="critical"),
            _make_tool_def("mcp__slack__msg", "s", priority="normal"),
        ]
        text = build_deferred_list_text(tools, core_names=set())
        assert "### Tools from MCP server `github`" in text
        assert "### Tools from MCP server `slack`" in text
        github_section = text.split("### Tools from MCP server `github`")[1].split("###")[0]
        # critical issue_a before normal issue_b
        a_idx = github_section.index("issue_a")
        b_idx = github_section.index("issue_b")
        assert a_idx < b_idx

    def test_skill_tools_cluster_by_source_skill(self):
        """When _source_skill is set, skill tools cluster by skill name."""
        def with_source(td, skill):
            return {**td, "_source_skill": skill}

        tools = [
            with_source(_make_tool_def("tool_z"), "skill_alpha"),
            with_source(_make_tool_def("tool_a"), "skill_beta"),
            with_source(_make_tool_def("tool_b"), "skill_alpha"),
        ]
        text = build_deferred_list_text(tools, core_names=set())
        skills_section = text.split("### Skills")[1].split("###")[0]
        # skill_alpha tools should appear before skill_beta tools
        # within skill_alpha: tool_b before tool_z (alphabetical)
        b_idx = skills_section.index("tool_b")
        z_idx = skills_section.index("tool_z")
        a_idx = skills_section.index("tool_a")
        assert b_idx < z_idx < a_idx


# -- Fetched tools helpers -----------------------------------------------------


class TestFetchedToolsHelpers:
    def test_get_empty(self, ctx):
        assert get_fetched_tools(ctx) == set()

    def test_add_and_get(self, ctx):
        add_fetched_tools(ctx, {"tool_a", "tool_b"})
        assert get_fetched_tools(ctx) == {"tool_a", "tool_b"}

    def test_add_is_additive(self, ctx):
        add_fetched_tools(ctx, {"tool_a"})
        add_fetched_tools(ctx, {"tool_b"})
        assert get_fetched_tools(ctx) == {"tool_a", "tool_b"}

    def test_stored_as_sorted_list(self, ctx):
        """Stored as sorted list for JSON serialization compatibility."""
        add_fetched_tools(ctx, {"c", "a", "b"})
        raw = ctx.skills.data["fetched_tools"]
        assert isinstance(raw, list)
        assert raw == ["a", "b", "c"]

    def test_handles_existing_list(self, ctx):
        """get_fetched_tools handles list from JSON deserialization."""
        ctx.skills.data = {"fetched_tools": ["tool_a", "tool_b"]}
        assert get_fetched_tools(ctx) == {"tool_a", "tool_b"}


# -- Priority enum value smoke ----------------------------------------------


class TestPriorityEnum:
    def test_string_values(self):
        assert Priority.CRITICAL.value == "critical"
        assert Priority.NORMAL.value == "normal"
        assert Priority.LOW.value == "low"


# -- Invariant: every core tool declares priority -----------------------------


class TestCoreToolsDeclarePriority:
    """Every entry in TOOL_DEFINITIONS must carry an explicit priority field.
    Catches new tools added without a declaration."""

    def test_all_core_tools_have_priority(self):
        missing = []
        for td in TOOL_DEFINITIONS:
            if "priority" not in td:
                name = td.get("function", {}).get("name", "<unknown>")
                missing.append(name)
        assert not missing, (
            f"Tool definitions missing 'priority' field: {missing}. "
            "Every core tool must declare critical/normal/low."
        )

    def test_all_priorities_valid(self):
        valid = {"critical", "normal", "low"}
        invalid = []
        for td in TOOL_DEFINITIONS:
            prio = td.get("priority")
            if prio is not None and prio not in valid:
                name = td.get("function", {}).get("name", "<unknown>")
                invalid.append((name, prio))
        assert not invalid, f"Invalid priority values: {invalid}"
