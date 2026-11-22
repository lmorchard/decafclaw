"""Workspace-containment checks for the read-only classifier (#1641).

A read-only verb still needs every argument that could be a path to resolve
inside the workspace. Symlinks that escape are rejected here, because the
classifier runs commands that no human or model reviews.
"""

import re
from pathlib import Path

_ATTACHED_PATH_RE = re.compile(r"^-([A-Za-z]+)(.+)$")


def path_values(token: str) -> list[str]:
    """Values in one token that could name a filesystem path."""
    if not token.startswith("-"):
        return [token]
    if "=" in token:
        value = token.split("=", 1)[1]
        return [value] if value else []
    if token.startswith("--"):
        return []
    attached = _ATTACHED_PATH_RE.match(token)
    if attached and attached.group(2)[:1] in "/~.":
        return [attached.group(2)]
    return []


def contained(value: str, cwd: Path, workspace: Path) -> bool:
    if value in ("", "-"):
        return True
    if value.startswith("~") or "$" in value or "`" in value:
        return False
    try:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved.is_relative_to(workspace)


def paths_contained(args: list[str], cwd: Path, workspace: Path) -> bool:
    after_double_dash = False
    for token in args:
        if token == "--":
            after_double_dash = True
            continue
        values = [token] if after_double_dash else path_values(token)
        for value in values:
            if not contained(value, cwd, workspace):
                return False
    return True
