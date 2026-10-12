"""Shared compaction notice text for the Python transports.

The ``compaction_end`` event is rendered by both the Mattermost post
(``mattermost.py``) and the interactive terminal
(``interactive_terminal.py``). This module owns the wording so each
transport only adds its own framing (Mattermost's ``📦`` emoji prefix,
the terminal's ``[ ]`` brackets) around the shared text. The web store
and the TUI are separate languages and keep their own copies of the
wording; this module is the reference they should be read against in
review.
"""


def compaction_notice(
    success: bool,
    before: int = 0,
    after: int = 0,
    elapsed: float = 0.0,
    est_tokens: int = 0,
) -> str:
    """Render the compaction_end notice shared by the Python transports.

    ``success`` is the event's ``success`` flag: ``False`` renders the
    failure notice (the conversation is unchanged); anything else
    renders the success notice with whatever details are available.
    Callers prepend their transport-specific framing to the result.
    """
    if success is False:
        return "Compaction failed — conversation unchanged"
    if elapsed < 60:
        duration = f"{elapsed:.0f}s"
    else:
        duration = f"{elapsed / 60:.1f}m"
    details = duration
    if before and after:
        details += f", {before} → {after} messages"
    if est_tokens:
        details += f", ~{est_tokens:,} tokens compacted"
    return f"Conversation compacted ({details})"
