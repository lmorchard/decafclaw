"""Diagnostics and telemetry core extension (#1488).

Writes the Context Inspector's ``last_request.json`` sidecar and records turn
token counters for Prometheus. Both jobs observe the turn loop through
lifecycle hooks only:

- ``BEFORE_LLM_CALL`` captures a frozen snapshot of the request payload the
  core loop is about to send (model, messages, tools, and composer sources).
  Synchronous and allocation-only — no I/O in the turn path.
- ``ON_TURN_SETTLE`` writes the latest snapshot for the conversation and folds
  the turn's token totals into the in-memory metrics registry. The hook is
  dispatched by the core loop as an unawaited background task.

Fail-open everywhere: a capture, write, or metric failure logs a warning and
never raises into the turn. Disabling the extension (or the ``metrics_enabled``
knob for the counters) leaves the core loop running with no sidecar writes.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import logging
from typing import Any, cast

from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.metrics import record_metric

from . import BaseExtension, register_extension_type

log = logging.getLogger(__name__)


def _conv_id(ctx: Any) -> str:
    return str(getattr(ctx, "conv_id", "") or getattr(ctx, "channel_id", "") or "")


def _resolve_target_model(ctx: Any, config: Any) -> str:
    """Return the model the core loop will actually use.

    Mirrors ``agent._resolve_model_override``: a named ``ctx.active_model``
    wins when it resolves, otherwise the effective default. The legacy
    ``llm_url``/``llm_model`` overrides are not used by the turn loop.
    """
    model_name = getattr(ctx, "active_model", "") or None
    try:
        from decafclaw.llm import _resolve

        return _resolve(config, model_name=model_name).model
    except Exception:
        from decafclaw.model_catalog import effective_default_model

        return model_name or effective_default_model(config) or getattr(config.llm, "model", "")


def _snapshot_sources(ctx: Any) -> list[dict[str, Any]] | None:
    """Serialize the composer's per-source diagnostics, when the turn composed."""
    sources = getattr(getattr(ctx, "composer", None), "last_sources", None)
    if not sources:
        return None
    serialized: list[dict[str, Any]] = []
    for entry in sources:
        if dataclasses.is_dataclass(entry):
            serialized.append(dataclasses.asdict(cast(Any, entry)))
        else:  # pragma: no cover - composer always produces SourceEntry
            serialized.append(dict(entry))
    return serialized


def _write_snapshot(config: Any, conv_id: str, snapshot: dict[str, Any]) -> None:
    """Write one conversation's last request payload. Raises on failure."""
    from decafclaw.conversation_paths import last_request_path

    path = last_request_path(config, conv_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")


class DiagnosticsAndTelemetryExtension(BaseExtension):
    """Observe the turn loop for the Context Inspector and Prometheus metrics."""

    def __init__(self, options: dict[str, Any], config: Any):
        super().__init__(name="diagnostics_and_telemetry")
        self.options = options or {}
        self.config = config
        # conv_id -> frozen snapshot captured by the latest BEFORE_LLM_CALL.
        # Turns are serialized per conversation, so one entry is enough; it is
        # replaced on every call and consumed by the turn's settle hook.
        self._snapshots: dict[str, dict[str, Any]] = {}

    def enable(self, bus: EventBus) -> list[str]:
        self.disable(bus)
        self.subscription_ids = [
            bus.subscribe(TurnLifecycle.BEFORE_LLM_CALL, self.on_before_llm_call),
            bus.subscribe(TurnLifecycle.ON_TURN_SETTLE, self.on_turn_settle_hook),
        ]
        return self.subscription_ids

    def disable(self, bus: EventBus) -> None:
        super().disable(bus)
        self._snapshots.clear()

    def on_before_llm_call(self, ctx: Any, messages: list[dict], tools: list[dict]) -> None:
        """Capture the outgoing request payload; never mutate it."""
        conv_id = _conv_id(ctx)
        if not conv_id:
            return
        try:
            # Deep copy: the loop appends tool results to the same message list
            # after the call, and the snapshot must stay what the provider saw.
            snapshot: dict[str, Any] = {
                "model": _resolve_target_model(ctx, self.config),
                "messages": copy.deepcopy(messages),
                "tools": copy.deepcopy(tools),
            }
            sources = _snapshot_sources(ctx)
            if sources is not None:
                snapshot["sources"] = sources
            self._snapshots[conv_id] = snapshot
        except Exception as exc:  # fail-open observer
            log.warning("Diagnostics snapshot capture failed for %s: %s", conv_id, exc)

    async def on_turn_settle_hook(self, ctx: Any, _transcript: list[dict] | None = None) -> None:
        """Persist the turn's last request and token counters. Fail-open."""
        conv_id = _conv_id(ctx)
        if not conv_id:
            return
        snapshot = self._snapshots.pop(conv_id, None)
        if snapshot is not None:
            try:
                # File I/O off the event loop; the core dispatches this hook as
                # an unawaited task so it never delays turn settlement.
                await asyncio.to_thread(_write_snapshot, self.config, conv_id, snapshot)
            except Exception as exc:  # fail-open observer
                log.warning("Failed to write last request sidecar for %s: %s", conv_id, exc)
        self._record_token_metrics(ctx, snapshot)

    def _record_token_metrics(self, ctx: Any, snapshot: dict[str, Any] | None) -> None:
        try:
            if not getattr(getattr(self.config, "telemetry", None), "metrics_enabled", True):
                return
            tokens = getattr(ctx, "tokens", None)
            if tokens is None:
                return
            model = snapshot.get("model") if snapshot else _resolve_target_model(ctx, self.config)
            if not model:
                return
            counters = {
                "prompt": getattr(tokens, "total_prompt", 0),
                "cached": getattr(tokens, "total_cached_prompt", 0),
                "completion": getattr(tokens, "total_completion", 0),
            }
            for kind, value in counters.items():
                if value:
                    record_metric("llm_tokens_total", {"model": model, "kind": kind}, value, "counter")
        except Exception as exc:  # fail-open observer
            log.warning("Failed to record token metrics: %s", exc)


register_extension_type("diagnostics_and_telemetry", DiagnosticsAndTelemetryExtension)
