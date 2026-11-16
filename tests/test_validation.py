import pytest

from liquidity_reserves.validation import Checks, synthetic_checks, two_bond_checks


def test_two_bond_proposal_figures():
    checks = Checks()
    two_bond_checks(checks)
    failed = {row["check"]: row["note"] for row in checks.rows if row["status"] != "pass"}
    assert not failed
    # The 16x16 grid reaches the same $105m as adaptive refinement; the proposal
    # was corrected after an earlier draft said it left a $2.5m bracket.
    grid = next(r for r in checks.rows if r["check"] == "16x16 grid result at 0.5%")
    assert "Corrected in the proposal" in grid["note"]


@pytest.mark.slow
def test_synthetic_rows():
    checks = Checks()
    synthetic_checks(checks, quick=False)
    failed = [row["check"] for row in checks.rows if row["status"] != "pass"]
    assert not failed
