#!/bin/bash
set -euo pipefail
PAGEBRIDGE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PAGEBRIDGE_ROOT"
if [[ ! -x engine/.venv/bin/python || ! -f engine/runtime-info.json ]]; then
  bash engine/setup.sh
fi
export CLANG_MODULE_CACHE_PATH="${CLANG_MODULE_CACHE_PATH:-/tmp/pagebridge-clang-cache}"
export SWIFT_MODULECACHE_PATH="${SWIFT_MODULECACHE_PATH:-/tmp/pagebridge-swift-cache}"
swift build --package-path macOS --cache-path /tmp/pagebridge-spm-cache --disable-sandbox -c release
engine/.venv/bin/python scripts/assemble-app.py
printf 'Built: %s/dist/PageBridge.app\n' "$PAGEBRIDGE_ROOT"
