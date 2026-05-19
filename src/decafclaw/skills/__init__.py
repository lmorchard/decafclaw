"""Skills system — discovery, parsing, and catalog for Agent Skills standard."""

import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Bundled skills directory (this package directory itself)
_BUNDLED_SKILLS_DIR = Path(__file__).parent


def _detect_repo_root() -> Path | None:
    """Auto-detect the DecafClaw repo root for `$DECAFCLAW_REPO`.

    Walks up from this file looking for a directory that has BOTH
    ``contrib/`` and ``pyproject.toml`` — the signature of a source
    checkout. Returns ``None`` when those markers aren't found (e.g.
    a wheel-only install), in which case `$DECAFCLAW_REPO` is left
    unset and `extra_skill_paths` entries that reference it will
    miss as before.
    """
    for parent in [_BUNDLED_SKILLS_DIR, *_BUNDLED_SKILLS_DIR.parents]:
        if (parent / "contrib").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    return None


_AUTO_REPO_ROOT = _detect_repo_root()


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
    auto_approve: bool = False  # bundled-only — skip activation confirmation
    # Scheduling: skills ship a SCHEDULE.md sidecar (not SKILL.md frontmatter).
    # See docs/schedules.md for the sidecar layout and overlay precedence.
    # Trust tier derived from placement at discovery time:
    #   "bundled" — src/decafclaw/skills/ (project author)
    #   "admin"   — data/{agent_id}/skills/ (user placed)
    #   "extra"   — extra_skill_paths entries (user configured)
    #   "workspace" — data/{agent_id}/workspace/skills/ (agent writable)
    # The bundled/admin/extra tiers are "trusted" — activation skips
    # confirmation, always-loaded is permitted. Workspace remains the
    # security boundary requiring explicit user approval.
    trust_tier: str = "bundled"


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


def _resolve_extra_skill_paths(config) -> list[Path]:
    """Resolve user-configured external skill paths.

    For each entry: expand $VARS and ~, then anchor relative paths to
    config.agent_path (matching vault_root). Order is preserved.

    Two variables are auto-populated from the detected source-checkout
    root (see ``_detect_repo_root``) when unset, so `extra_skill_paths`
    entries work out of the box:

    - ``$DECAFCLAW_REPO`` — the repo root.
    - ``$CONTRIB`` — shorthand for ``$DECAFCLAW_REPO/contrib``. Useful
      for compact entries like ``$CONTRIB/skills/<name>``.

    Explicit env-var settings always win. ``$CONTRIB`` is derived from
    the resolved value of ``$DECAFCLAW_REPO`` — set ``DECAFCLAW_REPO``
    to point at a different checkout and ``$CONTRIB`` follows.
    """
    if "DECAFCLAW_REPO" not in os.environ and _AUTO_REPO_ROOT is not None:
        os.environ["DECAFCLAW_REPO"] = str(_AUTO_REPO_ROOT)
    if "CONTRIB" not in os.environ and "DECAFCLAW_REPO" in os.environ:
        os.environ["CONTRIB"] = os.path.join(os.environ["DECAFCLAW_REPO"], "contrib")

    resolved: list[Path] = []
    for raw in config.extra_skill_paths:
        expanded = os.path.expandvars(str(raw))
        p = Path(expanded).expanduser()
        if not p.is_absolute():
            p = config.agent_path / p
        resolved.append(p)
    return resolved


def _iter_skill_dirs(base_path: Path) -> Iterator[Path]:
    """Yield skill-directory candidates for a scan entry.

    If ``base_path`` itself contains ``SKILL.md`` at its root, it IS a
    skill directory — yield it directly. Otherwise, if ``base_path`` is
    a directory, yield each immediate subdirectory (the caller checks
    for SKILL.md presence inside each).

    Yields nothing when ``base_path`` doesn't exist or isn't a directory.
    """
    if (base_path / "SKILL.md").exists():
        yield base_path
        return
    if not base_path.is_dir():
        return
    for entry in sorted(base_path.iterdir()):
        if entry.is_dir():
            yield entry


def discover_skills(config) -> list[SkillInfo]:
    """Scan skill directories and return discovered skills.

    Scan order (highest priority first):
    1. Workspace skills: data/{agent_id}/workspace/skills/
    2. Agent-level skills: data/{agent_id}/skills/
    3. Bundled skills: src/decafclaw/skills/
    4. config.extra_skill_paths (lowest — never shadows bundled)

    Each scan entry is interpreted polymorphically: if the entry path
    itself contains a SKILL.md at its root, it IS the skill (per-skill
    opt-in). Otherwise, each immediate subdirectory with a SKILL.md is
    a discovered skill (directory-of-skills, the original behavior).
    Both forms can be freely mixed in any scan entry — most useful for
    `extra_skill_paths`, where deployments opt into shared skills
    from `contrib/skills/` by referencing them directly.
    """
    # Each scan entry carries its trust tier so SkillInfo can record
    # where the skill came from. Extra-paths entries all share the
    # "extra" tier regardless of how many were configured.
    scan_entries: list[tuple[str, Path]] = [
        ("workspace", config.workspace_path / "skills"),
        ("admin", config.agent_path / "skills"),
        ("bundled", _BUNDLED_SKILLS_DIR),
        *(("extra", p) for p in _resolve_extra_skill_paths(config)),
    ]

    seen_names: dict[str, Path] = {}
    skills: list[SkillInfo] = []

    for tier, base_path in scan_entries:
        for skill_dir in _iter_skill_dirs(base_path):
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            info = parse_skill_md(skill_md)
            if info is None:
                continue

            info.trust_tier = tier

            # auto-approve and always-loaded are allowed for trusted
            # tiers (bundled / admin / extra) but denied for workspace —
            # workspace skills could be agent-authored, so they can't
            # self-approve or force themselves into the system prompt.
            if info.auto_approve and tier == "workspace":
                log.warning(
                    "Ignoring 'auto-approve: true' on workspace skill "
                    "'%s' at %s — workspace skills cannot auto-approve.",
                    info.name, skill_dir,
                )
                info.auto_approve = False
            if info.always_loaded and tier == "workspace":
                log.warning(
                    "Ignoring 'always-loaded: true' on workspace skill "
                    "'%s' at %s — workspace skills cannot always-load.",
                    info.name, skill_dir,
                )
                info.always_loaded = False

            # `skills_always_loaded` config: opt a trusted-tier skill
            # into always-loaded without editing its SKILL.md. Same
            # workspace restriction applies.
            if info.name in (config.skills_always_loaded or []):
                if tier == "workspace":
                    log.warning(
                        "Ignoring config.skills_always_loaded entry for "
                        "workspace skill '%s' — workspace skills cannot "
                        "always-load.", info.name,
                    )
                else:
                    info.always_loaded = True

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


def build_skill_tool_owners(skills: list[SkillInfo]) -> dict[str, str]:
    """Map every skill-provided tool name to its owning skill name.

    Imports each skill's ``tools.py`` (only for skills with
    ``has_native_tools``) and indexes their ``TOOL_DEFINITIONS``. Used
    by the unknown-tool error path and by ``tool_search`` to route
    hidden-tool-name guesses back to the skill that owns the tool.

    Importing tools.py here has a startup cost — same work as
    ``_load_native_tools`` (which the activation path runs eventually
    anyway), just front-loaded. ``init()`` is NOT called during the
    indexing; only the module is imported and ``TOOL_DEFINITIONS``
    is read.
    """
    owners: dict[str, str] = {}
    for skill in skills:
        if not skill.has_native_tools:
            continue
        try:
            from ..tools.skill_tools import _load_native_tools
            _, tool_defs, _ = _load_native_tools(skill)
        except Exception as exc:
            log.warning(
                "build_skill_tool_owners: failed to import tools.py for "
                "skill '%s': %s",
                skill.name, exc,
            )
            continue
        for td in tool_defs:
            tool_name = td.get("function", {}).get("name", "")
            if tool_name:
                # First-discovered wins on duplicate names (matches the
                # runtime resolution order).
                owners.setdefault(tool_name, skill.name)
    return owners


def build_catalog_text(skills: list[SkillInfo]) -> str:
    """Build the skill catalog text for injection into the system prompt."""
    if not skills:
        return ""

    # Separate always-loaded skills from on-demand. Always-loaded is
    # available to any trusted-tier skill (bundled / admin / extra);
    # workspace skills have already had the flag stripped at discovery.
    always_loaded = [s for s in skills if s.always_loaded]
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
            "The following skills exist but are not yet loaded. Call "
            "activate_skill(name) to load a skill's body and tools — "
            "the tools are not visible until the skill is activated.",
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
