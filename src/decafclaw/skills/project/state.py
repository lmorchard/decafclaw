"""Project state machine and persistence.

Manages project lifecycle states, valid transitions, and JSON serialization
for project metadata stored in project.json files.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)


class ProjectState(str, Enum):
    BRAINSTORMING = "brainstorming"
    SPEC_REVIEW = "spec_review"
    PLANNING = "planning"
    PLAN_REVIEW = "plan_review"
    EXECUTING = "executing"
    DONE = "done"


# Valid transitions: state → set of reachable states
TRANSITIONS: dict[ProjectState, set[ProjectState]] = {
    ProjectState.BRAINSTORMING: {ProjectState.SPEC_REVIEW},
    ProjectState.SPEC_REVIEW: {ProjectState.PLANNING, ProjectState.BRAINSTORMING},
    ProjectState.PLANNING: {ProjectState.PLAN_REVIEW},
    ProjectState.PLAN_REVIEW: {ProjectState.EXECUTING, ProjectState.PLANNING},
    ProjectState.EXECUTING: {
        ProjectState.DONE,
        ProjectState.PLANNING,
        ProjectState.BRAINSTORMING,
    },
    ProjectState.DONE: set(),
}


def validate_transition(current: ProjectState, target: ProjectState) -> bool:
    """Check whether a state transition is valid."""
    return target in TRANSITIONS.get(current, set())


@dataclass
class ProjectInfo:
    slug: str
    description: str
    status: ProjectState
    mode: str  # "normal" or "express"
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601
    directory: Path = field(repr=False)

    @property
    def spec_path(self) -> Path:
        return self.directory / "spec.md"

    @property
    def plan_path(self) -> Path:
        return self.directory / "plan.md"

    @property
    def notes_path(self) -> Path:
        return self.directory / "notes.md"

    @property
    def json_path(self) -> Path:
        return self.directory / "project.json"


def _projects_root(config) -> Path:
    """Root directory for all projects in the workspace."""
    return config.workspace_path / "projects"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slugify(text: str, max_len: int = 30) -> str:
    """Convert a description into a filesystem-safe slug.

    Truncates at word boundary to keep slugs readable.
    """
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    if len(slug) > max_len:
        # Truncate at last word boundary before max_len
        truncated = slug[:max_len]
        last_dash = truncated.rfind("-")
        if last_dash > max_len // 2:
            truncated = truncated[:last_dash]
        slug = truncated.rstrip("-")
    return slug


def create_project(
    config, description: str, slug: str = ""
) -> ProjectInfo:
    """Create a new project directory and initialize metadata."""
    if not slug:
        slug = _slugify(description)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    dir_name = f"{timestamp}-{slug}"
    directory = _projects_root(config) / dir_name
    if directory.exists():
        # Add microseconds suffix to avoid collision
        dir_name = f"{timestamp}-{datetime.now().microsecond:06d}-{slug}"
        directory = _projects_root(config) / dir_name
    directory.mkdir(parents=True)

    now = _now_iso()
    info = ProjectInfo(
        slug=slug,
        description=description,
        status=ProjectState.BRAINSTORMING,
        mode="normal",
        created_at=now,
        updated_at=now,
        directory=directory,
    )

    # Create artifact files
    for path in [info.spec_path, info.plan_path, info.notes_path]:
        if not path.exists():
            path.write_text("")

    save_project(info)
    log.info("[project] created %s at %s", slug, directory)
    return info


def save_project(info: ProjectInfo) -> None:
    """Persist project metadata to project.json."""
    info.updated_at = _now_iso()
    data = {
        "slug": info.slug,
        "description": info.description,
        "status": info.status.value,
        "mode": info.mode,
        "created_at": info.created_at,
        "updated_at": info.updated_at,
    }
    info.json_path.write_text(json.dumps(data, indent=2) + "\n")


def load_project(config, slug_or_dir: str) -> ProjectInfo | None:
    """Find and load a project by slug or directory name.

    Searches workspace/projects/ for a directory ending with the given slug
    or matching the full directory name. Returns the most recent match.
    """
    root = _projects_root(config)
    if not root.is_dir():
        return None

    # Reject path traversal attempts
    if "/" in slug_or_dir or "\\" in slug_or_dir or ".." in slug_or_dir:
        return None

    # First try exact directory name match
    direct = root / slug_or_dir
    if direct.is_dir() and direct.resolve().is_relative_to(root.resolve()):
        return _load_from_dir(direct)

    # Search by slug in project.json
    candidates: list[tuple[Path, ProjectInfo]] = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        info = _load_from_dir(d)
        if info and info.slug == slug_or_dir:
            candidates.append((d, info))

    if not candidates:
        return None

    # Most recent first (timestamp prefix sorts lexically)
    candidates.sort(key=lambda x: x[0].name, reverse=True)
    return candidates[0][1]


def _load_from_dir(directory: Path) -> ProjectInfo | None:
    """Load project metadata from a directory."""
    json_path = directory / "project.json"
    if not json_path.exists():
        return None
    try:
        data = json.loads(json_path.read_text())
        return ProjectInfo(
            slug=data["slug"],
            description=data["description"],
            status=ProjectState(data["status"]),
            mode=data.get("mode", "normal"),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            directory=directory,
        )
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        log.warning("[project] failed to load %s: %s", directory, exc)
        return None


def list_projects(config) -> list[ProjectInfo]:
    """List all projects, most recent first."""
    root = _projects_root(config)
    if not root.is_dir():
        return []

    projects = []
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        info = _load_from_dir(d)
        if info:
            projects.append(info)
    return projects
