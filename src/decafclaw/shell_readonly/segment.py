"""Quote-aware command splitting for the read-only classifier (#1641).

The front end is shared by every verb contract: it rejects any construct that
could redirect, execute, or escape, then splits the command into top-level
segments. Keeping it in one place is deliberate — a per-verb copy would drift,
and this is the security boundary every verb module relies on.
"""

import re
import shlex

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def split_segments(command: str) -> list[str] | None:
    """Split a command into quote-aware top-level segments.

    Returns ``None`` when the command contains a construct this classifier
    cannot prove read-only: unbalanced quotes, redirection, command or process
    substitution, subshell parentheses, expansion, or bare backgrounding.
    """
    segments: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    index = 0
    length = len(command)
    while index < length:
        char = command[index]
        if quote == "'":
            # Single quotes are entirely literal to the shell.
            buffer.append(char)
            if char == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\" and index + 1 < length:
                buffer.append(char)
                buffer.append(command[index + 1])
                index += 2
                continue
            if char in "$`":
                return None
            buffer.append(char)
            if char == '"':
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            buffer.append(char)
            index += 1
            continue
        if char == "\\":
            if index + 1 >= length:
                return None
            buffer.append(char)
            buffer.append(command[index + 1])
            index += 2
            continue
        if char in "$`":
            return None
        if char == "~" and (not buffer or buffer[-1] in " \t"):
            return None
        if char in "><()":
            return None
        if char == "&":
            if command.startswith("&&", index):
                segments.append("".join(buffer))
                buffer = []
                index += 2
                continue
            return None
        if char == "|":
            if command.startswith("||", index):
                segments.append("".join(buffer))
                buffer = []
                index += 2
                continue
            segments.append("".join(buffer))
            buffer = []
            index += 1
            continue
        if char in ";\n":
            segments.append("".join(buffer))
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    if quote is not None:
        return None
    segments.append("".join(buffer))
    result = [segment.strip() for segment in segments if segment.strip()]
    return result or None


def tokenize_segment(segment: str) -> list[str] | None:
    """Split one segment into argv, or ``None`` when it cannot be trusted.

    Rejects a leading environment assignment and any path-qualified verb. Only
    a bare PATH name is trusted: ``./tools/ls`` can be an agent-written script,
    and ``/tmp/evil/ls`` is an outside executable posing as a read-only verb.
    """
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None
    if _ENV_ASSIGN_RE.match(tokens[0]):
        return None
    if "/" in tokens[0] or "\\" in tokens[0]:
        return None
    return tokens
