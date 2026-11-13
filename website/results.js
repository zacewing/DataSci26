// Results page: draws the numbers in data/benchmark.json (made by python -m src.benchmark).
// Nothing is calculated here except formatting; Python is the one source of truth.

const $ = (id) => document.getElementById(id);

// Colors come from style.css, so charts follow light/dark mode with the page.
// Blue = the app's current detector, aqua = the tuned one, gray = the rule of thumb
// (gray because it's the reference everything is measured against).
function colors() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  return {
    current: v("--series-1"), tuned: v("--series-3"), baseline: v("--muted"),
    text: v("--text"), text2: v("--text-2"), muted: v("--muted"), grid: v("--grid"), axis: v("--axis"),
    surface: v("--surface"),
  };
}

function chartConfig() {
  const c = colors();
  return {
    background: null,
    font: "system-ui, -apple-system, Segoe UI, sans-serif",
    view: { stroke: null },
    axis: {
      labelColor: c.muted, titleColor: c.text2, gridColor: c.grid, domainColor: c.axis,
      tickColor: c.axis, labelFontSize: 12, titleFontSize: 12, titleFontWeight: 500,
    },
    legend: { labelColor: c.text2, labelFontSize: 12, orient: "top", title: null, ...(innerWidth < 520 ? { columns: 2 } : {}) },
    title: { color: c.text, fontSize: 13, anchor: "start" },
  };
}

function draw(id, spec) {
  spec.$schema = "https://vega.github.io/schema/vega-lite/v5.json";
  spec.width = "container";
  spec.autosize = { type: "fit-x", contains: "padding" };
  spec.config = chartConfig();
  return vegaEmbed("#" + id, spec, { actions: false, renderer: "svg" }).catch((err) => {
    $(id).textContent = "Couldn't draw this chart.";
    console.error(err);
  });
}

const pct = (x) => `${Math.round(100 * x)}%`;
const ci = ([est, lo, hi], f = pct) => `${f(est)} (${f(lo)}–${f(hi)})`;
const settingsText = (s) => `${s.window_days / 7}-week window, ${s.stall_pct}%/week, ${s.plateau_weeks} week${s.plateau_weeks > 1 ? "s" : ""} in a row`;

function table(id, headers, rows) {
  const t = $(id);
  const head = `<thead><tr>${headers.map(([h, num]) => `<th${num ? ' class="num"' : ""}>${h}</th>`).join("")}</tr></thead>`;
  const body = rows.map((r) => `<tr>${r.map((v, i) => `<td${headers[i][1] ? ' class="num"' : ""}>${v}</td>`).join("")}</tr>`).join("");
  t.innerHTML = head + `<tbody>${body}</tbody>`;
}

// A dot with a 95% interval whisker, the shared mark for every comparison here.
function dotInterval({ y, yOffset, color, colorDomain, colorRange, xTitle, xFormat = ".0%", height, xDomain }) {
  const c = colors();
  const enc = {
    y: { field: y, type: "nominal", title: null, sort: null, axis: { labelLimit: 220 } },
    ...(yOffset ? { yOffset: { field: yOffset, sort: colorDomain } } : {}),
    color: color
      ? { field: color, type: "nominal", scale: { domain: colorDomain, range: colorRange }, sort: colorDomain }
      : { value: c.current },
  };
  const x = { type: "quantitative", title: xTitle, axis: { format: xFormat, grid: true }, scale: { zero: false, ...(xDomain ? { domain: xDomain } : {}) } };
  const tooltip = [
    ...(color ? [{ field: color, title: "Method" }] : []),
    { field: y, title: y === "metric" ? "Measure" : "Group" },
    { field: "est", title: "Estimate", format: xFormat },
    { field: "lo", title: "95% CI low", format: xFormat },
    { field: "hi", title: "95% CI high", format: xFormat },
  ];
  return {
    height,
    encoding: enc,
    // Same scale, separate axes, so the hidden dot-layer axis can't hide the visible one.
    resolve: { axis: { x: "independent" } },
    layer: [
      { mark: { type: "rule", strokeWidth: 2, strokeCap: "round" }, encoding: { x: { field: "lo", ...x }, x2: { field: "hi" } } },
      {
        mark: { type: "point", filled: true, size: 90, opacity: 1, stroke: c.surface, strokeWidth: 2 },
        // The dot layer's axis is drawn invisibly: two layers each draw an axis, and with
        // slightly different ticks the labels overlap. The interval layer's axis is the visible one.
        encoding: { x: { field: "est", ...x, axis: { labels: false, ticks: false, domain: false, grid: false, title: null } }, tooltip },
      },
    ],
  };
}

// ---------- Sections ----------

function renderStats(r) {
  const d = r.detector, m = r.model.models;
  const tiles = [
    { big: `${Math.round(m.linear.improvement_pct[0])}%`, label: "smaller prediction error than “same as last session”",
      sub: `95% CI ${Math.round(m.linear.improvement_pct[1])}–${Math.round(m.linear.improvement_pct[2])}%, across ${r.model.lifters} lifters` },
    { big: `${pct(d.current.metrics.f1[0])} vs ${pct(d.baseline.metrics.f1[0])}`, label: "plateau F1: the app's tuned detector vs. the 3-week rule",
      sub: `on ${r.design.test_lifters} new lifters` },
    { big: pct(d.original.metrics.f1[0]), label: "F1 of the original settings on new lifters",
      sub: `below the simple rule (${pct(d.baseline.metrics.f1[0])}): they didn't generalize` },
  ];
  $("stats").innerHTML = tiles.map((t) =>
    `<div class="card stat"><div class="stat-big">${t.big}</div><div class="stat-label">${t.label}</div><div class="note">${t.sub}</div></div>`
  ).join("");
}

function renderDetector(r) {
  const d = r.detector, c = colors();
  const methods = [
    ["Original settings", d.original],
    ["Tuned settings", d.current],
    ["No PR in 3 weeks (rule)", d.baseline],
  ];
  const names = methods.map(([n]) => n);
  const measures = [["f1", "F1"], ["precision", "Precision"], ["recall", "Recall"], ["episodes_caught", "Plateaus caught"], ["false_alarm_share", "False alarms (lower is better)"]];

  const rows = [];
  for (const [name, m] of methods) {
    for (const [key, label] of measures) {
      const [est, lo, hi] = m.metrics[key];
      rows.push({ method: name, metric: label, est, lo, hi });
    }
  }
  draw("chart-detector", {
    data: { values: rows },
    ...dotInterval({
      y: "metric", yOffset: "method", color: "method", colorDomain: names,
      colorRange: [c.current, c.tuned, c.baseline], xTitle: "Score (95% CI)", height: 300, xDomain: [0, 1],
    }),
  });

  table("table-detector",
    [["Method"], ["Settings"], ["F1", 1], ["Precision", 1], ["Recall", 1], ["Caught", 1], ["Median lag", 1], ["False alarms", 1]],
    methods.map(([name, m]) => [
      name,
      m.settings ? settingsText(m.settings) : m.name,
      ci(m.metrics.f1), ci(m.metrics.precision), ci(m.metrics.recall), ci(m.metrics.episodes_caught),
      `${m.metrics.median_delay_weeks[0]} wk`, ci(m.metrics.false_alarm_share),
    ]));

  const diff = d.f1_original_minus_baseline, gain = d.f1_current_minus_original, edge = d.f1_current_minus_baseline;
  const pts = (v) => `${Math.round(100 * v[0])} points (95% CI ${Math.round(100 * v[1])} to ${Math.round(100 * v[2])})`;
  $("detector-text").innerHTML =
    `The detector's <strong>original settings</strong> (${settingsText(d.original.settings)}) had been chosen by testing on one scripted
     lifter. On ${r.design.test_lifters} new lifters they scored an F1 of <strong>${pct(d.original.metrics.f1[0])}</strong>, which is
     <strong>${Math.abs(Math.round(100 * diff[0]))} points worse</strong> than the simple rule: they didn't carry over. Re-tuning on separate
     lifters gave <strong>${settingsText(d.current.settings)}</strong>, which <strong>the app now uses</strong>. It scores
     <strong>${pct(d.current.metrics.f1[0])}</strong>, better than the original by ${pts(gain)} and better than the rule by ${pts(edge)},
     at the cost of more false alarms.`;
  $("grace").textContent = r.design.grace_weeks;
  $("truth-def").textContent =
    `A week counts as truly stalled when the lifter's hidden true strength grew under 0.25% per week over the last two weeks.
     About ${pct(d.prevalence)} of all lifter-weeks were truly stalled.`;
}

function renderTradeoff(r) {
  const d = r.detector, c = colors();
  const same = (a, b) => a.window_days === b.window_days && a.stall_pct === b.stall_pct && a.plateau_weeks === b.plateau_weeks;
  const grid = r.sensitivity.map((g) => ({
    ...g, settings: settingsText(g),
    kind: same(g, d.original.settings) ? "Original settings" : same(g, d.current.settings) ? "Tuned settings" : "Other settings",
  }));
  const highlights = grid.filter((g) => g.kind !== "Other settings");
  const b = r.baseline_tune;
  const rule = b ? [{ precision: b.precision, recall: b.recall, f1: b.f1, kind: "No PR in 3 weeks (rule)", settings: "rule of thumb" }] : [];
  const domain = ["Original settings", "Tuned settings", "No PR in 3 weeks (rule)"];
  const tooltip = [
    { field: "settings", title: "Settings" }, { field: "precision", format: ".0%", title: "Precision" },
    { field: "recall", format: ".0%", title: "Recall" }, { field: "f1", format: ".0%", title: "F1" },
  ];
  const x = { field: "recall", type: "quantitative", title: "Recall: real stalls flagged", axis: { format: ".0%" }, scale: { domain: [0, 1] } };
  const y = { field: "precision", type: "quantitative", title: "Precision: flags that are right", axis: { format: ".0%" }, scale: { zero: false } };
  draw("chart-tradeoff", {
    height: 320,
    layer: [
      { data: { values: grid.filter((g) => g.kind === "Other settings") },
        mark: { type: "circle", size: 40, color: c.muted, opacity: 0.35 }, encoding: { x, y, tooltip } },
      { data: { values: [...highlights, ...rule] },
        mark: { type: "point", filled: true, size: 140, opacity: 1, stroke: c.surface, strokeWidth: 2 },
        encoding: { x, y, tooltip: [{ field: "kind", title: "Method" }, ...tooltip],
          color: { field: "kind", type: "nominal", scale: { domain, range: [c.current, c.tuned, c.baseline] }, sort: domain },
          shape: { field: "kind", type: "nominal", scale: { domain, range: ["circle", "circle", "diamond"] }, legend: null } } },
      // Labels: the rule's sits above its point so it can't collide with its neighbors'.
      { data: { values: highlights },
        mark: { type: "text", align: "left", dx: 10, dy: -8, fontSize: 12, color: c.text2 },
        encoding: { x, y, text: { field: "kind" } } },
      { data: { values: rule },
        mark: { type: "text", align: "center", dy: -16, fontSize: 12, color: c.text2 },
        encoding: { x, y, text: { field: "kind" } } },
    ],
  });
  $("tradeoff-text").innerHTML =
    `No single setting wins on everything. Stricter settings raise fewer false alarms but miss more real stalls; looser ones catch
     more but cry wolf more often. The tuned settings sit where F1 (the balance of the two) is highest. If false alarms matter more to
     you than missed stalls, a point further up and to the left is the better choice.`;
}

function renderGroups(r) {
  const d = r.detector;
  const rows = (groups) => groups.map((g) => ({ group: `${g.group} (n=${g.lifters})`, est: g.f1[0], lo: g.f1[1], hi: g.f1[2] }));
  draw("chart-noise", { title: "By day-to-day noise", data: { values: rows(d.by_noise) },
    ...dotInterval({ y: "group", xTitle: "F1, tuned settings (95% CI)", height: 120, xDomain: [0, 0.8] }) });
  draw("chart-plan", { title: "By training frequency", data: { values: rows(d.by_plan) },
    ...dotInterval({ y: "group", xTitle: "F1, tuned settings (95% CI)", height: 150, xDomain: [0, 0.8] }) });
  $("groups-text").textContent =
    "Scores for the app's tuned settings, split by kind of lifter. Overlapping intervals mean the difference could be chance. " +
    "It does a little better for consistent lifters than noisy ones, and for lifters who train more often (more sessions give the trend line more points). " +
    "The gaps are small, a few points of F1, so it works reasonably for every kind of lifter tested.";
}

function renderModel(r) {
  const m = r.model.models, c = colors();
  const label = { baseline: "Same as last session", linear: "Linear regression", forest: "Random forest" };
  const order = ["baseline", "linear", "forest"];
  const rows = order.map((k) => ({ group: label[k], est: m[k].mae_pct[0] / 100, lo: m[k].mae_pct[1] / 100, hi: m[k].mae_pct[2] / 100 }));
  // Two decimals: the differences are fractions of a percent, and one decimal made ticks repeat.
  const spec = dotInterval({ y: "group", xTitle: "Average miss, % of e1RM (95% CI, lower is better)", xFormat: ".2%", height: 130 });
  spec.layer[0].encoding.x.axis = { ...spec.layer[0].encoding.x.axis, tickCount: 5 };  // fewer, roomier ticks
  spec.encoding.color = { condition: { test: "datum.group === 'Same as last session'", value: c.baseline }, value: c.current };
  draw("chart-model", { data: { values: rows }, ...spec });

  table("table-model",
    [["Predictor"], ["Avg. miss (% of e1RM)", 1], ["vs. baseline", 1], ["Beats baseline for", 1], ["p-value", 1]],
    order.map((k) => [
      k === "baseline" ? `${label[k]} (baseline)` : label[k],
      ci(m[k].mae_pct, (x) => `${x.toFixed(2)}%`),
      m[k].improvement_pct ? `${ci(m[k].improvement_pct, (x) => `${Math.round(x)}%`)} smaller` : "–",
      m[k].beats_baseline_share != null ? `${pct(m[k].beats_baseline_share)} of lifters` : "–",
      m[k].wilcoxon_p != null ? (m[k].wilcoxon_p < 0.001 ? "< 0.001" : m[k].wilcoxon_p.toFixed(3)) : "–",
    ]));
  $("model-text").innerHTML =
    `Linear regression's typical miss is <strong>${Math.round(m.linear.improvement_pct[0])}% smaller</strong> than guessing "same as last
     session" (95% CI ${Math.round(m.linear.improvement_pct[1])}–${Math.round(m.linear.improvement_pct[2])}%), and it wins for
     ${pct(m.linear.beats_baseline_share)} of the ${r.model.lifters} lifters. The random forest, despite being the fancier model, barely
     helps (${Math.round(m.forest.improvement_pct[0])}%): with only a few dozen sessions per lifter there isn't enough data for it to
     learn more than a straight line does.`;
}

async function start() {
  let r;
  try {
    r = await (await fetch("data/benchmark.json")).json();
  } catch (err) {
    $("loading").hidden = true;
    $("load-error").hidden = false;
    return;
  }
  $("loading").hidden = true;
  $("results").hidden = false;
  $("lede").textContent =
    `Every number here comes from ${r.design.test_lifters} simulated lifters the models were never tuned on, compared against a simple
     rule of thumb, with 95% confidence intervals.`;
  $("split-text").textContent =
    `Settings were chosen on ${r.design.tune_lifters} lifters. Every score on this page comes from ${r.design.test_lifters} different
     lifters the tuning never saw. Scoring on the same data you tuned on makes a model look better than it is.`;
  $("generated").textContent = `Last run ${r.generated}: ${r.design.test_lifters} test lifters, ${r.design.weeks} weeks each, ${r.design.bootstrap.toLocaleString()} bootstrap resamples.`;
  renderStats(r);
  renderDetector(r);
  renderTradeoff(r);
  renderGroups(r);
  renderModel(r);
}

start();
