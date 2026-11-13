# Gym Progress Tracker

Class project (FAU, Data Science & Analytics). Tracks lifting + recovery data, models performance, and uses an LLM to generate plain-English insights. Full plan is in PROJECT_PROPOSAL.md.

## Stack
- Python 3.11+, pandas, numpy, scikit-learn, matplotlib; website in plain HTML/CSS/JS with Vega-Lite charts
- Data lives in `data/` as CSV (no database needed)

## Data files
- `data/workouts.csv`: one row per set (date, exercise, weight_lbs, reps, rpe, set_type). `set_type` is optional: blank = normal set, `fatigued` = done tired (e.g. a single after back-offs)
- `data/daily.csv`: one row per day (date, sleep_hours, bodyweight_lbs, soreness, energy, protein_g, train_time)
- Templates: `data/workouts_template.csv`, `data/daily_template.csv`
- The CSVs are the official log. Entries come from the website's Log tab: `server.py` serves `website/` at localhost:8000 and appends to the CSVs (local only; run with `.venv/bin/python`). `data/workouts.numbers` is the old log and is only read if no CSV exists.
- Dashboard: the website's Dashboard tab (`website/dashboard.html` + `dashboard.js`, charts in Vega-Lite). Python computes everything in `src/dashboard_data.py`; `server.py` serves it at `/api/dashboard` (live) and `/api/summary` (AI, on request).
- GitHub Pages: static only, no Python. `python -m src.export_site` writes `website/data/dashboard-sim.json`; without the server, the Dashboard tab falls back to that simulated demo. Never export real data there. Rerun the export after changing analysis code.
- Simulated data: `python -m src.simulate` writes `data/sim/` (workouts, daily, and `truth.csv` with the planted plateaus). Fake data, fine to commit; never mix it into the real CSVs.
- Modules: `src/plateau.py` (trend + plateau flags), `src/evaluate_plateau.py` (scores the detector vs. sim truth), `src/model.py` (baseline vs. linear vs. random forest, time-based split), `src/summary.py` (weekly stats → Claude summary, rule-based fallback without a key)
- Benchmark: `python -m src.benchmark` (~70 s) scores the plateau detector and prediction model on hundreds of random simulated lifters (tune on one set, test on another, cluster-bootstrap 95% CIs, vs. simple baselines) and writes `website/data/benchmark.json` for the Results page (`website/results.html` + `results.js`). Rerun after changing analysis code. `simulate(seed, athlete=...)` takes an athlete profile; leaving it out gives the demo lifter.

## Conventions
- Weights in lbs. RPE, soreness, and energy are 1-10.
- Estimated 1RM uses Epley: `weight * (1 + reps / 30)`, except a single (reps = 1) is just its weight
- Fatigued sets are excluded from e1RM (best-per-day, trends, plateaus) but still count toward volume
- Keep code simple and readable; I need to explain every part of this in class.
- Put reusable logic in `src/`, exploratory work in `notebooks/`, the website in `website/` (served by `server.py`).
- Comment the "why" for any modeling choice.

## Build order
1. `src/analysis.py`: load/clean data, compute e1RM, weekly volume, rolling averages
2. Charts of e1RM over time per lift
3. Plateau detection (rolling slope; flag stalls over N weeks)
4. Baseline model vs. naive baseline (last session e1RM), report MAE/RMSE
5. Driver analysis (sleep, soreness, time of day)
6. LLM weekly summary (Anthropic API; read key from env var `ANTHROPIC_API_KEY`, never hardcode)
7. Dashboard: the website's Dashboard tab (replaced the original Streamlit plan)

## Rules
- Never commit API keys or real personal data; keep `.env` in `.gitignore`.
- Explain what you're doing and why before large changes. I'm learning, not just shipping.
- Run the code to check it works before saying it's done.
- Insights from the LLM are suggestions, not medical advice.
