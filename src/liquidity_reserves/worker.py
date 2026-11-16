"""evaluation-v1 team worker.

Usage: python worker.py --request request.json --output result.json

The organizer's evaluator starts this in a fresh directory with a request
naming one configuration, its problem ID, the target names, a seed and a
unique run ID. The worker returns all five targets as a classical_cpu entry
with zero shots and qubits. Model loading, fingerprints and dataset checksum
checks use the organizer's unmodified qjlb package.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

_STARTED = time.perf_counter()

EXECUTION = "classical_cpu"
METHODS = {
    "adb_drb": "tensor_chain_contraction_exact_amounts",
    "worldbank_elt": "catalogue_annual_rate_integration",
}


def _check_request(request: dict):
    if not isinstance(request, dict) or request.get("schema_version") != 1:
        raise ValueError("expected an evaluation-v1 request with schema_version 1")
    if not isinstance(request.get("run_id"), str) or not request["run_id"]:
        raise ValueError("request run_id must be a nonempty string")
    if type(request.get("seed")) is not int:
        raise ValueError("request seed must be an integer")
    for key in ("problem_id", "track", "config_file"):
        if not isinstance(request.get(key), str):
            raise ValueError(f"request {key} must be a string")
    if not isinstance(request.get("targets"), list):
        raise ValueError("request targets must be a list")


def solve_request(request: dict) -> dict:
    from .paths import ensure_qjlb

    ensure_qjlb()
    from qjlb.cli import load_model

    from .benchmark import ADB_UNITS, WORLD_BANK_UNITS, adb_term_targets, world_bank_annual_targets

    _check_request(request)
    model, _ = load_model(request["config_file"])
    if model.fingerprint != request["problem_id"]:
        raise ValueError("input problem ID does not match the configuration and data")
    track = model.config["track"]
    if track != request["track"]:
        raise ValueError("request track does not match the configuration")
    if list(request["targets"]) != list(model.metric_names):
        raise ValueError("request targets differ from this track's official target names")
    if track == "adb_drb":
        terms_path = Path(request["config_file"]).parent / model.config["terms_file"]
        terms = json.loads(terms_path.read_text(encoding="utf-8"))
        prepared = time.perf_counter()
        estimates, evaluations, _ = adb_term_targets(model.config, terms)
        units = ADB_UNITS
    else:
        prepared = time.perf_counter()
        estimates, evaluations = world_bank_annual_targets(model)
        units = WORLD_BANK_UNITS
    return {
        "problem_id": model.fingerprint,
        "method": METHODS[track],
        "execution": EXECUTION,
        "estimates": {name: float(estimates[name]) for name in model.metric_names},
        "resources": {
            "elapsed_seconds": None,  # filled in just before writing
            "preparation_seconds": prepared - _STARTED,
            "shots": 0,
            "qubits": 0,
            "model_evaluations": int(evaluations),
        },
        "run_id": request["run_id"],
        "seed": request["seed"],
        "track": track,
        "target_units": units,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    from .paths import ensure_qjlb

    ensure_qjlb()
    from qjlb.validation import read_json

    submission = solve_request(read_json(args.request))
    resources = submission["resources"]
    resources["elapsed_seconds"] = time.perf_counter() - _STARTED
    resources["preparation_seconds"] = min(
        resources["preparation_seconds"], resources["elapsed_seconds"]
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(submission, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
