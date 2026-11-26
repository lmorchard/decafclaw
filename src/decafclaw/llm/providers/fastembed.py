"""In-process embedding provider backed by fastembed (ONNX Runtime on CPU).

Keeps vault text on the local machine: no HTTP request, no cloud credentials.
The model weights are downloaded from Hugging Face on first use and cached on
disk; after that the provider works offline.

``fastembed`` is imported lazily so a broken or missing install surfaces as an
actionable log message (and a ``None`` result) rather than an import error at
startup.
"""

import asyncio
import logging
import threading
from typing import Any, Iterable, Protocol

from ..types import ModelInfo

log = logging.getLogger(__name__)


class _EmbeddingModel(Protocol):
    """The slice of ``fastembed.TextEmbedding`` this provider uses."""

    def embed(self, documents: list[str], **kwargs) -> Iterable[Any]: ...


class FastembedProvider:
    """Provider that embeds text locally with fastembed's ONNX models."""

    # Not a chat model source: model-catalog discovery skips it.
    embedding_only = True

    def __init__(self) -> None:
        self._models: dict[str, _EmbeddingModel] = {}
        self._lock = threading.Lock()

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        streaming: bool = False,
        on_chunk: Any = None,
        cancel_event: asyncio.Event | None = None,
        timeout: int = 300,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """fastembed is embeddings-only; completions belong to another provider."""
        raise NotImplementedError("FastembedProvider does not support chat completions")

    async def get_model_info(
        self,
        model: str,
        *,
        timeout: int = 30,
        **kwargs: Any,
    ) -> ModelInfo | None:
        """No model metadata to report — local embedding model only."""
        return None

    def default_context_window(self, model: str) -> int | None:
        """Embedding models have no context window; not a chat provider."""
        return None

    def _get_model(self, model: str) -> _EmbeddingModel | None:
        """Return the loaded ``TextEmbedding`` for ``model``, or ``None``.

        Loading is synchronous and may download weights on first use; callers
        run it in a worker thread. Failures are logged with a fix hint and are
        not cached, so a corrected model name or a recovered network retries.
        """
        with self._lock:
            cached = self._models.get(model)
            if cached is not None:
                return cached
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                log.error(
                    "Embedding provider 'fastembed' is configured but the package is not installed (%s). "
                    "Install it with 'uv sync' (it is a project dependency) or 'pip install fastembed'.",
                    exc,
                )
                return None
            try:
                embedding_model = TextEmbedding(model_name=model)
            except Exception as exc:
                log.error(
                    "fastembed could not load model '%s': %s. Check the model name against "
                    "fastembed's supported models (TextEmbedding.list_supported_models()) and that "
                    "Hugging Face is reachable for the first-time weight download.",
                    model,
                    exc,
                )
                return None
            self._models[model] = embedding_model
            log.info("Loaded local fastembed model '%s'", model)
            return embedding_model

    def _embed_sync(self, model: str, texts: list[str]) -> list[list[float]] | None:
        embedding_model = self._get_model(model)
        if embedding_model is None:
            return None
        try:
            vectors = list(embedding_model.embed(texts))
        except Exception as exc:
            log.error("fastembed embedding failed for model '%s': %s", model, exc)
            return None
        return [[float(value) for value in vector] for vector in vectors]

    async def embed(
        self,
        model: str,
        text: str,
        *,
        timeout: int = 30,
        **kwargs,
    ) -> list[float] | None:
        vectors = await asyncio.to_thread(self._embed_sync, model, [text])
        if not vectors:
            return None
        return vectors[0]

    async def embed_batch(
        self,
        model: str,
        texts: list[str],
        *,
        timeout: int = 30,
        **kwargs,
    ) -> list[list[float]] | None:
        """Embed many texts in one ONNX pass. Returns ``None`` on failure."""
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_sync, model, list(texts))
