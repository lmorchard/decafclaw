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


def test_testpaths_excludes_the_agent_data_directory():
    """`data/` is agent-writable by design. It must never be a test root."""
    for entry in _testpaths():
        top = pathlib.PurePosixPath(entry).parts[0]
        assert top != "data", (
            f"testpaths entry {entry!r} would collect the agent's own "
            "workspace as part of our test suite"
        )


def test_every_tracked_test_file_is_still_collected():
    """Narrowing the scope must not silently orphan real tests.

    Uses `git ls-files`, so gitignored trees (notably `data/`) are excluded
    for free and only tests we actually own are considered. This is the
    check that catches someone setting `testpaths = ["tests"]` and quietly
    dropping the colocated contrib skill tests.
    """
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
