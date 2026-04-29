"""Tests for the text_input widget's data_schema validation."""

from decafclaw.widgets import load_widget_registry


class _Cfg:
    def __init__(self, agent_path):
        self.agent_path = agent_path


def _registry(tmp_path):
    return load_widget_registry(_Cfg(tmp_path / "agent"))


def test_text_input_registered(tmp_path):
    reg = _registry(tmp_path)
    desc = reg.get("text_input")
    assert desc is not None
    assert desc.accepts_input is True
    assert desc.modes == ["inline"]


def test_validate_single_field(tmp_path):
    reg = _registry(tmp_path)
    ok, err = reg.validate("text_input", {
        "prompt": "What's your name?",
        "fields": [{"key": "value", "label": "Name"}],
    })
    assert ok, err


def test_validate_multi_field_with_optionals(tmp_path):
    reg = _registry(tmp_path)
    ok, err = reg.validate("text_input", {
        "prompt": "Contact info",
        "fields": [
            {"key": "name", "label": "Name", "placeholder": "Les"},
            {"key": "email", "label": "Email", "default": "x@y",
             "max_length": 200, "required": False},
            {"key": "bio", "label": "Bio", "multiline": True},
        ],
        "submit_label": "Send",
    })
    assert ok, err


def test_validate_rejects_empty_fields(tmp_path):
    reg = _registry(tmp_path)
    ok, _ = reg.validate("text_input", {
        "prompt": "x", "fields": [],
    })
    assert not ok


def test_validate_rejects_missing_key(tmp_path):
    reg = _registry(tmp_path)
    ok, _ = reg.validate("text_input", {
        "prompt": "x",
        "fields": [{"label": "no key"}],
    })
    assert not ok


def test_validate_rejects_missing_prompt(tmp_path):
    reg = _registry(tmp_path)
    ok, _ = reg.validate("text_input", {
        "fields": [{"key": "v", "label": "V"}],
    })
    assert not ok
