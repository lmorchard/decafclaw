"""System prompt assembly from markdown files.

Loads prompt fragments from bundled files and optional workspace overrides.
Order: SOUL.md + AGENT.md + USER.md (if exists in workspace)
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Bundled prompt files (shipped with the code)
_PROMPTS_DIR = Path(__file__).parent

# Prompt files loaded in order
_PROMPT_FILES = ["SOUL.md", "AGENT.md"]


def load_system_prompt(config) -> str:
    """Assemble the system prompt from markdown files.

    For each prompt file (SOUL.md, AGENT.md):
    1. Check workspace override: data/workspace/{agent_id}/prompts/{file}
    2. Fall back to bundled: src/decafclaw/prompts/{file}

    Then append USER.md from workspace if it exists.
    """
    workspace_prompts = config.workspace_path / "prompts"
    sections = []

    for filename in _PROMPT_FILES:
        # Check workspace override first
        workspace_file = workspace_prompts / filename
        if workspace_file.exists():
            text = workspace_file.read_text().strip()
            log.info(f"Loaded prompt {filename} from workspace")
        else:
            bundled_file = _PROMPTS_DIR / filename
            if bundled_file.exists():
                text = bundled_file.read_text().strip()
            else:
                continue
        if text:
            sections.append(text)

    # USER.md only from workspace (not bundled — it's per-deployment)
    user_file = workspace_prompts / "USER.md"
    if user_file.exists():
        text = user_file.read_text().strip()
        if text:
            sections.append(text)
            log.info("Loaded USER.md from workspace")

    return "\n\n".join(sections)
