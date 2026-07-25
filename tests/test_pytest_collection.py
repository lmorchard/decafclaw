"""Guards on what pytest collects (#682).

Without `testpaths`, pytest collects from the rootdir — which in a working
clone includes `data/`, the agent's own writable workspace. Anything the
agent scaffolds there matching `test_*.py` becomes part of our suite. That
actually happened: a `playtime` scratch project the agent generated failed
locally with `FileNotFoundError: 'playtime'` while the same commit passed
in CI and in worktrees.

The failure is invisible in the two places we normally look — CI has no
`data/` (it's gitignored) and worktrees have no `data/` of their own — so
it only ever appears on a developer's main clone, attributable to code
nobody on the project wrote.
"""

import pathlib
import subprocess
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pytest_config() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["tool"]["pytest"]["ini_options"]


def _testpaths() -> list[str]:
    return _pytest_config().get("testpaths", [])


def test_testpaths_is_declared():
    """An explicit collection scope, rather than 'whatever is on disk'."""
    assert _testpaths(), (
        "pyproject.toml declares no [tool.pytest.ini_options] testpaths, so "
        "pytest collects from the rootdir — including the agent's writable "
        "data/ directory. See #682."
    )


def _overlaps(entry: str, target: pathlib.Path) -> bool:
    """Do the `entry` and `target` trees intersect at all?

    Overlap in either direction, not a name match, because the two ways to
    get this wrong are different shapes:

    - an entry ABOVE the target (`"."`) names no directory but sweeps up
      every one, reintroducing precisely the problem testpaths prevents;
    - an entry BELOW it (`"data/decafclaw"`) never reaches `data/` itself
      yet still collects agent-authored files.

    A leading-component check misses the first; plain containment misses
    the second.
    """
    root = (REPO_ROOT / entry).resolve()
    return target.is_relative_to(root) or root.is_relative_to(target)


def test_overlaps_catches_entries_above_and_below_the_target():
    """The guard below is the actual deliverable here, so it gets its own
    test — a guard that silently stops guarding is worse than none."""
    data_dir = (REPO_ROOT / "data").resolve()
    assert _overlaps("data", data_dir)
    assert _overlaps("data/decafclaw", data_dir)   # below — plain containment misses
    assert _overlaps(".", data_dir)                # above — a name check misses
    assert _overlaps("./", data_dir)
    assert not _overlaps("tests", data_dir)
    assert not _overlaps("contrib", data_dir)


def test_testpaths_excludes_the_agent_data_directory():
    """`data/` is agent-writable by design. It must never be reachable from
    a test root — whether named directly or swept up by a broader entry."""
    data_dir = (REPO_ROOT / "data").resolve()
    offenders = [e for e in _testpaths() if _overlaps(e, data_dir)]
    assert not offenders, (
        f"testpaths entries {offenders} reach the agent's own workspace, "
        "which would collect agent-authored files as part of our suite (#682)"
    )


def test_every_tracked_test_file_is_still_collected():
    """Narrowing the scope must not silently orphan real tests.

    Uses `git ls-files`, so gitignored trees (notably `data/`) are excluded
    for free and only tests we actually own are considered. This is the
    check that catches someone setting `testpaths = ["tests"]` and quietly
    dropping the colocated contrib skill tests.
    """
    # `.git` is a directory in a normal clone and a file in a worktree, so
    # exists() rather than is_dir(). Checked explicitly because otherwise a
    # non-checkout (source zip, sdist) fails with a bare CalledProcessError
    # that says nothing about the requirement.
    assert (REPO_ROOT / ".git").exists(), (
        "this guard needs git metadata to tell tracked tests from "
        "agent-authored files; run it from a checkout"
    )
    tracked = subprocess.run(
        ["git", "ls-files", "test_*.py", "*/test_*.py", "**/test_*.py",
         "*_test.py", "*/*_test.py", "**/*_test.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert tracked, "expected to find tracked test files"

    roots = tuple(f"{pathlib.PurePosixPath(p).as_posix().rstrip('/')}/"
                  for p in _testpaths())
    orphaned = [f for f in tracked if not f.startswith(roots)]
    assert not orphaned, (
        f"these tracked test files fall outside testpaths {list(roots)} and "
        f"would no longer run: {orphaned}"
    )
