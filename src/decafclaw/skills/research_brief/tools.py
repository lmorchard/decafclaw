"""Registered Python functions for the research_brief workflow's python steps.

These are called by the engine's ``python`` step kind. Each function receives
the full workflow state dict and returns a dict written to ``state[step_id]``.
"""


def count_draft_words(state: dict) -> dict:
    """Count words in the current draft body.

    Word_count runs immediately after draft, before any potential shorten
    step. Always counts the latest draft (latest-wins per state model),
    even on cycle revisits where state.shorten may exist from a prior cycle.
    """
    body = state.get("draft", {}).get("body", "")
    count = len(body.split()) if body.strip() else 0
    return {"count": count}
