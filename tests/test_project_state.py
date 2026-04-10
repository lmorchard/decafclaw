"""Tests for project state machine and persistence."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from decafclaw.skills.project.state import (
    TRANSITIONS,
    ProjectState,
    create_project,
    list_projects,
    load_project,
    save_project,
    validate_transition,
)


@pytest.fixture
def config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return SimpleNamespace(workspace_path=workspace)


class TestTransitions:
    def test_forward_transitions(self):
        assert validate_transition(ProjectState.BRAINSTORMING, ProjectState.SPEC_REVIEW)
        assert validate_transition(ProjectState.SPEC_REVIEW, ProjectState.PLANNING)
        assert validate_transition(ProjectState.PLANNING, ProjectState.PLAN_REVIEW)
        assert validate_transition(ProjectState.PLAN_REVIEW, ProjectState.EXECUTING)
        assert validate_transition(ProjectState.EXECUTING, ProjectState.DONE)

    def test_backward_transitions(self):
        assert validate_transition(ProjectState.SPEC_REVIEW, ProjectState.BRAINSTORMING)
        assert validate_transition(ProjectState.PLAN_REVIEW, ProjectState.PLANNING)
        assert validate_transition(ProjectState.EXECUTING, ProjectState.PLANNING)
        assert validate_transition(ProjectState.EXECUTING, ProjectState.BRAINSTORMING)

    def test_invalid_transitions(self):
        assert not validate_transition(ProjectState.BRAINSTORMING, ProjectState.DONE)
        assert not validate_transition(ProjectState.DONE, ProjectState.BRAINSTORMING)
        assert not validate_transition(ProjectState.PLANNING, ProjectState.SPEC_REVIEW)

    def test_done_has_no_transitions(self):
        assert TRANSITIONS[ProjectState.DONE] == set()


class TestProjectCRUD:
    def test_create_project(self, config):
        info = create_project(config, "Build a widget", slug="widget")
        assert info.slug == "widget"
        assert info.status == ProjectState.BRAINSTORMING
        assert info.mode == "normal"
        assert info.directory.exists()
        assert info.spec_path.exists()
        assert info.plan_path.exists()
        assert info.notes_path.exists()
        assert info.json_path.exists()

    def test_create_project_auto_slug(self, config):
        info = create_project(config, "Refactor the Auth System!")
        assert info.slug == "refactor-the-auth-system"

    def test_create_default_mode(self, config):
        info = create_project(config, "Quick task")
        assert info.mode == "normal"

    def test_save_and_load(self, config):
        info = create_project(config, "Test project", slug="test-proj")
        info.status = ProjectState.PLANNING
        save_project(info)

        loaded = load_project(config, "test-proj")
        assert loaded is not None
        assert loaded.status == ProjectState.PLANNING
        assert loaded.slug == "test-proj"

    def test_load_by_dir_name(self, config):
        info = create_project(config, "Test project", slug="test-proj")
        loaded = load_project(config, info.directory.name)
        assert loaded is not None
        assert loaded.slug == "test-proj"

    def test_load_nonexistent(self, config):
        assert load_project(config, "nonexistent") is None

    def test_list_projects(self, config):
        create_project(config, "First", slug="first")
        create_project(config, "Second", slug="second")
        projects = list_projects(config)
        assert len(projects) == 2
        # Most recent first
        assert projects[0].slug == "second"

    def test_list_empty(self, config):
        assert list_projects(config) == []
