"""Skills system — discovery, parsing, and catalog for Agent Skills standard."""

import logging
import os
import re
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
    shell_patterns: list[str] = field(default_factory=list)
    context: str = "inline"  # "inline" or "fork"
    argument_hint: str = ""
    model: str = ""  # named model config, empty = inherit conversation model
    requires_skills: list[str] = field(default_factory=list)
    always_loaded: bool = False
    schedule: str = ""  # cron expression, empty = not scheduled
    enabled: bool = True  # can be disabled via frontmatter
    auto_approve: bool = False  # bundled-only — skip activation confirmation


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
    # Entries like "shell(pattern)" are scoped shell approvals
    allowed_tools_raw = meta.get("allowed-tools", "")
    allowed_tools, shell_patterns = _parse_allowed_tools(allowed_tools_raw)

    return SkillInfo(
        name=name,
        description=description,
        location=skill_dir,
        body=body.strip(),
        has_native_tools=has_native_tools,
        requires_env=requires_env,
        user_invocable=meta.get("user-invocable", meta.get("user_invocable", True)),
        allowed_tools=allowed_tools,
        shell_patterns=shell_patterns,
        context=meta.get("context", "inline"),
        argument_hint=meta.get("argument-hint", ""),
        model=meta.get("model", meta.get("effort", "")),
        requires_skills=_coerce_str_list(meta.get("required-skills", [])),
        always_loaded=bool(meta.get("always-loaded", False)),
        schedule=str(meta.get("schedule") or ""),
        enabled=_coerce_bool(meta.get("enabled", True)),
        auto_approve=bool(meta.get("auto-approve", False)),
    )


_SCOPED_SHELL_RE = re.compile(r"^shell\((.+)\)$")


def _parse_allowed_tools(raw: str) -> tuple[list[str], list[str]]:
    """Parse allowed-tools string into (tool_names, shell_patterns).

    Entries like "shell(pattern)" are extracted as scoped shell patterns.
    Bare "shell" and other tool names go into the tool_names list.
    """
    if not raw:
        return [], []

    tools: list[str] = []
    patterns: list[str] = []

    # Handle both comma-separated string and YAML list formats
    entries = raw if isinstance(raw, list) else raw.split(",")
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        m = _SCOPED_SHELL_RE.match(entry)
        if m:
            patterns.append(m.group(1).strip())
        else:
            tools.append(entry)

    return tools, patterns


def _coerce_str_list(value) -> list[str]:
    """Coerce a YAML value to a list of strings (handles scalar or None)."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _coerce_bool(value) -> bool:
    """Coerce a YAML value to bool (handles string 'false', etc.)."""
    if isinstance(value, bool):
        return value
    return str(value).lower() not in ("false", "0", "no", "off")


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
    bundled_dir = _BUNDLED_SKILLS_DIR.resolve()

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

            # auto-approve is bundled-only (trust boundary). Admin and
            # workspace skills declaring it get the flag stripped and
            # a warning logged.
            if info.auto_approve:
                is_bundled = skill_dir.resolve().is_relative_to(bundled_dir)
                if not is_bundled:
                    log.warning(
                        "Ignoring 'auto-approve: true' on non-bundled skill "
                        "'%s' at %s — only bundled skills may auto-approve.",
                        info.name, skill_dir,
                    )
                    info.auto_approve = False

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

    # Separate always-loaded (bundled only) from on-demand skills
    bundled_dir = _BUNDLED_SKILLS_DIR.resolve()
    always_loaded = [
        s for s in skills
        if s.always_loaded and Path(s.location).resolve().is_relative_to(bundled_dir)
    ]
    always_loaded_names = {s.name for s in always_loaded}
    on_demand = [s for s in skills if s.name not in always_loaded_names]

    lines = []

    if always_loaded:
        lines.extend([
            "## Active Skills",
            "",
            "The following skills are always active — their tools are available now.",
            "",
        ])
        for skill in always_loaded:
            lines.append(f"- **{skill.name}**: {skill.description}")
        lines.append("")

    if on_demand:
        lines.extend([
            "## Available Skills",
            "",
            "The following skills can be activated. Their tools are NOT available until you "
            "call activate_skill first. You MUST activate a skill before using any of its tools.",
            "",
        ])
        for skill in on_demand:
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
