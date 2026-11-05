#!/usr/bin/env bash
# ==============================================================================
# Open Maestri Linux - GTK 4 Visual & Interaction E2E Test Runner in Xvfb
# ==============================================================================
# Executes automated visual interaction and regression test suites inside a
# headless virtual framebuffer (Xvfb) for CI/CD and continuous local regression.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

echo "========================================================================"
echo "🚀 Open Maestri GTK 4 - Visual E2E Continuous Regression (Xvfb)"
echo "========================================================================"

# 1. Dependency checks
if ! command -v python3 &>/dev/null; then
    echo "❌ Error: python3 is not installed or not in PATH." >&2
    exit 1
fi

if ! command -v xvfb-run &>/dev/null; then
    echo "❌ Error: xvfb-run is not installed. Please install xvfb (e.g. sudo apt install xvfb)." >&2
    exit 1
fi

# 2. Environment configuration for headless GTK 4
export GDK_BACKEND=x11
export LIBGL_ALWAYS_SOFTWARE=1
export GTK_A11Y=none
export PYTHONPATH="${REPO_ROOT}:${REPO_ROOT}/canvas-cairo:${REPO_ROOT}/gtk4-ui/src:${PYTHONPATH:-}"

ARTIFACTS_DIR="${REPO_ROOT}/artifacts/e2e-screenshots"
mkdir -p "${ARTIFACTS_DIR}"

# 3. Test execution under virtual framebuffer (1920x1080x24)
TARGET="${1:-tests/e2e/test_gtk4_window_visual.py}"
shift || true
EXTRA_ARGS=("$@")

echo "🖥️  Running visual E2E test suite under Xvfb (1920x1080x24)..."
echo "📂 Target: ${TARGET}"
echo "🖼️  Artifacts directory: ${ARTIFACTS_DIR}"
echo "------------------------------------------------------------------------"

set +e
xvfb-run -a -s "-screen 0 1920x1080x24" python3 -m pytest "${TARGET}" -v "${EXTRA_ARGS[@]}"
TEST_STATUS=$?
set -e

echo "------------------------------------------------------------------------"
if [ ${TEST_STATUS} -eq 0 ]; then
    echo "✅ Visual E2E tests PASSED!"
    if [ -d "${ARTIFACTS_DIR}" ] && [ "$(ls -A "${ARTIFACTS_DIR}" 2>/dev/null)" ]; then
        echo "📸 Generated Visual Regression Screenshots:"
        ls -lh "${ARTIFACTS_DIR}"/*.png 2>/dev/null || true
    fi
else
    echo "❌ Visual E2E tests FAILED with exit code ${TEST_STATUS}" >&2
fi
echo "========================================================================"

exit ${TEST_STATUS}
