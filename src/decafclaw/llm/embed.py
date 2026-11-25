"""Shared embedding HTTP transport.

The three embedding call sites (``vertex``, ``openai_compat``, and the legacy
fallback in ``embeddings.py``) share one shape: POST a JSON body, retry 429s
with exponential backoff, and fail open (return ``None``) on any error. This
module holds that shared transport; each provider supplies its URL, body,
headers source, and response extraction.

Providers import this as a public name (#1319).
"""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .retry import MAX_RETRIES

log = logging.getLogger(__name__)

#: A header dict, or a zero-arg callable (sync or async) that returns one.
#: Async sources cover ADC token refresh; sync sources cover static keys.
HeadersSource = dict[str, str] | Callable[[], "dict[str, str] | Awaitable[dict[str, str]]"]


async def _resolve_headers(headers: HeadersSource) -> dict[str, str]:
    """Resolve a headers source to a concrete header dict.

    A plain dict is copied; a callable is invoked (and awaited when it returns
    an awaitable) so sync and async sources both work. Callers resolve per
    attempt so a token is refreshed on every try.
    """
    if not callable(headers):
        return dict(headers)
    # ``Any``: pyright cannot narrow sync-vs-async through inspect.isawaitable.
    result: Any = headers()
    if inspect.isawaitable(result):
        result = await result
    return dict(result)


async def post_embedding(
    *,
    url: str,
    body: dict,
    headers: HeadersSource,
    extract: Callable[[dict], list[float]],
    timeout: int = 30,
    max_retries: int = MAX_RETRIES,
    log_label: str = "Embedding",
) -> list[float] | None:
    """POST an embedding request and return the vector, or ``None`` on failure.

    Retries 429 responses with exponential backoff (1s, 2s, 4s) up to
    ``max_retries`` retries. Any other failure — a non-429 HTTP error, a network
    error, or a response ``extract`` cannot parse — is logged and returns
    ``None`` so callers fail open. The caller owns the request inputs (url/body/
    headers); this only owns transport, retry, and fail-open.
    """
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    json=body,
                    headers=await _resolve_headers(headers),
                    timeout=timeout,
                )
            if resp.status_code == 429 and attempt < max_retries:
                delay = 2**attempt
                log.warning(
                    "%s rate limited, retrying in %ds (attempt %d/%d)",
                    log_label,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return extract(resp.json())
        except httpx.HTTPStatusError as e:
            log.error("%s failed: %s", log_label, e)
            return None
        except Exception as e:
            log.error("%s failed: %s", log_label, e)
            return None
    return None
