"""Guard against test-name-string fixture gating (#1423).

A fixture that decides what to do from a substring of the test's name
(``"friction" in request.node.name``) silently stops applying when the test
is renamed — no error, just a missing mock. The original instance was
``mock_friction_llm`` in ``tests/conftest.py``, which gated the friction
skill's LLM call on the test name. That fixture was removed with the retired
friction skill (#1520); this guard keeps the *class* of bug from returning.

The rule: no conftest.py may reference a test node's ``name`` attribute.
Gate on an explicit marker (``request.node.get_closest_marker(...)``) or a
fixture dependency instead, and name the test in messages with ``nodeid``,
which is unambiguous across files.
"""

import ast
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _node_name_refs(tree: ast.AST) -> list[int]:
    """Line numbers of ``*.node.name`` attribute chains in a parsed tree.

    Any receiver, so a renamed fixture parameter (``req.node.name``) is
    caught too. Direct attribute access only: a ``getattr(node, "name")``
    spelling would slip past, but that is not how the original bug was
    written and no such use exists in this tree.
    """
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "name"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "node"
    ]


def _tracked_conftest_files() -> list[str]:
    """Conftest files we own, at any depth.

    ``git ls-files`` so gitignored trees (notably the agent-writable
    ``data/``) are excluded for free, mirroring test_pytest_collection.py.
    """
    assert (REPO_ROOT / ".git").exists(), (
        "this guard needs git metadata to find tracked conftest files; run it from a checkout"
    )
    out = subprocess.run(
        ["git", "ls-files", "conftest.py", "**/conftest.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return sorted(set(out))


def test_detector_flags_name_string_gating():
    """The detector is the deliverable, so it gets its own test: a guard that
    silently stops guarding is worse than none. This is the original bug,
    verbatim from the retired ``mock_friction_llm``."""
    src = 'if "friction" in request.node.name:\n    yield mock\n'
    assert _node_name_refs(ast.parse(src)) == [1]


def test_detector_flags_renamed_receiver():
    src = "if req.node.name == 'test_x':\n    apply()\n"
    assert _node_name_refs(ast.parse(src)) == [1]


def test_detector_ignores_marker_and_nodeid_usage():
    """The sanctioned alternatives must not trip the guard."""
    src = 'if request.node.get_closest_marker("live_llm"):\n    return\nmsg = f"failed in {request.node.nodeid}"\n'
    assert _node_name_refs(ast.parse(src)) == []


def test_no_conftest_gates_on_the_test_node_name():
    offenders: dict[str, list[int]] = {}
    for rel in _tracked_conftest_files():
        lines = _node_name_refs(ast.parse((REPO_ROOT / rel).read_text()))
        if lines:
            offenders[rel] = lines
    assert not offenders, (
        f"conftest files reference the test node's name: {offenders}. "
        "Renaming a test would silently change what the fixture applies. "
        "Gate on an explicit marker or fixture dependency; name tests in "
        "messages with nodeid (#1423)."
    )
