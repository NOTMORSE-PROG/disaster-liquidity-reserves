// SVG charts for the app and the printable report. Every value drawn here
// comes from the engine's JSON; these functions only lay it out.

const SVG_NS = "http://www.w3.org/2000/svg";

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of [].concat(children)) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

export function svg(tag, attrs = {}, children = []) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null) continue;
    if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

export const fmt = {
  pct: (p, d = 3) => (p === null || p === undefined ? "n/a" : `${(100 * p).toFixed(d)}%`),
  usdM: (v, d = 2) => `$${(v / 1e6).toLocaleString("en-US", { maximumFractionDigits: d })}m`,
  usd: (v) => `$${Math.round(v).toLocaleString("en-US")}`,
  num: (v, d = 0) => Number(v).toLocaleString("en-US", { maximumFractionDigits: d }),
  sci: (v) => (v === 0 ? "0" : v.toExponential(1).replace("e-", "e−")),
};

function niceStep(span, target) {
  const raw = span / Math.max(target, 1);
  const power = 10 ** Math.floor(Math.log10(raw || 1));
  const unit = raw / power;
  return power * (unit < 1.5 ? 1 : unit < 3 ? 2 : unit < 7 ? 5 : 10);
}

function frame(host, height, margin) {
  host.replaceChildren();
  const width = Math.max(host.clientWidth || 0, 300);
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width, height, class: "chart-svg" });
  host.append(root);
  return { root, width, height, w: width - margin.left - margin.right, h: height - margin.top - margin.bottom, m: margin };
}

export function proofCostNote(result) {
  const h = result.headline;
  const grid = result.evaluations.grid;
  const diff = h.evaluations_saved_percent;
  const relation = diff >= 0 ? `${diff.toFixed(1)}% fewer` : `${Math.abs(diff).toFixed(1)}% more`;
  if (!grid) return `${h.grid_evaluations} for a 16x16 grid by the counting rule (${relation})`;
  const gridResult = grid.resolved ? `resolves ${grid.upper.text}` : `still leaves ${grid.lower.text} to ${grid.upper.text}`;
  return `A ${grid.method.replace("uniform ", "")} uses ${grid.evaluations} and ${gridResult}; adaptive uses ${relation}`;
}

// --- tooltip -------------------------------------------------------------------

let tooltipNode = null;
export function tooltip() {
  if (!tooltipNode) {
    tooltipNode = el("div", { class: "tooltip", role: "status" });
    tooltipNode.hidden = true;
    document.body.append(tooltipNode);
  }
  return {
    show(event, rows) {
      tooltipNode.replaceChildren(...rows);
      tooltipNode.hidden = false;
      const x = Math.min(event.clientX + 14, window.innerWidth - tooltipNode.offsetWidth - 8);
      const y = Math.max(8, event.clientY - tooltipNode.offsetHeight - 12);
      tooltipNode.style.left = `${x}px`;
      tooltipNode.style.top = `${y}px`;
    },
    hide() {
      tooltipNode.hidden = true;
    },
  };
}

export function tipRow(label, value, swatch) {
  return el("div", { class: "t-row" }, [
    el("span", { class: "t-key" }, [swatch ? el("span", { class: `swatch ${swatch}` }) : null, label]),
    el("strong", { text: value }),
  ]);
}

// --- exceedance curves -----------------------------------------------------------

const SERIES_LABEL = {
  base: "Base assumptions",
  observed: "Highest feasible scenario",
  bound: "Certified upper bound",
};

function stepValue(series, x) {
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

export function exceedanceChart(host, chart, options = {}) {
  const groups = new Map();
  for (const marker of chart.markers) {
    const key = marker.usd.toFixed(2);
    groups.set(key, [...(groups.get(key) || []), marker]);
  }
  const labelRows = groups.size;
  const f = frame(host, options.height || 360, { top: 18 + 15 * labelRows, right: 16, bottom: 40, left: 64 });
  const { root, w, h, m, width, height } = f;
  const limit = chart.risk_limit;
  const positives = chart.series.flatMap((s) => s.y.filter((v) => v > 0));
  const yMin = 10 ** Math.floor(Math.log10(Math.max(Math.min(limit / 10, ...positives), 1e-7)));
  const xMax = chart.x_max_usd;
  const sx = (x) => m.left + (Math.min(x, xMax) / xMax) * w;
  const sy = (y) => m.top + (-Math.log10(Math.max(y, yMin)) / -Math.log10(yMin)) * h;
  const grid = svg("g", { class: "grid" });
  const axis = svg("g", { class: "axis" });
  for (let e = Math.log10(yMin); e <= 0; e += 1) {
    const y = sy(10 ** e);
    grid.append(svg("line", { x1: m.left, x2: m.left + w, y1: y, y2: y }));
    axis.append(svg("text", { x: m.left - 8, y: y + 4, "text-anchor": "end", text: fmt.pct(10 ** e, e < -2 ? Math.min(-e - 2, 4) : 0) }));
  }
  const step = niceStep(xMax / 1e6, 6) * 1e6;
  for (let x = 0; x <= xMax + 1; x += step) {
    grid.append(svg("line", { x1: sx(x), x2: sx(x), y1: m.top, y2: m.top + h }));
    axis.append(svg("text", { x: sx(x), y: m.top + h + 16, "text-anchor": "middle", text: fmt.usdM(x, 1) }));
  }
  axis.append(svg("line", { class: "baseline", x1: m.left, x2: m.left + w, y1: m.top + h, y2: m.top + h }));
  axis.append(svg("text", { class: "axis-title", x: m.left + w / 2, y: height - 4, "text-anchor": "middle", text: "Reserve" }));
  axis.append(svg("text", { class: "axis-title", x: 12, y: m.top + h / 2, "text-anchor": "middle", transform: `rotate(-90 12 ${m.top + h / 2})`, text: "P(payouts > reserve)" }));
  root.append(grid, axis);
  root.append(svg("line", { class: "limit", x1: m.left, x2: m.left + w, y1: sy(limit), y2: sy(limit) }));
  root.append(svg("text", { class: "direct-label", x: m.left + w - 4, y: sy(limit) - 6, "text-anchor": "end", text: `Risk limit ${fmt.pct(limit, 2)}` }));
  for (const series of chart.series) {
    let d = "";
    series.x.forEach((x, i) => {
      d += i === 0 ? `M${sx(x)},${sy(series.y[i])}` : `H${sx(x)}V${sy(series.y[i])}`;
    });
    root.append(svg("path", { class: `series s-${series.id}`, d: `${d}H${sx(xMax)}` }));
  }
  let row = 0;
  for (const markers of groups.values()) {
    const px = sx(markers[0].usd);
    const anchorEnd = px > m.left + w * 0.6;
    root.append(svg("line", { class: `marker${markers.some((mk) => mk.id === "upper") ? " marker-strong" : ""}`, x1: px, x2: px, y1: m.top - 4, y2: m.top + h }));
    root.append(
      svg("text", {
        class: "marker-label",
        x: anchorEnd ? px - 4 : px + 4,
        y: 12 + row * 15,
        "text-anchor": anchorEnd ? "end" : "start",
        text: `${markers.map((mk) => mk.label).join(" = ")}: ${fmt.usdM(markers[0].usd)}`,
      }),
    );
    row += 1;
  }
  if (options.interactive === false) return;
  const tip = tooltip();
  const cross = svg("line", { class: "crosshair", y1: m.top, y2: m.top + h, visibility: "hidden" });
  const dots = chart.series.map((s) => svg("circle", { class: `dot s-${s.id}`, r: 4, visibility: "hidden" }));
  const hit = svg("rect", { x: m.left, y: m.top, width: w, height: h, fill: "transparent" });
  root.append(cross, ...dots, hit);
  hit.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
    tip.hide();
  });
  hit.addEventListener("pointermove", (event) => {
    const box = root.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    const x = Math.max(0, Math.min(xMax, ((px - m.left) / w) * xMax));
    cross.setAttribute("x1", sx(x));
    cross.setAttribute("x2", sx(x));
    cross.setAttribute("visibility", "visible");
    const rows = chart.series.map((s, i) => {
      const v = stepValue(s, x);
      dots[i].setAttribute("cx", sx(x));
      dots[i].setAttribute("cy", sy(v));
      dots[i].setAttribute("visibility", "visible");
      return tipRow(SERIES_LABEL[s.id], fmt.pct(v), `s-${s.id}`);
    });
    tip.show(event, [el("div", { class: "t-title", text: `Reserve ${fmt.usdM(x)}` }), ...rows]);
  });
}

// --- assumption map ----------------------------------------------------------------

const RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];

function rampColor(value, lo, hi) {
  if (hi <= lo) return RAMP[3];
  const t = (value - lo) / (hi - lo);
  return RAMP[Math.min(RAMP.length - 1, Math.max(0, Math.round(t * (RAMP.length - 1))))];
}

export function assumptionMap(host, map, options = {}) {
  const f = frame(host, options.height || 360, { top: 12, right: 16, bottom: 46, left: 58 });
  const { root, w, h, m, height } = f;
  const xs = map.earthquake_multiplier;
  const ys = map.rain_share;
  const values = map.reserve_usd.flat();
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const x0 = xs[0];
  const x1 = xs[xs.length - 1];
  const y0 = ys[0];
  const y1 = ys[ys.length - 1];
  const sx = (x) => m.left + (x1 > x0 ? ((x - x0) / (x1 - x0)) * w : w / 2);
  const sy = (y) => m.top + h - (y1 > y0 ? ((y - y0) / (y1 - y0)) * h : h / 2);
  const cw = w / xs.length;
  const ch = h / ys.length;
  const tip = tooltip();
  const cells = svg("g", { class: "heat" });
  xs.forEach((x, i) => {
    ys.forEach((y, j) => {
      const value = map.reserve_usd[i][j];
      const rect = svg("rect", {
        x: m.left + i * cw,
        y: m.top + h - (j + 1) * ch,
        width: cw + 0.5,
        height: ch + 0.5,
        fill: rampColor(value, lo, hi),
      });
      if (options.interactive !== false) {
        rect.addEventListener("pointermove", (event) =>
          tip.show(event, [
            el("div", { class: "t-title", text: `Earthquake ${x.toFixed(2)}x, rain ${(100 * y).toFixed(1)}%` }),
            tipRow("Reserve needed here", fmt.usdM(value)),
            tipRow(`P(L > ${fmt.usdM(map.amount_usd)})`, fmt.pct(map.exceedance[i][j])),
          ]),
        );
        rect.addEventListener("pointerleave", () => tip.hide());
      }
      cells.append(rect);
    });
  });
  root.append(cells);
  const overlay = svg("g", { class: "cells" });
  for (const cell of map.cells) {
    const [a, b] = cell.earthquake_multiplier;
    const [c, d] = cell.rain_share;
    overlay.append(svg("rect", { x: sx(a), y: sy(d), width: Math.max(sx(b) - sx(a), 0.5), height: Math.max(sy(c) - sy(d), 0.5) }));
  }
  root.append(overlay);
  const points = svg("g", { class: "evaluated" });
  for (const [x, y] of map.evaluated_points) points.append(svg("circle", { cx: sx(x), cy: sy(y), r: 2.5 }));
  root.append(points);
  if (map.base_in_range) {
    root.append(svg("circle", { class: "base-marker", cx: sx(map.base[0]), cy: sy(map.base[1]), r: 7 }));
  }
  const [wx, wy] = map.witness;
  const star = [];
  for (let k = 0; k < 10; k += 1) {
    const r = k % 2 ? 4 : 9;
    const a = (Math.PI / 5) * k - Math.PI / 2;
    star.push(`${sx(wx) + r * Math.cos(a)},${sy(wy) + r * Math.sin(a)}`);
  }
  root.append(svg("polygon", { class: "witness-marker", points: star.join(" ") }));
  const axis = svg("g", { class: "axis" });
  const xStep = niceStep(x1 - x0 || 1, 5);
  for (let x = x0; x <= x1 + 1e-9; x += xStep) axis.append(svg("text", { x: sx(x), y: m.top + h + 16, "text-anchor": "middle", text: `${+x.toFixed(2)}x` }));
  const yStep = niceStep(y1 - y0 || 1, 5);
  for (let y = y0; y <= y1 + 1e-9; y += yStep) axis.append(svg("text", { x: m.left - 8, y: sy(y) + 4, "text-anchor": "end", text: `${+(100 * y).toFixed(1)}%` }));
  axis.append(svg("text", { class: "axis-title", x: m.left + w / 2, y: height - 6, "text-anchor": "middle", text: "Shared earthquake rate (x base)" }));
  axis.append(svg("text", { class: "axis-title", x: 12, y: m.top + h / 2, "text-anchor": "middle", transform: `rotate(-90 12 ${m.top + h / 2})`, text: "Shared rain (% of smaller rate)" }));
  root.append(axis);
  return { lo, hi, ramp: RAMP };
}

export function rampLegend(host, lo, hi) {
  const stops = RAMP.map((color, i) => el("span", { class: `ramp-stop ramp-${i}`, title: color }));
  host.replaceChildren(
    el("span", { class: "legend-item" }, [el("span", { class: "legend-text", text: fmt.usdM(lo) }), el("span", { class: "ramp" }, stops), el("span", { class: "legend-text", text: fmt.usdM(hi) })]),
    el("span", { class: "legend-item" }, [el("span", { class: "legend-glyph glyph-cell" }), "Refinement cell"]),
    el("span", { class: "legend-item" }, [el("span", { class: "legend-glyph glyph-point" }), "Evaluated scenario"]),
    el("span", { class: "legend-item" }, [el("span", { class: "legend-glyph glyph-base" }), "Base assumptions"]),
    el("span", { class: "legend-item" }, [el("span", { class: "legend-glyph glyph-witness" }), "Scenario that sets the lower bound"]),
  );
}

// --- horizontal bars ------------------------------------------------------------------

export function driverBars(host, drivers, options = {}) {
  const rows = drivers.countries;
  const barH = 18;
  const gap = 10;
  const f = frame(host, 26 + rows.length * (barH + gap), { top: 8, right: 64, bottom: 18, left: 56 });
  const { root, w, m } = f;
  const max = Math.max(...rows.map((r) => r.cap_usd));
  const tip = tooltip();
  rows.forEach((row, i) => {
    const y = m.top + i * (barH + gap);
    const capW = (row.cap_usd / max) * w;
    const valW = (row.expected_payout_given_shortfall_usd / max) * w;
    root.append(svg("text", { class: "bar-label", x: m.left - 8, y: y + barH * 0.72, "text-anchor": "end", text: row.country }));
    root.append(svg("rect", { class: "bar-track", x: m.left, y, width: capW, height: barH, rx: 4 }));
    const bar = svg("rect", { class: "bar-value", x: m.left, y, width: Math.max(valW, 1), height: barH, rx: 4 });
    root.append(bar);
    root.append(svg("text", { class: "bar-value-label", x: m.left + capW + 6, y: y + barH * 0.72, text: fmt.pct(row.share_of_shortfall_payouts, 0) }));
    if (options.interactive !== false) {
      bar.addEventListener("pointermove", (event) =>
        tip.show(event, [
          el("div", { class: "t-title", text: row.name }),
          tipRow("Expected payout in a shortfall", fmt.usdM(row.expected_payout_given_shortfall_usd)),
          tipRow("Chance it pays in a shortfall", fmt.pct(row.probability_pays_given_shortfall, 1)),
          tipRow("Share of shortfall payouts", fmt.pct(row.share_of_shortfall_payouts, 1)),
          tipRow("Payout cap", fmt.usdM(row.cap_usd)),
        ]),
      );
      bar.addEventListener("pointerleave", () => tip.hide());
    }
  });
}

// --- regional chain -----------------------------------------------------------------------

export function chainMap(host, chain, options = {}) {
  const n = chain.nodes.length;
  const perRow = Math.min(n, Math.max(2, Math.floor((host.clientWidth || 600) / 92)));
  const rows = Math.ceil(n / perRow);
  const f = frame(host, 40 + rows * 120, { top: 26, right: 24, bottom: 14, left: 24 });
  const { root, w, m } = f;
  const step = perRow > 1 ? w / (perRow - 1) : 0;
  const maxCap = Math.max(...chain.nodes.map((c) => c.cap_usd));
  const maxQuake = Math.max(...chain.links.map((l) => l.earthquake_range[1]), 1e-12);
  const pos = chain.nodes.map((_, i) => {
    const r = Math.floor(i / perRow);
    const k = i % perRow;
    const col = r % 2 ? perRow - 1 - k : k; // serpentine so neighbours stay adjacent
    return { x: m.left + col * step + (perRow === 1 ? w / 2 : 0), y: m.top + 34 + r * 120 };
  });
  const tip = tooltip();
  chain.links.forEach((link, i) => {
    const a = pos[i];
    const b = pos[i + 1];
    const width = 2 + 8 * (link.earthquake_at_point / maxQuake);
    const line = svg("line", { class: "chain-link", x1: a.x, y1: a.y, x2: b.x, y2: b.y, "stroke-width": width });
    const rain = svg("line", { class: "chain-rain", x1: a.x, y1: a.y + 14, x2: b.x, y2: b.y + 14 });
    root.append(line, rain);
    if (options.interactive !== false) {
      const show = (event) =>
        tip.show(event, [
          el("div", { class: "t-title", text: `Shared shocks ${link.pair}` }),
          tipRow("Earthquakes / yr (scenario)", link.earthquake_at_point.toPrecision(3)),
          tipRow("Earthquakes / yr (range)", `${link.earthquake_range[0].toPrecision(2)} to ${link.earthquake_range[1].toPrecision(3)}`),
          tipRow("Rain events / yr (scenario)", link.rain_at_point.toPrecision(3)),
        ]);
      line.addEventListener("pointermove", show);
      line.addEventListener("pointerleave", () => tip.hide());
    }
  });
  chain.nodes.forEach((node, i) => {
    const { x, y } = pos[i];
    const r = 14 + 12 * Math.sqrt(node.cap_usd / maxCap);
    const circle = svg("circle", { class: "chain-node", cx: x, cy: y, r });
    root.append(circle);
    root.append(svg("text", { class: "chain-code", x, y: y + 4, "text-anchor": "middle", text: node.code }));
    root.append(svg("text", { class: "chain-cap", x, y: y + r + 14, "text-anchor": "middle", text: fmt.usdM(node.cap_usd, 1) }));
    if (options.interactive !== false) {
      circle.addEventListener("pointermove", (event) =>
        tip.show(event, [
          el("div", { class: "t-title", text: node.name }),
          tipRow("Earthquake triggers / yr", node.earthquake_rate.toPrecision(3)),
          tipRow("Rain triggers / yr", node.rain_rate.toPrecision(3)),
          tipRow("Earthquake cap", fmt.usdM(node.earthquake_cap_usd)),
          tipRow("Rain payment", fmt.usdM(node.rain_payment_usd)),
        ]),
      );
      circle.addEventListener("pointerleave", () => tip.hide());
    }
  });
}

// --- line charts ---------------------------------------------------------------------------

export function sweepChart(host, sweep, options = {}) {
  const f = frame(host, options.height || 320, { top: 16, right: 20, bottom: 44, left: 70 });
  const { root, w, h, m, height } = f;
  const rows = sweep.rows;
  const xs = rows.map((r) => r.risk_limit);
  const lx0 = Math.log10(Math.min(...xs));
  const lx1 = Math.log10(Math.max(...xs));
  const ys = rows.flatMap((r) => [r.base_usd, r.upper_usd, r.lower_usd]);
  const yMax = Math.max(...ys) * 1.08;
  const yMin = Math.max(0, Math.min(...ys) * 0.85);
  const sx = (x) => m.left + (lx1 > lx0 ? ((Math.log10(x) - lx0) / (lx1 - lx0)) * w : w / 2);
  const sy = (y) => m.top + h - ((y - yMin) / (yMax - yMin)) * h;
  const grid = svg("g", { class: "grid" });
  const axis = svg("g", { class: "axis" });
  const yStep = niceStep((yMax - yMin) / 1e6, 5) * 1e6;
  for (let y = Math.ceil(yMin / yStep) * yStep; y <= yMax; y += yStep) {
    grid.append(svg("line", { x1: m.left, x2: m.left + w, y1: sy(y), y2: sy(y) }));
    axis.append(svg("text", { x: m.left - 8, y: sy(y) + 4, "text-anchor": "end", text: fmt.usdM(y, 1) }));
  }
  for (const x of xs) axis.append(svg("text", { x: sx(x), y: m.top + h + 16, "text-anchor": "middle", text: fmt.pct(x, x < 0.01 ? 2 : 1) }));
  axis.append(svg("text", { class: "axis-title", x: m.left + w / 2, y: height - 6, "text-anchor": "middle", text: "Risk limit (log scale)" }));
  root.append(grid, axis);
  const band = rows.map((r) => `${sx(r.risk_limit)},${sy(r.upper_usd)}`).join(" ") + " " + [...rows].reverse().map((r) => `${sx(r.risk_limit)},${sy(r.lower_usd)}`).join(" ");
  root.append(svg("polygon", { class: "band", points: band }));
  const line = (key, cls) => svg("polyline", { class: `series ${cls}`, points: rows.map((r) => `${sx(r.risk_limit)},${sy(r[key])}`).join(" ") });
  root.append(line("base_usd", "s-base"), line("upper_usd", "s-observed"));
  for (const r of rows) {
    root.append(svg("circle", { class: "dot s-base", cx: sx(r.risk_limit), cy: sy(r.base_usd), r: 4 }));
    root.append(svg("circle", { class: "dot s-observed", cx: sx(r.risk_limit), cy: sy(r.upper_usd), r: 4 }));
  }
  if (sweep.current_limit) {
    const cx = sx(sweep.current_limit);
    root.append(svg("line", { class: "marker", x1: cx, x2: cx, y1: m.top, y2: m.top + h }));
    root.append(svg("text", { class: "marker-label", x: cx + 4, y: m.top + 10, text: "Current limit" }));
  }
  if (options.interactive === false) return;
  const tip = tooltip();
  for (const r of rows) {
    const hit = svg("rect", { x: sx(r.risk_limit) - 14, y: m.top, width: 28, height: h, fill: "transparent" });
    hit.addEventListener("pointermove", (event) =>
      tip.show(event, [
        el("div", { class: "t-title", text: `Risk limit ${fmt.pct(r.risk_limit, 2)}` }),
        tipRow("Base reserve", fmt.usdM(r.base_usd), "s-base"),
        tipRow("Across the range", r.resolved ? fmt.usdM(r.upper_usd) : `${fmt.usdM(r.lower_usd)} to ${fmt.usdM(r.upper_usd)}`, "s-observed"),
        tipRow("Evaluations", String(r.evaluations)),
      ]),
    );
    hit.addEventListener("pointerleave", () => tip.hide());
    root.append(hit);
  }
}

export function queryChart(host, study, options = {}) {
  const f = frame(host, options.height || 320, { top: 16, right: 20, bottom: 44, left: 70 });
  const { root, w, h, m, height } = f;
  const rows = study.rows;
  const xs = rows.map((r) => r.queries);
  const ys = rows.flatMap((r) => [r.qae_rmse, r.monte_carlo_rmse]).filter((v) => v > 0);
  const lx0 = Math.floor(Math.log10(Math.min(...xs)));
  const lx1 = Math.ceil(Math.log10(Math.max(...xs)));
  const ly0 = Math.floor(Math.log10(Math.min(...ys)));
  const ly1 = Math.ceil(Math.log10(Math.max(...ys)));
  const sx = (x) => m.left + ((Math.log10(x) - lx0) / (lx1 - lx0)) * w;
  const sy = (y) => m.top + h - ((Math.log10(y) - ly0) / (ly1 - ly0)) * h;
  const grid = svg("g", { class: "grid" });
  const axis = svg("g", { class: "axis" });
  for (let e = lx0; e <= lx1; e += 1) {
    grid.append(svg("line", { x1: sx(10 ** e), x2: sx(10 ** e), y1: m.top, y2: m.top + h }));
    axis.append(svg("text", { x: sx(10 ** e), y: m.top + h + 16, "text-anchor": "middle", text: fmt.num(10 ** e) }));
  }
  for (let e = ly0; e <= ly1; e += 1) {
    grid.append(svg("line", { x1: m.left, x2: m.left + w, y1: sy(10 ** e), y2: sy(10 ** e) }));
    axis.append(svg("text", { x: m.left - 8, y: sy(10 ** e) + 4, "text-anchor": "end", text: `1e${e}` }));
  }
  axis.append(svg("text", { class: "axis-title", x: m.left + w / 2, y: height - 6, "text-anchor": "middle", text: "Model queries (log)" }));
  axis.append(svg("text", { class: "axis-title", x: 12, y: m.top + h / 2, "text-anchor": "middle", transform: `rotate(-90 12 ${m.top + h / 2})`, text: "RMSE of P(L > R) (log)" }));
  root.append(grid, axis);
  const line = (key, cls) => svg("polyline", { class: `series ${cls}`, points: rows.map((r) => `${sx(r.queries)},${sy(Math.max(r[key], 10 ** ly0))}`).join(" ") });
  root.append(line("monte_carlo_rmse", "s-observed"), line("qae_rmse", "s-base"));
  const tip = tooltip();
  for (const r of rows) {
    for (const [key, cls] of [["monte_carlo_rmse", "s-observed"], ["qae_rmse", "s-base"]]) {
      const dot = svg("circle", { class: `dot ${cls}`, cx: sx(r.queries), cy: sy(Math.max(r[key], 10 ** ly0)), r: 4 });
      if (options.interactive !== false) {
        dot.addEventListener("pointermove", (event) =>
          tip.show(event, [
            el("div", { class: "t-title", text: `${fmt.num(r.queries)} queries (max Grover power ${r.max_power})` }),
            tipRow("Amplitude estimation RMSE", fmt.sci(r.qae_rmse), "s-base"),
            tipRow("Monte Carlo RMSE", fmt.sci(r.monte_carlo_rmse), "s-observed"),
          ]),
        );
        dot.addEventListener("pointerleave", () => tip.hide());
      }
      root.append(dot);
    }
  }
  const end = rows[rows.length - 1];
  root.append(svg("text", { class: "direct-label", x: sx(end.queries) - 6, y: sy(end.monte_carlo_rmse) - 8, "text-anchor": "end", text: "Monte Carlo" }));
  root.append(svg("text", { class: "direct-label", x: sx(end.queries) - 6, y: sy(Math.max(end.qae_rmse, 10 ** ly0)) + 18, "text-anchor": "end", text: "Amplitude estimation" }));
}

export function pairedBars(host, rows, options = {}) {
  // rows: [{label, a, b}] drawn as two bars per row on a log axis (a: series 1, b: series 2)
  const barH = 12;
  const f = frame(host, 20 + rows.length * (2 * barH + 16), { top: 8, right: 70, bottom: 16, left: options.left || 170 });
  const { root, w, m } = f;
  const max = Math.max(...rows.flatMap((r) => [r.a, r.b]));
  const scale = (v) => (Math.log10(Math.max(v, 1)) / Math.log10(max)) * w;
  rows.forEach((row, i) => {
    const y = m.top + i * (2 * barH + 16);
    root.append(svg("text", { class: "bar-label", x: m.left - 8, y: y + barH + 4, "text-anchor": "end", text: row.label }));
    root.append(svg("rect", { class: "bar-a", x: m.left, y, width: Math.max(scale(row.a), 2), height: barH, rx: 3 }));
    root.append(svg("text", { class: "bar-value-label", x: m.left + scale(row.a) + 6, y: y + barH - 2, text: fmt.num(row.a) }));
    root.append(svg("rect", { class: "bar-b", x: m.left, y: y + barH + 2, width: Math.max(scale(row.b), 2), height: barH, rx: 3 }));
    root.append(svg("text", { class: "bar-value-label", x: m.left + scale(row.b) + 6, y: y + 2 * barH, text: fmt.num(row.b) }));
  });
}

// --- method diagram ----------------------------------------------------------------------------

export function tensorDiagram(host) {
  const f = frame(host, 210, { top: 20, right: 20, bottom: 20, left: 20 });
  const { root, w, m } = f;
  const n = 5;
  const step = w / n;
  const y = m.top + 40;
  for (let i = 0; i < n; i += 1) {
    const x = m.left + step * (i + 0.5);
    if (i < n - 1) {
      root.append(svg("line", { class: "td-bond", x1: x + 26, y1: y, x2: x + step - 26, y2: y }));
      root.append(svg("text", { class: "td-bond-label", x: x + step / 2, y: y - 8, "text-anchor": "middle", text: "shared state (10)" }));
    }
    root.append(svg("rect", { class: "td-node", x: x - 26, y: y - 20, width: 52, height: 40, rx: 8 }));
    root.append(svg("text", { class: "td-node-label", x, y: y + 5, "text-anchor": "middle", text: `A${i + 1}` }));
    root.append(svg("line", { class: "td-leg", x1: x, y1: y + 20, x2: x, y2: y + 62 }));
    root.append(svg("text", { class: "td-leg-label", x: x + 6, y: y + 46, text: "payout" }));
  }
  root.append(svg("line", { class: "td-register", x1: m.left + 10, y1: y + 70, x2: m.left + w - 10, y2: y + 70 }));
  root.append(svg("text", { class: "td-register-label", x: m.left + w / 2, y: y + 92, "text-anchor": "middle", text: "Payout register: each country adds its payout as the sweep moves left to right" }));
  root.append(svg("text", { class: "td-register-label", x: m.left + w / 2, y: y + 112, "text-anchor": "middle", text: "Shared states are summed out once both neighbours are processed" }));
}
