# Gym Progress Tracker

A data science project (FAU, Data Science & Analytics) that turns lifting logs into answers: it estimates
strength over time, flags plateaus, predicts the next session, and writes a plain-English weekly summary.

**Live site:** https://zacewing.github.io/DataSci26/ · **Results:** https://zacewing.github.io/DataSci26/results.html

## What it does

| Page | What's on it |
|---|---|
| Project | The question, the method, build progress |
| Dashboard | Estimated 1RM trends, plateau flags, next-session forecast, what drives your sessions, weekly summary. Simulated demo online, real logs locally, and anyone can upload their own CSV (analyzed in their browser) |
| Results | How well the methods work, on hundreds of simulated lifters and 30,000+ real powerlifters |
| Log | Log sets and daily check-ins, with a rest timer (works when running locally) |

## Key results

Every score comes from lifters the methods were never tuned on, with 95% confidence intervals from a cluster bootstrap.

- **Plateau detection (200 simulated lifters):** F1 56%, beating the rule of thumb "no PR in 3 weeks" (52%).
  The first settings, tuned on a single scripted lifter, scored only 43%; the benchmark caught that and the app
  now uses re-tuned settings.
- **Next-session prediction (120 simulated lifters):** linear regression has 16% smaller error (95% CI 13–19%)
  than "same as last session", and beats it for 82% of lifters (Wilcoxon p < 0.001).
- **Real lifters (OpenPowerlifting, 30,556 test lifters):** when the trend line through the last 4 meets was flat,
  lifters set a PR at their next meet 20% of the time, versus 46% otherwise. Trend lines barely beat
  "same as last meet" for forecasting meets (0.3% better), an honest negative result.
- **What drives good sessions (200 simulated lifters, known effects):** recovers the effect of sleep (+1.00% per hour, true +1.00)
  and morning training (−1.21%, true −1.20); underestimates soreness (−0.30%, true −0.40). A single lifter needs about
  13 weeks of logs to see the sleep effect clearly.

## How it works

- **Estimated 1RM** (Epley): `weight × (1 + reps / 30)`; a single counts as-is, and sets done tired are left out.
- **Plateau detection:** fit a straight line through the last 6 weeks of sessions; under 0.5% gain per week = plateau.
- **Prediction:** linear regression and random forest on recent form and recovery, compared with "same as last session"
  using a time-based train/test split.
- **What drives good sessions:** each session's e1RM vs. the median of the same lift's 3 sessions before and after,
  regressed on sleep, soreness and morning training together.
- **Upload your own log:** the Dashboard runs the same `src/` Python code in the visitor's browser with
  [Pyodide](https://pyodide.org), so the online site needs no server and files never leave the visitor's computer.
- **Weekly summary:** the Claude API turns the week's numbers into a short check-in (rule-based fallback without a key).

## Run it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py          # then open http://localhost:8000
```

Real logs live in `data/workouts.csv` and `data/daily.csv`. They are git-ignored and never leave the computer.

## Reproduce the results

```bash
.venv/bin/python -m src.benchmark         # simulated lifters, ~70 s
.venv/bin/python -m src.openpowerlifting  # real lifters, ~8 min the first time
.venv/bin/python -m src.drivers           # what drives good sessions, ~10 s
.venv/bin/python -m src.export_site       # demo data for the online dashboard
```

`src.openpowerlifting` needs the public-domain OpenPowerlifting bulk CSV in `data/opl/`
(download `openpowerlifting-latest.zip` from https://openpowerlifting.gitlab.io/opl-csv/bulk-csv.html).
All random seeds are fixed, so the numbers come out the same every run.

## Code map

| File | Purpose |
|---|---|
| `src/analysis.py` | Load and clean logs, e1RM, weekly volume, rolling averages |
| `src/plateau.py` | Trend slopes and plateau flags |
| `src/model.py` | Next-session prediction models and their evaluation |
| `src/simulate.py` | Simulated lifters with known "true" strength |
| `src/benchmark.py` | Tune/test benchmark on hundreds of random simulated lifters |
| `src/openpowerlifting.py` | Real-lifter check on OpenPowerlifting meet histories |
| `src/drivers.py` | What drives good sessions (sleep, soreness, training time) |
| `website/upload.js` | Runs the analysis in the browser for uploaded logs (Pyodide) |
| `src/summary.py` | Weekly stats and the AI summary |
| `src/dashboard_data.py`, `src/export_site.py` | Data for the Dashboard page |
| `server.py` | Local web server: serves the site and saves Log entries |
| `website/` | The site (plain HTML/CSS/JS, charts in Vega-Lite) |

## Limitations

The main benchmark is simulated, so it shows the methods work when the world behaves like the simulator.
The real-lifter check uses meet results (months apart, no sleep or soreness data), so it tests the trend-line method,
not the full prediction model. Findings are predictive, not causal. See the Results page for more.

## Credits

This project uses data from the [OpenPowerlifting](https://www.openpowerlifting.org) project (public domain).
A copy of the data is available at https://gitlab.com/openpowerlifting/opl-data.

Insights from the AI summary are suggestions, not medical advice.
