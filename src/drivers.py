"""What drives good sessions? How sleep, soreness and training time relate to performance.

Two uses:
  analyze(workouts, daily)    the Dashboard's "What drives your sessions" panel
  python -m src.drivers       checks the method on simulated lifters with KNOWN
                              effects, and how many weeks of logs it needs.
                              Writes website/data/drivers.json (Results page).

Method, in two steps:
1. How good was each session *for that lifter at that time*? Compare the
   session's e1RM to the median of the same lift's nearby sessions (3 before,
   3 after). "+2%" means a better-than-usual day. Comparing a lifter to
   themselves removes differences between people (some are just stronger),
   and the nearby window follows long-term progress, so slow gains aren't
   mistaken for good days.
2. A linear regression of that day-quality on sleep, soreness and morning
   training, all at once. Doing them together matters: a poorly-slept day is
   often a sore day too, and looking at one at a time would blame one for the
   other's effect.

This explains past sessions, so using sessions after the day is fine here.
For predicting the future, src/model.py only ever looks backwards.
"""
import json

import numpy as np
import pandas as pd

from src.analysis import PROJECT_ROOT, best_e1rm_per_day

OUT = PROJECT_ROOT / "website" / "data" / "drivers.json"

NEIGHBORS = 3        # sessions on each side that define "normal" for that time
MIN_NEIGHBORS = 4    # need at least this many to trust the comparison
MIN_SESSIONS = 20    # below this, the regression is mostly noise (see the power check below)
DRIVERS = {
    "sleep_hours": "Each extra hour of sleep",
    "soreness": "Each extra point of soreness",
    "is_morning": "Training in the morning",
}


def session_quality(workouts):
    """% above or below normal for every session (one row per lift per day)."""
    best = best_e1rm_per_day(workouts)
    rows = []
    for lift, s in best.groupby("exercise"):
        s = s.sort_values("date").reset_index(drop=True)
        v = s["e1rm"].to_numpy(float)
        for i in range(len(v)):
            nearby = np.r_[v[max(0, i - NEIGHBORS):i], v[i + 1:i + 1 + NEIGHBORS]]
            if len(nearby) < MIN_NEIGHBORS:
                continue
            # Median, not mean: one very good or bad neighbor shouldn't move "normal".
            rows.append({"exercise": lift, "date": s.loc[i, "date"], "quality_pct": 100 * (v[i] / np.median(nearby) - 1)})
    return pd.DataFrame(rows, columns=["exercise", "date", "quality_pct"])


def driver_data(workouts, daily):
    """Session quality joined with that day's check-in. Days missing any driver are left out."""
    q = session_quality(workouts)
    if q.empty or daily.empty:
        return pd.DataFrame()
    d = daily[["date", "sleep_hours", "soreness", "train_time"]].copy()
    for col in ["sleep_hours", "soreness"]:
        d[col] = pd.to_numeric(d[col], errors="coerce")
    tt = d["train_time"].fillna("").astype(str).str.strip().str.lower()
    # A blank training time on a training day is unknown, not "not morning".
    d["is_morning"] = np.where(tt == "", np.nan, (tt == "morning").astype(float))
    return q.merge(d.drop(columns="train_time"), on="date", how="inner").dropna(subset=list(DRIVERS))


def fit(data):
    """Least-squares regression with standard errors. Returns {driver: (estimate, se)}.

    A driver that never changes (say, every session in the evening) can't be
    estimated, so it's left out rather than breaking the fit.
    """
    cols = [c for c in DRIVERS if data[c].nunique() > 1]
    if not cols or len(data) <= len(cols) + 1:
        return {}
    X = np.column_stack([np.ones(len(data)), data[cols].to_numpy(float)])
    y = data["quality_pct"].to_numpy(float)
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    sigma2 = resid @ resid / (len(y) - X.shape[1])
    se = np.sqrt(np.diag(sigma2 * np.linalg.pinv(X.T @ X)))
    return {c: (float(coef[k + 1]), float(se[k + 1])) for k, c in enumerate(cols)}


def analyze(workouts, daily):
    """The Dashboard panel: each driver's effect with a 95% interval, or why not yet."""
    data = driver_data(workouts, daily)
    out = {"ready": len(data) >= MIN_SESSIONS, "have": int(len(data)), "need": MIN_SESSIONS, "effects": []}
    if not out["ready"]:
        return out
    for c, (est, se) in fit(data).items():
        lo, hi = est - 1.96 * se, est + 1.96 * se
        out["effects"].append({
            "driver": c, "label": DRIVERS[c], "estimate": est, "low": lo, "high": hi,
            # Only call it a finding when the whole interval is on one side of zero.
            "clear": bool(lo > 0 or hi < 0),
        })
    return out


# ---------- Checking the method on simulated lifters ----------

# The effects built into src/simulate.py (the day_factor formula), in % per unit.
TRUE_EFFECTS = {"sleep_hours": 1.0, "soreness": -0.4, "is_morning": -1.2}
WEEKS_TO_TRY = [8, 13, 26]
N_BOOT = 1000


def benchmark(seeds=range(0, 200)):
    from src.benchmark import make_lifter  # the same random lifters as the main benchmark

    lifters = []
    for seed in seeds:
        _, workouts, daily, _ = make_lifter(seed)
        lifters.append((workouts, daily))

    # 1. Pooled over all lifters: does the method recover the true effects?
    per_lifter = [driver_data(w, d) for w, d in lifters]
    pooled = pd.concat(per_lifter, ignore_index=True)
    point = fit(pooled)
    rng = np.random.default_rng(0)
    draws = {c: [] for c in DRIVERS}
    for _ in range(N_BOOT):  # resample whole lifters, as in src/benchmark.py
        sample = pd.concat([per_lifter[i] for i in rng.integers(0, len(per_lifter), len(per_lifter))], ignore_index=True)
        for c, (est, _) in fit(sample).items():
            draws[c].append(est)
    recovery = [{
        "driver": c, "label": DRIVERS[c], "true": TRUE_EFFECTS[c], "estimate": point[c][0],
        "low": float(np.percentile(draws[c], 2.5)), "high": float(np.percentile(draws[c], 97.5)),
    } for c in DRIVERS]

    # 2. One lifter at a time: after N weeks of logging, how often is each effect clearly found?
    power = []
    start = lifters[0][0]["date"].min()
    for weeks in WEEKS_TO_TRY:
        found = {c: 0 for c in DRIVERS}
        sessions, n = [], 0
        for w, d in lifters:
            cutoff = start + pd.Timedelta(weeks=weeks)
            data = driver_data(w[w["date"] < cutoff], d[d["date"] < cutoff])
            sessions.append(len(data))
            if len(data) < 8:
                n += 1
                continue
            n += 1
            for c, (est, se) in fit(data).items():
                lo, hi = est - 1.96 * se, est + 1.96 * se
                if (TRUE_EFFECTS[c] > 0 and lo > 0) or (TRUE_EFFECTS[c] < 0 and hi < 0):
                    found[c] += 1  # right direction, interval clear of zero
        power.append({"weeks": weeks, "median_sessions": float(np.median(sessions)),
                      **{f"found_{c}": found[c] / n for c in DRIVERS}})

    return {"lifters": len(lifters), "sessions": int(len(pooled)), "recovery": recovery, "power": power,
            "neighbors": NEIGHBORS, "min_sessions": MIN_SESSIONS}


if __name__ == "__main__":
    from src.dashboard_data import clean

    result = benchmark()
    OUT.write_text(json.dumps(clean(result)))
    print(f"Wrote {OUT.relative_to(PROJECT_ROOT)}\n")
    print(f"Recovering known effects ({result['lifters']} lifters, {result['sessions']:,} sessions), % per unit:")
    for r in result["recovery"]:
        inside = r["low"] <= r["true"] <= r["high"]
        print(f"  {r['label']:30s} true {r['true']:+.2f}  found {r['estimate']:+.2f} [{r['low']:+.2f} to {r['high']:+.2f}]"
              f"  {'covers the truth' if inside else 'MISSES the truth'}")
    print("\nOne lifter at a time: share of lifters where the effect is clearly found")
    for p in result["power"]:
        print(f"  {p['weeks']:2d} weeks (~{p['median_sessions']:.0f} sessions): "
              + ", ".join(f"{c} {100 * p[f'found_{c}']:.0f}%" for c in DRIVERS))
