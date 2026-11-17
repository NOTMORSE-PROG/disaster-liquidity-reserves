import json
import threading
import urllib.error
import urllib.request

import pytest

from liquidity_reserves.app.server import make_server
from liquidity_reserves.portfolio import InputError
from liquidity_reserves.service import calculate, default_inputs, export


@pytest.fixture(scope="module")
def server():
    httpd = make_server("127.0.0.1", 0, quiet=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def request(base, path, payload=None, method=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_default_calculation_matches_the_proposal():
    result = calculate(default_inputs("public-two-bond"))
    assert result["base"]["reserve"]["usd"] == "80000000"
    assert result["range_result"]["upper"]["usd"] == "87500000"
    assert result["resolved"] and result["status"] == "resolved"
    assert result["evaluations"]["adaptive"]["total"] == 7
    assert result["evaluations"]["grid"]["evaluations"] == 517
    assert result["supporting_scenario"]["shortfall"]["exceedance"] > 0.015
    assert result["sufficiency"]["bound_at_upper"] <= 0.015
    assert all(check["passed"] for check in result["checks"])
    assert result["applicant"] == "Mark Andrei Condino (sole applicant)"


def test_changed_risk_limit_and_shared_rates():
    payload = default_inputs("public-two-bond")
    payload["risk_limit_percent"] = 0.5
    result = calculate(payload)
    assert result["range_result"]["upper"]["usd"] == "105000000"
    assert result["evaluations"]["adaptive"]["total"] == 39
    payload["earthquake_multiplier_high"] = 0
    payload["rain_share_percent_high"] = 0
    payload["base_earthquake_multiplier"] = 0
    payload["base_rain_share_percent"] = 0
    independent = calculate(payload)
    assert independent["resolved"]
    assert independent["range_result"]["upper"]["value"] <= 105_000_000


def test_unresolved_result_reports_the_bracket():
    payload = default_inputs("synthetic-chain", 10)
    payload["max_evaluations"] = 15
    payload["compare_grid"] = False
    result = calculate(payload)
    assert result["status"] == "budget" and not result["resolved"]
    assert result["range_result"]["lower"]["value"] < result["range_result"]["upper"]["value"]
    assert any("Unresolved" in m["text"] for m in result["messages"])
    assert result["inputs"]["pool"]["synthetic"]


def test_invalid_inputs_list_every_problem():
    payload = default_inputs("public-two-bond")
    payload.update(risk_limit_percent="abc", earthquake_multiplier_high=40, gap_usd=-1)
    payload["countries"][0]["earthquake_cap_usd"] = "70000000.001"
    with pytest.raises(InputError) as error:
        calculate(payload)
    text = " ".join(error.value.messages)
    assert "Risk limit" in text and "KGZ" in text and "Gap tolerance" in text
    assert "whole number of cents" in text


@pytest.mark.parametrize(
    "change",
    [
        {"risk_limit_percent": 0},
        {"risk_limit_percent": 100},
        {"earthquake_multiplier_low": 3, "earthquake_multiplier_high": 2},
        {"rain_share_percent_high": 101},
        {"max_evaluations": 3},
        {"term_years": 2.5},
        {"example": "unknown"},
    ],
)
def test_out_of_range_inputs(change):
    payload = default_inputs("public-two-bond")
    payload.update(change)
    with pytest.raises(InputError):
        calculate(payload)


def test_export_contains_inputs_bounds_scenario_and_validation():
    payload = default_inputs("public-two-bond")
    name, content_type, body = export(payload, "json")
    report = json.loads(body)
    assert name.endswith(".json") and content_type == "application/json"
    for key in ("inputs", "range_result", "supporting_scenario", "checks", "recorded_validation"):
        assert key in report
    assert report["exceedance_table"][0]["amount_usd"] == "0"
    assert "chart" not in report
    name, content_type, body = export(payload, "md")
    text = body.decode()
    assert name.endswith(".md") and "Why a smaller reserve falls short" in text
    assert "Validation status" in text and "$87,500,000" in text


def test_http_api_round_trip(server):
    status, _, body = request(server, "/api/health")
    assert status == 200 and json.loads(body)["status"] == "ok"
    status, _, body = request(server, "/api/defaults?example=synthetic-chain&countries=3")
    defaults = json.loads(body)
    assert status == 200 and len(defaults["countries"]) == 3 and defaults["synthetic"]
    status, _, body = request(server, "/api/calculate", defaults)
    assert status == 200 and json.loads(body)["inputs"]["synthetic_countries"] == 3
    status, headers, body = request(server, "/api/export?format=md", defaults)
    assert status == 200 and "attachment" in headers["Content-Disposition"]
    assert body.startswith(b"# Reserve report")


def test_http_errors_and_static_files(server):
    status, _, body = request(server, "/api/calculate", {"example": "public-two-bond"})
    assert status == 422 and json.loads(body)["errors"]
    status, _, body = request(server, "/api/calculate", payload=None, method="POST")
    assert status == 422
    status, _, _ = request(server, "/api/export?format=xml", default_inputs())
    assert status == 422
    status, headers, body = request(server, "/")
    assert status == 200 and b"<title>ReserveBound</title>" in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    for path in ("/static/app.js", "/static/charts.js", "/static/report.js", "/favicon.ico"):
        assert request(server, path)[0] == 200
    for path in ("/static/../server.py", "/static/%2e%2e/server.py", "/etc/passwd", "/nope"):
        assert request(server, path)[0] == 404


def test_dashboard_insights_are_consistent():
    result = calculate(default_inputs("public-two-bond"))
    headline = result["headline"]
    assert headline["uplift"]["usd"] == "7500000" and headline["uplift_sign"] == 1
    assert headline["evaluations"] == 7 and headline["grid_evaluations"] == 517
    grid = result["insights"]["map"]
    assert max(max(row) for row in grid["reserve_usd"]) == 87_500_000
    assert grid["witness"] == [2.0, 0.25] and grid["base_in_range"]
    shares = [c["share_of_shortfall_payouts"] for c in result["insights"]["drivers"]["countries"]]
    assert sum(shares) == pytest.approx(1)
    assert len(result["insights"]["chain"]["links"]) == 1


def test_sweep_quantum_and_evidence_endpoints(server):
    payload = default_inputs("public-two-bond")
    status, _, body = request(server, "/api/sweep", {**payload, "limits_percent": [0.5, 1.5]})
    rows = json.loads(body)["rows"]
    assert status == 200 and [r["upper_usd"] for r in rows] == [105_000_000, 87_500_000]
    status, _, body = request(server, "/api/sweep", {**payload, "limits_percent": [150]})
    assert status == 422
    status, _, body = request(server, "/api/quantum", payload)
    check = json.loads(body)
    assert status == 200 and check["qubits"] == 6
    assert check["probability"] == pytest.approx(check["exact_from_contraction"], abs=1e-15)
    status, _, body = request(server, "/api/evidence")
    data = json.loads(body)
    assert status == 200 and data["complexity"][0]["countries"] == 2
    assert request(server, "/report")[0] == 200


def test_financing_endpoints(server):
    payload = default_inputs("public-two-bond")
    status, _, body = request(server, "/api/financing", payload)
    assert status == 200
    report = json.loads(body)
    assert report["plan"]["capacity_usd"] == 87_500_000
    assert report["pooling"]["saving_percent"] > 30
    assert len(report["frontier"]) >= 5
    status, _, body = request(server, "/api/financing", {**payload, "insurance_multiple": "abc"})
    assert status == 422 and b"Insurance multiple" in body
    status, _, body = request(server, "/api/cost-defaults")
    assert status == 200 and json.loads(body)["insurance_multiple"] == 2.6


def test_range_evidence_and_surrogate_endpoints(server):
    status, _, body = request(server, "/api/range-evidence")
    assert status == 200
    assert json.loads(body)["evidence_range"]["earthquake_multiplier"] == [0.0, 1.0]
    status, _, body = request(server, "/api/surrogate", default_inputs("public-two-bond"))
    assert status == 200
    report = json.loads(body)
    assert report["evaluations"] < report["exact_evaluations"]
