"""Tests for canvas_* agent tools."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.media import ToolResult
from decafclaw.tools import canvas_tools


def _make_ctx(config, manager=None, conv_id="conv1"):
    ctx = MagicMock()
    ctx.config = config
    ctx.conv_id = conv_id
    ctx.manager = manager
    return ctx


@pytest.fixture
def config(tmp_path):
    cfg = SimpleNamespace(workspace_path=tmp_path / "workspace")
    cfg.workspace_path.mkdir()
    return cfg


@pytest.fixture
def md_doc_registry(monkeypatch):
    from decafclaw import canvas as canvas_mod

    class _Reg:
        _d = {
            "markdown_document": SimpleNamespace(
                modes=["inline", "canvas"], required=["content"]
            ),
        }

        def get(self, name):
            return self._d.get(name)

        def validate(self, name, data):
            d = self._d.get(name)
            if not d:
                return False, "unknown"
            for r in getattr(d, "required", []):
                if r not in data:
                    return False, f"missing {r}"
            return True, None

        def normalize(self, name, data):
            return data

    monkeypatch.setattr(canvas_mod, "get_widget_registry", lambda: _Reg())


@pytest.fixture
def manager_mock():
    m = MagicMock()
    m.emit = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_canvas_new_tab_returns_tab_id(config, md_doc_registry, manager_mock):
    ctx = _make_ctx(config, manager_mock)
    result = await canvas_tools.tool_canvas_new_tab(
        ctx, "markdown_document", {"content": "# Hi"},
    )
    assert isinstance(result, ToolResult)
    assert result.data["tab_id"] == "canvas_1"
    assert "/canvas/conv1/canvas_1" in result.text
    manager_mock.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_canvas_new_tab_unknown_widget(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_new_tab(
        ctx, "no_such", {"content": "x"},
    )
    assert result.text.startswith("[error: ")
    assert "not registered" in result.text


@pytest.mark.asyncio
async def test_canvas_update_targets_explicit_id(config, md_doc_registry, manager_mock):
    ctx = _make_ctx(config, manager_mock)
    r1 = await canvas_tools.tool_canvas_new_tab(
        ctx, "markdown_document", {"content": "v1"},
    )
    tab_id = r1.data["tab_id"]
    result = await canvas_tools.tool_canvas_update(ctx, tab_id, {"content": "v2"})
    assert "updated" in result.text.lower()
    assert "[error" not in result.text


@pytest.mark.asyncio
async def test_canvas_update_unknown_id(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_update(ctx, "canvas_99", {"content": "x"})
    assert result.text.startswith("[error: ")
    assert "not found" in result.text


@pytest.mark.asyncio
async def test_canvas_close_tab_returns_new_active(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    await canvas_tools.tool_canvas_new_tab(ctx, "markdown_document", {"content": "1"})
    await canvas_tools.tool_canvas_new_tab(ctx, "markdown_document", {"content": "2"})
    # active is canvas_2
    result = await canvas_tools.tool_canvas_close_tab(ctx, "canvas_2")
    assert "active=canvas_1" in result.text


@pytest.mark.asyncio
async def test_canvas_close_last_tab_hides_panel(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    await canvas_tools.tool_canvas_new_tab(ctx, "markdown_document", {"content": "1"})
    result = await canvas_tools.tool_canvas_close_tab(ctx, "canvas_1")
    assert "no tabs left" in result.text


@pytest.mark.asyncio
async def test_canvas_close_tab_unknown_id(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_close_tab(ctx, "canvas_99")
    assert result.text.startswith("[error: ")


@pytest.mark.asyncio
async def test_canvas_clear_when_empty(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_clear(ctx)
    assert result.text == "canvas already empty"


@pytest.mark.asyncio
async def test_canvas_clear_with_tabs(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    await canvas_tools.tool_canvas_new_tab(ctx, "markdown_document", {"content": "1"})
    await canvas_tools.tool_canvas_new_tab(ctx, "markdown_document", {"content": "2"})
    result = await canvas_tools.tool_canvas_clear(ctx)
    assert result.text == "canvas cleared"


@pytest.mark.asyncio
async def test_canvas_read_empty(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_read(ctx)
    assert result.data == {"active_tab": None, "tabs": []}


@pytest.mark.asyncio
async def test_canvas_read_full_state(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    await canvas_tools.tool_canvas_new_tab(
        ctx, "markdown_document", {"content": "a"}, label="A",
    )
    await canvas_tools.tool_canvas_new_tab(
        ctx, "markdown_document", {"content": "b"}, label="B",
    )
    result = await canvas_tools.tool_canvas_read(ctx)
    assert result.data["active_tab"] == "canvas_2"
    assert len(result.data["tabs"]) == 2
    assert result.data["tabs"][0]["label"] == "A"
    assert result.data["tabs"][1]["label"] == "B"


def test_tools_registered_as_always_loaded():
    from decafclaw.tools import TOOL_DEFINITIONS, TOOLS
    expected = {"canvas_new_tab", "canvas_update", "canvas_close_tab",
                "canvas_clear", "canvas_read"}
    for name in expected:
        assert name in TOOLS, f"{name} missing from TOOLS"
    names = {d["function"]["name"] for d in TOOL_DEFINITIONS
             if d.get("type") == "function"}
    assert expected.issubset(names)
    # Old Phase 3 tool removed
    assert "canvas_set" not in TOOLS


@pytest.mark.asyncio
async def test_canvas_new_tab_url_uses_explicit_form(config, md_doc_registry, manager_mock):
    """Returned URL uses /canvas/{conv}/{tab_id} not bare /canvas/{conv}."""
    ctx = _make_ctx(config, manager_mock)
    result = await canvas_tools.tool_canvas_new_tab(
        ctx, "markdown_document", {"content": "x"},
    )
    assert f"/canvas/conv1/{result.data['tab_id']}" in result.text


# ---------------------------------------------------------------------------
# C2 — closing a terminal tab through the agent tool kills its PTY
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_canvas_close_tab_passes_registry(config, md_doc_registry, monkeypatch):
    """C2: tool_canvas_close_tab SHALL pass registry kwarg to canvas.close_tab."""
    from decafclaw import canvas as canvas_mod

    sentinel = object()
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    ctx.terminal_registry = sentinel

    # Create a tab to close
    await canvas_tools.tool_canvas_new_tab(
        ctx, "markdown_document", {"content": "x"},
    )

    # Spy on canvas.close_tab to capture kwargs
    captured_kwargs = {}

    original_close_tab = canvas_mod.close_tab

    async def spy_close_tab(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return await original_close_tab(*args, **kwargs)

    monkeypatch.setattr(canvas_mod, "close_tab", spy_close_tab)

    # Call the tool
    await canvas_tools.tool_canvas_close_tab(ctx, "canvas_1")

    # Assert registry was passed and matches sentinel
    assert "registry" in captured_kwargs, \
        "tool_canvas_close_tab must pass 'registry' to canvas.close_tab"
    assert captured_kwargs["registry"] is sentinel, \
        "passed registry must match ctx.terminal_registry"
