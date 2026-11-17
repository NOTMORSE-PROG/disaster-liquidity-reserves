"""Machine-learning surrogate of the assumption map.

The exact map contracts the model at every grid assumption (441 contractions
for a 21 x 21 grid) to show where dependence moves the reserve. The surrogate
learns log10 P(L > level) at each candidate reserve level as a smooth function
of the two range coordinates with Gaussian-process regression (Matern 5/2
kernel, hyperparameters by marginal likelihood; Rasmussen and Williams 2006).
It chooses where to contract next by active learning with the U criterion of
AK-MCS (Echard, Gayton and Lemaire 2011): evaluate the grid point whose
reserve level is least certain, and stop once every grid point is classified
with |mean - log10(limit)| >= 2 standard deviations at every level, or the
budget is spent.

Every contraction returns the whole payout curve, so one evaluation trains
all levels at once. The surrogate is for exploration only: the certified
reserve bounds still come from the dominating models in bounds.py.
"""

from __future__ import annotations

import json
import time

import numpy as np
from scipy.linalg import cho_solve, cholesky, solve_triangular
from scipy.optimize import minimize
from scipy.stats import norm

from .contraction import choose_registers, local_tensors
from .curve import Curve
from .money import UNITS_PER_USD
from .paths import results_dir

U_STOP = 2.0  # AK-MCS stopping level: misclassification probability below ~2.3% per level
TAIL_FLOOR = 1e-16


def _matern52(a: np.ndarray, b: np.ndarray, lengths: np.ndarray, variance: float) -> np.ndarray:
    d = np.sqrt(np.sum(((a[:, None, :] - b[None, :, :]) / lengths) ** 2, axis=-1))
    s = np.sqrt(5.0) * d
    return variance * (1.0 + s + 5.0 / 3.0 * d**2) * np.exp(-s)


class GaussianProcess:
    """Exact GP regression with a constant mean and an ARD Matern 5/2 kernel."""

    def __init__(self, nugget: float = 1e-8):
        self.nugget = nugget

    def fit(self, x: np.ndarray, y: np.ndarray) -> GaussianProcess:
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        self.x = x
        self.offset = float(y.mean())
        spread = float(y.std())
        self.scale = spread if spread > 1e-12 else 1.0
        z = (y - self.offset) / self.scale
        n, dims = x.shape

        def objective(theta):
            lengths, variance = np.exp(theta[:dims]), np.exp(theta[dims])
            k = _matern52(x, x, lengths, variance) + self.nugget * np.eye(n)
            try:
                chol = cholesky(k, lower=True)
            except np.linalg.LinAlgError:
                return 1e10
            alpha = cho_solve((chol, True), z)
            return float(0.5 * z @ alpha + np.log(np.diag(chol)).sum())

        bounds = [(np.log(0.05), np.log(20.0))] * dims + [(np.log(0.05), np.log(50.0))]
        best = None
        for start in (0.3, 1.0, 3.0):
            theta0 = np.r_[np.full(dims, np.log(start)), 0.0]
            fit = minimize(objective, theta0, method="L-BFGS-B", bounds=bounds)
            if best is None or fit.fun < best.fun:
                best = fit
        self.lengths = np.exp(best.x[:dims])
        self.variance = float(np.exp(best.x[dims]))
        k = _matern52(x, x, self.lengths, self.variance) + self.nugget * np.eye(n)
        self.chol = cholesky(k, lower=True)
        self.alpha = cho_solve((self.chol, True), z)
        return self

    def predict(self, points: np.ndarray):
        cross = _matern52(np.asarray(points, dtype=float), self.x, self.lengths, self.variance)
        mean = cross @ self.alpha
        v = solve_triangular(self.chol, cross.T, lower=True)
        var = np.maximum(self.variance - np.sum(v**2, axis=0), 1e-14)
        return mean * self.scale + self.offset, np.sqrt(var) * self.scale

    def predict_many(self, ys: np.ndarray, points: np.ndarray):
        """Predict several outputs observed at the same inputs with this kernel.

        Each row of ys is standardised on its own; the kernel hyperparameters
        (fitted on one representative output) are shared, so one Cholesky
        factor serves every output.
        """
        ys = np.asarray(ys, dtype=float)
        offsets = ys.mean(axis=1, keepdims=True)
        scales = ys.std(axis=1, keepdims=True)
        scales[scales <= 1e-12] = 1.0
        z = (ys - offsets) / scales
        alphas = cho_solve((self.chol, True), z.T)  # n x outputs
        cross = _matern52(np.asarray(points, dtype=float), self.x, self.lengths, self.variance)
        v = solve_triangular(self.chol, cross.T, lower=True)
        sd_unit = np.sqrt(np.maximum(self.variance - np.sum(v**2, axis=0), 1e-14))
        means = (cross @ alphas).T * scales + offsets
        return means, sd_unit[None, :] * scales


def _initial_design(points: int) -> list:
    """Corners, centre and four interior points of a 2-D low-discrepancy set."""
    last = points - 1
    picks = [(0, 0), (0, last), (last, 0), (last, last), (last // 2, last // 2)]
    for k in range(1, 5):
        u = (k * 0.6180339887498949) % 1.0
        v = (k * 0.7548776662466927) % 1.0
        picks.append((int(round(u * last)), int(round(v * last))))
    seen, design = set(), []
    for p in picks:
        if p not in seen:
            seen.add(p)
            design.append(p)
    return design


def surrogate_map(
    pool,
    shock_range,
    risk_limit: float,
    register=None,
    upper_units: int | None = None,
    points: int = 21,
    budget: int = 60,
) -> dict:
    """Point-reserve map from a GP surrogate trained by active learning."""
    register = register or choose_registers(pool).lower
    multipliers = np.linspace(*shock_range.earthquake_multiplier, points)
    shares = np.linspace(*shock_range.rain_share, points)
    span = np.array(
        [max(multipliers[-1] - multipliers[0], 1e-12), max(shares[-1] - shares[0], 1e-12)]
    )
    grid = np.array(
        [
            [(m - multipliers[0]) / span[0], (s - shares[0]) / span[1]]
            for m in multipliers
            for s in shares
        ]
    )
    support = register.support_units
    log_limit = np.log10(risk_limit)
    tails: dict[tuple, np.ndarray] = {}

    def evaluate(cell):
        i, j = cell
        tensors, weights = local_tensors(pool, pool.rates((multipliers[i], shares[j])))
        tails[cell] = Curve(support, register.contract(tensors, weights)).tail.copy()

    for cell in _initial_design(points):
        evaluate(cell)

    def level_window():
        reserves = [
            int(np.flatnonzero(np.r_[t[:-1], 0.0] <= risk_limit)[0]) for t in tails.values()
        ]
        lo = max(min(reserves) - 3, 0)
        hi = max(reserves) + 3
        if upper_units is not None:
            hi = max(hi, int(np.searchsorted(support, upper_units)))
        return lo, min(hi, len(support) - 1)

    history = []
    while True:
        lo, hi = level_window()
        cells = list(tails)
        x = np.array([grid[i * points + j] for i, j in cells])
        ys = np.log10(
            np.maximum(
                np.array([[tails[c][level] for c in cells] for level in range(lo, hi + 1)]),
                TAIL_FLOOR,
            )
        )
        # Fit the kernel on the level closest to the limit, then share it across levels.
        pivot = int(np.argmin(np.abs(ys - log_limit).mean(axis=1)))
        gp = GaussianProcess().fit(x, ys[pivot])
        means, sds = gp.predict_many(ys, grid)
        u = np.abs(means - log_limit) / sds
        u_point = u.min(axis=0)
        for i, j in cells:
            u_point[i * points + j] = np.inf  # evaluated exactly
        candidate = int(np.argmin(u_point))
        history.append({"evaluations": len(tails), "min_u": float(u_point[candidate])})
        if u_point[candidate] >= U_STOP or len(tails) >= budget:
            break
        evaluate((candidate // points, candidate % points))

    # Reserve = first level whose predicted tail is at most the limit; exact where evaluated.
    below = means <= log_limit
    first = np.where(below.any(axis=0), below.argmax(axis=0), below.shape[0] - 1) + lo
    reserve = support[first].astype(np.int64)
    for (i, j), tail in tails.items():
        reserve[i * points + j] = support[
            int(np.flatnonzero(np.r_[tail[:-1], 0.0] <= risk_limit)[0])
        ]
    confidence = norm.cdf(np.minimum(u.min(axis=0), 8.0))
    return {
        "earthquake_multiplier": multipliers.tolist(),
        "rain_share": shares.tolist(),
        "reserve_usd": (reserve.reshape(points, points) / UNITS_PER_USD).tolist(),
        "confidence": confidence.reshape(points, points).tolist(),
        "evaluated_points": [[float(multipliers[i]), float(shares[j])] for i, j in tails],
        "evaluations": len(tails),
        "exact_evaluations": points * points,
        "levels": int(hi - lo + 1),
        "stopping": "confidence" if history[-1]["min_u"] >= U_STOP else "budget",
        "history": history,
        "method": "Gaussian-process surrogate with AK-MCS active learning",
    }


def compare(pool, shock_range, risk_limit: float, points: int = 21, budget: int = 60) -> dict:
    """Surrogate against the exact map on the same grid."""
    from .bounds import bracket_reserve
    from .insights import assumption_map

    registers = choose_registers(pool)
    result = bracket_reserve(pool, shock_range, risk_limit, registers=registers)
    started = time.perf_counter()
    exact = assumption_map(
        pool, shock_range, risk_limit, registers.lower, result.upper_units, points
    )
    exact_seconds = time.perf_counter() - started
    started = time.perf_counter()
    fast = surrogate_map(
        pool, shock_range, risk_limit, registers.lower, result.upper_units, points, budget
    )
    fast_seconds = time.perf_counter() - started
    a = np.array(exact["reserve_usd"])
    b = np.array(fast["reserve_usd"])
    return {
        "pool": pool.name,
        "countries": pool.n,
        "risk_limit": risk_limit,
        "grid": f"{points}x{points}",
        "exact_evaluations": exact["evaluations"],
        "surrogate_evaluations": fast["evaluations"],
        "evaluations_saved_percent": 100 * (1 - fast["evaluations"] / exact["evaluations"]),
        "cells_correct_percent": float(100 * np.mean(a == b)),
        "max_abs_error_usd": float(np.max(np.abs(a - b))),
        "distinct_reserves": int(len(np.unique(a))),
        "stopping": fast["stopping"],
        "exact_seconds": exact_seconds,
        "surrogate_seconds": fast_seconds,
    }


def main(argv=None) -> int:
    from .examples import DEFAULT_RANGE, public_two_bond, synthetic_chain

    cases = [(public_two_bond(), 0.015), (public_two_bond(), 0.005)]
    cases += [(synthetic_chain(n), 0.015) for n in (5, 10, 20)]
    rows = []
    for pool, limit in cases:
        row = compare(pool, DEFAULT_RANGE, limit)
        rows.append(row)
        print(
            f"{pool.n:>2} countries {limit:.1%}: "
            f"{row['surrogate_evaluations']} vs {row['exact_evaluations']} "
            f"evaluations, {row['cells_correct_percent']:.1f}% cells exact, max error "
            f"${row['max_abs_error_usd']:,.0f}"
        )
    out = results_dir() / "surrogate.json"
    out.write_text(
        json.dumps(
            {
                "schema": "dlr-surrogate-v1",
                "method": "Gaussian-process regression (Matern 5/2, ARD) on log10 "
                "exceedance per reserve "
                "level, AK-MCS U-criterion active learning, stop at U >= 2 or 60 evaluations",
                "note": "Exploration only. Certified reserve bounds come from the "
                "dominating models.",
                "rows": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
