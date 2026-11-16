import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "benchmark" / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from liquidity_reserves.examples import public_two_bond, synthetic_chain  # noqa: E402


@pytest.fixture(scope="session")
def root():
    return ROOT


@pytest.fixture(scope="session")
def two_bond():
    return public_two_bond()


@pytest.fixture(scope="session")
def chain5():
    return synthetic_chain(5)
