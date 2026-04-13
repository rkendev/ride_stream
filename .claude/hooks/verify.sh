#!/bin/bash
# Enhanced verification hook for RideStream v2
# Runs before every commit to catch rule violations early
# Exit non-zero on any failure

set -e

echo "========================================"
echo "RideStream v2 Pre-Commit Verification"
echo "========================================"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

FAILED=0

# 1. Ruff: Format and lint
echo ""
echo "1. Ruff format & lint check..."
if ruff check src/ tests/ --fix > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Ruff passed${NC}"
else
    echo -e "${RED}✗ Ruff failed${NC}"
    ruff check src/ tests/
    FAILED=1
fi

# 2. MyPy: Strict type checking
echo ""
echo "2. MyPy strict type checking..."
if mypy --strict src/ > /dev/null 2>&1; then
    echo -e "${GREEN}✓ MyPy passed${NC}"
else
    echo -e "${RED}✗ MyPy failed${NC}"
    mypy --strict src/
    FAILED=1
fi

# 3. Bandit: Security audit
echo ""
echo "3. Bandit security audit..."
if bandit -r src/ -ll 2>/dev/null | grep -q "No issues"; then
    echo -e "${GREEN}✓ Bandit passed (no HIGH severity issues)${NC}"
else
    echo -e "${RED}✗ Bandit found security issues${NC}"
    bandit -r src/ -ll
    FAILED=1
fi

# 4. Unit tests
echo ""
echo "4. Unit tests..."
if pytest tests/unit/ -q > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Unit tests passed${NC}"
else
    echo -e "${RED}✗ Unit tests failed${NC}"
    pytest tests/unit/ -v
    FAILED=1
fi

# 5. NEW: Check for f-string SQL patterns (Rule 3)
echo ""
echo "5. Checking for f-string SQL patterns (Rule 3)..."
f_string_sql=$(grep -r 'f".*SELECT\|f".*INSERT\|f".*UPDATE\|f".*DELETE' src/ --include="*.py" || true)
if [ -n "$f_string_sql" ]; then
    echo -e "${RED}✗ Found f-string SQL patterns (Rule 3 violation):${NC}"
    echo "$f_string_sql"
    FAILED=1
else
    echo -e "${GREEN}✓ No f-string SQL patterns found${NC}"
fi

# 6. NEW: Check for bare `except Exception` (Rule 7)
echo ""
echo "6. Checking for bare exception handlers (Rule 7)..."
bare_except=$(grep -r 'except Exception:\|except:' src/ --include="*.py" | grep -v '#' || true)
if [ -n "$bare_except" ]; then
    echo -e "${RED}✗ Found bare exception handlers (Rule 7 violation):${NC}"
    echo "$bare_except"
    FAILED=1
else
    echo -e "${GREEN}✓ No bare exception handlers found${NC}"
fi

# 7. NEW: Check for hardcoded AWS credentials (Rule 8)
echo ""
echo "7. Checking for hardcoded secrets (Rule 8)..."
hardcoded_secrets=$(grep -r 'password.*=\|api_key.*=\|secret.*=\|token.*=' src/ --include="*.py" | \
    grep -v 'Secrets\|Environment\|os.getenv\|env_var\|#' || true)
if [ -n "$hardcoded_secrets" ]; then
    echo -e "${RED}✗ Found potential hardcoded secrets (Rule 8 violation):${NC}"
    echo "$hardcoded_secrets"
    FAILED=1
else
    echo -e "${GREEN}✓ No hardcoded secrets found${NC}"
fi

# 8. Check for domain layer importing external libraries (Rule 6 domain.md)
echo ""
echo "8. Checking domain layer imports (Rule 6)..."
domain_imports=$(grep -r '^import \|^from ' src/domain/*.py 2>/dev/null | \
    grep -v 'pydantic\|datetime\|hashlib\|math\|enum\|typing\|dataclass\|#' || true)
if [ -n "$domain_imports" ]; then
    echo -e "${YELLOW}⚠ Domain layer imports (verify manually):${NC}"
    echo "$domain_imports"
    # Don't fail, just warn
fi

# 9. Check for adapter layer importing from adapters (Rule in application.md)
echo ""
echo "9. Checking application layer imports..."
app_adapter_imports=$(grep -r 'from src.adapters\.' src/application/ --include="*.py" 2>/dev/null || true)
if [ -n "$app_adapter_imports" ]; then
    echo -e "${RED}✗ Application layer imports from adapters (violation):${NC}"
    echo "$app_adapter_imports"
    FAILED=1
else
    echo -e "${GREEN}✓ Application layer correctly depends on ports only${NC}"
fi

# 10. Check test coverage (if .coverage exists)
echo ""
echo "10. Test coverage check..."
if [ -f ".coverage" ]; then
    coverage_report=$(coverage report --fail-under=85 2>&1 || true)
    if echo "$coverage_report" | grep -q "FAILED"; then
        echo -e "${RED}✗ Test coverage below 85%${NC}"
        coverage report
        FAILED=1
    else
        echo -e "${GREEN}✓ Test coverage >= 85%${NC}"
    fi
else
    echo -e "${YELLOW}⚠ No .coverage file (run tests first)${NC}"
fi

# Summary
echo ""
echo "========================================"
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All verification checks passed!${NC}"
    echo "========================================"
    exit 0
else
    echo -e "${RED}✗ Verification failed! Fix errors above before committing.${NC}"
    echo "========================================"
    exit 1
fi
