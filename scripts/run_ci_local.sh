#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH="."

PY_BIN=".venv/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="python3"
fi

echo "=== Disklavier-Auto Local Verification Suite ==="
echo "Python: $PY_BIN ($($PY_BIN --version))"

RUFF_BIN=".venv/bin/ruff"
if [[ ! -x "$RUFF_BIN" ]]; then
  RUFF_BIN="$(command -v ruff || true)"
fi

if [[ -n "$RUFF_BIN" ]]; then
  echo "--- [1/3] Ruff lint check ---"
  "$RUFF_BIN" check src tests validate_system_integrity.py
else
  echo "--- [1/3] Ruff not installed; skipping lint ---"
fi

echo "--- [2/3] Running test suites ---"
if "$PY_BIN" -m pytest --version >/dev/null 2>&1; then
  "$PY_BIN" -m pytest tests/
else
  for test_file in tests/test_*.py; do
    echo "Running $test_file ..."
    "$PY_BIN" "$test_file"
  done
fi

echo "--- [3/3] Running system integrity validator ---"
"$PY_BIN" validate_system_integrity.py

echo "=== Local Verification Suite PASSED ==="
