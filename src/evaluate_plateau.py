"""How well does the plateau detector find real plateaus?

Run from the project folder with:  python -m src.evaluate_plateau

Simulated data has a known "true" strength for every week, so we know when a
lift was really stalled. This runs the detector on 20 different simulated
histories and scores its weekly calls against that truth.
"""
import pandas as pd

from src.analysis import best_e1rm_per_day, epley_1rm
from src.plateau import weekly_trend
from src.simulate import simulate

# A lift is "really" stalled when its true strength grows under 0.25%/week.
# Kept separate from the detector's threshold so every setting is judged
# against the same answer key.
TRUE_STALL_PCT = 0.25


def true_stalls(truth):
    t = truth.sort_values(["exercise", "week"]).copy()
    # Average true gain over the last 2 weeks, as % of the lift.
    t["gain_pct"] = t.groupby("exercise")["true_1rm"].diff(2) / 2 / t["true_1rm"] * 100
    t["true_stall"] = t["gain_pct"] < TRUE_STALL_PCT
    t["week_end"] = t["week"] + pd.Timedelta(days=6)
    return t.dropna(subset=["gain_pct"])


def evaluate(seeds=range(20)):
    results = []
    for seed in seeds:
        workouts, _, truth = simulate(seed=seed)
        workouts["e1rm"] = epley_1rm(workouts["weight_lbs"], workouts["reps"])
        trend = weekly_trend(best_e1rm_per_day(workouts))
        merged = true_stalls(truth).merge(trend, on=["exercise", "week_end"], how="left")
        # Weeks too early for the detector count as "no plateau flagged".
        merged["flagged"] = merged["plateau"].fillna(False).astype(bool)
        results.append(merged)
    all_weeks = pd.concat(results)

    rows = []
    for lift, g in list(all_weeks.groupby("exercise")) + [("all lifts", all_weeks)]:
        hit = (g["flagged"] & g["true_stall"]).sum()
        rows.append({
            "lift": lift,
            # Share of weeks where the detector's call matched the truth.
            "agreement": (g["flagged"] == g["true_stall"]).mean(),
            # When it says "plateau", how often is that right?
            "precision": hit / max(g["flagged"].sum(), 1),
            # Of the truly stalled weeks, how many did it catch?
            "recall": hit / max(g["true_stall"].sum(), 1),
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    print(evaluate().to_string(index=False, formatters={
        c: "{:.0%}".format for c in ["agreement", "precision", "recall"]
    }))
