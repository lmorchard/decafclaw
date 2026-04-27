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

    monkeypatch.setattr(canvas_mod, "get_widget_registry", lambda: _Reg())


@pytest.mark.asyncio
async def test_canvas_set_happy_path(config, md_doc_registry):
    manager = MagicMock()
    manager.emit = AsyncMock()
    ctx = _make_ctx(config, manager)
    result = await canvas_tools.tool_canvas_set(
        ctx, "markdown_document", {"content": "# Hi"},
    )
    assert isinstance(result, ToolResult)
    assert "canvas updated" in result.text
    assert "/canvas/conv1" in result.text
    manager.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_canvas_set_unknown_widget(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_set(
        ctx, "no_such", {"content": "x"},
    )
    assert result.text.startswith("[error: ")
    assert "not registered" in result.text


@pytest.mark.asyncio
async def test_canvas_update_no_prior_set(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_update(ctx, {"content": "x"})
    assert result.text.startswith("[error: ")
    assert "no canvas widget set" in result.text


@pytest.mark.asyncio
async def test_canvas_update_after_set(config, md_doc_registry):
    manager = MagicMock(emit=AsyncMock())
    ctx = _make_ctx(config, manager)
    await canvas_tools.tool_canvas_set(ctx, "markdown_document", {"content": "v1"})
    result = await canvas_tools.tool_canvas_update(ctx, {"content": "v2"})
    assert result.text == "canvas updated"


@pytest.mark.asyncio
async def test_canvas_clear_when_empty(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_clear(ctx)
    assert result.text == "canvas already empty"


@pytest.mark.asyncio
async def test_canvas_clear_with_tab(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    await canvas_tools.tool_canvas_set(ctx, "markdown_document", {"content": "x"})
    result = await canvas_tools.tool_canvas_clear(ctx)
    assert result.text == "canvas cleared"


@pytest.mark.asyncio
async def test_canvas_read_empty(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    result = await canvas_tools.tool_canvas_read(ctx)
    assert result.data is None
    assert "empty" in result.text.lower()


@pytest.mark.asyncio
async def test_canvas_read_populated(config, md_doc_registry):
    ctx = _make_ctx(config, MagicMock(emit=AsyncMock()))
    await canvas_tools.tool_canvas_set(ctx, "markdown_document",
                                       {"content": "x"}, label="Lbl")
    result = await canvas_tools.tool_canvas_read(ctx)
    assert result.data is not None
    assert result.data["widget_type"] == "markdown_document"
    assert result.data["label"] == "Lbl"
    assert result.data["data"] == {"content": "x"}


def test_tools_registered_as_always_loaded():
    """Canvas tools appear in the always-loaded registry."""
    from decafclaw.tools import TOOL_DEFINITIONS, TOOLS
    for name in ("canvas_set", "canvas_update", "canvas_clear", "canvas_read"):
        assert name in TOOLS, f"{name} missing from TOOLS"
    names = {d["function"]["name"] for d in TOOL_DEFINITIONS
             if d.get("type") == "function"}
    for name in ("canvas_set", "canvas_update", "canvas_clear", "canvas_read"):
        assert name in names, f"{name} missing from TOOL_DEFINITIONS"
