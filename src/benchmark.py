"""Benchmark: does the analysis work for lifters in general, not just one?

Run from the project folder with:  python -m src.benchmark   (takes a few minutes)
Writes website/data/benchmark.json for the website's Results page.

Why this exists: src/evaluate_plateau.py scores the detector on 20 copies of
ONE scripted lifter (only the noise changes), with settings chosen on that same
data. That shows the code runs, not that it generalizes. Here:

1. Hundreds of RANDOM lifters: different strength, rate of progress, weekly
   plan (2-5 days), day-to-day noise, and randomly placed plateaus,
   regressions and deloads.
2. Tune on one group, test on another. Settings are picked on TUNE lifters
   only; every reported score comes from TEST lifters the tuning never saw.
   Picking settings and scoring them on the same data overstates accuracy.
3. Confidence intervals by resampling whole lifters (a "cluster bootstrap").
   One lifter's weeks are correlated, so resampling single weeks would
   pretend we have more independent evidence than we do.
4. A baseline to beat: the common rule of thumb "no PR in 3 weeks = plateau".
5. The prediction model is scored per lifter against "same as last session",
   with a paired significance test (Wilcoxon signed-rank).

Everything here is simulated. Real logs are never read.
"""
import json
import time

import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

from src import model
from src.analysis import PROJECT_ROOT, best_e1rm_per_day, epley_1rm
from src.evaluate_plateau import true_stalls
from src.plateau import PLATEAU_WEEKS, STALL_PCT_PER_WEEK, WINDOW_DAYS, add_plateau_flags, weekly_trend
from src.simulate import simulate

OUT = PROJECT_ROOT / "website" / "data" / "benchmark.json"

WEEKS = 26                         # half a year per lifter
TUNE_SEEDS = range(10_000, 10_080)  # 80 lifters for choosing settings
TEST_SEEDS = range(0, 200)         # 200 different lifters for the final scores
MODEL_SEEDS = range(0, 120)        # the prediction model is slower, so 120 of the test lifters
N_BOOT = 2000                      # bootstrap resamples for each confidence interval

# A true plateau must last at least 3 weeks to count as an "episode". A single
# deload week is a planned dip, not a plateau worth flagging.
MIN_EPISODE_WEEKS = 3
# The detector needs a few flat weeks before it's sure, so a flag up to 2 weeks
# after a plateau ends still counts as catching it (and isn't a false alarm).
GRACE_WEEKS = 2

# The detector's first settings, chosen on one scripted lifter. Kept here so the
# Results page can show how they compare after the app switched to tuned ones.
ORIGINAL_SETTINGS = {"window_days": 56, "stall_pct": 0.35, "plateau_weeks": 2}

# Settings tried during tuning.
GRID_WINDOWS = [28, 42, 56, 70]               # days of history in each trend line
GRID_STALL_PCT = [0.1, 0.2, 0.3, 0.35, 0.4, 0.5, 0.6, 0.8]
GRID_PLATEAU_WEEKS = [1, 2, 3, 4]

# Weekly plans (0 = Monday), from minimal to high frequency.
PLANS = {
    "2 days/week": {1: ["squat", "bench"], 4: ["deadlift", "bench"]},
    "3 days/week": {0: ["squat", "bench"], 2: ["deadlift", "bench"], 4: ["squat", "bench"]},
    "4 days/week": {0: ["squat"], 1: ["bench"], 3: ["deadlift"], 4: ["bench", "squat"]},
    "5 days/week": {0: ["squat", "bench"], 1: ["deadlift"], 2: ["bench"], 3: ["squat"], 4: ["bench", "deadlift"]},
}


# ---------- Random lifters ----------

def random_story(rng, start):
    """One lift's hidden weekly strength: progress, then a random setback, repeat.

    Rates are % per week, so a 200 lb bench and a 500 lb deadlift are treated alike.
    """
    values, phases = [], []
    strength = start
    rate = rng.uniform(0.3, 1.2)  # typical intermediate gains, 0.3-1.2% per week
    while len(values) < WEEKS:
        for _ in range(rng.integers(4, 11)):
            strength *= 1 + rate / 100
            values.append(strength)
            phases.append("progressing")
        rate *= rng.uniform(0.7, 1.0)  # gains slow down over time (diminishing returns)
        r = rng.random()
        if r < 0.55:     # plateau: 3-8 flat weeks
            for _ in range(rng.integers(3, 9)):
                values.append(strength)
                phases.append("plateau")
        elif r < 0.75:   # regression: losing 0.4-1.5% a week for 2-4 weeks
            loss = rng.uniform(0.4, 1.5)
            for _ in range(rng.integers(2, 5)):
                strength *= 1 - loss / 100
                values.append(strength)
                phases.append("regressing")
        elif r < 0.9:    # deload: one lighter week, then back to where you were
            values.append(strength * (1 - rng.uniform(0.01, 0.02)))
            phases.append("deload")
        # else: no setback this time, keep progressing
    return values[:WEEKS], phases[:WEEKS]


def random_athlete(rng):
    squat = rng.uniform(225, 500)
    starts = {"squat": squat, "bench": squat * rng.uniform(0.6, 0.8), "deadlift": squat * rng.uniform(1.1, 1.35)}
    stories = {lift: random_story(rng, s) for lift, s in starts.items()}
    plan_name = rng.choice(list(PLANS))
    return {
        "weeks": WEEKS,
        "plan": PLANS[plan_name],
        "plan_name": str(plan_name),
        # Day-to-day swing in strength (SD): 0.6% is a very consistent lifter, 2.5% a noisy one.
        "noise": float(rng.uniform(0.006, 0.025)),
        "strength": lambda lift, week: (stories[lift][0][week], stories[lift][1][week]),
    }


def make_lifter(seed):
    # The lifter's profile uses its own random stream, separate from the one
    # simulate() uses for daily noise, so each part is reproducible on its own.
    athlete = random_athlete(np.random.default_rng(seed + 1_000_000))
    workouts, daily, truth = simulate(seed=seed, athlete=athlete)
    workouts["set_type"] = workouts["set_type"].fillna("")
    workouts["e1rm"] = epley_1rm(workouts["weight_lbs"], workouts["reps"])
    workouts["volume"] = workouts["weight_lbs"] * workouts["reps"]
    return athlete, workouts, daily, truth


# ---------- Scoring one lifter ----------

def no_pr_rule(best, labels, weeks=3):
    """Baseline: flag a plateau when the last 3 weeks had no new best e1RM."""
    flagged = []
    for _, row in labels.iterrows():
        s = best[best["exercise"] == row["exercise"]]
        cutoff = row["week_end"] - pd.Timedelta(weeks=weeks)
        before = s[s["date"] <= cutoff]["e1rm"]
        recent = s[(s["date"] > cutoff) & (s["date"] <= row["week_end"])]["e1rm"]
        flagged.append(len(before) > 0 and (recent.empty or recent.max() <= before.max()))
    return np.array(flagged)


def runs(mask):
    """Start and end index of each stretch of True values."""
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - 1))
            start = None
    return out


def score(labels, flagged):
    """Weekly confusion counts plus plateau-episode results for one lifter."""
    truth = labels["true_stall"].to_numpy()
    s = {
        "tp": int((flagged & truth).sum()), "fp": int((flagged & ~truth).sum()),
        "fn": int((~flagged & truth).sum()), "tn": int((~flagged & ~truth).sum()),
        "episodes": 0, "caught": 0, "delays": [], "flag_runs": 0, "false_runs": 0,
    }
    for lift in labels["exercise"].unique():
        idx = np.flatnonzero(labels["exercise"].to_numpy() == lift)
        t, f = truth[idx], flagged[idx]
        for a, b in runs(t):
            if b - a + 1 < MIN_EPISODE_WEEKS:
                continue
            s["episodes"] += 1
            hits = np.flatnonzero(f[a:b + GRACE_WEEKS + 1])
            if len(hits):
                s["caught"] += 1
                s["delays"].append(int(hits[0]))  # weeks from plateau start to first flag
        for a, b in runs(f):
            s["flag_runs"] += 1
            if not t[max(0, a - GRACE_WEEKS):b + 1].any():
                s["false_runs"] += 1  # flagged while truly progressing the whole time
    return s


def detector_inputs(seed):
    """Everything needed to score any detector setting on one lifter, computed once."""
    athlete, workouts, _, truth = make_lifter(seed)
    best = best_e1rm_per_day(workouts)
    labels = true_stalls(truth)[["exercise", "week_end", "true_stall"]].reset_index(drop=True)
    # Fitting trend lines is the slow part, so do it once per window size;
    # thresholds are applied afterwards with add_plateau_flags.
    slopes = {w: weekly_trend(best, window_days=w) for w in GRID_WINDOWS}
    return {"athlete": athlete, "best": best, "labels": labels, "slopes": slopes}


def detector_flags(inp, window_days, stall_pct, plateau_weeks):
    trend = add_plateau_flags(inp["slopes"][window_days], stall_pct, plateau_weeks)
    merged = inp["labels"].merge(trend[["exercise", "week_end", "plateau"]], on=["exercise", "week_end"], how="left")
    # Weeks too early for the detector (not enough sessions yet) count as "no plateau".
    return merged["plateau"].fillna(False).astype(bool).to_numpy()


# ---------- Combining lifters, with confidence intervals ----------

def summarize(scores):
    """Pooled metrics over a list of per-lifter scores."""
    tot = {k: sum(s[k] for s in scores) for k in ["tp", "fp", "fn", "tn", "episodes", "caught", "flag_runs", "false_runs"]}
    precision = tot["tp"] / max(tot["tp"] + tot["fp"], 1)
    recall = tot["tp"] / max(tot["tp"] + tot["fn"], 1)
    specificity = tot["tn"] / max(tot["tn"] + tot["fp"], 1)
    delays = [d for s in scores for d in s["delays"]]
    return {
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / max(precision + recall, 1e-9),
        # Average of "catches stalls" and "stays quiet when progressing". Unlike plain
        # accuracy, always answering the more common case can't score well.
        "balanced_accuracy": (recall + specificity) / 2,
        "episodes_caught": tot["caught"] / max(tot["episodes"], 1),
        "median_delay_weeks": float(np.median(delays)) if delays else float("nan"),
        "false_alarm_share": tot["false_runs"] / max(tot["flag_runs"], 1),
    }


def bootstrap(scores, rng, metric_fn=summarize):
    """Point estimate and 95% interval for every metric, resampling whole lifters."""
    point = metric_fn(scores)
    draws = {k: [] for k in point}
    n = len(scores)
    for _ in range(N_BOOT):
        sample = [scores[i] for i in rng.integers(0, n, n)]
        for k, v in metric_fn(sample).items():
            draws[k].append(v)
    return {k: [point[k], *np.nanpercentile(draws[k], [2.5, 97.5])] for k in point}


def paired_difference(scores_a, scores_b, rng, metric="f1"):
    """95% interval for (A - B) on the same lifters, resampled together."""
    n = len(scores_a)
    diffs = []
    for _ in range(N_BOOT):
        i = rng.integers(0, n, n)
        diffs.append(summarize([scores_a[j] for j in i])[metric] - summarize([scores_b[j] for j in i])[metric])
    point = summarize(scores_a)[metric] - summarize(scores_b)[metric]
    return [point, *np.percentile(diffs, [2.5, 97.5])]


# ---------- The prediction model across lifters ----------

def model_scores(seeds):
    """Per-lifter error of each predictor, as % of that lifter's e1RM."""
    rows = []
    for seed in seeds:
        _, workouts, daily, _ = make_lifter(seed)
        result = model.evaluate(workouts, daily)
        if result is None:
            continue
        p = result["predictions"]
        scale = p["e1rm"].mean()
        # Error as a % of the lifter's strength, so a 1,000 lb and a 1,500 lb
        # total can be averaged fairly.
        rows.append({"seed": seed, **{m: 100 * (p["e1rm"] - p[m]).abs().mean() / scale
                                      for m in ["baseline", "linear", "forest"]}})
    return pd.DataFrame(rows)


def model_summary(df, rng):
    out = {"lifters": len(df), "models": {}}
    for m in ["baseline", "linear", "forest"]:
        boot = [df[m].sample(len(df), replace=True, random_state=int(rng.integers(1 << 31))).mean() for _ in range(N_BOOT)]
        out["models"][m] = {"mae_pct": [df[m].mean(), *np.percentile(boot, [2.5, 97.5])]}
        if m == "baseline":
            continue
        gain = 100 * (1 - df[m] / df["baseline"])  # % smaller error than the baseline, per lifter
        boot = [gain.sample(len(gain), replace=True, random_state=int(rng.integers(1 << 31))).mean() for _ in range(N_BOOT)]
        out["models"][m].update({
            "improvement_pct": [gain.mean(), *np.percentile(boot, [2.5, 97.5])],
            "beats_baseline_share": float((df[m] < df["baseline"]).mean()),
            # Paired test: same lifters, two predictors. Wilcoxon doesn't assume
            # the errors are normally distributed.
            "wilcoxon_p": float(wilcoxon(df[m], df["baseline"]).pvalue),
        })
    return out


# ---------- Main ----------

def main():
    rng = np.random.default_rng(0)
    t0 = time.time()

    print(f"Simulating {len(TUNE_SEEDS)} tuning lifters...")
    tune = [detector_inputs(s) for s in TUNE_SEEDS]

    # 1. Sensitivity grid, on tuning lifters only.
    grid = []
    for w in GRID_WINDOWS:
        for pct in GRID_STALL_PCT:
            for pw in GRID_PLATEAU_WEEKS:
                scores = [score(i["labels"], detector_flags(i, w, pct, pw)) for i in tune]
                grid.append({"window_days": w, "stall_pct": pct, "plateau_weeks": pw, **summarize(scores)})
    # The rule of thumb on the same tuning lifters, so the trade-off chart compares like with like.
    baseline_tune = summarize([score(i["labels"], no_pr_rule(i["best"], i["labels"])) for i in tune])
    grid_df = pd.DataFrame(grid)
    best_row = grid_df.loc[grid_df["f1"].idxmax()]
    tuned = {"window_days": int(best_row["window_days"]), "stall_pct": float(best_row["stall_pct"]),
             "plateau_weeks": int(best_row["plateau_weeks"])}
    current = {"window_days": WINDOW_DAYS, "stall_pct": STALL_PCT_PER_WEEK, "plateau_weeks": PLATEAU_WEEKS}
    print(f"  best on tuning lifters: {tuned} (F1 {best_row['f1']:.2f})")

    # 2. Final scores on fresh test lifters.
    print(f"Simulating {len(TEST_SEEDS)} test lifters...")
    test = [detector_inputs(s) for s in TEST_SEEDS]
    cur_scores = [score(i["labels"], detector_flags(i, **current)) for i in test]
    orig_scores = [score(i["labels"], detector_flags(i, **ORIGINAL_SETTINGS)) for i in test]
    base_scores = [score(i["labels"], no_pr_rule(i["best"], i["labels"])) for i in test]

    def by_group(key, order):
        out = []
        for name in order:
            idx = [n for n, i in enumerate(test) if key(i["athlete"]) == name]
            if idx:
                out.append({"group": name, "lifters": len(idx),
                            "f1": bootstrap([cur_scores[n] for n in idx], rng)["f1"]})
        return out

    def noise_band(a):
        return "low (<1.2%)" if a["noise"] < 0.012 else "medium (1.2-1.8%)" if a["noise"] < 0.018 else "high (>1.8%)"

    detector = {
        # "current" = the settings in src/plateau.py right now; "tuned" = what this
        # run's tuning picked. If they differ, plateau.py may need updating.
        "current": {"settings": current, "metrics": bootstrap(cur_scores, rng)},
        "original": {"settings": ORIGINAL_SETTINGS, "metrics": bootstrap(orig_scores, rng)},
        "baseline": {"name": "No new best e1RM in 3 weeks", "metrics": bootstrap(base_scores, rng)},
        "tuned_settings": tuned,
        "f1_current_minus_baseline": paired_difference(cur_scores, base_scores, rng),
        "f1_current_minus_original": paired_difference(cur_scores, orig_scores, rng),
        "f1_original_minus_baseline": paired_difference(orig_scores, base_scores, rng),
        "by_noise": by_group(noise_band, ["low (<1.2%)", "medium (1.2-1.8%)", "high (>1.8%)"]),
        "by_plan": by_group(lambda a: a["plan_name"], list(PLANS)),
        "delays": [d for s in cur_scores for d in s["delays"]],
        "prevalence": sum(s["tp"] + s["fn"] for s in cur_scores) / sum(s["tp"] + s["fn"] + s["fp"] + s["tn"] for s in cur_scores),
    }

    # 3. Prediction model, per lifter.
    print(f"Scoring the prediction model on {len(MODEL_SEEDS)} test lifters...")
    models = model_summary(model_scores(MODEL_SEEDS), rng)

    out = {
        "generated": pd.Timestamp.now().strftime("%Y-%m-%d"),
        "design": {"weeks": WEEKS, "tune_lifters": len(TUNE_SEEDS), "test_lifters": len(TEST_SEEDS),
                   "model_lifters": len(MODEL_SEEDS), "bootstrap": N_BOOT,
                   "min_episode_weeks": MIN_EPISODE_WEEKS, "grace_weeks": GRACE_WEEKS},
        "detector": detector,
        "sensitivity": grid,
        "baseline_tune": baseline_tune,
        "model": models,
    }
    from src.dashboard_data import clean  # same JSON cleaning as the dashboard
    OUT.write_text(json.dumps(clean(out)))
    print(f"Wrote {OUT.relative_to(PROJECT_ROOT)} in {time.time() - t0:.0f}s\n")
    report(out)


def report(out):
    def ci(v, pct=True):
        f = (lambda x: f"{100 * x:.0f}%") if pct else (lambda x: f"{x:.1f}")
        return f"{f(v[0])} [{f(v[1])}-{f(v[2])}]"

    d = out["detector"]
    print(f"Plateau detector on {out['design']['test_lifters']} test lifters (95% CI):")
    for name in ["current", "original", "baseline"]:
        m = d[name]["metrics"]
        label = d[name].get("settings", d[name].get("name"))
        print(f"  {name:8s} F1 {ci(m['f1'])}  precision {ci(m['precision'])}  recall {ci(m['recall'])}  "
              f"caught {ci(m['episodes_caught'])}  delay {m['median_delay_weeks'][0]:.0f} wk  "
              f"false alarms {ci(m['false_alarm_share'])}   {label}")
    print(f"  F1 current - baseline: {ci(d['f1_current_minus_baseline'])}")
    print(f"  F1 current - original: {ci(d['f1_current_minus_original'])}")
    if d["tuned_settings"] != d["current"]["settings"]:
        print(f"  NOTE: tuning picked {d['tuned_settings']}, which differs from src/plateau.py")
    for g in d["by_noise"] + d["by_plan"]:
        print(f"    {g['group']:18s} n={g['lifters']:3d}  F1 {ci(g['f1'])}")
    m = out["model"]
    print(f"\nPrediction model on {m['lifters']} lifters (error as % of e1RM):")
    for name, v in m["models"].items():
        extra = ""
        if "improvement_pct" in v:
            extra = (f"  {ci(v['improvement_pct'], False)}% smaller error than baseline, "
                     f"beats it for {100 * v['beats_baseline_share']:.0f}% of lifters, p={v['wilcoxon_p']:.1g}")
        print(f"  {name:8s} MAE {ci(v['mae_pct'], False)}%{extra}")


if __name__ == "__main__":
    main()
