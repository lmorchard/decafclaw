"""Tests for jinja_env — render_template, eval_condition, sandbox."""

import pytest

from decafclaw.workflow.jinja_env import eval_condition, render_template


def test_render_template_basic():
    result = render_template("Hello {{ state.name }}", {"name": "world"})
    assert result == "Hello world"


def test_render_template_default_filter():
    result = render_template(
        "Topic: {{ state.topic | default('agent testbed') }}", {}
    )
    assert result == "Topic: agent testbed"


def test_render_template_with_value():
    result = render_template(
        "Topic: {{ state.topic | default('agent testbed') }}", {"topic": "ML"}
    )
    assert result == "Topic: ML"


def test_render_template_nested():
    result = render_template(
        "{{ state.step1.greeting }}", {"step1": {"greeting": "hi"}}
    )
    assert result == "hi"


def test_eval_condition_empty_is_true():
    assert eval_condition("", {}) is True
    assert eval_condition("   ", {}) is True


def test_eval_condition_truthy():
    assert eval_condition("state.x == 1", {"x": 1}) is True
    assert eval_condition("state.x > 0", {"x": 5}) is True


def test_eval_condition_falsy():
    assert eval_condition("state.x == 1", {"x": 2}) is False
    assert eval_condition("state.done", {"done": False}) is False


def test_eval_condition_missing_key_is_falsy():
    # Jinja2 evaluates undefined as falsy
    assert eval_condition("state.missing", {}) is False


def test_sandbox_blocks_dangerous_import():
    """Sandbox blocks access to dangerous builtins like __import__."""
    with pytest.raises(Exception):
        render_template("{{ ''.__class__.__mro__[1].__subclasses__() }}", {})
