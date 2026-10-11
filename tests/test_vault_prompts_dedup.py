"""Guards against vault instruction bloat and duplication across prompts (#1050).

Ensures clear boundaries:
- AGENT.md: minimal high-level overview with behavioral guardrails.
- vault SKILL.md: tool contracts and workflows (no duplicate gardening rules).
- vault AGENTS.md: user-specific vault conventions, structure, and permissions.
"""

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).parents[1]


def test_agent_md_vault_section_is_condensed():
    agent_md = (_repo_root() / "src" / "decafclaw" / "prompts" / "AGENT.md").read_text(encoding="utf-8")
    assert "## Vault — Your Persistent Memory" in agent_md

    # Extract the Vault section
    lines = agent_md.splitlines()
    vault_lines: list[str] = []
    in_vault = False
    for line in lines:
        if line.startswith("## Vault — Your Persistent Memory"):
            in_vault = True
            vault_lines.append(line)
        elif in_vault and line.startswith("## "):
            break
        elif in_vault:
            vault_lines.append(line)

    vault_text = "\n".join(vault_lines)

    # Must be condensed to under 25 lines (previously 39 lines)
    assert len([line for line in vault_lines if line.strip()]) < 25

    # Points to vault skill and vault guide for specific contracts and conventions
    assert "vault" in vault_text
    assert "AGENTS.md" in vault_text

    # Retains core behavioral guardrails
    assert 'Search before saying "I don\'t know."' in vault_text
    assert "Vault pages are NOT skills." in vault_text
    assert "Journal mistakes later, not now." in vault_text

    # No longer duplicates redundant bullet breakdown of agent/ subdirs
    assert "- `agent/pages/` —" not in vault_text
    assert "- `agent/journal/` —" not in vault_text


def test_vault_skill_md_no_longer_duplicates_gardening_rules():
    skill_md = (_repo_root() / "src" / "decafclaw" / "skills" / "vault" / "SKILL.md").read_text(encoding="utf-8")

    # Retains tool contracts and operational workflows
    assert "## Your Home Folder" in skill_md
    assert "## Journal vs Pages" in skill_md
    assert "## Boundaries" in skill_md
    assert "## Editing Sections" in skill_md
    assert "## Organizing with Folders" in skill_md
    assert "## Navigating the Knowledge Graph" in skill_md
    assert "## When to Consult the Vault" in skill_md
    assert "## When to Update the Vault" in skill_md

    # Deduplicated: Gardening rules belong in user AGENTS.md and garden/dream skills
    assert "## Vault Gardening Rules" not in skill_md
    assert "tl;dr summaries." not in skill_md
