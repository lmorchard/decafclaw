"""Tests for the signature -> tool-definition schema builder (#1371, step 9.6).

The core assertion: a tool function's typed signature produces the same
``parameters`` schema (property names, types, and required list) as the
hand-written ``TOOL_DEFINITIONS`` entry. That is exactly the duplication the
builder removes — parameter names and types spelled in both a signature and a
literal dict must stay in sync.

Descriptions are prose a signature cannot express, so the structural comparison
ignores them; callers supply descriptions explicitly.
"""

import pytest

from decafclaw.skills.vault import tools as vault_tools
from decafclaw.tools.schema_builder import (
    _UNSET,
    build_tool_definition,
    signature_to_parameters,
)


def _structural(params: dict) -> dict:
    """Reduce a ``parameters`` schema to its structural skeleton.

    Drops every ``description`` key (at any nesting depth) so a generated
    schema can be compared against a hand-written one that carries prose the
    signature cannot reproduce. Property order and required order are preserved.
    """

    def _strip(node):
        if isinstance(node, dict):
            return {k: _strip(v) for k, v in node.items() if k != "description"}
        if isinstance(node, list):
            return [_strip(v) for v in node]
        return node

    return _strip(params)


# Every vault tool name that has a matching ``tool_<name>`` function. Collected
# at import so the parametrized case below covers the whole module — the ~600
# lines of hand-written definitions this builder exists to replace.
_VAULT_TOOL_NAMES = [
    name
    for name in (d["function"]["name"] for d in vault_tools.TOOL_DEFINITIONS)
    if hasattr(vault_tools, f"tool_{name}")
]


@pytest.mark.parametrize("tool_name", _VAULT_TOOL_NAMES)
def test_vault_signature_matches_handwritten_definition(tool_name: str) -> None:
    """A vault tool's signature derives the same parameters as its literal def."""
    fn = getattr(vault_tools, f"tool_{tool_name}")
    hand_written = next(d for d in vault_tools.TOOL_DEFINITIONS if d["function"]["name"] == tool_name)

    generated = signature_to_parameters(fn)
    assert _structural(generated) == _structural(hand_written["function"]["parameters"])


def test_vault_read_full_definition_shape() -> None:
    """build_tool_definition assembles a valid, provider-shaped definition."""
    fn = vault_tools.tool_vault_read
    defn = build_tool_definition(fn, priority="critical", writes=False)

    assert defn["type"] == "function"
    assert defn["priority"] == "critical"
    assert defn["writes"] is False
    # The runtime ctx is never exposed to the model.
    assert "ctx" not in defn["function"]["parameters"]["properties"]

    hand_written = next(d for d in vault_tools.TOOL_DEFINITIONS if d["function"]["name"] == "vault_read")
    assert defn["function"]["name"] == hand_written["function"]["name"]
    assert _structural(defn["function"]["parameters"]) == _structural(hand_written["function"]["parameters"])


def test_name_derivation_strips_tool_prefix() -> None:
    def tool_widget_read(page: str) -> str:
        return page

    def helper(page: str) -> str:
        return page

    assert build_tool_definition(tool_widget_read)["function"]["name"] == "widget_read"
    # No tool_ prefix: the raw function name is kept.
    assert build_tool_definition(helper)["function"]["name"] == "helper"


def test_explicit_name_and_description_win() -> None:
    def tool_thing(page: str) -> str:
        """Docstring first line."""
        return page

    defn = build_tool_definition(tool_thing, name="custom_name", description="Custom prose.")
    assert defn["function"]["name"] == "custom_name"
    assert defn["function"]["description"] == "Custom prose."


def test_description_falls_back_to_docstring() -> None:
    def tool_thing(page: str) -> str:
        """Docstring first line.

        More detail on later lines that must not leak into the description.
        """
        return page

    assert build_tool_definition(tool_thing)["function"]["description"] == "Docstring first line."


def test_metadata_fields_omitted_when_unset() -> None:
    def tool_thing(page: str) -> str:
        return page

    defn = build_tool_definition(tool_thing)
    assert "priority" not in defn
    assert "writes" not in defn
    # timeout left at the _UNSET sentinel -> key omitted (dispatch uses default).
    assert "timeout" not in defn


def test_timeout_none_is_explicit_opt_out() -> None:
    def tool_thing(page: str) -> str:
        return page

    defn = build_tool_definition(tool_thing, timeout=None)
    assert "timeout" in defn and defn["timeout"] is None


def test_timeout_value_is_carried() -> None:
    def tool_thing(page: str) -> str:
        return page

    defn = build_tool_definition(tool_thing, timeout=45)
    assert defn["timeout"] == 45


# --- Type-mapping unit tests, exercised through signature_to_parameters. ----
# Each stub declares one parameter of a given type; the builder must map it to
# the matching JSON Schema fragment.


def _params_for(fn) -> dict:
    # These mapper stubs have no leading ctx parameter, so nothing is skipped.
    return signature_to_parameters(fn, skip_first=False)["properties"]


def test_type_mapping_scalars() -> None:
    def f(s: str, i: int, flt: float, b: bool) -> None:
        pass

    props = _params_for(f)
    assert props["s"] == {"type": "string"}
    assert props["i"] == {"type": "integer"}
    assert props["flt"] == {"type": "number"}
    # bool must map to boolean, not integer (bool subclasses int).
    assert props["b"] == {"type": "boolean"}


def test_type_mapping_optional_and_unions() -> None:
    def f(a: int | None = None, b: str | None = None) -> None:
        pass

    props = _params_for(f)
    assert props["a"] == {"type": "integer"}
    assert props["b"] == {"type": "string"}


def test_type_mapping_containers() -> None:
    def f(items: list[str], mapping: dict, pairs: dict[str, int]) -> None:
        pass

    props = _params_for(f)
    assert props["items"] == {"type": "array", "items": {"type": "string"}}
    assert props["mapping"] == {"type": "object"}
    assert props["pairs"] == {"type": "object"}


def test_type_mapping_optional_container() -> None:
    def f(tags: list[str] | None = None) -> None:
        pass

    assert _params_for(f)["tags"] == {"type": "array", "items": {"type": "string"}}


def test_required_tracks_defaults() -> None:
    # Python requires defaulted params to trail non-defaulted ones, so the
    # optional param comes last; required must still list only the two that lack defaults.
    def f(req_a: str, req_c: bool, opt_b: int = 3) -> None:
        pass

    params = signature_to_parameters(f, skip_first=False)
    assert params["required"] == ["req_a", "req_c"]


def test_ctx_is_skipped_and_varargs_ignored() -> None:
    def f(ctx, page: str, *extra) -> None:
        pass

    params = signature_to_parameters(f)
    assert "ctx" not in params["properties"]
    assert "extra" not in params["properties"]  # *args is never model-facing
    assert list(params["properties"]) == ["page"]
    assert params["required"] == ["page"]


def test_unresolvable_string_annotation_fails_open() -> None:
    """A string annotation that cannot be resolved maps to an open schema.

    This is the ``from __future__ import annotations`` / quoted-name case where
    the referenced name is absent at runtime (e.g. a TYPE_CHECKING-only import).
    Failing open to ``{}`` keeps the definition valid rather than raising.
    """

    def f(page: "SomeUnresolvableType") -> None:  # noqa: F821 - intentionally unresolvable
        pass

    assert _params_for(f)["page"] == {}


def test_empty_signature_yields_empty_object() -> None:
    def f(ctx) -> None:
        pass

    params = signature_to_parameters(f)
    assert params == {"type": "object", "properties": {}, "required": []}


def test_unset_sentinel_is_module_constant() -> None:
    """The timeout sentinel is a distinct object, not None or a value."""
    assert _UNSET is not None
