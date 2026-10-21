// Dashboard tab: draws the numbers that Python calculates.
//
// Two ways it gets data:
//   live  - server.py is running (localhost:8000): /api/dashboard, fresh from the logs
//   demo  - hosted as plain files (GitHub Pages): data/dashboard-sim.json, simulated only

const state = {
  mode: null,        // "live" or "demo"
  source: "real",    // "real" or "sim"
  data: null,
  lift: null,
  aiSummaries: {},   // already-written AI summaries, so we don't pay for them twice
};

// ---------- Small helpers ----------

const $ = (id) => document.getElementById(id);
const title = (s) => s.replace(/\b\w/g, (c) => c.toUpperCase());
const fmt = (n, digits = 1) => (n == null ? "–" : n.toFixed(digits));
const signed = (n, digits = 1) => (n == null ? "" : (n >= 0 ? "+" : "") + n.toFixed(digits));

function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode etc. */ }
}
function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

// Colors come from style.css, so charts follow light/dark mode with the page.
function colors() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  return {
    blue: v("--series-1"), orange: v("--series-2"), aqua: v("--series-3"),
    plateau: "#fab219", text: v("--text"), text2: v("--text-2"), muted: v("--muted"),
    grid: v("--grid"), axis: v("--axis"),
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
    // On narrow screens, wrap legend entries onto two columns instead of cutting them off.
    legend: { labelColor: c.text2, labelFontSize: 12, orient: "top", title: null, ...(innerWidth < 520 ? { columns: 2 } : {}) },
    title: { color: c.text, fontSize: 13, anchor: "start" },
  };
}

function draw(el, spec) {
  spec.$schema = "https://vega.github.io/schema/vega-lite/v5.json";
  spec.width = "container";
  spec.autosize = { type: "fit-x", contains: "padding" };
  spec.config = chartConfig();
  return vegaEmbed(el, spec, { actions: false, renderer: "svg" }).catch((err) => {
    $(el.slice(1)).textContent = "Couldn't draw this chart.";
    console.error(err);
  });
}

// Tiny Markdown for the summaries: **bold**, _italic_, "- " bullets, paragraphs.
function markdown(text) {
  const escape = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) => escape(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/(^|\s)_(.+?)_(?=\s|$|[.,])/g, "$1<em>$2</em>");
  let html = "", inList = false;
  for (const line of text.split("\n")) {
    if (line.trim().startsWith("- ")) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${inline(line.trim().slice(2))}</li>`;
      continue;
    }
    if (inList) { html += "</ul>"; inList = false; }
    if (line.trim()) html += `<p>${inline(line.trim())}</p>`;
  }
  return html + (inList ? "</ul>" : "");
}

// ---------- Loading ----------

async function loadData() {
  $("loading").hidden = false;
  try {
    if (state.mode === "demo") {
      state.data = await (await fetch("data/dashboard-sim.json")).json();
    } else {
      const res = await fetch(`/api/dashboard?source=${state.source}`);
      if (!res.ok) throw new Error((await res.json()).error || "Server error");
      state.data = await res.json();
    }
    $("load-error").hidden = true;
    render();
  } catch (err) {
    $("load-error").hidden = false;
    $("load-error").textContent = `Couldn't load the dashboard: ${err.message}`;
  } finally {
    $("loading").hidden = true;
  }
}

async function start() {
  if (location.protocol === "file:") {
    $("loading").hidden = true;
    $("load-error").hidden = false;
    $("load-error").innerHTML = "This page was opened as a file, so it can't load data. Start the site with " +
      "<code>.venv/bin/python server.py</code> and open <a href=\"http://localhost:8000/dashboard.html\">localhost:8000/dashboard.html</a>.";
    return;
  }
  // Is server.py running? If so, use live data; otherwise fall back to the demo file.
  try {
    const res = await fetch("/api/dashboard?source=real");
    if (!res.ok) throw new Error();
    state.mode = "live";
    state.source = recall("dashboard-source") || "real";
  } catch {
    state.mode = "demo";
    state.source = "sim";
  }
  setUpSourceSwitch();
  await loadData();
}

function setUpSourceSwitch() {
  const note = $("mode-note");
  if (state.mode === "demo") {
    note.hidden = false;
    note.innerHTML = "🧪 <strong>Demo with simulated data.</strong> Five months of made-up training with planted plateaus. " +
      "My real logs, the Log tab and AI summaries stay on my own computer.";
    return;
  }
  const sw = $("source-switch");
  sw.hidden = false;
  for (const btn of sw.querySelectorAll("button")) {
    btn.setAttribute("aria-checked", btn.dataset.source === state.source);
    btn.addEventListener("click", () => {
      state.source = btn.dataset.source;
      remember("dashboard-source", state.source);
      for (const b of sw.querySelectorAll("button")) b.setAttribute("aria-checked", b === btn);
      state.lift = null;
      loadData();
    });
  }
}

// ---------- Rendering ----------

function render() {
  const d = state.data;
  const dash = $("dashboard");
  const note = $("mode-note");
  if (state.mode === "live") {
    note.hidden = state.source !== "sim";
    note.innerHTML = "🧪 Showing <strong>simulated</strong> data, not your real logs.";
  }
  if (!d.lifts.length) {
    dash.hidden = true;
    $("load-error").hidden = false;
    $("load-error").innerHTML = 'No sets logged yet. Add some from the <a href="log.html">Log</a> tab.';
    return;
  }
  dash.hidden = false;
  if (!state.lift || !d.lifts.some((l) => l.name === state.lift)) state.lift = d.lifts[0].name;

  renderCards();
  renderLiftSwitch();
  renderLiftCharts();
  renderModel();
  renderWeeks();
}

function renderCards() {
  const box = $("lift-cards");
  box.replaceChildren();
  for (const lift of state.data.lifts) {
    const card = document.createElement("div");
    card.className = "card lift-card";
    const change = lift.change_vs_last == null ? "" :
      `<span class="delta ${lift.change_vs_last >= 0 ? "up" : "down"}">${signed(lift.change_vs_last)} lbs vs last session</span>`;
    // Status = icon + words, never color alone.
    card.innerHTML = `
      <div class="muted">${title(lift.name)}</div>
      <div class="big">${fmt(lift.latest_e1rm)} <span class="unit">lbs</span></div>
      ${change}
      <div class="lift-status"><span aria-hidden="true">${lift.status.icon}</span> <strong>${title(lift.status.status)}</strong><br><span class="muted">${lift.status.text}</span></div>`;
    box.append(card);
  }
}

function renderLiftSwitch() {
  const sw = $("lift-switch");
  sw.replaceChildren();
  for (const lift of state.data.lifts) {
    const btn = document.createElement("button");
    btn.setAttribute("role", "radio");
    btn.setAttribute("aria-checked", lift.name === state.lift);
    btn.textContent = title(lift.name);
    btn.addEventListener("click", () => {
      state.lift = lift.name;
      renderLiftSwitch();
      renderLiftCharts();
      renderForecast();
      renderPredictionChart();
    });
    sw.append(btn);
  }
}

function currentLift() {
  return state.data.lifts.find((l) => l.name === state.lift);
}

function renderLiftCharts() {
  const c = colors();
  const lift = currentLift();
  const best = "Best set (that day)";
  const trend = `Trend (${state.data.window}-session avg)`;
  const color = { field: "series", type: "nominal", scale: { domain: [best, trend], range: [c.blue, c.orange] } };
  const x = { field: "date", type: "temporal", title: null, axis: { format: "%b %d" } };
  // zero: false - starting at 0 would squash real progress into a flat line.
  const y = { field: "value", type: "quantitative", title: "Estimated 1RM (lbs)", scale: { zero: false } };
  const tooltip = [
    { field: "date", type: "temporal", title: "Date", format: "%b %d, %Y" },
    { field: "series", title: "Series" },
    { field: "value", type: "quantitative", title: "e1RM (lbs)", format: ".1f" },
  ];
  const sessions = lift.sessions.flatMap((s) => [
    { date: s.date, series: best, value: s.e1rm },
    { date: s.date, series: trend, value: s.e1rm_rolling },
  ]);
  const layers = [];
  if (lift.plateaus.length) {
    layers.push({
      data: { values: lift.plateaus },
      mark: { type: "rect", color: c.plateau, opacity: 0.2 },
      encoding: {
        x: { field: "start", type: "temporal" }, x2: { field: "end" },
        tooltip: [{ field: "start", type: "temporal", title: "Plateau from", format: "%b %d" },
                  { field: "end", type: "temporal", title: "to", format: "%b %d" }],
      },
    });
  }
  layers.push(
    { data: { values: sessions.filter((s) => s.series === trend) }, mark: { type: "line", strokeWidth: 2 },
      encoding: { x, y, color, tooltip } },
    { data: { values: sessions.filter((s) => s.series === best) }, mark: { type: "circle", size: 64, opacity: 1 },
      encoding: { x, y, color, tooltip } },
  );
  draw("#chart-e1rm", { height: 320, layer: layers });

  const notes = [];
  if (lift.plateaus.length) notes.push("🟨 Shaded = plateau flagged by the trend detector.");
  if (lift.sessions.length < 3) notes.push(`Only ${lift.sessions.length} session(s) of ${lift.name} so far. The trend becomes meaningful after a few more.`);
  $("e1rm-note").textContent = notes.join(" ");

  if (lift.trend.length) {
    draw("#chart-rate", {
      height: 200,
      layer: [
        { data: { values: [{ y: 0 }] }, mark: { type: "rule", color: c.muted, strokeDash: [4, 4] }, encoding: { y: { field: "y", type: "quantitative" } } },
        { data: { values: lift.trend }, mark: { type: "line", point: { size: 40 }, strokeWidth: 2, color: c.blue },
          encoding: {
            x: { field: "week_end", type: "temporal", title: "Week ending", axis: { format: "%b %d" } },
            y: { field: "slope_lbs_per_week", type: "quantitative", title: "Progress rate (lbs/week)" },
            tooltip: [{ field: "week_end", type: "temporal", title: "Week ending", format: "%b %d" },
                      { field: "slope_lbs_per_week", title: "lbs/week", format: "+.1f" },
                      { field: "stalled_weeks", title: "Stalled weeks in a row" }],
          } },
      ],
    });
  } else {
    $("chart-rate").textContent = "Not enough sessions yet to measure a trend.";
  }

  draw("#chart-volume", {
    height: 220,
    data: { values: lift.volume },
    // A time unit gives each week a slot, so bars get a real width (not hairlines).
    mark: { type: "bar", color: c.blue, cornerRadiusTopLeft: 4, cornerRadiusTopRight: 4, width: { band: 0.7 } },
    encoding: {
      x: { field: "week", timeUnit: "yearweek", type: "temporal", title: "Week starting", axis: { format: "%b %d" } },
      y: { field: "total_volume", type: "quantitative", title: "Volume (lbs × reps)" },
      tooltip: [{ field: "week", type: "temporal", title: "Week of", format: "%b %d" },
                { field: "total_volume", title: "Volume", format: ",.0f" },
                { field: "sets", title: "Sets" }],
    },
  });
}

// ---------- Prediction model ----------

function renderModel() {
  const m = state.data.model;
  $("model-ready").hidden = !m.ready;
  $("model-empty").hidden = m.ready;
  if (!m.ready) {
    $("model-empty").innerHTML = `The model needs about ${m.min_sessions} sessions to learn from; there are ${m.have} so far.` +
      (state.mode === "live" ? " Switch to <strong>Simulated demo</strong> above to see it in action." : "");
    return;
  }
  const scores = Object.fromEntries(m.scores.map((s) => [s.model, s]));
  const bestScore = scores[m.best_model], base = scores.baseline;
  $("model-text").innerHTML =
    `Trained on ${m.n_train} older sessions, tested on the ${m.n_test} most recent ones. ` +
    `The <strong>${m.best_model}</strong> model was off by <strong>${fmt(bestScore.MAE)} lbs</strong> on average, ` +
    `vs <strong>${fmt(base.MAE)} lbs</strong> for just guessing "same as last session" ` +
    `(${Math.round(100 * (1 - bestScore.MAE / base.MAE))}% better).`;

  const names = { baseline: 'Same as last session', linear: "Linear regression", forest: "Random forest" };
  $("score-table").innerHTML = m.scores.map((s) =>
    `<tr><td>${names[s.model]}${s.model === m.best_model ? " ✓" : ""}</td><td class="num">${fmt(s.MAE)}</td><td class="num">${fmt(s.RMSE)}</td></tr>`).join("");

  // Sliders start at the last week's averages.
  const r = state.data.recent_recovery;
  const setSlider = (id, value, fallback) => { $(id).value = value == null ? fallback : value; };
  setSlider("in-sleep", r.sleep_hours == null ? null : Math.round(r.sleep_hours * 2) / 2, 7.5);
  setSlider("in-sore", r.soreness == null ? null : Math.round(r.soreness), 3);
  setSlider("in-energy", r.energy == null ? null : Math.round(r.energy), 6);
  for (const id of ["in-sleep", "in-sore", "in-energy", "in-morning"]) $(id).oninput = renderForecast;
  renderForecast();
  renderPredictionChart();

  const c = colors();
  draw("#chart-importance", {
    height: 170,
    data: { values: m.importances.map((i) => ({ ...i, feature: i.feature.replace(/_/g, " ") })) },
    mark: { type: "bar", color: c.blue, cornerRadiusEnd: 4 },
    encoding: {
      x: { field: "importance", type: "quantitative", title: "Importance", axis: { format: "%" } },
      y: { field: "feature", type: "nominal", sort: "-x", title: null },
      tooltip: [{ field: "feature" }, { field: "importance", format: ".0%" }],
    },
  });
  $("importance-note").textContent = state.source === "sim"
    ? "On simulated data this only recovers the effects built into the simulator, not real-world ones." : "";
}

function renderPredictionChart() {
  const m = state.data.model;
  if (!m.ready) return;
  const c = colors();
  const rows = m.predictions.filter((p) => p.exercise === state.lift);
  if (!rows.length) {
    $("chart-predictions").textContent = `No ${state.lift} sessions in the test period.`;
    return;
  }
  const actual = "Actual", predicted = `Predicted (${m.best_model})`, base = "Same as last session";
  const values = rows.flatMap((p) => [
    { date: p.date, series: actual, value: p.e1rm },
    { date: p.date, series: predicted, value: p[m.best_model] },
    { date: p.date, series: base, value: p.baseline },
  ]);
  const enc = {
    x: { field: "date", type: "temporal", title: null, axis: { format: "%b %d" } },
    y: { field: "value", type: "quantitative", title: "e1RM (lbs)", scale: { zero: false } },
    color: { field: "series", type: "nominal", scale: { domain: [actual, predicted, base], range: [c.blue, c.orange, c.aqua] } },
    tooltip: [{ field: "date", type: "temporal", format: "%b %d" }, { field: "series" }, { field: "value", format: ".1f", title: "e1RM (lbs)" }],
  };
  draw("#chart-predictions", {
    height: 260,
    title: `${title(state.lift)}: held-out test sessions`,
    layer: [
      { data: { values: values.filter((v) => v.series !== actual) }, mark: { type: "line", strokeWidth: 2 }, encoding: enc },
      { data: { values: values.filter((v) => v.series === actual) }, mark: { type: "circle", size: 64, opacity: 1 }, encoding: enc },
    ],
  });
}

// The linear model is just a formula, so the page can run it with no Python:
// predicted e1RM = last e1RM + intercept + sum(coefficient x feature).
function renderForecast() {
  const m = state.data.model;
  if (!m.ready) return;
  const sleep = +$("in-sleep").value, sore = +$("in-sore").value, energy = +$("in-energy").value;
  $("out-sleep").textContent = `${sleep} h`;
  $("out-sore").textContent = sore;
  $("out-energy").textContent = energy;
  $("forecast-title").textContent = `Forecast your next ${state.lift} session`;

  const f = m.forecaster;
  const start = f && f.lifts[state.lift];
  if (!start) {
    $("forecast-value").textContent = "–";
    $("forecast-change").textContent = "";
    $("forecast-note").textContent = `Not enough ${state.lift} history for a forecast yet.`;
    return;
  }
  const x = { ...start, sleep_hours: sleep, soreness: sore, energy, is_morning: $("in-morning").checked ? 1 : 0 };
  for (const name of f.features) if (name.startsWith("lift_")) x[name] = name === `lift_${state.lift}` ? 1 : 0;
  const change = f.intercept + f.features.reduce((sum, name) => sum + f.coefficients[name] * x[name], 0);
  const predicted = start.prev_e1rm + change;
  $("forecast-value").textContent = `${fmt(predicted)} lbs`;
  $("forecast-change").textContent = `${signed(change)} vs last session`;
  $("forecast-note").textContent = `Linear regression (it runs right in the page). Typical miss: ±${Math.round(m.linear_mae)} lbs.`;
}

// ---------- Weekly summary ----------

function renderWeeks() {
  const sel = $("week-select");
  sel.replaceChildren(...state.data.weeks.map((w, i) => {
    const d = new Date(w.week_end + "T00:00:00");
    return new Option(`Week ending ${d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`, i);
  }));
  sel.onchange = renderSummary;
  const ai = $("ai-button");
  ai.hidden = !state.data.ai_available;
  ai.onclick = writeAiSummary;
  $("ai-note").textContent = state.data.ai_available
    ? "AI summaries send this week's numbers (not your raw logs) to the Anthropic API. Each one is a small paid request."
    : state.mode === "live"
      ? "No API key set, so summaries are rule-based. Set ANTHROPIC_API_KEY before starting server.py to enable AI summaries."
      : "";
  renderSummary();
}

function weekKey() {
  return `${state.source}|${state.data.weeks[+$("week-select").value].week_end}`;
}

function renderSummary() {
  const week = state.data.weeks[+$("week-select").value];
  const ai = state.aiSummaries[weekKey()];
  $("summary-text").innerHTML = markdown(ai || week.summary);
  $("ai-status").textContent = "";
}

async function writeAiSummary() {
  const week = state.data.weeks[+$("week-select").value];
  const btn = $("ai-button");
  btn.disabled = true;
  $("ai-status").className = "status";
  $("ai-status").textContent = "Asking Claude…";
  try {
    const res = await fetch("/api/summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: state.source, week_end: week.week_end }),
    });
    const reply = await res.json();
    if (!res.ok) throw new Error(reply.error || "Couldn't write the summary.");
    state.aiSummaries[weekKey()] = reply.summary + "\n\n_Written by Claude from this week's numbers. Suggestions, not medical advice._";
    renderSummary();
  } catch (err) {
    $("ai-status").className = "status error";
    $("ai-status").textContent = `${err.message} Showing the rule-based summary.`;
  } finally {
    btn.disabled = false;
  }
}

// Redraw charts when the system switches between light and dark mode.
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => state.data && render());

start();
