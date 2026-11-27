#!/usr/bin/env bash
# Shared backup wrapper for decafclaw.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/decafclaw}"
DATA_HOME="${DATA_HOME:-$HOME/.decafclaw/decafclaw}"
# Default to the off-disk NFS mount, NOT $DATA_HOME/backups. That in-home path
# was a hard NFS mount inside the walked data tree; defaulting to it silently
# re-creates the hazard for any caller that does not set BACKUP_DIR.
BACKUP_DIR="${BACKUP_DIR:-/mnt/decafclaw-backups}"

exec /usr/bin/python3 "$REPO_DIR/scripts/backup.py" \
    --data-home "$DATA_HOME" \
    --repo-dir "$REPO_DIR" \
    --backup-dir "$BACKUP_DIR" \
    --force
