#!/usr/bin/env python3
"""Publish a unit failure to ntfy (#failure-notifications).

Invoked as ``ExecStart`` of ``decafclaw-notify-failure@.service``, which is wired
to the backup and deploy units via ``OnFailure=``. Fires when a unit enters the
``failed`` state, which is exactly the case that used to be silent: a bounded
failure now exists (see the watchdog in ``deploy_runner.py`` and
``TimeoutStartSec`` on the backup unit), but nothing announced it.

Design constraints, all deliberate:

* **Standalone, standard library only.** Like ``deploy_runner.py``, this must run
  when the application's environment is broken -- a failing deploy is exactly when
  the venv may be mid-``uv sync``. System Python, no imports from ``decafclaw``.
* **Reuses the app's ntfy configuration** instead of a second copy of the
  credential. It reads ``config.json`` for the channel the agent already uses, so
  rotating that token does not need a second change here.
* **Fail-open.** A notifier that raises turns one failure into two. Every error
  path logs and exits 0, except a missing configuration, which exits 1 so the
  unit itself shows as failed rather than silently doing nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".decafclaw" / "decafclaw" / "config.json"

# ntfy priority for a failed unit. 4 is "high" -- it buzzes a phone without the
# override-DND behaviour of 5 ("urgent"), which is reserved for things that need
# an answer right now.
PRIORITY = 4


def _load_ntfy_config(config_path: Path) -> dict:
    """Return the enabled ntfy channel config, or raise."""
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"no config at {config_path}") from exc
    except (OSError, ValueError) as exc:
        raise SystemExit(f"unreadable config at {config_path}: {exc}") from exc

    channel = ((raw.get("notifications") or {}).get("channels") or {}).get("ntfy") or {}
    if not channel.get("enabled"):
        raise SystemExit("ntfy channel is not enabled; nothing to publish to")
    for field in ("server_url", "topic", "access_token"):
        if not channel.get(field):
            raise SystemExit(f"ntfy channel is missing {field}")
    return channel


def _publish_url(server_url: str) -> str:
    """ntfy's JSON API wants the server root, not ``{server}/{topic}``.

    Posting JSON to ``/topic`` makes ntfy skip JSON parsing and treat the body as
    literal message text, which renders as a JSON blob with no title or priority.
    The topic goes in the body instead.
    """
    return server_url.rstrip("/") + "/"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish a unit failure to ntfy.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--unit", required=True, help="unit that failed")
    # %i in an instantiated unit is the escaped unit name; passing it in keeps this
    # script from having to guess.
    parser.add_argument("--detail", default="", help="extra context for the body")
    args = parser.parse_args(argv)

    channel = _load_ntfy_config(Path(args.config).expanduser())

    title = f"Failed: {args.unit}"
    body = args.detail or f"{args.unit} entered the failed state on decafclaw."
    payload = json.dumps(
        {
            "topic": channel["topic"],
            "title": title,
            "message": body,
            "priority": PRIORITY,
            "tags": ["rotating_light"],
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        _publish_url(channel["server_url"]),
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {channel['access_token']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        print(f"ntfy rejected the notification: {exc.code} {exc.reason}", file=sys.stderr)
        return 0  # fail-open: never turn one failure into two
    except (OSError, ValueError) as exc:
        print(f"could not reach ntfy: {exc}", file=sys.stderr)
        return 0

    print(f"published unit failure for {args.unit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
