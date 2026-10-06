#!/usr/bin/env bash
# Compatibility entry point for callers using the former retained-workspace publisher.
# Fetch the released artifacts by VERSION/BUILD_ID; do not trust mutable SDK outputs.
set -euo pipefail
: "${1:?usage: ota-publish-release.sh <sdk-dir> <channel>}"
channel="${2:?usage: ota-publish-release.sh <sdk-dir> <channel>}"
exec python3 "$(dirname "$0")/ota-publish.py" \
  --tag "${VERSION:?VERSION is required}" \
  --build-id "${BUILD_ID:?BUILD_ID is required}" --channel "$channel"
