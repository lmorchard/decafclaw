from decafclaw import sticky


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
