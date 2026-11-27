"""Tests for the skill_validate tool and phantom tool-call checks (split from #1419)."""

import pytest

from decafclaw.skills import discover_skills
from decafclaw.tools.skill_tools import (
    _compute_skill_hash,
    _save_permission,
    tool_activate_skill,
    tool_refresh_skills,
    tool_skill_validate,
)
from tests.skill_helpers import _text, _tiered_skill, _write_skill, _write_ws_skill

# -- skill_validate tool --


def test_skill_validate_missing_skill_md(ctx):
    d = ctx.config.workspace_path / "skills" / "empty"
    d.mkdir(parents=True)
    result = tool_skill_validate(ctx, path="skills/empty")
    assert result.data["ok"] is False
    names = {c["name"] for c in result.data["checks"]}
    assert "skill_md_present" in names


def test_skill_validate_no_frontmatter(ctx):
    d = ctx.config.workspace_path / "skills" / "nofm"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("# Heading only\n")
    result = tool_skill_validate(ctx, path="skills/nofm")
    assert result.data["ok"] is False
    fm = next(c for c in result.data["checks"] if c["name"] == "frontmatter")
    assert fm["passed"] is False


def test_skill_validate_valid_text_only(ctx):
    _write_ws_skill(ctx, "texty", "name: texty\ndescription: Text only.")
    result = tool_skill_validate(ctx, path="skills/texty")
    assert result.data["ok"] is True
    # no tools.py → no tools checks
    names = {c["name"] for c in result.data["checks"]}
    assert "tools_import" not in names


def test_skill_validate_valid_with_tools(ctx):
    _write_ws_skill(
        ctx,
        "withtools",
        "name: withtools\ndescription: Has tools.",
        tools_py="def get_tools(ctx):\n    return {}, []\n",
    )
    result = tool_skill_validate(ctx, path="skills/withtools")
    assert result.data["ok"] is True
    sig = next(c for c in result.data["checks"] if c["name"] == "get_tools_signature")
    assert sig["passed"] is True


def test_skill_validate_tools_syntax_error(ctx):
    _write_ws_skill(
        ctx,
        "broken",
        "name: broken\ndescription: Bad tools.",
        tools_py="def get_tools(ctx)\n    return {}, []\n",  # missing colon
    )
    result = tool_skill_validate(ctx, path="skills/broken")
    assert result.data["ok"] is False
    imp = next(c for c in result.data["checks"] if c["name"] == "tools_import")
    assert imp["passed"] is False
    assert "SyntaxError" in imp["message"]


def test_skill_validate_tools_undefined_name(ctx):
    _write_ws_skill(
        ctx,
        "phantom",
        "name: phantom\ndescription: Phantom api.",
        tools_py="TOOLS = {'x': default_api.shell}\n",  # NameError at import
    )
    result = tool_skill_validate(ctx, path="skills/phantom")
    assert result.data["ok"] is False
    imp = next(c for c in result.data["checks"] if c["name"] == "tools_import")
    assert imp["passed"] is False
    assert "NameError" in imp["message"]


def test_skill_validate_get_tools_no_ctx(ctx):
    _write_ws_skill(
        ctx,
        "noctx",
        "name: noctx\ndescription: Bad signature.",
        tools_py="def get_tools():\n    return {}, []\n",  # missing ctx
    )
    result = tool_skill_validate(ctx, path="skills/noctx")
    assert result.data["ok"] is False
    sig = next(c for c in result.data["checks"] if c["name"] == "get_tools_signature")
    assert sig["passed"] is False
    assert "ctx" in sig["message"]


def test_skill_validate_stray_main_py(ctx):
    d = _write_ws_skill(ctx, "mislabeled", "name: mislabeled\ndescription: Wrong file.")
    (d / "main.py").write_text("def get_tools(ctx):\n    return {}, []\n")
    result = tool_skill_validate(ctx, path="skills/mislabeled")
    fn = next(c for c in result.data["checks"] if c["name"] == "tools_filename")
    assert fn["passed"] is False
    assert "main.py" in fn["message"]


def test_skill_validate_outside_workspace(ctx):
    result = tool_skill_validate(ctx, path="../../etc")
    assert "outside the workspace" in _text(result)


# -- discoverable location --
#
# Same failure class as the #675 shape contract below: skill_validate used to
# check only "inside the workspace and has a SKILL.md", so it reported a
# fully-green PASS on a directory `discover_skills` never scans. The agent then
# got PASS from the validator and silence from refresh_skills, with nothing to
# reconcile them, and looped. A validator that green-lights an undiscoverable
# location is worse than none.


def test_skill_validate_rejects_redundant_workspace_prefix(ctx):
    """The exact trap: workspace_write paths are workspace-relative, so a
    'workspace/skills/<name>' path lands the skill one level too deep."""
    d = ctx.config.workspace_path / "workspace" / "skills" / "blog-tools"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: blog-tools\ndescription: Blog helpers.\n---\nBody.\n")
    result = tool_skill_validate(ctx, path="workspace/skills/blog-tools")
    assert result.data["ok"] is False
    check = next(c for c in result.data["checks"] if c["name"] == "discoverable")
    assert check["passed"] is False
    # Must name the corrected path, not just say "wrong".
    assert "skills/blog-tools" in check["message"]


def test_skill_validate_rejects_nested_subdirectory(ctx):
    """Discovery only scans immediate children of skills/, so a skill nested
    any deeper is invisible no matter how valid its SKILL.md is."""
    d = ctx.config.workspace_path / "skills" / "group" / "nested"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: nested\ndescription: Too deep.\n---\nBody.\n")
    result = tool_skill_validate(ctx, path="skills/group/nested")
    check = next(c for c in result.data["checks"] if c["name"] == "discoverable")
    assert check["passed"] is False


def test_skill_validate_discoverable_passes_at_correct_location(ctx):
    _write_ws_skill(ctx, "correct", "name: correct\ndescription: Right place.")
    result = tool_skill_validate(ctx, path="skills/correct")
    assert result.data["ok"] is True
    check = next(c for c in result.data["checks"] if c["name"] == "discoverable")
    assert check["passed"] is True


def test_skill_validate_discoverable_accepts_skill_md_path(ctx):
    """Passing the SKILL.md itself resolves to its parent, which is valid."""
    _write_ws_skill(ctx, "viamd", "name: viamd\ndescription: Via SKILL.md.")
    result = tool_skill_validate(ctx, path="skills/viamd/SKILL.md")
    check = next(c for c in result.data["checks"] if c["name"] == "discoverable")
    assert check["passed"] is True


# -- tools.py shape contract (#675) --
#
# The loader needs TOOLS to be dict[str, callable] and TOOL_DEFINITIONS to be
# a list of OpenAI function schemas. Before #675 skill_validate only checked
# that the names EXISTED, so it reported PASS on skills that could not
# possibly load — the contradiction that derailed a whole session.


def test_skill_validate_rejects_tools_as_list(ctx):
    """TOOLS = [fn] imports cleanly but breaks ctx.tools.extra.update()."""
    _write_ws_skill(
        ctx,
        "toolslist",
        "name: toolslist\ndescription: Wrong TOOLS shape.",
        tools_py="def hello():\n    return 'hi'\n\nTOOLS = [hello]\n",
    )
    result = tool_skill_validate(ctx, path="skills/toolslist")
    assert result.data["ok"] is False
    shape = next(c for c in result.data["checks"] if c["name"] == "tools_shape")
    assert shape["passed"] is False
    # Actionable: names the expected type, not just "invalid".
    assert "dict" in shape["message"]
    assert "TOOLS" in shape["message"]


def test_skill_validate_rejects_tool_definitions_as_dict(ctx):
    """TOOL_DEFINITIONS = {name: fn} imports cleanly but breaks every
    consumer that iterates it as a list of schema dicts."""
    _write_ws_skill(
        ctx,
        "defsdict",
        "name: defsdict\ndescription: Wrong defs shape.",
        tools_py=(
            "def hello(ctx):\n    return 'hi'\n\nTOOLS = {'hello': hello}\nTOOL_DEFINITIONS = {'hello': hello}\n"
        ),
    )
    result = tool_skill_validate(ctx, path="skills/defsdict")
    assert result.data["ok"] is False
    shape = next(c for c in result.data["checks"] if c["name"] == "tools_shape")
    assert shape["passed"] is False
    assert "TOOL_DEFINITIONS" in shape["message"]
    assert "list" in shape["message"]


def test_skill_validate_rejects_non_callable_tool_value(ctx):
    _write_ws_skill(
        ctx,
        "notcallable",
        "name: notcallable\ndescription: Bad value.",
        tools_py="TOOLS = {'hello': 'not a function'}\n",
    )
    result = tool_skill_validate(ctx, path="skills/notcallable")
    assert result.data["ok"] is False
    shape = next(c for c in result.data["checks"] if c["name"] == "tools_shape")
    assert shape["passed"] is False
    assert "callable" in shape["message"]


def test_skill_validate_rejects_definition_without_function_name(ctx):
    _write_ws_skill(
        ctx,
        "nofname",
        "name: nofname\ndescription: Missing name.",
        tools_py=(
            "def hello(ctx):\n    return 'hi'\n\n"
            "TOOLS = {'hello': hello}\n"
            "TOOL_DEFINITIONS = [{'type': 'function', 'function': {}}]\n"
        ),
    )
    result = tool_skill_validate(ctx, path="skills/nofname")
    assert result.data["ok"] is False
    shape = next(c for c in result.data["checks"] if c["name"] == "tools_shape")
    assert shape["passed"] is False
    assert "function.name" in shape["message"]


def test_skill_validate_accepts_correct_tools_shape(ctx):
    """The happy path still passes, and reports the shape check explicitly."""
    _write_ws_skill(
        ctx,
        "goodshape",
        "name: goodshape\ndescription: Correct shape.",
        tools_py=(
            "def hello(ctx):\n    return 'hi'\n\n"
            "TOOLS = {'hello': hello}\n"
            "TOOL_DEFINITIONS = [{'type': 'function', "
            "'function': {'name': 'hello'}}]\n"
        ),
    )
    result = tool_skill_validate(ctx, path="skills/goodshape")
    assert result.data["ok"] is True
    shape = next(c for c in result.data["checks"] if c["name"] == "tools_shape")
    assert shape["passed"] is True


def test_load_native_tools_rejects_wrong_shape_with_actionable_error(tmp_path):
    """_load_native_tools raises a message naming the contract, not an
    opaque 'cannot convert dictionary update sequence element #0'."""
    from decafclaw.skills import SkillInfo
    from decafclaw.tools.skill_tools import SkillContractError, _load_native_tools

    d = tmp_path / "badshape"
    d.mkdir()
    (d / "tools.py").write_text("def hello():\n    return 'hi'\n\nTOOLS = [hello]\n")
    info = SkillInfo(name="badshape", description="d", location=d, has_native_tools=True)
    with pytest.raises(SkillContractError) as exc:
        _load_native_tools(info)
    assert "TOOLS" in str(exc.value)
    assert "dict" in str(exc.value)


def test_build_skill_tool_owners_survives_malformed_definitions(tmp_path, config):
    """A workspace skill with the wrong TOOL_DEFINITIONS shape must not take
    down startup — build_skill_tool_owners is called unguarded from
    decafclaw.main()."""
    from decafclaw.skills import build_skill_tool_owners

    root = tmp_path / "skills-root"
    bad = root / "badskill"
    _write_skill(bad, "name: badskill\ndescription: Bad.", tools_py=False)
    (bad / "tools.py").write_text(
        "def hello(ctx):\n    return 'hi'\n\n"
        "TOOLS = {'hello': hello}\n"
        "TOOL_DEFINITIONS = {'hello': hello}\n"  # dict, not list
    )
    good = root / "goodskill"
    _write_skill(good, "name: goodskill\ndescription: Good.", tools_py=False)
    (good / "tools.py").write_text(
        "TOOLS = {'good_one': lambda ctx: None}\n"
        "TOOL_DEFINITIONS = [{'type': 'function', "
        "'function': {'name': 'good_one'}}]\n"
    )
    config.extra_skill_paths = [str(root)]
    skills = discover_skills(config)

    owners = build_skill_tool_owners(skills)  # must not raise
    # The malformed skill contributes nothing; the healthy one is unaffected.
    assert owners.get("good_one") == "goodskill"
    assert "hello" not in owners


# -- skill_validate advisories --
#
# `ok` must keep meaning exactly "the loader will accept this". A name that
# disagrees with its directory, or uses spaces and capitals, loads perfectly
# well (the bundled catalog has both), so flagging either as a FAILURE would
# recreate the validator-contradicts-loader trap in reverse. They are
# reported as advisories instead.


def test_skill_validate_advises_on_name_directory_mismatch(ctx):
    _write_ws_skill(ctx, "blog-tools", "name: blogtools\ndescription: Mismatch.")
    result = tool_skill_validate(ctx, path="skills/blog-tools")
    assert result.data["ok"] is True, "a name mismatch still loads"
    advisories = " ".join(result.data["advisories"])
    assert "blogtools" in advisories
    assert "blog-tools" in advisories


def test_skill_validate_advises_on_nonstandard_name_format(ctx):
    _write_ws_skill(ctx, "shouty", "name: Shouty Skill\ndescription: Caps.")
    result = tool_skill_validate(ctx, path="skills/shouty")
    assert result.data["ok"] is True
    assert any("lowercase" in a for a in result.data["advisories"])


def test_skill_validate_no_advisories_when_name_is_clean(ctx):
    _write_ws_skill(ctx, "tidy", "name: tidy\ndescription: All good.")
    result = tool_skill_validate(ctx, path="skills/tidy")
    assert result.data["ok"] is True
    assert result.data["advisories"] == []


def test_refresh_skills_does_not_call_everything_new_on_a_cold_catalog(ctx):
    """An empty baseline is initial population, not 38 new skills."""
    ctx.config.discovered_skills = []
    text = _text(tool_refresh_skills(ctx))
    assert "New:" not in text
    assert "no change" not in text.lower()


# -- phantom tool calls in tools.py --
#
# A skill tool cannot call another decaf tool. The model's prior that it can is
# strong and recurring: three separate variants appeared in one session
# (`default_api.shell_background_start`, `ctx.shell_background_start`,
# `ctx.tools.shell_background_start`), and evals showed prose in skill-creator
# does not suppress it (0/6 with the guidance loaded). These calls import
# cleanly and only explode when the tool is invoked, so static detection is the
# only thing that catches them before the user does. Same lesson as #675: the
# validator has to enforce what documentation cannot.


def _validate_tools_py(ctx, name, source):
    # The check matches against the live tool catalog. At runtime
    # skill_tool_owners is populated at startup (which is how
    # shell_background_start — a bundled `background` skill tool — is known);
    # a bare test ctx has none, so stand it in explicitly.
    # test_phantom_check_sees_bundled_skill_tools covers the real wiring.
    ctx.config.skill_tool_owners = {"shell_background_start": "background"}
    _write_ws_skill(
        ctx,
        name,
        f"name: {name}\ndescription: Phantom probe.",
        tools_py=source,
    )
    return tool_skill_validate(ctx, path=f"skills/{name}")


def test_phantom_check_sees_bundled_skill_tools(config):
    """Discovery must actually put shell_background_start in the catalog the
    check consults — otherwise the check silently passes everything."""
    from decafclaw.tools.skill_tools import _known_tool_names, rediscover_skills

    rediscover_skills(config)
    names = _known_tool_names(config)
    assert "shell_background_start" in names, "the most-wrapped tool must be known"
    assert "workspace_write" in names, "core tools must be known"


def _phantom_check(result):
    return next(
        (c for c in result.data["checks"] if c["name"] == "no_phantom_tool_calls"),
        None,
    )


def test_skill_validate_rejects_default_api(ctx):
    result = _validate_tools_py(
        ctx,
        "pdefault",
        (
            "def go(ctx):\n"
            "    return default_api.shell_background_start(command='npm start')\n"
            "TOOLS = {'go': go}\nTOOL_DEFINITIONS = []\n"
        ),
    )
    assert result.data["ok"] is False
    assert "default_api" in _phantom_check(result)["message"]


def test_skill_validate_rejects_ctx_tool_call(ctx):
    result = _validate_tools_py(
        ctx,
        "pctx",
        (
            "def go(ctx):\n"
            "    return ctx.shell_background_start(command='npm start')\n"
            "TOOLS = {'go': go}\nTOOL_DEFINITIONS = []\n"
        ),
    )
    assert result.data["ok"] is False
    assert "shell_background_start" in _phantom_check(result)["message"]


def test_skill_validate_rejects_ctx_tools_namespace_call(ctx):
    """The variant the eval actually produced."""
    result = _validate_tools_py(
        ctx,
        "pctxtools",
        (
            "def go(ctx):\n"
            "    return ctx.tools.shell_background_start(command='npm start')\n"
            "TOOLS = {'go': go}\nTOOL_DEFINITIONS = []\n"
        ),
    )
    assert result.data["ok"] is False
    assert "shell_background_start" in _phantom_check(result)["message"]


def test_skill_validate_allows_legitimate_ctx_use(ctx):
    """ctx.publish / ctx.config are real. Only decaf *tool* names are phantom."""
    result = _validate_tools_py(
        ctx,
        "goodctx",
        (
            "def go(ctx):\n"
            "    ctx.publish('tool_status', {'text': 'hi'})\n"
            "    return str(ctx.config.workspace_path)\n"
            "TOOLS = {'go': go}\nTOOL_DEFINITIONS = []\n"
        ),
    )
    assert result.data["ok"] is True
    assert _phantom_check(result)["passed"] is True


def test_skill_validate_allows_subprocess(ctx):
    """The documented way to run a command from inside a tool."""
    result = _validate_tools_py(
        ctx,
        "subproc",
        (
            "import subprocess\n"
            "def go(ctx):\n"
            "    return subprocess.run(['npm', 'start'], capture_output=True).stdout\n"
            "TOOLS = {'go': go}\nTOOL_DEFINITIONS = []\n"
        ),
    )
    assert result.data["ok"] is True


def test_skill_validate_phantom_check_survives_syntax_error(ctx):
    """A SyntaxError is tools_import's job; the phantom check must not crash."""
    result = _validate_tools_py(ctx, "psyntax", "def go(ctx\n    pass\n")
    assert result.data["ok"] is False
    imp = next(c for c in result.data["checks"] if c["name"] == "tools_import")
    assert imp["passed"] is False


# -- phantom tool calls blocked at activation (#701) --
#
# skill_validate catches these, but only if the agent chooses to run it — and
# evals measured that choice as a coin flip, which capped the correct-outcome
# rate at ~1/3. Checking at activation closes the "nobody validated it" gap:
# the agent must activate a skill to use it, so this path cannot be skipped.
#
# Enforcement is keyed to trust tier. Workspace skills are agent-authored, so
# refusing to load is the forcing function. Trusted tiers (bundled / admin /
# extra) are the user's own code and may hold many working tools beside one
# broken one, so they get a loud warning instead — and `activate_always_loaded`
# skips workspace tier entirely, so startup can never be blocked by this.

PHANTOM_TOOLS_PY = (
    "from decafclaw.media import ToolResult\n\n"
    "def start_it(ctx):\n"
    "    return ctx.tools.shell_background_start(command='npm start')\n\n"
    "TOOLS = {'start_it': start_it}\n"
    "TOOL_DEFINITIONS = [{'type': 'function', "
    "'function': {'name': 'start_it'}}]\n"
)


@pytest.mark.asyncio
async def test_activation_blocked_for_workspace_skill_with_phantom_call(ctx, tmp_path):
    skill = _tiered_skill(tmp_path / "ph", "ph", PHANTOM_TOOLS_PY, "workspace")
    ctx.config.discovered_skills = [skill]
    ctx.config.skill_tool_owners = {"shell_background_start": "background"}
    _save_permission(ctx.config, "ph", {"status": "always", "hash": _compute_skill_hash(skill)})

    result = await tool_activate_skill(ctx, name="ph")
    text = _text(result)
    assert "shell_background_start" in text
    assert "start_it" not in ctx.tools.extra, "broken tool must not register"
    assert "ph" not in ctx.skills.activated


@pytest.mark.asyncio
async def test_activation_blocked_for_default_api(ctx, tmp_path):
    src = "def go(ctx):\n    return default_api.workspace_read(path='x')\nTOOLS = {'go': go}\nTOOL_DEFINITIONS = []\n"
    skill = _tiered_skill(tmp_path / "da", "da", src, "workspace")
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, "da", {"status": "always", "hash": _compute_skill_hash(skill)})

    result = await tool_activate_skill(ctx, name="da")
    assert "default_api" in _text(result)
    assert "go" not in ctx.tools.extra


@pytest.mark.asyncio
async def test_activation_warns_but_allows_trusted_tier(ctx, tmp_path):
    """A bundled skill is the user's own code — warn, don't break their agent."""
    skill = _tiered_skill(tmp_path / "bt", "bt", PHANTOM_TOOLS_PY, "bundled")
    ctx.config.discovered_skills = [skill]
    ctx.config.skill_tool_owners = {"shell_background_start": "background"}

    result = await tool_activate_skill(ctx, name="bt")
    text = _text(result)
    assert "start_it" in ctx.tools.extra, "trusted skill must still load"
    assert "bt" in ctx.skills.activated
    assert "shell_background_start" in text, "but the problem must be surfaced"


@pytest.mark.asyncio
async def test_activation_unaffected_for_clean_workspace_skill(ctx, tmp_path):
    src = (
        "import subprocess\n"
        "def go(ctx):\n"
        "    subprocess.run(['npm', 'start'], check=False)\n"
        "    ctx.publish('tool_status', {'text': 'ok'})\n"
        "    return 'started'\n"
        "TOOLS = {'go': go}\n"
        "TOOL_DEFINITIONS = [{'type': 'function', 'function': {'name': 'go'}}]\n"
    )
    skill = _tiered_skill(tmp_path / "clean2", "clean2", src, "workspace")
    ctx.config.discovered_skills = [skill]
    ctx.config.skill_tool_owners = {"shell_background_start": "background"}
    _save_permission(ctx.config, "clean2", {"status": "always", "hash": _compute_skill_hash(skill)})

    result = await tool_activate_skill(ctx, name="clean2")
    assert "go" in ctx.tools.extra
    assert "cannot" not in _text(result).lower()


@pytest.mark.asyncio
async def test_reload_into_a_phantom_call_keeps_working_tools(ctx, tmp_path):
    """Editing an active skill into a broken state must not strand it."""
    skill_dir = tmp_path / "regress"
    good = (
        "def go(ctx):\n    return 'fine'\n"
        "TOOLS = {'go': go}\n"
        "TOOL_DEFINITIONS = [{'type': 'function', 'function': {'name': 'go'}}]\n"
    )
    skill = _tiered_skill(skill_dir, "regress", good, "workspace")
    ctx.config.discovered_skills = [skill]
    ctx.config.skill_tool_owners = {"shell_background_start": "background"}
    _save_permission(ctx.config, "regress", {"status": "always", "hash": _compute_skill_hash(skill)})

    await tool_activate_skill(ctx, name="regress")
    assert "go" in ctx.tools.extra

    (skill_dir / "tools.py").write_text(PHANTOM_TOOLS_PY)
    result = await tool_activate_skill(ctx, name="regress")
    assert "shell_background_start" in _text(result)
    assert "go" in ctx.tools.extra, "the working tool must survive a bad reload"
    assert "start_it" not in ctx.tools.extra


# Two shapes the first version of the check missed, both produced by a real
# eval run as the model worked around successive validation failures:
#   context['shell_background_start'](...)   — subscript, not attribute
#   context.shell_background_start(...)      — receiver isn't named `ctx`
# The tool's first parameter is `ctx` by convention only; nothing enforces the
# name. Keying on the receiver was the mistake — the tool *name* is the signal.


def test_skill_validate_rejects_subscript_tool_call(ctx):
    result = _validate_tools_py(
        ctx,
        "psub",
        (
            "def get_tools(context):\n"
            "    def go():\n"
            "        return context['shell_background_start'](command='npm start')\n"
            "    return {'go': go}, []\n"
        ),
    )
    assert result.data["ok"] is False
    assert "shell_background_start" in _phantom_check(result)["message"]


def test_skill_validate_rejects_tool_call_on_renamed_receiver(ctx):
    result = _validate_tools_py(
        ctx,
        "prenamed",
        (
            "def go(context):\n"
            "    return context.shell_background_start(command='npm start')\n"
            "TOOLS = {'go': go}\nTOOL_DEFINITIONS = []\n"
        ),
    )
    assert result.data["ok"] is False
    assert "shell_background_start" in _phantom_check(result)["message"]


def test_skill_validate_rejects_self_tool_call(ctx):
    result = _validate_tools_py(
        ctx,
        "pself",
        (
            "class Helper:\n"
            "    def run(self):\n"
            "        return self.shell_background_start(command='npm start')\n"
            "TOOLS = {}\nTOOL_DEFINITIONS = []\n"
        ),
    )
    assert result.data["ok"] is False


def test_skill_validate_allows_same_named_local_variable(ctx):
    """A dict lookup that isn't a call is not a phantom tool call."""
    result = _validate_tools_py(
        ctx,
        "plocal",
        (
            "def go(ctx):\n"
            "    labels = {'shell_background_start': 'Start server'}\n"
            "    return labels['shell_background_start']\n"
            "TOOLS = {'go': go}\nTOOL_DEFINITIONS = []\n"
        ),
    )
    assert result.data["ok"] is True


# Bare tool names (`shell`, `wait` — 2 of ~107) collide with everyday Python
# methods. A scan of every bundled and contrib tools.py caught the broadened
# rule firing on `background` and `claude_code`, both of which call `.wait()`
# on real objects. Underscored names flag anywhere; bare ones need a ctx-ish
# receiver.


def test_skill_validate_allows_wait_on_a_real_object(ctx):
    result = _validate_tools_py(
        ctx,
        "pwait",
        (
            "import asyncio\n"
            "async def go(ctx):\n"
            "    ev = asyncio.Event()\n"
            "    await ev.wait()\n"
            "    return 'done'\n"
            "TOOLS = {'go': go}\nTOOL_DEFINITIONS = []\n"
        ),
    )
    assert result.data["ok"] is True, _phantom_check(result)


def test_skill_validate_allows_ctx_cancelled_wait(ctx):
    """A real idiom in this codebase — the receiver check must stay shallow."""
    result = _validate_tools_py(
        ctx,
        "pcancel",
        (
            "async def go(ctx):\n"
            "    await ctx.cancelled.wait()\n"
            "    return 'done'\n"
            "TOOLS = {'go': go}\nTOOL_DEFINITIONS = []\n"
        ),
    )
    assert result.data["ok"] is True, _phantom_check(result)


def test_skill_validate_allows_subprocess_wait(ctx):
    result = _validate_tools_py(
        ctx,
        "psubwait",
        (
            "import subprocess\n"
            "def go(ctx):\n"
            "    p = subprocess.Popen(['npm', 'start'])\n"
            "    return p.wait()\n"
            "TOOLS = {'go': go}\nTOOL_DEFINITIONS = []\n"
        ),
    )
    assert result.data["ok"] is True, _phantom_check(result)


def test_skill_validate_rejects_bare_tool_name_on_ctx(ctx):
    """`ctx.shell(...)` is still the mistake, bare name notwithstanding."""
    ctx.config.skill_tool_owners = {"shell": "core"}
    _write_ws_skill(
        ctx,
        "pbare",
        "name: pbare\ndescription: Bare.",
        tools_py=("def go(ctx):\n    return ctx.shell(command='ls')\nTOOLS = {'go': go}\nTOOL_DEFINITIONS = []\n"),
    )
    result = tool_skill_validate(ctx, path="skills/pbare")
    assert result.data["ok"] is False
    assert "shell" in _phantom_check(result)["message"]


# -- shadowing survives a reload (Copilot review, #700) --
#
# Shadowing is legal: execute_tool checks ctx.tools.extra before the global
# registry, so a later-activated skill's tool of the same name genuinely wins.
# Retracting purely by name therefore had a hole — renaming a shadowing tool
# removed the name outright, taking the still-active shadowed skill's version
# with it. Before this PR retraction never ran, so the loss is new.
