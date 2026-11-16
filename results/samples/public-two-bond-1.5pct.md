# Reserve report: Public two-bond example (ADB 2026 KGZ and TJK bonds)

Sizing Disaster Liquidity Reserves under Overlapping Regional Shocks. Prepared with the reserve engine 0.1.0 on 2026-11-16T15:16:42+00:00. Applicant: Mark Andrei Condino (sole applicant).

## Result

- Risk limit: 1.5% probability that total payouts over 3 years are strictly greater than the reserve
- Base reserve: $80,000,000
- Reserve across the range: $87,500,000
- Status: Resolved: the lower and upper reserve bounds agree.
- Distribution evaluations: 7 (adaptive); 517 for the uniform 16x16 grid ($87,500,000)

## Why a smaller reserve falls short

Under shared earthquake rate 2 x base, shared rain 25% of the smaller neighbouring rain rate, total payouts exceed $85,000,000 with probability 1.607%, above the 1.5% limit. Payouts take no value between $85,000,000 and $87,500,000, so every smaller reserve falls short in this feasible scenario.

## Why the upper amount is sufficient

The range was divided into 1 cell. For each cell a dominating model uses the cell's highest shared rates and highest private rates. Every feasible assumption in the cell is a thinning of that model, and capped payouts never fall when events are added, so its exceedance is an upper bound for the whole cell. At $87,500,000 the largest cell bound is 1.023%, within the 1.5% limit, so this reserve is sufficient across the range.

## Inputs

Shared earthquake multiplier: 0 to 2. Shared rain share: 0% to 25%. Base point: 1 and 25%.

| Country | Earthquake rate / yr | Rain rate / yr | Earthquake cap | Rain payment |
|---|---|---|---|---|
| KGZ | 0.039604 | 0.020203 | $70,000,000 | $10,000,000 |
| TJK | 0.040041 | 0.020203 | $70,000,000 | $10,000,000 |

## Key amounts

| Amount | Base P(L > amount) | Highest feasible | Certified bound |
|---|---|---|---|
| $80,000,000 | 1.205% | 1.607% | 1.831% |
| $85,000,000 | 1.205% | 1.607% | 1.831% |
| $87,500,000 | 0.660% | 0.867% | 1.023% |

## Assumptions

- Public two-bond inputs from the organizer's benchmark calibration of ADB's 2026 Kyrgyz Republic and Tajikistan disaster relief bonds. The range and risk limit are demonstration inputs.
- Term of 3 years. Earthquake and precipitation triggers are independent Poisson processes with stationary annual rates; perils are independent.
- Each earthquake trigger pays 1 to 4 quarter steps of the country's earthquake cap (severity mix from the public calibration); payments accumulate to the cap. Precipitation pays its full amount once over the term.
- Each neighbouring pair has its own shared earthquake and shared precipitation source. A shared earthquake triggers both neighbours with independent severity marks; a shared precipitation event triggers both payments.
- Each country's private rate equals its marginal rate minus the shared rates of its pairs, so marginal event frequencies are the same at every assumption in the range.
- Range: shared earthquake rate 0 to 2 times the base shared rate; shared precipitation rate 0% to 25% of the smaller neighbouring marginal rate. The same scalars apply to every pair.
- Risk limit: probability that total payouts over the term are strictly greater than the reserve is at most 1.5%. A payout exactly equal to the reserve is covered.
- Exact amounts on a $2,500,000 payout lattice (65 payout levels).
- Probabilities carry a 1e-12 floating-point allowance: a scenario within that distance of the limit does not decide a bound.

## Validation status

- Base payout probabilities sum to one: pass (total probability 1.000000000000000)
- Lower bound does not exceed upper bound: pass ($87,500,000 <= $87,500,000)
- Supporting scenario lies in the range and preserves marginal rates: pass (shared earthquake rate 2 x base, shared rain 25% of the smaller neighbouring rain rate)
- Certified bound is never below an observed feasible scenario: pass (largest (observed - bound) 0.00e+00)
- Independent state-space solver agrees at the base and supporting scenarios: pass (max abs exceedance difference 5.55e-17)
- Recorded validation (2026-11-16T15:15:26+00:00): 55 of 55 checks agree with the proposal targets
- Official evaluation-v1 evaluator (classical_cpu): 13/13 cases and 65/65 metric checks pass
