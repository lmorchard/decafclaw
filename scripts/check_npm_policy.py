#!/usr/bin/env python3
"""Check the npm dependency policy for the web UI and TUI projects (#930).

Rules:
- Direct dependencies must be exact-pinned: no `^` or `~` range specifier in
  dependencies, devDependencies, optionalDependencies, or peerDependencies.
  Ranges are how unreviewed versions enter the lockfile; the lockfile is the
  reviewed ground truth.
- Each entry in the project's `.npm-script-allowlist` (packages permitted to
  run lifecycle scripts at install time) must name a package that actually
  carries an install script (`hasInstallScript`) in the adjacent
  package-lock.json. Stale entries fail, so the allowlist cannot rot silently.

See docs/dependency-install-policy.md for the full policy and how to add an
allowlist entry.

Run via: make check-npm-policy (part of `make check`, so CI gates it).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The two npm projects in the repo. The check reads both, so tui/ is covered
# even though `make check` does not otherwise gate it.
DEFAULT_PROJECTS = (
    REPO_ROOT / "src" / "decafclaw" / "web" / "static" / "package.json",
    REPO_ROOT / "tui" / "package.json",
)

DEP_FIELDS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")

ALLOWLIST_NAME = ".npm-script-allowlist"


def check_pins(pkg_path: Path) -> list[str]:
    """Reject ^/~ range specifiers on every direct dependency."""
    violations: list[str] = []
    data = json.loads(pkg_path.read_text(encoding="utf-8"))
    for field in DEP_FIELDS:
        for name, spec in (data.get(field) or {}).items():
            if isinstance(spec, str) and spec.startswith(("^", "~")):
                violations.append(
                    f"{pkg_path}: {field}.{name} = {spec} — ranges are forbidden; "
                    f"exact-pin the version (see docs/dependency-install-policy.md, #930)"
                )
    return violations


def check_allowlist(pkg_path: Path) -> list[str]:
    """Every allowlist entry must be an install-script package in the lockfile."""
    allowlist = pkg_path.parent / ALLOWLIST_NAME
    if not allowlist.exists():
        return []
    lock_path = pkg_path.parent / "package-lock.json"
    if not lock_path.exists():
        return [f"{allowlist}: {lock_path.name} not found, cannot validate allowlist entries"]
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    # Lockfile keys are paths like "node_modules/esbuild" or the nested
    # "node_modules/vitest/node_modules/esbuild"; the package name is what
    # follows the last "node_modules/" segment.
    with_install_script = {
        key.split("node_modules/")[-1]
        for key, meta in lock.get("packages", {}).items()
        if key and meta.get("hasInstallScript")
    }
    violations: list[str] = []
    for lineno, line in enumerate(allowlist.read_text(encoding="utf-8").splitlines(), 1):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry not in with_install_script:
            violations.append(
                f"{allowlist}:{lineno}: {entry!r} has no install script in "
                f"{lock_path.name} — stale allowlist entry, remove it"
            )
    return violations


def main(argv: list[str]) -> int:
    pkg_paths = [Path(arg).resolve() for arg in argv] if argv else list(DEFAULT_PROJECTS)
    violations: list[str] = []
    for pkg_path in pkg_paths:
        if not pkg_path.exists():
            violations.append(f"{pkg_path}: not found")
            continue
        violations.extend(check_pins(pkg_path))
        violations.extend(check_allowlist(pkg_path))
    if violations:
        print(f"npm policy check FAILED — {len(violations)} violation(s):", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    print(f"npm policy check: OK — {len(pkg_paths)} project(s), direct deps exact-pinned, allowlists valid")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
