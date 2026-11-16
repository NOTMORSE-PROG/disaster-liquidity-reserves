"""Preloaded pools: the public ADB two-bond example and a synthetic overlapping chain."""

from __future__ import annotations

import json
from pathlib import Path

from .calibration import earthquake_jump_rates, precipitation_rate, shared_earthquake_rate
from .money import usd_to_units
from .paths import benchmark_dir
from .portfolio import Country, RegionalPool, SharedShockRange

COUNTRY_NAMES = {
    "KGZ": "Kyrgyz Republic",
    "TJK": "Tajikistan",
    "KAZ": "Kazakhstan",
    "TKM": "Turkmenistan",
    "UZB": "Uzbekistan",
}

# Demonstration settings stated in the technical proposal: shared earthquake
# rate from zero to twice ADB's base rate, shared precipitation rate from zero
# to 25% of the smaller marginal rain rate, and an illustrative 1.5% limit.
DEFAULT_RANGE = SharedShockRange(earthquake_multiplier=(0.0, 2.0), rain_share=(0.0, 0.25))
DEFAULT_RISK_LIMIT = 0.015
SYNTHETIC_TICK_USD = 250_000


def load_terms(directory: Path | None = None) -> dict:
    path = (directory or benchmark_dir()) / "data" / "adb_terms.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_scenario(name: str = "adb-base", directory: Path | None = None) -> dict:
    path = (directory or benchmark_dir()) / "scenarios" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def adb_pool_from_config(config: dict, terms: dict, name: str | None = None) -> RegionalPool:
    """Pool for any ADB-track configuration (public scenario or generated case)."""
    if config.get("track") != "adb_drb":
        raise ValueError("expected an adb_drb configuration")
    codes = list(config["countries"])
    if len(codes) != 2:
        raise ValueError("ADB bond configurations cover two countries")
    jumps = [
        earthquake_jump_rates(terms["annual_earthquake_exceedance_probabilities"][code])
        for code in codes
    ]
    totals = [float(j.sum()) for j in jumps]
    base_shared = shared_earthquake_rate(
        totals, terms["same_event_conditional_trigger_probabilities"], codes
    )
    multiplier = float(config["precipitation_rate_multiplier"])
    countries = tuple(
        Country(
            code=code,
            name=COUNTRY_NAMES.get(code, code),
            earthquake_rate=totals[i],
            severity=tuple(float(x) for x in jumps[i] / totals[i]),
            rain_rate=precipitation_rate(
                config["precipitation_annual_trigger_probability"][i], multiplier
            ),
            earthquake_cap_units=usd_to_units(
                config["earthquake_sublimit_usd"][i], f"{code} earthquake cap"
            ),
            rain_cap_units=usd_to_units(
                config["precipitation_sublimit_usd"][i], f"{code} precipitation payment"
            ),
        )
        for i, code in enumerate(codes)
    )
    return RegionalPool(
        name=name or "ADB two-bond configuration",
        countries=countries,
        term_years=int(config["term_years"]),
        base_shared_earthquake=(base_shared,),
        base_point=(
            float(config["earthquake_common_rate_multiplier"]),
            float(config["precipitation_common_rate_share"]),
        ),
        synthetic=False,
    )


def public_two_bond(scenario: str = "adb-base", directory: Path | None = None) -> RegionalPool:
    """ADB's public 2026 Kyrgyz Republic and Tajikistan bond model (benchmark inputs)."""
    directory = directory or benchmark_dir()
    config = load_scenario(scenario, directory)
    terms = load_terms(directory)
    pool = adb_pool_from_config(config, terms)
    return RegionalPool(
        name="Public two-bond example (ADB 2026 KGZ and TJK bonds)",
        countries=pool.countries,
        term_years=pool.term_years,
        base_shared_earthquake=pool.base_shared_earthquake,
        base_point=pool.base_point,
        synthetic=False,
        description=(
            "Organizer's public benchmark calibration: USD 70m earthquake and USD 10m "
            "precipitation cover per country over a three-year term. Earthquakes pay in "
            "quarter steps up to the cap; precipitation pays once."
        ),
        source={
            "scenario": f"benchmark/scenarios/{scenario}.json",
            "terms": "benchmark/data/adb_terms.json",
            "documents": {k: v["url"] for k, v in terms["sources"].items()},
        },
    )


def synthetic_chain(countries: int = 5, directory: Path | None = None) -> RegionalPool:
    """Synthetic overlapping-region chain (illustrative; not ADB data).

    Countries alternate the public KGZ and TJK calibrations. Each adjacent
    pair shares earthquake and precipitation sources at ADB's base shared
    rates, so interior countries share events with both neighbours. Caps vary
    by country on a USD 250,000 grid.
    """
    if not 2 <= countries <= 50:
        raise ValueError("the synthetic chain supports 2 to 50 countries")
    public = public_two_bond(directory=directory)
    tick = SYNTHETIC_TICK_USD
    members = []
    for i in range(countries):
        template = public.countries[i % 2]
        members.append(
            Country(
                code=f"S{i + 1:02d}",
                name=f"Synthetic country {i + 1} ({template.code} calibration)",
                earthquake_rate=template.earthquake_rate,
                severity=template.severity,
                rain_rate=template.rain_rate,
                earthquake_cap_units=usd_to_units(tick * (264 + 4 * (i % 7))),
                rain_cap_units=usd_to_units(tick * (32 + i % 5)),
            )
        )
    shared = public.base_shared_earthquake[0]
    return RegionalPool(
        name=f"Synthetic overlapping-region chain ({countries} countries)",
        countries=tuple(members),
        term_years=public.term_years,
        base_shared_earthquake=tuple([shared] * (countries - 1)),
        base_point=(1.0, 0.25),
        synthetic=True,
        description=(
            "SYNTHETIC illustration, not ADB data. Countries alternate the public KGZ and TJK "
            "earthquake and precipitation calibrations. Each neighbouring pair has its own "
            "shared earthquake and precipitation sources, so interior countries share events "
            "with both neighbours. Earthquake caps are USD 66m to USD 72m and precipitation "
            "payments USD 8m to USD 9m on a USD 250,000 grid."
        ),
        source={"generator": "liquidity_reserves.examples.synthetic_chain", "countries": countries},
    )


def example_catalogue() -> list:
    return [
        {
            "id": "public-two-bond",
            "label": "Public two-bond example (ADB KGZ and TJK, 3 years)",
            "synthetic": False,
        },
        {
            "id": "synthetic-chain",
            "label": "Synthetic overlapping-region chain (illustrative)",
            "synthetic": True,
        },
    ]
