// Printable reserve report. The inputs come from the app through local
// storage; every number is recalculated by the engine for this page.
import {
  assumptionMap,
  driverBars,
  el,
  exceedanceChart,
  fmt,
  proofCostNote,
  rampLegend,
} from "./charts.js";

const $ = (id) => document.getElementById(id);
const REPORT_KEY = "reservebound:report";

function readPayload() {
  try {
    const raw = window.localStorage.getItem(REPORT_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function table(headers, rows) {
  return el("table", {}, [
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", { scope: "col", text: h })))),
    el("tbody", {}, rows.map((cells) => el("tr", {}, cells.map((c) => el("td", { text: String(c) }))))),
  ]);
}

function stat(label, value, note) {
  return el("div", {}, [el("dt", { text: label }), el("dd", { text: value }), el("p", { text: note })]);
}

function render(r) {
  const pool = r.inputs.pool;
  const h = r.headline;
  const range = r.range_result;
  const generated = new Date(r.generated_utc);
  document.title = `ReserveBound Report: ${pool.name}`;
  $("report-meta").replaceChildren(
    el("div", { text: `Prepared ${generated.toUTCString()}` }),
    el("div", { text: `Engine ${r.engine_version}` }),
    el("div", { text: r.applicant }),
  );
  $("report-title").textContent = pool.name;
  $("report-synthetic").hidden = !pool.synthetic;
  $("r-eyebrow").textContent = `Reserve to hold · ${pool.term_years}-year term · ${fmt.pct(h.risk_limit, 2)} risk limit`;
  $("r-value").textContent = range.resolved ? range.upper.text : `${range.lower.text} to ${range.upper.text}`;
  $("r-text").textContent = range.resolved
    ? `The smallest reserve that keeps the chance of payouts exceeding it at or below ${fmt.pct(h.risk_limit, 2)} for every shared-shock assumption in the range.`
    : `The smallest sufficient reserve lies in this bracket. ${r.status_text}`;
  const sign = h.uplift_sign > 0 ? "+" : h.uplift_sign < 0 ? "-" : "";
  $("r-stats").replaceChildren(
    stat("Base assumptions", h.base.text, `Exceeded with probability ${fmt.pct(r.base.exceedance_at_reserve)}`),
    stat("Dependence uplift", `${sign}${h.uplift.text}`, h.uplift_percent === null ? "" : `${sign}${Math.abs(h.uplift_percent).toFixed(1)}% over the base reserve`),
    stat("Proof cost", `${h.evaluations} distribution evaluations`, proofCostNote(r)),
  );
  $("r-shortfall").textContent = r.supporting_scenario.shortfall
    ? r.supporting_scenario.shortfall.explanation
    : "A zero reserve meets the limit in every evaluated scenario.";
  $("r-sufficiency").textContent = r.sufficiency.explanation;
  $("r-curve-legend").replaceChildren(
    ...[
      ["s-base", "Base assumptions"],
      ["s-observed", "Highest feasible scenario"],
      ["s-bound", "Certified upper bound"],
      ["s-limit", "Risk limit"],
    ].map(([cls, label]) => el("span", { class: "legend-item" }, [el("span", { class: `swatch ${cls}` }), label])),
  );
  exceedanceChart($("r-curve"), r.chart, { interactive: false, height: 300 });
  const { lo, hi } = assumptionMap($("r-map"), r.insights.map, { interactive: false, height: 300 });
  rampLegend($("r-map-legend"), lo, hi);
  $("r-map-note").textContent = `Reserve needed at each assumption; outlines show the ${r.insights.map.cells.length} refinement cells behind the certified bound.`;
  const drivers = r.insights.drivers;
  $("r-drivers-note").textContent = `Expected payout by country when payouts exceed ${fmt.usdM(drivers.amount_usd)} in the supporting scenario (probability ${fmt.pct(drivers.probability_exceeded)}).`;
  driverBars($("r-drivers"), drivers, { interactive: false });
  $("r-key").replaceChildren(
    table(
      ["Reserve", "Base", "Highest feasible", "Certified bound", "E[payout above], base", "Meets limit everywhere"],
      r.key_amounts.map((k) => [
        k.amount.text,
        fmt.pct(k.base_exceedance),
        fmt.pct(k.observed_exceedance),
        fmt.pct(k.bound_exceedance),
        fmt.usd(k.base_expected_excess_usd),
        k.meets_limit_everywhere ? "Yes" : "No",
      ]),
    ),
  );
  const rng = r.inputs.range;
  $("r-range").textContent = `Shared earthquake rate ${rng.earthquake_multiplier[0]}x to ${rng.earthquake_multiplier[1]}x the base shared rate; shared rain ${(100 * rng.rain_share[0]).toFixed(1)}% to ${(100 * rng.rain_share[1]).toFixed(1)}% of the smaller neighbouring rate. Base point ${pool.base_point.earthquake_multiplier}x and ${(100 * pool.base_point.rain_share).toFixed(1)}%. ${r.payout_register.description}`;
  $("r-countries").replaceChildren(
    table(
      ["Country", "Earthquake triggers / yr", "Rain triggers / yr", "Earthquake cap", "Rain payment"],
      pool.countries.map((c) => [`${c.code} ${c.name}`, c.earthquake_rate_per_year.toFixed(6), c.rain_rate_per_year.toFixed(6), c.earthquake_cap, c.rain_payment]),
    ),
  );
  $("r-assumptions").replaceChildren(...r.assumptions.map((a) => el("li", { text: a })));
  const marks = { true: "Pass", false: "Fail", null: "Not run" };
  $("r-checks").replaceChildren(
    ...r.checks.map((c) => el("li", {}, [el("strong", { text: `${marks[String(c.passed)]}: ` }), el("span", { text: `${c.check} (${c.detail})` })])),
  );
  const recorded = r.recorded_validation;
  const lines = [];
  if (recorded.validation) {
    lines.push(`Recorded validation: ${recorded.validation.passed} of ${recorded.validation.checks} checks agree with the technical proposal and independent calculations; ${recorded.validation.discrepancies.length} discrepancies are documented.`);
  }
  if (recorded.benchmark) {
    const t = recorded.benchmark.totals;
    lines.push(`Official evaluation-v1 evaluator (classical_cpu): ${t.cases_passing} of ${t.cases} cases and ${t.metric_passes} of ${t.metric_checks} metric checks pass.`);
  }
  $("r-recorded").replaceChildren(...lines.map((text) => el("p", { class: "hint", text })));
  $("r-foot").textContent = `${r.project}. Prepared with ReserveBound by ${r.applicant}. Results hold under the stated assumptions and are demonstrations, not advice for a live financing decision. Validate regional assumptions, payout terms and the risk limit before use.`;
}

async function main() {
  const payload = readPayload();
  if (!payload) {
    $("report-state").textContent = "No calculation to report. Open the app, calculate, then choose Export, PDF report.";
    return;
  }
  const response = await fetch("/api/calculate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) {
    $("report-state").textContent = `The report could not be prepared: ${(body.errors || ["unknown error"]).join("; ")}`;
    return;
  }
  $("sheet").hidden = false;
  render(body);
  $("report-state").textContent = "Report ready. Use Save as PDF, then choose 'Save as PDF' as the printer.";
  $("print").disabled = false;
  $("print").addEventListener("click", () => window.print());
}

main().catch((error) => {
  $("report-state").textContent = `The report could not be prepared: ${error.message}`;
});
