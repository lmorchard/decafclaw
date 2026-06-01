"""Argument parsing for the decafclaw client CLI. Pure: no network, no event loop."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass


@dataclass
class SmokeArgs:
    action: str  # "send" | "respond"
    token: str
    host: str
    timeout: float
    fmt: str  # "summary" | "jsonl"
    conv: str | None = None
    model: str | None = None
    prompts: list[str] | None = None
    confirmation_id: str | None = None
    approved: bool = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decafclaw-client",
        description="Drive a conversation in a running decafclaw instance over "
                    "the /ws/chat WebSocket gateway and emit machine-readable "
                    "results for smoke testing.",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--token", default=None,
                       help="Web token (or env DECAFCLAW_TOKEN).")
        p.add_argument("--host", default=None,
                       help="Base URL (or env DECAFCLAW_HOST; "
                            "default http://localhost:8088).")
        p.add_argument("--timeout", type=float, default=180.0,
                       help="Per-turn timeout in seconds (default 180).")
        p.add_argument("--format", dest="fmt", choices=("summary", "jsonl"),
                       default="summary", help="Output format (default summary).")

    p_send = sub.add_parser("send", help="Send prompt(s) and record the turn(s).")
    add_common(p_send)
    p_send.add_argument("--conv", default=None,
                        help="Existing conversation id; omit to create a new one.")
    p_send.add_argument("--model", default=None,
                        help="Set the conversation model before sending.")
    p_send.add_argument("--prompt", action="append", default=[],
                        help="Prompt text. Repeatable; runs sequentially.")
    p_send.add_argument("--script", default=None,
                        help="File of prompts, one per line (blank lines skipped).")

    p_resp = sub.add_parser("respond", help="Respond to a pending confirmation.")
    add_common(p_resp)
    p_resp.add_argument("--conv", required=True, help="Conversation id.")
    p_resp.add_argument("--confirmation-id", dest="confirmation_id", required=True,
                        help="Confirmation id to respond to (copy it from the "
                             "halted send's `confirmations` output).")
    decision = p_resp.add_mutually_exclusive_group()
    decision.add_argument("--approve", dest="approved", action="store_true",
                          default=True, help="Approve (default).")
    decision.add_argument("--deny", dest="approved", action="store_false",
                          help="Deny.")

    return parser


def parse_args(argv: list[str] | None = None) -> SmokeArgs:
    parser = build_parser()
    ns = parser.parse_args(argv)

    token = ns.token or os.environ.get("DECAFCLAW_TOKEN", "")
    if not token:
        parser.error("a web token is required (--token or DECAFCLAW_TOKEN)")
    host = ns.host or os.environ.get("DECAFCLAW_HOST", "http://localhost:8088")

    if ns.action == "send":
        prompts = list(ns.prompt)
        if ns.script:
            with open(ns.script, encoding="utf-8") as fh:
                prompts.extend(line.strip() for line in fh if line.strip())
        if not prompts:
            parser.error("send requires at least one --prompt or --script")
        return SmokeArgs(
            action="send", token=token, host=host, timeout=ns.timeout,
            fmt=ns.fmt, conv=ns.conv, model=ns.model, prompts=prompts,
        )

    return SmokeArgs(
        action="respond", token=token, host=host, timeout=ns.timeout,
        fmt=ns.fmt, conv=ns.conv, confirmation_id=ns.confirmation_id,
        approved=ns.approved,
    )
