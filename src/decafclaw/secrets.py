"""Secret resolution for ``${VAR_NAME}`` references (#1032).

The settled design (see the issue): secret values live OUT of
``data/{agent_id}/config.json`` and in an external store (environment
variables / ``.env``) that is out-of-bounds for the agent. ``config.json``
therefore holds *references* — string interpolation tokens of the shape
``${VAR_NAME}``.

This module owns the pluggable resolver and the ``${VAR_NAME}`` → value
substitution. The default driver resolves from environment variables; the
protocol allows other drivers (e.g. a secret-manager backend) without touching
callers.
"""

from __future__ import annotations

import os
import re
from typing import Protocol

_VAR_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class SecretResolutionError(RuntimeError):
    """Raised when a required secret variable is missing or empty."""


class SecretResolver(Protocol):
    """Pluggable secret resolver: name → value."""

    def resolve(self, name: str, default: str = "") -> str: ...


class EnvSecretResolver:
    """Resolves secret names from environment variables (the default driver)."""

    def __init__(self, environ: dict[str, str] | None = None) -> None:
        self._env: dict[str, str] = dict(os.environ if environ is None else environ)

    def resolve(self, name: str, default: str = "") -> str:
        value = self._env.get(name)
        if value is None or value == "":
            raise SecretResolutionError(f"Secret variable {name} is not set")
        return value


def is_secret_ref(value: str | None) -> bool:
    """True when ``value`` is a ``${VAR_NAME}`` interpolation token."""
    if not value:
        return False
    return _VAR_REF_RE.match(value.strip()) is not None


def resolve_secret(ref: str, resolver: SecretResolver | None = None) -> str:
    """Resolve a ``${VAR_NAME}`` reference to its secret value.

    Returns the resolved value, or the input unchanged when it is not a
    ``${VAR_NAME}`` reference (literal passthrough — the caller decides how to
    treat literals). Raises :class:`SecretResolutionError` when a required
    variable is missing.
    """
    ref = (ref or "").strip()
    if not ref:
        return ""
    if not is_secret_ref(ref):
        return ref
    name = _VAR_REF_RE.match(ref).group(1)  # type: ignore[union-attr]
    return (resolver or EnvSecretResolver()).resolve(name, default="")
