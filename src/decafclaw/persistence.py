"""Per-conversation state persistence — skills, skill data, and other sidecars."""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _skills_path(config, conv_id: str) -> Path:
    return config.workspace_path / "conversations" / f"{conv_id}.skills.json"


def write_skills_state(config, conv_id: str, skills: set[str]) -> None:
    """Persist activated skill names for a conversation to a sidecar file."""
    path = _skills_path(config, conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(skills)) + "\n")


def read_skills_state(config, conv_id: str) -> set[str]:
    """Read persisted activated skill names, or empty set if none."""
    path = _skills_path(config, conv_id)
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text()))
    except (json.JSONDecodeError, OSError):
        return set()


def _skill_data_path(config, conv_id: str) -> Path:
    return config.workspace_path / "conversations" / f"{conv_id}.skill_data.json"


def write_skill_data(config, conv_id: str, data: dict) -> None:
    """Persist skill_data dict for a conversation to a sidecar file."""
    path = _skill_data_path(config, conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) + "\n")


def read_skill_data(config, conv_id: str) -> dict:
    """Read persisted skill_data, or empty dict if none."""
    path = _skill_data_path(config, conv_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


