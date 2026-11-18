"""Guards for the skill tools.py future-annotations gate (#1302).

The skill loader execs each bundled tools.py in a fresh module object that can
leave sys.modules; with `from __future__ import annotations` (PEP 563) every
annotation becomes a string that `typing.get_type_hints` resolves through the
module's globals, so any non-builtin annotation raises NameError and the skill
is silently dropped from the catalog. The gate (scripts/check_skill_annotations.py,
composed into `make check`) fails on any bundled tools.py that imports it.

These tests protect three invariants:

- The gate passes on the committed bundled skills (so `make check` is green).
- The gate fails on a deliberate offender, and does not false-positive on a
  comment or docstring that merely *mentions* the import.
- The gate stays wired into `make check`, and CI runs `make check` — so the
  target actually gates merges.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = REPO_ROOT / "Makefile"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_skill_annotations.py"


def run_check(*tools_py: Path) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(CHECK_SCRIPT)] + [str(path) for path in tools_py]
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


def test_skill_annotations_pass_on_committed_skills():
    result = run_check()  # no args -> every bundled skill's tools.py
    assert result.returncode == 0, result.stdout + result.stderr


def test_check_rejects_future_annotations(tmp_path: Path):
    tools_py = tmp_path / "tools.py"
    tools_py.write_text(
        "from __future__ import annotations\n\n\ndef tool(ctx):\n    return None\n",
        encoding="utf-8",
    )
    result = run_check(tools_py)
    assert result.returncode == 1
    assert "tools.py" in result.stderr and "__future__" in result.stderr


def test_check_rejects_multi_name_future_import(tmp_path: Path):
    """`from __future__ import annotations, generator_stop` is legal Python and
    still enables PEP 563 — the gate must flag it, not just the bare form."""
    tools_py = tmp_path / "tools.py"
    tools_py.write_text(
        "from __future__ import annotations, generator_stop\n\n\ndef tool(ctx):\n    return None\n",
        encoding="utf-8",
    )
    result = run_check(tools_py)
    assert result.returncode == 1
    assert "__future__" in result.stderr


def test_check_rejects_aliased_future_import(tmp_path: Path):
    """`from __future__ import annotations as ann` still enables PEP 563 (the
    alias does not disable it) — the gate must flag it, not just the bare form."""
    tools_py = tmp_path / "tools.py"
    tools_py.write_text(
        "from __future__ import annotations as ann\n\n\ndef tool(ctx):\n    return None\n",
        encoding="utf-8",
    )
    result = run_check(tools_py)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "annotations as ann" in result.stderr


def test_check_ignores_docstring_mention(tmp_path: Path):
    """A docstring or comment that merely mentions the import (as
    contrib/skills/improvement-inbox/tools.py does) is not a violation."""
    tools_py = tmp_path / "tools.py"
    tools_py.write_text(
        '"""Do not add `from __future__ import annotations` here."""\n'
        "\n"
        "# from __future__ import annotations would break the loader\n"
        "from typing import TYPE_CHECKING\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    from decafclaw.context import Context\n"
        "\n"
        '\ndef tool(ctx: "Context"):\n'
        "    return None\n",
        encoding="utf-8",
    )
    result = run_check(tools_py)
    assert result.returncode == 0, result.stdout + result.stderr


def test_check_flags_unparseable_tools_py(tmp_path: Path):
    """A tools.py the loader cannot parse is a gate failure, not a silent skip."""
    tools_py = tmp_path / "tools.py"
    tools_py.write_text("def tool(:\n", encoding="utf-8")
    result = run_check(tools_py)
    assert result.returncode == 1
    assert "cannot parse" in result.stderr


def test_makefile_wires_gate_into_check():
    """The gate must be a prerequisite of `check`, so CI (which runs
    `make check`) gates it — the same composition rule as check-npm-policy."""
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^check:\s*(.*)$", line)
        if match:
            assert "check-skill-annotations" in match.group(1), (
                "`check` no longer depends on check-skill-annotations — the gate "
                "would run nowhere. Re-add it (see #1302)."
            )
            return
    raise AssertionError("no `check:` target found in the Makefile")


def test_ci_runs_make_check():
    """CI must invoke `make check` so the composed gate actually runs on PRs."""
    assert "make check" in CI_WORKFLOW.read_text(encoding="utf-8")
