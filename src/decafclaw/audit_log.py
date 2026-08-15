import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable
from logging.handlers import RotatingFileHandler

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def infer_outcome(result_text: str) -> str:
    text = (result_text or "").lstrip()
    if text.startswith("[cancelled"):
        return "cancelled"
    if text.startswith("[error"):
        return "error"
    return "success"


def sanitize_args(args: dict) -> dict:
    if not isinstance(args, dict):
        return args
    sanitized = {}
    for k, v in args.items():
        k_lower = k.lower()
        if any(sec in k_lower for sec in ("password", "token", "secret", "key", "auth")):
            sanitized[k] = "***REDACTED***"
        elif isinstance(v, str) and len(v) > 200:
            sanitized[k] = v[:200] + "... [truncated]"
        elif isinstance(v, dict):
            sanitized[k] = sanitize_args(v)
        elif isinstance(v, list):
            sanitized[k] = [sanitize_args(item) if isinstance(item, dict) else item for item in v]
        else:
            sanitized[k] = v
    return sanitized


class AuditLogSubscriber:
    def __init__(self, config):
        self.config = config
        path = config.workspace_path / config.audit_log.path
        path.parent.mkdir(parents=True, exist_ok=True)
        
        self.logger = logging.getLogger(f"decafclaw.audit_log.writer.{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        
        handler = RotatingFileHandler(
            path,
            maxBytes=config.audit_log.max_size_bytes,
            backupCount=config.audit_log.max_backups,
            encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(handler)

    def append_record(self, record: dict) -> None:
        try:
            record["timestamp"] = _now_iso()
            self.logger.info(json.dumps(record))
        except Exception as exc:
            log.debug("audit log write failed: %s", exc)

    async def handle_event(self, event: dict) -> None:
        try:
            event_type = event.get("type")
            
            if event_type == "llm_end":
                usage = event.get("usage", {})
                self.append_record({
                    "event": "llm_call",
                    "model": event.get("model", ""),
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "duration_ms": event.get("duration_ms", 0),
                    "streaming": event.get("streaming", False),
                })
            elif event_type == "tool_end":
                result_text = event.get("result_text", "") or ""
                raw_args = event.get("args", {})
                sanitized_args = sanitize_args(raw_args)
                try:
                    args_str = json.dumps(sanitized_args)
                except Exception:
                    args_str = str(sanitized_args)
                    
                self.append_record({
                    "event": "tool_call",
                    "tool_name": event.get("tool", ""),
                    "args": args_str,
                    "result_length": len(result_text.encode("utf-8")),
                    "duration_ms": event.get("duration_ms", 0),
                    "outcome": infer_outcome(result_text),
                })
            elif event_type == "skill_activated":
                self.append_record({
                    "event": "skill_activated",
                    "identifier": event.get("skill", ""),
                })
            elif event_type == "mcp_server_connected":
                self.append_record({
                    "event": "mcp_server_connected",
                    "identifier": event.get("server", ""),
                })
        except Exception as exc:
            log.debug("audit log subscriber error: %s", exc)


def make_audit_log_subscriber(config) -> Callable[[dict], Awaitable[None]]:
    subscriber = AuditLogSubscriber(config)
    return subscriber.handle_event
