#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY="${SCRIPT_DIR}/fetch_feeds.py"

if ! command -v uv >/dev/null 2>&1; then
    echo "[error: uv not found — required to run rss-ingest]" >&2
    exit 1
fi

# Feed-management subcommands pass straight through.
case "${1:-}" in
    list|add|remove)
        exec uv run --no-project "$PY" "$@"
        ;;
esac

# Fetch mode. With no args → auto (advances state). Any args (--since/--start/
# --end) → backfill re-scan that does NOT advance state.
if [ "$#" -gt 0 ]; then
    exec uv run --no-project "$PY" fetch "$@"
fi
exec uv run --no-project "$PY" fetch
