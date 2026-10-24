#!/bin/bash
set -euo pipefail
ENGINE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ENGINE_DIR"
PYTHON_VERSION="$(<"$ENGINE_DIR/.python-version")"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ENGINE_DIR/.cache/uv}"
UV_BIN="${PAGEBRIDGE_UV:-$ENGINE_DIR/.tools/bin/uv}"
if [[ ! -x "$UV_BIN" ]]; then
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
  else
    BOOTSTRAP_PYTHON="${PAGEBRIDGE_BOOTSTRAP_PYTHON:-python3}"
    "$BOOTSTRAP_PYTHON" -m pip install --target "$ENGINE_DIR/.tools" uv==0.9.17
  fi
fi
"$UV_BIN" sync --frozen --managed-python --python "$PYTHON_VERSION"
INSTALLED_PYTHON_VERSION="$("$ENGINE_DIR/.venv/bin/python" -c 'import platform; print(platform.python_version())')"
if [[ "$INSTALLED_PYTHON_VERSION" != "$PYTHON_VERSION" ]]; then
  echo "Expected Python $PYTHON_VERSION, found $INSTALLED_PYTHON_VERSION." >&2
  exit 1
fi
"$UV_BIN" pip check --python "$ENGINE_DIR/.venv/bin/python"
"$ENGINE_DIR/.venv/bin/python" "$ENGINE_DIR/runtime_info.py" > "$ENGINE_DIR/runtime-info.json"
cat "$ENGINE_DIR/runtime-info.json"
if [[ "${1:-}" == "--assets" ]]; then
  "$ENGINE_DIR/.venv/bin/babeldoc" --warmup
fi
