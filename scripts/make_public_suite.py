"""Package the seven public scenarios as an evaluation-v1 suite.

The organizer's `qjlb evaluate` command reads suites written by `qjlb
holdout`. This script writes the unmodified public scenario files and data in
the same layout, with the same manifest fields and file hashes, so the
official evaluator can score the public cases too. The seed only drives the
evaluator's per-repeat seeds.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmark"
sys.path.insert(0, str(BENCHMARK / "src"))

from qjlb.holdout import DATA_FILES, digest, load_suite  # noqa: E402


def build(output: Path, seed: int) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "cases").mkdir()
    (output / "data").mkdir()
    for name in DATA_FILES:
        shutil.copyfile(BENCHMARK / "data" / name, output / "data" / name)
    cases = []
    for scenario in sorted((BENCHMARK / "scenarios").glob("*.json")):
        target = output / "cases" / scenario.name
        shutil.copyfile(scenario, target)
        cases.append(f"cases/{scenario.name}")
    manifest = {
        "schema_version": 1,
        "generator_version": "holdout-v1",
        "seed": seed,
        "cases": cases,
        "files": {
            p.relative_to(output).as_posix(): digest(p)
            for p in sorted(output.rglob("*"))
            if p.is_file()
        },
        "note": "Public practice scenarios from benchmark/scenarios, copied unmodified.",
    }
    (output / "suite.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    load_suite(output)  # the organizer's own manifest check
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261113)
    args = parser.parse_args()
    manifest = build(args.output, args.seed)
    print(f"Public suite with {len(manifest['cases'])} cases saved to {args.output}")


if __name__ == "__main__":
    main()
