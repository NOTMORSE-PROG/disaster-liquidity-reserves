"use strict";

// Every number shown here comes from the server's engine response; the page
// only formats and draws it.

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const FIELDS = [
  "risk_limit_percent",
  "earthquake_multiplier_low",
  "earthquake_multiplier_high",
  "rain_share_percent_low",
  "rain_share_percent_high",
  "base_earthquake_multiplier",
  "base_rain_share_percent",
  "term_years",
  "gap_usd",
  "max_evaluations",
  "grid_subdivisions",
];
const SERIES = {
  base: "Base assumptions",
  observed: "Highest feasible scenario",
  bound: "Certified upper bound",
};

const state = { defaults: null, lastPayload: null, lastResult: null, dirty: false };

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null) continue;
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  return node;
}

const pct = (p, digits = 3) => (p === null || p === undefined ? "n/a" : `${(100 * p).toFixed(digits)}%`);
const usdM = (usd) => `$${(usd / 1e6).toLocaleString("en-US", { maximumFractionDigits: 2 })}m`;
const usd = (value) => `$${Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const type = response.headers.get("Content-Type") || "";
  const body = type.includes("json") ? await response.json() : await response.blob();
  if (!response.ok) {
    const errors = body && body.errors ? body.errors : [`Request failed (${response.status})`];
    const error = new Error(errors.join("; "));
    error.messages = errors;
    throw error;
  }
  return { body, response };
}

// --- form -----------------------------------------------------------------

function fillForm(defaults) {
  state.defaults = defaults;
  $("example").value = defaults.example;
  $("synthetic_countries").value = defaults.synthetic_countries;
  $("countries-field").hidden = defaults.example !== "synthetic-chain";
  $("synthetic-badge").hidden = !defaults.synthetic;
  $("example-description").textContent = defaults.description;
  for (const name of FIELDS) $(name).value = defaults[name];
  $("compare_grid").checked = Boolean(defaults.compare_grid);
  const rows = $("country-rows");
  rows.replaceChildren();
  defaults.countries.forEach((country, i) => {
    const cell = (key, step) =>
      el("td", {}, [
        el("input", {
          type: "number",
          step,
          min: "0",
          "data-row": String(i),
          "data-key": key,
          "aria-label": `${country.code} ${key.replaceAll("_", " ")}`,
          value: String(country[key]),
        }),
      ]);
    rows.append(
      el("tr", {}, [
        el("th", { scope: "row", text: country.code, title: country.name }),
        cell("earthquake_rate", "any"),
        cell("rain_rate", "any"),
        cell("earthquake_cap_usd", "0.01"),
        cell("rain_payment_usd", "0.01"),
      ]),
    );
  });
  clearErrors();
}

function readForm() {
  const payload = {
    example: $("example").value,
    synthetic_countries: $("synthetic_countries").value,
    compare_grid: $("compare_grid").checked,
  };
  for (const name of FIELDS) payload[name] = $(name).value;
  const countries = state.defaults.countries.map((c) => ({ code: c.code }));
  for (const input of $("country-rows").querySelectorAll("input")) {
    countries[Number(input.dataset.row)][input.dataset.key] = input.value;
  }
  payload.countries = countries;
  return payload;
}

function clearErrors() {
  $("errors").hidden = true;
  $("errors").replaceChildren();
  for (const input of document.querySelectorAll("[aria-invalid]")) input.removeAttribute("aria-invalid");
}

function showErrors(messages) {
  const box = $("errors");
  box.replaceChildren(
    el("strong", { text: "Please correct these inputs:" }),
    el("ul", {}, messages.map((m) => el("li", { text: m }))),
  );
  box.hidden = false;
  box.focus?.();
}

let defaultsTicket = 0;

async function loadDefaults(example, countries, keepSettings = false) {
  // Responses can arrive out of order; only the newest request may fill the form.
  const ticket = ++defaultsTicket;
  const query = new URLSearchParams({ example, countries: String(countries || 5) });
  const { body } = await api(`/api/defaults?${query}`);
  if (ticket !== defaultsTicket) return;
  const kept = Object.fromEntries(FIELDS.map((name) => [name, $(name).value]));
  const grid = $("compare_grid").checked;
  fillForm(body);
  if (keepSettings) {
    // A new country count rebuilds the country table only; the analyst's
    // risk limit, ranges and calculation settings stay as entered.
    for (const name of FIELDS) $(name).value = kept[name];
    $("compare_grid").checked = grid;
  }
}

// --- results ----------------------------------------------------------------

function render(result) {
  state.lastResult = result;
  $("placeholder").hidden = true;
  $("result-body").hidden = false;

  const status = $("status");
  status.className = `status ${result.resolved ? "ok" : "warn"}`;
  status.textContent = result.status_text;

  $("messages").replaceChildren(
    ...result.messages.map((m) => el("li", { class: m.level, text: m.text })),
  );

  const base = result.base;
  $("tile-base").textContent =
    base.reserve.usd === base.reserve_upper.usd
      ? base.reserve.text
      : `${base.reserve.text} to ${base.reserve_upper.text}`;
  $("tile-base-note").textContent = `Exceeded with probability ${pct(base.exceedance_at_reserve)} at the base assumptions`;
  const range = result.range_result;
  $("tile-range").textContent = range.resolved ? range.upper.text : `${range.lower.text} to ${range.upper.text}`;
  $("tile-range-note").textContent = range.resolved
    ? "Smallest reserve meeting the limit for every assumption in the range"
    : `Unresolved bracket, width ${range.gap.text}`;
  const evals = result.evaluations;
  $("tile-evals").textContent = String(evals.adaptive.total);
  $("tile-evals-note").textContent = evals.grid
    ? `Adaptive refinement vs ${evals.grid.evaluations} for the ${evals.grid.method} (${
        evals.grid.resolved ? evals.grid.upper.text : `${evals.grid.lower.text} to ${evals.grid.upper.text}`
      })`
    : `Adaptive refinement (budget ${evals.budget})`;

  renderChart(result.chart);
  renderKeyTable(result);
  renderShortfall(result);
  renderSufficiency(result);
  $("assumptions").replaceChildren(...result.assumptions.map((a) => el("li", { text: a })));
  renderChecks(result);
  setDirty(false);
}

function renderKeyTable(result) {
  const head = el("tr", {}, [
    el("th", { scope: "col", text: "Reserve" }),
    el("th", { scope: "col", text: "Base" }),
    el("th", { scope: "col", text: "Highest feasible" }),
    el("th", { scope: "col", text: "Certified bound" }),
    el("th", { scope: "col", text: "Expected payout above (base)" }),
    el("th", { scope: "col", text: "Expected payout above (highest)" }),
    el("th", { scope: "col", text: "Meets limit everywhere" }),
  ]);
  const rows = result.key_amounts.map((row) =>
    el("tr", {}, [
      el("td", { text: row.amount.text }),
      el("td", { text: pct(row.base_exceedance) }),
      el("td", { text: pct(row.observed_exceedance) }),
      el("td", { text: pct(row.bound_exceedance) }),
      el("td", { text: usd(row.base_expected_excess_usd) }),
      el("td", { text: usd(row.observed_expected_excess_usd) }),
      el("td", { text: row.meets_limit_everywhere ? "Yes" : "No" }),
    ]),
  );
  $("key-table").replaceChildren(
    el("table", {}, [
      el("caption", { class: "hint", text: "Probability that payouts are strictly greater than each amount" }),
      el("thead", {}, head),
      el("tbody", {}, rows),
    ]),
  );
}

function rateTable(headers, rows) {
  return el("table", {}, [
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", { scope: "col", text: h })))),
    el(
      "tbody",
      {},
      rows.map((r) => el("tr", {}, r.map((v) => el("td", { text: v })))),
    ),
  ]);
}

function renderShortfall(result) {
  const scenario = result.supporting_scenario;
  $("shortfall-text").textContent = scenario.shortfall
    ? scenario.shortfall.explanation
    : "A zero reserve meets the limit in every evaluated scenario, so no smaller reserve exists.";
  const pairs = scenario.point.pairs.map((p) => [
    p.pair,
    p.shared_earthquake_per_year.toPrecision(4),
    p.shared_rain_per_year.toPrecision(4),
  ]);
  const detail = [
    rateTable(["Pair", "Shared earthquakes / yr", "Shared rain / yr"], pairs),
    rateTable(
      ["Country", "Private earthquakes / yr", "Private rain / yr"],
      scenario.private_rates.map((r) => [
        r.country,
        r.private_earthquake_per_year.toPrecision(4),
        r.private_rain_per_year.toPrecision(4),
      ]),
    ),
  ];
  if (scenario.shortfall) {
    detail.unshift(
      el("p", {
        class: "hint",
        text: `Smaller reserve ${scenario.shortfall.smaller_reserve.text}: exceeded with probability ${pct(
          scenario.shortfall.exceedance,
        )}; expected payout above it ${usd(scenario.shortfall.expected_excess_usd)}.`,
      }),
    );
  }
  $("shortfall-detail").replaceChildren(...detail);
}

function renderSufficiency(result) {
  const s = result.sufficiency;
  $("sufficiency-text").textContent = s.explanation;
  const cell = s.binding_cell;
  const rates = cell.dominating_rates;
  const countries = result.inputs.pool.countries.map((c) => c.code);
  $("sufficiency-detail").replaceChildren(
    el("p", {
      class: "hint",
      text: `Tightest cell: earthquake multiple ${cell.earthquake_multiplier[0].toFixed(3)} to ${cell.earthquake_multiplier[1].toFixed(
        3,
      )}, rain share ${(100 * cell.rain_share[0]).toFixed(2)}% to ${(100 * cell.rain_share[1]).toFixed(2)}%. Its dominating model:`,
    }),
    rateTable(
      ["Country", "Private earthquakes / yr", "Private rain / yr"],
      countries.map((code, i) => [
        code,
        rates.earthquake_private_per_year[i].toPrecision(4),
        rates.rain_private_per_year[i].toPrecision(4),
      ]),
    ),
  );
}

function renderChecks(result) {
  const marks = { true: ["pass", "Pass"], false: ["fail", "Fail"], null: ["skip", "Not run"] };
  $("checks").replaceChildren(
    ...result.checks.map((c) => {
      const [cls, label] = marks[String(c.passed)];
      return el("li", {}, [
        el("span", { class: `mark ${cls}`, text: label }),
        el("span", {}, [c.check, el("span", { class: "detail", text: c.detail })]),
      ]);
    }),
  );
  const recorded = result.recorded_validation;
  const parts = [];
  if (recorded.validation) {
    const v = recorded.validation;
    parts.push(
      el("p", {
        text: `Recorded validation (${v.generated_utc}): ${v.passed} of ${v.checks} checks agree with the technical proposal's figures and independent calculations.`,
      }),
    );
    if (v.discrepancies.length) {
      parts.push(
        el(
          "ul",
          { class: "assumptions" },
          v.discrepancies.map((d) =>
            el("li", { text: `Discrepancy: ${d.check}. Proposal: ${d.target}; computed: ${d.computed}. ${d.note}` }),
          ),
        ),
      );
    }
  } else {
    parts.push(el("p", { class: "hint", text: "No recorded validation found in results/." }));
  }
  if (recorded.benchmark) {
    const t = recorded.benchmark.totals;
    parts.push(
      el("p", {
        text: `Official evaluation-v1 evaluator, classical_cpu, ${recorded.benchmark.repeats} repeats: ${t.cases_passing} of ${t.cases} cases and ${t.metric_passes} of ${t.metric_checks} metric checks pass; ${t.valid_runs} of ${t.runs} runs valid.`,
      }),
    );
  }
  $("recorded").replaceChildren(...parts);
}

// --- chart --------------------------------------------------------------------

function niceStep(span, target) {
  const raw = span / target;
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / power;
  return power * (unit < 1.5 ? 1 : unit < 3 ? 2 : unit < 7 ? 5 : 10);
}

function stepValue(series, x) {
  // Exceedance is a right-continuous step function: value at the last point <= x.
  let lo = 0;
  let hi = series.x.length - 1;
  if (x < series.x[0]) return series.y[0];
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (series.x[mid] <= x) lo = mid;
    else hi = mid - 1;
  }
  return series.y[lo];
}

function renderChart(chart) {
  const host = $("chart");
  host.replaceChildren();
  const width = Math.max(host.clientWidth, 320);
  const height = 380;
  const m = { top: 58, right: 18, bottom: 40, left: 74 };
  const w = width - m.left - m.right;
  const h = height - m.top - m.bottom;
  const limit = chart.risk_limit;
  const positives = chart.series.flatMap((s) => s.y.filter((v) => v > 0));
  const yMin = 10 ** Math.floor(Math.log10(Math.max(Math.min(limit / 10, ...positives), 1e-7)));
  const yMax = 1;
  const xMax = chart.x_max_usd;
  const sx = (x) => m.left + (Math.min(x, xMax) / xMax) * w;
  const sy = (y) => m.top + ((Math.log10(yMax) - Math.log10(Math.max(y, yMin))) / (Math.log10(yMax) - Math.log10(yMin))) * h;

  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width, height });
  const grid = svg("g", { class: "grid" });
  const axis = svg("g", { class: "axis" });
  for (let e = Math.log10(yMin); e <= 0; e += 1) {
    const y = sy(10 ** e);
    grid.append(svg("line", { x1: m.left, x2: m.left + w, y1: y, y2: y }));
    axis.append(svg("text", { x: m.left - 8, y: y + 4, "text-anchor": "end", text: pct(10 ** e, e < -2 ? Math.min(-e - 2, 4) : 0) }));
  }
  const step = niceStep(xMax / 1e6, 6) * 1e6;
  for (let x = 0; x <= xMax + 1; x += step) {
    const px = sx(x);
    grid.append(svg("line", { x1: px, x2: px, y1: m.top, y2: m.top + h }));
    axis.append(svg("text", { x: px, y: m.top + h + 16, "text-anchor": "middle", text: usdM(x) }));
  }
  axis.append(svg("line", { x1: m.left, x2: m.left + w, y1: m.top + h, y2: m.top + h }));
  axis.append(svg("text", { x: m.left + w / 2, y: height - 4, "text-anchor": "middle", text: "Reserve (USD)" }));
  axis.append(
    svg("text", {
      x: 12,
      y: m.top + h / 2,
      "text-anchor": "middle",
      transform: `rotate(-90 12 ${m.top + h / 2})`,
      text: "P(payouts > reserve)",
    }),
  );
  root.append(grid, axis);

  // Risk limit line with a direct label.
  root.append(svg("line", { class: "limit", x1: m.left, x2: m.left + w, y1: sy(limit), y2: sy(limit) }));
  root.append(svg("text", { class: "direct-label", x: m.left + w - 4, y: sy(limit) - 6, "text-anchor": "end", text: `Risk limit ${pct(limit, 2)}` }));

  for (const series of chart.series) {
    let d = "";
    series.x.forEach((x, i) => {
      const px = sx(x);
      const py = sy(series.y[i]);
      d += i === 0 ? `M${px},${py}` : `H${px}V${py}`;
    });
    d += `H${sx(xMax)}`;
    root.append(svg("path", { class: `series s-${series.id}`, d }));
  }

  // Reserve markers; identical amounts share one label.
  const groups = new Map();
  for (const marker of chart.markers) {
    const key = marker.usd.toFixed(2);
    groups.set(key, [...(groups.get(key) || []), marker]);
  }
  let row = 0;
  for (const markers of groups.values()) {
    const px = sx(markers[0].usd);
    const label = `${markers.map((mk) => mk.label).join(" = ")}: ${usdM(markers[0].usd)}`;
    root.append(svg("line", { class: "marker", x1: px, x2: px, y1: m.top - 6, y2: m.top + h }));
    const anchorEnd = px > m.left + w * 0.6;
    root.append(
      svg("text", {
        class: "marker-label",
        x: anchorEnd ? px - 4 : px + 4,
        y: 14 + row * 15,
        "text-anchor": anchorEnd ? "end" : "start",
        text: label,
      }),
    );
    row += 1;
  }

  // Crosshair and tooltip.
  const cross = svg("line", { class: "crosshair", y1: m.top, y2: m.top + h, visibility: "hidden" });
  const dots = chart.series.map((s) => svg("circle", { class: `dot s-${s.id}`, r: 4, visibility: "hidden" }));
  const hit = svg("rect", { x: m.left, y: m.top, width: w, height: h, fill: "transparent" });
  root.append(cross, ...dots, hit);
  const tooltip = $("chart-tooltip");
  const hide = () => {
    cross.setAttribute("visibility", "hidden");
    dots.forEach((dot) => dot.setAttribute("visibility", "hidden"));
    tooltip.hidden = true;
  };
  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("pointermove", (event) => {
    const box = root.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    const x = Math.max(0, Math.min(xMax, ((px - m.left) / w) * xMax));
    cross.setAttribute("x1", sx(x));
    cross.setAttribute("x2", sx(x));
    cross.setAttribute("visibility", "visible");
    const rows = chart.series.map((s, i) => {
      const value = stepValue(s, x);
      dots[i].setAttribute("cx", sx(x));
      dots[i].setAttribute("cy", sy(value));
      dots[i].setAttribute("visibility", "visible");
      return el("div", { class: "t-row" }, [
        el("span", { class: "t-key" }, [el("span", { class: `legend-swatch s-${s.id}` }), SERIES[s.id]]),
        el("strong", { text: pct(value) }),
      ]);
    });
    tooltip.replaceChildren(el("div", { class: "t-row" }, [el("strong", { text: `Reserve ${usdM(x)}` })]), ...rows);
    tooltip.hidden = false;
    const left = Math.min(event.clientX + 14, window.innerWidth - tooltip.offsetWidth - 8);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${Math.max(8, event.clientY - tooltip.offsetHeight - 12)}px`;
  });
  host.append(root);
  host.setAttribute(
    "aria-label",
    `Exceedance curves on a log scale. ${chart.markers.map((mk) => `${mk.label} ${usdM(mk.usd)}`).join(", ")}. Risk limit ${pct(limit, 2)}. The table view lists the values.`,
  );

  $("legend").replaceChildren(
    ...chart.series.map((s) =>
      el("span", { class: "legend-item" }, [el("span", { class: `legend-swatch s-${s.id}` }), SERIES[s.id]]),
    ),
    el("span", { class: "legend-item" }, [el("span", { class: "legend-swatch s-limit" }), "Risk limit"]),
  );
}

// --- actions --------------------------------------------------------------------

function setDirty(dirty) {
  state.dirty = dirty;
  $("dirty-note").hidden = !dirty || !state.lastResult;
  $("export-json").disabled = dirty || !state.lastResult;
  $("export-md").disabled = dirty || !state.lastResult;
}

async function runCalculation(event) {
  event?.preventDefault();
  clearErrors();
  const button = $("calculate");
  button.disabled = true;
  button.textContent = "Calculating...";
  const payload = readForm();
  try {
    const { body } = await api("/api/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.lastPayload = payload;
    render(body);
  } catch (error) {
    showErrors(error.messages || [error.message]);
  } finally {
    button.disabled = false;
    button.textContent = "Calculate reserve";
  }
}

async function download(format) {
  if (!state.lastPayload) return;
  const button = $(format === "json" ? "export-json" : "export-md");
  button.disabled = true;
  try {
    const { body, response } = await api(`/api/export?format=${format}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.lastPayload),
    });
    const blob = body instanceof Blob ? body : new Blob([JSON.stringify(body, null, 2)], { type: "application/json" });
    const match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "");
    const link = el("a", { href: URL.createObjectURL(blob), download: match ? match[1] : `reserve-report.${format}` });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  } catch (error) {
    showErrors(error.messages || [error.message]);
  } finally {
    button.disabled = state.dirty;
  }
}

async function init() {
  const { body } = await api("/api/examples");
  $("example").replaceChildren(...body.examples.map((e) => el("option", { value: e.id, text: e.label })));
  await loadDefaults("public-two-bond", 5);
  $("inputs").addEventListener("submit", runCalculation);
  $("inputs").addEventListener("input", (event) => {
    if (event.target.id !== "example" && event.target.id !== "synthetic_countries") setDirty(true);
  });
  $("example").addEventListener("change", async () => {
    await loadDefaults($("example").value, $("synthetic_countries").value || 5);
    setDirty(true);
  });
  $("synthetic_countries").addEventListener("change", async () => {
    try {
      await loadDefaults("synthetic-chain", $("synthetic_countries").value, true);
      setDirty(true);
    } catch (error) {
      showErrors(error.messages || [error.message]);
    }
  });
  $("reset").addEventListener("click", () =>
    loadDefaults($("example").value, $("synthetic_countries").value || 5).then(() => setDirty(true)),
  );
  $("export-json").addEventListener("click", () => download("json"));
  $("export-md").addEventListener("click", () => download("md"));
  $("toggle-table").addEventListener("click", () => {
    const table = $("key-table");
    table.hidden = !table.hidden;
    $("toggle-table").setAttribute("aria-expanded", String(!table.hidden));
    $("toggle-table").textContent = table.hidden ? "Show table" : "Hide table";
  });
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => state.lastResult && renderChart(state.lastResult.chart), 150);
  });
  await runCalculation();
}

init().catch((error) => {
  $("placeholder").textContent = `The app could not start: ${error.message}`;
});
