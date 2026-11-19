"""Every discovered skill's `allowed-tools` entry must name a registered tool.

A skill can declare ``allowed-tools: X`` in SKILL.md while ``X`` is never
registered (the retired ``friction`` skill did exactly this — #1299), and
activation succeeds silently: the entry matches nothing. This test walks every
discovered skill and fails if any ``allowed-tools`` entry does not resolve to a
registered tool name.

"Registered" means defined in the codebase: a core/built-in tool, or a tool
exported by a trusted skill's ``tools.py``. The set is built from every skill
directory — not just discovered skills — so env-gated skills (whose tools are
defined but not active without their API key) still count; a skill's
``allowed-tools`` may name a tool owned by such a skill (e.g. ``ingest`` ->
``tabstack_extract_markdown``, where ``tabstack`` needs ``TABSTACK_API_KEY``).

Workspace-tier skills are excluded from the set, matching
``build_skill_tool_owners``: their ``tools.py`` is agent-authored and importing
it would exec untrusted code. In the test environment there are no workspace
skills, so this changes nothing in practice — it keeps the check honest about
what it imports.
"""

import re
from pathlib import Path

from decafclaw.skills import (
    SKILL_CAPABILITY_TIERS,
    _iter_skill_dirs,
    discover_skills,
    skill_scan_entries,
)
from decafclaw.tools import TOOLS as CORE_TOOLS
from decafclaw.tools.skill_tools import _import_tools_module


def _skill_tool_names(module_name: str, tools_py: Path) -> set[str]:
    """Tool names a skill's ``tools.py`` defines (TOOLS keys + TOOL_DEFINITIONS).

    Returns an empty set when the module fails to import (e.g. a missing
    optional dependency) — such a skill registers no tools, so any
    ``allowed-tools`` entry pointing at one of them is phantom.
    """
    try:
        module = _import_tools_module(module_name, tools_py)
    except Exception:
        return set()

    names: set[str] = set()
    tools = getattr(module, "TOOLS", None)
    if isinstance(tools, dict):
        names.update(tools.keys())
    for td in getattr(module, "TOOL_DEFINITIONS", None) or []:
        if isinstance(td, dict):
            name = td.get("function", {}).get("name")
            if isinstance(name, str) and name:
                names.add(name)
    return names


def _registered_tool_names(config) -> set[str]:
    """Every tool name defined in the codebase: core tools + trusted skill tools.

    Enumerates every skill directory across all scan tiers and imports each
    ``tools.py`` directly (rather than via ``discover_skills``) so that
    env-gated skills still contribute their defined tools to the set.
    """
    registered = set(CORE_TOOLS.keys())
    for tier, base in skill_scan_entries(config):
        if tier not in SKILL_CAPABILITY_TIERS:
            continue  # never import agent-authored workspace code (see module docstring)
        for d in _iter_skill_dirs(base):
            tools_py = d / "tools.py"
            if not tools_py.is_file():
                continue
            module_name = "allowed_tools_test_" + re.sub(r"\W", "_", str(d.resolve()))
            registered |= _skill_tool_names(module_name, tools_py)
    return registered


def _unresolved_allowed_tools(skills, registered: set[str]) -> list[str]:
    """``(skill) entry`` for every ``allowed-tools`` entry that does not resolve."""
    return [
        f"{skill.name} ({skill.trust_tier}): {entry}"
        for skill in skills
        for entry in skill.allowed_tools
        if entry not in registered
    ]


def test_allowed_tools_entries_resolve(config):
    """Every discovered skill's ``allowed-tools`` entry names a registered tool.

    Walks every shipped skill — bundled (always discovered) plus contrib, opted
    in via ``$CONTRIB/skills`` so the check covers the whole repo, not just the
    bundled tier.
    """
    config.extra_skill_paths = ["$CONTRIB/skills"]

    registered = _registered_tool_names(config)
    skills = discover_skills(config)

    problems = _unresolved_allowed_tools(skills, registered)
    assert not problems, (
        "These allowed-tools entries do not resolve to a registered tool name — "
        "activation would succeed silently while the entry matches nothing: " + "; ".join(sorted(problems))
    )


def test_phantom_allowed_tools_entry_is_flagged(config):
    """The friction case: a skill declaring an entry that registers no such tool.

    Seeds the exact defect #1299 retired — ``allowed-tools`` naming a tool that
    is not registered anywhere — and confirms the check catches it.
    """
    skill_dir = config.workspace_path / "skills" / "phantom"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: phantom\n"
        "description: Declares an allowed-tool it never registers.\n"
        "allowed-tools: definitely_not_a_real_tool\n"
        "---\n"
        "Body.\n"
    )

    registered = _registered_tool_names(config)
    skills = discover_skills(config)
    phantom = next(s for s in skills if s.name == "phantom")

    problems = _unresolved_allowed_tools([phantom], registered)
    assert any("definitely_not_a_real_tool" in p for p in problems), (
        "Expected the phantom allowed-tools entry to be flagged, but it resolved: " + f"{problems}"
    )
