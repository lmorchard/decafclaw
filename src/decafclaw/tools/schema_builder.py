"""Build tool-definition ``parameters`` schemas from typed function signatures.

Hand-written ``TOOL_DEFINITIONS`` (see :mod:`decafclaw.tools` and each skill's
``tools.py``) re-spell every parameter name, type, and required-ness that
already appears in the tool function's signature. The two copies must be kept
in sync by hand, and drift between them is a recurring source of bugs.

This module is the shared primitive for that duplication (#1371, step 9.6):
it inspects a tool function's signature and derives the ``parameters`` JSON
schema (property names, types, and the required list) so the schema cannot
silently disagree with the code that actually runs.

Scope note: this is a prototype. It produces the *structural* part of a
definition — the ``parameters`` object plus simple metadata (name, priority,
writes, timeout). Per-parameter and per-tool *descriptions* are prose that a
signature cannot express; callers supply those explicitly. Migrating the
vault's hand-written definitions to this builder is step 9.7, not here.

The first parameter of every tool is the runtime ``ctx`` (see
:mod:`decafclaw.context`); it is never exposed to the model, so the builder
skips it by default.
"""

from __future__ import annotations

import inspect
import logging
import types
from typing import Any, Union, get_args, get_origin, get_type_hints

log = logging.getLogger(__name__)


# Sentinel distinguishing "timeout not specified" (omit the key, so dispatch
# falls back to the configured default) from an explicit ``None`` (opt out of
# the watchdog). Mirrors dispatch's ``_MISSING`` handling.
_UNSET = object()


def _is_union(tp: Any) -> bool:
    """True for PEP 604 unions (``int | None``) and ``typing.Union``/``Optional``."""
    if tp is None:
        return False
    origin = get_origin(tp)
    return origin is Union or isinstance(tp, types.UnionType)


def _type_to_schema(tp: Any) -> dict[str, Any]:
    """Map a resolved Python type to a JSON Schema fragment.

    Covers the scalar types and generic containers that appear in tool
    signatures. Unions drop their ``None`` member (so ``int | None`` maps to
    ``integer``); a union of several concrete types has no single JSON Schema
    type and fails open to ``{}``. Unknown or unresolvable types likewise fail
    open to ``{}`` (accepts any value) rather than raising — a schema the
    provider cannot read is worse than an unconstrained one.
    """
    if tp is None or tp is inspect.Parameter.empty:
        return {}

    if _is_union(tp):
        non_none = [a for a in get_args(tp) if a is not type(None)]
        if len(non_none) == 1:
            return _type_to_schema(non_none[0])
        # None-only, or a multi-type union: no single JSON Schema type.
        return {}

    origin = get_origin(tp)
    if origin is list or tp is list:  # list[str] -> array; bare list -> array
        schema: dict[str, Any] = {"type": "array"}
        args = get_args(tp)
        if args:
            schema["items"] = _type_to_schema(args[0])
        return schema

    if origin is dict or tp is dict:  # dict / dict[str, X] -> object
        return {"type": "object"}

    # Scalars. ``bool`` is checked before ``int`` because bool subclasses int.
    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is str:
        return {"type": "string"}

    if tp is Any:  # typing.Any -> unconstrained
        return {}

    log.debug("schema_builder: no JSON Schema mapping for %r; failing open to {}", tp)
    return {}


def _resolve_annotation(fn: Any, name: str, raw: Any) -> Any:
    """Resolve a parameter's annotation to a real type object.

    Real objects (the common case — skill ``tools.py`` files must not use
    ``from __future__ import annotations``) pass through unchanged. String
    annotations (quoted, or produced by ``from __future__ import annotations``)
    are resolved via :func:`typing.get_type_hints`. If that fails — typically
    because a name is imported only under ``if TYPE_CHECKING`` and so absent at
    runtime — the parameter fails open to an unconstrained schema.
    """
    if raw is inspect.Parameter.empty:
        return None
    if not isinstance(raw, str):
        return raw
    try:
        hints = get_type_hints(fn)
    except Exception:  # noqa: BLE001 - an unresolvable annotation fails open
        return None
    return hints.get(name)


def signature_to_parameters(fn: Any, *, skip_first: bool = True) -> dict[str, Any]:
    """Build the ``parameters`` JSON schema for a tool function's signature.

    The first parameter (the runtime ``ctx``) is skipped when ``skip_first``;
    it is never exposed to the model. Parameters without a default value are
    listed in ``required``; signature order is preserved for both. Returns::

        {"type": "object", "properties": {...}, "required": [...]}
    """
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    if skip_first and params:
        params = params[1:]

    properties: dict[str, Any] = {}
    required: list[str] = []
    for p in params:
        # *args / **kwargs are not part of a model-facing tool schema.
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        properties[p.name] = _type_to_schema(_resolve_annotation(fn, p.name, p.annotation))
        if p.default is inspect.Parameter.empty:
            required.append(p.name)

    return {"type": "object", "properties": properties, "required": required}


def _default_tool_name(fn: Any) -> str:
    """Derive a tool name from the function, stripping a leading ``tool_``.

    Matches the convention where ``tool_vault_read`` is exposed to the model as
    ``vault_read``. Falls back to the raw function name when there is no prefix.
    """
    name = getattr(fn, "__name__", "") or ""
    return name[len("tool_") :] if name.startswith("tool_") else name


def _first_line(doc: str | None) -> str:
    """First non-empty line of a docstring, or "" when there is none."""
    if not doc:
        return ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def build_tool_definition(
    fn: Any,
    *,
    name: str | None = None,
    description: str = "",
    priority: str | None = None,
    writes: bool | None = None,
    timeout: Any = _UNSET,
) -> dict[str, Any]:
    """Assemble a full tool definition from a typed signature.

    ``name`` defaults to the function name with a leading ``tool_`` stripped;
    ``description`` falls back to the docstring's first line when empty. The
    ``parameters`` object is derived from the signature via
    :func:`signature_to_parameters`.

    ``priority`` and ``writes`` are omitted from the definition when left as
    ``None`` (matching hand-written definitions that omit them). ``timeout``
    uses the :data:`_UNSET` sentinel: leave it unset to omit the key (dispatch
    then applies the configured default), or pass ``None`` explicitly to opt
    out of the watchdog.

    Key order matches the hand-written definitions (``type``, ``priority``,
    ``timeout``, ``writes``, ``function``) so a migrated definition reads like
    the originals.
    """
    defn: dict[str, Any] = {"type": "function"}
    if priority is not None:
        defn["priority"] = priority
    if timeout is not _UNSET:
        defn["timeout"] = timeout
    if writes is not None:
        defn["writes"] = writes

    defn["function"] = {
        "name": name or _default_tool_name(fn),
        "description": description or _first_line(getattr(fn, "__doc__", None)),
        "parameters": signature_to_parameters(fn),
    }
    return defn
