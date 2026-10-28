"""Ephemeral scripted test server entry point (#957).

Runs inside a child process started by ``scripts/ephemeral_instance.py``
against the checkout under test::

    uv run --project <checkout> python -m decafclaw.testing.ephemeral_server \
        --responses <file.json>

It loads a scripted-response file in the #923 format, registers the scripted
provider under the fixed ``PROVIDER_SCRIPTED`` name *after* ``init_providers``
(in the ``start_app`` extra-provider hook), and starts the server through the
same startup function ``decafclaw.main()`` uses. Production ``config.json``
can never select the scripted provider — only this entry point registers it,
in this process.

The response file is a JSON array of response objects, one per scripted
LLM call (see the scripted provider section of ``docs/testing.md``). Each
object may use these keys (all optional): ``content``, ``thinking``,
``tool_calls``, ``usage``, ``finish_reason``, ``error`` (string). Streaming
chunking and hold barriers are in-memory only and cannot be expressed in a
file.

Diagnostic log lines (INFO) for the launcher and its tests — values are
never logged, only names:

- ``ephemeral server: data home: <path>`` — the ``DATA_HOME`` actually used
- ``ephemeral server: registered LLM providers: [...]`` — the full provider
  registry after startup (must be ``["scripted"]`` for an isolated run)
- ``ephemeral server: environment variable names: [...]`` — proves the child
  never imported a checkout ``.env`` with foreign variables
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from ..llm.registry import list_providers
from .scripted_provider import (
    PROVIDER_SCRIPTED,
    ScriptedResponse,
    register_scripted_provider,
)

log = logging.getLogger(__name__)

_ALLOWED_RESPONSE_KEYS = ("content", "thinking", "tool_calls", "usage", "finish_reason", "error")


def load_responses_file(path: Path) -> list[ScriptedResponse]:
    """Parse a scripted-response file into ``ScriptedResponse`` objects."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read responses file {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"responses file {path} must contain a JSON array of response objects")

    responses: list[ScriptedResponse] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"responses file {path}: entry {i} is not a JSON object")
        unknown = set(entry) - set(_ALLOWED_RESPONSE_KEYS)
        if unknown:
            raise ValueError(
                f"responses file {path}: entry {i} has unknown keys {sorted(unknown)}; "
                f"allowed: {list(_ALLOWED_RESPONSE_KEYS)}"
            )
        responses.append(
            ScriptedResponse(
                content=entry.get("content"),
                thinking=entry.get("thinking"),
                tool_calls=entry.get("tool_calls"),
                usage=entry.get("usage"),
                finish_reason=entry.get("finish_reason"),
                error=entry.get("error"),
            )
        )
    return responses


def _make_hook(responses: list[ScriptedResponse]):
    """Build the extra-provider hook for ``start_app``.

    Runs after ``init_providers(config)``: registers the scripted provider
    under the fixed ``PROVIDER_SCRIPTED`` name and logs the diagnostics the
    launcher relies on.
    """

    def hook(config: Any) -> None:
        register_scripted_provider(PROVIDER_SCRIPTED, responses=responses)
        log.info("ephemeral server: registered LLM providers: %s", list_providers())
        log.info("ephemeral server: data home: %s", config.agent_path)
        log.info("ephemeral server: environment variable names: %s", sorted(os.environ))

    return hook


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decafclaw.testing.ephemeral_server",
        description=(
            "Ephemeral scripted test server (#957): starts the decafclaw server "
            "with only the scripted LLM provider registered. Launched by "
            "scripts/ephemeral_instance.py; not a user-facing command."
        ),
    )
    parser.add_argument(
        "--responses",
        required=True,
        help="JSON file of scripted responses (docs/testing.md, scripted provider).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    responses_path = Path(args.responses)
    try:
        responses = load_responses_file(responses_path)
    except ValueError as exc:
        print(f"ephemeral server: {exc}", file=sys.stderr)
        return 1
    log.info("ephemeral server: loaded %d scripted response(s) from %s", len(responses), responses_path)

    # Lazy import so tests can patch decafclaw.start_app to prove both entry
    # points share the same startup function (#957).
    from decafclaw import start_app

    start_app(extra_provider_hook=_make_hook(responses))
    return 0


if __name__ == "__main__":
    sys.exit(main())
