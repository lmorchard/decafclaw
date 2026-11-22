"""``jq`` read-only contract (#1641).

Contract: a jq filter is read-only only when it cannot reach the process
environment. `jq -n env` prints the whole environment, which on a live agent
host holds API keys and tokens. The builtin has many spellings (``env``,
``$ENV``, ``env[]``, ``env["HOME"]``, ``env|to_entries``, ``$ENV.HOME``), and
enumerating them is a losing game, so the check is inverted: any non-flag
argument that mentions the env builtin in any form is refused. A field
literally named ``.env`` is still allowed, so a contained ``jq .env file.json``
keeps working. Filter files and other path arguments still have to resolve
inside the workspace.
"""

import re
from pathlib import Path

from ..containment import paths_contained

VERBS = frozenset({"jq"})

_JQ_ENV_RE = re.compile(r"(?:^|[^A-Za-z0-9_.])env(?![A-Za-z0-9_])|\$ENV(?![A-Za-z0-9_])", re.IGNORECASE)


def _jq_read_only(args: list[str]) -> bool:
    return not any(_JQ_ENV_RE.search(token) for token in args if not token.startswith("-"))


def allows(verb: str, args: list[str], cwd: Path, workspace: Path) -> bool:
    if not _jq_read_only(args):
        return False
    return paths_contained(args, cwd, workspace)
