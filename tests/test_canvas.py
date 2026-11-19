"""Tests for canvas.py — per-conversation canvas state sidecar."""

import json
from types import SimpleNamespace

import pytest

from decafclaw import canvas


@pytest.fixture
def config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return SimpleNamespace(workspace_path=workspace)


def test_canvas_sidecar_path_basic(config):
    path = canvas._canvas_sidecar_path(config, "abc123")
    # #1112: a fresh conversation resolves under the session root.
    expected = config.workspace_path / "sessions" / "abc123" / "canvas.json"
    assert path == expected.resolve()


def test_canvas_sidecar_path_traversal_guard(config):
    base = (config.workspace_path / "sessions").resolve()
    bad = canvas._canvas_sidecar_path(config, "../etc/passwd")
    # ../etc/passwd → etcpasswd (slashes + dots stripped) → safe dir
    assert bad.is_relative_to(base)
    assert bad.parent.name == "etcpasswd"
    assert bad.name == "canvas.json"


def test_canvas_sidecar_path_empty(config):
    bad = canvas._canvas_sidecar_path(config, "")
    assert bad.parent.name == "_invalid"
    assert bad.name == "canvas.json"


def test_read_canvas_state_missing_file(config):
    state = canvas.read_canvas_state(config, "nope")
    assert state == canvas.empty_canvas_state()


def test_read_canvas_state_corrupt_file(config):
    path = canvas._canvas_sidecar_path(config, "corrupt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json")
    state = canvas.read_canvas_state(config, "corrupt")
    assert state == canvas.empty_canvas_state()


def test_write_then_read_round_trip(config):
    state = {
        "schema_version": 1,
        "active_tab": "canvas_1",
        "next_tab_id": 2,
        "tabs": [
            {
                "id": "canvas_1",
                "label": "Hello",
                "widget_type": "markdown_document",
                "data": {"content": "# Hi"},
            }
        ],
    }
    canvas.write_canvas_state(config, "conv1", state)
    assert canvas.read_canvas_state(config, "conv1") == state


def test_write_is_atomic(config):
    """Write goes through tmp file + rename — no leftover .tmp file."""
    state = {"schema_version": 1, "active_tab": None, "tabs": []}
    canvas.write_canvas_state(config, "conv2", state)
    path = canvas._canvas_sidecar_path(config, "conv2")
    assert not path.with_suffix(".json.tmp").exists()


class _FakeRegistry:
    """Stand-in for WidgetRegistry used by canvas validation in tests."""

    def __init__(self, descriptors):
        self._descriptors = descriptors

    def get(self, name):
        return self._descriptors.get(name)

    def validate(self, name, data):
        desc = self._descriptors.get(name)
        if desc is None:
            return False, f"unknown widget '{name}'"
        for r in desc.required:
            if r not in data:
                return False, f"missing required field '{r}'"
        return True, None

    def normalize(self, name, data):
        # No registered normalizers in tests — pass through.
        return data


@pytest.fixture
def md_doc_registry(monkeypatch):
    reg = _FakeRegistry(
        {
            "markdown_document": SimpleNamespace(modes=["inline", "canvas"], required=["content"]),
            "data_table": SimpleNamespace(modes=["inline"], required=[]),
            # Canvas-mode widget that is NOT artifact-backed (#1253).
            "chart": SimpleNamespace(modes=["canvas"], required=[]),
        }
    )
    monkeypatch.setattr(canvas, "get_widget_registry", lambda: reg)
    return reg


@pytest.fixture
def emit_recorder():
    events = []

    async def emit(conv_id, event):
        events.append((conv_id, event))

    emit.events = events
    return emit


async def test_clear_canvas_when_empty(config, md_doc_registry, emit_recorder):
    result = await canvas.clear_canvas(config, "c", emit=emit_recorder)
    assert result.ok
    assert result.text == "canvas already empty"
    assert emit_recorder.events == []


async def test_clear_canvas_with_tab(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document", {"content": "v1"}, emit=emit_recorder)
    emit_recorder.events.clear()
    result = await canvas.clear_canvas(config, "c", emit=emit_recorder)
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    assert state["tabs"] == []
    assert state["active_tab"] is None
    _, event = emit_recorder.events[0]
    assert event["kind"] == "clear"
    assert event["tab"] is None


async def test_clear_canvas_preserves_next_tab_id(config, md_doc_registry, emit_recorder):
    """Clear must not reset next_tab_id — closed ids never get rebound."""
    await canvas.new_tab(config, "c", "markdown_document", {"content": "a"}, emit=emit_recorder)
    await canvas.new_tab(config, "c", "markdown_document", {"content": "b"}, emit=emit_recorder)
    state = canvas.read_canvas_state(config, "c")
    assert state["next_tab_id"] == 3

    await canvas.clear_canvas(config, "c", emit=emit_recorder)
    state = canvas.read_canvas_state(config, "c")
    assert state["next_tab_id"] == 3, "clear must preserve next_tab_id so a new tab gets a fresh id"

    result = await canvas.new_tab(config, "c", "markdown_document", {"content": "c"}, emit=emit_recorder)
    assert result.tab_id == "canvas_3"


def test_read_phase3_sidecar_synthesizes_next_tab_id(config):
    """A Phase 3 sidecar (no next_tab_id field) gets one synthesized on read."""
    path = canvas._canvas_sidecar_path(config, "phase3conv")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_tab": "canvas_2",
                "tabs": [
                    {"id": "canvas_1", "label": "L1", "widget_type": "markdown_document", "data": {"content": "a"}},
                    {"id": "canvas_2", "label": "L2", "widget_type": "markdown_document", "data": {"content": "b"}},
                ],
            }
        )
    )
    state = canvas.read_canvas_state(config, "phase3conv")
    assert state["next_tab_id"] == 3


def test_read_empty_state_has_next_tab_id_one(config):
    state = canvas.read_canvas_state(config, "fresh")
    assert state["next_tab_id"] == 1


def test_write_then_read_preserves_next_tab_id(config):
    state = {
        "schema_version": 1,
        "active_tab": "canvas_5",
        "next_tab_id": 7,
        "tabs": [
            {"id": "canvas_5", "label": "L", "widget_type": "markdown_document", "data": {"content": "x"}},
        ],
    }
    canvas.write_canvas_state(config, "c", state)
    got = canvas.read_canvas_state(config, "c")
    assert got["next_tab_id"] == 7


def test_read_phase3_sidecar_with_no_tabs(config):
    """A Phase 3 sidecar with empty tabs gets next_tab_id=1."""
    path = canvas._canvas_sidecar_path(config, "emptyphase3")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "active_tab": None, "tabs": []}))
    state = canvas.read_canvas_state(config, "emptyphase3")
    assert state["next_tab_id"] == 1


# ---------------------------------------------------------------------------
# Phase 4 tab-aware state ops
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_tab_creates_and_activates(config, md_doc_registry, emit_recorder):
    result = await canvas.new_tab(
        config,
        "c",
        "markdown_document",
        {"content": "# Doc"},
        label="Doc",
        emit=emit_recorder,
    )
    assert result.ok
    assert result.tab_id == "canvas_1"
    state = canvas.read_canvas_state(config, "c")
    assert state["active_tab"] == "canvas_1"
    assert state["next_tab_id"] == 2
    assert len(state["tabs"]) == 1
    assert state["tabs"][0]["id"] == "canvas_1"
    assert state["tabs"][0]["label"] == "Doc"
    # Event
    assert len(emit_recorder.events) == 1
    _, event = emit_recorder.events[0]
    assert event["kind"] == "new_tab"
    assert event["active_tab"] == "canvas_1"
    assert event["tab"]["id"] == "canvas_1"


@pytest.mark.asyncio
async def test_new_tab_increments_counter_across_close(config, md_doc_registry, emit_recorder):
    """Closing a tab does NOT decrement next_tab_id; ids never reused."""
    r1 = await canvas.new_tab(config, "c", "markdown_document", {"content": "a"}, emit=emit_recorder)
    r2 = await canvas.new_tab(config, "c", "markdown_document", {"content": "b"}, emit=emit_recorder)
    assert r1.tab_id == "canvas_1"
    assert r2.tab_id == "canvas_2"
    await canvas.close_tab(config, "c", "canvas_2", emit=emit_recorder)
    r3 = await canvas.new_tab(config, "c", "markdown_document", {"content": "c"}, emit=emit_recorder)
    assert r3.tab_id == "canvas_3"  # NOT canvas_2 again


@pytest.mark.asyncio
async def test_new_tab_unknown_widget(config, md_doc_registry, emit_recorder):
    result = await canvas.new_tab(
        config,
        "c",
        "no_such",
        {"content": "x"},
        emit=emit_recorder,
    )
    assert not result.ok
    assert "not registered" in result.error
    assert canvas.read_canvas_state(config, "c") == canvas.empty_canvas_state()


@pytest.mark.asyncio
async def test_new_tab_invalid_data(config, md_doc_registry, emit_recorder):
    result = await canvas.new_tab(
        config,
        "c",
        "markdown_document",
        {"wrong": 1},
        emit=emit_recorder,
    )
    assert not result.ok
    assert "schema validation failed" in result.error


@pytest.mark.asyncio
async def test_update_tab_by_id(config, md_doc_registry, emit_recorder):
    r1 = await canvas.new_tab(config, "c", "markdown_document", {"content": "v1"}, label="L", emit=emit_recorder)
    emit_recorder.events.clear()
    result = await canvas.update_tab(
        config,
        "c",
        r1.tab_id,
        {"content": "v2"},
        emit=emit_recorder,
    )
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    assert state["tabs"][0]["data"]["content"] == "v2"
    assert state["tabs"][0]["label"] == "L"  # preserved
    assert state["tabs"][0]["widget_type"] == "markdown_document"  # preserved
    _, event = emit_recorder.events[0]
    assert event["kind"] == "update"
    assert event["tab"]["id"] == "canvas_1"


@pytest.mark.asyncio
async def test_update_tab_unknown_id(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document", {"content": "x"}, emit=emit_recorder)
    result = await canvas.update_tab(
        config,
        "c",
        "canvas_99",
        {"content": "y"},
        emit=emit_recorder,
    )
    assert not result.ok
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_update_tab_invalid_data(config, md_doc_registry, emit_recorder):
    r1 = await canvas.new_tab(config, "c", "markdown_document", {"content": "x"}, emit=emit_recorder)
    result = await canvas.update_tab(
        config,
        "c",
        r1.tab_id,
        {"wrong": 1},
        emit=emit_recorder,
    )
    assert not result.ok
    assert "schema validation failed" in result.error


# ---------------------------------------------------------------------------
# Per-widget normalization (e.g. iframe_sandbox CSP wrapping)
# ---------------------------------------------------------------------------


class _NormalizingFakeRegistry(_FakeRegistry):
    """Fake registry that runs a normalize hook by widget name."""

    def __init__(self, descriptors, normalizers):
        super().__init__(descriptors)
        self._normalizers = normalizers

    def normalize(self, name, data):
        fn = self._normalizers.get(name)
        return fn(data) if fn else data


@pytest.mark.asyncio
async def test_new_tab_runs_normalize(config, monkeypatch, emit_recorder):
    """new_tab should invoke registry.normalize after validate, so widgets
    like iframe_sandbox get their server-controlled fields injected before
    state is persisted or events are emitted."""

    def normalize_iframe(data):
        return {**data, "html": f"WRAPPED:{data.get('body', '')}"}

    reg = _NormalizingFakeRegistry(
        descriptors={
            "iframe_sandbox": SimpleNamespace(modes=["inline", "canvas"], required=["body"]),
        },
        normalizers={"iframe_sandbox": normalize_iframe},
    )
    monkeypatch.setattr(canvas, "get_widget_registry", lambda: reg)

    result = await canvas.new_tab(
        config,
        "c",
        "iframe_sandbox",
        {"body": "<p>hi</p>"},
        emit=emit_recorder,
    )
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    stored = state["tabs"][0]["data"]
    # Normalized form: original body preserved, html field added.
    assert stored["body"] == "<p>hi</p>"
    assert stored["html"] == "WRAPPED:<p>hi</p>"
    # Emitted event also carries the normalized data.
    _, event = emit_recorder.events[0]
    assert event["tab"]["data"]["html"] == "WRAPPED:<p>hi</p>"


@pytest.mark.asyncio
async def test_update_tab_runs_normalize(config, monkeypatch, emit_recorder):
    """update_tab must re-run normalize so a stale html field can't survive
    a round-trip."""

    def normalize_iframe(data):
        return {**data, "html": f"WRAPPED:{data.get('body', '')}"}

    reg = _NormalizingFakeRegistry(
        descriptors={
            "iframe_sandbox": SimpleNamespace(modes=["inline", "canvas"], required=["body"]),
        },
        normalizers={"iframe_sandbox": normalize_iframe},
    )
    monkeypatch.setattr(canvas, "get_widget_registry", lambda: reg)

    r = await canvas.new_tab(config, "c", "iframe_sandbox", {"body": "v1"}, emit=emit_recorder)
    emit_recorder.events.clear()
    result = await canvas.update_tab(
        config,
        "c",
        r.tab_id,
        # Agent passes a stale html alongside fresh body — normalize should overwrite it.
        {"body": "v2", "html": "STALE"},
        emit=emit_recorder,
    )
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    stored = state["tabs"][0]["data"]
    assert stored["body"] == "v2"
    assert stored["html"] == "WRAPPED:v2"


@pytest.mark.asyncio
async def test_close_tab_active_switches_to_left_neighbor(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document", {"content": "1"}, emit=emit_recorder)
    await canvas.new_tab(config, "c", "markdown_document", {"content": "2"}, emit=emit_recorder)
    await canvas.new_tab(config, "c", "markdown_document", {"content": "3"}, emit=emit_recorder)
    # active is canvas_3 (most recent new_tab)
    emit_recorder.events.clear()
    result = await canvas.close_tab(config, "c", "canvas_3", emit=emit_recorder)
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    assert state["active_tab"] == "canvas_2"  # left neighbor
    assert len(state["tabs"]) == 2
    _, event = emit_recorder.events[0]
    assert event["kind"] == "close_tab"
    assert event["closed_tab_id"] == "canvas_3"
    assert event["active_tab"] == "canvas_2"


@pytest.mark.asyncio
async def test_close_tab_first_switches_to_right(config, md_doc_registry, emit_recorder):
    """Closing the leftmost active tab → right neighbor (no left exists)."""
    r1 = await canvas.new_tab(config, "c", "markdown_document", {"content": "1"}, emit=emit_recorder)
    await canvas.new_tab(config, "c", "markdown_document", {"content": "2"}, emit=emit_recorder)
    # Manually activate canvas_1
    await canvas.set_active_tab(config, "c", r1.tab_id, emit=emit_recorder)
    emit_recorder.events.clear()
    await canvas.close_tab(config, "c", r1.tab_id, emit=emit_recorder)
    state = canvas.read_canvas_state(config, "c")
    assert state["active_tab"] == "canvas_2"


@pytest.mark.asyncio
async def test_close_tab_non_active(config, md_doc_registry, emit_recorder):
    """Closing a non-active tab leaves the active tab unchanged."""
    await canvas.new_tab(config, "c", "markdown_document", {"content": "1"}, emit=emit_recorder)
    await canvas.new_tab(config, "c", "markdown_document", {"content": "2"}, emit=emit_recorder)
    # active is canvas_2
    await canvas.close_tab(config, "c", "canvas_1", emit=emit_recorder)
    state = canvas.read_canvas_state(config, "c")
    assert state["active_tab"] == "canvas_2"
    assert len(state["tabs"]) == 1
    assert state["tabs"][0]["id"] == "canvas_2"


@pytest.mark.asyncio
async def test_close_last_tab_clears_active(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document", {"content": "1"}, emit=emit_recorder)
    emit_recorder.events.clear()
    await canvas.close_tab(config, "c", "canvas_1", emit=emit_recorder)
    state = canvas.read_canvas_state(config, "c")
    assert state["active_tab"] is None
    assert state["tabs"] == []
    _, event = emit_recorder.events[0]
    assert event["kind"] == "close_tab"
    assert event["active_tab"] is None
    assert event["tab"] is None


@pytest.mark.asyncio
async def test_close_tab_unknown_id(config, md_doc_registry, emit_recorder):
    result = await canvas.close_tab(config, "c", "canvas_99", emit=emit_recorder)
    assert not result.ok
    assert "not found" in result.error


class _FakeTerminalRegistry:
    """Stand-in for TerminalRegistry — spies on kill() calls."""

    def __init__(self, sessions):
        self._sessions = sessions
        self.killed = []

    def get(self, conv_id, tab_id):
        return self._sessions.get((conv_id, tab_id))

    async def kill(self, session, grace=1.0):
        self.killed.append(session)


@pytest.mark.asyncio
async def test_close_tab_kills_terminal_pty(config, emit_recorder):
    state = {
        "schema_version": 1,
        "active_tab": "canvas_1",
        "next_tab_id": 3,
        "tabs": [
            {
                "id": "canvas_1",
                "label": "Terminal",
                "widget_type": "terminal",
                "data": {"session_id": "s1", "cwd": "/tmp", "shell": "/bin/sh"},
            },
            {"id": "canvas_2", "label": "Table", "widget_type": "data_table", "data": {}},
        ],
    }
    canvas.write_canvas_state(config, "c", state)
    session = object()
    reg = _FakeTerminalRegistry({("c", "canvas_1"): session})

    result = await canvas.close_tab(config, "c", "canvas_1", emit=emit_recorder, registry=reg)
    assert result.ok
    assert reg.killed == [session]


@pytest.mark.asyncio
async def test_close_tab_non_terminal_does_not_kill(config, emit_recorder):
    state = {
        "schema_version": 1,
        "active_tab": "canvas_2",
        "next_tab_id": 3,
        "tabs": [
            {
                "id": "canvas_1",
                "label": "Terminal",
                "widget_type": "terminal",
                "data": {"session_id": "s1", "cwd": "/tmp", "shell": "/bin/sh"},
            },
            {"id": "canvas_2", "label": "Table", "widget_type": "data_table", "data": {}},
        ],
    }
    canvas.write_canvas_state(config, "c", state)
    reg = _FakeTerminalRegistry({("c", "canvas_1"): object()})

    result = await canvas.close_tab(config, "c", "canvas_2", emit=emit_recorder, registry=reg)
    assert result.ok
    assert reg.killed == []


@pytest.mark.asyncio
async def test_set_active_tab(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document", {"content": "1"}, emit=emit_recorder)
    await canvas.new_tab(config, "c", "markdown_document", {"content": "2"}, emit=emit_recorder)
    emit_recorder.events.clear()
    result = await canvas.set_active_tab(config, "c", "canvas_1", emit=emit_recorder)
    assert result.ok
    state = canvas.read_canvas_state(config, "c")
    assert state["active_tab"] == "canvas_1"
    _, event = emit_recorder.events[0]
    assert event["kind"] == "set_active"
    assert event["active_tab"] == "canvas_1"


@pytest.mark.asyncio
async def test_set_active_tab_unknown_id(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document", {"content": "1"}, emit=emit_recorder)
    result = await canvas.set_active_tab(config, "c", "canvas_99", emit=emit_recorder)
    assert not result.ok
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_get_tab_by_id(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document", {"content": "x"}, label="L", emit=emit_recorder)
    tab = canvas.get_tab(config, "c", "canvas_1")
    assert tab is not None
    assert tab["label"] == "L"
    assert canvas.get_tab(config, "c", "canvas_99") is None


# ---------------------------------------------------------------------------
# C1 — the agent cannot create a widget marked un-createable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_tab_rejects_agent_uncreateable_widget(config, emit_recorder, tmp_path):
    """C1: new_tab SHALL reject agent_createable: false widgets."""
    from decafclaw import widgets as widgets_mod

    # Load the real bundled widget registry — terminal widget must be present
    # The test config is a SimpleNamespace, so we need to add agent_path for load_widget_registry
    config.agent_path = tmp_path / "agent"
    config.agent_path.mkdir()
    registry = widgets_mod.load_widget_registry(config)

    # Verify terminal widget exists and is registered
    terminal_desc = registry.get("terminal")
    assert terminal_desc is not None, "terminal widget missing from bundled registry"

    # Patch the global registry getter to return our loaded registry
    def get_reg():
        return registry

    import unittest.mock

    with unittest.mock.patch.object(canvas, "get_widget_registry", get_reg):
        # Attempt to create a terminal tab
        result = await canvas.new_tab(
            config,
            "c",
            "terminal",
            {"session_id": "s1", "cwd": "/tmp", "shell": "/bin/sh"},
            emit=emit_recorder,
        )

        # Must reject with ok=False
        assert not result.ok, "new_tab should reject agent_createable: false widgets"

        # Error message must mention the rejection reason (agent_createable)
        assert "agent_createable" in result.error or "agent" in result.error.lower(), (
            f"rejection reason unclear: {result.error}"
        )

        # No tab should have been created
        state = canvas.read_canvas_state(config, "c")
        assert state["tabs"] == [], "no tab should exist after rejection"


# ---------------------------------------------------------------------------
# C3 — clearing the canvas kills terminal PTYs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_canvas_kills_terminal_ptys(config, emit_recorder):
    """C3: clear_canvas SHALL accept a registry and kill terminal sessions."""
    # Create a canvas state with a terminal tab
    state = {
        "schema_version": 1,
        "active_tab": "canvas_1",
        "next_tab_id": 2,
        "tabs": [
            {
                "id": "canvas_1",
                "label": "Term",
                "widget_type": "terminal",
                "data": {"session_id": "s1", "cwd": "/tmp", "shell": "/bin/sh"},
            },
        ],
    }
    canvas.write_canvas_state(config, "c", state)

    # Mock a terminal session for that tab
    session = object()
    reg = _FakeTerminalRegistry({("c", "canvas_1"): session})

    # Clear with the registry
    result = await canvas.clear_canvas(config, "c", emit=emit_recorder, registry=reg)

    assert result.ok
    # Registry.kill should have been called for the terminal session
    assert reg.killed == [session], "clear_canvas should kill terminal PTY sessions"


# ---------------------------------------------------------------------------
# #1253 — durable artifact identity for markdown_document tabs
# ---------------------------------------------------------------------------


def _raw_canvas(config, conv_id):
    return json.loads(canvas._canvas_sidecar_path(config, conv_id).read_text())


def _raw_artifacts(config, conv_id):
    return json.loads(canvas._artifact_sidecar_path(config, conv_id).read_text())


@pytest.mark.asyncio
async def test_new_markdown_tab_keys_content_by_artifact(config, md_doc_registry, emit_recorder):
    result = await canvas.new_tab(
        config, "c", "markdown_document", {"content": "# Doc"}, label="Doc", emit=emit_recorder
    )
    assert result.ok

    # The tab is a view handle: canvas.json carries the artifact id, not the content.
    raw_tab = _raw_canvas(config, "c")["tabs"][0]
    assert raw_tab["id"] == "canvas_1"
    assert raw_tab["artifact_id"] == "artifact_1"
    assert "data" not in raw_tab

    # Content lives once, in the sibling artifact sidecar.
    artifacts = _raw_artifacts(config, "c")
    assert artifacts["next_artifact_id"] == 2
    assert artifacts["artifacts"]["artifact_1"] == {
        "widget_type": "markdown_document",
        "label": "Doc",
        "data": {"content": "# Doc"},
    }

    # Reads stay hydrated so clients keep their {data: {content}} contract.
    tab = canvas.read_canvas_state(config, "c")["tabs"][0]
    assert tab["data"] == {"content": "# Doc"}
    assert emit_recorder.events[0][1]["tab"]["data"] == {"content": "# Doc"}


@pytest.mark.asyncio
async def test_non_markdown_tab_keeps_inline_data(config, md_doc_registry, emit_recorder):
    result = await canvas.new_tab(config, "c", "chart", {"series": [1, 2]}, emit=emit_recorder)
    assert result.ok
    raw_tab = _raw_canvas(config, "c")["tabs"][0]
    assert "artifact_id" not in raw_tab
    assert raw_tab["data"] == {"series": [1, 2]}
    # No artifact store is created for a widget that is not artifact-backed.
    assert not canvas._artifact_sidecar_path(config, "c").exists()


@pytest.mark.asyncio
async def test_close_tab_hides_view_and_reopen_gets_fresh_handle(config, md_doc_registry, emit_recorder):
    created = await canvas.new_tab(
        config, "c", "markdown_document", {"content": "# Keep"}, label="Keep", emit=emit_recorder
    )
    artifact_id = _raw_canvas(config, "c")["tabs"][0]["artifact_id"]

    closed = await canvas.close_tab(config, "c", created.tab_id, emit=emit_recorder)
    assert closed.ok
    # Closing hides the view; the artifact content survives.
    assert canvas.read_canvas_state(config, "c")["tabs"] == []
    assert _raw_artifacts(config, "c")["artifacts"][artifact_id]["data"] == {"content": "# Keep"}

    reopened = await canvas.reopen_artifact(config, "c", artifact_id, emit=emit_recorder)
    assert reopened.ok
    assert reopened.tab_id == "canvas_2"  # a fresh view handle, never the closed one
    tab = canvas.read_canvas_state(config, "c")["tabs"][0]
    assert tab["id"] == "canvas_2"
    assert tab["artifact_id"] == artifact_id
    assert tab["label"] == "Keep"
    assert tab["data"] == {"content": "# Keep"}


@pytest.mark.asyncio
async def test_closed_tab_url_target_never_aliases_reopened_content(config, md_doc_registry, emit_recorder):
    """Standalone /canvas/{conv}/{tab_id} must not pick up a later artifact."""
    first = await canvas.new_tab(config, "c", "markdown_document", {"content": "# One"}, emit=emit_recorder)
    await canvas.close_tab(config, "c", first.tab_id)
    artifact_id = _raw_artifacts(config, "c")["artifacts"]
    assert list(artifact_id) == ["artifact_1"]

    reopened = await canvas.reopen_artifact(config, "c", "artifact_1")
    # The old tab-locked URL still selects nothing: get_tab is the resolver.
    assert canvas.get_tab(config, "c", first.tab_id) is None
    assert canvas.get_tab(config, "c", reopened.tab_id)["data"] == {"content": "# One"}
    # Ids are never rebound, so a second reopen keeps moving forward.
    second = await canvas.reopen_artifact(config, "c", "artifact_1")
    assert second.tab_id == "canvas_3"
    assert canvas.get_tab(config, "c", first.tab_id) is None


@pytest.mark.asyncio
async def test_reopen_unknown_artifact_errors(config, md_doc_registry):
    result = await canvas.reopen_artifact(config, "c", "artifact_9")
    assert not result.ok
    assert "artifact_9" in result.error
    assert canvas.read_canvas_state(config, "c")["tabs"] == []


@pytest.mark.asyncio
async def test_update_artifact_tab_is_live_bound(config, md_doc_registry, emit_recorder):
    """canvas_update writes the artifact once; every view of it sees the new content."""
    first = await canvas.new_tab(config, "c", "markdown_document", {"content": "# v1"}, label="Doc", emit=emit_recorder)
    second = await canvas.reopen_artifact(config, "c", "artifact_1", emit=emit_recorder)

    result = await canvas.update_tab(config, "c", second.tab_id, {"content": "# v2"}, emit=emit_recorder)
    assert result.ok

    tabs = {t["id"]: t for t in canvas.read_canvas_state(config, "c")["tabs"]}
    assert tabs[first.tab_id]["data"] == {"content": "# v2"}
    assert tabs[second.tab_id]["data"] == {"content": "# v2"}
    # One content record, not one per tab.
    artifacts = _raw_artifacts(config, "c")["artifacts"]
    assert list(artifacts) == ["artifact_1"]
    assert artifacts["artifact_1"]["data"] == {"content": "# v2"}
    # The update event carries hydrated data so live clients re-render both views.
    assert emit_recorder.events[-1][1]["tab"]["data"] == {"content": "# v2"}


@pytest.mark.asyncio
async def test_artifact_content_is_per_conversation(config, md_doc_registry, emit_recorder):
    """An artifact id from one conversation never resolves in another."""
    await canvas.new_tab(config, "conv-a", "markdown_document", {"content": "# Secret"}, emit=emit_recorder)

    result = await canvas.reopen_artifact(config, "conv-b", "artifact_1")
    assert not result.ok
    assert "not found" in result.error
    assert canvas.read_canvas_state(config, "conv-b")["tabs"] == []
    assert not canvas._artifact_sidecar_path(config, "conv-b").exists()
    # And the other conversation's content is not readable through the store.
    assert canvas.get_artifact(config, "conv-b", "artifact_1") is None


@pytest.mark.asyncio
async def test_clear_canvas_preserves_artifacts(config, md_doc_registry, emit_recorder):
    await canvas.new_tab(config, "c", "markdown_document", {"content": "# Keep"}, emit=emit_recorder)
    assert (await canvas.clear_canvas(config, "c")).ok
    assert canvas.read_canvas_state(config, "c")["tabs"] == []
    assert canvas.get_artifact(config, "c", "artifact_1")["data"] == {"content": "# Keep"}
    reopened = await canvas.reopen_artifact(config, "c", "artifact_1")
    assert reopened.ok


def test_read_canvas_state_with_missing_artifact_is_fail_open(config, md_doc_registry):
    canvas.write_canvas_state(
        config,
        "c",
        {
            "schema_version": 1,
            "active_tab": "canvas_1",
            "next_tab_id": 2,
            "tabs": [
                {"id": "canvas_1", "label": "Doc", "widget_type": "markdown_document", "artifact_id": "artifact_1"}
            ],
        },
    )
    tab = canvas.read_canvas_state(config, "c")["tabs"][0]
    assert tab["artifact_id"] == "artifact_1"
    assert tab["data"] == {}  # no content, and no unrelated content aliased in
    assert canvas.reopen_artifact  # store reader survived the missing file


def test_artifact_ids_never_rebind_after_delete_of_tab(config):
    state = canvas.empty_artifact_state()
    first = canvas._create_artifact(state, "markdown_document", "A", {"content": "a"})
    del state["artifacts"][first]
    second = canvas._create_artifact(state, "markdown_document", "B", {"content": "b"})
    assert (first, second) == ("artifact_1", "artifact_2")


def test_list_artifacts_reports_view_metadata(config, md_doc_registry):
    canvas.write_artifact_state(
        config,
        "c",
        {
            "schema_version": 1,
            "next_artifact_id": 2,
            "artifacts": {
                "artifact_1": {"widget_type": "markdown_document", "label": "Doc", "data": {"content": "# D"}}
            },
        },
    )
    assert canvas.list_artifacts(config, "c") == [
        {"artifact_id": "artifact_1", "widget_type": "markdown_document", "label": "Doc"}
    ]


@pytest.mark.asyncio
async def test_mutations_keep_content_out_of_canvas_json(config, md_doc_registry, emit_recorder):
    """A read-modify-write of hydrated state must not copy content into canvas.json."""
    first = await canvas.new_tab(
        config, "c", "markdown_document", {"content": "# One"}, label="One", emit=emit_recorder
    )
    second = await canvas.new_tab(
        config, "c", "markdown_document", {"content": "# Two"}, label="Two", emit=emit_recorder
    )
    # set_active_tab reads hydrated tabs and writes the whole state back.
    assert (await canvas.set_active_tab(config, "c", first.tab_id)).ok

    raw = _raw_canvas(config, "c")
    assert [t["id"] for t in raw["tabs"]] == [first.tab_id, second.tab_id]
    assert all("data" not in t for t in raw["tabs"]), raw["tabs"]
    # Content still comes back hydrated from the artifact store.
    assert canvas.get_tab(config, "c", first.tab_id)["data"] == {"content": "# One"}
    assert _raw_artifacts(config, "c")["artifacts"]["artifact_2"]["data"] == {"content": "# Two"}


@pytest.mark.asyncio
async def test_canvas_new_tab_web_frame_lifecycle(config, emit_recorder):
    """web_frame creates a canvas tab with allowed origins and rejects untrusted origins."""
    from decafclaw.widgets import init_widgets

    init_widgets(config)

    # 1. Allowed origin (localhost)
    result = await canvas.new_tab(
        config,
        "c",
        "web_frame",
        {"url": "http://localhost:2718", "title": "Marimo"},
        emit=emit_recorder,
    )
    assert result.ok is True
    assert result.tab_id == "canvas_1"
    tab = canvas.get_tab(config, "c", "canvas_1")
    assert tab["label"] == "Marimo"
    assert tab["data"]["url"] == "http://localhost:2718"

    # 2. Derive label from url netloc when title omitted
    result2 = await canvas.new_tab(
        config,
        "c",
        "web_frame",
        {"url": "http://decafclaw:2718"},
        emit=emit_recorder,
    )
    assert result2.ok is True
    tab2 = canvas.get_tab(config, "c", result2.tab_id)
    assert tab2["label"] == "decafclaw:2718"

    # 3. Disallowed origin rejected by normalization
    bad_result = await canvas.new_tab(
        config,
        "c",
        "web_frame",
        {"url": "https://untrusted-external-site.com"},
        emit=emit_recorder,
    )
    assert bad_result.ok is False
    assert "not in allowed web_frame origins" in bad_result.error
