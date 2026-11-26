"""Model resolution — the shared leaf for the config ↔ llm boundary (#1329).

Both the configuration layer (``config``, ``config_types``) and the LLM layer
(``llm``) need two things that used to live in each other's modules:

- **Provider-type constants** — the ``type`` values used in a config.json
  ``providers`` entry. These moved out of :mod:`decafclaw.llm.types` so that
  importing them never triggers the ``llm`` package ``__init__`` (which is what
  made ``config_types → llm.types`` a latent cycle).
- **Model resolution** — mapping a named model to its provider + config, and the
  effective-default / context-window ladders. These moved out of ``config`` and
  :mod:`decafclaw.model_catalog` so neither layer has to import the other.

This module is a true leaf: at runtime it imports nothing from ``decafclaw``
(the one exception is a deferred :func:`load_catalog` import inside
:func:`effective_default_model`, which cannot create a cycle because it runs at
call time, after every module is fully loaded). Type-only names are imported
under ``TYPE_CHECKING``. Both layers import this module at the top level with no
import cycle.

No runtime behavior changes — these are the same functions and constants, only
re-homed so the deferred imports that papered over the cycle can be removed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .config_types import ModelConfig, ProviderConfig

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider type constants — used in config.json "providers" section.
# (Moved from llm.types; re-exported there for backward compatibility.)
# ---------------------------------------------------------------------------

PROVIDER_OPENAI_COMPAT = "openai-compat"
PROVIDER_OPENAI = "openai"
PROVIDER_OPENAI_RESPONSES = "openai-responses"
PROVIDER_VERTEX = "vertex"
# Built-in in-process provider: local ONNX embeddings, registered without a
# config.providers entry (see registry.init_providers).
PROVIDER_FASTEMBED = "fastembed"
# Legacy alias
PROVIDER_LITELLM = "litellm"
# Test-only: replayed in-memory provider (decafclaw.testing.ScriptedProvider).
# Registered directly via the registry — init_providers never instantiates it.
PROVIDER_SCRIPTED = "scripted"


# ---------------------------------------------------------------------------
# Model resolution (new provider-based system)
# ---------------------------------------------------------------------------


def resolve_model(
    config: "Config",
    name: str = "",
) -> tuple["ProviderConfig", "ModelConfig"]:
    """Resolve a named model config to its provider + model config.

    Falls back to the effective default model if name is empty — the
    model catalog sidecar's pointer when it names a selectable model,
    else config.default_model (#1211).
    Raises KeyError if the model or its provider isn't found.
    """
    model_name = name or effective_default_model(config)
    if not model_name:
        raise KeyError("No model name given and no default_model configured")

    if model_name not in config.model_configs:
        available = ", ".join(sorted(config.model_configs.keys())) or "(none)"
        raise KeyError(f"Unknown model config '{model_name}'. Available: {available}")

    mc = config.model_configs[model_name]
    if mc.provider not in config.providers:
        available = ", ".join(sorted(config.providers.keys())) or "(none)"
        raise KeyError(f"Model '{model_name}' references unknown provider '{mc.provider}'. Available: {available}")

    return config.providers[mc.provider], mc


def effective_default_model(config: "Config") -> str:
    """Global default model, resolution order (#1211):

    1. The sidecar's ``default_model`` pointer, when it names a
       selectable model (enabled discovered or hand-configured).
    2. ``config.json``'s ``default_model`` (the ``Config`` field, kept
       pristine — the sidecar never overwrites it).
    3. Empty string; callers fall through to ``config.llm.model`` exactly
       as they did before this feature existed.

    A pointer at a disabled or vanished model falls back cleanly.
    """
    # Deferred: this module is the shared leaf imported by both config and llm;
    # model_catalog imports it back (for re-export) at module level, so the
    # sidecar read is resolved at call time to keep the import graph acyclic.
    from .model_catalog import load_catalog

    pointer = load_catalog(config).default_model
    if pointer and pointer in config.model_configs:
        return pointer
    return config.default_model


def effective_context_window(config: "Config", model_name: str = "") -> int:
    """Context-window ladder (#1360), highest first:

    1. The named model's configured ``context_window_size``, when positive
       (``model_name`` empty means ``config.default_model``).
    2. ``config.llm.context_window_size``, when positive.
    3. ``config.compaction.max_tokens`` — the conservative proxy.

    Configured values only: discovery of a provider-reported window is an
    async enrichment (``llm.ensure_model_context_window``) that writes the
    model config and then reads this ladder.
    """
    name = model_name or config.default_model
    if name and name in config.model_configs:
        window = config.model_configs[name].context_window_size
        if window and window > 0:
            return window
    window = config.llm.context_window_size
    if window and window > 0:
        return window
    return config.compaction.max_tokens
