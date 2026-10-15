"""Guards for the npm dependency install policy (#930).

See docs/dependency-install-policy.md. These tests protect two invariants:

- Every `npm install` / `npm ci` invocation in the Makefile carries
  `--ignore-scripts`, so the normal check/CI install path cannot run a
  dependency lifecycle script. The lifecycle-script fixture is a throwaway
  package built in a temp dir, deliberately outside the live installs: it
  proves npm's `--ignore-scripts` behavior for the exact command forms the
  Makefile uses (`npm install --ignore-scripts` and the CI path
  `npm ci --ignore-scripts`).
- The policy check (scripts/check_npm_policy.py, composed into `make check`)
  rejects `^`/`~` ranges on direct dependencies and stale allowlist entries.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = REPO_ROOT / "Makefile"
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_npm_policy.py"


def run_check(*pkg_jsons: Path) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(CHECK_SCRIPT)] + [str(path) for path in pkg_jsons]
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


def test_policy_check_passes_on_committed_projects():
    result = run_check()  # no args -> the two project package.json files
    assert result.returncode == 0, result.stdout + result.stderr


def test_policy_check_rejects_caret_range(tmp_path: Path):
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"name": "probe", "dependencies": {"bad": "^1.2.3"}}), encoding="utf-8")
    result = run_check(pkg)
    assert result.returncode == 1
    assert "bad" in result.stderr and "^1.2.3" in result.stderr


def test_policy_check_rejects_tilde_range(tmp_path: Path):
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"name": "probe", "devDependencies": {"bad": "~1.2.3"}}), encoding="utf-8")
    result = run_check(pkg)
    assert result.returncode == 1
    assert "bad" in result.stderr and "~1.2.3" in result.stderr


def test_policy_check_accepts_exact_pins(tmp_path: Path):
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"name": "probe", "dependencies": {"good": "1.2.3"}}), encoding="utf-8")
    result = run_check(pkg)
    assert result.returncode == 0, result.stdout + result.stderr


def test_policy_check_rejects_stale_allowlist_entry(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "probe", "dependencies": {}}), encoding="utf-8")
    (tmp_path / "package-lock.json").write_text(
        json.dumps({"name": "probe", "lockfileVersion": 3, "packages": {"": {}}}), encoding="utf-8"
    )
    (tmp_path / ".npm-script-allowlist").write_text("esbuild\n", encoding="utf-8")
    result = run_check(tmp_path / "package.json")
    assert result.returncode == 1
    assert "esbuild" in result.stderr


def test_policy_check_accepts_allowlisted_script_package(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "probe", "dependencies": {"esbuild": "0.25.12"}}), encoding="utf-8"
    )
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "probe",
                "lockfileVersion": 3,
                "packages": {
                    "": {"dependencies": {"esbuild": "0.25.12"}},
                    "node_modules/esbuild": {"version": "0.25.12", "hasInstallScript": True},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".npm-script-allowlist").write_text("esbuild\n", encoding="utf-8")
    result = run_check(tmp_path / "package.json")
    assert result.returncode == 0, result.stdout + result.stderr


def test_makefile_npm_installs_disable_lifecycle_scripts():
    """Every npm install/ci invocation in the Makefile must carry
    --ignore-scripts, so the check/CI install path cannot run a dependency
    lifecycle script unless the package is allowlisted (#930)."""
    violations = []
    invocation_count = 0
    for lineno, line in enumerate(MAKEFILE.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("echo"):
            continue
        matches = re.findall(r"\bnpm (?:install|ci)\b", line)
        invocation_count += len(matches)
        if matches and "--ignore-scripts" not in line:
            violations.append(f"Makefile:{lineno}: {stripped}")
    # install, install-js, install-tui, and vendor each invoke npm.
    assert invocation_count >= 4, f"expected >= 4 npm install/ci invocations, found {invocation_count}"
    assert not violations, "\n".join(violations)


def test_dependency_lifecycle_scripts_do_not_run_under_policy_installs(tmp_path: Path):
    """Throwaway fixture outside the live installs: a dependency whose
    preinstall script writes a marker. With scripts enabled (control) the
    marker appears; under the Makefile's --ignore-scripts forms it must not
    (#930)."""
    if shutil.which("npm") is None:
        pytest.skip("npm not on PATH (Node toolchain required)")

    def make_consumer(name: str) -> Path:
        consumer = tmp_path / name
        consumer.mkdir()
        (consumer / "package.json").write_text(
            json.dumps(
                {
                    "name": f"decafclaw-probe-{name}",
                    "version": "0.0.0",
                    "dependencies": {"decafclaw-script-fixture": "file:./fixture"},
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        fixture = consumer / "fixture"
        fixture.mkdir()
        (fixture / "package.json").write_text(
            json.dumps(
                {
                    "name": "decafclaw-script-fixture",
                    "version": "0.0.0",
                    "scripts": {
                        "preinstall": (
                            "node -e \"require('fs').writeFileSync("
                            "require('path').join(process.cwd(),'MARKER'),'executed')\""
                        )
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return consumer

    def npm_in(consumer: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["npm", *args, "--no-audit", "--no-fund"],
            cwd=consumer,
            capture_output=True,
            text=True,
            timeout=300,
        )

    def marker(consumer: Path) -> Path:
        return consumer / "node_modules" / "decafclaw-script-fixture" / "MARKER"

    control = make_consumer("control")
    result = npm_in(control, "install")
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker(control).exists(), "control install did not run the fixture's preinstall — probe is vacuous"

    policy = make_consumer("policy")
    result = npm_in(policy, "install", "--ignore-scripts")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker(policy).exists(), "npm install --ignore-scripts ran the fixture's preinstall"

    ci = tmp_path / "ci"
    ci.mkdir()
    shutil.copy2(policy / "package.json", ci / "package.json")
    shutil.copy2(policy / "package-lock.json", ci / "package-lock.json")
    shutil.copytree(policy / "fixture", ci / "fixture")
    result = npm_in(ci, "ci", "--ignore-scripts")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker(ci).exists(), "npm ci --ignore-scripts (the CI install path) ran the fixture's preinstall"
