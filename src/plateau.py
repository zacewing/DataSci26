"""Plateau detection: is each lift still getting stronger, or has it stalled?

Method: every week, fit a straight line through the lift's session e1RMs from
the last few weeks. The slope of that line is the rate of progress in lbs per
week. If the slope stays near zero for several weeks in a row, it's a plateau.
"""
import numpy as np
import pandas as pd

# These settings were chosen by python -m src.benchmark: tried on 80 random
# simulated lifters, they gave the best F1 (balance of catching real stalls and
# not crying wolf), then were confirmed on 200 different lifters. The first
# version (8 weeks, 0.35%, 2 weeks) was tuned on one scripted lifter and scored
# worse than the simple "no PR in 3 weeks" rule on new lifters.

# Why 6 weeks: day-to-day e1RM swings by a few percent (sleep, stress, warm-ups).
# A shorter window lets one good or bad day swing the slope; a longer one reacts
# slowly to a real stall. 6 weeks was the best trade-off in the benchmark.
WINDOW_DAYS = 42
MIN_SESSIONS = 6  # don't trust a line through fewer points than this

# Why a percentage: "1 lb/week" means a lot more on bench than on deadlift.
# Below 0.5% of the lift per week (~2 lbs/week at 400) counts as stalled.
STALL_PCT_PER_WEEK = 0.5

# Why 1 week: the 6-week trend line already smooths out single bad days, so
# waiting for a second flat week mostly just delayed real catches.
PLATEAU_WEEKS = 1


def weekly_trend(daily_best, window_days=WINDOW_DAYS, stall_pct=STALL_PCT_PER_WEEK,
                 plateau_weeks=PLATEAU_WEEKS):
    """Rate of progress for each lift at the end of every week.

    daily_best: output of analysis.best_e1rm_per_day (exercise, date, e1rm).
    Returns one row per lift per week: slope (lbs/week), slope as % of the
    lift, whether that week counts as stalled, and how many weeks in a row.
    The settings default to the app's; src/benchmark.py passes others to test them.
    """
    rows = []
    for lift, sessions in daily_best.groupby("exercise"):
        sessions = sessions.sort_values("date")
        # Week ends = every Sunday from the first session to the last.
        week_ends = pd.date_range(
            sessions["date"].min() + pd.Timedelta(days=6),
            sessions["date"].max() + pd.Timedelta(days=6),
            freq="W-SUN",
        )
        for week_end in week_ends:
            in_window = sessions[
                (sessions["date"] > week_end - pd.Timedelta(days=window_days))
                & (sessions["date"] <= week_end)
            ]
            if len(in_window) < MIN_SESSIONS:
                continue
            days = (in_window["date"] - in_window["date"].min()).dt.days
            slope_per_day = np.polyfit(days, in_window["e1rm"], 1)[0]
            slope = slope_per_day * 7
            rows.append({
                "exercise": lift,
                "week_end": week_end,
                "slope_lbs_per_week": slope,
                "slope_pct_per_week": 100 * slope / in_window["e1rm"].mean(),
            })
    trend = pd.DataFrame(rows, columns=["exercise", "week_end", "slope_lbs_per_week", "slope_pct_per_week"])
    return add_plateau_flags(trend, stall_pct, plateau_weeks)


def add_plateau_flags(trend, stall_pct=STALL_PCT_PER_WEEK, plateau_weeks=PLATEAU_WEEKS):
    """Turn weekly slopes into stalled / plateau calls.

    Kept separate from the slope fitting so the benchmark can try many
    thresholds without refitting every line (the slow part).
    """
    trend = trend.copy()
    trend["stalled"] = trend["slope_pct_per_week"] < stall_pct
    # Weeks in a row stalled: the count restarts after every non-stalled week.
    streak_id = (~trend["stalled"]).groupby(trend["exercise"]).cumsum()
    trend["stalled_weeks"] = trend["stalled"].astype(int).groupby([trend["exercise"], streak_id]).cumsum()
    trend["plateau"] = trend["stalled_weeks"] >= plateau_weeks
    return trend[[
        "exercise", "week_end", "slope_lbs_per_week", "slope_pct_per_week",
        "stalled", "stalled_weeks", "plateau",
    ]]


def current_status(trend, lifts):
    """Plain-language status for each lift, based on its most recent week."""
    statuses = {}
    for lift in lifts:
        t = trend[trend["exercise"] == lift]
        if t.empty:
            statuses[lift] = {
                "status": "not enough data", "icon": "⏳",
                "text": f"Needs about {MIN_SESSIONS} sessions over {WINDOW_DAYS // 7} weeks.",
            }
            continue
        last = t.iloc[-1]
        rate = f"{last['slope_lbs_per_week']:+.1f} lbs/week"
        if last["plateau"]:
            statuses[lift] = {"status": "plateau", "icon": "🛑",
                              "text": f"Stalled for {last['stalled_weeks']} weeks ({rate})."}
        elif last["stalled"]:
            statuses[lift] = {"status": "stalling", "icon": "⚠️",
                              "text": f"Flat for {last['stalled_weeks']} week(s) ({rate}). Watch it."}
        else:
            # "Slowing": still progressing, but at under half the rate of 4 weeks ago.
            earlier = t[t["week_end"] <= last["week_end"] - pd.Timedelta(weeks=4)]
            slowing = (
                not earlier.empty
                and earlier.iloc[-1]["slope_lbs_per_week"] > 0
                and last["slope_lbs_per_week"] < 0.5 * earlier.iloc[-1]["slope_lbs_per_week"]
            )
            statuses[lift] = {
                "status": "slowing" if slowing else "progressing",
                "icon": "↘️" if slowing else "✅",
                "text": f"{'Progress is slowing' if slowing else 'Progressing'} ({rate}).",
            }
        statuses[lift]["rate"] = last["slope_lbs_per_week"]
    return statuses


def plateau_periods(trend):
    """Turn weekly plateau flags into (lift, start, end) stretches, for charts.

    A plateau 'starts' when the stall streak began, not when it hit 3 weeks,
    so the shaded area covers the whole flat stretch.
    """
    periods = []
    for lift, t in trend.groupby("exercise"):
        t = t.sort_values("week_end").reset_index(drop=True)
        i = 0
        while i < len(t):
            if t.loc[i, "plateau"]:
                start_idx = i - t.loc[i, "stalled_weeks"] + 1
                j = i
                while j + 1 < len(t) and t.loc[j + 1, "plateau"]:
                    j += 1
                periods.append({
                    "exercise": lift,
                    "start": t.loc[start_idx, "week_end"] - pd.Timedelta(days=6),
                    "end": t.loc[j, "week_end"],
                })
                i = j + 1
            else:
                i += 1
    return pd.DataFrame(periods, columns=["exercise", "start", "end"])
