#!/usr/bin/env python3
"""Check that bundled skill tools.py files do not use `from __future__ import annotations`.

The skill loader (tools/skill_tools.py) compiles each tools.py and execs it in a
fresh module object. With PEP 563 (the future import), every annotation becomes
a string, and `typing.get_type_hints` — which `load_sub_config` calls on the
skill's SkillConfig dataclass — resolves those strings through
`sys.modules[cls.__module__]`. When that registration is gone (a failed
re-import pops it; a same-named skill shadows it), any non-builtin annotation
raises NameError and the skill is silently dropped from the catalog.

The rule (CLAUDE.md, Tools): quote the annotation instead — `ctx: "Context"` —
and never add `from __future__ import annotations` to a skill's tools.py.

This gate scans every bundled skill's tools.py (`src/decafclaw/skills/**/tools.py`)
and fails on any file that imports the name — including an aliased form such as
`from __future__ import annotations as ann`, which enables PEP 563 just the same.
It parses with `ast`, so a comment or docstring merely *mentioning* the import is
not flagged.

Run via: make check-skill-annotations (part of `make check`, so CI gates it).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "src" / "decafclaw" / "skills"

REMEDIATION = (
    'remove the import and quote annotations that need it (e.g. `ctx: "Context"`); '
    "see CLAUDE.md (Tools) and docs/skills.md"
)


def find_violations(tools_path: Path) -> list[str]:
    """Return the violations in one tools.py file (empty when clean)."""
    source = tools_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(tools_path))
    except SyntaxError as exc:
        return [f"{tools_path}:{exc.lineno}: cannot parse ({exc.msg}) — the loader would fail to load it"]
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "__future__":
            continue
        for alias in node.names:
            if alias.name == "annotations":
                statement = ast.get_source_segment(source, node) or "from __future__ import annotations"
                violations.append(
                    f"{tools_path}:{node.lineno}: `{statement}` — "
                    f"the skill loader execs tools.py in a module that can leave sys.modules, "
                    f"so postponed dataclass annotations can crash loading and silently drop the skill. {REMEDIATION}"
                )
    return violations


def main(argv: list[str]) -> int:
    if argv:
        tools_paths = [Path(arg).resolve() for arg in argv]
    else:
        tools_paths = sorted(SKILLS_DIR.rglob("tools.py"))

    violations: list[str] = []
    for tools_path in tools_paths:
        if not tools_path.is_file():
            violations.append(f"{tools_path}: not found")
            continue
        violations.extend(find_violations(tools_path))

    if violations:
        print(f"skill annotations check FAILED — {len(violations)} violation(s):", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    print(
        f"skill annotations check: OK — {len(tools_paths)} tools.py file(s) scanned, "
        f"no `from __future__ import annotations`"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
