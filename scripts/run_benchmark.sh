#!/usr/bin/env bash
# Run the organizer's unmodified evaluation-v1 evaluator on the public
# scenarios and on seeded practice cases, with this repository's worker.
#
# Linux or macOS only (the evaluator requires POSIX). PYTHON must point to an
# interpreter where numpy, scipy and the vendored qjlb package are installed
# (`pip install -r requirements.txt ./benchmark`): the evaluator starts its
# baseline workers with a minimal environment and no PYTHONPATH.
#
# Usage: scripts/run_benchmark.sh [output-dir]
# Environment: PYTHON, REPEATS (default 10), SAMPLES (8192), TIMEOUT (60),
#              PRACTICE_SEED (20261113), SUITE_SEED (20261113)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
PYTHON="$(command -v "$PYTHON")"
REPEATS="${REPEATS:-10}"
SAMPLES="${SAMPLES:-8192}"
TIMEOUT="${TIMEOUT:-60}"
PRACTICE_SEED="${PRACTICE_SEED:-20261113}"
SUITE_SEED="${SUITE_SEED:-20261113}"
OUT="${1:-$ROOT/runs/benchmark-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

"$PYTHON" -c "import numpy, scipy, qjlb" || {
  echo "numpy, scipy and qjlb must be importable by $PYTHON" >&2
  exit 2
}

echo "Verifying vendored benchmark checksums"
(cd "$ROOT/benchmark" && sha256sum --check --ignore-missing --quiet SHA256SUMS)

"$PYTHON" "$ROOT/scripts/make_public_suite.py" --output "$OUT/public-suite" --seed "$SUITE_SEED"
"$PYTHON" -m qjlb.cli holdout --seed "$PRACTICE_SEED" --data "$ROOT/benchmark/data" \
  --output "$OUT/practice-suite"

status=0
for suite in public practice; do
  echo "Evaluating $suite suite ($REPEATS repeats)"
  "$PYTHON" -m qjlb.cli evaluate "$OUT/$suite-suite" --output "$OUT/$suite-report" \
    --execution classical_cpu --trusted-code --repeats "$REPEATS" --samples "$SAMPLES" \
    --timeout "$TIMEOUT" -- "$PYTHON" "$ROOT/worker.py" || status=$?
done

"$PYTHON" "$ROOT/scripts/summarize_benchmark.py" "$OUT" --output "$OUT/summary.json"
echo "Reports: $OUT"
exit "$status"
