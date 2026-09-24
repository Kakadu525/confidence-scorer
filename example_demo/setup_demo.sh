#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-/tmp/confidence-scorer-demo}"
rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"
cd "$TARGET_DIR"

git init -q
git config user.email "demo@example.com"
git config user.name "Confidence Scorer Demo"

cat > pricing.py <<'EOF'
def apply_discount(price: float, percent: float) -> float:
    """Return the price after a percentage discount (0-100)."""
    if percent < 0 or percent > 100:
        raise ValueError("percent must be between 0 and 100")
    return price - (price * percent / 100)
EOF

cat > textutils.py <<'EOF'
def normalize_whitespace(text: str) -> str:
    """Collapse any run of whitespace characters into a single space."""
    return " ".join(text.split())
EOF

git add -A
git commit -q -m "initial: pricing + textutils"
git branch -q -m main

cat > pricing.py <<'EOF'
def apply_discount(price: float, percent: float) -> float:
    """Return the price after a percentage discount (0-100)."""
    if percent > 100:
        raise ValueError("percent must be between 0 and 100")
    return price - (price * percent / 100)
EOF
git add -A
git commit -q -m "ai: simplify apply_discount validation"
git tag bug-commit

cat > textutils.py <<'EOF'
import re


def normalize_whitespace(text: str) -> str:
    """Collapse any run of whitespace characters into a single space."""
    return re.sub(r"\s+", " ", text).strip()
EOF
git add -A
git commit -q -m "ai: rewrite normalize_whitespace using regex"
git tag safe-commit

echo "Demo repository ready: $TARGET_DIR"
echo
echo "Bug (should fail the property test and get a low score):"
echo "  cd $TARGET_DIR && confidence-score run --base bug-commit~1 --head bug-commit"
echo
echo "Safe refactoring (should pass the property test):"
echo "  cd $TARGET_DIR && confidence-score run --base safe-commit~1 --head safe-commit"
