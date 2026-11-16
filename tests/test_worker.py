import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from qjlb.cli import load_model, methods_for
from qjlb.evaluation import strict_submission, tolerances
from qjlb.holdout import generate

from liquidity_reserves.benchmark import ADB_UNITS, WORLD_BANK_UNITS
from liquidity_reserves.worker import solve_request

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = sorted((ROOT / "benchmark" / "scenarios").glob("*.json"))


def stage(config: Path, data: Path, work: Path, model):
    """Mirror the evaluator's fresh working directory for one invocation."""
    (work / "cases").mkdir(parents=True)
    (work / "data").mkdir()
    shutil.copyfile(config, work / "cases" / "case.json")
    names = (
        ["adb_terms.json"]
        if model.config["track"] == "adb_drb"
        else ["audit.json", "seismic.npz", "flood.npz"]
    )
    for name in names:
        shutil.copyfile(data / name, work / "data" / name)
    request = {
        "schema_version": 1,
        "run_id": secrets.token_hex(16),
        "problem_id": model.fingerprint,
        "track": model.config["track"],
        "config_file": "cases/case.json",
        "seed": 12345,
        "samples": 8192,
        "targets": list(model.metric_names),
    }
    (work / "request.json").write_text(json.dumps(request), encoding="utf-8")
    return request


def check_against_reference(submission, request, model):
    assert strict_submission(submission, request, model, "classical_cpu", 60.0) == (
        "code_review_required"
    )
    reference = methods_for(model)["exact"](model)
    bounds = tolerances(model, reference)
    for name in model.metric_names:
        error = abs(submission["estimates"][name] - reference["estimates"][name]["estimate"])
        assert error <= 1e-9 * bounds[name], name


@pytest.mark.parametrize("config", SCENARIOS, ids=[p.stem for p in SCENARIOS])
def test_public_scenarios_meet_the_contract(config, tmp_path, monkeypatch):
    model, _ = load_model(config)
    request = stage(config, ROOT / "benchmark" / "data", tmp_path, model)
    monkeypatch.chdir(tmp_path)
    submission = solve_request(request)
    submission["resources"]["elapsed_seconds"] = 1.0
    submission["resources"]["preparation_seconds"] = 0.5
    check_against_reference(submission, request, model)
    assert submission["execution"] == "classical_cpu"
    assert submission["resources"]["shots"] == submission["resources"]["qubits"] == 0
    units = ADB_UNITS if model.config["track"] == "adb_drb" else WORLD_BANK_UNITS
    assert set(submission["estimates"]) == set(units) == set(submission["target_units"])


def test_generated_practice_cases(tmp_path, monkeypatch):
    suite = tmp_path / "suite"
    generate(suite, ROOT / "benchmark" / "data", count=6, seed=20261113)
    for case in sorted((suite / "cases").glob("*.json")):
        model, _ = load_model(case)
        work = tmp_path / case.stem
        request = stage(case, suite / "data", work, model)
        monkeypatch.chdir(work)
        submission = solve_request(request)
        submission["resources"]["elapsed_seconds"] = 1.0
        submission["resources"]["preparation_seconds"] = 0.5
        check_against_reference(submission, request, model)


def test_fresh_process_entry_point(tmp_path):
    config = ROOT / "benchmark" / "scenarios" / "adb-base.json"
    model, _ = load_model(config)
    request = stage(config, ROOT / "benchmark" / "data", tmp_path, model)
    env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "LANG") if k in os.environ}
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "worker.py"),
            "--request",
            "request.json",
            "--output",
            "out.json",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        timeout=120,
    )
    submission = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    check_against_reference(submission, request, model)
    resources = submission["resources"]
    assert 0 <= resources["preparation_seconds"] <= resources["elapsed_seconds"]
    assert submission["run_id"] == request["run_id"] and submission["seed"] == request["seed"]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"problem_id": "0" * 64}, "problem ID"),
        ({"targets": ["term_expected_principal_loss_usd"]}, "target names"),
        ({"seed": "1"}, "seed"),
        ({"schema_version": 2}, "schema_version"),
        ({"track": "worldbank_elt"}, "track"),
    ],
)
def test_bad_requests_fail_loudly(change, message, tmp_path, monkeypatch):
    config = ROOT / "benchmark" / "scenarios" / "adb-base.json"
    model, _ = load_model(config)
    request = stage(config, ROOT / "benchmark" / "data", tmp_path, model)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match=message):
        solve_request({**request, **change})
