# Disaster liquidity reserves

Prototype for **Sizing Disaster Liquidity Reserves under Overlapping Regional Shocks**
(ADB Quantum Innovation Challenge).

A regional disaster pool must decide how much cash to hold when several countries may
claim at once. Given country loss rates, payout caps, a range of shared earthquake and
rain rates, and a limit on the chance of running short, the engine returns the smallest
reserve that meets the limit across the whole range. When the minimum is not resolved
within the calculation budget it returns lower and upper bounds. It also shows a feasible
scenario in which a smaller reserve falls short and explains why the upper amount is
sufficient.

- **Engine** (`src/liquidity_reserves`): capped payout model; quantum-inspired
  tensor-network (matrix-product) contraction on a CPU with exact monetary amounts;
  adaptive refinement of reserve bounds; an independent state-space solver for checks.
- **Quantum algorithm** (`quantum_chain.py`): the same tensors loaded into a circuit,
  with amplitude estimation of the shortfall probability and a quantum reserve search,
  verified on a simulator.
- **Benchmark worker** (`worker.py`): the organizer's evaluation-v1 request/result
  contract as a `classical_cpu` entry for both public tracks.
- **Range evidence** (`evidence.py`): the shared-shock range checked against the World
  Bank event loss tables that ship with the benchmark.
- **Financing optimizer** (`layering.py`, `pooling.py`, `qaoa.py`): the cheapest mix of
  cash reserve, contingent credit and parametric insurance that funds the certified
  reserve, the pooling benefit with a fair split between members, and the same choice
  solved as a QUBO with QAOA.
- **Machine-learning surrogate** (`surrogate.py`): a Gaussian-process map of the reserve
  across the range, trained by active learning on a fraction of the contractions.
- **Noise study** (`noise.py`): amplitude estimation under IBM Heron-class noise and the
  error rates the full circuits need.
- **ReserveBound app** (`python -m liquidity_reserves.app`, or `docker run`): a local
  decision dashboard with live inputs, the evidence behind each bound, scenario
  comparison, the financing stack, the benchmark record, the quantum methods and a PDF
  report.

![ReserveBound decision view for the public two-bond example](docs/screenshots/decision.jpg)

## What is in this repository

| Item | Where |
|---|---|
| Source code | `src/liquidity_reserves/` (engine, app, quantum check, validation), `worker.py`, `scripts/`, `tests/` |
| Environment specifications and dependencies | `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `requirements-quantum.txt`; [Environment and hardware](#environment-and-hardware) |
| Setup and execution instructions | This README: [Setup](#setup), [Run the app](#run-the-app), [Test](#test), [Run the official benchmark](#run-the-official-benchmark) |
| Hardware and simulator details | [Environment and hardware](#environment-and-hardware) |
| Sample results and outputs | [`results/`](results) and [Sample outputs](#sample-outputs) |

## Setup

Python 3.10 or later. From the repository folder:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

`requirements.txt` pins the versions used for the recorded results (numpy 1.26.4,
scipy 1.15.3). The app uses only the Python standard library on top of those. For the
quantum algorithm (circuits, simulation and resource counts), also run
`python -m pip install -r requirements-quantum.txt`.

## Run the app

```sh
python -m liquidity_reserves.app --open
```

The app serves `http://127.0.0.1:8765/` on the local machine only. It preloads the
public two-bond example; a clearly labelled synthetic overlapping-region chain (2 to 20
countries) is in the portfolio menu. Results update as inputs change (up to 10
countries; longer chains use the Calculate button).

| View | What it shows |
|---|---|
| Decision | Reserve to hold, base reserve, dependence uplift and proof cost; why a smaller reserve falls short and why the upper amount is enough; exceedance curves; a map of the reserve needed across the shared-shock range with the refinement cells (exact, or the ML surrogate); each country's share of a shortfall; the regional chain; assumptions and live checks |
| Scenarios | What the World Bank catalogue and the bond documents say about the shared-shock range, with one-click evidence and stress ranges; reserve against the risk limit; pinned scenarios compared side by side |
| Financing | The cheapest reserve / credit / insurance stack for the certified reserve with editable, sourced cost assumptions; alternatives; funding cost against the risk limit; pooling benefit and each member's share; QAOA on the same choice |
| Evidence | Official evaluator results against the organizer's classical baselines, every proposal figure recomputed, evaluation counts, the ML surrogate study, the noise study and error budget, and scaling |
| Method | How the model, contraction and bounds work; the quantum circuit built from the same tensors, its verification and the reserve decision reached with it; query scaling against Monte Carlo; quantum resource counts |

Export offers a print-ready PDF report (A4, through the browser's Save as PDF), JSON
data and a Markdown summary. Each contains the inputs, the reserve bounds, the
supporting scenario and the validation status.

## Run with Docker

```sh
docker build -t reservebound .
docker run --rm -p 8765:8765 reservebound
```

Then open `http://localhost:8765/`. The image runs as a non-root user and has a health
check on `/api/health`. `--build-arg WITH_QUANTUM=0` builds a smaller image without
Qiskit; the live QAOA button then reports that the quantum extras are missing, and the
recorded QAOA results still show.

## Test

```sh
python -m pytest              # all tests
python -m pytest -m "not slow"
ruff check . && ruff format --check .
```

## Quantum algorithm

The tensor chain maps directly to a quantum circuit (`src/liquidity_reserves/quantum_chain.py`):

1. each neighbouring pair gets a 4-qubit register holding its shared earthquake count and
   shared rain event, prepared with amplitudes equal to the square roots of their
   probabilities;
2. each country gets a 4-qubit outcome register prepared conditionally on its two pair
   registers from that country's tensor, so the outcome registers carry the exact joint law;
3. a Fourier-space (Draper) adder sums the payouts and subtracts the reserve plus one
   payout step, and the sign bit sets a flag when payouts are strictly above the reserve;
4. Grover powers amplify the flag, and maximum-likelihood amplitude estimation turns the
   counts into P(payout > reserve).

Intervals combine exact Clopper-Pearson intervals across Grover powers (Bonferroni), so
their coverage is at least 95% by construction; 1,000 repeated runs measured 97.0% to
97.5%. Recorded results, all on simulators:

| Check | Result |
|---|---|
| Two-bond circuit | 21 qubits; loaded law matches the exact joint law to 1.1e-15 |
| Three overlapping countries | 33 qubits; loaded law matches to 1.2e-14 |
| Payout adder and reserve flag | exact for all 100 (two countries) and 1,000 (three countries) outcome combinations |
| Full 21-qubit circuit, Grover powers 0 to 5 | matches the amplitude rotation to 7.9e-13 |
| Quantum reserve search, base assumptions | $80m at 1.5% (matches the classical engine); $97.5m to $105m at 0.5% (a probe 0.03 points from the limit stays undecided; the classical $105m lies inside) |
| Range bounds at 1.5% | both $87.5m bounds confirmed by quantum estimates |
| Circuit size | 24 to 413 qubits for 2 to 50 synthetic countries, about 8 per country |

```sh
python -m pip install -r requirements-quantum.txt
python -m liquidity_reserves.quantum    # about 20 minutes; writes results/quantum.json
```

Scope: the circuits run on Qiskit Aer's statevector simulator with every qubit simulated
and no noise. No quantum hardware was used. Amplitude estimation needs quadratically
fewer model queries than Monte Carlo for the same precision, but it is not faster than
the exact classical contraction at these sizes, and a 0.1% estimate needs Grover depth
1,024, which calls for fault-tolerant hardware.

## Range evidence, financing, machine learning and noise

Each study writes a JSON record to `results/` and appears in the app.

```sh
python -m liquidity_reserves.evidence    # results/evidence.json    (seconds)
python -m liquidity_reserves.surrogate   # results/surrogate.json   (about a minute)
python -m liquidity_reserves.layering    # results/layering.json    (about a minute; QAOA needs requirements-quantum.txt)
python -m liquidity_reserves.noise       # results/noise.json       (needs requirements-quantum.txt)
```

**Range evidence.** Each country's trigger in the World Bank SFRARR Central Asia event
loss tables is calibrated to its bond trigger rate (the highest-loss events up to that
rate), and the catalogue is searched for events that trigger both countries. For the
KGZ-TJK pair, no catalogue earthquake triggers both (397 and 402 events trigger each;
exact Poisson 95% upper bound 0.12 times the bond's shared rate), and one flood event
triggers both rain covers (2.5% of the smaller rate). The bond documents imply 1 times
the base rate and the organizer's stress scenario 2 times. Over the evidence range (0 to 1
times, 0 to 25% rain) the certified reserve at 1.5% is USD 80m; the USD 7.5m uplift comes
only from the doubled stress scenario. Secondary uncertainty (each event's loss standard
deviation, as a gamma loss) is reported as a sensitivity.

**Financing.** The certified reserve R* fixes the money that must be available. The
optimizer stacks a cash reserve [0, a1), contingent credit [a1, a2) and parametric
insurance [a2, R*) and minimises the expected cost over the term, following the sovereign
risk-layering framework of Clarke, Mahul, Poulter and Teh (World Bank WPS 7721, 2016;
Geneva Papers 42, 2017). Defaults, all editable: 4.3% a year on idle cash; ADB Contingent
Disaster Financing fees of 0.25% front-end and 0.25% a year on the undrawn balance
(Crisis Preparedness and Response Toolkit, 2026); 2.84% a year for using cheap borrowing
room and 0.972 per drawn dollar (Clarke et al. case study); an insurance multiple of 2.6
(ADB's 2026 Kyrgyz Republic bond: 6.00% risk margin over 2.296% expected loss). With a
credit cap the problem is solved over every admissible pair; without one the cost
separates and a prefix-minimum scan is exact. Tests check both against brute force.
Pooling compares each country alone with the pool at the same limit and splits the pooled
reserve pro rata or by each member's expected payout in the shortfall outcomes.

**QAOA.** The choice of the two breakpoints from six candidate levels is a 12-variable
QUBO (one-hot registers, ordering penalty) mapped to an Ising Hamiltonian. CVaR-QAOA
(Barkoutsos et al., Quantum 4, 256, 2020) with three layers is trained on the exact
simulated state and sampled with seeded shots on Qiskit Aer. A ring XY mixer with W-state
registers keeps every state one-hot (Hadfield et al., Algorithms 12, 34, 2019). Brute
force over all 4,096 bitstrings checks the answer.

**Surrogate.** Gaussian-process regression (Matern 5/2 kernel, hyperparameters by
marginal likelihood; Rasmussen and Williams 2006) on log10 P(L > level) at each candidate
reserve level, with the AK-MCS U criterion for active learning (Echard, Gayton and
Lemaire, Structural Safety 33, 2011): evaluate where the reserve level is least certain,
stop when every grid point is classified at U >= 2. It is for exploration; the certified
bounds never use it.

**Noise.** A 6-qubit amplitude-estimation circuit for the public payout law, compiled to
cz/rz/sx/x and simulated with Aer's density-matrix method under a noise model from median
IBM Heron r2 calibration data (CZ 3.2e-3, single-qubit 2.3e-4, readout 1.0%, T1 197 us,
T2 118 us), scaled down to show the effect of better hardware. Standard maximum-likelihood
estimation is compared with a noise-aware fit of the amplitude, a decay rate and the
noise floor (after Tanaka et al., Quantum Inf. Process. 20, 293, 2021). The error budget
uses the compiled CX counts of the full tensor-chain circuits.

## Validate the proposal figures

```sh
python -m liquidity_reserves.validation          # writes results/validation.json and .md
python -m liquidity_reserves.validation --quick  # skips the 10- and 20-country rows
```

Every figure is recomputed by the engine and compared with the technical proposal.
[`results/validation.md`](results/validation.md) lists each target, the computed value
and any discrepancy.

## Run the official benchmark

The organizer's evaluator needs Linux or macOS (WSL works on Windows). Install the
pinned dependencies and the vendored, unmodified benchmark package into the interpreter
the evaluator will use, then run both suites:

```sh
python -m pip install -r requirements.txt ./benchmark
PYTHON=python scripts/run_benchmark.sh runs/benchmark
```

The script verifies `benchmark/SHA256SUMS`, packages the seven public scenarios as a
suite, generates the six seeded practice cases (`qjlb holdout --seed 20261113`), and runs
`qjlb evaluate ... --execution classical_cpu --trusted-code -- python worker.py` with 10
repeats on each. Reports and timings stay in the evaluator output (`report.json`,
`summary.csv`); `summary.json` counts passes and failures. Recorded results are in
[`results/benchmark`](results/benchmark).

The worker can also be called directly:

```sh
python worker.py --request request.json --output result.json
```

## Recorded results

| Check | Result |
|---|---|
| Official evaluator, 7 public + 6 practice cases, 10 repeats | 13/13 cases, 65/65 metric checks, 130/130 runs valid |
| Two-bond base reserve, 1.5% three-year limit | USD 80m |
| Two-bond reserve across the dependence range, 1.5% | USD 87.5m, 7 distribution evaluations |
| Two-bond reserve across the dependence range, 0.5% | USD 105m, 39 evaluations (16x16 grid: 517) |
| Synthetic chain, 5 / 10 / 20 countries, 1.5% | USD 136.25m / 191.25m-191.5m / 289m-290m |
| Contraction vs organizer's exact reference and independent state-space solver | agree to < 1e-15 |
| Amplitude estimation vs Monte Carlo, P(L > $80m), 103,200 queries (ideal simulator) | RMSE 2.8e-5 vs 3.4e-4 |
| Quantum reserve search on the full simulated circuit, 1.5% | $80m, matching the classical engine |
| Range evidence, World Bank catalogue, KGZ-TJK | 0 joint earthquake triggers, 1 joint rain trigger; reserve across the evidence range USD 80m at 1.5% |
| Cheapest funding stack, two-bond, 1.5% | credit to USD 70m, insurance USD 70m-87.5m; 16.1% below an all-cash reserve |
| Pooling benefit, two-bond / five-country chain, 1.5% | 37.5% / 59.9% less capital than each country alone |
| QAOA, 12 qubits, XY mixer, 3 layers | optimal stack in 24.9% of shots (random guess 0.02%); standard X mixer 2.3% |
| ML surrogate of the 21x21 map, 2 to 20 countries | identical to the exact map with 13 to 32 contractions instead of 441 |
| Amplitude estimation under Heron r2 noise, 6 qubits | RMSE 7.4e-3 (noise-aware 4.5e-3); 1.5e-3 at 10x lower error; 2.5e-4 at 100x lower |
| Error rate the full two-bond circuit needs, Grover depth 16 / 1,024 | 1.1e-5 / 1.8e-7 two-qubit error (today about 3e-3) |

ADB targets are term probabilities and USD over the bond term; World Bank targets are
annual event rates and USD per year. They are computed and reported separately.

## Environment and hardware

**Hardware.** One CPU workstation: AMD Ryzen 5 9600X (6 cores, 12 threads), 31 GB RAM.
No GPU, no quantum hardware and no cloud service were used.

**Operating systems and Python.** Tests, validation, the app and the quantum check ran on
Windows 10 Pro with Python 3.12.10. The organizer's evaluator, which needs POSIX, ran
under WSL2 Ubuntu (Linux 6.18, glibc 2.39) with Python 3.12.3. CI runs the tests on
Ubuntu and Windows with Python 3.10 and 3.12.

**Dependencies.** Pinned versions used for every recorded result:

| File | Contents |
|---|---|
| `requirements.txt` | numpy 1.26.4, scipy 1.15.3 (the engine, worker and app need nothing else) |
| `requirements-dev.txt` | adds pytest 9.2.0 and ruff 0.17.5 |
| `requirements-quantum.txt` | adds Qiskit 2.2.3, Qiskit Aer 0.17.2 and their dependencies, to build, simulate and compile the quantum circuits |
| `benchmark/pyproject.toml` | the organizer's package (`qjlb` 0.5.0), vendored unmodified |

**Classical computation.** The reserve engine, the benchmark worker and all recorded
reserve figures run on the CPU. The tensor-network contraction is the quantum-inspired
part. The benchmark entry is declared `classical_cpu` with zero shots and qubits.

**Noise model and QAOA.** The noise study uses Aer's density-matrix method with a
noise model built from median IBM Heron r2 calibration figures (no IBM account or
hardware). QAOA circuits are trained on Qiskit's exact statevector and sampled with Aer's
`SamplerV2` (4,000 seeded shots), compiled to rz, sx, x and cx.

**Simulator.** The quantum algorithm runs on Qiskit Aer 0.17.2's statevector simulator:
every qubit of the 21-qubit two-bond circuit is simulated, with no noise model and no
error mitigation, and shots use fixed seeds (from 20261113). Loading and arithmetic checks
use Qiskit's exact statevector. The query-scaling study in the app uses a NumPy
statevector of the same amplitude rotation. Resource counts come from the Qiskit 2.2.3
transpiler targeting rz, sx, x and cx gates with all-to-all connectivity (optimization
level 1, seed 7).

## Sample outputs

| File | What it is |
|---|---|
| [`results/samples/public-two-bond-1.5pct-report.pdf`](results/samples/public-two-bond-1.5pct-report.pdf) | PDF reserve report for the public two-bond example at the 1.5% limit |
| [`results/samples/synthetic-5-countries-1.5pct-report.pdf`](results/samples/synthetic-5-countries-1.5pct-report.pdf) | PDF reserve report for the synthetic five-country chain |
| [`results/samples/public-two-bond-1.5pct.json`](results/samples/public-two-bond-1.5pct.json), [`.md`](results/samples/public-two-bond-1.5pct.md) | JSON and Markdown exports of the same calculation |
| [`results/validation.md`](results/validation.md) | Every proposal figure recomputed, with discrepancies |
| [`results/benchmark/`](results/benchmark) | Official evaluator reports for the public and practice suites |
| [`results/quantum.json`](results/quantum.json) | Quantum circuit verification, reserve search, bound confirmation, coverage and resource counts |
| [`results/evidence.json`](results/evidence.json) | World Bank catalogue evidence for the shared-shock range |
| [`results/layering.json`](results/layering.json) | Financing stacks, sensitivity, cost frontier, pooling benefit and QAOA runs |
| [`results/surrogate.json`](results/surrogate.json) | ML surrogate against the exact map, 2 to 20 countries |
| [`results/noise.json`](results/noise.json) | Amplitude estimation under Heron-class noise and the error budget |
| [`docs/screenshots/`](docs/screenshots) | The Decision, Scenarios, Evidence and Method views |

![Where the decision binds: refinement cells for the synthetic five-country chain](docs/screenshots/synthetic-five-countries.jpg)

## Layout

```
src/liquidity_reserves/   engine, insights, quantum check, worker, validation, app
worker.py                 evaluation-v1 entry point for the organizer's evaluator
Dockerfile                container image for the app and studies
benchmark/                organizer's benchmark, vendored unmodified (see SHA256SUMS)
scripts/                  public-suite packaging, evaluator run, report summary
tests/                    pytest suite
results/                  recorded validation, evaluator, quantum, evidence, financing, surrogate and noise reports
results/samples/          sample PDF reports and exports
docs/screenshots/         screenshots of the app
```

## Method notes

- Exceedance is strict: a payout exactly equal to the reserve is covered.
- Shared earthquake and precipitation sources sit on each neighbouring pair. Private
  rates equal marginal rates minus incident shared rates, so marginals stay fixed.
- Lower bound: a feasible scenario (range corner, cell centre or base point) whose
  exceedance is above the limit. Upper bound: per cell, a dominating model with the
  cell's highest shared and private rates bounds every feasible scenario in the cell.
- Public examples use exact amounts (integer quarter-cents). When the payout lattice
  would be too large, payouts are rounded down for feasible scenarios and up for
  dominating models, and the rounding bound is reported.
- One distribution evaluation is one aggregate payout distribution (one contraction).

## Licence

Copyright 2026 Mark Andrei Condino. Licensed under the Apache License 2.0
([`LICENSE`](LICENSE), [`NOTICE`](NOTICE)).

## Sources and licences

`benchmark/` is the organizer's joint-loss baseline: code under Apache 2.0
(`benchmark/LICENSE`), World Bank data under CC BY 4.0 (`benchmark/data/LICENSE.txt`,
`benchmark/data/source-manifest.json`). The public bond inputs cite ADB's 2026 offering
documents (`benchmark/data/adb_terms.json`). Financing defaults cite the World Bank
working paper WPS 7721 (Clarke, Mahul, Poulter and Teh, 2016), ADB's Crisis Preparedness
and Response Toolkit (2026) and the Artemis deal directory entries for ADB's 2026 bonds. The organizer's `.gitignore` listed in
`SHA256SUMS` was not in the distributed copy; every other listed file matches.
