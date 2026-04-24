"""Tests for the widget catalog registry."""

import json
from pathlib import Path

import pytest

from decafclaw.widgets import (
    WidgetRegistry,
    _scan_tier,
    load_widget_registry,
)


def _write_widget(catalog: Path, name: str, *,
                  schema: dict | None = None,
                  description: str = "test widget",
                  modes: list[str] | None = None,
                  accepts_input: bool = False,
                  js_body: str = "// stub\n",
                  skip_js: bool = False) -> None:
    d = catalog / name
    d.mkdir(parents=True)
    descriptor = {
        "name": name,
        "description": description,
        "modes": modes or ["inline"],
        "accepts_input": accepts_input,
        "data_schema": schema or {
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
        },
    }
    (d / "widget.json").write_text(json.dumps(descriptor))
    if not skip_js:
        (d / "widget.js").write_text(js_body)


@pytest.fixture
def fake_config(tmp_path):
    class _Cfg:
        agent_path = tmp_path / "agent"
    return _Cfg()


# ---------------- scan ----------------


def test_scan_empty_dirs(tmp_path, fake_config):
    reg = load_widget_registry(fake_config,
                               bundled_dir=tmp_path / "bundled",
                               admin_dir=tmp_path / "admin")
    assert reg.list() == []


def test_scan_bundled_only(tmp_path, fake_config):
    bundled = tmp_path / "bundled"
    _write_widget(bundled, "data_table")
    reg = load_widget_registry(fake_config,
                               bundled_dir=bundled,
                               admin_dir=tmp_path / "admin")
    assert reg.tier("data_table") == "bundled"
    assert reg.get("data_table") is not None


def test_admin_overrides_bundled(tmp_path, fake_config):
    bundled = tmp_path / "bundled"
    admin = tmp_path / "admin"
    _write_widget(bundled, "pie_chart",
                  description="bundled version")
    _write_widget(admin, "pie_chart",
                  description="admin version")
    reg = load_widget_registry(fake_config,
                               bundled_dir=bundled, admin_dir=admin)
    assert reg.tier("pie_chart") == "admin"
    assert reg.get("pie_chart").description == "admin version"


def test_missing_widget_js_skipped(tmp_path, fake_config, caplog):
    bundled = tmp_path / "bundled"
    _write_widget(bundled, "no_js", skip_js=True)
    _write_widget(bundled, "ok_one")
    reg = load_widget_registry(fake_config,
                               bundled_dir=bundled,
                               admin_dir=tmp_path / "admin")
    assert reg.get("no_js") is None
    assert reg.get("ok_one") is not None


def test_malformed_widget_json_skipped(tmp_path, fake_config, caplog):
    bundled = tmp_path / "bundled"
    d = bundled / "bad"
    d.mkdir(parents=True)
    (d / "widget.json").write_text("{ not valid json")
    (d / "widget.js").write_text("// stub")
    reg = load_widget_registry(fake_config,
                               bundled_dir=bundled,
                               admin_dir=tmp_path / "admin")
    assert reg.get("bad") is None


def test_meta_schema_rejects_missing_fields(tmp_path, fake_config):
    bundled = tmp_path / "bundled"
    d = bundled / "incomplete"
    d.mkdir(parents=True)
    # Missing `data_schema` and `modes`.
    (d / "widget.json").write_text(json.dumps({
        "name": "incomplete",
        "description": "x",
    }))
    (d / "widget.js").write_text("// stub")
    reg = load_widget_registry(fake_config,
                               bundled_dir=bundled,
                               admin_dir=tmp_path / "admin")
    assert reg.get("incomplete") is None


def test_meta_schema_rejects_invalid_mode(tmp_path, fake_config):
    bundled = tmp_path / "bundled"
    _write_widget(bundled, "weird", modes=["something_else"])
    reg = load_widget_registry(fake_config,
                               bundled_dir=bundled,
                               admin_dir=tmp_path / "admin")
    assert reg.get("weird") is None


# ---------------- validate ----------------


def test_validate_happy(tmp_path, fake_config):
    bundled = tmp_path / "bundled"
    _write_widget(bundled, "data_table", schema={
        "type": "object",
        "required": ["columns", "rows"],
        "properties": {
            "columns": {"type": "array"},
            "rows": {"type": "array"},
        },
    })
    reg = load_widget_registry(fake_config,
                               bundled_dir=bundled,
                               admin_dir=tmp_path / "admin")
    ok, err = reg.validate("data_table", {"columns": [], "rows": []})
    assert ok is True
    assert err is None


def test_validate_missing_required_field(tmp_path, fake_config):
    bundled = tmp_path / "bundled"
    _write_widget(bundled, "data_table", schema={
        "type": "object",
        "required": ["columns", "rows"],
        "properties": {
            "columns": {"type": "array"},
            "rows": {"type": "array"},
        },
    })
    reg = load_widget_registry(fake_config,
                               bundled_dir=bundled,
                               admin_dir=tmp_path / "admin")
    ok, err = reg.validate("data_table", {"columns": []})  # missing rows
    assert ok is False
    assert err is not None
    assert "rows" in err


def test_validate_unknown_widget(tmp_path, fake_config):
    reg = load_widget_registry(fake_config,
                               bundled_dir=tmp_path / "bundled",
                               admin_dir=tmp_path / "admin")
    ok, err = reg.validate("no_such_widget", {})
    assert ok is False
    assert "unknown widget" in err


def test_validate_does_not_raise_on_bad_input_shape(tmp_path, fake_config):
    """Validate must never raise, even with odd inputs — callers expect
    a (bool, str|None) return."""
    bundled = tmp_path / "bundled"
    _write_widget(bundled, "strict", schema={
        "type": "object",
        "required": ["x"],
        "properties": {"x": {"type": "integer"}},
    })
    reg = load_widget_registry(fake_config,
                               bundled_dir=bundled,
                               admin_dir=tmp_path / "admin")
    ok, err = reg.validate("strict", {"x": "not an integer"})
    assert ok is False
    assert err is not None


# ---------------- resolve_path / tier ----------------


def test_resolve_path_returns_js(tmp_path, fake_config):
    bundled = tmp_path / "bundled"
    _write_widget(bundled, "foo")
    reg = load_widget_registry(fake_config,
                               bundled_dir=bundled,
                               admin_dir=tmp_path / "admin")
    path = reg.resolve_path("foo")
    assert path.name == "widget.js"
    assert path.is_file()


def test_resolve_path_unknown_raises(tmp_path, fake_config):
    reg = load_widget_registry(fake_config,
                               bundled_dir=tmp_path / "bundled",
                               admin_dir=tmp_path / "admin")
    with pytest.raises(KeyError):
        reg.resolve_path("nope")


def test_registry_empty_by_default():
    reg = WidgetRegistry()
    assert reg.list() == []
    assert reg.get("x") is None
    assert reg.tier("x") is None


def test_load_registry_missing_dirs_does_not_raise(fake_config):
    """Startup must tolerate entirely-absent catalog dirs (first-run)."""
    reg = load_widget_registry(fake_config,
                               bundled_dir=Path("/nonexistent/bundled"),
                               admin_dir=Path("/nonexistent/admin"))
    assert reg.list() == []


# ---------------- real bundled widgets ----------------


def test_bundled_data_table_is_registered(fake_config):
    """A default registry scan should find the bundled data_table widget
    shipped with the app, with the expected schema shape."""
    reg = load_widget_registry(fake_config,
                               admin_dir=Path("/nonexistent/admin"))
    desc = reg.get("data_table")
    assert desc is not None
    assert desc.tier == "bundled"
    assert "inline" in desc.modes
    assert desc.accepts_input is False
    # Schema requires columns + rows (the contract used by tool retrofits).
    required = desc.data_schema.get("required", [])
    assert "columns" in required
    assert "rows" in required
    # A happy payload validates.
    ok, err = reg.validate("data_table", {
        "columns": [{"key": "a", "label": "A"}],
        "rows": [{"a": 1}],
    })
    assert ok is True, err
    # A missing-rows payload fails.
    ok, err = reg.validate("data_table", {"columns": []})
    assert ok is False
