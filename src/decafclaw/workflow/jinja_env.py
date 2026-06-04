"""Jinja2 sandbox for workflow templates and edge conditions.

Uses SandboxedEnvironment — safe for author-written, human-reviewed
workflow YAML. Not intended for LLM-emitted templates at runtime.
"""

from __future__ import annotations

from jinja2.sandbox import SandboxedEnvironment

_env = SandboxedEnvironment(autoescape=False)


def render_template(template_str: str, state: dict) -> str:
    """Render a Jinja template string against workflow state.

    The template sees ``state`` as the root variable, matching the
    authoring convention ``{{ state.step_id.field }}``.
    """
    return _env.from_string(template_str).render(state=state)


def eval_condition(expr: str, state: dict) -> bool:
    """Evaluate a Jinja expression to bool.

    Empty or whitespace-only expressions return True (unconditional).
    The expression sees ``state`` as the root variable.
    """
    if not expr.strip():
        return True
    compiled = _env.compile_expression(expr)
    return bool(compiled(state=state))
