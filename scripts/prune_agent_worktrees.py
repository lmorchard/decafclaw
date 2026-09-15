#!/usr/bin/env python3
"""Prune git worktrees and local branches whose work has already landed.

Agent-driven development leaves a lot of debris: this repo accumulated 21
worktrees and 30 local branches, most of them holding work that had already
merged. This script decides which of those are safe to drop and, with
``--apply``, drops them.

A branch is considered LANDED if either:

* it is an ancestor of ``origin/main`` (merge commit), or
* it has a MERGED pull request and its last commit predates the merge
  (squash merge, which leaves the branch off main's ancestry).

Anything else is reported as UNLANDED and never touched -- that includes
abandoned experiments, which are the whole reason this isn't just
``git worktree prune``.

Dry run by default. See ``make prune-worktrees-dry`` / ``make prune-worktrees``.
"""

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROTECTED = {"main"}


def git(*args: str, check: bool = True) -> str:
    """Run a git command in the repo root and return stripped stdout."""
    proc = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


@dataclass
class Branch:
    name: str
    verdict: str = "UNLANDED"
    reason: str = ""
    worktree: Path | None = None
    dirty_tracked: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)

    @property
    def landed(self) -> bool:
        return self.verdict == "LANDED"


def scan_worktrees() -> tuple[dict[str, Path], list[tuple[Path, str]]]:
    """Return (branch name -> worktree path, [(path, sha) for detached ones]).

    Detached worktrees have no branch to key off, so a branch-driven scan would
    miss them entirely and ``git worktree prune`` only reaps directories that
    are already gone. They get reported so a human can decide.
    """
    attached: dict[str, Path] = {}
    detached: list[tuple[Path, str]] = []
    path: Path | None = None
    sha = ""
    claimed = False
    for line in git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            if path is not None and path != REPO_ROOT and not claimed:
                detached.append((path, sha))
            path = Path(line.removeprefix("worktree "))
            claimed = False
        elif line.startswith("HEAD "):
            sha = line.removeprefix("HEAD ")[:8]
        elif line.startswith("branch ") and path is not None:
            claimed = True
            if path != REPO_ROOT:
                attached[line.removeprefix("branch refs/heads/")] = path
    if path is not None and path != REPO_ROOT and not claimed:
        detached.append((path, sha))
    return attached, detached


def merged_prs() -> dict[str, str]:
    """Map head branch -> mergedAt timestamp, for every merged PR."""
    if shutil.which("gh") is None:
        print("warning: gh not found; squash-merged branches will read as UNLANDED")
        return {}
    proc = subprocess.run(
        ["gh", "pr", "list", "--state", "merged", "--limit", "500", "--json", "headRefName,mergedAt"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(f"warning: gh pr list failed: {proc.stderr.strip()}")
        return {}
    return {pr["headRefName"]: pr["mergedAt"] for pr in json.loads(proc.stdout or "[]") if pr.get("mergedAt")}


def classify(name: str, prs: dict[str, str]) -> tuple[str, str]:
    """Decide whether a branch's work is already in origin/main."""
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", name, "origin/main"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if ancestor.returncode == 0:
        return "LANDED", "ancestor of origin/main"

    merged_at = prs.get(name)
    if merged_at:
        last = git("log", "-1", "--format=%cI", name)
        if last <= merged_at:
            return "LANDED", f"squash-merged {merged_at[:10]}"
        return "UNLANDED", f"commits after PR merged {merged_at[:10]}"

    ahead = git("rev-list", "--count", f"origin/main..{name}")
    return "UNLANDED", f"no merged PR, {ahead} commit(s) ahead"


def inspect_worktree(wt: Path) -> tuple[list[str], list[str]]:
    """Return (tracked modifications, untracked paths) for a worktree."""
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=wt,
        capture_output=True,
        text=True,
        check=False,
    )
    tracked, untracked = [], []
    for line in proc.stdout.splitlines():
        status, _, path = line[:2], line[2:3], line[3:]
        (untracked if status == "??" else tracked).append(path)
    return tracked, untracked


def collect(only: str | None) -> tuple[list[Branch], list[tuple[Path, str]]]:
    wts, detached = scan_worktrees()
    prs = merged_prs()
    current = git("rev-parse", "--abbrev-ref", "HEAD")

    branches = []
    for name in git("for-each-ref", "--format=%(refname:short)", "refs/heads/").splitlines():
        if name in PROTECTED or name == current:
            continue
        if only and only not in name and only not in str(wts.get(name, "")):
            continue
        verdict, reason = classify(name, prs)
        b = Branch(name=name, verdict=verdict, reason=reason, worktree=wts.get(name))
        if b.worktree and b.worktree.exists():
            b.dirty_tracked, b.untracked = inspect_worktree(b.worktree)
        branches.append(b)
    return branches, detached


def report(branches: list[Branch], detached: list[tuple[Path, str]]) -> None:
    for verdict in ("LANDED", "UNLANDED"):
        rows = [b for b in branches if b.verdict == verdict]
        print(f"\n=== {verdict} ({len(rows)}) ===")
        for b in sorted(rows, key=lambda x: x.name):
            marks = []
            if b.worktree:
                marks.append("worktree")
            if b.dirty_tracked:
                marks.append(f"{len(b.dirty_tracked)} TRACKED EDIT(S)")
            if b.untracked:
                marks.append(f"{len(b.untracked)} untracked")
            print(f"  {b.name:<44} {b.reason:<34} {', '.join(marks)}")
            for path in b.dirty_tracked:
                print(f"      ! {path}")

    if detached:
        print(f"\n=== DETACHED worktrees ({len(detached)}) - not pruned, review by hand ===")
        for path, sha in detached:
            in_main = (
                subprocess.run(
                    ["git", "merge-base", "--is-ancestor", sha, "origin/main"],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    check=False,
                ).returncode
                == 0
            )
            state = "in origin/main" if in_main else "NOT in origin/main"
            print(f"  {str(path):<60} {sha}  {state}")


def apply(branches: list[Branch], force: bool) -> int:
    failures = 0
    for b in sorted(branches, key=lambda x: x.name):
        if not b.landed:
            continue
        if b.dirty_tracked and not force:
            print(f"SKIP     {b.name}: {len(b.dirty_tracked)} tracked edit(s); use --force")
            continue
        if b.worktree:
            out = subprocess.run(
                ["git", "worktree", "remove", "--force", str(b.worktree)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            if out.returncode != 0:
                print(f"FAILED   worktree {b.worktree}: {out.stderr.strip()}")
                failures += 1
                continue
            print(f"REMOVED  worktree {b.worktree}")
        sha = git("rev-parse", "--short", b.name)
        out = subprocess.run(
            ["git", "branch", "-D", b.name],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            print(f"FAILED   branch {b.name}: {out.stderr.strip()}")
            failures += 1
        else:
            print(f"DELETED  branch {b.name} ({sha}) - recover with: git branch {b.name} {sha}")
    return failures


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true", help="actually remove worktrees and delete branches")
    p.add_argument("--force", action="store_true", help="also prune landed worktrees that have tracked edits")
    p.add_argument("--only", metavar="SUBSTR", help="limit to branches or worktree paths containing SUBSTR")
    args = p.parse_args()

    git("fetch", "origin", "--quiet")
    branches, detached = collect(args.only)
    report(branches, detached)

    landed = [b for b in branches if b.landed]
    if not args.apply:
        print(f"\nDry run. {len(landed)} branch(es) would be pruned; re-run with --apply.")
        return 0

    print(f"\nPruning {len(landed)} landed branch(es)...")
    failures = apply(branches, args.force)
    git("worktree", "prune")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
