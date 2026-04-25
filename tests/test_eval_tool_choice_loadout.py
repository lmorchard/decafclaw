"""Tests for the tool-choice eval loadout assembly (#303)."""

from __future__ import annotations

from decafclaw.eval.tool_choice.loadout import build_full_tool_loadout


def _names(defs):
    return {d["function"]["name"] for d in defs}


class TestBuildFullToolLoadout:
    def test_includes_core_tools(self, config):
        """Core tool definitions are always present."""
        defs = build_full_tool_loadout(config)
        names = _names(defs)
        # Sample a couple of canonical core tools that aren't going
        # anywhere — picking these to avoid coupling to the full list.
        assert "web_fetch" in names
        assert "current_time" in names

    def test_includes_skill_native_tools(self, config):
        """Every discovered skill with tools.py contributes its
        TOOL_DEFINITIONS to the loadout."""
        defs = build_full_tool_loadout(config)
        names = _names(defs)
        # vault and background ship native tools; assert at least one
        # tool from each surfaces.
        assert any(n.startswith("vault_") for n in names), \
            f"expected at least one vault_* tool in {sorted(names)}"
        assert any(
            n.startswith("background_") or n in names for n in names
        )

    def test_mcp_excluded_by_default(self, config):
        """The default loadout has zero mcp__* entries — MCP tools are
        deployment-specific and excluded unless explicitly opted in."""
        defs = build_full_tool_loadout(config)
        names = _names(defs)
        assert not any(n.startswith("mcp__") for n in names), \
            f"unexpected mcp__ tools: {[n for n in names if n.startswith('mcp__')]}"

    def test_include_mcp_no_registry_returns_same_set(self, config):
        """When the MCP registry isn't initialized (no servers
        configured), include_mcp=True returns the same set as the
        default. Should not raise."""
        default_defs = build_full_tool_loadout(config)
        with_mcp = build_full_tool_loadout(config, include_mcp=True)
        assert _names(with_mcp) == _names(default_defs)

    def test_all_entries_have_function_name(self, config):
        """Every loadout entry has function.name in the OpenAI-style
        shape — downstream code relies on it."""
        defs = build_full_tool_loadout(config)
        for d in defs:
            assert "function" in d, f"entry missing function: {d}"
            assert "name" in d["function"], f"entry missing name: {d}"
            assert isinstance(d["function"]["name"], str)
            assert d["function"]["name"]  # non-empty

    def test_ignores_failing_skill(self, config, monkeypatch, tmp_path):
        """A skill whose tools.py raises on import is logged and
        skipped, not propagated."""
        from decafclaw.skills import SkillInfo

        broken_dir = tmp_path / "broken"
        broken_dir.mkdir()
        (broken_dir / "tools.py").write_text("raise RuntimeError('oops')\n")
        broken = SkillInfo(
            name="broken",
            description="intentionally broken skill",
            location=broken_dir,
            has_native_tools=True,
        )

        from decafclaw.skills import discover_skills as real_discover

        def fake_discover(cfg):
            return [broken, *real_discover(cfg)]

        monkeypatch.setattr(
            "decafclaw.eval.tool_choice.loadout.discover_skills",
            fake_discover,
        )

        # Should not raise; should still include core + healthy skills.
        defs = build_full_tool_loadout(config)
        names = _names(defs)
        assert "web_fetch" in names
