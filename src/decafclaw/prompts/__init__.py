"""System prompt assembly from markdown files.

Loads prompt fragments from bundled files and optional workspace overrides.
Order: SOUL.md + AGENT.md + USER.md (if exists in workspace) + skill catalog
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Bundled prompt files (shipped with the code)
_PROMPTS_DIR = Path(__file__).parent

# Prompt files loaded in order
_PROMPT_FILES = ["SOUL.md", "AGENT.md"]


def load_system_prompt(config):
    """Assemble the system prompt from markdown files.

    For each prompt file (SOUL.md, AGENT.md):
    1. Check agent directory: data/{agent_id}/{file}
    2. Fall back to bundled: src/decafclaw/prompts/{file}

    Then append USER.md from agent directory if it exists.
    Finally, append the skill catalog if any skills are discovered.

    Returns:
        (prompt_text, discovered_skills) tuple
    """
    from ..skills import build_catalog_text, discover_skills

    agent_dir = config.agent_path
    sections = []

    for filename in _PROMPT_FILES:
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
        if text:
            sections.append(text)

    # USER.md only from workspace (not bundled — it's per-deployment)
    user_file = agent_dir / "USER.md"
    if user_file.exists():
        text = user_file.read_text().strip()
        if text:
            sections.append(text)
            log.info("Loaded USER.md from workspace")

    # Discover skills and append catalog
    skills = discover_skills(config)
    catalog = build_catalog_text(skills)
    if catalog:
        sections.append(catalog)

    # Append always-loaded skill bodies to system prompt (bundled only — trust boundary)
    from ..skills import _BUNDLED_SKILLS_DIR
    bundled_dir = _BUNDLED_SKILLS_DIR.resolve()
    for skill in skills:
        if not skill.always_loaded or not skill.body:
            continue
        if not Path(skill.location).resolve().is_relative_to(bundled_dir):
            log.warning(f"Ignoring always-loaded on non-bundled skill '{skill.name}' "
                        f"at {skill.location}")
            continue
        sections.append(skill.body)
        log.info(f"Always-loaded skill '{skill.name}' body appended to system prompt")

    return "\n\n".join(sections), skills
