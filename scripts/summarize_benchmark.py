"""Summarise evaluator reports: passes, failures and accuracy per track.

Reads <run>/public-report/report.json and <run>/practice-report/report.json
written by `qjlb evaluate`. Timings stay in those reports; this summary keeps
accuracy, validity and workload counts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def summarize_report(path: Path) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for case in report["cases"]:
        team = case["methods"]["team"]
        baselines = {
            name: method["accuracy_pass"]
            for name, method in case["methods"].items()
            if name != "team"
        }
        runs = team["runs"]
        cases.append(
            {
                "case": case["case"],
                "track": case["track"],
                "countries": case["countries"],
                "team_accuracy_pass": team["accuracy_pass"],
                "valid_runs": sum(r["status"] == "valid" for r in runs),
                "runs": len(runs),
                "invalid_details": sorted({r["detail"] for r in runs if r["status"] != "valid"}),
                "metric_checks": len(team["metrics"]),
                "metric_passes": sum(m["normalised_rmse"] <= 1 for m in team["metrics"]),
                "worst_normalised_rmse": max(
                    (m["normalised_rmse"] for m in team["metrics"]), default=None
                ),
                "model_evaluations": sorted(
                    {
                        r["submission"]["resources"]["model_evaluations"]
                        for r in runs
                        if "submission" in r
                    }
                ),
                "baseline_accuracy_pass": baselines,
            }
        )
    return {
        "protocol": report["protocol"],
        "declared_execution": report["declared_execution"],
        "repeats": report["repeats"],
        "baseline_samples": report["baseline_samples"],
        "official_repeat_count_met": report["official_repeat_count_met"],
        "workload_coverage_met": report["workload_coverage_met"],
        "protocol_eligible": report["protocol_eligible"],
        "tracks": report["tracks"],
        "platform": report["environment"]["platform"],
        "python": report["environment"]["python"],
        "suite_sha256": report["suite_sha256"],
        "cases": cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, help="folder created by scripts/run_benchmark.sh")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = {}
    for suite in ("public", "practice"):
        path = args.run / f"{suite}-report" / "report.json"
        if path.is_file():
            summary[suite] = summarize_report(path)
    cases = [case for suite in summary.values() for case in suite["cases"]]
    summary["totals"] = {
        "cases": len(cases),
        "cases_passing": sum(c["team_accuracy_pass"] for c in cases),
        "metric_checks": sum(c["metric_checks"] for c in cases),
        "metric_passes": sum(c["metric_passes"] for c in cases),
        "runs": sum(c["runs"] for c in cases),
        "valid_runs": sum(c["valid_runs"] for c in cases),
        "worst_normalised_rmse": max(
            (c["worst_normalised_rmse"] for c in cases if c["worst_normalised_rmse"] is not None),
            default=None,
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    totals = summary["totals"]
    print(
        f"{totals['cases_passing']}/{totals['cases']} cases pass; "
        f"{totals['metric_passes']}/{totals['metric_checks']} metric checks pass; "
        f"{totals['valid_runs']}/{totals['runs']} team runs valid"
    )


if __name__ == "__main__":
    main()
