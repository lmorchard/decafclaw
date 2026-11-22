"""Registry and polarity tests for the read-only classifier split (#1660).

The adversarial suites pin behavior. These tests pin the *structure* the
refactor introduced: every verb has exactly one owner module, the vocabulary
did not silently change, and an unregistered verb can never be read-only.
"""

import pytest

from decafclaw.shell_readonly import is_read_only_command
from decafclaw.shell_readonly.verbs import VERB_OWNERS

# The pre-refactor `_READ_ONLY_VERBS` vocabulary, frozen so neither a typo nor
# a dropped registration can shrink the tier silently.
EXPECTED_VERBS = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "grep",
        "egrep",
        "fgrep",
        "rg",
        "find",
        "fd",
        "tree",
        "pwd",
        "which",
        "jq",
        "wc",
        "du",
        "file",
        "stat",
        "echo",
        "diff",
        "cut",
        "sort",
        "uniq",
        "basename",
        "dirname",
        "realpath",
        "readlink",
        "git",
        "gh",
        "curl",
    }
)


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "afile.txt").write_text("hello\n", encoding="utf-8")
    return tmp_path


def test_registered_vocabulary_matches_pre_refactor_set():
    assert frozenset(VERB_OWNERS) == EXPECTED_VERBS


def test_every_verb_has_exactly_one_owner_module():
    for verb, owner in VERB_OWNERS.items():
        assert verb in owner.VERBS, f"{verb} is registered to a module that does not claim it"
    # No verb appears in two modules' VERBS sets without the registry raising.
    claimed: list[str] = []
    for owner in set(VERB_OWNERS.values()):
        claimed.extend(owner.VERBS)
    assert sorted(claimed) == sorted(EXPECTED_VERBS)


@pytest.mark.parametrize("command", ["frobnicate --version", "sed -n 1p afile.txt", "xargs ls", "./ls"])
def test_unregistered_verb_is_never_read_only(ws, command):
    assert is_read_only_command(command, cwd=ws, workspace=ws) is False


@pytest.mark.parametrize(
    "command",
    [
        "git config --definitely-not-a-read-action",
        "gh pr frobnicate",
        "gh auth token",
    ],
)
def test_unknown_action_on_an_allowlisted_contract_is_rejected(ws, command):
    """Where a verb's contract is an allowlist, an unknown entry is a denial."""
    assert is_read_only_command(command, cwd=ws, workspace=ws) is False


@pytest.mark.parametrize(
    "command",
    [
        "jq -n env[]",
        "jq -n 'env|to_entries'",
        "jq -n 'env[\"HOME\"]'",
    ],
)
def test_env_leak_cases_stay_rejected_after_the_split(ws, command):
    assert is_read_only_command(command, cwd=ws, workspace=ws) is False
