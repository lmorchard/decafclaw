"""Stateless OpenAI Responses adapter with ordered reasoning-item replay."""

import asyncio
import inspect
import json
import logging
from copy import deepcopy
from typing import Any

import httpx
import httpx_sse

from ..types import StreamCallback
from .openai import OpenAIProvider
from .openai_compat import _MAX_RETRIES, _cancellable_sleep, _RetryableError

log = logging.getLogger(__name__)


def _visible_output(output: list[dict]) -> tuple[str | None, list[dict] | None]:
    text, calls = [], []
    for item in output:
        if item["type"] == "message":
            for part in item.get("content", []):
                if part["type"] == "output_text":
                    text.append(part["text"])
                elif part["type"] == "refusal":
                    text.append(part["refusal"])
        elif item["type"] == "function_call":
            calls.append(
                {
                    "id": item["call_id"],
                    "type": "function",
                    "function": {
                        "name": item["name"],
                        "arguments": item["arguments"],
                    },
                }
            )
        elif item["type"] != "reasoning":
            raise ValueError(f"Unsupported Responses output item: {item['type']}")
    return "".join(text) or None, calls or None


class OpenAIResponsesProvider(OpenAIProvider):
    """Uses local history, never a mutable server-side conversation chain.

    Inherits authentication and embedding transport only. Completion requests
    and events have their own wire format and parser.
    """

    def __init__(self, api_key: str = "", url: str = "", name: str = ""):
        super().__init__(api_key=api_key, url=url or "https://api.openai.com/v1/responses")
        self.name = name

    def _responses_url(self) -> str:
        url = self.url.rstrip("/")
        if url.endswith("/responses"):
            return url
        if url.endswith("/chat/completions"):
            raise ValueError("openai-responses URL must be a base URL or /responses endpoint")
        return url + ("/responses" if url.endswith("/v1") else "/v1/responses")

    def _embeddings_url(self) -> str:
        return self._responses_url().removesuffix("/responses") + "/embeddings"

    def _provenance(self, model: str) -> dict[str, str]:
        return {"provider": self.name, "url": self._responses_url(), "model": model}

    def _input(self, model: str, messages: list[dict]) -> list[dict]:
        items = []
        for message in messages:
            role, content = message["role"], message.get("content")
            if role == "assistant":
                data = (message.get("provider_data") or {}).get("openai_responses", {})
                output = data.get("output")
                # Visible history is authoritative, including edits made after
                # archival. Foreign models/connections see normalized history.
                if output and all(data.get(key) == value for key, value in self._provenance(model).items()):
                    text, calls = _visible_output(output)
                    if (text or "") == (content or "") and (calls or []) == (message.get("tool_calls") or []):
                        items.extend(deepcopy(output))
                        continue
                if content:
                    items.append({"role": role, "content": content})
                for call in message.get("tool_calls") or []:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": call["id"],
                            "name": call["function"]["name"],
                            "arguments": call["function"]["arguments"],
                        }
                    )
            elif role == "tool":
                items.append(
                    {"type": "function_call_output", "call_id": message["tool_call_id"], "output": content or ""}
                )
            elif role in ("system", "developer", "user"):
                if isinstance(content, list):
                    parts = []
                    for part in content:
                        if part["type"] == "text":
                            parts.append({"type": "input_text", "text": part["text"]})
                        elif part["type"] == "image_url":
                            image = part["image_url"]
                            parts.append(
                                {
                                    "type": "input_image",
                                    "image_url": image["url"],
                                    "detail": image.get("detail", "auto"),
                                }
                            )
                        else:
                            raise ValueError(f"Unsupported Responses input content: {part['type']}")
                    content = parts
                items.append({"role": role, "content": content or ""})
            else:
                raise ValueError(f"Unsupported Responses input role: {role}")
        return items

    async def complete(
        self,
        model: str,
        messages: list[dict],
        *,
        tools: list | None = None,
        streaming: bool = False,
        on_chunk: StreamCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        timeout: int = 300,
        reasoning_effort: str | None = None,
        max_output_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "input": self._input(model, messages),
            "store": False,
            # Explicit inclusion also supports deployments that do not return
            # encrypted reasoning automatically in stateless mode.
            "include": ["reasoning.encrypted_content"],
        }
        if reasoning_effort is not None:
            body["reasoning"] = {"effort": reasoning_effort}
        if max_output_tokens is not None:
            body["max_output_tokens"] = max_output_tokens
        if tools:
            body["tools"] = [
                {"type": "function", **deepcopy(tool["function"]), "strict": tool["function"].get("strict", False)}
                for tool in tools
            ]
        if streaming:
            body["stream"] = True
        state = _ResponsesState(self._provenance(model), on_chunk)
        if cancel_event and cancel_event.is_set():
            state.finish_reason = "cancelled"
        else:
            request = asyncio.create_task(self._request(body, timeout, streaming, state, cancel_event))
            watcher = asyncio.create_task(cancel_event.wait()) if cancel_event else None
            try:
                if watcher:
                    done, _ = await asyncio.wait([request, watcher], return_when=asyncio.FIRST_COMPLETED)
                    if watcher in done:
                        state.finish_reason = "cancelled"
                        request.cancel()
                        await asyncio.gather(request, return_exceptions=True)
                    else:
                        await request
                else:
                    await request
            finally:
                request.cancel()
                if watcher:
                    watcher.cancel()
                await asyncio.gather(request, *([watcher] if watcher else []), return_exceptions=True)
        return await state.finalize()

    async def _request(self, body: dict, timeout: int, streaming: bool, state: "_ResponsesState", cancel_event):
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient() as client:
                    if streaming:
                        async with httpx_sse.aconnect_sse(
                            client,
                            "POST",
                            self._responses_url(),
                            json=body,
                            headers=self._headers(),
                            timeout=httpx.Timeout(timeout),
                        ) as source:
                            await self._check_response(source.response)
                            async for event in source.aiter_sse():
                                await state.process(json.loads(event.data))
                                if state.terminal:
                                    break
                    else:
                        response = await client.post(
                            self._responses_url(), json=body, headers=self._headers(), timeout=timeout
                        )
                        await self._check_response(response)
                        state.accept_response(response.json())
                return
            except _RetryableError as exc:
                if attempt == _MAX_RETRIES:
                    raise RuntimeError(f"LLM failed after {_MAX_RETRIES} retries (status {exc.status})") from exc
                await _cancellable_sleep(min(int(exc.retry_after or 2**attempt), 30), cancel_event)
            except Exception as exc:
                if not state.has_output:
                    raise
                log.debug("Responses stream interrupted; partial output kept: %s", exc)
                state.finish_reason = "error"
                return

    @staticmethod
    async def _check_response(response: httpx.Response):
        if response.status_code >= 400:
            body = (await response.aread()).decode(errors="replace")[:500]
            if response.status_code == 429 or response.status_code >= 500:
                raise _RetryableError(response.status_code, body, response.headers.get("retry-after"))
            raise RuntimeError(f"LLM API error ({response.status_code}): {body}")


class _ResponsesState:
    def __init__(self, provenance: dict, on_chunk: StreamCallback | None):
        self.provenance, self.on_chunk = provenance, on_chunk
        self.items: dict[int, dict] = {}
        self.item_indices: dict[str, int] = {}
        self.text: list[str] = []
        self.response: dict | None = None
        self.finish_reason = "error"  # EOF is not successful completion.
        self.terminal = False
        self.started: set[int] = set()

    @property
    def has_output(self) -> bool:
        return bool(self.items or self.text)

    async def emit(self, kind: str, data: Any):
        if self.on_chunk:
            result = self.on_chunk(kind, data)
            if inspect.isawaitable(result):
                await result

    def accept_response(self, response: dict):
        self.response = response
        self.items = dict(enumerate(response.get("output", [])))
        status = response.get("status")
        self.terminal = True
        if status == "completed":
            self.finish_reason = (
                "tool_calls" if any(item["type"] == "function_call" for item in self.items.values()) else "stop"
            )
        elif status == "incomplete" and (response.get("incomplete_details") or {}).get("reason") == "max_output_tokens":
            self.finish_reason = "length"
        elif status == "cancelled":
            self.finish_reason = "cancelled"
        else:
            self.finish_reason = "error"
        if status == "failed" and not self.has_output and response.get("error"):
            raise RuntimeError(f"LLM Responses error: {response['error'].get('message', 'request failed')}")

    async def process(self, event: dict):
        kind = event["type"]
        index: int = event.get("output_index", 0)
        if kind in ("response.output_item.added", "response.output_item.done"):
            item = deepcopy(event["item"])
            self.items[index] = item
            if item.get("id"):
                self.item_indices[item["id"]] = index
            if item["type"] == "function_call" and index not in self.started:
                self.started.add(index)
                await self.emit("tool_call_start", {"index": index, "name": item["name"]})
        elif kind in ("response.output_text.delta", "response.refusal.delta"):
            self.text.append(event["delta"])
            await self.emit("text", event["delta"])
        elif kind == "response.function_call_arguments.delta":
            index = self.item_indices.get(event.get("item_id", ""), index)
            item = self.items[index]
            item["arguments"] = item.get("arguments", "") + event["delta"]
            await self.emit("tool_call_delta", {"index": index, "arguments_delta": event["delta"]})
        elif kind == "response.function_call_arguments.done":
            index = self.item_indices.get(event.get("item_id", ""), index)
            self.items[index]["arguments"] = event["arguments"]
        elif kind in ("response.completed", "response.incomplete", "response.failed"):
            self.accept_response(event["response"])
        elif kind == "error":
            self.finish_reason, self.terminal = "error", True
            raise RuntimeError(f"LLM Responses error: {event.get('message', 'stream failed')}")
        # Other lifecycle/content/reasoning-summary events do not change the
        # existing callback contract. Their complete items carry replay state.

    async def finalize(self) -> dict:
        output = [self.items[index] for index in sorted(self.items)]
        content, calls = _visible_output(output)
        content = content or "".join(self.text) or None
        usage = None
        if self.response and self.response.get("usage"):
            raw = self.response["usage"]
            usage = {
                "prompt_tokens": raw.get("input_tokens", 0),
                "completion_tokens": raw.get("output_tokens", 0),
                "cached_tokens": (raw.get("input_tokens_details") or {}).get("cached_tokens", 0),
                "reasoning_tokens": (raw.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
            }
        for index, item in sorted(self.items.items()):
            if item["type"] == "function_call":
                await self.emit(
                    "tool_call_end", {"index": index, "name": item["name"], "arguments": item.get("arguments", "")}
                )
        await self.emit("done", {"usage": usage})
        result = {
            "role": "assistant",
            "content": content,
            "tool_calls": calls,
            "usage": usage,
            "finish_reason": self.finish_reason,
        }
        if self.finish_reason in ("stop", "tool_calls"):
            result["provider_data"] = {"openai_responses": {**self.provenance, "output": deepcopy(output)}}
        return result
