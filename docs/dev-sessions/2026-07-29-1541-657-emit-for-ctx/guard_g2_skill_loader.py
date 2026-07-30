#!/usr/bin/env python3
"""Guard G2 for issue #657 — the skill-loader import path (CLAUDE.md:63 rule).

Execs src/decafclaw/skills/project/tools.py through the SAME loader the real
skill system uses (`_import_tools_module`, which wraps
`importlib.util.spec_from_file_location` with no package context) and confirms
the emit helper resolves in the loaded module's namespace.

Why this guard exists: a *relative* import (`from ..events import emit_for_ctx`)
would satisfy criterion C1 and would pass an ordinary package import, but raises
under the real loader because the module has no package context. That regression
is exactly what this refactor invites.

Exits 0 on success, nonzero via AssertionError otherwise.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

from decafclaw.tools.skill_tools import _import_tools_module  # noqa: E402

TOOLS_PATH = REPO_ROOT / "src" / "decafclaw" / "skills" / "project" / "tools.py"

# Hardcoded path, no existence guard: a rename must raise loudly.
module = _import_tools_module("decafclaw_skill_project", TOOLS_PATH)
print("loader-path exec OK")

names = [n for n in vars(module) if n.endswith("emit_for_ctx")]
print(f"emit helper present: {bool(names)} {names}")

assert names, (
    "no emit_for_ctx helper resolved in the loader-exec'd project skill module; "
    "a relative import would produce exactly this"
)

resolved = vars(module)[names[0]]
assert callable(resolved), f"{names[0]} resolved to a non-callable: {resolved!r}"

# The helper must actually work under the loader-exec'd module, not merely be
# bound — a name bound to a broken forward ref would still pass the checks above.
class _NoManager:
    pass


class _WithManager:
    def __init__(self):
        self.manager = self

    def emit(self):  # pragma: no cover - identity is all that's checked
        pass


assert resolved(_NoManager()) is None, "expected None when ctx has no manager attribute"
holder = _WithManager()
assert resolved(holder) == holder.emit, "expected manager.emit when manager is present"
print("helper semantics OK under loader-exec (None-branch and manager.emit branch)")

print("PASS: G2 skill-loader import path")
sys.exit(0)
