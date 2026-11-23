"""Vault page retrieval for the suggested-prompt survey (#1738).

The survey scores a predicted prompt by the vault pages it points at. The
production path embeds the prompt and searches the warm index
(:func:`decafclaw.embeddings.search_similar_sync`). When that endpoint is
unavailable the local fastembed backend embeds a small in-memory vault corpus
instead, so the survey still runs offline.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from statistics import mean, median
from typing import Protocol

from .. import embeddings
from .corpus import page_key

log = logging.getLogger(__name__)

DEFAULT_LOCAL_MODEL = "BAAI/bge-small-en-v1.5"
_WIKI_LINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")


@dataclass(frozen=True)
class PageCase:
    """The minimal turn data page calibration needs."""

    turn_id: str
    actual_next: str
    previous_user: str
    assistant_before: str
    provenance_pages: tuple[str, ...] = ()


class PageRetriever(Protocol):
    """Map text to the top-k vault page keys it points at."""

    name: str

    async def pages_for(self, text: str) -> tuple[str, ...]: ...


class EmbeddingRetriever:
    """Production path: warm sqlite/index search through the embedding API."""

    name = "embeddings-index"

    def __init__(self, config, top_k: int = 5):
        self._config = config
        self._top_k = top_k

    async def pages_for(self, text: str) -> tuple[str, ...]:
        vector = await embeddings.embed_text(self._config, text)
        if not vector:
            raise RuntimeError("embedding provider returned no vector")
        results = embeddings.search_similar_sync(self._config, vector, top_k=self._top_k)
        keys: list[str] = []
        seen: set[str] = set()
        for result in results:
            key = page_key(result.get("file_path") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            keys.append(key)
        return tuple(keys)


class LocalVaultRetriever:
    """Offline path: embed the vault in memory with fastembed, cosine top-k."""

    def __init__(self, config, top_k: int = 5, *, model: str = DEFAULT_LOCAL_MODEL):
        self._config = config
        self._top_k = top_k
        self._model = model
        self._provider = None
        self._entries: list[tuple[str, list[float]]] | None = None
        self._lock = None
        self.name = f"local-vault/{model}"

    def _provider_instance(self):
        if self._provider is None:
            from ..llm.providers.fastembed import FastembedProvider

            self._provider = FastembedProvider()
        return self._provider

    async def _load(self) -> list[tuple[str, list[float]]]:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._entries is not None:
                return self._entries
            grouped: dict[str, list[str]] = {}
            for source_id, text, _source_type, _meta in embeddings.iter_index_entries(self._config):
                key = page_key(source_id)
                if not key or not text.strip():
                    continue
                grouped.setdefault(key, []).append(text)
            entries: list[tuple[str, str]] = [(key, "\n\n".join(texts)) for key, texts in sorted(grouped.items())]
            if not entries:
                raise RuntimeError("vault has no indexable entries for the local survey backend")
            provider = self._provider_instance()
            vectors = await provider.embed_batch(self._model, [text for _key, text in entries])
            if not vectors or len(vectors) != len(entries):
                raise RuntimeError(f"local embedding backend '{self._model}' failed to embed the vault")
            self._entries = [(key, vector) for (key, _text), vector in zip(entries, vectors)]
            log.info("Local survey vault index ready: %d entries", len(self._entries))
            return self._entries

    async def pages_for(self, text: str) -> tuple[str, ...]:
        entries = await self._load()
        provider = self._provider_instance()
        vectors = await provider.embed_batch(self._model, [text])
        if not vectors:
            return ()
        query = vectors[0]
        qnorm = math.sqrt(sum(value * value for value in query)) or 1.0
        scored: list[tuple[float, str]] = []
        for key, vector in entries:
            vnorm = math.sqrt(sum(value * value for value in vector)) or 1.0
            dot = sum(a * b for a, b in zip(query, vector))
            scored.append((dot / (qnorm * vnorm), key))
        scored.sort(reverse=True)
        seen: set[str] = set()
        keys: list[str] = []
        for _score, key in scored:
            if key in seen:
                continue
            seen.add(key)
            keys.append(key)
            if len(keys) >= self._top_k:
                break
        return tuple(keys)


async def create_retriever(
    config,
    *,
    backend: str = "auto",
    top_k: int = 5,
    local_model: str = DEFAULT_LOCAL_MODEL,
) -> PageRetriever:
    """Build the requested backend, falling back to local only in auto mode."""
    if backend not in ("auto", "embeddings", "local"):
        raise ValueError(f"unknown page backend: {backend}")
    if backend in ("auto", "embeddings"):
        try:
            vector = await embeddings.embed_text(config, "suggested next prompt calibration probe")
            if vector and embeddings.search_similar_sync(config, vector, top_k=1):
                return EmbeddingRetriever(config, top_k)
            reason = "empty embedding or index"
        except Exception as exc:  # embedding endpoints fail in many ways
            reason = f"{type(exc).__name__}: {exc}"
        if backend == "embeddings":
            raise RuntimeError(f"embedding page backend unavailable ({reason})")
        log.warning("Embedding page backend unavailable (%s); using local vault backend", reason)
    return LocalVaultRetriever(config, top_k, model=local_model)


def connected_page_keys(config) -> list[str]:
    """Rank vault pages by inbound wiki links (a static "most-connected" prior)."""
    pages: dict[str, str] = {}
    link_counts: dict[str, int] = {}
    for source_id, text, _source_type, _meta in embeddings.iter_index_entries(config):
        key = page_key(source_id)
        if key and key not in pages:
            pages[key] = text
    for key, text in pages.items():
        for match in _WIKI_LINK.finditer(text):
            target = page_key(match.group(1))
            if target and target != key and target in pages:
                link_counts[target] = link_counts.get(target, 0) + 1
    ranked = sorted(pages, key=lambda key: (-link_counts.get(key, 0), key))
    return ranked


def page_set(pages) -> frozenset[str]:
    return frozenset(key for key in (page_key(page) for page in pages) if key)


def set_jaccard(left, right) -> float:
    left_set = page_set(left)
    right_set = page_set(right)
    if not left_set and not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def jaccard_series_stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": 0.0, "median": 0.0, "p10": 0.0, "p90": 0.0}
    ordered = sorted(values)
    p10 = ordered[max(0, int(len(ordered) * 0.1) - 1)]
    p90 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]
    return {
        "n": len(values),
        "mean": round(mean(values), 4),
        "median": round(median(values), 4),
        "p10": round(p10, 4),
        "p90": round(p90, 4),
    }


async def calibrate_pages(
    cases: list[PageCase],
    retriever: PageRetriever,
    *,
    top_k: int,
    connected: list[str],
) -> tuple[dict, dict[str, dict]]:
    """Score trivial predictors against the turn's vault-direction truth.

    Returns ``(summary, details_by_turn_id)``. Ground truth is the archived
    provenance page set when present, else the pages the real next message
    retrieves (``retrieval_proxy``); the summary records which.
    """
    connected_pages = tuple(connected[:top_k])
    truth_sources = {"provenance": 0, "retrieval_proxy": 0}
    empty_truth = 0
    series: dict[str, list[float]] = {
        "reuse_previous_user": [],
        "reuse_last_assistant": [],
        "global_connected": [],
        "retrieval_of_actual": [],
    }
    details: dict[str, dict] = {}
    for case in cases:
        retrieval_of_actual = await retriever.pages_for(case.actual_next)
        if case.provenance_pages:
            truth = case.provenance_pages
            truth_source = "provenance"
        else:
            truth = retrieval_of_actual
            truth_source = "retrieval_proxy"
        truth_sources[truth_source] += 1
        truth_set = page_set(truth)
        if not truth_set:
            empty_truth += 1
            details[case.turn_id] = {
                "turn_id": case.turn_id,
                "truth_source": truth_source,
                "truth_pages": [],
            }
            continue
        previous = await retriever.pages_for(case.previous_user)
        assistant = await retriever.pages_for(case.assistant_before[-2000:])
        values = {
            "reuse_previous_user": set_jaccard(previous, truth_set),
            "reuse_last_assistant": set_jaccard(assistant, truth_set),
            "global_connected": set_jaccard(connected_pages, truth_set),
            "retrieval_of_actual": set_jaccard(retrieval_of_actual, truth_set),
        }
        for key, value in values.items():
            series[key].append(value)
        details[case.turn_id] = {
            "turn_id": case.turn_id,
            "truth_source": truth_source,
            "truth_pages": sorted(truth_set),
            "pages_retrieval_of_actual": sorted(page_set(retrieval_of_actual)),
            **{key: round(value, 4) for key, value in values.items()},
        }

    vault_pages = max(len(connected), 1)
    floor_k = min(top_k, vault_pages)
    denominator = 2 * vault_pages - floor_k
    random_floor = floor_k / denominator if denominator > 0 else 0.0
    mean_truth = (
        mean([len(detail.get("truth_pages", [])) for detail in details.values() if detail.get("truth_pages")])
        if any(detail.get("truth_pages") for detail in details.values())
        else 0.0
    )
    summary = {
        "top_k": top_k,
        "retriever": retriever.name,
        "vault_pages": vault_pages,
        "truth_sources": truth_sources,
        "empty_ground_truth": empty_truth,
        "mean_truth_pages": round(mean_truth, 3),
        "baselines": {key: jaccard_series_stats(values) for key, values in series.items()},
        "analytic_random_floor": round(random_floor, 4),
        "assistant_before_chars": 2000,
    }
    return summary, details
