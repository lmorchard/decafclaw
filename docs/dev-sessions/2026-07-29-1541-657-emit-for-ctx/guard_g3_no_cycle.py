#!/usr/bin/env python3
"""Guard G3 for issue #657 — no import cycle.

Imports all four consumer modules plus decafclaw.events in a fresh interpreter.
`events.py` imports nothing from `context`/`tools` today, so hosting the shared
helper there is cycle-free; this pins that. If the helper lands somewhere that
reintroduces the documented `context -> context_composer -> skill modules` cycle,
one of these imports raises ImportError ("cannot import name ... from partially
initialized module") and this guard fails.

Exits 0 on success, nonzero on any ImportError.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

MODULES = [
    "decafclaw.events",
    "decafclaw.context",
    "decafclaw.tools.sticky_tools",
    "decafclaw.tools.canvas_tools",
    "decafclaw.tools.checklist_tools",
    "decafclaw.skills.project.tools",
]

import importlib  # noqa: E402

for name in MODULES:
    importlib.import_module(name)
    print(f"  imported {name}")

print("imports OK, no cycle")
print("PASS: G3 no import cycle")
sys.exit(0)
