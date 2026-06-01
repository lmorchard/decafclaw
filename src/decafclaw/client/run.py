"""Orchestration for the decafclaw client: drive turns, emit output, exit codes.

`drive_turn` is transport-agnostic (any object with `events()`), which is what
makes the loop unit-testable with a fake transport.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from collections.abc import Callable

from .cli import SmokeArgs, parse_args
from .recorder import TurnRecorder, TurnSummary
from .transport import TransportError, WSTransport

_EXIT_BY_STATUS = {
    "complete": 0,
    "error": 1,
    "halted_confirmation": 2,
    "timeout": 3,
}


async def drive_turn(transport, recorder: TurnRecorder, *, timeout: float,
                     sink: Callable[[dict], None] | None) -> str:
    """Consume events until a stop condition. Returns the stop reason:
    "turn_complete" | "confirmation" | "timeout" | "disconnect".
    """
    try:
        async with asyncio.timeout(timeout):
            async for event in transport.events():
                if sink is not None:
                    sink(event)
                recorder.record(event)
                etype = event.get("type")
                if etype == "confirm_request":
                    return "confirmation"
                if etype == "turn_complete":
                    return "turn_complete"
    except TimeoutError:
        return "timeout"
    return "disconnect"


def _sink_for(fmt: str) -> Callable[[dict], None] | None:
    if fmt == "jsonl":
        return lambda ev: print(json.dumps(ev), flush=True)
    return None


async def _select(transport, conv_id: str) -> None:
    await transport.send({"type": "select_conv", "conv_id": conv_id})


async def run_send(transport, args: SmokeArgs) -> list[TurnSummary]:
    conv_id = args.conv or await transport.create_conversation()
    await _select(transport, conv_id)
    if args.model:
        await transport.send({"type": "set_model", "conv_id": conv_id,
                              "model": args.model})
    sink = _sink_for(args.fmt)
    summaries: list[TurnSummary] = []
    for prompt in args.prompts or []:
        recorder = TurnRecorder(conv_id)
        await transport.send({"type": "send", "conv_id": conv_id, "text": prompt})
        reason = await drive_turn(transport, recorder, timeout=args.timeout,
                                  sink=sink)
        summaries.append(recorder.finalize(reason))
        if reason != "turn_complete":
            # Only a completed turn continues the sequence. A halt, timeout, or
            # disconnect stops it — in particular, sending the next prompt after
            # a disconnect would hit a closed socket and raise an uncaught
            # ConnectionClosed past _amain's TransportError handler.
            break
    return summaries


async def run_respond(transport, args: SmokeArgs) -> list[TurnSummary]:
    conv_id = args.conv or ""
    await _select(transport, conv_id)
    recorder = TurnRecorder(conv_id)
    await transport.send({
        "type": "confirm_response", "conv_id": conv_id,
        "confirmation_id": args.confirmation_id or "",
        "approved": args.approved, "always": False, "add_pattern": False,
    })
    reason = await drive_turn(transport, recorder, timeout=args.timeout,
                              sink=_sink_for(args.fmt))
    return [recorder.finalize(reason)]


def exit_code_for(statuses: list[str]) -> int:
    for status in statuses:
        if status != "complete":
            return _EXIT_BY_STATUS.get(status, 1)
    return 0


def emit(summaries: list[TurnSummary], fmt: str) -> None:
    if fmt == "jsonl":
        return  # events already streamed by the sink
    payload = [dataclasses.asdict(s) for s in summaries]
    if not payload:
        return
    print(json.dumps(payload[0] if len(payload) == 1 else payload, indent=2))


async def _amain(args: SmokeArgs) -> int:
    transport = WSTransport(args.host, args.token)
    try:
        await transport.connect()
        if args.action == "send":
            summaries = await run_send(transport, args)
        else:
            summaries = await run_respond(transport, args)
    except TransportError as exc:
        print(json.dumps({"status": "error", "errors": [str(exc)]}),
              file=sys.stderr)
        return 4
    finally:
        await transport.close()
    emit(summaries, args.fmt)
    return exit_code_for([s.status for s in summaries])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_amain(args))
