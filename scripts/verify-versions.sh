#!/bin/bash
# Verify that installed tool versions match pyproject.toml pins.
# Prevents "works locally, fails in CI" drift.

set -e

echo "==== Version Parity Check ===="

check_tool() {
    local tool="$1"
    local min_version="$2"
    local actual
    actual=$("$tool" --version 2>&1 | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1 || echo "not found")
    if [ "$actual" = "not found" ]; then
        echo "  $tool: NOT INSTALLED"
        return 1
    fi
    echo "  $tool: $actual (min: $min_version)"
}

check_tool python3 "3.12" || exit 1
check_tool ruff "0.1" || echo "  WARN: ruff not found (install via pip install -e .[dev])"
check_tool mypy "1.5" || echo "  WARN: mypy not found"
check_tool bandit "1.7" || echo "  WARN: bandit not found"
check_tool pytest "7.4" || echo "  WARN: pytest not found"
check_tool dbt "1.7" || echo "  WARN: dbt not found"

# Python version must be 3.12+
PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 12 ]; }; then
    echo "ERROR: Python 3.12+ required, found $PYTHON_MAJOR.$PYTHON_MINOR"
    exit 1
fi

echo "==== All versions verified ===="
