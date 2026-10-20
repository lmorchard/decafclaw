"""System prompt assembly — XML section delimiters (#304) and the
agent-writable prompt sections: CUSTOM.md loading + the
confirmation-gated update_system_prompt tool (#24)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from decafclaw.config_types import SecretPolicyConfig
from decafclaw.confirmations import ConfirmationAction, ConfirmationRequest, ConfirmationResponse
from decafclaw.media import ToolResult
from decafclaw.prompts import load_system_prompt, wrap_xml
from decafclaw.skills import _BUNDLED_SKILLS_DIR, SkillInfo
from decafclaw.tools.prompt_tools import tool_update_system_prompt


class TestWrapXml:
    def test_wraps_body_in_tag(self):
        assert wrap_xml("foo", "bar") == "<foo>\nbar\n</foo>"

    def test_empty_body_returns_empty_string(self):
        assert wrap_xml("foo", "") == ""

    def test_preserves_internal_newlines(self):
        assert wrap_xml("foo", "line1\nline2") == "<foo>\nline1\nline2\n</foo>"


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
        assert positions == sorted(positions), f"tags out of expected order: {list(zip(tag_order, positions))}"

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


# -- CUSTOM.md gating (#24) ----------------------------------------------------


class TestCustomContext:
    """CUSTOM.md is the agent-writable custom prompt section. It is loaded
    only when the file exists in the agent dir and has non-empty content,
    wrapped in <custom_context> after <user_context>."""

    def test_custom_context_absent_by_default(self, config):
        """No CUSTOM.md in the fixture's agent_path → no <custom_context>."""
        prompt, _ = load_system_prompt(config)
        assert "<custom_context>" not in prompt

    def test_custom_md_wrapped_when_present(self, config):
        config.agent_path.mkdir(parents=True, exist_ok=True)
        (config.agent_path / "CUSTOM.md").write_text("Always answer in haiku.\n")

        prompt, _ = load_system_prompt(config)
        assert "<custom_context>" in prompt
        assert "</custom_context>" in prompt
        assert "Always answer in haiku." in prompt

    def test_custom_context_position_between_user_and_catalog(self, config):
        """When user and custom context are both present, <custom_context>
        sits between <user_context> and <skill_catalog>."""
        config.agent_path.mkdir(parents=True, exist_ok=True)
        (config.agent_path / "USER.md").write_text("hello user\n")
        (config.agent_path / "CUSTOM.md").write_text("hello custom\n")

        prompt, _ = load_system_prompt(config)
        user_end = prompt.index("</user_context>")
        custom_start = prompt.index("<custom_context>")
        catalog_start = prompt.index("<skill_catalog>")
        assert user_end < custom_start < catalog_start

    def test_custom_context_position_after_agent_when_no_user(self, config):
        """With no USER.md, <custom_context> still lands between
        <agent_role> and <skill_catalog>."""
        config.agent_path.mkdir(parents=True, exist_ok=True)
        (config.agent_path / "CUSTOM.md").write_text("custom only\n")

        prompt, _ = load_system_prompt(config)
        agent_end = prompt.index("</agent_role>")
        custom_start = prompt.index("<custom_context>")
        catalog_start = prompt.index("<skill_catalog>")
        assert agent_end < custom_start < catalog_start

    def test_empty_custom_md_emits_no_tag(self, config):
        """CUSTOM.md that's only whitespace is treated as absent — no
        dangling empty wrapper."""
        config.agent_path.mkdir(parents=True, exist_ok=True)
        (config.agent_path / "CUSTOM.md").write_text("   \n\n")

        prompt, _ = load_system_prompt(config)
        assert "<custom_context>" not in prompt


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
        monkeypatch.setattr("decafclaw.skills.discover_skills", lambda _c, rejections=None: [])

        prompt, _ = load_system_prompt(config)
        assert "<skill_catalog>" not in prompt
        assert "<loaded_skills>" not in prompt

    def test_loaded_skills_contains_one_block_per_skill(self, config):
        """Every bundled always-loaded skill shows up as a
        <skill name="..."> block inside <loaded_skills>."""
        prompt, skills = load_system_prompt(config)
        always_loaded = [
            s
            for s in skills
            if s.always_loaded and Path(s.location).resolve().is_relative_to(_BUNDLED_SKILLS_DIR.resolve())
        ]
        assert always_loaded, "test precondition: at least one always-loaded skill"

        loaded_start = prompt.index("<loaded_skills>")
        loaded_end = prompt.index("</loaded_skills>")
        block = prompt[loaded_start:loaded_end]
        for s in always_loaded:
            assert f'<skill name="{s.name}">' in block
            assert "</skill>" in block

    def test_skill_name_xml_attribute_escaped(
        self,
        config,
        monkeypatch,
    ):
        """Skill names come from YAML frontmatter with no character
        validation at ingest. Guard against a name containing `"`, `<`,
        `>`, or `&` breaking the `<skill name="…">` wrapper. Not an
        expected case in practice (bundled names are simple), but we
        don't want the prompt to become malformed XML if a workspace
        skill author picks an unusual name."""
        nasty = 'weird"&<>'
        bundled_dir = _BUNDLED_SKILLS_DIR.resolve()
        location = bundled_dir / "vault" / "SKILL.md"
        rogue = SkillInfo(
            name=nasty,
            description="test",
            location=location,
            body="NASTY_BODY",
            always_loaded=True,
            # Explicit: `trust_tier` (not `location`) is what the
            # always-loaded trust check reads, and it now defaults to the
            # untrusted tier. This test is about XML escaping, so it needs a
            # tier whose body actually reaches the prompt.
            trust_tier="bundled",
        )
        monkeypatch.setattr("decafclaw.skills.discover_skills", lambda _c, rejections=None: [rogue])

        prompt, _ = load_system_prompt(config)

        # The raw name must NOT appear inside the attribute verbatim.
        assert 'name="weird"&<>"' not in prompt
        # The escaped form is what landed.
        assert 'name="weird&quot;&amp;&lt;&gt;"' in prompt
        # Body still made it through the wrapper.
        assert "NASTY_BODY" in prompt

    def test_workspace_always_loaded_skill_body_excluded(
        self,
        config,
        monkeypatch,
        tmp_path,
    ):
        """The trust-boundary check excludes always_loaded bodies from
        workspace-tier skills. The discovery walker normally strips the
        flag from workspace skills, but the prompts loader also defends
        against an in-memory SkillInfo that slipped through."""
        outside_location = tmp_path / "rogue" / "SKILL.md"
        outside_location.parent.mkdir(parents=True)
        outside_location.write_text("# rogue body\n")

        rogue = SkillInfo(
            name="rogue",
            description="test rogue skill",
            location=outside_location,
            body="ROGUE_BODY_MARKER",
            always_loaded=True,
            trust_tier="workspace",
        )

        # Keep the bundled discovery but prepend the rogue; the loader
        # will warn and skip its body.
        real_discover = __import__("decafclaw.skills", fromlist=["discover_skills"]).discover_skills

        def fake_discover(cfg, rejections=None):
            return [rogue, *real_discover(cfg, rejections=rejections)]

        monkeypatch.setattr("decafclaw.skills.discover_skills", fake_discover)

        prompt, _ = load_system_prompt(config)
        assert "ROGUE_BODY_MARKER" not in prompt
        # …but the catalog still mentions it as a plain entry, since
        # build_catalog_text doesn't enforce the trust boundary itself.


# -- update_system_prompt tool (#24) ------------------------------------------


def _text(result: str | ToolResult) -> str:
    return result.text if isinstance(result, ToolResult) else result


def _mock_confirm(approved: bool = True):
    return AsyncMock(return_value=ConfirmationResponse(confirmation_id="c1", approved=approved))


async def _capture_pending(ctx, section: str, content: str) -> ConfirmationRequest:
    """Run the interactive tool with a capturing confirmation that then
    simulates the process dying while the request is pending (no write).
    Returns the captured request round-tripped through the archive —
    ``to_archive_message`` / ``from_archive_message`` — exactly the shape
    ``_scan_archive_for_pending`` reconstructs on restart. Driving the
    real persisted shape is the point: a hand-built ``action_data`` would
    silently drift from what ``_confirm_admin_mutation`` actually stores."""

    captured = {}

    async def fake_confirm(req):
        captured["req"] = req
        raise RuntimeError("simulated process death while confirmation pending")

    ctx.request_confirmation = AsyncMock(side_effect=fake_confirm)
    with pytest.raises(RuntimeError, match="simulated process death"):
        await tool_update_system_prompt(ctx, section, content)

    return ConfirmationRequest.from_archive_message(captured["req"].to_archive_message())


@pytest.fixture(autouse=True)
def _ensure_agent_path_exists(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)


class TestUpdateSystemPrompt:
    """update_system_prompt(section, content) rewrites USER.md / CUSTOM.md
    in data/{agent_id}/, guards SOUL.md, rejects unknown sections, and
    requires interactive confirmation before writing."""

    @pytest.mark.asyncio
    async def test_update_user_md_writes_content(self, ctx):
        ctx.request_confirmation = _mock_confirm(approved=True)

        result = await tool_update_system_prompt(ctx, "USER.md", "The user is named Les and prefers brevity.\n")

        target = ctx.config.agent_path / "USER.md"
        assert target.exists()
        assert target.read_text() == "The user is named Les and prefers brevity.\n"
        assert "Updated 'USER.md'" in _text(result)
        # Confirmation was requested before the write, with a preview.
        assert ctx.request_confirmation.call_count == 1
        req = ctx.request_confirmation.call_args[0][0]
        assert "update_system_prompt" in req.action_data["command"]
        assert "USER.md" in req.action_data["command"]

    @pytest.mark.asyncio
    async def test_update_custom_md_writes_content(self, ctx):
        ctx.request_confirmation = _mock_confirm(approved=True)

        result = await tool_update_system_prompt(ctx, "CUSTOM.md", "Always answer in haiku.\n")

        target = ctx.config.agent_path / "CUSTOM.md"
        assert target.exists()
        assert target.read_text() == "Always answer in haiku.\n"
        assert "Updated 'CUSTOM.md'" in _text(result)
        assert ctx.request_confirmation.call_count == 1

    @pytest.mark.asyncio
    async def test_update_overwrite_shows_diff_in_preview(self, ctx):
        (ctx.config.agent_path / "USER.md").write_text("old line\n")
        ctx.request_confirmation = _mock_confirm(approved=True)

        result = await tool_update_system_prompt(ctx, "USER.md", "new line\n")

        req = ctx.request_confirmation.call_args[0][0]
        assert "-old line" in req.action_data["command"]
        assert "+new line" in req.action_data["command"]
        assert "Updated 'USER.md'" in _text(result)
        assert "-old line" in _text(result)

    @pytest.mark.asyncio
    async def test_update_empty_content_clears_section(self, ctx):
        """An empty string is a legitimate update (it clears the section);
        the diff preview shows the wipe, and the loader drops empty files."""
        (ctx.config.agent_path / "CUSTOM.md").write_text("old custom text\n")
        ctx.request_confirmation = _mock_confirm(approved=True)

        result = await tool_update_system_prompt(ctx, "CUSTOM.md", "")

        target = ctx.config.agent_path / "CUSTOM.md"
        assert target.exists()
        assert target.read_text() == ""
        assert "Updated 'CUSTOM.md'" in _text(result)

    @pytest.mark.asyncio
    async def test_update_soul_md_is_guarded(self, ctx):
        soul = ctx.config.agent_path / "SOUL.md"
        soul.write_text("core identity\n")
        ctx.request_confirmation = _mock_confirm(approved=True)

        result = await tool_update_system_prompt(ctx, "SOUL.md", "hijacked identity\n")

        text = _text(result)
        assert "[error:" in text
        # "core identity file" only appears in the guarded-section message —
        # the generic invalid-section error also mentions the SOUL.md guard,
        # so assert the specific phrasing.
        assert "core identity file" in text
        assert soul.read_text() == "core identity\n"
        # Guarded sections fail before any confirmation is requested.
        assert ctx.request_confirmation.call_count == 0

    @pytest.mark.asyncio
    async def test_update_soul_md_guarded_even_when_absent(self, ctx):
        """The guard is on the *name*, not the on-disk state — a missing
        SOUL.md must not be creatable through this tool either."""
        assert not (ctx.config.agent_path / "SOUL.md").exists()
        ctx.request_confirmation = _mock_confirm(approved=True)

        result = await tool_update_system_prompt(ctx, "SOUL.md", "brand new identity\n")

        assert "core identity file" in _text(result)
        assert not (ctx.config.agent_path / "SOUL.md").exists()
        assert ctx.request_confirmation.call_count == 0

    @pytest.mark.asyncio
    async def test_update_invalid_section_rejected(self, ctx):
        ctx.request_confirmation = _mock_confirm(approved=True)

        result = await tool_update_system_prompt(ctx, "AGENT.md", "whatever\n")

        text = _text(result)
        assert "[error:" in text
        # Descriptive: names the bad section and the allowed ones.
        assert "AGENT.md" in text
        assert "USER.md" in text
        assert "CUSTOM.md" in text
        assert not (ctx.config.agent_path / "AGENT.md").exists()
        assert ctx.request_confirmation.call_count == 0

    @pytest.mark.asyncio
    async def test_update_path_escape_rejected(self, ctx):
        """A section string is a *name*, not a path: separators and
        traversal must be rejected, and nothing may be written outside
        data/{agent_id}/."""
        ctx.request_confirmation = _mock_confirm(approved=True)

        for evil in ("../USER.md", "skills/x.md", "sub/dir.md", "/etc/hostname", "USER.md/extra"):
            result = await tool_update_system_prompt(ctx, evil, "x\n")
            assert "[error:" in _text(result)
            assert ctx.request_confirmation.call_count == 0

        assert not (ctx.config.agent_path.parent / "USER.md").exists()
        assert not (ctx.config.agent_path / "skills").exists()

    @pytest.mark.asyncio
    async def test_update_requires_confirmation_before_write(self, ctx):
        """ctx.request_confirmation is None (no manager) → refused, no write."""
        assert ctx.request_confirmation is None

        result = await tool_update_system_prompt(ctx, "USER.md", "should not land\n")

        assert "confirmation" in _text(result)
        assert "[error:" in _text(result)
        assert not (ctx.config.agent_path / "USER.md").exists()

    @pytest.mark.asyncio
    async def test_update_denied_by_user(self, ctx):
        ctx.request_confirmation = _mock_confirm(approved=False)

        result = await tool_update_system_prompt(ctx, "USER.md", "denied content\n")

        assert "denied by user" in _text(result)
        assert not (ctx.config.agent_path / "USER.md").exists()

    @pytest.mark.asyncio
    async def test_update_blocked_on_unattended_turns(self, ctx):
        ctx.task_mode = "heartbeat"
        ctx.request_confirmation = _mock_confirm(approved=True)

        result = await tool_update_system_prompt(ctx, "USER.md", "scheduled content\n")

        assert "unattended" in _text(result)
        assert not (ctx.config.agent_path / "USER.md").exists()
        assert ctx.request_confirmation.call_count == 0

    @pytest.mark.asyncio
    async def test_update_blocked_on_child_agents(self, ctx):
        ctx.is_child = True
        ctx.request_confirmation = _mock_confirm(approved=True)

        result = await tool_update_system_prompt(ctx, "CUSTOM.md", "child content\n")

        assert "child" in _text(result)
        assert not (ctx.config.agent_path / "CUSTOM.md").exists()
        assert ctx.request_confirmation.call_count == 0

    @pytest.mark.asyncio
    async def test_update_refuses_redaction_marker(self, ctx):
        """A payload holding the <redacted> marker is refused before
        confirmation — the same read→write clobber guard as admin_write."""
        ctx.request_confirmation = _mock_confirm(approved=True)

        result = await tool_update_system_prompt(ctx, "USER.md", "key: <redacted>\n")

        assert "redaction marker" in _text(result)
        assert not (ctx.config.agent_path / "USER.md").exists()
        assert ctx.request_confirmation.call_count == 0

    @pytest.mark.asyncio
    async def test_update_honors_secret_policy_refuse_paths(self, ctx):
        """Admin-widened refuse_paths must block the tool too: the one
        shared secret rule applies to every writer under the agent dir."""
        ctx.config = replace(ctx.config, secret_policy=SecretPolicyConfig(refuse_paths=["USER.md"]))
        ctx.request_confirmation = _mock_confirm(approved=True)

        result = await tool_update_system_prompt(ctx, "USER.md", "should be refused\n")

        assert "protected secret" in _text(result)
        assert not (ctx.config.agent_path / "USER.md").exists()
        assert ctx.request_confirmation.call_count == 0

    @pytest.mark.asyncio
    async def test_update_aborts_if_file_changed_during_confirmation(self, ctx):
        """Snapshot revalidation: the target must not change between
        confirmation and write (a fresh confirmation is required)."""
        target = ctx.config.agent_path / "USER.md"
        target.write_text("original\n")

        async def fake_confirm(req):
            target.write_text("tweaked on disk while pending\n")
            return ConfirmationResponse(confirmation_id="c1", approved=True)

        ctx.request_confirmation = AsyncMock(side_effect=fake_confirm)
        result = await tool_update_system_prompt(ctx, "USER.md", "agent's version\n")

        assert "while confirmation was pending" in _text(result)
        assert target.read_text() == "tweaked on disk while pending\n"

    @pytest.mark.asyncio
    async def test_update_symlink_to_soul_md_refused(self, ctx):
        """A USER.md symlink pointing to SOUL.md must be refused before reading
        it — preventing overwriting guarded identity files via redirection."""
        ctx.request_confirmation = _mock_confirm(approved=True)
        soul = ctx.config.agent_path / "SOUL.md"
        soul.write_text("protected identity\n")

        user_md = ctx.config.agent_path / "USER.md"
        user_md.symlink_to(soul)

        result = await tool_update_system_prompt(ctx, "USER.md", "hijacked\n")

        text = _text(result)
        assert "core identity file" in text or "symlink" in text
        assert soul.read_text() == "protected identity\n"
        assert ctx.request_confirmation.call_count == 0

    @pytest.mark.asyncio
    async def test_update_symlink_to_secret_file_refused(self, ctx):
        """A USER.md symlink pointing to a secret file (e.g. web_tokens.json)
        must be refused before reading it — secret contents must not be copied
        into confirmation preview/snapshot or overwritten."""
        ctx.request_confirmation = _mock_confirm(approved=True)
        tokens = ctx.config.agent_path / "web_tokens.json"
        tokens.write_text('{"secret": "token_val"}\n')

        user_md = ctx.config.agent_path / "USER.md"
        user_md.symlink_to(tokens)

        result = await tool_update_system_prompt(ctx, "USER.md", "clobbered\n")

        text = _text(result)
        assert "protected secret" in text or "symlink" in text
        assert tokens.read_text() == '{"secret": "token_val"}\n'
        assert ctx.request_confirmation.call_count == 0

    @pytest.mark.asyncio
    async def test_update_arbitrary_symlink_refused(self, ctx):
        """Any symlink for a prompt section is refused before confirmation."""
        ctx.request_confirmation = _mock_confirm(approved=True)
        other = ctx.config.agent_path / "other.txt"
        other.write_text("other\n")

        custom_md = ctx.config.agent_path / "CUSTOM.md"
        custom_md.symlink_to(other)

        result = await tool_update_system_prompt(ctx, "CUSTOM.md", "new\n")

        assert "symlink" in _text(result)
        assert other.read_text() == "other\n"
        assert ctx.request_confirmation.call_count == 0


class TestUpdateSystemPromptRecovery:
    """The UPDATE_SYSTEM_PROMPT confirmation handler (recovery path) must
    inherit every guard the interactive tool has — a pending approval that
    arrives after a restart is re-applied without the agent loop.

    The tests drive the *real* persisted request shape (see
    ``_capture_pending``): hand-built ``action_data`` is reserved for
    shapes the interactive flow can never emit (a tampered archive entry),
    and those use the real keys — the shared machinery stores the section
    under ``"path"``, and a handler that reads anything else drops every
    legitimate recovery."""

    @pytest.mark.asyncio
    async def test_interactive_request_persists_section_under_path_key(self, ctx):
        """Contract between the tool and the recovery handler: the shared
        machinery stores the section under the ``"path"`` key, and the
        handler reads exactly that key. This fails if either side moves to
        a bespoke key."""
        req = await _capture_pending(ctx, "USER.md", "x\n")

        assert req.action_data["path"] == "USER.md"
        assert req.action_data["tool_name"] == "update_system_prompt"
        assert req.action_data["payload"] == {"content": "x\n"}
        assert "section" not in req.action_data

    @pytest.mark.asyncio
    async def test_recovery_approve_applies_write(self, ctx):
        """A pending approval that survives a restart is applied on
        recovery — driven by the real persisted request."""
        from decafclaw.tools.prompt_tools import UpdateSystemPromptHandler

        handler = UpdateSystemPromptHandler()
        target = ctx.config.agent_path / "USER.md"
        target.write_text("before\n")

        req = await _capture_pending(ctx, "USER.md", "after\n")
        assert target.read_text() == "before\n"  # died before the write

        resp = ConfirmationResponse(confirmation_id=req.confirmation_id, approved=True)
        result = await handler.on_approve(ctx, req, resp)

        assert "Updated 'USER.md'" in result["inject_message"]
        assert result["continue_loop"] is False
        assert target.read_text() == "after\n"

    @pytest.mark.asyncio
    async def test_recovery_approve_revalidates_section(self, ctx):
        """Stored action_data is not trusted: a tampered archive entry
        naming SOUL.md must still be refused on the recovery path (the
        guard is inherited, #1013). The interactive flow can never emit
        this shape — SOUL.md is refused before confirmation — so the
        request is hand-built, in the real persisted shape."""
        from decafclaw.tools.prompt_tools import UpdateSystemPromptHandler

        handler = UpdateSystemPromptHandler()
        soul = ctx.config.agent_path / "SOUL.md"
        soul.write_text("identity\n")

        req = ConfirmationRequest(
            action_type=ConfirmationAction.UPDATE_SYSTEM_PROMPT,
            action_data={
                "tool_name": "update_system_prompt",
                "path": "SOUL.md",
                "payload": {"content": "hijacked\n"},
            },
        )
        resp = ConfirmationResponse(confirmation_id=req.confirmation_id, approved=True)

        result = await handler.on_approve(ctx, req, resp)
        assert "error" in result
        assert "core identity file" in result["error"]
        assert soul.read_text() == "identity\n"

    @pytest.mark.asyncio
    async def test_recovery_approve_refuses_marker(self, ctx):
        """The redaction-marker guard is inherited on the recovery path.
        Hand-built in the real persisted shape: the interactive flow
        refuses the marker before confirmation, so it can never emit this
        request itself."""
        from decafclaw.tools.prompt_tools import UpdateSystemPromptHandler

        handler = UpdateSystemPromptHandler()

        req = ConfirmationRequest(
            action_type=ConfirmationAction.UPDATE_SYSTEM_PROMPT,
            action_data={
                "tool_name": "update_system_prompt",
                "path": "USER.md",
                "payload": {"content": "key: <redacted>\n"},
            },
        )
        resp = ConfirmationResponse(confirmation_id=req.confirmation_id, approved=True)

        result = await handler.on_approve(ctx, req, resp)
        assert "error" in result
        assert "redaction marker" in result["error"]
        assert not (ctx.config.agent_path / "USER.md").exists()

    @pytest.mark.asyncio
    async def test_recovery_approve_aborts_if_stale(self, ctx):
        """A file modified on disk while the confirmation was pending
        (here: while the process was down) must abort the recovery apply,
        like the admin mutation recovery path. Driven by the real flow:
        the captured snapshot is from before the offline edit."""
        from decafclaw.tools.prompt_tools import UpdateSystemPromptHandler

        handler = UpdateSystemPromptHandler()
        target = ctx.config.agent_path / "CUSTOM.md"
        target.write_text("old content before offline edit\n")

        req = await _capture_pending(ctx, "CUSTOM.md", "agent's version\n")
        # Simulate an edit landing while the process was down.
        target.write_text("edited on disk while agent was offline\n")

        resp = ConfirmationResponse(confirmation_id=req.confirmation_id, approved=True)
        result = await handler.on_approve(ctx, req, resp)

        assert "error" in result
        assert target.read_text() == "edited on disk while agent was offline\n"

    @pytest.mark.asyncio
    async def test_recovery_deny(self, ctx):
        """A recovered denial names the real section (persisted under
        ``"path"``), not a placeholder."""
        from decafclaw.tools.prompt_tools import UpdateSystemPromptHandler

        handler = UpdateSystemPromptHandler()
        req = await _capture_pending(ctx, "USER.md", "denied content\n")
        assert not (ctx.config.agent_path / "USER.md").exists()

        resp = ConfirmationResponse(confirmation_id=req.confirmation_id, approved=False)
        result = await handler.on_deny(ctx, req, resp)

        assert result["inject_message"] == "[update_system_prompt for 'USER.md' was denied by user]"
        assert result["continue_loop"] is False
        assert not (ctx.config.agent_path / "USER.md").exists()

    @pytest.mark.asyncio
    async def test_recovery_approve_refuses_symlink_destination_to_soul(self, ctx):
        """Recovery handler rejects a persisted request targeting SOUL.md via
        symlink or snapshot redirection, protecting core identity files."""
        from decafclaw.tools.prompt_tools import UpdateSystemPromptHandler

        handler = UpdateSystemPromptHandler()
        soul = ctx.config.agent_path / "SOUL.md"
        soul.write_text("protected soul\n")

        # Hand-built persisted request whose snapshot targets SOUL.md
        req = ConfirmationRequest(
            action_type=ConfirmationAction.UPDATE_SYSTEM_PROMPT,
            action_data={
                "tool_name": "update_system_prompt",
                "path": "USER.md",
                "payload": {"content": "tampered\n"},
                "snapshot": {
                    "resolved": str(soul),
                    "exists": True,
                    "content": "protected soul\n",
                    "mtime_ns": 12345,
                    "size": len("protected soul\n"),
                    "is_dir": False,
                },
            },
        )
        resp = ConfirmationResponse(confirmation_id=req.confirmation_id, approved=True)
        result = await handler.on_approve(ctx, req, resp)

        assert "error" in result
        assert "core identity file" in result["error"]
        assert soul.read_text() == "protected soul\n"

    @pytest.mark.asyncio
    async def test_recovery_approve_refuses_symlink_destination_to_secret(self, ctx):
        """Recovery handler rejects a persisted request targeting a secret file
        such as web_tokens.json, protecting secret stores on restart recovery."""
        from decafclaw.tools.prompt_tools import UpdateSystemPromptHandler

        handler = UpdateSystemPromptHandler()
        tokens = ctx.config.agent_path / "web_tokens.json"
        tokens.write_text('{"token": "secret"}\n')

        req = ConfirmationRequest(
            action_type=ConfirmationAction.UPDATE_SYSTEM_PROMPT,
            action_data={
                "tool_name": "update_system_prompt",
                "path": "USER.md",
                "payload": {"content": "tampered\n"},
                "snapshot": {
                    "resolved": str(tokens),
                    "exists": True,
                    "content": '{"token": "secret"}\n',
                    "mtime_ns": 12345,
                    "size": len('{"token": "secret"}\n'),
                    "is_dir": False,
                },
            },
        )
        resp = ConfirmationResponse(confirmation_id=req.confirmation_id, approved=True)
        result = await handler.on_approve(ctx, req, resp)

        assert "error" in result
        assert "protected secret" in result["error"]
        assert tokens.read_text() == '{"token": "secret"}\n'

    @pytest.mark.asyncio
    async def test_recovery_approve_refuses_on_disk_symlink(self, ctx):
        """Recovery handler rejects when target on disk is a symlink."""
        from decafclaw.tools.prompt_tools import UpdateSystemPromptHandler

        handler = UpdateSystemPromptHandler()
        other = ctx.config.agent_path / "other.txt"
        other.write_text("other\n")

        user_md = ctx.config.agent_path / "USER.md"
        user_md.symlink_to(other)

        req = ConfirmationRequest(
            action_type=ConfirmationAction.UPDATE_SYSTEM_PROMPT,
            action_data={
                "tool_name": "update_system_prompt",
                "path": "USER.md",
                "payload": {"content": "tampered\n"},
                "snapshot": {
                    "resolved": str(user_md),
                    "exists": True,
                    "content": "other\n",
                    "mtime_ns": 12345,
                    "size": len("other\n"),
                    "is_dir": False,
                },
            },
        )
        resp = ConfirmationResponse(confirmation_id=req.confirmation_id, approved=True)
        result = await handler.on_approve(ctx, req, resp)

        assert "error" in result
        assert "symlink" in result["error"]
        assert other.read_text() == "other\n"
