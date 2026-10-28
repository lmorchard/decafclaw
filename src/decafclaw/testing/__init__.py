"""Test-only helpers shipped with the decafclaw package.

Everything in this subpackage is for tests and test infrastructure —
production code must never import from it. It is intentionally importable
from ``decafclaw`` (not only from ``tests/``) so ephemeral test servers
that run their own process — e.g. #957's child-process runner — can
register the scripted provider in that process (see
``register_scripted_provider``).

Note: the agent-runtime integration harness itself is test-only and
lives under ``tests/harness.py`` (it drives the ConversationManager,
which test servers do not need).
"""

from .scripted_provider import (
    PROVIDER_SCRIPTED,
    ScriptedProvider,
    ScriptedProviderError,
    ScriptedRequest,
    ScriptedResponse,
    ScriptExhaustedError,
    UnexpectedCallError,
    register_scripted_provider,
    unregister_scripted_provider,
)

__all__ = [
    "PROVIDER_SCRIPTED",
    "ScriptedProvider",
    "ScriptedProviderError",
    "ScriptExhaustedError",
    "ScriptedRequest",
    "ScriptedResponse",
    "UnexpectedCallError",
    "register_scripted_provider",
    "unregister_scripted_provider",
]
