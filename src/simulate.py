"""Generate realistic made-up training data for testing and demos.

Run from the project folder with:  python -m src.simulate

Why simulate: plateau detection and prediction models need months of history,
and real logging has only just started. Simulated data lets us build and test
everything now. Because we *choose* when each lift plateaus, we can also check
whether the plateau detector finds the stalls we planted (see truth.csv).

Writes to data/sim/ (never touches the real logs):
  workouts.csv  one row per set, same columns as the real log
  daily.csv     one row per day, same columns as the real log
  truth.csv     the hidden "true" strength each week, with the phase we planted
"""
import numpy as np
import pandas as pd

from src.analysis import DATA_DIR

SIM_DIR = DATA_DIR / "sim"
START = pd.Timestamp("2026-05-25")  # a Monday
WEEKS = 21
SEED = 7  # fixed seed = the same "random" data every run, so results are reproducible


# ---------- The hidden truth: how strong you "really" are each week ----------
# Each lift gets a story, so the detector has something real to find.

def true_strength(lift, week):
    if lift == "squat":
        # Steady gains for 11 weeks, a 6-week plateau, a deload week, then gains again.
        if week <= 10:
            return 330 + 2.5 * week, "progressing"
        if week <= 16:
            return 355, "plateau"
        if week == 17:
            return 350, "deload"
        return 355 + 2.0 * (week - 17), "progressing"
    if lift == "bench":
        # Fast early gains that gradually slow down (diminishing returns), never fully flat.
        return 240 + 24 * (1 - np.exp(-week / 9)), "progressing"
    if lift == "deadlift":
        # Gains for 8 weeks, then stuck for the rest of the period (still stalled "today").
        if week <= 7:
            return 420 + 3.0 * week, "progressing"
        return 441, "plateau"
    raise ValueError(lift)


# Training plan: which lifts on which weekday (0 = Monday).
PLAN = {0: ["squat", "bench"], 2: ["deadlift", "bench"], 4: ["squat", "bench"]}
REP_CHOICES = {"squat": [3, 5], "bench": [3, 5, 8], "deadlift": [3, 5]}


def round_to_5(x):
    return float(5 * round(x / 5))


def simulate(seed=SEED):
    rng = np.random.default_rng(seed)
    days = pd.date_range(START, periods=WEEKS * 7, freq="D")
    sets, daily, truth = [], [], []

    bodyweight = 182.0
    soreness = 3.0
    for day in days:
        week = (day - START).days // 7

        # ----- Recovery for the day -----
        sleep = float(np.clip(rng.normal(7.3, 0.9), 4.5, 9.5))
        bodyweight += rng.normal(0.01, 0.35)
        # Soreness eases each day; training days push it back up (below).
        soreness = max(1.0, soreness * 0.6 + rng.normal(1.2, 0.6))
        energy = 5.5 + 1.3 * (sleep - 7.3) - 0.35 * (soreness - 3) + rng.normal(0, 1.0)

        lifts_today = PLAN.get(day.weekday(), [])
        if lifts_today and rng.random() < 0.08:  # skip ~8% of planned sessions
            lifts_today = []
        train_time = ""
        if lifts_today:
            train_time = "morning" if rng.random() < 0.25 else "evening"

        # ----- Training -----
        for lift in lifts_today:
            strength, _ = true_strength(lift, week)
            # How strong you are *today*: better sleep helps, soreness and
            # morning sessions hurt a little, plus random day-to-day noise.
            day_factor = (
                1
                + 0.010 * (sleep - 7.3)
                - 0.004 * (soreness - 3)
                - (0.012 if train_time == "morning" else 0)
                + rng.normal(0, 0.012)
            )
            day_max = strength * day_factor

            # Top set at about RPE 8 (roughly 2 reps left in the tank), so the
            # weight is what you could do for reps + 2.
            reps = int(rng.choice(REP_CHOICES[lift]))
            top = round_to_5(day_max / (1 + (reps + 2) / 30))
            rpe = float(np.clip(round(rng.normal(8, 0.5) * 2) / 2, 6.5, 9.5))
            sets.append((day, lift, top, reps, rpe, ""))

            # Back-off sets: lighter, same reps.
            backoff = round_to_5(top * 0.9)
            for _ in range(int(rng.integers(2, 4))):
                sets.append((day, lift, backoff, reps, max(6.0, rpe - 1.5), ""))

            # Sometimes a heavy single at the end, done tired.
            if lift != "bench" and rng.random() < 0.15:
                sets.append((day, lift, round_to_5(day_max * 0.9), 1, 9.0, "fatigued"))

            soreness += 1.5

        daily.append((
            day, round(sleep, 1), round(bodyweight, 1),
            int(np.clip(round(soreness), 1, 10)), int(np.clip(round(energy), 1, 10)),
            int(rng.normal(165, 20)), train_time,
        ))

    for week in range(WEEKS):
        for lift in ["squat", "bench", "deadlift"]:
            strength, phase = true_strength(lift, week)
            truth.append((START + pd.Timedelta(weeks=week), lift, round(strength, 1), phase))

    workouts = pd.DataFrame(sets, columns=["date", "exercise", "weight_lbs", "reps", "rpe", "set_type"])
    daily = pd.DataFrame(daily, columns=["date", "sleep_hours", "bodyweight_lbs", "soreness",
                                         "energy", "protein_g", "train_time"])
    truth = pd.DataFrame(truth, columns=["week", "exercise", "true_1rm", "phase"])
    return workouts, daily, truth


def save(workouts, daily, truth):
    SIM_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in [("workouts", workouts), ("daily", daily), ("truth", truth)]:
        out = df.copy()
        for col in ("date", "week"):
            if col in out:
                out[col] = out[col].dt.strftime("%Y-%m-%d")
        out.to_csv(SIM_DIR / f"{name}.csv", index=False)


if __name__ == "__main__":
    workouts, daily, truth = simulate()
    save(workouts, daily, truth)
    print(f"Wrote {len(workouts)} sets, {len(daily)} days and {len(truth)} truth rows to {SIM_DIR}")
