"""Locate the vendored organizer benchmark and the recorded results."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_MARKER = Path("data") / "adb_terms.json"


def project_root() -> Path:
    """Repository root when running from a source checkout."""
    return Path(__file__).resolve().parents[2]


def benchmark_dir() -> Path:
    """The organizer's benchmark folder (override with DLR_BENCHMARK_DIR)."""
    override = os.environ.get("DLR_BENCHMARK_DIR")
    if override:
        path = Path(override).resolve()
        if not (path / _MARKER).is_file():
            raise FileNotFoundError(f"DLR_BENCHMARK_DIR has no {_MARKER}: {path}")
        return path
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "benchmark"
        if (candidate / _MARKER).is_file():
            return candidate
    raise FileNotFoundError(
        "Cannot find the ADB benchmark folder. Run from the repository or set DLR_BENCHMARK_DIR."
    )


def results_dir() -> Path:
    return Path(os.environ.get("DLR_RESULTS_DIR", project_root() / "results"))


def ensure_qjlb():
    """Import the organizer's unmodified qjlb package, adding benchmark/src if needed."""
    try:
        import qjlb  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(benchmark_dir() / "src"))
    import qjlb

    return qjlb
