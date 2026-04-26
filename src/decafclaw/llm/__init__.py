"""LLM client — multi-provider abstraction for LLM completions and embeddings.

Public API:
  from decafclaw.llm import call_llm, call_llm_streaming, embed_text

Call sites can use either:
  - model_name="gemini-flash" (new: resolves through config.model_configs)
  - llm_url/llm_model/llm_api_key overrides (legacy: creates one-off provider)
"""

import logging
from typing import Any

from .registry import (  # noqa: F401
    clear_providers,
    get_provider,
    init_providers,
    list_providers,
    register_provider,
)
from .types import (  # noqa: F401
    PROVIDER_LITELLM,
    PROVIDER_OPENAI,
    PROVIDER_OPENAI_COMPAT,
    PROVIDER_VERTEX,
    Provider,
    StreamCallback,
)

log = logging.getLogger(__name__)


async def call_llm(config: Any, messages: list, tools: list | None = None,
                   llm_url: str | None = None, llm_model: str | None = None,
                   llm_api_key: str | None = None,
                   model_name: str | None = None) -> dict:
    """Call the LLM and return the response message.

    Use model_name to resolve through the provider/model config system.
    Or use llm_url/llm_model/llm_api_key for legacy override behavior.
    """
    provider, model, timeout = _resolve(
        config, model_name=model_name,
        llm_url=llm_url, llm_model=llm_model, llm_api_key=llm_api_key,
    )
    return await provider.complete(
        model, messages, tools=tools, streaming=False, timeout=timeout,
    )


async def call_llm_streaming(
    config: Any, messages: list, tools: list | None = None,
    on_chunk: Any = None, cancel_event: Any = None,
    llm_url: str | None = None, llm_model: str | None = None,
    llm_api_key: str | None = None,
    model_name: str | None = None,
) -> dict:
    """Call the LLM with streaming.

    Use model_name to resolve through the provider/model config system.
    Or use llm_url/llm_model/llm_api_key for legacy override behavior.
    """
    provider, model, timeout = _resolve(
        config, model_name=model_name,
        llm_url=llm_url, llm_model=llm_model, llm_api_key=llm_api_key,
    )
    return await provider.complete(
        model, messages, tools=tools, streaming=True,
        on_chunk=on_chunk, cancel_event=cancel_event, timeout=timeout,
    )


async def embed_text(config: Any, text: str,
                     model_name: str | None = None) -> list[float] | None:
    """Embed text using a named model config.

    Falls back to the default model's provider if model_name is not given.
    """
    provider, model, _timeout = _resolve(config, model_name=model_name)
    return await provider.embed(model, text)


def _resolve(
    config: Any,
    model_name: str | None = None,
    llm_url: str | None = None,
    llm_model: str | None = None,
    llm_api_key: str | None = None,
) -> tuple[Any, str, int]:
    """Resolve a provider, model name, and timeout.

    Priority:
    1. model_name — resolve through config.model_configs/providers
    2. llm_url/llm_model/llm_api_key — legacy overrides (one-off provider)
    3. Default provider from registry
    """
    from .providers.openai_compat import OpenAICompatProvider

    # Path 1: Named model config
    if model_name:
        from ..config import resolve_model
        try:
            pc, mc = resolve_model(config, model_name)
            provider = get_provider(mc.provider)
            return provider, mc.model, mc.timeout
        except KeyError as e:
            log.warning("Model resolution failed: %s; falling back to default", e)

    # Path 2: Legacy overrides
    if llm_url or llm_api_key:
        url = llm_url or config.llm.url
        model = llm_model or config.llm.model
        api_key = llm_api_key or config.llm.api_key
        timeout = getattr(config.llm, "timeout", 300)
        return OpenAICompatProvider(url=url, api_key=api_key), model, timeout

    # Path 3: Default from registry (model from config.llm or default_model)
    model = llm_model or config.llm.model
    timeout = getattr(config.llm, "timeout", 300)

    # Try named default first
    if config.default_model and config.default_model in config.model_configs:
        try:
            mc = config.model_configs[config.default_model]
            provider = get_provider(mc.provider)
            return provider, mc.model, mc.timeout
        except KeyError as exc:
            log.debug("default model %r not in provider registry: %s; falling through",
                      config.default_model, exc)

    # Fall back to "default" provider in registry
    try:
        provider = get_provider("default")
    except KeyError:
        provider = OpenAICompatProvider(url=config.llm.url, api_key=config.llm.api_key)

    return provider, model, timeout
