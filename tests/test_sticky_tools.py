import pytest

from decafclaw import sticky
from decafclaw.tools.sticky_tools import tool_widget_pin_sticky, tool_widget_unpin_sticky
from decafclaw.widgets import init_widgets


@pytest.fixture
def widgets_ready(config):
    init_widgets(config)


@pytest.mark.asyncio
async def test_pin_sticky_pins(ctx, widgets_ready):
    res = await tool_widget_pin_sticky(ctx, "markdown_document", {"content": "# hi"})
    assert "[error" not in res.text
    state = sticky.read_sticky_state(ctx.config, ctx.conv_id)
    assert state["widget_type"] == "markdown_document"


@pytest.mark.asyncio
async def test_pin_sticky_rejects_unknown_mode(ctx, widgets_ready):
    res = await tool_widget_pin_sticky(ctx, "text_input", {})
    assert res.text.startswith("[error")


@pytest.mark.asyncio
async def test_unpin_sticky_clears(ctx, widgets_ready):
    await tool_widget_pin_sticky(ctx, "markdown_document", {"content": "# hi"})
    res = await tool_widget_unpin_sticky(ctx)
    assert "[error" not in res.text
    assert sticky.read_sticky_state(ctx.config, ctx.conv_id) == sticky.empty_sticky_state()
