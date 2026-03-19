"""Skills system — discovery, parsing, and catalog for Agent Skills standard."""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Bundled skills directory (this package directory itself)
_BUNDLED_SKILLS_DIR = Path(__file__).parent


@dataclass
class SkillInfo:
    """Metadata for a discovered skill."""

    name: str
    description: str
    location: Path
    body: str = ""
    has_native_tools: bool = False
    requires_env: list[str] = field(default_factory=list)
    user_invocable: bool = True
    allowed_tools: list[str] = field(default_factory=list)
    context: str = "inline"  # "inline" or "fork"
    argument_hint: str = ""


def parse_skill_md(path: Path) -> SkillInfo | None:
    """Parse a SKILL.md file into a SkillInfo, or None if invalid.

    Lenient: warns on issues, returns None only if name/description
    is missing or YAML is completely unparseable.
    """
    try:
        text = path.read_text()
    except OSError as e:
        log.warning(f"Cannot read {path}: {e}")
        return None

    # Split frontmatter from body
    meta, body = _split_frontmatter(text)
    if meta is None:
        log.warning(f"No valid YAML frontmatter in {path}")
        return None

    name = meta.get("name")
    description = meta.get("description")

    if not name:
        log.warning(f"Missing 'name' in {path}")
        return None
    if not description:
        log.warning(f"Missing 'description' in {path}")
        return None

    # Check for tools.py in the same directory
    skill_dir = path.parent
    has_native_tools = (skill_dir / "tools.py").exists()

    # Parse requires.env
    requires = meta.get("requires", {})
    requires_env = requires.get("env", []) if isinstance(requires, dict) else []

    # Parse allowed-tools: comma-separated string → list
    allowed_tools_raw = meta.get("allowed-tools", "")
    allowed_tools = [
        t.strip() for t in allowed_tools_raw.split(",") if t.strip()
    ] if allowed_tools_raw else []

    return SkillInfo(
        name=name,
        description=description,
        location=skill_dir,
        body=body.strip(),
        has_native_tools=has_native_tools,
        requires_env=requires_env,
        user_invocable=meta.get("user-invocable", meta.get("user_invocable", True)),
        allowed_tools=allowed_tools,
        context=meta.get("context", "inline"),
        argument_hint=meta.get("argument-hint", ""),
    )


def _split_frontmatter(text: str) -> tuple[dict | None, str]:
    """Split YAML frontmatter from markdown body.

    Returns (parsed_dict, body_str). parsed_dict is None
    if no valid frontmatter delimiters found or YAML is invalid.
    """
    stripped = text.strip()
    if not stripped.startswith("---"):
        return None, text

    # Find the closing --- (skip the opening one)
    end = stripped.find("\n---", 3)
    if end == -1:
        return None, text

    frontmatter_str = stripped[3:end].strip()
    body = stripped[end + 4:]  # skip past \n---

    try:
        parsed = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as e:
        log.warning(f"Unparseable YAML frontmatter: {e}")
        return None, text

    if not isinstance(parsed, dict):
        return None, text

    return parsed, body


def discover_skills(config) -> list[SkillInfo]:
    """Scan skill directories and return discovered skills.

    Scan order (highest priority first):
    1. Workspace skills: data/{agent_id}/workspace/skills/
    2. Agent-level skills: data/{agent_id}/skills/
    3. Bundled skills: src/decafclaw/skills/
    """
    scan_paths = [
        config.workspace_path / "skills",
        config.agent_path / "skills",
        _BUNDLED_SKILLS_DIR,
    ]

    seen_names: dict[str, Path] = {}
    skills: list[SkillInfo] = []

    for base_path in scan_paths:
        if not base_path.is_dir():
            continue

        for skill_dir in sorted(base_path.iterdir()):
            if not skill_dir.is_dir():
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            info = parse_skill_md(skill_md)
            if info is None:
                continue

            # Check requires.env
            missing_env = [v for v in info.requires_env if not os.environ.get(v)]
            if missing_env:
                log.debug(f"Skipping skill '{info.name}': missing env vars {missing_env}")
                continue

            # Name collision: first-found wins
            if info.name in seen_names:
                log.debug(
                    f"Skill '{info.name}' at {skill_dir} shadowed by {seen_names[info.name]}"
                )
                continue

            seen_names[info.name] = skill_dir
            skills.append(info)

    log.info(f"Discovered {len(skills)} skills: {[s.name for s in skills]}")
    return skills


def build_catalog_text(skills: list[SkillInfo]) -> str:
    """Build the skill catalog text for injection into the system prompt."""
    if not skills:
        return ""

    lines = [
        "## Available Skills",
        "",
        "The following skills can be activated. Their tools are NOT available until you "
        "call activate_skill first. You MUST activate a skill before using any of its tools.",
        "",
    ]
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")

    return "\n".join(lines)


def find_command(name: str, discovered_skills: list[SkillInfo]) -> SkillInfo | None:
    """Find a user-invokable command by name. Returns first match."""
    for skill in discovered_skills:
        if skill.user_invocable and skill.name == name:
            return skill
    return None


def list_commands(discovered_skills: list[SkillInfo]) -> list[SkillInfo]:
    """Return all user-invokable commands, sorted by name."""
    return sorted(
        [s for s in discovered_skills if s.user_invocable],
        key=lambda s: s.name,
    )
