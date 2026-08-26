/* Pushpak Index dashboard — hand-rolled SVG charts, no CDN, no build step.
   Deliberately dependency-free: a demo booth may have no network, and a
   government reviewer should be able to read every line that draws a number. */
"use strict";

const $ = (s) => document.querySelector(s);
const SVGNS = "http://www.w3.org/2000/svg";
const fmt = (n, d = 2) =>
  n === null || n === undefined || Number.isNaN(n)
    ? "—"
    : Number(n).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (n, d = 1) => (n == null ? "—" : `${(n * 100).toFixed(d)}%`);
const inr = (n) => (n == null ? "—" : `₹${Math.round(n).toLocaleString("en-IN")}`);

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json();
}

function el(tag, attrs = {}, parent = null) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (parent) parent.appendChild(n);
  return n;
}
function svgRoot(host, w, h) {
  host.innerHTML = "";
  const s = el("svg", { viewBox: `0 0 ${w} ${h}`, role: "img" });
  host.appendChild(s);
  return s;
}

/* ---- shared tooltip ------------------------------------------------ */
const tt = $("#tt");
function showTip(evt, html) {
  tt.innerHTML = html;
  tt.style.opacity = "1";
  const pad = 14;
  let x = evt.clientX + pad;
  let y = evt.clientY + pad;
  const r = tt.getBoundingClientRect();
  if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - pad;
  if (y + r.height > innerHeight - 8) y = evt.clientY - r.height - pad;
  tt.style.left = `${x}px`;
  tt.style.top = `${y}px`;
}
const hideTip = () => (tt.style.opacity = "0");

/* ==================================================================== */
/* Provenance banner + headline tiles                                    */
/* ==================================================================== */
async function loadHeader() {
  const [latest, prov, routes] = await Promise.all([
    api("/api/v1/index/latest"),
    api("/api/v1/provenance"),
    api("/api/v1/routes"),
  ]);

  const synth = prov.synthetic_share;
  const blocked = (prov.robots_compliance?.results || []).filter((r) => !r.allowed).length;
  const total = (prov.robots_compliance?.results || []).length;

  // Compact and factual. States what the data actually is, and changes when
  // that changes, rather than asserting a fixed warning.
  const live = prov.live_share ?? 0;
  const official = prov.official_api_share ?? 0;
  const parts = [];
  if (synth > 0) parts.push(`<strong>${pct(synth, 0)} synthetic</strong>`);
  if (live > 0) parts.push(`${pct(live, 0)} scraped`);
  if (official > 0) parts.push(`${pct(official, 0)} licensed API`);
  if (!parts.length) parts.push("no data collected");

  $("#provbar").innerHTML =
    `<span>Data: ${parts.join(" <span class='sep'>·</span> ")}</span>` +
    `<span class="sep">|</span>` +
    `<span>${latest.n_quotes.toLocaleString("en-IN")} quotes · ${pct(latest.imputed_share)} cells imputed</span>` +
    (total
      ? `<span class="sep">|</span><span>${total - blocked} of ${total} sources permitted</span>`
      : "") +
    `<a href="/api/v1/provenance">provenance</a>`;

  const chg = latest.change_pct;
  const dir = chg == null ? "" : chg >= 0 ? "up" : "down";
  const arrow = chg == null ? "" : chg >= 0 ? "▲" : "▼";

  $("#tiles").innerHTML = [
    `<div class="card tile">
       <div class="label">Pushpak Index · ${latest.period}</div>
       <div class="value">${fmt(latest.index_value)}</div>
       <div class="meta ${dir}">${arrow} ${chg == null ? "—" : fmt(chg) + "% vs previous day"}</div>
     </div>`,
    `<div class="card tile">
       <div class="label">Base period</div>
       <div class="value">100</div>
       <div class="meta">${latest.base_period.join(" · ")}</div>
     </div>`,
    `<div class="card tile">
       <div class="label">Quotes today</div>
       <div class="value">${latest.n_quotes.toLocaleString("en-IN")}</div>
       <div class="meta">${pct(latest.soldout_share)} sold out · ${pct(latest.imputed_share)} cells imputed</div>
     </div>`,
    `<div class="card tile">
       <div class="label">Data quality</div>
       <div class="value" style="font-size:22px;padding-top:6px">
         <span class="pill ${latest.quality_flag}"><span class="dot"></span>${latest.quality_flag.toUpperCase()}</span>
       </div>
       <div class="meta">${pct(latest.synthetic_share, 0)} synthetic · basket covers ${pct(routes.national_coverage_share)} of national pax</div>
     </div>`,
  ].join("");

  return routes;
}

/* ==================================================================== */
/* 1. Index trend                                                        */
/* ==================================================================== */
let trendFreq = "daily";
async function drawTrend() {
  const host = $("#trend");
  const data = await api(`/api/v1/index?frequency=${trendFreq}`);
  const s = data.series;
  if (!s.length) { host.innerHTML = '<p class="err">No index data.</p>'; return; }

  const W = 1000, H = 320, M = { t: 16, r: 18, b: 34, l: 46 };
  const iw = W - M.l - M.r, ih = H - M.t - M.b;
  const svg = svgRoot(host, W, H);

  const vals = s.map((d) => d.index_value);
  let lo = Math.min(...vals, 100), hi = Math.max(...vals, 100);
  const pad = (hi - lo) * 0.15 || 2;
  lo -= pad; hi += pad;

  const X = (i) => M.l + (s.length === 1 ? iw / 2 : (i / (s.length - 1)) * iw);
  const Y = (v) => M.t + ih - ((v - lo) / (hi - lo)) * ih;

  // gridlines + y axis
  const g = el("g", { class: "axis" }, svg);
  const ticks = 5;
  for (let i = 0; i <= ticks; i++) {
    const v = lo + ((hi - lo) * i) / ticks;
    const y = Y(v);
    el("line", { x1: M.l, x2: W - M.r, y1: y, y2: y, class: "gridline" }, g);
    const t = el("text", { x: M.l - 8, y: y + 4, "text-anchor": "end" }, g);
    t.textContent = v.toFixed(0);
  }

  // synthetic-data shading: contiguous runs where every quote was synthetic
  let runStart = null;
  s.forEach((d, i) => {
    const isSyn = d.synthetic_share >= 0.999;
    if (isSyn && runStart === null) runStart = i;
    if ((!isSyn || i === s.length - 1) && runStart !== null) {
      const end = isSyn ? i : i - 1;
      el("rect", {
        x: X(runStart), y: M.t, width: Math.max(2, X(end) - X(runStart)), height: ih,
        fill: "var(--grid)", opacity: "0.55",
      }, svg);
      runStart = null;
    }
  });

  // base = 100 reference
  if (lo < 100 && hi > 100) {
    el("line", {
      x1: M.l, x2: W - M.r, y1: Y(100), y2: Y(100),
      stroke: "var(--axis)", "stroke-width": 1.5, "stroke-dasharray": "5 4",
    }, svg);
  }

  // the line — 2px, per mark spec
  el("path", {
    d: s.map((d, i) => `${i ? "L" : "M"}${X(i)},${Y(d.index_value)}`).join(" "),
    fill: "none", stroke: "var(--s1)", "stroke-width": 2,
    "stroke-linejoin": "round", "stroke-linecap": "round",
  }, svg);

  // x labels — first, middle, last only, to avoid collisions
  [0, Math.floor(s.length / 2), s.length - 1].forEach((i) => {
    if (i < 0 || i >= s.length) return;
    const t = el("text", {
      x: X(i), y: H - 12, "text-anchor": i === 0 ? "start" : i === s.length - 1 ? "end" : "middle",
      class: "",
    }, g);
    t.textContent = s[i].period;
  });

  // crosshair + hover targets
  const cross = el("line", {
    y1: M.t, y2: M.t + ih, stroke: "var(--axis)", "stroke-width": 1, opacity: "0",
  }, svg);
  const dot = el("circle", { r: 4.5, fill: "var(--s1)", stroke: "var(--surface-1)", "stroke-width": 2, opacity: "0" }, svg);

  const hit = el("rect", { x: M.l, y: M.t, width: iw, height: ih, fill: "transparent" }, svg);
  hit.addEventListener("mousemove", (evt) => {
    const box = svg.getBoundingClientRect();
    const rel = ((evt.clientX - box.left) / box.width) * W;
    let i = Math.round(((rel - M.l) / iw) * (s.length - 1));
    i = Math.max(0, Math.min(s.length - 1, i));
    const d = s[i];
    cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("opacity", "1");
    dot.setAttribute("cx", X(i)); dot.setAttribute("cy", Y(d.index_value)); dot.setAttribute("opacity", "1");
    showTip(evt,
      `<b>${d.period}</b>
       <div class="row"><span>Pushpak Index</span><span>${fmt(d.index_value)}</span></div>
       <div class="row"><span>Quotes</span><span>${d.n_quotes.toLocaleString("en-IN")}</span></div>
       <div class="row"><span>Synthetic</span><span>${pct(d.synthetic_share, 0)}</span></div>
       <div class="row"><span>Cells imputed</span><span>${pct(d.imputed_share)}</span></div>
       <div class="row"><span>Sold out</span><span>${pct(d.soldout_share)}</span></div>
       <div class="row"><span>Quality</span><span>${d.quality_flag.toUpperCase()}</span></div>`);
  });
  hit.addEventListener("mouseleave", () => {
    hideTip(); cross.setAttribute("opacity", "0"); dot.setAttribute("opacity", "0");
  });
}

/* ==================================================================== */
/* 2. Route heatmap                                                      */
/* ==================================================================== */
// Sequential ramp, one hue, ordered by magnitude. On a light plane it runs
// light to dark. On a dark plane that ordering would push the highest values
// into the background they are drawn on, so the ramp is re-stepped rather than
// reused: low values sit near the surface, high values step away from it.
const SEQ_LIGHT = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
const SEQ_DARK = ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"];
const seqRamp = () =>
  document.documentElement.getAttribute("data-theme") === "dark" ? SEQ_DARK : SEQ_LIGHT;
let heatMetric = "index";
async function drawHeatmap() {
  const host = $("#heatmap");
  const d = await api(`/api/v1/heatmap?metric=${heatMetric}`);
  const flat = d.values.flat().filter((v) => v != null);
  if (!flat.length) { host.innerHTML = '<p class="err">No data for this metric.</p>'; return; }

  const lo = Math.min(...flat), hi = Math.max(...flat);
  const cellW = Math.max(8, Math.min(26, 900 / d.periods.length));
  const cellH = 30, L = 92, T = 8;
  const W = L + cellW * d.periods.length + 12;
  const H = T + cellH * d.routes.length + 30;
  const svg = svgRoot(host, W, H);

  const ramp = seqRamp();
  const colour = (v) => {
    if (v == null) return "var(--grid)";
    const t = hi === lo ? 0.5 : (v - lo) / (hi - lo);
    return ramp[Math.min(ramp.length - 1, Math.floor(t * ramp.length))];
  };
  const label = (v) =>
    heatMetric === "fare" ? inr(v) : heatMetric === "volatility" ? `${fmt(v)}%` : fmt(v);

  d.routes.forEach((route, r) => {
    const t = el("text", { x: L - 10, y: T + r * cellH + cellH / 2 + 4, "text-anchor": "end", class: "" }, svg);
    t.setAttribute("fill", "var(--ink-2)");
    t.setAttribute("font-size", "12");
    t.textContent = route;

    d.periods.forEach((p, c) => {
      const v = d.values[r][c];
      // 2px surface gap between adjacent fills, per mark spec
      const rect = el("rect", {
        x: L + c * cellW + 1, y: T + r * cellH + 1,
        width: cellW - 2, height: cellH - 2, rx: 3,
        fill: colour(v), stroke: "var(--surface-1)", "stroke-width": 1,
      }, svg);
      rect.style.cursor = "crosshair";
      rect.addEventListener("mousemove", (e) =>
        showTip(e, `<b>${route} · ${p}</b>
          <div class="row"><span>${d.metric}</span><span>${label(v)}</span></div>
          <div class="row"><span>unit</span><span>${d.unit}</span></div>`));
      rect.addEventListener("mouseleave", hideTip);
    });
  });

  [0, Math.floor(d.periods.length / 2), d.periods.length - 1].forEach((c) => {
    const t = el("text", {
      x: L + c * cellW + cellW / 2, y: H - 10,
      "text-anchor": c === 0 ? "start" : c === d.periods.length - 1 ? "end" : "middle",
    }, svg);
    t.setAttribute("fill", "var(--muted)"); t.setAttribute("font-size", "11");
    t.textContent = d.periods[c];
  });

  $("#heat-legend").innerHTML =
    `<span>${label(lo)}</span>` +
    ramp.map((c) => `<i style="background:${c};width:26px;border-radius:2px"></i>`).join("") +
    `<span>${label(hi)}</span><span style="color:var(--muted)">${d.unit}</span>`;
}

/* ==================================================================== */
/* 3. Lead-time elasticity                                               */
/* ==================================================================== */
async function drawLeadtime(routeId) {
  const host = $("#leadtime");
  const d = await api(`/api/v1/leadtime/${routeId}`);
  const c = d.curve;
  if (!c.length) { host.innerHTML = '<p class="err">No lead-time data.</p>'; return; }

  const W = 560, H = 300, M = { t: 14, r: 16, b: 42, l: 62 };
  const iw = W - M.l - M.r, ih = H - M.t - M.b;
  const svg = svgRoot(host, W, H);

  const xs = c.map((p) => p.advance_purchase_days);
  const xmin = Math.min(...xs), xmax = Math.max(...xs);
  const ymin = Math.min(...c.map((p) => p.p10)) * 0.95;
  const ymax = Math.max(...c.map((p) => p.p90)) * 1.05;
  // x reversed: departure approaches to the right
  const X = (v) => M.l + ((xmax - v) / (xmax - xmin || 1)) * iw;
  const Y = (v) => M.t + ih - ((v - ymin) / (ymax - ymin || 1)) * ih;

  const g = el("g", { class: "axis" }, svg);
  for (let i = 0; i <= 4; i++) {
    const v = ymin + ((ymax - ymin) * i) / 4, y = Y(v);
    el("line", { x1: M.l, x2: W - M.r, y1: y, y2: y, class: "gridline" }, g);
    const t = el("text", { x: M.l - 8, y: y + 4, "text-anchor": "end" }, g);
    t.textContent = `₹${Math.round(v / 100) * 100}`;
  }

  // p10–p90 band
  el("path", {
    d: c.map((p, i) => `${i ? "L" : "M"}${X(p.advance_purchase_days)},${Y(p.p90)}`).join(" ") +
       " " + [...c].reverse().map((p) => `L${X(p.advance_purchase_days)},${Y(p.p10)}`).join(" ") + " Z",
    fill: "var(--s1)", opacity: "0.14",
  }, svg);

  el("path", {
    d: c.map((p, i) => `${i ? "L" : "M"}${X(p.advance_purchase_days)},${Y(p.mean_fare)}`).join(" "),
    fill: "none", stroke: "var(--s1)", "stroke-width": 2, "stroke-linejoin": "round",
  }, svg);

  c.forEach((p) => {
    const cx = X(p.advance_purchase_days), cy = Y(p.mean_fare);
    el("circle", { cx, cy, r: 5, fill: "var(--s1)", stroke: "var(--surface-1)", "stroke-width": 2 }, svg);
    const hit = el("circle", { cx, cy, r: 16, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", (e) =>
      showTip(e, `<b>T+${p.advance_purchase_days} days out</b>
        <div class="row"><span>Mean fare</span><span>${inr(p.mean_fare)}</span></div>
        <div class="row"><span>Median</span><span>${inr(p.median_fare)}</span></div>
        <div class="row"><span>P10–P90</span><span>${inr(p.p10)} – ${inr(p.p90)}</span></div>
        <div class="row"><span>Sold out</span><span>${pct(p.soldout_share)}</span></div>
        <div class="row"><span>Quotes</span><span>${Number(p.n_quotes).toLocaleString("en-IN")}</span></div>`));
    hit.addEventListener("mouseleave", hideTip);

    // direct label per point — only 4 points, so no collision risk
    const t = el("text", { x: cx, y: cy - 14, "text-anchor": "middle" }, svg);
    t.setAttribute("fill", "var(--ink-2)"); t.setAttribute("font-size", "11");
    t.setAttribute("font-weight", "600");
    t.textContent = `T+${p.advance_purchase_days}`;
  });

  const xl = el("text", { x: M.l + iw / 2, y: H - 8, "text-anchor": "middle" }, g);
  xl.textContent = "days to departure  →  departure";

  $("#elasticity").innerHTML = d.lead_time_elasticity == null
    ? ""
    : `Lead-time elasticity <strong>${fmt(d.lead_time_elasticity, 3)}</strong> — a 10% shorter booking
       window raises the mean fare about ${fmt(Math.abs(d.lead_time_elasticity) * 10, 1)}%.`;
}

/* ==================================================================== */
/* 4. Back-test                                                          */
/* ==================================================================== */
async function drawBacktest() {
  const host = $("#backtest");
  const d = await api("/api/v1/validation");
  $("#bt-hint").innerHTML =
    `Verdict <strong>${d.verdict}</strong>. ${d.caveat}`;

  const s = d.series || [];
  if (s.length < 2) { host.innerHTML = '<p class="err">Not enough overlap to compare.</p>'; return; }

  const W = 560, H = 260, M = { t: 14, r: 16, b: 32, l: 46 };
  const iw = W - M.l - M.r, ih = H - M.t - M.b;
  const svg = svgRoot(host, W, H);

  const all = s.flatMap((r) => [r.pushpak_index, r.benchmark_index]);
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.12 || 2; lo -= pad; hi += pad;
  const X = (i) => M.l + (i / (s.length - 1)) * iw;
  const Y = (v) => M.t + ih - ((v - lo) / (hi - lo)) * ih;

  const g = el("g", { class: "axis" }, svg);
  for (let i = 0; i <= 4; i++) {
    const v = lo + ((hi - lo) * i) / 4, y = Y(v);
    el("line", { x1: M.l, x2: W - M.r, y1: y, y2: y, class: "gridline" }, g);
    const t = el("text", { x: M.l - 8, y: y + 4, "text-anchor": "end" }, g);
    t.textContent = v.toFixed(0);
  }

  [["pushpak_index", "var(--s1)"], ["benchmark_index", "var(--s2)"]].forEach(([k, col]) => {
    el("path", {
      d: s.map((r, i) => `${i ? "L" : "M"}${X(i)},${Y(r[k])}`).join(" "),
      fill: "none", stroke: col, "stroke-width": 2, "stroke-linejoin": "round",
    }, svg);
  });

  const hit = el("rect", { x: M.l, y: M.t, width: iw, height: ih, fill: "transparent" }, svg);
  hit.addEventListener("mousemove", (evt) => {
    const box = svg.getBoundingClientRect();
    const rel = ((evt.clientX - box.left) / box.width) * W;
    let i = Math.round(((rel - M.l) / iw) * (s.length - 1));
    i = Math.max(0, Math.min(s.length - 1, i));
    const r = s[i];
    showTip(evt, `<b>${r.period}</b>
      <div class="row"><span>Pushpak Index</span><span>${fmt(r.pushpak_index)}</span></div>
      <div class="row"><span>Benchmark</span><span>${fmt(r.benchmark_index)}</span></div>
      <div class="row"><span>Difference</span><span>${fmt(r.difference)}</span></div>`);
  });
  hit.addEventListener("mouseleave", hideTip);

  const m = d.metrics;
  $("#bt-metrics").innerHTML = `<table><tbody>
    <tr><td>Overlap</td><td class="num">${m.n_periods} days</td></tr>
    <tr><td>Correlation</td><td class="num">${fmt(m.correlation, 3)}</td></tr>
    <tr><td>MAPE</td><td class="num">${fmt(m.mape_percent)}%</td></tr>
    <tr><td>Tracking error</td><td class="num">${fmt(m.tracking_error_index_points)} pts</td></tr>
    <tr><td>Direction agreement</td><td class="num">${pct(m.direction_agreement)}</td></tr>
    <tr><td>Benchmark official?</td><td class="num">${d.benchmark_is_official ? "yes" : "NO — placeholder"}</td></tr>
  </tbody></table>`;
}

/* ==================================================================== */
/* 5. Audit ledger                                                       */
/* ==================================================================== */
async function drawAudit() {
  const host = $("#audit");
  const d = await api("/api/v1/audit?limit=40");
  const counts = Object.entries(d.by_outcome || {});
  const head = counts.length
    ? `<p class="hint">${counts.map(([k, v]) => `<span class="pill ${k === "OK" ? "green" : k.includes("BLOCKED") ? "red" : "amber"}"><span class="dot"></span>${k}: ${v}</span>`).join(" ")}</p>`
    : `<p class="hint">No requests logged yet — run <code>python3 -m pushpak.cli collect --mode live</code>.</p>`;

  const rows = d.requests.slice(0, 12).map((r) => `<tr>
      <td class="mono">${(r.ts || "").replace("T", " ").slice(0, 19)}</td>
      <td>${r.source}</td>
      <td><span class="pill ${r.outcome === "OK" ? "green" : String(r.outcome).includes("BLOCKED") ? "red" : "amber"}"><span class="dot"></span>${r.outcome}</span></td>
      <td class="mono" style="max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.robots_rule || r.error || ""}</td>
    </tr>`).join("");

  host.innerHTML = head + (rows
    ? `<div class="scrollx"><table>
        <thead><tr><th>Timestamp (UTC)</th><th>Source</th><th>Outcome</th><th>Rule / reason</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`
    : "");
}

/* ==================================================================== */
/* 6. Hand reconstruction                                                */
/* ==================================================================== */
async function drawExplain() {
  const host = $("#explain");
  const d = await api("/api/v1/index/explain");
  const rows = d.terms.map((t) => `<tr>
      <td class="mono">${t.cell}</td>
      <td class="num">${inr(t.base_price)}</td>
      <td class="num">${inr(t.price)}</td>
      <td class="num">${fmt(t.relative, 4)}</td>
      <td class="num">${fmt(t.weight, 4)}</td>
      <td class="num"><strong>${fmt(t.contribution, 4)}</strong></td>
      <td>${t.is_imputed ? `<span class="pill amber"><span class="dot"></span>${t.imputation_method}</span>` : ""}
          ${t.synthetic_share > 0 ? `<span class="pill red"><span class="dot"></span>SYN ${pct(t.synthetic_share, 0)}</span>` : ""}</td>
    </tr>`).join("");

  host.innerHTML = `
    <p class="hint mono">${d.formula}</p>
    <table>
      <thead><tr>
        <th>Cell (route @ advance purchase)</th><th class="num">Base ₹</th><th class="num">Price ₹</th>
        <th class="num">Relative</th><th class="num">Weight</th><th class="num">Contribution</th><th>Flags</th>
      </tr></thead>
      <tbody>${rows}</tbody>
      <tfoot>
        <tr><th colspan="4"></th><th class="num">1.0000</th>
            <th class="num">${fmt(d.sum_of_contributions, 4)}</th><th>sum</th></tr>
        <tr><th colspan="4"></th><th></th>
            <th class="num">${fmt(d.published_index_value, 4)}</th>
            <th>published — ${d.reconciles ? "reconciles ✓" : "MISMATCH"}</th></tr>
      </tfoot>
    </table>`;
}

/* ==================================================================== */
async function main() {
  try {
    const routes = await loadHeader();

    const sel = $("#route-select");
    sel.innerHTML = routes.routes
      .map((r) => `<option value="${r.route_id}">${r.route_id} — ${pct(r.weight, 1)} of basket</option>`)
      .join("");
    sel.addEventListener("change", () => drawLeadtime(sel.value).catch(console.error));

    document.querySelectorAll("[data-freq]").forEach((b) =>
      b.addEventListener("click", () => {
        document.querySelectorAll("[data-freq]").forEach((x) => x.setAttribute("aria-pressed", "false"));
        b.setAttribute("aria-pressed", "true");
        trendFreq = b.dataset.freq;
        drawTrend().catch(console.error);
      }));

    document.querySelectorAll("[data-metric]").forEach((b) =>
      b.addEventListener("click", () => {
        document.querySelectorAll("[data-metric]").forEach((x) => x.setAttribute("aria-pressed", "false"));
        b.setAttribute("aria-pressed", "true");
        heatMetric = b.dataset.metric;
        drawHeatmap().catch(console.error);
      }));

    await Promise.all([
      drawTrend(), drawHeatmap(), drawLeadtime(routes.routes[0].route_id),
      drawBacktest(), drawStats(), drawCpi(), drawAudit(), drawExplain(),
      drawSources(),
    ]);
    wireCollection();
    wireTheme();

    addEventListener("resize", () => {
      clearTimeout(window.__rz);
      window.__rz = setTimeout(() => { drawHeatmap().catch(() => {}); }, 200);
    });
  } catch (e) {
    console.error(e);
    $("#banner-text").innerHTML =
      `<strong class="err">Dashboard could not load:</strong> ${e.message}. ` +
      `Seed data first: <code>python3 -m pushpak.cli seed --days 30 &amp;&amp; python3 -m pushpak.cli index</code>`;
  }
}
main();

/* ==================================================================== */
/* 7. Derived statistics                                                 */
/* ==================================================================== */
async function drawStats() {
  const d = await api("/api/v1/stats");
  const inf = d.inflation || {};
  const vol = d.volatility || {};
  const dif = d.diffusion || {};

  const tile = (label, value, meta, cls = "") =>
    `<div class="card tile">
       <div class="label">${label}</div>
       <div class="value ${cls}" style="font-size:26px">${value}</div>
       <div class="meta">${meta}</div>
     </div>`;

  const signed = (v) =>
    v == null ? "—" : `${v >= 0 ? "+" : ""}${fmt(v)}%`;

  $("#stat-tiles").innerHTML = [
    tile("Day on day", signed(inf.day_on_day_pct),
         `week on week ${signed(inf.week_on_week_pct)}`,
         (inf.day_on_day_pct ?? 0) >= 0 ? "up" : "down"),
    tile("Year on year", "unavailable",
         inf.span_days ? `series is ${inf.span_days} days long` : "insufficient history"),
    tile("Daily volatility", vol.available ? `${fmt(vol.daily_stdev_pct)}%` : "—",
         vol.available ? `mean abs move ${fmt(vol.mean_abs_daily_change_pct)}%` : ""),
    tile("Diffusion", dif.available ? fmt(dif.diffusion_index, 0) : "—",
         dif.available ? `${dif.cells_up} up · ${dif.cells_down} down of ${dif.n_cells}` : ""),
  ].join("");

  // ---- attribution: diverging bars, blue up / red down, gray zero line ----
  const att = d.attribution;
  const host = $("#attribution");
  if (!att?.available) {
    host.innerHTML = '<p class="err">No previous period to attribute against.</p>';
  } else {
    const rows = att.routes;
    const W = 520, rowH = 34, H = rows.length * rowH + 34, mid = W * 0.52;
    const max = Math.max(...rows.map((r) => Math.abs(r.contribution_points)), 0.01);
    const svg = svgRoot(host, W, H);

    el("line", { x1: mid, x2: mid, y1: 4, y2: rows.length * rowH + 4,
                 stroke: "var(--axis)", "stroke-width": 1 }, svg);

    rows.forEach((r, i) => {
      const v = r.contribution_points;
      const w = (Math.abs(v) / max) * (W * 0.4);
      const y = i * rowH + 8;
      const g = el("g", {}, svg);
      // 4px rounded data-end, anchored to the zero line
      el("rect", {
        x: v >= 0 ? mid + 1 : mid - w - 1, y, width: Math.max(w, 2), height: rowH - 14,
        rx: 4, fill: v >= 0 ? "var(--s1)" : "var(--critical)",
        stroke: "var(--surface-1)", "stroke-width": 1,
      }, g);
      const lbl = el("text", { x: mid - w - 8, y: y + 13, "text-anchor": "end" }, g);
      lbl.setAttribute("fill", "var(--ink-2)"); lbl.setAttribute("font-size", "12");
      lbl.textContent = r.route_id;
      if (v >= 0) { lbl.setAttribute("x", mid - 8); }
      const val = el("text", {
        x: v >= 0 ? mid + w + 8 : mid + 8, y: y + 13,
        "text-anchor": "start",
      }, g);
      val.setAttribute("fill", "var(--ink)"); val.setAttribute("font-size", "12");
      val.setAttribute("font-weight", "600");
      val.textContent = `${v >= 0 ? "+" : ""}${fmt(v, 3)}`;

      const hit = el("rect", { x: 0, y, width: W, height: rowH - 10, fill: "transparent" }, g);
      hit.addEventListener("mousemove", (e) =>
        showTip(e, `<b>${r.route_id}</b>
          <div class="row"><span>Contribution</span><span>${fmt(v, 4)} pts</span></div>
          <div class="row"><span>Share of change</span><span>${r.share_of_change == null ? "—" : pct(r.share_of_change)}</span></div>
          <div class="row"><span>Route index</span><span>${fmt(r.index_before)} → ${fmt(r.index_now)}</span></div>`));
      hit.addEventListener("mouseleave", hideTip);
    });

    const tot = el("text", { x: 4, y: H - 8 }, svg);
    tot.setAttribute("fill", "var(--muted)"); tot.setAttribute("font-size", "11.5");
    tot.textContent = `total move ${fmt(att.total_change_points, 4)} pts · contributions sum exactly`;
  }

  // ---- dispersion table ----
  const disp = d.dispersion;
  $("#dispersion").innerHTML = !disp?.available
    ? '<p class="err">No priced cells.</p>'
    : `<table><thead><tr>
         <th>Window</th><th class="num">Mean</th><th class="num">P10</th>
         <th class="num">P90</th><th class="num">P90/P10</th><th class="num">CV</th>
       </tr></thead><tbody>${disp.by_window.map((w) => `<tr>
         <td class="mono">T+${w.advance_purchase_days}</td>
         <td class="num">${inr(w.mean_fare)}</td>
         <td class="num">${inr(w.p10)}</td>
         <td class="num">${inr(w.p90)}</td>
         <td class="num">${fmt(w.p90_p10_ratio, 3)}</td>
         <td class="num">${fmt(w.coefficient_of_variation, 3)}</td>
       </tr>`).join("")}</tbody></table>`;
}

/* ==================================================================== */
/* 8. Official CPI context                                               */
/* ==================================================================== */
async function drawCpi() {
  const d = await api("/api/v1/cpi/compare");
  const off = d.official_series;
  const s = off.series;

  $("#cpi-hint").innerHTML =
    `${off.name}. ${off.n_observations} monthly observations, ` +
    `${off.period_start} to ${off.period_end}. Source: ${off.source}`;

  $("#cpi-caveat").innerHTML =
    `<span class="ico">⚠</span><div>${d.caveat}</div>`;

  // ---- CPI line ----
  const host = $("#cpi-chart");
  if (s.length > 1) {
    const W = 520, H = 240, M = { t: 14, r: 16, b: 30, l: 46 };
    const iw = W - M.l - M.r, ih = H - M.t - M.b;
    const svg = svgRoot(host, W, H);
    const vals = s.map((r) => r.index_value);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.15 || 1; lo -= pad; hi += pad;
    const X = (i) => M.l + (i / (s.length - 1)) * iw;
    const Y = (v) => M.t + ih - ((v - lo) / (hi - lo)) * ih;

    const g = el("g", { class: "axis" }, svg);
    for (let i = 0; i <= 4; i++) {
      const v = lo + ((hi - lo) * i) / 4, y = Y(v);
      el("line", { x1: M.l, x2: W - M.r, y1: y, y2: y, class: "gridline" }, g);
      const t = el("text", { x: M.l - 8, y: y + 4, "text-anchor": "end" }, g);
      t.textContent = v.toFixed(0);
    }
    el("path", {
      d: s.map((r, i) => `${i ? "L" : "M"}${X(i)},${Y(r.index_value)}`).join(" "),
      fill: "none", stroke: "var(--s2)", "stroke-width": 2, "stroke-linejoin": "round",
    }, svg);

    [0, s.length - 1].forEach((i) => {
      const t = el("text", {
        x: X(i), y: H - 10, "text-anchor": i === 0 ? "start" : "end",
      }, g);
      t.textContent = s[i].period;
    });

    const hit = el("rect", { x: M.l, y: M.t, width: iw, height: ih, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", (evt) => {
      const box = svg.getBoundingClientRect();
      const rel = ((evt.clientX - box.left) / box.width) * W;
      let i = Math.round(((rel - M.l) / iw) * (s.length - 1));
      i = Math.max(0, Math.min(s.length - 1, i));
      const r = s[i];
      showTip(evt, `<b>${r.period}</b>
        <div class="row"><span>Index</span><span>${fmt(r.index_value)}</span></div>
        <div class="row"><span>Month on month</span><span>${r.mom_pct == null ? "—" : fmt(r.mom_pct) + "%"}</span></div>
        <div class="row"><span>Year on year</span><span>${r.yoy_pct == null ? "—" : fmt(r.yoy_pct) + "%"}</span></div>`);
    });
    hit.addEventListener("mouseleave", hideTip);
  }

  // ---- the frequency gap, which is the actual point ----
  const gap = d.frequency_gap;
  $("#freq-gap").innerHTML = `
    <table><tbody>
      <tr><th>Observations per year</th><th class="num">CPI</th><th class="num">Pushpak Index</th></tr>
      <tr><td>Releases</td>
          <td class="num">${fmt(gap.cpi.observations_per_year, 0)}</td>
          <td class="num">${fmt(gap.pushpak.observations_per_year, 0)}</td></tr>
      <tr><td>Granularity</td>
          <td class="num" style="font-size:11.5px">all-India</td>
          <td class="num" style="font-size:11.5px">route × window</td></tr>
    </tbody></table>
    <p class="hint" style="margin-top:10px">${gap.statement}</p>`;

  // ---- state-wise official inflation ----
  const sw = await api("/api/v1/cpi/statewise?measure=inflation_pct");
  const top = (sw.states || []).filter((x) => x.combined != null).slice(0, 6);
  $("#statewise").innerHTML = !top.length
    ? '<p class="hint">No state data.</p>'
    : `<table><tbody>${top.map((x) => `<tr>
         <td>${x.state}</td>
         <td class="num">${fmt(x.combined)}%</td></tr>`).join("")}
       </tbody></table>
       <p class="hint" style="margin-top:8px">Combined rural and urban, ${sw.period}. All ${sw.n_states} states and UTs via the API.</p>`;
}

/* ==================================================================== */
/* 9. Collection console                                                 */
/* ==================================================================== */
async function drawSources() {
  const d = await api("/api/v1/sources");
  const rows = d.sources.map((r) => {
    const ok = (r.robots || {}).allowed;
    return `<tr>
      <td class="mono">${r.name}</td>
      <td>${r.type}</td>
      <td><span class="pill ${ok ? "green" : "red"}"><span class="dot"></span>${ok ? "PERMITTED" : "REFUSED"}</span></td>
      <td class="mono" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${(r.robots || {}).rule || (r.robots || {}).reason || ""}</td>
      <td class="num">${r.min_seconds_between_requests}s</td>
    </tr>`;
  }).join("");

  const lic = d.licensed_api || {};
  $("#sources").innerHTML = `
    <p class="hint">${d.n_permitted} of ${d.n_sources} sources permit automated requests. ${d.note}</p>
    <table>
      <thead><tr><th>Source</th><th>Type</th><th>robots.txt</th><th>Rule / reason</th><th class="num">Min interval</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="hint" style="margin-top:12px">
      Licensed channel: <strong>${lic.provider || "n/a"}</strong> ·
      <span class="pill ${lic.configured ? "green" : "amber"}"><span class="dot"></span>${lic.configured ? "configured" : "not configured"}</span>
      ${lic.configured ? "" : `<br>Set <code>AMADEUS_CLIENT_ID</code> and <code>AMADEUS_CLIENT_SECRET</code>, then use "Run licensed API" for real fares without scraping.`}
    </p>`;
}

async function runCollection(mode, dry) {
  const btns = document.querySelectorAll("button.run");
  btns.forEach((b) => (b.disabled = true));
  const state = $("#job-state");
  const out = $("#job-result");
  state.textContent = `starting ${mode}${dry ? " (dry run)" : ""}…`;
  out.innerHTML = "";

  try {
    const start = await (await fetch(
      `/api/v1/collect?mode=${mode}&dry_run=${dry ? "true" : "false"}`,
      { method: "POST" },
    )).json();

    const t0 = Date.now();
    let job;
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      job = await api(`/api/v1/collect/${start.job_id}`);
      state.textContent = `${job.status} · ${Math.round((Date.now() - t0) / 1000)}s`;
      if (job.status !== "running") break;
      if (Date.now() - t0 > 300000) { state.textContent = "timed out waiting"; break; }
    }

    if (job.status === "failed") {
      out.innerHTML = `<p class="err">Run failed: ${job.error}</p>`;
    } else if (job.summary) {
      const s = job.summary;
      const row = (k, v, cls = "") =>
        `<tr><td>${k}</td><td class="num ${cls}">${Number(v || 0).toLocaleString("en-IN")}</td></tr>`;
      out.innerHTML = `<table style="max-width:520px"><tbody>
        ${row("quotes recorded", s.total_quotes)}
        ${row("priced by scraping", s.live_priced, s.live_priced ? "" : "")}
        ${row("priced by licensed API", s.api_priced)}
        ${row("refused by robots.txt", s.blocked_by_robots)}
        ${row("fetch failed", s.fetch_failed)}
        ${row("parse failed", s.parse_failed)}
        ${row("labelled synthetic", s.synthetic_quotes)}
        ${row("audit ledger entries", s.audit_requests)}
      </tbody></table>
      ${(s.notes || []).length ? `<p class="hint" style="margin-top:10px">${s.notes.join("<br>")}</p>` : ""}
      ${s.live_priced === 0 && s.api_priced === 0
        ? `<p class="hint">No prices returned. Sources are either refused by robots.txt or render their fare grid client-side. The licensed API is the supported route to real fares.</p>`
        : ""}`;
      // refresh the parts of the page the new data affects
      await Promise.all([loadHeader().catch(() => {}), drawAudit().catch(() => {})]);
    }
  } catch (e) {
    out.innerHTML = `<p class="err">${e.message}</p>`;
  } finally {
    btns.forEach((b) => (b.disabled = false));
  }
}

function wireCollection() {
  document.querySelectorAll("button.run").forEach((b) =>
    b.addEventListener("click", () =>
      runCollection(b.dataset.mode, b.dataset.dry === "1")));
}

/* ==================================================================== */
/* Theme toggle                                                          */
/* ==================================================================== */
const THEME_KEY = "pushpak-theme";

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark"
    ? "dark"
    : "light";
}

function paintToggle() {
  const btn = $("#theme-toggle");
  if (!btn) return;
  const t = currentTheme();
  // Sun means "switch to light", moon means "switch to dark".
  btn.textContent = t === "dark" ? "☀" : "☾";
  btn.setAttribute("aria-label", `Switch to ${t === "dark" ? "light" : "dark"} theme`);
  btn.setAttribute("title", `Switch to ${t === "dark" ? "light" : "dark"} theme`);
}

function wireTheme() {
  const btn = $("#theme-toggle");
  if (!btn) return;
  paintToggle();
  btn.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* storage blocked */ }
    paintToggle();
    // Charts read their colours from CSS variables at draw time, so they must
    // be redrawn for the new palette rather than left on stale strokes.
    redrawCharts();
  });
}

function redrawCharts() {
  const sel = $("#route-select");
  Promise.all([
    drawTrend(),
    drawHeatmap(),
    sel && sel.value ? drawLeadtime(sel.value) : Promise.resolve(),
    drawBacktest(),
    drawStats(),
    drawCpi(),
  ]).catch(console.error);
}
