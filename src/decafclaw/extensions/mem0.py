"""Local mem0 memory extension for DecafClaw (#1454).

Reference implementation using:
- Embedded Qdrant on local disk (data/mem0_qdrant)
- FastEmbed CPU embeddings (BAAI/bge-small-en-v1.5)
- Local Otari LLM endpoint (http://synobian:4100/v1) targeting homelab models
- Generic lifecycle hooks: PRE_COMPOSE (search) and ON_TURN_SETTLE (extraction)
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.extensions import BaseExtension, register_extension_type

log = logging.getLogger(__name__)


class Mem0Extension(BaseExtension):
    """Mem0 semantic memory extension providing local-first fact extraction and recall."""

    def __init__(self, options: dict[str, Any], config: Any, client: Any = None):
        super().__init__(name="mem0")
        self.options = options or {}
        # Retained for extensions that want core config (non-memory) access.
        self.config = config
        self.client = client
        self._initialized = False

    def _build_mem0_config(self) -> dict[str, Any]:
        """Construct embedded, local-first Mem0 configuration dictionary."""
        opts = self.options
        qdrant_path = opts.get("qdrant_path", "data/mem0_qdrant")
        embedder_model = opts.get("embedder_model", "BAAI/bge-small-en-v1.5")
        llm_base_url = opts.get("llm_base_url", "http://synobian:4100/v1")
        llm_model = opts.get("llm_model", "mandelbrot3-flint")
        api_key = opts.get("api_key", "") or "local"

        return {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "path": qdrant_path,
                },
            },
            "embedder": {
                "provider": "fastembed",
                "config": {
                    "model": embedder_model,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": llm_model,
                    "openai_base_url": llm_base_url,
                    "api_key": api_key,
                },
            },
        }

    def _init_client(self) -> Any:
        """Initialize local Mem0 Memory client."""
        if self.client is not None:
            return self.client
        try:
            from mem0 import Memory  # pyright: ignore[reportMissingImports]

            mem0_config = self._build_mem0_config()
            self.client = Memory.from_config(mem0_config)
            self._initialized = True
            log.info("Initialized local Mem0 memory store at %s", mem0_config["vector_store"]["config"]["path"])
            return self.client
        except ImportError:
            log.warning("mem0 package is not installed; running in fallback mode")
            return None
        except Exception as e:
            log.warning("Failed to initialize mem0 local store: %s", e)
            return None

    def enable(self, bus: EventBus) -> list[str]:
        """Enable extension and register lifecycle hooks."""
        self.disable(bus)
        self._init_client()
        sub_pre = bus.subscribe(TurnLifecycle.PRE_COMPOSE, self.pre_compose_hook)
        sub_settle = bus.subscribe(TurnLifecycle.ON_TURN_SETTLE, self.on_turn_settle_hook)
        self.subscription_ids = [sub_pre, sub_settle]
        return self.subscription_ids

    async def pre_compose_hook(self, ctx: Any, user_message: str, history: list | None = None) -> str | None:
        """Search memory store for relevant facts prior to composing the turn."""
        if getattr(ctx, "skip_memory_extension", False):
            return None
        if not self.client:
            return None
        try:
            user_id = getattr(ctx, "user_id", "") or "default"
            top_k = int(self.options.get("top_k", 5))
            # Call search on mem0 client (handles sync or async)
            search_fn = getattr(self.client, "search", None)
            if not callable(search_fn):
                return None
            res = search_fn(user_message, user_id=user_id, limit=top_k)
            if inspect.iscoroutine(res):
                res = await res
            if not res:
                return None

            facts: list[str] = []
            if isinstance(res, list):
                for item in res:
                    if isinstance(item, dict):
                        mem = item.get("memory") or item.get("text") or item.get("content")
                        if mem:
                            facts.append(str(mem))
                    elif isinstance(item, str):
                        facts.append(item)
            elif isinstance(res, dict):
                entries = res.get("results") or res.get("memories") or []
                for item in entries:
                    if isinstance(item, dict):
                        mem = item.get("memory") or item.get("text") or item.get("content")
                        if mem:
                            facts.append(str(mem))
                    elif isinstance(item, str):
                        facts.append(item)

            return "\n".join(facts) if facts else None
        except Exception as e:
            log.warning("Mem0 pre_compose_hook search failed: %s", e)
            return None

    async def on_turn_settle_hook(self, ctx: Any, transcript: list) -> None:
        """Extract and persist facts from the settled turn transcript slice."""
        if getattr(ctx, "skip_memory_extension", False):
            return
        if not self.client:
            return
        try:
            user_id = getattr(ctx, "user_id", "") or "default"
            messages: list[dict[str, str]] = []
            for m in transcript:
                role = m.get("role")
                content = m.get("content")
                if role in ("user", "assistant") and content and isinstance(content, str):
                    messages.append({"role": role, "content": content})
            if not messages:
                return

            add_fn = getattr(self.client, "add", None)
            if not callable(add_fn):
                return
            res = add_fn(messages, user_id=user_id)
            if inspect.iscoroutine(res):
                await res
        except Exception as e:
            log.warning("Mem0 on_turn_settle_hook add failed: %s", e)


register_extension_type("mem0", Mem0Extension)
