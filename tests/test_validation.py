import pytest

from liquidity_reserves.validation import Checks, synthetic_checks, two_bond_checks


def test_two_bond_proposal_figures():
    checks = Checks()
    two_bond_checks(checks)
    statuses = {row["check"]: row["status"] for row in checks.rows}
    # The proposal says the 16x16 grid left a $2.5m bracket at 0.5%; the grid
    # actually resolves to $105m. That discrepancy is recorded, not hidden.
    assert statuses.pop("16x16 grid bracket width at 0.5%") == "discrepancy"
    assert set(statuses.values()) == {"pass"}, statuses


@pytest.mark.slow
def test_synthetic_rows():
    checks = Checks()
    synthetic_checks(checks, quick=False)
    failed = [row["check"] for row in checks.rows if row["status"] != "pass"]
    assert not failed
