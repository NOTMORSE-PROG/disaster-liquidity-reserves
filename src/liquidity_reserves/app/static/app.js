// ReserveBound front end. All numbers come from the local engine API.
import {
  assumptionMap,
  chainMap,
  driverBars,
  el,
  exceedanceChart,
  fmt,
  frontierChart,
  pairedBars,
  proofCostNote,
  queryChart,
  rampLegend,
  stackChart,
  sweepChart,
  tensorDiagram,
} from "./charts.js";

const $ = (id) => document.getElementById(id);
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
const SLIDERS = [
  ["risk_slider", "risk_limit_percent"],
  ["quake_high_slider", "earthquake_multiplier_high"],
  ["rain_high_slider", "rain_share_percent_high"],
];
const PIN_KEY = "reservebound:pins";
const REPORT_KEY = "reservebound:report";
const AUTO_MAX_COUNTRIES = 10;

const state = {
  defaults: null,
  lastPayload: null,
  lastResult: null,
  dirty: false,
  view: "decision",
  evidence: null,
  sweep: null,
  sweepKey: null,
  quantum: null,
  mapMode: "exact",
  surrogate: null,
  financing: null,
  financingKey: null,
  costDefaults: null,
  rangeEvidence: null,
  qaoaLive: false,
  ticket: 0,
  defaultsTicket: 0,
  timer: null,
};

const store = {
  get(key, fallback) {
    try {
      const raw = window.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch {
      return false;
    }
  },
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const type = response.headers.get("Content-Type") || "";
  const body = type.includes("json") ? await response.json() : await response.blob();
  if (!response.ok) {
    const messages = body && body.errors ? body.errors : [`Request failed (${response.status})`];
    const error = new Error(messages.join("; "));
    error.messages = messages;
    throw error;
  }
  return { body, response };
}

const post = (path, payload) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });

const usdBig = (value) =>
  value >= 1e6 ? `$${(value / 1e6).toLocaleString("en-US", { maximumFractionDigits: 3 })} million` : fmt.usd(value);

function table(headers, rows, options = {}) {
  return el("table", { class: options.className }, [
    options.caption ? el("caption", { text: options.caption }) : null,
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", { scope: "col", class: h.wrap ? "wrap" : null, text: h.label ?? h })))),
    el("tbody", {}, rows.map((cells) => el("tr", {}, cells.map((c) => (c instanceof Node ? el("td", {}, c) : el("td", { text: String(c) })))))),
  ]);
}

function chip(text, kind) {
  return el("span", { class: `chip ${kind}`, text });
}

// --- form --------------------------------------------------------------------------

function syncSliders() {
  for (const [slider, input] of SLIDERS) $(slider).value = $(input).value;
  $("countries-out").textContent = $("synthetic_countries").value;
}

function fillForm(defaults, keepSettings = false) {
  const kept = Object.fromEntries(FIELDS.map((name) => [name, $(name).value]));
  const keptGrid = $("compare_grid").checked;
  state.defaults = defaults;
  $("example").value = defaults.example;
  $("synthetic_countries").value = defaults.synthetic_countries;
  $("countries-field").hidden = defaults.example !== "synthetic-chain";
  $("synthetic-badge").hidden = !defaults.synthetic;
  $("example-description").textContent = defaults.description;
  for (const name of FIELDS) $(name).value = keepSettings ? kept[name] : defaults[name];
  $("compare_grid").checked = keepSettings ? keptGrid : Boolean(defaults.compare_grid);
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
  syncSliders();
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

async function loadDefaults(example, countries, keepSettings = false) {
  // Responses can arrive out of order; only the newest request may fill the form.
  const ticket = ++state.defaultsTicket;
  const query = new URLSearchParams({ example, countries: String(countries || 5) });
  const { body } = await api(`/api/defaults?${query}`);
  if (ticket === state.defaultsTicket) fillForm(body, keepSettings);
}

function clearErrors() {
  $("errors").hidden = true;
  $("errors").replaceChildren();
}

function showErrors(messages) {
  $("errors").replaceChildren(
    el("strong", { text: "Please correct these inputs:" }),
    el("ul", {}, messages.map((m) => el("li", { text: m }))),
  );
  $("errors").hidden = false;
}

function setDirty(dirty) {
  state.dirty = dirty;
  $("dirty-note").hidden = !dirty || !state.lastResult;
  for (const id of ["export-pdf", "export-json", "export-md"]) $(id).disabled = dirty || !state.lastResult;
}

function autoAllowed() {
  const countries = $("example").value === "synthetic-chain" ? Number($("synthetic_countries").value) : 2;
  return $("auto").checked && countries <= AUTO_MAX_COUNTRIES;
}

function scheduleAuto() {
  setDirty(true);
  if (!autoAllowed()) return;
  clearTimeout(state.timer);
  state.timer = setTimeout(() => calculate(), 450);
}

// --- calculation ---------------------------------------------------------------------

async function calculate(event) {
  event?.preventDefault();
  clearTimeout(state.timer);
  clearErrors();
  const ticket = ++state.ticket;
  const payload = readForm();
  $("busy").hidden = false;
  $("calculate").disabled = true;
  try {
    const { body } = await post("/api/calculate", payload);
    if (ticket !== state.ticket) return; // a newer calculation superseded this one
    state.lastPayload = payload;
    state.lastResult = body;
    state.quantum = null;
    state.surrogate = null;
    renderDecision(body);
    setDirty(false);
    if (state.view === "scenarios") runSweep();
    if (state.view === "financing") runFinancing();
  } catch (error) {
    if (ticket === state.ticket) showErrors(error.messages || [error.message]);
  } finally {
    if (ticket === state.ticket) {
      $("busy").hidden = true;
      $("calculate").disabled = false;
    }
  }
}

// --- decision view --------------------------------------------------------------------

function meter(host, rows, limit) {
  const scale = Math.max(limit, ...rows.map((r) => r.value)) * 1.3;
  host.replaceChildren(
    ...rows.map((row) => {
      const fill = el("span", { class: `meter-fill ${row.value > limit ? "over" : "under"}` });
      fill.style.width = `${Math.min(100, (row.value / scale) * 100)}%`;
      const mark = el("span", { class: "meter-limit", title: `Risk limit ${fmt.pct(limit, 2)}` });
      mark.style.left = `${(limit / scale) * 100}%`;
      return el("div", { class: "meter-row" }, [
        el("span", { class: "meter-label", text: row.label }),
        el("span", { class: "meter-track" }, [fill, mark]),
        el("span", { class: "meter-value", text: fmt.pct(row.value) }),
      ]);
    }),
    el("p", { class: "hint", text: `Black tick: the ${fmt.pct(limit, 2)} risk limit.` }),
  );
}

function renderDecision(r) {
  $("placeholder").hidden = true;
  $("decision-body").hidden = false;
  $("messages").replaceChildren(...r.messages.map((m) => el("li", { class: m.level, text: m.text })));
  const h = r.headline;
  const pool = r.inputs.pool;
  const range = r.range_result;
  $("verdict-eyebrow").textContent = `Reserve to hold · ${pool.term_years}-year term · ${fmt.pct(h.risk_limit, 2)} risk limit`;
  $("verdict-value").textContent = range.resolved ? usdBig(range.upper.value) : `${fmt.usdM(range.lower.value)} to ${fmt.usdM(range.upper.value)}`;
  $("verdict-text").textContent = range.resolved
    ? `${range.upper.text} is the smallest reserve that keeps the chance of payouts exceeding it at or below ${fmt.pct(h.risk_limit, 2)} for every shared-shock assumption in the range.`
    : `The smallest sufficient reserve lies in this bracket. ${r.status_text} Holding ${range.upper.text} is proven sufficient.`;
  const pill = $("verdict-status");
  pill.className = `pill ${range.resolved ? "ok" : "warn"}`;
  pill.textContent = range.resolved ? "Minimum resolved" : r.status === "within_gap" ? "Bracket within tolerance" : "Unresolved bracket";
  $("stat-base").textContent = h.base.text;
  $("stat-base-note").textContent = `Exceeded with probability ${fmt.pct(r.base.exceedance_at_reserve)} under base assumptions`;
  const sign = h.uplift_sign > 0 ? "+" : h.uplift_sign < 0 ? "-" : "";
  $("stat-uplift").textContent = `${sign}${h.uplift.text}`;
  $("stat-uplift-note").textContent =
    h.uplift_percent === null ? "" : `${sign}${Math.abs(h.uplift_percent).toFixed(1)}% to cover uncertainty in shared shocks`;
  $("stat-evals").textContent = `${h.evaluations} evaluations`;
  $("stat-evals-note").textContent = proofCostNote(r);

  const scenario = r.supporting_scenario;
  const limit = r.inputs.risk_limit;
  if (scenario.shortfall) {
    meter($("shortfall-meter"), [{ label: `P(L > ${fmt.usdM(scenario.shortfall.smaller_reserve.value)})`, value: scenario.shortfall.exceedance }], limit);
    $("shortfall-text").textContent = scenario.shortfall.explanation;
  } else {
    $("shortfall-meter").replaceChildren();
    $("shortfall-text").textContent = "A zero reserve meets the limit in every evaluated scenario, so no smaller reserve exists.";
  }
  $("shortfall-detail").replaceChildren(
    table(
      ["Pair", "Shared quakes / yr", "Shared rain / yr"],
      scenario.point.pairs.map((p) => [p.pair, p.shared_earthquake_per_year.toPrecision(4), p.shared_rain_per_year.toPrecision(4)]),
    ),
    table(
      ["Country", "Private quakes / yr", "Private rain / yr"],
      scenario.private_rates.map((p) => [p.country, p.private_earthquake_per_year.toPrecision(4), p.private_rain_per_year.toPrecision(4)]),
    ),
  );
  const upperRow = r.key_amounts.find((k) => k.amount.usd === range.upper.usd);
  meter(
    $("sufficiency-meter"),
    [
      { label: `Bound at ${fmt.usdM(range.upper.value)}`, value: r.sufficiency.bound_at_upper },
      upperRow ? { label: "Highest feasible", value: upperRow.observed_exceedance } : null,
    ].filter(Boolean),
    limit,
  );
  $("sufficiency-text").textContent = r.sufficiency.explanation;
  const cell = r.sufficiency.binding_cell;
  $("sufficiency-detail").replaceChildren(
    el("p", {
      class: "hint",
      text: `Tightest cell: earthquake ${cell.earthquake_multiplier[0].toFixed(3)}x to ${cell.earthquake_multiplier[1].toFixed(3)}x, rain ${(100 * cell.rain_share[0]).toFixed(2)}% to ${(100 * cell.rain_share[1]).toFixed(2)}%.`,
    }),
    table(
      ["Country", "Private quakes / yr", "Private rain / yr"],
      pool.countries.map((c, i) => [
        c.code,
        cell.dominating_rates.earthquake_private_per_year[i].toPrecision(4),
        cell.dominating_rates.rain_private_per_year[i].toPrecision(4),
      ]),
    ),
  );
  renderDecisionCharts(r);
  renderKeyTable(r);
  $("assumptions").replaceChildren(...r.assumptions.map((a) => el("li", { text: a })));
  const marks = { true: ["pass", "Pass"], false: ["fail", "Fail"], null: ["muted", "Not run"] };
  $("checks").replaceChildren(
    ...r.checks.map((c) => {
      const [kind, label] = marks[String(c.passed)];
      return el("li", {}, [chip(label, kind), el("span", {}, [c.check, el("span", { class: "detail", text: c.detail })])]);
    }),
  );
}

function renderDecisionCharts(r) {
  $("curve-legend").replaceChildren(
    ...[
      ["s-base", "Base assumptions"],
      ["s-observed", "Highest feasible scenario"],
      ["s-bound", "Certified upper bound"],
      ["s-limit", "Risk limit"],
    ].map(([cls, label]) => el("span", { class: "legend-item" }, [el("span", { class: `swatch ${cls}` }), label])),
  );
  exceedanceChart($("curve"), r.chart);
  $("curve").setAttribute(
    "aria-label",
    `Exceedance curves on a log scale with the base reserve ${r.base.reserve.text}, lower bound ${r.range_result.lower.text} and upper bound ${r.range_result.upper.text}. Use the Table button for values.`,
  );
  renderMap(r);
  const drivers = r.insights.drivers;
  $("drivers-note").textContent = `In the supporting scenario (${drivers.scenario}), payouts exceed ${fmt.usdM(drivers.amount_usd)} with probability ${fmt.pct(drivers.probability_exceeded)}. Bars show each country's expected payout in those outcomes.`;
  driverBars($("drivers"), drivers);
  chainMap($("chain"), r.insights.chain);
}

function renderMap(r) {
  const exact = r.insights.map;
  for (const [id, mode] of [["map-exact", "exact"], ["map-ml", "ml"]]) $(id).setAttribute("aria-pressed", String(state.mapMode === mode));
  if (state.mapMode === "ml" && state.surrogate) {
    const s = state.surrogate;
    const blank = s.reserve_usd.map((row) => row.map(() => null));
    const map = { ...exact, reserve_usd: s.reserve_usd, exceedance: blank, evaluated_points: s.evaluated_points };
    const { lo, hi } = assumptionMap($("map"), map);
    rampLegend($("map-legend"), lo, hi);
    const a = exact.reserve_usd.flat();
    const b = s.reserve_usd.flat();
    const agree = a.filter((v, i) => v === b[i]).length / a.length;
    $("map").setAttribute("aria-label", `Surrogate map of the reserve needed at each assumption, from ${fmt.usdM(lo)} to ${fmt.usdM(hi)}.`);
    $("map-note").textContent = `Gaussian-process surrogate trained by active learning: ${s.evaluations} contractions instead of ${exact.evaluations}, stopped on ${s.stopping}. It matches the exact map on ${(100 * agree).toFixed(1)}% of grid points. Dots: the assumptions it evaluated. Exploration only; the certified bounds do not use it.`;
    return;
  }
  const { lo, hi } = assumptionMap($("map"), exact);
  rampLegend($("map-legend"), lo, hi);
  $("map").setAttribute("aria-label", `Reserve needed at each assumption ranges from ${fmt.usdM(lo)} to ${fmt.usdM(hi)}.`);
  $("map-note").textContent = `Colour: reserve needed if that one assumption were true. Outlined: the ${exact.cells.length} refinement cells used for the certified bound. ${exact.evaluations} map evaluations are for explanation only.`;
}

async function setMapMode(mode) {
  state.mapMode = mode;
  if (!state.lastResult) return;
  if (mode === "ml" && !state.surrogate) {
    $("map-note").textContent = "Training the surrogate...";
    try {
      const { body } = await post("/api/surrogate", state.lastPayload);
      state.surrogate = body;
    } catch (error) {
      state.mapMode = "exact";
      $("map-note").textContent = `Surrogate failed: ${(error.messages || [error.message]).join("; ")}`;
    }
  }
  renderMap(state.lastResult);
}

function renderKeyTable(r) {
  $("key-table").replaceChildren(
    table(
      ["Reserve", "Base", "Highest feasible", "Certified bound", "E[payout above], base", "E[payout above], highest", "Meets limit everywhere"],
      r.key_amounts.map((k) => [
        k.amount.text,
        fmt.pct(k.base_exceedance),
        fmt.pct(k.observed_exceedance),
        fmt.pct(k.bound_exceedance),
        fmt.usd(k.base_expected_excess_usd),
        fmt.usd(k.observed_expected_excess_usd),
        k.meets_limit_everywhere ? "Yes" : "No",
      ]),
      { caption: "Probability that payouts are strictly greater than each amount" },
    ),
  );
}

// --- scenarios view -----------------------------------------------------------------------

async function runSweep(force = false) {
  if (!state.lastPayload) return;
  const key = JSON.stringify(state.lastPayload);
  if (!force && key === state.sweepKey && state.sweep) {
    renderSweep();
    return;
  }
  $("run-sweep").disabled = true;
  $("sweep-note").textContent = "Calculating the reserve at seven risk limits...";
  try {
    const { body } = await post("/api/sweep", state.lastPayload);
    state.sweep = body;
    state.sweepKey = key;
    renderSweep();
  } catch (error) {
    $("sweep-note").textContent = `Sweep failed: ${(error.messages || [error.message]).join("; ")}`;
  } finally {
    $("run-sweep").disabled = false;
  }
}

function renderSweep() {
  const s = state.sweep;
  if (!s) return;
  $("sweep-note").textContent = `${s.pool}${s.synthetic ? " (synthetic)" : ""}: ${s.rows.length} risk limits, ${s.total_evaluations} distribution evaluations in total. The vertical line marks the current limit.`;
  sweepChart($("sweep"), s);
  $("sweep-table").replaceChildren(
    table(
      ["Risk limit", "Base reserve", "Reserve across the range", "Uplift", "Evaluations", "Status"],
      s.rows.map((row) => [
        fmt.pct(row.risk_limit, 2),
        fmt.usdM(row.base_usd),
        row.resolved ? fmt.usdM(row.upper_usd) : `${fmt.usdM(row.lower_usd)} to ${fmt.usdM(row.upper_usd)}`,
        fmt.usdM(row.upper_usd - row.base_usd),
        row.evaluations,
        chip(row.resolved ? "Resolved" : "Bracket", row.resolved ? "pass" : "warn"),
      ]),
    ),
  );
}

function pinCurrent() {
  const r = state.lastResult;
  if (!r) return;
  const pins = store.get(PIN_KEY, []);
  const rng = r.inputs.range;
  pins.unshift({
    id: Date.now(),
    portfolio: r.inputs.pool.name,
    synthetic: r.inputs.pool.synthetic,
    limit: r.inputs.risk_limit,
    quake: rng.earthquake_multiplier,
    rain: rng.rain_share,
    base: r.headline.base.value,
    lower: r.range_result.lower.value,
    upper: r.range_result.upper.value,
    resolved: r.range_result.resolved,
    evaluations: r.headline.evaluations,
    payload: state.lastPayload,
  });
  const saved = store.set(PIN_KEY, pins.slice(0, 12));
  $("pin").textContent = saved ? "Pinned" : "Pinning unavailable";
  setTimeout(() => ($("pin").textContent = "Pin scenario"), 1400);
  renderPins();
}

function renderPins() {
  const pins = store.get(PIN_KEY, []);
  if (!pins.length) {
    $("pins").replaceChildren(el("p", { class: "hint", text: "No pinned scenarios yet." }));
    return;
  }
  $("pins").replaceChildren(
    table(
      ["Portfolio", "Limit", "Quake range", "Rain range", "Base", "Across the range", "Uplift", "Evaluations", ""],
      pins.map((p) => {
        const load = el("button", { type: "button", class: "link", text: "Load" });
        load.addEventListener("click", async () => {
          await loadDefaults(p.payload.example, p.payload.synthetic_countries);
          for (const name of FIELDS) if (name in p.payload) $(name).value = p.payload[name];
          $("compare_grid").checked = Boolean(p.payload.compare_grid);
          syncSliders();
          showView("decision");
          calculate();
        });
        const remove = el("button", { type: "button", class: "link", text: "Remove" });
        remove.addEventListener("click", () => {
          store.set(PIN_KEY, store.get(PIN_KEY, []).filter((x) => x.id !== p.id));
          renderPins();
        });
        return [
          `${p.portfolio}${p.synthetic ? " (synthetic)" : ""}`,
          fmt.pct(p.limit, 2),
          `${p.quake[0]}x to ${p.quake[1]}x`,
          `${(100 * p.rain[0]).toFixed(0)}% to ${(100 * p.rain[1]).toFixed(0)}%`,
          fmt.usdM(p.base),
          p.resolved ? fmt.usdM(p.upper) : `${fmt.usdM(p.lower)} to ${fmt.usdM(p.upper)}`,
          fmt.usdM(p.upper - p.base),
          p.evaluations,
          el("span", { class: "actions" }, [load, remove]),
        ];
      }),
    ),
  );
}

// --- evidence and method ----------------------------------------------------------------

async function loadEvidence() {
  if (state.evidence) return state.evidence;
  const { body } = await api("/api/evidence");
  state.evidence = body;
  return body;
}

function kpi(value, label, note) {
  return el("div", { class: "kpi" }, [el("p", { class: "kpi-value", text: value }), el("p", { class: "kpi-label", text: label }), el("p", { class: "kpi-note", text: note })]);
}

const TRACK = { adb_drb: "ADB term", worldbank_elt: "World Bank annual" };

async function renderEvidence() {
  const e = await loadEvidence();
  const bench = e.benchmark;
  const val = e.validation;
  const kpis = [];
  if (bench) {
    const t = bench.totals;
    kpis.push(kpi(`${t.metric_passes}/${t.metric_checks}`, "Official metric checks passed", `${t.cases_passing}/${t.cases} cases, ${t.valid_runs}/${t.runs} runs valid`));
  }
  if (val) {
    kpis.push(kpi(`${val.summary.passed}/${val.summary.checks}`, "Proposal and cross-checks agree", `${val.summary.discrepancies.length} discrepancies recorded below`));
    const cross = val.checks.find((c) => c.check.startsWith("Tensor contraction vs state-space"));
    if (cross) kpis.push(kpi(fmt.sci(cross.computed), "Max deviation from exact solver", "36 interior points, 2 to 5 countries"));
  }
  if (bench) kpis.push(kpi(fmt.sci(bench.totals.worst_normalised_rmse), "Worst error / tolerance", "Across all 65 official checks"));
  $("evidence-kpis").replaceChildren(...kpis);

  if (bench) {
    const practice = bench.practice || {};
    $("benchmark-note").textContent = `Organizer's unmodified evaluator, ${practice.repeats ?? "?"} fresh-process repeats per case, on ${practice.platform ?? "Linux"}. Practice suite protocol-eligible: ${practice.protocol_eligible ? "yes" : "no"}. ADB targets are term probabilities and USD; World Bank targets are annual rates. Timings stay in the evaluator reports.`;
    const rows = [];
    for (const suite of ["public", "practice"]) {
      for (const c of bench[suite]?.cases || []) {
        const baselines = el(
          "span",
          { class: "actions" },
          Object.entries(c.baseline_accuracy_pass).map(([name, ok]) => chip(name, ok ? "pass" : "fail")),
        );
        rows.push([
          suite === "public" ? `Public: ${c.case}` : `Practice: ${c.case}`,
          TRACK[c.track] || c.track,
          c.countries.join(", "),
          chip(c.team_accuracy_pass ? "Pass" : "Fail", c.team_accuracy_pass ? "pass" : "fail"),
          fmt.sci(c.worst_normalised_rmse),
          `${c.valid_runs}/${c.runs}`,
          baselines,
        ]);
      }
    }
    $("benchmark-table").replaceChildren(table(["Case", "Track", "Countries", "ReserveBound", "Worst error / tolerance", "Valid runs", "Classical baselines (organizer)"], rows));
  }
  if (val) {
    $("validation-note").textContent = `Recomputed ${val.generated_utc} by engine ${val.engine_version}. ${val.counting_rule}`;
    $("validation-table").replaceChildren(
      table(
        [{ label: "Check", wrap: true }, "Proposal / target", "Computed", "Status", { label: "Note", wrap: true }],
        val.checks.map((c) => [
          el("span", {}, [el("span", { class: "cell-group", text: c.group }), c.check]),
          String(c.proposal_target),
          String(c.computed_display),
          chip(c.status === "pass" ? "Agrees" : "Discrepancy", c.status === "pass" ? "pass" : "warn"),
          el("span", { text: c.note }),
        ]),
        { className: "dense" },
      ),
    );
    for (const row of $("validation-table").querySelectorAll("tbody tr")) {
      row.children[0].classList.add("wrap");
      row.children[1].classList.add("wrap");
      row.children[4].classList.add("wrap");
    }
    pairedBars(
      $("evals-chart"),
      val.reserve_table.map((row) => ({ label: row.portfolio, a: row.adaptive_evaluations, b: row.grid_evaluations })),
      { left: 200 },
    );
    $("evals-table").replaceChildren(
      table(
        ["Portfolio / limit", "Adaptive", "Evaluations", "16x16 grid", "Evaluations"],
        val.reserve_table.map((r) => [r.portfolio, r.reserve, r.adaptive_evaluations, r.grid, r.grid_evaluations]),
      ),
    );
    const memory = Object.fromEntries((val.memory || []).map((m) => [m.countries, m.peak_traced_mib]));
    $("scaling-table").replaceChildren(
      table(
        ["Countries", "Joint country states", "Payout levels", "Tensor entries", "Peak memory (MiB)"],
        e.complexity.map((c) => [c.countries, c.joint_country_states, fmt.num(c.payout_levels), fmt.num(c.tensor_entries), memory[c.countries] ?? "n/a"]),
      ),
    );
  }
  renderStudies(e);
}

function renderStudies(e) {
  if (e.surrogate) {
    $("surrogate-note").textContent = `${e.surrogate.method}. ${e.surrogate.note}`;
    $("surrogate-table").replaceChildren(
      table(
        ["Portfolio", "Limit", "Surrogate evaluations", "Exact map", "Cells exact", "Largest error", "Time: exact / surrogate"],
        e.surrogate.rows.map((r) => [
          r.countries === 2 ? "Public two-bond" : `Synthetic ${r.countries} countries`,
          fmt.pct(r.risk_limit, 1),
          r.surrogate_evaluations,
          r.exact_evaluations,
          `${r.cells_correct_percent.toFixed(1)}%`,
          fmt.usd(r.max_abs_error_usd),
          `${r.exact_seconds.toFixed(2)} s / ${r.surrogate_seconds.toFixed(2)} s`,
        ]),
        { className: "dense" },
      ),
    );
  }
  if (e.noise) {
    const n = e.noise;
    $("noise-note").textContent = `Amplitude estimation with Grover powers ${n.study.powers.join(", ")} on the public two-bond payout law (6 qubits, true value ${fmt.pct(n.study.truth)}), compiled to cz/rz/sx/x and simulated exactly (Aer density matrix) with IBM Heron r2 median error rates, scaled down by the factor shown. ${n.study.repeats} repeats of ${fmt.num(n.study.shots_per_power)} shots per power. ${n.reading}`;
    $("noise-table").replaceChildren(
      table(
        ["Error scale", "CZ error", "CZ gates at depth 8", "Flag probability at depth 8 (ideal)", "MLAE RMSE", "Noise-aware RMSE"],
        n.study.rows.map((r) => [
          r.error_scale === 0 ? "ideal" : `${r.error_scale}x Heron r2`,
          r.cz_error ? fmt.sci(r.cz_error) : "0",
          fmt.num(r.cz_gates["8"]),
          `${fmt.pct(r.flag_probability["8"], 1)} (${fmt.pct(r.ideal_flag_probability["8"], 1)})`,
          fmt.sci(r.mlae_rmse),
          fmt.sci(r.noise_aware_mlae_rmse),
        ]),
        { className: "dense" },
      ),
    );
    $("budget-table").replaceChildren(
      table(
        ["Full circuit", "Qubits", "Grover depth", "Two-qubit gates", "Error rate needed", "Fidelity at today's rate", "Improvement needed"],
        n.error_budget.map((r) => [r.case, r.qubits, fmt.num(r.grover_depth), fmt.num(r.two_qubit_gates), fmt.sci(r.error_needed_for_half_fidelity), r.fidelity_at_heron_r2 < 1e-4 ? "< 0.01%" : fmt.pct(r.fidelity_at_heron_r2, 2), `${fmt.num(r.improvement_needed)}x`]),
        { className: "dense", caption: "Error budget: two-qubit error rate that keeps the full circuit at fidelity one half" },
      ),
    );
  }
}

function renderQuantum(study, source) {
  const s = study.query_study || study;
  queryChart($("quantum-chart"), s);
  const last = s.rows[s.rows.length - 1];
  $("quantum-note").textContent = `${source}: P(L > ${fmt.usdM(s.amount_usd)}) = ${fmt.pct(s.probability)}, simulated for speed on a compact ${s.qubits}-qubit payout register with the same amplitude rotation as the full circuit (matches theory to ${fmt.sci(s.statevector_vs_analytic_max_error)}). At ${fmt.num(last.queries)} queries, amplitude estimation RMSE ${fmt.sci(last.qae_rmse)} vs Monte Carlo ${fmt.sci(last.monte_carlo_rmse)} (${(last.monte_carlo_rmse / last.qae_rmse).toFixed(1)}x lower). ${s.shots_per_power} shots per Grover power, ${s.repeats} repeats.`;
}

function interval(r) {
  if (!r.ci95) return `${fmt.pct(r.estimate)} [no consistent interval]`;
  return `${fmt.pct(r.estimate)} [${fmt.pct(r.ci95[0])}, ${fmt.pct(r.ci95[1])}]`;
}

function renderQuantumAlgorithm(q) {
  $("quantum-scope").textContent = q.scope;
  const v = q.verification;
  if (!v) return;
  const two = v.two_countries;
  const three = v.three_countries;
  const ok = (flag) => chip(flag ? "Pass" : "Fail", flag ? "pass" : "fail");
  $("quantum-verification").replaceChildren(
    table(
      ["Check", "Result", ""],
      [
        [`Loaded outcome law vs exact joint law, 2 countries (${two.qubits}-qubit circuit)`, `max error ${fmt.sci(two.loading_max_error)}`, ok(two.loading_max_error < 1e-12)],
        [`Loaded outcome law vs exact joint law, 3 countries (${three.qubits}-qubit circuit)`, `max error ${fmt.sci(three.loading_max_error)}`, ok(three.loading_max_error < 1e-12)],
        ["Payout adder and reserve flag, every outcome combination", `${two.arithmetic.combinations} and ${three.arithmetic.combinations} combinations exact`, ok(two.arithmetic.register_correct && two.arithmetic.flag_correct && three.arithmetic.register_correct && three.arithmetic.flag_correct)],
        [`Full circuit, all ${two.qubits} qubits simulated, Grover powers ${two.full_circuit_powers.join(", ")}`, `max deviation from theory ${fmt.sci(two.full_circuit_max_error)}`, ok(two.full_circuit_max_error < 1e-9)],
        ...(q.interval_coverage || []).map((c) => [
          `95% interval covers the exact value, ${fmt.num(c.repeats)} repeated runs, Grover powers up to ${c.max_power}`,
          `${fmt.pct(c.coverage, 1)} (median width ${fmt.pct(c.median_width, 3)})`,
          ok(c.coverage >= c.nominal),
        ]),
      ],
      { className: "dense" },
    ),
  );
  const est = q.full_circuit_estimate;
  const searches = q.reserve_search || [];
  const cert = q.bound_certification;
  const rows = [
    [`P(payout > ${fmt.usdM(est.reserve_usd)}), base assumptions`, interval(est), `exact ${fmt.pct(est.exact)}`, `${fmt.num(est.queries)} queries`],
    ...searches.map((s) => [
      `Quantum reserve search, base assumptions, ${fmt.pct(s.risk_limit, 1)} limit`,
      s.resolved ? fmt.usdM(s.reserve_usd) : `${fmt.usdM(s.lower_usd)} to ${fmt.usdM(s.upper_usd)}`,
      `classical ${fmt.usdM(s.classical_reserve_usd)}`,
      `${s.resolved ? "resolved" : "bracket"}: ${s.probes.length} probes, ${fmt.num(s.queries)} queries`,
    ]),
  ];
  if (cert) {
    rows.push([
      `Smaller reserve ${fmt.usdM(cert.witness.reserve_usd)} in the supporting scenario: exceeds ${fmt.pct(cert.risk_limit, 1)}?`,
      interval(cert.witness),
      `exact ${fmt.pct(cert.witness.exact)}`,
      cert.witness.verdict === "exceeds" ? "shown to fall short" : "not shown",
    ]);
    for (const cell of cert.dominating_cells) {
      rows.push([
        `${fmt.usdM(cell.reserve_usd)} under the dominating model: within ${fmt.pct(cert.risk_limit, 1)}?`,
        interval(cell),
        `exact ${fmt.pct(cell.exact)}`,
        cell.verdict === "meets" ? "shown sufficient" : "not shown",
      ]);
    }
  }
  $("quantum-decision").replaceChildren(table(["Quantity", "Quantum estimate [95% interval]", "Classical engine", "Outcome"], rows, { className: "dense" }));
  $("quantum-decision-note").textContent = cert
    ? `Every probability below comes from shot counts on the full simulated circuit. ${cert.certified ? `Both range bounds at ${fmt.pct(cert.risk_limit, 1)} are confirmed by the quantum estimates: ${fmt.usdM(cert.upper_usd)} is the reserve to hold.` : "Not every bound could be confirmed at this depth."}`
    : "";
  const probes = searches.flatMap((s) =>
    s.probes.map((p) => [fmt.pct(s.risk_limit, 1), fmt.usdM(p.reserve_usd), interval(p), p.verdict, fmt.num(p.queries)]),
  );
  $("quantum-probes").replaceChildren(table(["Limit", "Reserve probed", "P(payout > reserve)", "Verdict", "Queries"], probes, { className: "dense" }));
}

async function renderMethod() {
  tensorDiagram($("tensor"));
  const e = await loadEvidence();
  if (state.quantum) renderQuantum(state.quantum, "Current inputs");
  else if (e.quantum) renderQuantum(e.quantum, "Recorded study, public two-bond base case");
  if (e.quantum) renderQuantumAlgorithm(e.quantum);
  if (e.quantum) {
    $("resources-table").replaceChildren(
      table(
        ["Portfolio", "Precision (95%)", "Qubits", "CX: loading", "CX per Grover step", "Depth per step", "Grover depth", "QAE queries", "Monte Carlo queries"],
        e.quantum.resources.map((r) => [
          r.case,
          `±${fmt.pct(r.epsilon, 2)}`,
          r.qubits,
          fmt.num(r.cx_loading),
          fmt.num(r.cx_per_grover_iterate),
          fmt.num(r.depth_per_grover_iterate),
          fmt.num(r.qae_grover_depth),
          fmt.num(r.qae_queries_95),
          fmt.num(r.monte_carlo_queries_95),
        ]),
        { className: "dense" },
      ),
    );
  }
}

async function runQuantum() {
  if (!state.lastPayload) return;
  $("run-quantum").disabled = true;
  $("quantum-note").textContent = "Simulating amplitude estimation on the current base assumptions...";
  try {
    const { body } = await post("/api/quantum", state.lastPayload);
    state.quantum = body;
    renderQuantum(body, `Current inputs (${body.pool}${body.synthetic ? ", synthetic" : ""})`);
  } catch (error) {
    $("quantum-note").textContent = `Check failed: ${(error.messages || [error.message]).join("; ")}`;
  } finally {
    $("run-quantum").disabled = false;
  }
}

// --- financing ---------------------------------------------------------------------------

const COST_IDS = [
  ["reserve_cost_percent", "reserve_cost"],
  ["credit_front_end_fee_percent", "credit_fees"],
  ["credit_fee_percent", "credit_fees"],
  ["credit_capacity_charge_percent", "credit_capacity"],
  ["credit_drawn_cost", "credit_drawn"],
  ["insurance_multiple", "insurance_multiple"],
];

async function loadCostDefaults() {
  if (state.costDefaults) return;
  const { body } = await api("/api/cost-defaults");
  state.costDefaults = body;
  for (const [id, source] of COST_IDS) {
    $(id).value = body[id];
    $(id).title = `Default ${body[id]}: ${body.sources[source]}`;
  }
  $("credit_limit_usd").value = "";
  $("basis").value = body.basis;
}

function readCosts() {
  const costs = {};
  for (const [id] of COST_IDS) costs[id] = $(id).value;
  costs.credit_limit_usd = $("credit_limit_usd").value;
  costs.basis = $("basis").value;
  return costs;
}

async function runFinancing(force = false) {
  if (!state.lastPayload) {
    $("financing-errors").replaceChildren(el("strong", { text: "Calculate a reserve in the Decision view first." }));
    $("financing-errors").hidden = false;
    return;
  }
  await loadCostDefaults();
  const payload = { ...state.lastPayload, ...readCosts() };
  const key = JSON.stringify(payload);
  if (!force && key === state.financingKey && state.financing) return renderFinancing(state.financing);
  $("financing-errors").hidden = true;
  $("run-financing").disabled = true;
  $("run-financing").textContent = "Optimizing...";
  try {
    const { body } = await post("/api/financing", payload);
    state.financing = body;
    state.financingKey = key;
    renderFinancing(body);
  } catch (error) {
    $("financing-errors").replaceChildren(el("strong", { text: "Please correct these inputs:" }), el("ul", {}, (error.messages || [error.message]).map((m) => el("li", { text: m }))));
    $("financing-errors").hidden = false;
  } finally {
    $("run-financing").disabled = false;
    $("run-financing").textContent = "Optimize";
  }
}

const LAYER_LABEL = { reserve: "Cash reserve", credit: "Contingent credit", insurance: "Parametric insurance" };
const PLAN_LABEL = {
  all_reserve: "All cash reserve",
  reserve_and_insurance: "Reserve and insurance only",
  credit_and_insurance: "Credit and insurance only",
  all_insurance: "All insurance",
};

function layerText(layers) {
  return Object.entries(layers)
    .filter(([, [a, b]]) => b > a)
    .map(([k, [a, b]]) => `${LAYER_LABEL[k]} ${fmt.usdM(a)}–${fmt.usdM(b)}`)
    .join("; ") || "nothing needed";
}

function renderFinancing(f) {
  $("financing-results").hidden = false;
  const plan = f.plan;
  const best = plan.optimal;
  const kpis = [
    kpi(fmt.usdM(plan.capacity_usd), "Capacity to fund", `Certified reserve at ${fmt.pct(plan.risk_limit, 2)} (${f.status_text})`),
    kpi(fmt.usdM(best.total_cost_usd), "Expected cost of the optimal stack", `Over ${plan.term_years} years, ${plan.basis === "base" ? "base assumptions" : "certified bound"}`),
    kpi(`${plan.saving_vs_all_reserve_percent.toFixed(1)}%`, "Cheaper than all cash", `${fmt.usdM(plan.saving_vs_all_reserve_usd)} saved over the term`),
  ];
  if (f.pooling) kpis.push(kpi(`${f.pooling.saving_percent.toFixed(1)}%`, "Pooling saving", `${fmt.usdM(f.pooling.pooled_reserve_usd)} pooled vs ${fmt.usdM(f.pooling.standalone_total_usd)} standalone`));
  $("financing-kpis").replaceChildren(...kpis);
  stackChart($("stack-chart"), plan);
  $("stack-chart").setAttribute("aria-label", `Optimal stack: ${layerText(best.layers_usd)}.`);
  const rows = [["Optimal", best], ...Object.entries(plan.alternatives).map(([k, v]) => [PLAN_LABEL[k], v])];
  $("plans-table").replaceChildren(
    table(
      ["Plan", { label: "Layers", wrap: true }, "Expected cost", "vs optimal"],
      rows.map(([label, p]) => [label, layerText(p.layers_usd), fmt.usd(p.total_cost_usd), p === best ? "–" : `+${fmt.usd(p.total_cost_usd - best.total_cost_usd)}`]),
      { className: "dense" },
    ),
  );
  for (const row of $("plans-table").querySelectorAll("tbody tr")) row.children[1].classList.add("wrap");
  if (f.frontier) {
    frontierChart($("frontier-chart"), f.frontier);
    const tight = f.frontier[0];
    $("frontier-note").textContent = `Each point re-certifies the reserve at that limit and re-optimizes the stack. At ${fmt.pct(tight.risk_limit, 2)} the capacity is ${fmt.usdM(tight.capacity_usd)}.`;
  } else {
    $("frontier-chart").replaceChildren();
    $("frontier-note").textContent = "The frontier is computed for pools of up to five countries to keep the view responsive.";
  }
  if (f.pooling) {
    const p = f.pooling;
    $("pooling-note").textContent = `Each country alone at the same ${fmt.pct(p.risk_limit, 2)} limit needs ${fmt.usdM(p.standalone_total_usd)} in total; the pool needs ${fmt.usdM(p.pooled_reserve_usd)}. Contributions split the pooled reserve by each member's expected payout when pooled payouts exceed ${fmt.usdM(p.allocation_amount_usd)} (${p.allocation_scenario}).`;
    $("pooling-table").replaceChildren(
      table(
        ["Member", "Alone", "Split pro rata", "Split by tail risk"],
        p.members.map((m) => [`${m.country} (${m.name})`, fmt.usdM(m.reserve_usd), fmt.usdM(m.standalone_share_usd), fmt.usdM(m.shortfall_contribution_usd)]),
        { className: "dense" },
      ),
    );
  } else {
    $("pooling-note").textContent = "Pooling needs at least two countries.";
    $("pooling-table").replaceChildren();
  }
  if (f.recorded_qaoa && !state.qaoaLive) renderQaoa(f.recorded_qaoa, "Recorded study, public two-bond example at 1.5%");
}

function renderQaoa(runs, source) {
  const list = Array.isArray(runs) ? runs : [runs];
  const first = list[0];
  $("qaoa-note").textContent = `${source}. The choice of breakpoints is a ${first.variables}-variable QUBO (one-hot levels, ordering penalty) solved by CVaR-QAOA with ${first.reps} layers, trained on the exact simulated state and sampled with ${fmt.num(first.shots)} seeded shots on Qiskit Aer. Brute force over every bitstring checks the answer. Candidate levels: ${first.candidate_levels_usd.map((v) => fmt.usdM(v)).join(", ")}.`;
  $("qaoa-table").replaceChildren(
    table(
      ["Mixer", "Qubits", "CX", "P(optimum)", "Random", "Most frequent valid plan", "Optimum found"],
      list.map((q) => [
        q.mixer.replace(" (one-hot preserving)", ""),
        q.qubits,
        fmt.num(q.compiled_cx),
        fmt.pct(q.probability_optimal_sampled, 1),
        fmt.pct(q.uniform_probability_optimal, 2),
        layerText(q.most_frequent_feasible_sample),
        q.qaoa_finds_optimum ? "yes" : "no",
      ]),
      { className: "dense" },
    ),
  );
  for (const row of $("qaoa-table").querySelectorAll("tbody tr")) row.children[5].classList.add("wrap");
}

async function runQaoa() {
  if (!state.lastPayload) return;
  $("run-qaoa").disabled = true;
  $("qaoa-note").textContent = "Training and sampling the QAOA circuit, usually under a minute...";
  try {
    const { body } = await post("/api/qaoa", { ...state.lastPayload, ...readCosts() });
    state.qaoaLive = true;
    renderQaoa(body, "Current inputs");
  } catch (error) {
    $("qaoa-note").textContent = `QAOA failed: ${(error.messages || [error.message]).join("; ")}`;
  } finally {
    $("run-qaoa").disabled = false;
  }
}

// --- range evidence -------------------------------------------------------------------------

async function renderRangeEvidence() {
  if (!state.rangeEvidence) {
    const { body } = await api("/api/range-evidence");
    state.rangeEvidence = body;
  }
  const e = state.rangeEvidence;
  const q = e.earthquake;
  const r = e.rain;
  $("range-evidence-note").textContent = `Each country's trigger in the ${e.source} is calibrated to its bond trigger rate, then the catalogue is searched for events that trigger both. Earthquake: ${q.joint_trigger_events} joint events among ${q.trigger_events.KGZ} and ${q.trigger_events.TJK} triggering events. Rain: ${r.joint_trigger_events} joint event among ${r.trigger_events.KGZ} and ${r.trigger_events.TJK}. The evidence range spans every source below; the stress range adds the organizer's doubled common-shock scenario. These figures are for the public KGZ–TJK example.`;
  const rows = (list, f) => list.map((s) => [s.source, f(s.value)]);
  $("range-evidence-quake").replaceChildren(table(["Shared earthquake source", "x base"], rows(e.earthquake_multiplier_sources, (v) => `${v.toFixed(3)}x`), { className: "dense" }));
  $("range-evidence-rain").replaceChildren(table(["Shared rain source", "Share"], rows(e.rain_share_sources, (v) => fmt.pct(v, 1)), { className: "dense" }));
  for (const id of ["range-evidence-quake", "range-evidence-rain"]) {
    for (const row of $(id).querySelectorAll("tbody tr")) row.children[0].classList.add("wrap");
  }
  if (e.reserves) {
    $("range-evidence-reserves").replaceChildren(
      table(
        ["Range", "Risk limit", "Certified reserve", "Base reserve", "Evaluations"],
        e.reserves.map((x) => [x.range === "evidence_range" ? "Evidence (0–1x, 0–25%)" : "Stress (0–2x, 0–25%)", fmt.pct(x.risk_limit, 1), x.lower_usd === x.upper_usd ? fmt.usdM(x.upper_usd) : `${fmt.usdM(x.lower_usd)} to ${fmt.usdM(x.upper_usd)}`, fmt.usdM(x.base_usd), x.evaluations]),
        { className: "dense", caption: "Public two-bond example" },
      ),
    );
  }
}

function useRange(kind) {
  const e = state.rangeEvidence;
  if (!e) return;
  const spec = kind === "evidence" ? e.evidence_range : e.stress_range;
  $("earthquake_multiplier_low").value = spec.earthquake_multiplier[0];
  $("earthquake_multiplier_high").value = spec.earthquake_multiplier[1];
  $("rain_share_percent_low").value = +(100 * spec.rain_share[0]).toFixed(4);
  $("rain_share_percent_high").value = +(100 * spec.rain_share[1]).toFixed(4);
  syncSliders();
  setDirty(true);
  calculate();
}

// --- views and export -------------------------------------------------------------------

function showView(view) {
  state.view = view;
  for (const section of document.querySelectorAll(".view")) section.hidden = section.dataset.view !== view;
  for (const tab of document.querySelectorAll(".tab")) {
    if (tab.dataset.view === view) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  if (window.location.hash !== `#${view}`) history.replaceState(null, "", `#${view}`);
  const fail = (error) => console.error(error);
  if (view === "scenarios") {
    runSweep();
    renderPins();
    renderRangeEvidence().catch(fail);
  } else if (view === "financing") runFinancing().catch(fail);
  else if (view === "evidence") renderEvidence().catch(fail);
  else if (view === "method") renderMethod().catch(fail);
  else if (state.lastResult) renderDecisionCharts(state.lastResult);
}

async function download(format) {
  if (!state.lastPayload) return;
  $("export-menu").open = false;
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
  }
}

function openReport() {
  if (!state.lastPayload) return;
  $("export-menu").open = false;
  if (!store.set(REPORT_KEY, state.lastPayload)) {
    showErrors(["This browser blocks local storage, so the report page cannot receive the inputs."]);
    return;
  }
  window.open("/report", "_blank", "noopener");
}

async function init() {
  const { body } = await api("/api/examples");
  $("example").replaceChildren(...body.examples.map((e) => el("option", { value: e.id, text: e.label })));
  await loadDefaults("public-two-bond", 5);
  $("inputs").addEventListener("submit", calculate);
  $("inputs").addEventListener("input", (event) => {
    const id = event.target.id;
    if (id === "example" || id === "synthetic_countries" || id === "auto") return;
    for (const [slider, input] of SLIDERS) {
      if (id === slider) $(input).value = event.target.value;
      if (id === input) $(slider).value = event.target.value;
    }
    scheduleAuto();
  });
  $("example").addEventListener("change", async () => {
    await loadDefaults($("example").value, $("synthetic_countries").value || 5);
    scheduleAuto();
  });
  $("synthetic_countries").addEventListener("input", () => ($("countries-out").textContent = $("synthetic_countries").value));
  $("synthetic_countries").addEventListener("change", async () => {
    try {
      // A new country count rebuilds the country table only; other settings stay.
      await loadDefaults("synthetic-chain", $("synthetic_countries").value, true);
      scheduleAuto();
    } catch (error) {
      showErrors(error.messages || [error.message]);
    }
  });
  $("reset").addEventListener("click", () => loadDefaults($("example").value, $("synthetic_countries").value || 5).then(scheduleAuto));
  $("pin").addEventListener("click", pinCurrent);
  $("clear-pins").addEventListener("click", () => {
    store.set(PIN_KEY, []);
    renderPins();
  });
  $("run-sweep").addEventListener("click", () => runSweep(true));
  $("run-quantum").addEventListener("click", runQuantum);
  $("map-exact").addEventListener("click", () => setMapMode("exact"));
  $("map-ml").addEventListener("click", () => setMapMode("ml"));
  $("run-financing").addEventListener("click", () => runFinancing(true));
  $("cost-form").addEventListener("submit", (event) => {
    event.preventDefault();
    runFinancing(true);
  });
  $("run-qaoa").addEventListener("click", runQaoa);
  $("use-evidence-range").addEventListener("click", () => useRange("evidence"));
  $("use-stress-range").addEventListener("click", () => useRange("stress"));
  $("export-json").addEventListener("click", () => download("json"));
  $("export-md").addEventListener("click", () => download("md"));
  $("export-pdf").addEventListener("click", openReport);
  $("toggle-table").addEventListener("click", () => {
    const t = $("key-table");
    t.hidden = !t.hidden;
    $("toggle-table").setAttribute("aria-expanded", String(!t.hidden));
    $("toggle-table").textContent = t.hidden ? "Table" : "Hide table";
  });
  for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => showView(tab.dataset.view));
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => showView(state.view), 180);
  });
  window.addEventListener("hashchange", () => {
    const view = window.location.hash.replace("#", "");
    if (["decision", "scenarios", "financing", "evidence", "method"].includes(view) && view !== state.view) showView(view);
  });
  setDirty(false);
  await calculate();
  const initial = window.location.hash.replace("#", "");
  if (["scenarios", "financing", "evidence", "method"].includes(initial)) showView(initial);
}

init().catch((error) => {
  $("placeholder").textContent = `The app could not start: ${error.message}`;
});
