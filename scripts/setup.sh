#!/bin/bash
set -e

echo "==== RideStream v2 Local Setup ===="

# Verify Python version
python3 --version | grep -q "3.12\|3.13" || {
    echo "ERROR: Python 3.12+ required"
    exit 1
}

# Install project in editable mode with dev dependencies
echo "Installing dependencies..."
pip install -e ".[dev]"

# Install pre-commit hooks
echo "Installing pre-commit hooks..."
pre-commit install || echo "WARN: pre-commit install failed (non-fatal)"

# Verify key tools
echo "Verifying tools..."
ruff --version
mypy --version
pytest --version

echo ""
echo "Setup complete. Next steps:"
echo "  1. Copy .env.example to .env.local and fill in values"
echo "  2. Run 'make docker-up' to start the Docker stack"
echo "  3. Run 'make smoke-test' to verify infrastructure"
