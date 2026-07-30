#!/usr/bin/env python3
"""Acceptance check C1 for issue #657.

CRITERION: The codebase SHALL contain exactly one definition of the `emit_for_ctx`
helper, AND each of the four consumer modules SHALL obtain it by import rather than
by local definition.

Static, stdlib-only probe. Counts:
  DEFS    -- `def _?emit_for_ctx` definitions anywhere under src/decafclaw
  IMPORTS -- how many of the four named consumers import the name into scope
  USES    -- how many of the four named consumers still have a call-shaped use

Asserts DEFS == 1, IMPORTS == 4, USES == 4.

Host-agnostic on purpose: the helper may live in events.py, context.py, or a brand
new module, and all spellings must pass identically -- so no host module name is
hardcoded. Consumer paths ARE hardcoded with no fixture setup and no try/except, so
that a renamed consumer raises FileNotFoundError loudly instead of passing silently.
"""

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "decafclaw"

# Hardcoded, verbatim from the issue. Do not glob these. Do not guard them.
CONSUMERS = [
    "src/decafclaw/tools/sticky_tools.py",
    "src/decafclaw/tools/canvas_tools.py",
    "src/decafclaw/tools/checklist_tools.py",
    "src/decafclaw/skills/project/tools.py",
]

NAMES = {"emit_for_ctx", "_emit_for_ctx"}

# Accepts either spelling of the definition.
DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+_?emit_for_ctx\b")

# A call-shaped use: the helper name followed by an open paren.
CALL_RE = re.compile(r"\b_?emit_for_ctx\s*\(")


def count_definitions() -> tuple[int, list[str]]:
    """Count `def _?emit_for_ctx` lines across every .py file under src/decafclaw."""
    hits = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if DEF_RE.match(line):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    return len(hits), hits


def imports_symbol(text: str) -> bool:
    """True if an import statement brings emit_for_ctx/_emit_for_ctx into scope.

    Uses ast so parenthesized/multiline imports and `as` aliases are handled, and
    so a `def` line can never be mistaken for an import.
    """
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound = alias.asname or alias.name
                if bound in NAMES or alias.name in NAMES:
                    return True
    return False


def calls_symbol(text: str) -> bool:
    """True if a non-definition line contains a call-shaped use of the helper."""
    for line in text.splitlines():
        if DEF_RE.match(line):
            continue
        if CALL_RE.search(line):
            return True
    return False


def main() -> None:
    defs_count, def_sites = count_definitions()

    importers = []
    callers = []
    for rel in CONSUMERS:
        # Hardcoded path, no try/except: a rename must raise FileNotFoundError.
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        if imports_symbol(text):
            importers.append(rel)
        if calls_symbol(text):
            callers.append(rel)

    print(f"DEFS = {defs_count} {def_sites}")
    print(f"IMPORTS = {len(importers)} {importers}")
    print(f"USES = {len(callers)} {callers}")

    assert defs_count == 1, f"expected 1 definition, found {defs_count}: {def_sites}"
    assert len(importers) == 4, (
        f"expected all 4 consumers to import the helper, found {len(importers)}: "
        f"{importers} (missing: {[c for c in CONSUMERS if c not in importers]})"
    )
    assert len(callers) == 4, (
        f"expected all 4 consumers to still call the helper, found {len(callers)}: "
        f"{callers} (missing: {[c for c in CONSUMERS if c not in callers]})"
    )

    print("PASS: one definition, imported and used by all four consumers.")


if __name__ == "__main__":
    main()
    sys.exit(0)
