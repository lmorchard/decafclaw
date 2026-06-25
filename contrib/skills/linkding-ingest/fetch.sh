#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Platform detection
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case $ARCH in x86_64) ARCH="amd64" ;; aarch64|arm64) ARCH="arm64" ;; esac
PLATFORM="${OS}-${ARCH}"

BIN="${SCRIPT_DIR}/bin/${PLATFORM}/linkding-to-markdown"
if [ ! -x "$BIN" ]; then
    echo "Error: binary not found at ${BIN}" >&2
    exit 1
fi

# Check required env vars
: "${LINKDING_URL:?LINKDING_URL env var is required}"
: "${LINKDING_TOKEN:?LINKDING_TOKEN env var is required}"

# Backfill mode: any args from the caller are forwarded directly to the
# binary, bypassing the last-run-based incremental fetch. Last-run
# tracking is also skipped so a backfill doesn't clobber the timestamp
# the scheduled cycle relies on.
#
# Examples:
#   fetch.sh                              # auto: since last run (or 1 day)
#   fetch.sh --days 30                    # last 30 days, ad-hoc
#   fetch.sh --since 2026-04-01           # since a specific date
#   fetch.sh --since 2026-04-01 --until 2026-04-30
#   fetch.sh --query golang               # filtered fetch
if [ "$#" -gt 0 ]; then
    exec "$BIN" fetch \
        --url "${LINKDING_URL}" \
        --token "${LINKDING_TOKEN}" \
        "$@"
fi

# No args → auto-fetch since the last successful run.
# State lives in the runtime workspace, NOT the git checkout. The shell tool
# sets DECAFCLAW_WORKSPACE (and runs with cwd = the workspace); fall back to
# the current directory if it's somehow unset.
WORKSPACE_DIR="${DECAFCLAW_WORKSPACE:-$PWD}"
LAST_RUN_DIR="${WORKSPACE_DIR}/skill-state/linkding-ingest"
mkdir -p "$LAST_RUN_DIR"
LAST_RUN_FILE="${LAST_RUN_DIR}/last-run-time.txt"

# Determine date range from last run, or default to 1 day
if [ -f "$LAST_RUN_FILE" ]; then
    # .last_run contains an ISO 8601 date (YYYY-MM-DDTHH:MM:SSZ)
    SINCE_DATE=$(cat "$LAST_RUN_FILE" | cut -dT -f1)
    DATE_ARG="--since $SINCE_DATE"
else
    DATE_ARG="--days 1"
fi

# Fetch bookmarks
"$BIN" fetch \
    --url "${LINKDING_URL}" \
    --token "${LINKDING_TOKEN}" \
    $DATE_ARG

# Update last-run timestamp on success (ISO 8601)
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$LAST_RUN_FILE"
