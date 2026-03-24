#!/usr/bin/env bash
set -euo pipefail

# Download mastodon-to-markdown binaries for all supported platforms

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="${SCRIPT_DIR}/bin"
REPO="lmorchard/mastodon-to-markdown"
TOOL="mastodon-to-markdown"

PLATFORMS=("darwin-arm64" "darwin-amd64" "linux-amd64")

for PLATFORM in "${PLATFORMS[@]}"; do
    PLATFORM_DIR="${BIN_DIR}/${PLATFORM}"
    mkdir -p "$PLATFORM_DIR"

    URL="https://github.com/${REPO}/releases/download/latest/${TOOL}-${PLATFORM}.tar.gz"
    echo "Downloading ${TOOL} for ${PLATFORM}..."

    TMPFILE=$(mktemp /tmp/${TOOL}-XXXXXX.tar.gz)
    trap "rm -f $TMPFILE" EXIT

    if curl -L -f -o "$TMPFILE" "$URL"; then
        # Try extracting the named file first, fall back to extracting everything
        if ! tar -xzf "$TMPFILE" -C "$PLATFORM_DIR" "$TOOL" 2>/dev/null; then
            tar -xzf "$TMPFILE" -C "$PLATFORM_DIR"
            # Find and move the binary if it extracted into a subdirectory
            FOUND=$(find "$PLATFORM_DIR" -name "$TOOL" -type f | head -1)
            if [ -n "$FOUND" ] && [ "$FOUND" != "${PLATFORM_DIR}/${TOOL}" ]; then
                mv "$FOUND" "${PLATFORM_DIR}/${TOOL}"
            fi
        fi
        chmod +x "${PLATFORM_DIR}/${TOOL}"
        echo "  ✓ ${PLATFORM}"
    else
        echo "  ✗ ${PLATFORM} (download failed)"
    fi

    rm -f "$TMPFILE"
done

echo "Done."
