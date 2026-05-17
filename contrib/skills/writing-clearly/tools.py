"""writing-clearly skill — apply Strunk's *Elements of Style* via a clean-context child agent."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from decafclaw.media import ToolResult
from decafclaw.tools.delegate import tool_delegate_task

log = logging.getLogger(__name__)


@dataclass
class SkillConfig:
    model: str = field(
        default="",
        metadata={"env_alias": "WRITING_CLEARLY_MODEL"},
    )


_skill_config: SkillConfig | None = None
_corpus_path = Path(__file__).parent / "elements-of-style.md"


def init(config, skill_config: SkillConfig) -> None:
    global _skill_config
    _skill_config = skill_config


_CHILD_PROMPT_TEMPLATE = """\
You are a copy editor applying William Strunk Jr.'s *The Elements of Style* (1918). Revise the draft below for clarity and concision while preserving the author's meaning, voice, and intent. Do not rewrite for style or tone beyond what Strunk's rules require.

Editing pass:
1. Read the rules in <strunk_rules> end-to-end.
2. Apply them to the draft, prioritizing: omit needless words, active voice, definite/specific/concrete language, positive form, related words together, emphatic words at sentence end.
3. Preserve technical terms, names, code, links, and any quoted material exactly.
4. If the draft is already clean by Strunk's standards, return it unchanged.

Output format:
- Return ONLY the revised prose. No preamble, no explanation, no diff, no fenced block — just the edited text ready to paste.

Focus for this pass: {focus}

<strunk_rules>
{corpus}
</strunk_rules>

<draft>
{draft}
</draft>
"""


async def tool_edit_with_strunk(
    ctx,
    draft: str,
    focus: str = "",
) -> ToolResult:
    """Revise a prose draft using Strunk's *Elements of Style* in a child agent."""
    if not draft or not draft.strip():
        return ToolResult(text="[error: draft is required]")

    try:
        corpus = _corpus_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.error("edit_with_strunk: failed to read corpus: %s", exc)
        return ToolResult(text=f"[error: failed to read elements-of-style.md: {exc}]")

    focus_text = focus.strip() or "general clarity and concision"
    task = _CHILD_PROMPT_TEMPLATE.format(
        focus=focus_text,
        corpus=corpus,
        draft=draft,
    )

    log.info(
        "[tool:edit_with_strunk] draft=%dB focus=%r",
        len(draft),
        focus_text,
    )

    model = _skill_config.model if _skill_config and _skill_config.model else ""
    return await tool_delegate_task(ctx, task=task, model=model)


TOOLS = {
    "edit_with_strunk": tool_edit_with_strunk,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "edit_with_strunk",
            "description": (
                "Revise a prose draft for clarity and concision using "
                "Strunk's *The Elements of Style* (1918). Runs in a clean-"
                "context child agent — the rulebook never enters this "
                "conversation. Returns the revised prose ready to paste. "
                "Use whenever a human will read the writing: docs, commit "
                "messages, PR descriptions, replies, blog posts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "draft": {
                        "type": "string",
                        "description": "The prose draft to edit. Inline the full text.",
                    },
                    "focus": {
                        "type": "string",
                        "description": (
                            "Optional editing focus, e.g. 'omit needless "
                            "words', 'active voice', 'tighten openings'. "
                            "Leave blank for general clarity pass."
                        ),
                    },
                },
                "required": ["draft"],
            },
        },
    },
]
