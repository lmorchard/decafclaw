"""System prompt assembly from markdown files.

Loads prompt fragments from bundled files and optional workspace overrides.
Each section is wrapped in an XML tag at assembly time so the model can
distinguish identity from instructions from per-deployment facts from
skill metadata from skill bodies. See docs/context-composer.md.
"""

import html
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Bundled prompt files (shipped with the code)
_PROMPTS_DIR = Path(__file__).parent

# Prompt files loaded in order, paired with their wrapping XML tag
_PROMPT_FILES: list[tuple[str, str]] = [
    ("SOUL.md", "soul"),
    ("AGENT.md", "agent_role"),
]


def _wrap(tag: str, body: str) -> str:
    """Wrap body in <tag>\n…\n</tag>; return "" if body is empty.

    Callers rely on the empty-case returning "" so they can skip the
    section entirely — no dangling `<tag></tag>` wrappers.
    """
    if not body:
        return ""
    return f"<{tag}>\n{body}\n</{tag}>"


def load_system_prompt(config):
    """Assemble the system prompt from markdown files.

    For each prompt file (SOUL.md, AGENT.md):
    1. Check agent directory: data/{agent_id}/{file}
    2. Fall back to bundled: src/decafclaw/prompts/{file}

    Then append USER.md from agent directory if it exists, the skill
    catalog if any skills were discovered, and the bodies of always-
    loaded bundled skills. Each section is wrapped in an XML tag; see
    docs/context-composer.md.

    Returns:
        (prompt_text, discovered_skills) tuple
    """
    from ..skills import build_catalog_text, discover_skills

    agent_dir = config.agent_path
    sections: list[str] = []

    for filename, tag in _PROMPT_FILES:
        # Check workspace override first
        agent_file = agent_dir / filename
        if agent_file.exists():
            text = agent_file.read_text().strip()
            log.info(f"Loaded prompt {filename} from {agent_dir}")
        else:
            bundled_file = _PROMPTS_DIR / filename
            if bundled_file.exists():
                text = bundled_file.read_text().strip()
            else:
                continue
        wrapped = _wrap(tag, text)
        if wrapped:
            sections.append(wrapped)

    # USER.md only from workspace (not bundled — it's per-deployment)
    user_file = agent_dir / "USER.md"
    if user_file.exists():
        text = user_file.read_text().strip()
        wrapped = _wrap("user_context", text)
        if wrapped:
            sections.append(wrapped)
            log.info("Loaded USER.md from workspace")

    # Discover skills and append catalog
    skills = discover_skills(config)
    catalog = build_catalog_text(skills)
    wrapped = _wrap("skill_catalog", catalog)
    if wrapped:
        sections.append(wrapped)

    # Append always-loaded skill bodies to system prompt (bundled only —
    # trust boundary). Each body nests inside <loaded_skills> as a
    # <skill name="…"> block so the model can tell which body goes with
    # which skill.
    from ..skills import _BUNDLED_SKILLS_DIR
    bundled_dir = _BUNDLED_SKILLS_DIR.resolve()
    skill_blocks: list[str] = []
    for skill in skills:
        if not skill.always_loaded or not skill.body:
            continue
        if not Path(skill.location).resolve().is_relative_to(bundled_dir):
            log.warning(f"Ignoring always-loaded on non-bundled skill '{skill.name}' "
                        f"at {skill.location}")
            continue
        # Escape the name for XML attribute safety. Skill names come from
        # YAML frontmatter with no character validation at ingest today,
        # so defensively quote-escape here rather than assume they can't
        # contain `"`, `<`, `>`, or `&`.
        safe_name = html.escape(skill.name, quote=True)
        skill_blocks.append(f'<skill name="{safe_name}">\n{skill.body}\n</skill>')
        log.info(f"Always-loaded skill '{skill.name}' body appended to system prompt")

    if skill_blocks:
        sections.append(
            "<loaded_skills>\n" + "\n".join(skill_blocks) + "\n</loaded_skills>"
        )

    return "\n\n".join(sections), skills
