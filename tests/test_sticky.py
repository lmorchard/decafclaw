import pytest

from decafclaw import sticky
from decafclaw.widgets import init_widgets


def test_empty_state_shape():
    assert sticky.empty_sticky_state() == {
        "schema_version": 1, "widget_type": None, "data": None,
    }


def test_read_missing_is_empty(config):
    assert sticky.read_sticky_state(config, "no-such-conv") == \
        sticky.empty_sticky_state()


def test_write_then_read_roundtrip(config):
    state = {"schema_version": 1, "widget_type": "markdown_document",
             "data": {"content": "# hi"}}
    assert sticky.write_sticky_state(config, "conv-a", state) is True
    assert sticky.read_sticky_state(config, "conv-a") == state


def test_corrupt_file_is_empty(config):
    from decafclaw.conversation_paths import sidecar_path
    p = sidecar_path(config, "conv-b", "sticky.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json")
    assert sticky.read_sticky_state(config, "conv-b") == \
        sticky.empty_sticky_state()


@pytest.fixture
def widgets_ready(config):
    init_widgets(config)  # loads bundled widget.json registry


@pytest.mark.asyncio
async def test_set_sticky_writes_and_emits(config, widgets_ready):
    events = []

    async def emit(conv_id, payload):
        events.append((conv_id, payload))

    res = await sticky.set_sticky(
        config, "conv-s", "markdown_document", {"content": "# hi"}, emit=emit)
    assert res.ok, res.error
    state = sticky.read_sticky_state(config, "conv-s")
    assert state["widget_type"] == "markdown_document"
    assert events and events[0][1]["type"] == "sticky_set"
    assert events[0][1]["widget_type"] == "markdown_document"


@pytest.mark.asyncio
async def test_set_sticky_rejects_non_sticky_widget(config, widgets_ready):
    # text_input declares modes ["inline"] only.
    res = await sticky.set_sticky(config, "conv-s", "text_input", {})
    assert not res.ok
    assert "sticky" in res.error


@pytest.mark.asyncio
async def test_set_sticky_replaces_previous(config, widgets_ready):
    await sticky.set_sticky(config, "conv-s", "markdown_document", {"content": "# a"})
    await sticky.set_sticky(config, "conv-s", "markdown_document", {"content": "# b"})
    state = sticky.read_sticky_state(config, "conv-s")
    assert state["data"]["content"] == "# b"


@pytest.mark.asyncio
async def test_clear_sticky_emits_and_empties(config, widgets_ready):
    events = []

    async def emit(conv_id, payload):
        events.append(payload)

    await sticky.set_sticky(config, "conv-s", "markdown_document", {"content": "# a"})
    res = await sticky.clear_sticky(config, "conv-s", emit=emit)
    assert res.ok
    assert sticky.read_sticky_state(config, "conv-s") == sticky.empty_sticky_state()
    assert events and events[-1]["type"] == "sticky_clear"
