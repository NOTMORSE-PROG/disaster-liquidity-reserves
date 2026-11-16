# Validation report

Generated 2026-11-16T12:42:10+00:00 by engine 0.1.0 (full run). Mark Andrei Condino (sole applicant).

41 of 43 checks agree with their targets.

| Group | Check | Proposal / target | Computed | Status | Note |
|---|---|---|---|---|---|
| Public two-bond example | Base reserve at the 1.5% three-year limit | $80,000,000 | $80,000,000 | pass |  |
| Public two-bond example | Reserve across the dependence range at 1.5% | $87,500,000 | $87,500,000 | pass |  |
| Public two-bond example | 1.5% bracket resolved | True | True | pass |  |
| Public two-bond example | Adaptive distribution evaluations at 1.5% | 7 | 7 | pass |  |
| Public two-bond example | Reserve across the dependence range at 0.5% | $105,000,000 | $105,000,000 | pass |  |
| Public two-bond example | 0.5% bracket resolved | True | True | pass |  |
| Public two-bond example | Adaptive distribution evaluations at 0.5% | 39 | 39 | pass |  |
| Public two-bond example | 16x16 grid distribution evaluations at 0.5% | 517 | 517 | pass |  |
| Public two-bond example | 16x16 grid distribution evaluations at 1.5% | 517 | 517 | pass |  |
| Public two-bond example | 16x16 grid bracket width at 0.5% | $2,500,000 (proposal: 'left a $2.5m bracket') | $0 wide ($105,000,000) | discrepancy | The 16x16 dominating-cell grid resolves 0.5% to $105,000,000. A $2.5m bracket ($105,000,000 to $107,500,000) comes from the 200x200 grid with the Poisson coupling bound instead. |
| Public two-bond example | P(payout > $80m), base | 1.205% | 1.205% | pass |  |
| Public two-bond example | P(payout > $80m), highest feasible scenario | 1.607% | 1.607% | pass | at earthquake multiplier 2, rain share 0.25 |
| Public two-bond example | P(payout > $80m), bounded (200x200 grid plus coupling bound) | 1.631% | 1.631% | pass | coupling allowance 2.471e-04; the adaptive cell bounds after the 0.5% refinement give 1.634% |
| Public two-bond example | Expected payout above $80m, base | $282,355 | $282,354.70 | pass |  |
| Public two-bond example | Expected payout above $80m, highest feasible scenario | $385,043 | $385,043.37 | pass |  |
| Independent checks | Coupling-grid envelope reserve at 1.5% (Markov generator laws) | brackets the adaptive result | $87,500,000 to $87,500,000 | pass |  |
| Independent checks | Coupling-grid envelope reserve at 0.5% (Markov generator laws) | brackets the adaptive result | $105,000,000 to $107,500,000 | pass |  |
| Independent checks | Reserve bounds at 1.5% recomputed with the state-space solver | $87,500,000 | $87,500,000 | pass | 7 evaluations |
| Independent checks | Reserve bounds at 0.5% recomputed with the state-space solver | $105,000,000 | $105,000,000 | pass | 39 evaluations |
| Public two-bond example | Expected principal loss, base | near $10.06 million | $10,063,513 | pass |  |
| Public two-bond example | Expected principal loss, common shocks removed | near $10.06 million | $10,063,513 | pass |  |
| Public two-bond example | Joint attachment, base | 4.41% | 4.41% | pass |  |
| Public two-bond example | Joint attachment, common shocks removed | 2.72% | 2.72% | pass |  |
| Synthetic chain, 5 countries | Reserve across the dependence range at 1.5% | $136,250,000 | $136,250,000 | pass | gap tolerance $0; status resolved |
| Synthetic chain, 5 countries | Adaptive distribution evaluations | 567 | 567 | pass |  |
| Synthetic chain, 5 countries | 16x16 grid distribution evaluations | 517 | 517 | pass |  |
| Synthetic chain, 10 countries | Reserve across the dependence range at 1.5% | $191,250,000 to $191,500,000 | $191,250,000 to $191,500,000 | pass | gap tolerance $250,000; status within_gap |
| Synthetic chain, 10 countries | Adaptive distribution evaluations | 487 | 487 | pass |  |
| Synthetic chain, 10 countries | 16x16 grid distribution evaluations | 517 | 517 | pass |  |
| Synthetic chain, 20 countries | Reserve across the dependence range at 1.5% | $289,000,000 to $290,000,000 | $289,000,000 to $290,000,000 | pass | gap tolerance $1,000,000; status within_gap |
| Synthetic chain, 20 countries | Adaptive distribution evaluations | 379 | 379 | pass |  |
| Synthetic chain, 20 countries | 16x16 grid distribution evaluations | 517 | 517 | pass |  |
| Independent checks | Payout exceedance curves vs organizer's exact reference (5 ADB scenarios) | agree to numerical precision | max abs difference 2.78e-16 | pass | adb-base, adb-common-shock, adb-independent, adb-rain-cover, adb-rain-stress |
| Independent checks | Tensor contraction vs state-space solver at 36 interior points (2, 3, 5 countries) | 36 points agree to numerical precision | max abs difference 3.33e-16 | pass |  |
| Independent checks | Dominating cell bound never below the state-space exceedance (36 points) | no violation | largest (exact - bound) 0.00e+00 | pass |  |
| Independent checks | Each country's marginal payout law is unchanged across the range corners (5 countries) | unchanged | max abs difference 5.55e-16 | pass |  |
| Resource estimate | Peak traced memory of one contraction, 2 synthetic countries | 0.06 MiB | 0.06 MiB | pass | 598 payout levels; tracemalloc peak during factor construction and contraction |
| Resource estimate | Peak traced memory of one contraction, 5 synthetic countries | 0.40 MiB | 0.39 MiB | pass | 1,531 payout levels; tracemalloc peak during factor construction and contraction |
| Resource estimate | Peak traced memory of one contraction, 10 synthetic countries | 0.79 MiB | 0.79 MiB | pass | 3,077 payout levels; tracemalloc peak during factor construction and contraction |
| Resource estimate | Peak traced memory of one contraction, 20 synthetic countries | 1.58 MiB | 1.58 MiB | pass | 6,189 payout levels; tracemalloc peak during factor construction and contraction |
| Resource estimate | Peak traced memory of one contraction, 50 synthetic countries | 3.95 MiB | 3.96 MiB | pass | 15,489 payout levels; tracemalloc peak during factor construction and contraction |
| Benchmark | Metric checks passed (public scenarios and seeded practice cases) | 65 of 65 | 65 of 65 | pass | 13/13 cases; 130/130 team runs valid |
| Benchmark | Largest error as a share of its allowed tolerance | 2.1e-13 | 5.15e-13 | discrepancy | Evaluator run on Linux-6.18.58.1-microsoft-standard-WSL2-x86_64-with-glibc2.39. The exact value is floating-point rounding and changes with platform and BLAS; every case stays below 1e-12 of its tolerance. |

## Reserve brackets and evaluation counts

| Portfolio / limit | Adaptive | Evaluations | 16x16 grid | Evaluations |
|---|---|---|---|---|
| Public two-bond / 1.5% | $87,500,000 | 7 | $87,500,000 | 517 |
| Public two-bond / 0.5% | $105,000,000 | 39 | $105,000,000 | 517 |
| Synthetic 5 countries / 1.5% | $136,250,000 | 567 | $136,250,000 to $136,750,000 | 517 |
| Synthetic 10 countries / 1.5% | $191,250,000 to $191,500,000 | 487 | $191,250,000 to $192,750,000 | 517 |
| Synthetic 20 countries / 1.5% | $289,000,000 to $290,000,000 | 379 | $289,000,000 to $291,500,000 | 517 |

## Peak traced memory of one contraction

| Countries | Payout levels | Measured MiB | Proposal MiB |
|---|---|---|---|
| 2 | 598 | 0.06 | 0.06 |
| 5 | 1,531 | 0.39 | 0.4 |
| 10 | 3,077 | 0.79 | 0.79 |
| 20 | 6,189 | 1.58 | 1.58 |
| 50 | 15,489 | 3.96 | 3.95 |

Counting rule: One distribution evaluation is one aggregate payout distribution. Adaptive refinement counts the base point, each distinct range corner, and a centre scenario plus a dominating model for every cell created. The k-by-k grid counts the base point, four corners and 2k^2 cell evaluations (517 for k = 16).
