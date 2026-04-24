"""System prompt assembly — XML section delimiters (#304)."""

from __future__ import annotations

from pathlib import Path

from decafclaw.prompts import load_system_prompt
from decafclaw.skills import _BUNDLED_SKILLS_DIR, SkillInfo

# -- Default bundled load ------------------------------------------------------


class TestDefaultLoad:
    """A clean config with bundled SOUL/AGENT and the real skill discovery
    produces all the expected tags in order."""

    def test_expected_tags_present_in_order(self, config):
        prompt, _ = load_system_prompt(config)
        # Order the loader builds: SOUL → AGENT → (USER?) → skill_catalog → loaded_skills.
        # USER.md doesn't exist in the tmp fixture, so it's skipped.
        tag_order = ["<soul>", "<agent_role>", "<skill_catalog>", "<loaded_skills>"]
        positions = [prompt.index(t) for t in tag_order]
        assert positions == sorted(positions), (
            f"tags out of expected order: {list(zip(tag_order, positions))}"
        )

    def test_user_context_absent_by_default(self, config):
        """No USER.md in the fixture's agent_path → no <user_context>."""
        prompt, _ = load_system_prompt(config)
        assert "<user_context>" not in prompt

    def test_tags_are_closed(self, config):
        prompt, _ = load_system_prompt(config)
        for tag in ("soul", "agent_role", "skill_catalog", "loaded_skills"):
            assert f"<{tag}>" in prompt
            assert f"</{tag}>" in prompt


# -- USER.md gating ------------------------------------------------------------


class TestUserContext:
    def test_user_md_wrapped_when_present(self, config):
        config.agent_path.mkdir(parents=True, exist_ok=True)
        user_md = config.agent_path / "USER.md"
        user_md.write_text("The user is named Les and prefers brevity.\n")

        prompt, _ = load_system_prompt(config)
        assert "<user_context>" in prompt
        assert "</user_context>" in prompt
        assert "The user is named Les" in prompt

    def test_user_context_position_between_agent_and_catalog(self, config):
        """When all four are present, <user_context> sits between
        <agent_role> and <skill_catalog>."""
        config.agent_path.mkdir(parents=True, exist_ok=True)
        (config.agent_path / "USER.md").write_text("hello\n")

        prompt, _ = load_system_prompt(config)
        agent_end = prompt.index("</agent_role>")
        user_start = prompt.index("<user_context>")
        catalog_start = prompt.index("<skill_catalog>")
        assert agent_end < user_start < catalog_start

    def test_empty_user_md_emits_no_tag(self, config):
        """USER.md that's only whitespace is treated as absent — no
        dangling empty wrapper."""
        config.agent_path.mkdir(parents=True, exist_ok=True)
        (config.agent_path / "USER.md").write_text("   \n\n")

        prompt, _ = load_system_prompt(config)
        assert "<user_context>" not in prompt


# -- Inner content preserved ---------------------------------------------------


class TestInnerContent:
    def test_soul_body_preserved(self, config):
        """A known marker from the bundled SOUL.md appears inside
        <soul>…</soul> intact — not rewritten, not escaped."""
        prompt, _ = load_system_prompt(config)
        soul_start = prompt.index("<soul>")
        soul_end = prompt.index("</soul>")
        soul_block = prompt[soul_start:soul_end]
        # Pull a stable phrase from the actual bundled SOUL.md
        bundled_soul = (Path(__file__).parent.parent / "src/decafclaw/prompts/SOUL.md").read_text()
        first_line = bundled_soul.strip().splitlines()[0]
        assert first_line in soul_block

    def test_catalog_markdown_preserved(self, config):
        """The `## Available Skills` heading produced by build_catalog_text
        survives the wrap — tags are additive, not transformative."""
        prompt, _ = load_system_prompt(config)
        catalog_start = prompt.index("<skill_catalog>")
        catalog_end = prompt.index("</skill_catalog>")
        catalog_block = prompt[catalog_start:catalog_end]
        # At least one of these will be present for any real bundled load.
        assert "## Active Skills" in catalog_block or "## Available Skills" in catalog_block


# -- Skill catalog / loaded_skills gating --------------------------------------


class TestSkillSections:
    def test_no_skills_no_catalog_or_loaded(self, config, monkeypatch):
        """When discovery returns nothing, both skill-related tags are
        absent. build_catalog_text's early `if not skills: return ""`
        gate means <skill_catalog> disappears; the always-loaded loop
        collapses to empty skill_blocks so <loaded_skills> does too."""
        monkeypatch.setattr("decafclaw.skills.discover_skills", lambda _c: [])

        prompt, _ = load_system_prompt(config)
        assert "<skill_catalog>" not in prompt
        assert "<loaded_skills>" not in prompt

    def test_loaded_skills_contains_one_block_per_skill(self, config):
        """Every bundled always-loaded skill shows up as a
        <skill name="..."> block inside <loaded_skills>."""
        prompt, skills = load_system_prompt(config)
        always_loaded = [
            s for s in skills
            if s.always_loaded and Path(s.location).resolve().is_relative_to(
                _BUNDLED_SKILLS_DIR.resolve()
            )
        ]
        assert always_loaded, "test precondition: at least one always-loaded skill"

        loaded_start = prompt.index("<loaded_skills>")
        loaded_end = prompt.index("</loaded_skills>")
        block = prompt[loaded_start:loaded_end]
        for s in always_loaded:
            assert f'<skill name="{s.name}">' in block
            assert "</skill>" in block

    def test_skill_name_xml_attribute_escaped(
        self, config, monkeypatch,
    ):
        """Skill names come from YAML frontmatter with no character
        validation at ingest. Guard against a name containing `"`, `<`,
        `>`, or `&` breaking the `<skill name="…">` wrapper. Not an
        expected case in practice (bundled names are simple), but we
        don't want the prompt to become malformed XML if a workspace
        skill author picks an unusual name."""
        nasty = 'weird"&<>'
        bundled_dir = _BUNDLED_SKILLS_DIR.resolve()
        location = bundled_dir / "vault" / "SKILL.md"  # any bundled path for the trust check
        rogue = SkillInfo(
            name=nasty,
            description="test",
            location=location,
            body="NASTY_BODY",
            always_loaded=True,
        )
        monkeypatch.setattr("decafclaw.skills.discover_skills", lambda _c: [rogue])

        prompt, _ = load_system_prompt(config)

        # The raw name must NOT appear inside the attribute verbatim.
        assert 'name="weird"&<>"' not in prompt
        # The escaped form is what landed.
        assert 'name="weird&quot;&amp;&lt;&gt;"' in prompt
        # Body still made it through the wrapper.
        assert "NASTY_BODY" in prompt

    def test_non_bundled_always_loaded_skill_body_excluded(
        self, config, monkeypatch, tmp_path,
    ):
        """The trust-boundary check still excludes a skill whose
        location is outside the bundled dir, even if its frontmatter
        sets always_loaded: true. Body must NOT appear inside
        <loaded_skills>."""
        outside_location = tmp_path / "rogue" / "SKILL.md"
        outside_location.parent.mkdir(parents=True)
        outside_location.write_text("# rogue body\n")

        rogue = SkillInfo(
            name="rogue",
            description="test rogue skill",
            location=outside_location,
            body="ROGUE_BODY_MARKER",
            always_loaded=True,
        )

        # Keep the bundled discovery but prepend the rogue; the loader
        # will warn and skip its body.
        real_discover = __import__(
            "decafclaw.skills", fromlist=["discover_skills"]
        ).discover_skills

        def fake_discover(cfg):
            return [rogue, *real_discover(cfg)]

        monkeypatch.setattr("decafclaw.skills.discover_skills", fake_discover)

        prompt, _ = load_system_prompt(config)
        assert "ROGUE_BODY_MARKER" not in prompt
        # …but the catalog still mentions it as a plain entry, since
        # build_catalog_text doesn't enforce the trust boundary itself.
