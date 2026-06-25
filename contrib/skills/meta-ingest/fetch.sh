#!/usr/bin/env bash
set -euo pipefail

# me-to-markdown is a separately-installed orchestrator (NOT a per-platform
# binary bundled with this skill — it manages its own sub-tool binaries).
# It is expected on PATH. See https://github.com/lmorchard/me-to-markdown
if ! command -v me-to-markdown >/dev/null 2>&1; then
    echo "Error: me-to-markdown not found on PATH." >&2
    echo "Install it, then run 'me-to-markdown install' and 'me-to-markdown auth'." >&2
    exit 1
fi

# State + per-source export output live in the runtime workspace, NOT the
# git checkout. The shell tool sets DECAFCLAW_WORKSPACE (and runs with cwd =
# the workspace); fall back to the current directory if it's somehow unset.
WORKSPACE_DIR="${DECAFCLAW_WORKSPACE:-$PWD}"
STATE_DIR="${WORKSPACE_DIR}/skill-state/meta-ingest"
EXPORT_DIR="${STATE_DIR}/export"
LAST_RUN_FILE="${STATE_DIR}/last-run-time.txt"
mkdir -p "$EXPORT_DIR"

# Fresh export dir each run so stale per-source files from a prior cycle
# don't get re-ingested.
rm -f "${EXPORT_DIR}"/*.md 2>/dev/null || true

# Backfill mode: any args are forwarded directly to `me-to-markdown export`,
# bypassing the last-run-based incremental window. The last-run timestamp is
# NOT advanced so a backfill doesn't clobber the scheduled-cycle state. We
# always supply --output-dir ourselves; pass --since / --until / --include /
# --exclude as args.
#
# Examples:
#   fetch.sh                                       # auto: since last run (or 24h)
#   fetch.sh --since 7d                            # last week, ad-hoc
#   fetch.sh --since 2026-04-01 --until 2026-04-30
#   fetch.sh --since 168h --include mastodon,linkding
#
# Available flags (forwarded): --since <YYYY-MM-DD|Go duration>, --until,
# --include <slugs>, --exclude <slugs>, --omit-errors. See
# `me-to-markdown export --help` for the full list.
set +e
if [ "$#" -gt 0 ]; then
    me-to-markdown export --output-dir "$EXPORT_DIR" "$@"
    RC=$?
    ADVANCE_TIMESTAMP=0
else
    # No args → auto window since the last successful run, default 24h first run.
    if [ -f "$LAST_RUN_FILE" ]; then
        SINCE_ARG="$(cut -dT -f1 < "$LAST_RUN_FILE")"
    else
        SINCE_ARG="24h"
    fi
    me-to-markdown export --output-dir "$EXPORT_DIR" --since "$SINCE_ARG"
    RC=$?
    ADVANCE_TIMESTAMP=1
fi
set -e

# me-to-markdown returns non-zero if ANY source failed (its output still
# contains an error section for that source). Only advance the incremental
# timestamp on a fully-clean run, so a transient single-source failure makes
# the next cycle retry the same window rather than silently skipping it.
if [ "$RC" -ne 0 ]; then
    echo "WARNING: me-to-markdown export exited ${RC} — one or more sources failed" >&2
    echo "(their files contain an error section). Timestamp not advanced." >&2
elif [ "$ADVANCE_TIMESTAMP" -eq 1 ]; then
    date -u +"%Y-%m-%dT%H:%M:%SZ" > "$LAST_RUN_FILE"
fi

# Report a manifest of per-source files as workspace-relative paths (for
# workspace_read) plus byte sizes. Content is NOT emitted here — each source
# is handed to its own child agent, which reads its file directly, so source
# text never enters the parent's context.
REL_EXPORT="skill-state/meta-ingest/export"
shopt -s nullglob
FILES=("${EXPORT_DIR}"/*.md)
if [ "${#FILES[@]}" -eq 0 ]; then
    echo "No source files produced."
else
    echo "Per-source files (workspace-relative paths for workspace_read):"
    for f in "${FILES[@]}"; do
        slug="$(basename "$f" .md)"
        bytes="$(wc -c < "$f" | tr -d ' ')"
        printf '  %-12s %s  (%s bytes)\n' "$slug" "${REL_EXPORT}/${slug}.md" "$bytes"
    done
fi

# Propagate me-to-markdown's status so the caller/scheduler can detect a
# partial failure (a non-zero RC means at least one source errored; its file,
# if any, holds an error section). The manifest above is printed regardless so
# the agent can still process the sources that did succeed.
exit "$RC"
