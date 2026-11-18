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
    s1: v("--series-1"), s2: v("--series-2"), s3: v("--series-3"),
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
const pct1 = (x) => `${(100 * x).toFixed(1)}%`;
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

// ---------- What drives good sessions ----------

function renderDrivers(dr) {
  const c = colors();
  $("drivers").hidden = false;
  const label = { sleep_hours: "Each extra hour of sleep", soreness: "Each extra point of soreness", is_morning: "Training in the morning" };
  const short = { sleep_hours: "Sleep", soreness: "Soreness", is_morning: "Morning training" };

  // Found (dot + 95% interval) vs. the true effect built into the simulator (gray diamond).
  const rows = dr.recovery.map((r) => ({ group: label[r.driver], est: r.estimate / 100, lo: r.low / 100, hi: r.high / 100, truth: r.true / 100 }));
  const spec = dotInterval({ y: "group", xTitle: "Effect on session quality (95% CI)", xFormat: "+.1%", height: 140 });
  spec.layer.push({
    mark: { type: "point", shape: "diamond", filled: true, size: 110, color: c.baseline, stroke: c.surface, strokeWidth: 1.5 },
    encoding: { x: { field: "truth", type: "quantitative", axis: { labels: false, ticks: false, domain: false, grid: false, title: null } },
      tooltip: [{ field: "group", title: "Driver" }, { field: "truth", title: "True effect", format: "+.2%" },
        { field: "est", title: "Found", format: "+.2%" }] },
  });
  draw("chart-drivers", { data: { values: rows }, ...spec });

  table("table-drivers", [["Driver"], ["True effect", 1], ["Found (95% CI)", 1], ["Verdict"]],
    dr.recovery.map((r) => [
      label[r.driver], `${r.true > 0 ? "+" : ""}${r.true.toFixed(2)}%`,
      `${r.estimate > 0 ? "+" : ""}${r.estimate.toFixed(2)}% (${r.low.toFixed(2)} to ${r.high.toFixed(2)})`,
      r.low <= r.true && r.true <= r.high ? "✓ Recovered" : "Underestimated (see below)",
    ]));
  const sore = dr.recovery.find((r) => r.driver === "soreness");
  $("drivers-text").innerHTML =
    `Does sleep, soreness or training time change how a session goes? For each session, the method asks how far above or below
     <em>that lifter's</em> normal it was (compared with the ${dr.neighbors} sessions before and after), then fits a regression on all
     three at once, so one isn't blamed for another's effect. The simulator has known effects built in, so we can check the answers.
     Across ${dr.lifters} lifters (${dr.sessions.toLocaleString()} sessions), it recovers the sleep and morning effects almost exactly.
     Soreness comes out about ${Math.round(100 * (1 - sore.estimate / sore.true))}% too small.`;

  // Power: share of single lifters where each effect is clearly found, by weeks of logging.
  const keys = ["sleep_hours", "is_morning", "soreness"];
  const prow = dr.power.flatMap((p) => keys.map((k) => ({ weeks: p.weeks, sessions: p.median_sessions, driver: short[k], found: p[`found_${k}`] })));
  const domain = keys.map((k) => short[k]);
  const enc = {
    x: { field: "weeks", type: "quantitative", title: "Weeks of logging", axis: { values: dr.power.map((p) => p.weeks) }, scale: { domain: [0, 28] } },
    y: { field: "found", type: "quantitative", title: "Lifters where the effect is clearly found", axis: { format: ".0%" }, scale: { domain: [0, 1] } },
    color: { field: "driver", type: "nominal", scale: { domain, range: [c.s1, c.s2, c.s3] }, sort: domain },
  };
  const last = prow.filter((r) => r.weeks === Math.max(...dr.power.map((p) => p.weeks)));
  draw("chart-power", {
    height: 260, data: { values: prow },
    layer: [
      { mark: { type: "line", strokeWidth: 2 }, encoding: enc },
      { mark: { type: "point", filled: true, size: 70 }, encoding: { ...enc,
        tooltip: [{ field: "driver", title: "Driver" }, { field: "weeks", title: "Weeks" }, { field: "sessions", title: "Typical sessions" },
          { field: "found", title: "Clearly found", format: ".0%" }] } },
      { data: { values: last }, mark: { type: "text", align: "right", dx: -8, dy: -10, fontSize: 12 },
        encoding: { x: enc.x, y: enc.y, text: { field: "driver" }, color: { value: c.text2 } } },
    ],
  });
  const at = (w) => dr.power.find((p) => p.weeks === w);
  const mid = at(13) || dr.power[1], full = dr.power[dr.power.length - 1];
  $("power-text").innerHTML =
    `For a single person, small effects take a lot of data to see. With ${mid.weeks} weeks of logs (about
     ${Math.round(mid.median_sessions)} sessions), the sleep effect is clearly found for <strong>${pct(mid.found_sleep_hours)}</strong>
     of lifters, but morning training for only ${pct(mid.found_is_morning)}. After ${full.weeks} weeks, morning training reaches
     ${pct(full.found_is_morning)}, while soreness is still found for only ${pct(full.found_soreness)}. That's why the Dashboard waits for
     at least ${dr.min_sessions} sessions and says when an effect isn't clear yet, instead of reporting noise as a finding.`;
}

// ---------- Real lifters (OpenPowerlifting) ----------

const LB = 2.20462;

function renderReal(o) {
  const c = colors(), p = o.plateau, f = o.forecast;
  $("real").hidden = false;
  $("real-intro").innerHTML =
    `The simulation shows the methods work when the world behaves like my simulator. To check real people, I used
     <a href="https://www.openpowerlifting.org">OpenPowerlifting</a>: public competition results for
     <strong>${o.data.lifters.toLocaleString()} raw powerlifters</strong> with ${o.data.min_meets}+ meets each
     (${o.data.meets.toLocaleString()} meets, ${o.data.first_meet}–${o.data.last_meet.slice(0, 4)}). As before, settings were
     chosen on ${o.data.tune_lifters.toLocaleString()} lifters and every score comes from the other
     ${o.data.test_lifters.toLocaleString()}. Meets are months apart, so this tests the trend-line idea on a much slower clock,
     and meets don't record sleep or soreness, so the full prediction model can't be tested here.`;

  const d = p.detector, b = p.baseline;
  $("real-stats").innerHTML = [
    { big: `${pct(d.pr_rate_flagged[0])} vs ${pct(d.pr_rate_not_flagged[0])}`, label: "chance of a PR at the next meet: flagged as plateau vs. not",
      sub: `${Math.round(100 * d.difference[0])}-point gap (95% CI ${(100 * d.difference[1]).toFixed(1)}–${(100 * d.difference[2]).toFixed(1)})` },
    { big: p.auc_slope.toFixed(2), label: "AUC: how well the trend slope alone ranks who will PR",
      sub: "0.5 = coin flip, 1.0 = perfect" },
    { big: `${f.methods.trend.improvement_pct[0].toFixed(1)}%`, label: "better next-meet forecast than “same as last meet”",
      sub: "real but tiny: last meet is hard to beat" },
  ].map((t) => `<div class="card stat"><div class="stat-big">${t.big}</div><div class="stat-label">${t.label}</div><div class="note">${t.sub}</div></div>`).join("");

  // Plateau: PR rate if flagged vs. not, for the detector and the rule.
  const groups = ["Called a plateau", "Not called"];
  const rows = [];
  for (const [name, m] of [["Trend-line detector", d], ["No PR in last 2 meets (rule)", b]]) {
    rows.push({ method: name, group: groups[0], est: m.pr_rate_flagged[0], lo: m.pr_rate_flagged[1], hi: m.pr_rate_flagged[2] });
    rows.push({ method: name, group: groups[1], est: m.pr_rate_not_flagged[0], lo: m.pr_rate_not_flagged[1], hi: m.pr_rate_not_flagged[2] });
  }
  const spec = dotInterval({ y: "method", yOffset: "group", color: "group", colorDomain: groups, colorRange: [c.s2, c.s1],
    xTitle: "Chance of a PR at the next meet (95% CI)", height: 150, xDomain: [0, 0.6] });
  spec.encoding.y.title = null;
  draw("chart-real-plateau", { data: { values: rows }, ...spec });

  table("table-real-plateau",
    [["Method"], ["Flags", 1], ["PR rate if flagged", 1], ["PR rate if not", 1], ["Gap", 1]],
    [["Trend-line detector", d], ["No PR in last 2 meets (rule)", b]].map(([n, m]) => [
      // One decimal: with ~450,000 predictions the intervals are narrower than a whole percent.
      n, pct(m.flagged_share), ci(m.pr_rate_flagged, pct1), ci(m.pr_rate_not_flagged, pct1), ci(m.difference, pct1)]));
  const lifts = p.by_lift.map((l) => `${l.lift} ${pct(l.pr_rate_flagged[0])} vs ${pct(l.pr_rate_not_flagged[0])}`).join(", ");
  $("real-plateau-text").innerHTML =
    `At each meet, the detector's method fit a trend line through that lift's last ${p.settings.window_meets} meets. When the line
     was flat or falling, the lifter set a PR at their next meet <strong>${pct(d.pr_rate_flagged[0])}</strong> of the time,
     versus <strong>${pct(d.pr_rate_not_flagged[0])}</strong> when it wasn't (overall: ${pct(p.overall_pr_rate)}). That separates
     lifters better than the rule "no PR in the last 2 meets" (a ${Math.round(100 * b.difference[0])}-point gap) while flagging fewer
     of them (${pct(d.flagged_share)} vs ${pct(b.flagged_share)}). It holds for every lift: ${lifts}.`;
  $("real-plateau-note").textContent =
    `Based on ${p.points.toLocaleString()} meet-to-meet predictions from ${p.lifters.toLocaleString()} test lifters. "Flat" was fixed
     in advance (slope below ${p.settings.slope_pct_per_week}% per week) rather than tuned, so this result can't come from picking
     the cutoff that looked best.`;

  // Forecasting: error of each method.
  const label = { last: "Same as last meet", mean3: "Average of last 3 meets", trend: "Damped trend (tuned)", full_trend: "Full trend line" };
  const order = ["last", "mean3", "trend", "full_trend"];
  const frows = order.map((k) => ({ group: label[k], est: f.methods[k].mae_pct[0] / 100, lo: f.methods[k].mae_pct[1] / 100, hi: f.methods[k].mae_pct[2] / 100 }));
  const fspec = dotInterval({ y: "group", xTitle: "Average miss at the next meet, % of the lift (95% CI, lower is better)", xFormat: ".1%", height: 150 });
  fspec.encoding.color = { condition: { test: "datum.group === 'Same as last meet'", value: c.baseline }, value: c.current };
  draw("chart-real-forecast", { data: { values: frows }, ...fspec });
  table("table-real-forecast",
    [["Forecast"], ["Avg. miss", 1], ["vs. same as last meet", 1], ["Better for", 1]],
    order.map((k) => [
      k === "last" ? `${label[k]} (baseline)` : label[k],
      ci(f.methods[k].mae_pct, (x) => `${x.toFixed(2)}%`),
      f.methods[k].improvement_pct ? ci(f.methods[k].improvement_pct, (x) => `${x.toFixed(1)}%`) : "–",
      f.methods[k].beats_last_share != null ? `${pct(f.methods[k].beats_last_share)} of lifters` : "–",
    ]));
  const t = f.tuned;
  $("real-forecast-text").innerHTML =
    `Can a trend line predict a lifter's next meet? Barely. The best setting found on the tuning lifters used the last ${t.window}
     meets but kept only ${Math.round(100 * t.damping)}% of the trend. That beat "same as last meet" by just
     <strong>${f.methods.trend.improvement_pct[0].toFixed(1)}%</strong>: statistically real across
     ${f.lifters.toLocaleString()} lifters, but too small to matter. Following the full trend made forecasts
     <strong>${Math.abs(f.methods.full_trend.improvement_pct[0]).toFixed(0)}% worse</strong>. Meets are months apart and progress
     slows, so straight-line extrapolation overshoots. This is why the app forecasts session-to-session from recent form, not by
     extending a long trend.`;
}

function renderExample(e, testLifters) {
  if (!e) { $("example-section").hidden = true; return; }
  const c = colors();
  const names = { squat: "Squat", bench: "Bench", deadlift: "Deadlift" };
  const rows = e.meets.flatMap((m) => Object.keys(names).map((k) => ({
    date: m.date, lift: names[k], lbs: Math.round(m[k] * LB * 10) / 10, kg: m[k],
    call: m[`${k}_flat`] === null ? "Not enough meets yet" : m[`${k}_flat`] ? "Plateau call" : "Progressing",
  })));
  const domain = ["Squat", "Bench", "Deadlift"];
  const color = { field: "lift", type: "nominal", scale: { domain, range: [c.s1, c.s2, c.s3] }, sort: domain };
  const x = { field: "date", type: "temporal", title: null };
  const y = { field: "lbs", type: "quantitative", title: "Best at the meet (lbs)", scale: { zero: false } };
  draw("chart-example", {
    height: 300,
    data: { values: rows },
    layer: [
      // Straight segments: a smooth curve would suggest results between meets that never happened.
      { mark: { type: "line", strokeWidth: 2 }, encoding: { x, y, color } },
      { mark: { type: "point", filled: true, size: 70 },
        encoding: { x, y, color,
          tooltip: [{ field: "date", type: "temporal", title: "Meet" }, { field: "lift", title: "Lift" },
            { field: "lbs", title: "lbs" }, { field: "kg", title: "kg" }, { field: "call", title: "Trend call" }] } },
      // Plateau calls: punch a surface-colored hole in the dot so it reads as hollow.
      // (A separate layer, because encoding fill by lift would hide the color legend.)
      { transform: [{ filter: "datum.call === 'Plateau call'" }],
        mark: { type: "point", filled: true, size: 22, color: c.surface, opacity: 1 },
        encoding: { x, y, tooltip: [{ field: "date", type: "temporal", title: "Meet" }, { field: "lift", title: "Lift" },
          { field: "lbs", title: "lbs" }, { field: "call", title: "Trend call" }] } },
    ],
  });
  const flat = e.meets.filter((m) => Object.keys(names).some((k) => m[`${k}_flat`])).length;
  $("example-title").textContent = `One lifter's career: ${e.name}`;
  $("example-text").textContent =
    `${e.name} is one of the best-known raw powerlifters. The public record here covers ${e.meets.length} meets from
     ${e.meets[0].date.slice(0, 4)} to ${e.meets[e.meets.length - 1].date.slice(0, 4)}. The detector's method called at least one lift
     flat at ${flat} of them. This is just an illustration; the evidence is the ${testLifters.toLocaleString()} test lifters above.`;
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
    `Tested on ${r.design.test_lifters} simulated lifters the models were never tuned on, then checked against tens of thousands
     of real powerlifters' competition records. Every result is compared with a simple rule of thumb and given a 95% confidence interval.`;
  $("split-text").textContent =
    `Settings were chosen on ${r.design.tune_lifters} lifters. Every score on this page comes from ${r.design.test_lifters} different
     lifters the tuning never saw. Scoring on the same data you tuned on makes a model look better than it is.`;
  $("generated").textContent = `Last run ${r.generated}: ${r.design.test_lifters} test lifters, ${r.design.weeks} weeks each, ${r.design.bootstrap.toLocaleString()} bootstrap resamples.`;
  renderStats(r);
  renderDetector(r);
  renderTradeoff(r);
  renderGroups(r);
  renderModel(r);
  try {
    renderDrivers(await (await fetch("data/drivers.json")).json());
  } catch (err) {
    console.warn("No driver results:", err);
  }
  // The real-lifter section is optional: the page still works without its data file.
  try {
    const o = await (await fetch("data/opl.json")).json();
    renderReal(o);
    renderExample(o.example, o.data.test_lifters);
  } catch (err) {
    console.warn("No real-lifter results:", err);
  }
}

start();
