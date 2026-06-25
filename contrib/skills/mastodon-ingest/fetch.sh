#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Platform detection
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case $ARCH in x86_64) ARCH="amd64" ;; aarch64|arm64) ARCH="arm64" ;; esac
PLATFORM="${OS}-${ARCH}"

BIN="${SCRIPT_DIR}/bin/${PLATFORM}/mastodon-to-markdown"
if [ ! -x "$BIN" ]; then
    echo "Error: binary not found at ${BIN}" >&2
    exit 1
fi

# Check required env vars
: "${MASTODON_SERVER:?MASTODON_SERVER env var is required}"
: "${MASTODON_ACCESS_TOKEN:?MASTODON_ACCESS_TOKEN env var is required}"

# Backfill mode: any args from the caller are forwarded directly to the
# binary, bypassing the last-run-based incremental fetch. Last-run
# tracking is also skipped so a backfill doesn't clobber the timestamp
# the scheduled cycle relies on. `--exclude-boosts` / `--exclude-replies`
# defaults are NOT applied in this mode — pass them explicitly if you
# want them.
#
# Examples:
#   fetch.sh                              # auto: since last run (or 24h)
#   fetch.sh --since 7d                   # last week, ad-hoc
#   fetch.sh --start 2026-04-01 --end 2026-04-30
#   fetch.sh --since 7d --exclude-boosts --exclude-replies
if [ "$#" -gt 0 ]; then
    exec "$BIN" fetch \
        --server "${MASTODON_SERVER}" \
        --token "${MASTODON_ACCESS_TOKEN}" \
        "$@"
fi

# No args → auto-fetch since the last successful run.
# State lives in the runtime workspace, NOT the git checkout. The shell tool
# sets DECAFCLAW_WORKSPACE (and runs with cwd = the workspace); fall back to
# the current directory if it's somehow unset.
WORKSPACE_DIR="${DECAFCLAW_WORKSPACE:-$PWD}"
LAST_RUN_DIR="${WORKSPACE_DIR}/skill-state/mastodon-ingest"
mkdir -p "$LAST_RUN_DIR"
LAST_RUN_FILE="${LAST_RUN_DIR}/last-run-time.txt"

# Determine start date from last run, or default to 24h
if [ -f "$LAST_RUN_FILE" ]; then
    # .last_run contains an ISO 8601 date (YYYY-MM-DDTHH:MM:SSZ)
    START_DATE=$(cat "$LAST_RUN_FILE" | cut -dT -f1)
    DATE_ARG="--start $START_DATE"
else
    DATE_ARG="--since 24h"
fi

# Fetch posts
"$BIN" fetch \
    --server "${MASTODON_SERVER}" \
    --token "${MASTODON_ACCESS_TOKEN}" \
    --exclude-boosts \
    --exclude-replies \
    $DATE_ARG

# Update last-run timestamp on success (ISO 8601)
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$LAST_RUN_FILE"
