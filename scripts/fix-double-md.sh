#!/bin/bash
# Fix double .md.md extensions in vault files.
# Usage: ./scripts/fix-double-md.sh /path/to/vault
#
# Dry run by default. Pass --apply to actually rename files.

set -euo pipefail

VAULT="${1:?Usage: $0 /path/to/vault [--apply]}"
APPLY="${2:-}"

if [ ! -d "$VAULT" ]; then
    echo "Error: $VAULT is not a directory"
    exit 1
fi

count=0
while IFS= read -r -d '' bad_file; do
    # Strip the extra .md: Foo.md.md -> Foo.md
    good_file="${bad_file%.md}"
    count=$((count + 1))

    if [ -f "$good_file" ]; then
        echo "[CONFLICT] $bad_file -> $good_file already exists (skipping)"
    elif [ "$APPLY" = "--apply" ]; then
        mv "$bad_file" "$good_file"
        echo "[RENAMED] $bad_file -> $good_file"
    else
        echo "[DRY RUN] $bad_file -> $good_file"
    fi
done < <(find "$VAULT" -name '*.md.md' -print0)

if [ "$count" -eq 0 ]; then
    echo "No .md.md files found."
else
    echo ""
    echo "Found $count file(s) with double .md extension."
    if [ "$APPLY" != "--apply" ]; then
        echo "Run with --apply to rename them."
    fi
fi
