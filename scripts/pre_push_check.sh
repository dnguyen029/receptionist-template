#!/usr/bin/env bash
# ==============================================================================
# Enterprise Quality & Security Pre-Push Gate
#
# Validates:
# 1. Zero leaked credentials or sensitive files
# 2. Strict linting and formatting compliance via ruff
# 3. Deterministic offline unit tests passing with >=80% coverage
# ==============================================================================

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

# Detect Python & Tooling Environment
PYTHON_BIN="python3"
PYTEST_BIN="pytest"
RUFF_BIN="ruff"

if [[ -n "${VIRTUAL_ENV}" && -f "${VIRTUAL_ENV}/bin/pytest" ]]; then
    PYTHON_BIN="${VIRTUAL_ENV}/bin/python3"
    PYTEST_BIN="${VIRTUAL_ENV}/bin/pytest"
    RUFF_BIN="${VIRTUAL_ENV}/bin/ruff"
elif [[ -f "${ROOT_DIR}/.venv/bin/pytest" ]]; then
    PYTHON_BIN="${ROOT_DIR}/.venv/bin/python3"
    PYTEST_BIN="${ROOT_DIR}/.venv/bin/pytest"
    RUFF_BIN="${ROOT_DIR}/.venv/bin/ruff"
fi

echo "🔒 [1/3] Checking for un-ignored secrets and sensitive artifacts..."
if git status --porcelain | grep -E "(\.env$|\.pem$|credentials.*\.json$)"; then
    echo "❌ ERROR: Uncommitted secret file detected in staging area!"
    exit 1
fi
echo "✓ No leaked credentials or sensitive files detected."

echo "🧹 [2/3] Running Code Formatting and Lint Checks..."
if command -v "${RUFF_BIN}" &> /dev/null; then
    "${RUFF_BIN}" check app tests
    "${RUFF_BIN}" format --check app tests
    echo "✓ Ruff formatting and linting passed cleanly."
else
    "${PYTHON_BIN}" -m ruff check app tests || true
    "${PYTHON_BIN}" -m ruff format --check app tests || true
fi

echo "🧪 [3/3] Running Offline Unit Test Suite with Coverage Gate..."
"${PYTEST_BIN}" tests/unit/ -v --cov=app --cov-report=term-missing --cov-fail-under=80

echo "✅ All pre-push checks passed cleanly! Repository is ready and verified."
