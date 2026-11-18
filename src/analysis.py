"""Core analysis: load and clean training logs, then build the features
the rest of the project uses (e1RM, weekly volume, rolling averages).

Run from the project folder with:  python -m src.analysis
"""
from pathlib import Path

import numpy as np
import pandas as pd

# Build paths from this file's location so the code works no matter
# which folder you run it from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# Set types that don't reflect fresh strength, so they're left out of e1RM.
# A fatigued single makes you look weaker than you are, which would drag the
# trend down and could trigger a false plateau flag.
EXCLUDE_FROM_E1RM = {"fatigued"}


def data_path(name):
    """Pick which file to load for a log ("workouts" or "daily").

    Order: the CSV the website's Log tab saves to (workouts.csv), then a
    Numbers file (workouts.numbers), then the sample template. The template
    fallback lets the code run before any real logging starts.
    """
    for candidate in (f"{name}.csv", f"{name}.numbers"):
        if (DATA_DIR / candidate).exists():
            return DATA_DIR / candidate
    return DATA_DIR / f"{name}_template.csv"


def read_table(path):
    """Read a log into a DataFrame, from either a Numbers file or a CSV.

    Why support Numbers: I logged there before the website's Log tab existed,
    and reading it directly means no exporting to CSV. Only the first table on
    the first sheet is used; its first row must be the column names.
    """
    path = Path(path)
    if path.suffix == ".numbers":
        # Imported here, not at the top, so the analysis also runs in a web
        # browser (website/upload.js), where this Mac-only reader doesn't exist.
        from numbers_parser import Document
        rows = Document(path).sheets[0].tables[0].rows(values_only=True)
        return pd.DataFrame(rows[1:], columns=[str(c).strip() for c in rows[0]])
    return pd.read_csv(path)


# ---------- Loading + cleaning ----------

def epley_1rm(weight, reps):
    """Estimated one-rep max (Epley formula).

    Why Epley: it's simple and widely used. It gets less accurate above
    roughly 10 reps, so heavy sets give the most trustworthy estimates.

    Why singles are special-cased: plain Epley credits a single with
    weight * 1.033 (a 315 single becomes ~325), but a single already IS
    a one-rep max, so its e1RM is just the weight lifted.
    """
    return np.where(reps == 1, weight, weight * (1 + reps / 30))


def load_workouts(path=None):
    """Load the per-set log, clean it, and add e1RM and volume columns."""
    df = read_table(path or data_path("workouts"))
    # Convert explicitly: an empty file (or a Numbers table with blank cells)
    # wouldn't otherwise be recognized as dates/numbers, breaking the math.
    # errors="coerce" turns typos like "225lbs" into blanks, dropped below.
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ["weight_lbs", "reps", "rpe"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Typos like "Squat " vs "squat" would otherwise count as different lifts.
    df["exercise"] = df["exercise"].str.strip().str.lower()

    # A set without weight or reps can't be used for anything, so drop it.
    # RPE is optional, so a missing RPE is allowed.
    df = df.dropna(subset=["date", "exercise", "weight_lbs", "reps"])
    df = df[(df["weight_lbs"] > 0) & (df["reps"] > 0)].copy()

    # set_type is optional: blank means a normal set. Older logs without
    # the column still load; every set is just treated as normal.
    if "set_type" not in df.columns:
        df["set_type"] = ""
    df["set_type"] = df["set_type"].fillna("").astype(str).str.strip().str.lower()

    df["e1rm"] = epley_1rm(df["weight_lbs"], df["reps"])
    # Volume (weight x reps) is a standard measure of total training load.
    df["volume"] = df["weight_lbs"] * df["reps"]
    return df.sort_values("date").reset_index(drop=True)


def load_daily(path=None):
    """Load the per-day recovery log (sleep, bodyweight, soreness, ...)."""
    df = read_table(path or data_path("daily"))
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df = df.drop_duplicates(subset="date", keep="last")  # one row per day
    return df.sort_values("date").reset_index(drop=True)


# ---------- Features ----------

def best_e1rm_per_day(workouts):
    """Top estimated 1RM for each exercise on each day.

    Why the max instead of the average: later sets are tired sets, so the
    best set is the closest thing to what you could actually lift that day.

    Fatigued sets are skipped (see EXCLUDE_FROM_E1RM). They still count
    toward weekly volume, because they were real training work.
    """
    fresh = workouts[~workouts["set_type"].isin(EXCLUDE_FROM_E1RM)]
    return fresh.groupby(["exercise", "date"], as_index=False)["e1rm"].max()


def weekly_volume(workouts):
    """Total volume and number of sets per exercise per week (weeks start Monday)."""
    df = workouts.copy()
    df["week"] = df["date"].dt.to_period("W-SUN").dt.start_time
    return (
        df.groupby(["exercise", "week"], as_index=False)
        .agg(total_volume=("volume", "sum"), sets=("volume", "size"))
    )


def add_rolling_e1rm(daily_best, window=3):
    """Add a rolling average of e1RM over each lift's last `window` sessions.

    Why: single sessions are noisy (bad sleep, a rushed warm-up). A rolling
    average shows the underlying trend, which plateau detection builds on.
    min_periods=1 means the first sessions still get a value.
    """
    df = daily_best.sort_values(["exercise", "date"]).copy()
    df["e1rm_rolling"] = df.groupby("exercise")["e1rm"].transform(
        lambda s: s.rolling(window, min_periods=1).mean()
    )
    return df


if __name__ == "__main__":
    workouts = load_workouts()
    daily = load_daily()

    print("Workouts:", len(workouts), "sets")
    print("Daily log:", len(daily), "days\n")

    print("Best e1RM per day (with rolling average):")
    rolling = add_rolling_e1rm(best_e1rm_per_day(workouts))
    print(rolling.round({"e1rm": 1, "e1rm_rolling": 1}).to_string(index=False))

    print("\nWeekly volume:")
    print(weekly_volume(workouts).to_string(index=False))
