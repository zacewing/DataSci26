"""Everything the website's Dashboard tab shows, as plain JSON-ready data.

The math all happens here, in Python (reusing the other src/ modules). The web
page only draws what it's given. That keeps one source of truth for every number.

Used two ways:
  server.py           builds it live from the logs, at /api/dashboard
  python -m src.export_site   saves the simulated demo as a file for GitHub Pages
"""
import math

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from src.analysis import (
    DATA_DIR,
    add_rolling_e1rm,
    best_e1rm_per_day,
    load_daily,
    load_workouts,
    weekly_volume,
)
from src.model import MIN_SESSIONS, build_dataset, evaluate, feature_columns, fill_missing
from src.plateau import current_status, plateau_periods, weekly_trend
from src.summary import offline_summary, weekly_stats

SOURCES = {"real": "My logs", "sim": "Simulated demo"}


def load_source(source):
    if source == "sim":
        return load_workouts(DATA_DIR / "sim/workouts.csv"), load_daily(DATA_DIR / "sim/daily.csv")
    return load_workouts(), load_daily()


def clean(value):
    """Make a value safe for JSON: dates -> 'YYYY-MM-DD', NaN -> None, numpy -> Python."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        # 6 significant digits, not 2 decimals: model coefficients can be tiny
        # (e.g. 0.00012 per lb of volume) and still matter once multiplied.
        return None if math.isnan(value) else float(f"{float(value):.6g}")
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def records(df):
    return clean(df.to_dict(orient="records"))


def linear_forecaster(workouts, daily):
    """A linear model the web page can run itself, so the forecast sliders
    work even on GitHub Pages where there's no Python.

    Why linear (not the random forest): a linear model is just a formula,
    prediction = last e1RM + intercept + sum(coefficient x feature),
    which takes a few lines of JavaScript. A forest is hundreds of trees.
    """
    data = build_dataset(workouts, daily)
    if len(data) < MIN_SESSIONS:
        return None
    cols = feature_columns(data)
    fill_values = data[cols].mean()
    model = LinearRegression().fit(fill_missing(data[cols], fill_values), data["e1rm"] - data["prev_e1rm"])

    # Starting values for each lift's next session: its latest history.
    best = best_e1rm_per_day(workouts)
    per_lift = {}
    for lift, s in best.groupby("exercise"):
        if f"lift_{lift}" not in cols:
            continue
        s = s.sort_values("date")
        gaps = s["date"].diff().dt.days.dropna()
        last_day = s["date"].iloc[-1]
        recent = workouts[(workouts["exercise"] == lift) & (workouts["date"] > last_day - pd.Timedelta(days=7))]
        per_lift[lift] = {
            "prev_e1rm": s["e1rm"].iloc[-1],
            "prev3_mean": s["e1rm"].tail(3).mean(),
            "days_since_last": gaps.median() if len(gaps) else 3,
            "volume_last_7d": recent["volume"].sum(),
        }
    return {
        "features": cols,
        "coefficients": dict(zip(cols, model.coef_)),
        "intercept": model.intercept_,
        "lifts": per_lift,
    }


def build(source="real", window=3):
    workouts, daily = load_source(source)
    out = {"source": source, "source_label": SOURCES[source], "window": window, "lifts": []}
    if workouts.empty:
        return clean(out)

    best = add_rolling_e1rm(best_e1rm_per_day(workouts), window=window)
    volume = weekly_volume(workouts)
    trend = weekly_trend(best)
    lifts = sorted(best["exercise"].unique())
    statuses = current_status(trend, lifts)
    periods = plateau_periods(trend)

    for lift in lifts:
        s = best[best["exercise"] == lift]
        out["lifts"].append({
            "name": lift,
            "latest_e1rm": s["e1rm"].iloc[-1],
            "latest_date": s["date"].iloc[-1],
            "change_vs_last": s["e1rm"].iloc[-1] - s["e1rm"].iloc[-2] if len(s) > 1 else None,
            "status": statuses[lift],
            "sessions": records(s[["date", "e1rm", "e1rm_rolling"]]),
            "trend": records(trend[trend["exercise"] == lift][["week_end", "slope_lbs_per_week", "stalled_weeks", "plateau"]]),
            "plateaus": records(periods[periods["exercise"] == lift][["start", "end"]]),
            "volume": records(volume[volume["exercise"] == lift][["week", "total_volume", "sets"]]),
        })

    # Prediction model results.
    results = evaluate(workouts, daily)
    if results is None:
        out["model"] = {"ready": False, "min_sessions": MIN_SESSIONS, "have": len(build_dataset(workouts, daily))}
    else:
        scores = results["scores"].set_index("model")
        best_model = scores.drop("baseline")["MAE"].idxmin()
        out["model"] = {
            "ready": True,
            "n_train": results["n_train"],
            "n_test": results["n_test"],
            "scores": records(results["scores"]),
            "best_model": best_model,
            "predictions": records(results["predictions"]),
            "importances": [{"feature": k, "importance": v} for k, v in results["importances"].head(6).items()],
            "forecaster": linear_forecaster(workouts, daily),
            "linear_mae": scores.loc["linear", "MAE"],
        }

    # Recent recovery averages: starting points for the forecast sliders.
    recent = daily.tail(7)
    out["recent_recovery"] = {
        col: (recent[col].mean() if col in recent and recent[col].notna().any() else None)
        for col in ["sleep_hours", "soreness", "energy"]
    }

    # One rule-based summary per week (newest first). The AI version is made
    # on request by server.py, since it calls a paid API.
    week_ends = sorted(workouts["date"].dt.to_period("W-SUN").dt.end_time.dt.normalize().unique(), reverse=True)
    out["weeks"] = [
        {"week_end": pd.Timestamp(w), "summary": offline_summary(weekly_stats(workouts, daily, w))}
        for w in week_ends
    ]
    return clean(out)
