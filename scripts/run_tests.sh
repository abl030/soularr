#!/usr/bin/env bash
# Run full test suite, save output, print summary.
# Usage: nix-shell --run "bash scripts/run_tests.sh"
set -euo pipefail

OUT="/tmp/soularr-test-output.txt"
python3 -m unittest discover tests -v 2>&1 | tee "$OUT"

echo ""
echo "=== SUMMARY ==="
echo "Output saved to: $OUT"
echo ""
# Show failures/errors only
grep -E "^(ERROR|FAIL):" "$OUT" || echo "No failures."
echo ""
tail -3 "$OUT"
