"""Real-world check: do the trend-line ideas hold up on real lifters' meet histories?

Run from the project folder with:  python -m src.openpowerlifting   (a few minutes)
Needs data/opl/openpowerlifting-latest.zip (public domain, from
https://openpowerlifting.gitlab.io/opl-csv/). Writes website/data/opl.json.

The simulation benchmark (src/benchmark.py) proves the methods work when the
world behaves like my simulator. This tests them on the real thing: competition
results of thousands of real lifters. Two questions:

1. Forecasting: does a trend line through recent meets predict a lifter's next
   meet better than "same as last meet"?
2. Plateaus: when the trend line is flat, is that lifter really less likely to
   set a PR at their next meet?

What this can't test: the app's prediction model also uses sleep, soreness and
energy, which meet results don't record. So this checks the trend-line method
underneath the detector and forecaster, on a much slower timescale (meets are
months apart, training sessions are days apart).

This page uses data from the OpenPowerlifting project, https://www.openpowerlifting.org.
"""
import json
import time
import zipfile

import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

from src.analysis import DATA_DIR, PROJECT_ROOT

ZIP = DATA_DIR / "opl" / "openpowerlifting-latest.zip"
CLEAN = DATA_DIR / "opl" / "raw_sbd.parquet"
POINTS = DATA_DIR / "opl" / "points.parquet"  # cached prediction points (slow to build)
OUT = PROJECT_ROOT / "website" / "data" / "opl.json"

LIFTS = {"squat": "Best3SquatKg", "bench": "Best3BenchKg", "deadlift": "Best3DeadliftKg"}
MIN_MEETS = 6          # need some history before and after each prediction
MIN_HISTORY = 3        # meets before the one we predict from
MAX_GAP_DAYS = 730     # a 2+ year layoff is a different lifter coming back; skip those predictions
TUNE_SHARE = 0.25      # 25% of lifters for choosing settings, the rest for scoring
N_BOOT = 1000
SEED = 0

# Forecast settings tried on the tuning lifters.
WINDOWS = [3, 4, 5]                     # meets in the trend line
DAMPING = [0.0, 0.05, 0.1, 0.15, 0.25, 0.5, 0.75, 1.0]  # 0 = "same as last meet", 1 = full straight-line extrapolation

# Plateau call at meet scale. Fixed in advance rather than tuned: "flat" means the
# trend line through the last 4 meets isn't going up at all. (The app's 0.5%/week
# is for weekly training data; meet-to-meet progress is far slower, so that cutoff
# would call nearly everyone stalled.)
PLATEAU_WINDOW = 4
PLATEAU_SLOPE = 0.0
THRESHOLDS = [-0.1, -0.05, -0.02, 0.0, 0.02, 0.05, 0.1]  # % per week, for the sensitivity table


# ---------- 1. Load and clean ----------

def prepare():
    """Raw full-power meets for lifters with 6+ meets, one row per lifter per meet."""
    if CLEAN.exists():
        return pd.read_parquet(CLEAN)
    print("Reading the OpenPowerlifting CSV (about a minute)...")
    cols = ["Name", "Sex", "Event", "Equipment", "Age", "BodyweightKg", "Place", "Date", "Tested", *LIFTS.values()]
    with zipfile.ZipFile(ZIP) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        with z.open(name) as f:
            df = pd.read_csv(f, usecols=cols, dtype={"Place": str, "Tested": str}, low_memory=False)

    # Same lifts the app tracks, without supportive gear (gear changes the numbers a lot).
    df = df[(df["Event"] == "SBD") & (df["Equipment"] == "Raw")]
    # Disqualified / no-show / guest results aren't real performances.
    df = df[~df["Place"].isin(["DQ", "DD", "NS", "G"])]
    df = df.rename(columns={v: k for k, v in LIFTS.items()})
    # Negative values mean all attempts failed; keep only completed lifts.
    df = df[(df[list(LIFTS)] > 0).all(axis=1)]
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    # A lifter entered in two divisions at one meet appears twice: keep the best total.
    df["total"] = df[list(LIFTS)].sum(axis=1)
    df = df.sort_values("total").drop_duplicates(["Name", "Date"], keep="last")
    counts = df["Name"].value_counts()
    df = df[df["Name"].isin(counts[counts >= MIN_MEETS].index)]
    df = df.sort_values(["Name", "Date"]).reset_index(drop=True)
    df = df[["Name", "Sex", "Age", "BodyweightKg", "Tested", "Date", *LIFTS, "total"]]
    df.to_parquet(CLEAN)
    return df


def slope_pct_per_week(days, values):
    """Least-squares slope, as % of the lifter's average per week (unit-free, like the app)."""
    d = days - days.mean()
    denom = (d ** 2).sum()
    if denom == 0:
        return np.nan
    per_day = (d * (values - values.mean())).sum() / denom
    return 100 * per_day * 7 / values.mean()


# ---------- 2. One row per prediction point ----------

def prediction_points(df):
    """For every lifter, lift and meet i (with enough history): the facts known at
    meet i, and what happened at meet i+1."""
    rows = []
    for name, g in df.groupby("Name", sort=False):
        days = ((g["Date"] - g["Date"].iloc[0]).dt.days).to_numpy(float)
        for lift in LIFTS:
            v = g[lift].to_numpy(float)
            for i in range(MIN_HISTORY, len(v) - 1):
                gap = days[i + 1] - days[i]
                if gap > MAX_GAP_DAYS:
                    continue
                row = {"name": name, "lift": lift, "i": i, "actual": v[i + 1], "last": v[i],
                       "gap_days": gap, "best_so_far": v[: i + 1].max(),
                       "pr_next": v[i + 1] > v[: i + 1].max(),
                       # Baseline plateau rule: no PR at either of the last 2 meets.
                       "no_pr_2": v[i] <= v[: i - 1].max() and v[i - 1] <= v[: i - 1].max(),
                       "mean3": v[i - 2: i + 1].mean()}
                for w in WINDOWS:
                    lo = max(0, i - w + 1)
                    row[f"slope{w}"] = slope_pct_per_week(days[lo: i + 1], v[lo: i + 1])
                rows.append(row)
    return pd.DataFrame(rows)


def trend_forecast(p, window, damping):
    """Last meet plus a (damped) continuation of the recent trend to the next meet's date.

    Why damp: straight-line extrapolation assumes progress continues at the same
    rate, but gains slow down and meets are months apart. Damping shrinks the
    trend toward "same as last time"; the tuning lifters decide by how much.
    """
    weekly = p[f"slope{window}"].fillna(0) / 100 * p["last"]   # kg per week
    return p["last"] + damping * weekly * p["gap_days"] / 7


def per_lifter_error(p, pred):
    """Mean absolute error per lifter, as % of their lift (so lifters compare fairly)."""
    err = 100 * (p["actual"] - pred).abs() / p["actual"]
    return err.groupby(p["name"]).mean()


def boot_ratio_gain(model, last, rng):
    """% smaller average error than "same as last meet", resampling lifters.

    A ratio of averages, not an average of per-lifter ratios: a lifter whose
    last-meet guess was exactly right would otherwise divide by zero.
    """
    model, last = np.asarray(model), np.asarray(last)
    gain = lambda i: 100 * (1 - model[i].mean() / last[i].mean())
    draws = [gain(rng.integers(0, len(model), len(model))) for _ in range(N_BOOT)]
    return [float(gain(slice(None))), *map(float, np.percentile(draws, [2.5, 97.5]))]


def boot_mean(x, rng):
    x = np.asarray(x)
    draws = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(N_BOOT)]
    return [float(x.mean()), *map(float, np.percentile(draws, [2.5, 97.5]))]


# ---------- 3. Forecasting ----------

def forecasting(tune, test, rng):
    grid = []
    for w in WINDOWS:
        for a in DAMPING:
            grid.append({"window": w, "damping": a,
                         "mae_pct": float(per_lifter_error(tune, trend_forecast(tune, w, a)).mean())})
    best = min(grid, key=lambda g: g["mae_pct"])

    errors = pd.DataFrame({
        "last": per_lifter_error(test, test["last"]),
        "mean3": per_lifter_error(test, test["mean3"]),
        "trend": per_lifter_error(test, trend_forecast(test, best["window"], best["damping"])),
        "full_trend": per_lifter_error(test, trend_forecast(test, best["window"], 1.0)),
    })
    out = {"tuned": best, "grid": grid, "lifters": len(errors), "predictions": len(test), "methods": {}}
    for m in errors:
        out["methods"][m] = {"mae_pct": boot_mean(errors[m], rng)}
        if m != "last":
            same = bool(np.allclose(errors[m], errors["last"]))  # e.g. tuned trend with zero damping
            out["methods"][m].update({
                "improvement_pct": boot_ratio_gain(errors[m], errors["last"], rng),
                "beats_last_share": float((errors[m] < errors["last"]).mean()),
                "wilcoxon_p": None if same else float(wilcoxon(errors[m], errors["last"]).pvalue),
            })
    return out


# ---------- 4. Plateaus ----------

def pr_rates(p, flag, rng):
    """PR rate at the next meet for flagged vs. not-flagged points, resampling lifters."""
    names = p["name"].to_numpy()
    lifters, idx = np.unique(names, return_inverse=True)
    # Per-lifter counts, so each bootstrap draw is a cheap sum.
    f = flag.to_numpy(bool)
    pr = p["pr_next"].to_numpy(bool)
    n_f = np.bincount(idx, f, len(lifters))
    pr_f = np.bincount(idx, f & pr, len(lifters))
    n_n = np.bincount(idx, ~f, len(lifters))
    pr_n = np.bincount(idx, ~f & pr, len(lifters))

    def rates(s):
        a, b = pr_f[s].sum() / n_f[s].sum(), pr_n[s].sum() / n_n[s].sum()
        return a, b, b - a

    point = rates(slice(None))
    draws = np.array([rates(rng.integers(0, len(lifters), len(lifters))) for _ in range(N_BOOT)])
    lo, hi = np.percentile(draws, [2.5, 97.5], axis=0)
    keys = ["pr_rate_flagged", "pr_rate_not_flagged", "difference"]
    out = {k: [float(point[j]), float(lo[j]), float(hi[j])] for j, k in enumerate(keys)}
    out["flagged_share"] = float(f.mean())
    return out


def auc(score, outcome):
    """Chance a random no-PR point has a lower slope than a random PR point (0.5 = useless)."""
    from scipy.stats import rankdata
    r = rankdata(score)
    pos = outcome.to_numpy(bool)
    n1, n0 = pos.sum(), (~pos).sum()
    return float((r[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def plateaus(test, rng):
    s = test[f"slope{PLATEAU_WINDOW}"]
    ok = s.notna()
    t, s = test[ok], s[ok]
    flagged = s < PLATEAU_SLOPE
    out = {
        "settings": {"window_meets": PLATEAU_WINDOW, "slope_pct_per_week": PLATEAU_SLOPE},
        "points": int(len(t)), "lifters": int(t["name"].nunique()),
        "overall_pr_rate": float(t["pr_next"].mean()),
        "detector": pr_rates(t, flagged, rng),
        "baseline": pr_rates(t, t["no_pr_2"], rng),
        "auc_slope": auc(s.to_numpy(), t["pr_next"]),
        "sensitivity": [],
        "by_lift": [],
    }
    for th in THRESHOLDS:
        f = s < th
        out["sensitivity"].append({"threshold": th, "flagged_share": float(f.mean()),
                                   "pr_rate_flagged": float(t.loc[f, "pr_next"].mean()),
                                   "pr_rate_not_flagged": float(t.loc[~f, "pr_next"].mean())})
    for lift in LIFTS:
        m = (t["lift"] == lift).to_numpy()
        out["by_lift"].append({"lift": lift, **pr_rates(t[m], flagged[m], rng)})
    return out


# ---------- 5. One example lifter, for a picture ----------

def example_lifter(df, prefer=("Taylor Atwood", "Ray Williams", "John Haack", "Jesus Olivares")):
    """A well-known lifter's public meet history, with the plateau call at each meet."""
    name = next((n for n in prefer if (df["Name"] == n).sum() >= MIN_MEETS), None)
    if name is None:
        return None
    g = df[df["Name"] == name].reset_index(drop=True)
    days = ((g["Date"] - g["Date"].iloc[0]).dt.days).to_numpy(float)
    meets = []
    for i, r in g.iterrows():
        row = {"date": r["Date"].strftime("%Y-%m-%d")}
        for lift in LIFTS:
            v = g[lift].to_numpy(float)
            lo = max(0, i - PLATEAU_WINDOW + 1)
            slope = slope_pct_per_week(days[lo: i + 1], v[lo: i + 1]) if i >= PLATEAU_WINDOW - 1 else np.nan
            row[lift] = float(r[lift])
            row[f"{lift}_flat"] = bool(slope < PLATEAU_SLOPE) if not np.isnan(slope) else None
        meets.append(row)
    return {"name": name, "meets": meets}


def main():
    t0 = time.time()
    df = prepare()
    print(f"{df['Name'].nunique():,} lifters with {MIN_MEETS}+ raw full-power meets ({len(df):,} meets)")
    if POINTS.exists():
        p = pd.read_parquet(POINTS)
    else:
        print("Building prediction points (several minutes, cached afterwards)...")
        p = prediction_points(df)
        p.to_parquet(POINTS)

    # Split by lifter, not by row: all of a lifter's meets go to one side, so the
    # tuning never sees anyone it's later scored on.
    rng = np.random.default_rng(SEED)
    names = p["name"].unique()
    tune_names = set(rng.choice(names, int(len(names) * TUNE_SHARE), replace=False))
    is_tune = p["name"].isin(tune_names)
    tune, test = p[is_tune], p[~is_tune]

    data_date = ZIP.stat().st_mtime
    out = {
        "generated": pd.Timestamp.now().strftime("%Y-%m-%d"),
        "data": {"lifters": int(df["Name"].nunique()), "meets": int(len(df)),
                 "first_meet": df["Date"].min().strftime("%Y"), "last_meet": df["Date"].max().strftime("%Y-%m-%d"),
                 "tune_lifters": len(tune_names), "test_lifters": int(test["name"].nunique()),
                 "min_meets": MIN_MEETS, "max_gap_days": MAX_GAP_DAYS},
        "forecast": forecasting(tune, test, rng),
        "plateau": plateaus(test, rng),
        "example": example_lifter(df),
    }
    from src.dashboard_data import clean
    OUT.write_text(json.dumps(clean(out)))
    print(f"Wrote {OUT.relative_to(PROJECT_ROOT)} in {time.time() - t0:.0f}s\n")
    report(out)


def report(out):
    ci = lambda v, f="{:.1f}": f"{f.format(v[0])} [{f.format(v[1])}-{f.format(v[2])}]"
    f = out["forecast"]
    print(f"Forecasting the next meet ({f['lifters']:,} test lifters, {f['predictions']:,} predictions). "
          f"Tuned: window {f['tuned']['window']} meets, damping {f['tuned']['damping']}")
    for m, v in f["methods"].items():
        extra = ""
        if "improvement_pct" in v:
            pval = "n/a (same as last)" if v["wilcoxon_p"] is None else f"{v['wilcoxon_p']:.1g}"
            extra = f"  {ci(v['improvement_pct'])}% vs last, wins {100 * v['beats_last_share']:.0f}%, p={pval}"
        print(f"  {m:10s} MAE {ci(v['mae_pct'], '{:.2f}')}%{extra}")
    p = out["plateau"]
    pct = "{:.1%}"
    print(f"\nPR at next meet ({p['points']:,} points, {p['lifters']:,} lifters, overall {p['overall_pr_rate']:.1%}):")
    for k in ["detector", "baseline"]:
        d = p[k]
        print(f"  {k:9s} flagged {d['flagged_share']:.0%}: PR {ci(d['pr_rate_flagged'], pct)} if flagged vs "
              f"{ci(d['pr_rate_not_flagged'], pct)} if not  (diff {ci(d['difference'], pct)})")
    print(f"  AUC of the slope for predicting a PR: {p['auc_slope']:.3f}")
    for b in p["by_lift"]:
        print(f"    {b['lift']:9s} PR {b['pr_rate_flagged'][0]:.1%} flagged vs {b['pr_rate_not_flagged'][0]:.1%} not")
    if out["example"]:
        print(f"\nExample lifter: {out['example']['name']} ({len(out['example']['meets'])} meets)")


if __name__ == "__main__":
    main()
