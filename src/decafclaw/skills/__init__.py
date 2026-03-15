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
    disable_model_invocation: bool = False


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
    frontmatter, body = _split_frontmatter(text)
    if frontmatter is None:
        log.warning(f"No YAML frontmatter in {path}")
        return None

    try:
        meta = yaml.safe_load(frontmatter)
    except yaml.YAMLError as e:
        log.warning(f"Unparseable YAML in {path}: {e}")
        return None

    if not isinstance(meta, dict):
        log.warning(f"YAML frontmatter is not a mapping in {path}")
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

    return SkillInfo(
        name=name,
        description=description,
        location=skill_dir,
        body=body.strip(),
        has_native_tools=has_native_tools,
        requires_env=requires_env,
        user_invocable=meta.get("user-invocable", True),
        disable_model_invocation=meta.get("disable-model-invocation", False),
    )


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split YAML frontmatter from markdown body.

    Returns (frontmatter_str, body_str). frontmatter_str is None
    if no valid frontmatter delimiters found.
    """
    stripped = text.strip()
    if not stripped.startswith("---"):
        return None, text

    # Find the closing ---
    end = stripped.find("---", 3)
    if end == -1:
        return None, text

    frontmatter = stripped[3:end].strip()
    body = stripped[end + 3:]
    return frontmatter, body


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
