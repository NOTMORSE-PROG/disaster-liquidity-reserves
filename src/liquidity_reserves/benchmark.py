"""Benchmark targets for the two public tracks.

ADB targets are probabilities and USD amounts over the bond term. World Bank
targets are annual event rates and USD per year. They are computed by
separate functions and reported with separate units; no number from one
track is reused for the other.
"""

from __future__ import annotations

import numpy as np

from .contraction import chain_probability, choose_registers, local_tensors, tensor_entries
from .curve import Curve
from .examples import adb_pool_from_config
from .money import usd_to_units

ADB_UNITS = {
    "term_expected_principal_loss_usd": "USD, expected combined principal loss over the term",
    "term_joint_attachment_probability": "probability over the term",
    "term_portfolio_loss_exceedance_probability": "probability over the term (strictly above T)",
    "term_portfolio_stop_loss_usd": "USD, expected loss above T over the term",
    "term_both_bonds_exhausted_probability": "probability over the term",
}
WORLD_BANK_UNITS = {
    "annual_economic_loss_usd": "USD per year",
    "annual_layer_payment_usd": "USD per year",
    "joint_layer_exceedance_rate_per_year": "events per year",
    "portfolio_layer_exceedance_rate_per_year": "events per year (strictly above threshold)",
    "annual_layer_stop_loss_usd": "USD per year",
}


def adb_term_targets(config: dict, terms: dict):
    """Exact ADB term targets by tensor contraction with exact monetary amounts."""
    pool = adb_pool_from_config(config, terms)
    registers = choose_registers(pool)
    if not registers.exact:
        raise ValueError("two-country bond payouts should always have an exact register")
    rates = pool.rates(pool.base_point)
    tensors, weights = local_tensors(pool, rates)
    curve = Curve(registers.lower.support_units, registers.lower.contract(tensors, weights))
    threshold = usd_to_units(config["portfolio_threshold_usd"], "portfolio threshold")
    pay = pool.payout_table()
    attached = [row > 0 for row in pay]
    exhausted = [row >= c.principal_units for row, c in zip(pay, pool.countries, strict=True)]
    estimates = {
        "term_expected_principal_loss_usd": curve.mean_usd,
        "term_joint_attachment_probability": chain_probability(tensors, weights, attached),
        "term_portfolio_loss_exceedance_probability": curve.exceedance(threshold),
        "term_portfolio_stop_loss_usd": curve.expected_excess(threshold),
        "term_both_bonds_exhausted_probability": chain_probability(tensors, weights, exhausted),
    }
    return estimates, tensor_entries(tensors), curve


def world_bank_annual_targets(model):
    """Annual-rate integration over the organizer's checksum-verified event catalogue."""
    peril = model.peril
    attachment = model.attachment[peril]
    exhaustion = model.exhaustion[peril]
    payout = model.limit * np.clip((model.loss - attachment) / (exhaustion - attachment), 0, 1)
    total = payout.sum(axis=1)
    rates = model.rates
    estimates = {
        "annual_economic_loss_usd": float(rates @ model.loss.sum(axis=1)),
        "annual_layer_payment_usd": float(rates @ total),
        "joint_layer_exceedance_rate_per_year": float(rates @ np.all(payout > 0, axis=1)),
        "portfolio_layer_exceedance_rate_per_year": float(rates @ (total > model.threshold)),
        "annual_layer_stop_loss_usd": float(rates @ np.maximum(total - model.threshold, 0)),
    }
    return estimates, int(model.state_count)
