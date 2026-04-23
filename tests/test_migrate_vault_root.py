import json
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "migrate_vault_root.py"


def _setup(tmp_path):
    old = tmp_path / "old_vault"
    new = tmp_path / "new_vault"
    (old / "agent" / "pages").mkdir(parents=True)
    (old / "agent" / "pages" / "note.md").write_text("content\n")
    new.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"vault_path": str(old)}))
    return old, new, config


def test_dry_run_reports_without_moving(tmp_path):
    old, new, config = _setup(tmp_path)
    r = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--from", str(old),
            "--to", str(new),
            "--config", str(config),
        ],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    # Old content still there
    assert (old / "agent" / "pages" / "note.md").exists()
    # New dir still empty of agent/
    assert not (new / "agent").exists()


def test_apply_moves_and_updates_config(tmp_path):
    old, new, config = _setup(tmp_path)
    r = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--from", str(old),
            "--to", str(new),
            "--config", str(config),
            "--apply",
        ],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert (new / "agent" / "pages" / "note.md").exists()
    assert not (old / "agent").exists()
    updated = json.loads(config.read_text())
    assert updated["vault_path"] == str(new)


def test_apply_refuses_if_target_agent_exists(tmp_path):
    old, new, config = _setup(tmp_path)
    (new / "agent").mkdir()
    r = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--from", str(old),
            "--to", str(new),
            "--config", str(config),
            "--apply",
        ],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "already exists" in (r.stderr + r.stdout).lower()


def test_refuses_if_source_agent_missing(tmp_path):
    old, new, config = _setup(tmp_path)
    # Remove the agent dir from old
    shutil.rmtree(old / "agent")
    r = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--from", str(old),
            "--to", str(new),
            "--config", str(config),
        ],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "agent" in (r.stderr + r.stdout).lower()


def test_refuses_if_target_root_missing(tmp_path):
    old, new, config = _setup(tmp_path)
    # Remove new vault root entirely
    new.rmdir()
    r = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--from", str(old),
            "--to", str(new),
            "--config", str(config),
        ],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "not exist" in (r.stderr + r.stdout).lower()


def test_refuses_if_config_missing(tmp_path):
    old, new, config = _setup(tmp_path)
    config.unlink()
    r = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--from", str(old),
            "--to", str(new),
            "--config", str(config),
        ],
        capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "config" in (r.stderr + r.stdout).lower()


def test_dry_run_prints_what_would_happen(tmp_path):
    old, new, config = _setup(tmp_path)
    r = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--from", str(old),
            "--to", str(new),
            "--config", str(config),
        ],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    output = r.stdout + r.stderr
    assert "dry run" in output.lower()
    assert str(new) in output
