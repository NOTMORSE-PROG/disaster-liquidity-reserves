#!/usr/bin/env python3
"""evaluation-v1 entry point: python worker.py --request request.json --output result.json

The organizer's evaluator runs this in a fresh process with a minimal
environment, so the package and the vendored qjlb source are put on the path
here instead of relying on PYTHONPATH.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for path in (ROOT / "benchmark" / "src", ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from liquidity_reserves.worker import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
