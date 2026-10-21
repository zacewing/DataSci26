"""Predict next-session e1RM, and check whether the models beat a simple guess.

Three predictors are compared:
  baseline : "same as last session" (the naive guess any model must beat)
  linear   : linear regression
  forest   : random forest

Why predict the *change* from last session instead of the e1RM itself: a random
forest can't predict values outside what it saw in training, so it could never
forecast a new PR. Predicting "+3 lbs vs last time" sidesteps that, and the
linear model is fit the same way so the comparison is fair.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error

from src.analysis import best_e1rm_per_day

FEATURES = [
    "prev_e1rm",        # last session's e1RM for this lift
    "prev3_mean",       # average of the last 3 sessions (recent form)
    "days_since_last",  # rest since this lift was last trained
    "volume_last_7d",   # this lift's training load in the 7 days before
    "sleep_hours",      # recovery on the day
    "soreness",
    "energy",
    "is_morning",       # 1 if trained in the morning
]
MIN_SESSIONS = 30   # below this, results would be mostly noise
TEST_SHARE = 0.25   # the most recent 25% of sessions are held out for testing


def build_dataset(workouts, daily):
    """One row per (lift, session) with features known *before* the session."""
    best = best_e1rm_per_day(workouts).sort_values(["exercise", "date"])
    volume = workouts.groupby(["exercise", "date"], as_index=False)["volume"].sum()
    rows = []
    for lift, s in best.groupby("exercise"):
        s = s.reset_index(drop=True)
        v = volume[volume["exercise"] == lift]
        for i in range(1, len(s)):
            day = s.loc[i, "date"]
            recent_volume = v[(v["date"] < day) & (v["date"] >= day - pd.Timedelta(days=7))]
            rows.append({
                "exercise": lift,
                "date": day,
                "e1rm": s.loc[i, "e1rm"],
                "prev_e1rm": s.loc[i - 1, "e1rm"],
                # Uses only sessions before this one, so nothing leaks from the answer.
                "prev3_mean": s.loc[max(0, i - 3):i - 1, "e1rm"].mean(),
                "days_since_last": (day - s.loc[i - 1, "date"]).days,
                "volume_last_7d": recent_volume["volume"].sum(),
            })
    data = pd.DataFrame(rows)
    if data.empty:
        return data

    # Same-day recovery. Morning check-ins (sleep, soreness, energy) are known
    # before training, so using them doesn't peek at the answer.
    rec = daily[["date", "sleep_hours", "soreness", "energy", "train_time"]]
    data = data.merge(rec, on="date", how="left")
    data["is_morning"] = (data["train_time"] == "morning").astype(int)
    data = data.drop(columns="train_time")

    # One column per lift (0/1), so the models can learn lift-specific patterns.
    for lift in sorted(data["exercise"].unique()):
        data[f"lift_{lift}"] = (data["exercise"] == lift).astype(int)
    return data.sort_values("date").reset_index(drop=True)


def feature_columns(data):
    return FEATURES + [c for c in data.columns if c.startswith("lift_")]


def fill_missing(X, fill_values):
    """Days without a check-in get the training average, not a crash."""
    return X.fillna(fill_values).fillna(0)


def make_models():
    return {
        "linear": LinearRegression(),
        # Shallow trees + many of them: less likely to memorize a small dataset.
        "forest": RandomForestRegressor(n_estimators=300, max_depth=4, min_samples_leaf=5, random_state=0),
    }


def evaluate(workouts, daily):
    """Train on older sessions, test on the newest ones.

    Why a time-based split instead of a random one: in real use the model
    always predicts the future from the past. A random split would let it
    "see" sessions after the ones it's tested on, making scores look better
    than they really are.

    Returns None if there isn't enough data yet.
    """
    data = build_dataset(workouts, daily)
    if len(data) < MIN_SESSIONS:
        return None

    cols = feature_columns(data)
    split = int(len(data) * (1 - TEST_SHARE))
    train, test = data.iloc[:split], data.iloc[split:].copy()
    fill_values = train[cols].mean()
    X_train, X_test = fill_missing(train[cols], fill_values), fill_missing(test[cols], fill_values)
    y_train = train["e1rm"] - train["prev_e1rm"]  # predict the change

    test["baseline"] = test["prev_e1rm"]
    importances = None
    for name, model in make_models().items():
        model.fit(X_train, y_train)
        test[name] = test["prev_e1rm"] + model.predict(X_test)
        if name == "forest":
            importances = pd.Series(model.feature_importances_, index=cols).sort_values(ascending=False)

    scores = []
    for name in ["baseline", "linear", "forest"]:
        scores.append({
            "model": name,
            "MAE": mean_absolute_error(test["e1rm"], test[name]),
            "RMSE": np.sqrt(mean_squared_error(test["e1rm"], test[name])),
        })
    return {
        "scores": pd.DataFrame(scores),
        "predictions": test[["exercise", "date", "e1rm", "baseline", "linear", "forest"]],
        "importances": importances,
        "n_train": len(train),
        "n_test": len(test),
    }


def forecast_next(workouts, daily, lift, sleep_hours, soreness, energy, morning=False, model_name="forest"):
    """Predict the next session's e1RM for one lift, for a given recovery.

    Trains on all sessions (the evaluation above is what tells us how much
    to trust it). Returns None if there isn't enough data.
    """
    data = build_dataset(workouts, daily)
    if len(data) < MIN_SESSIONS or lift not in set(data["exercise"]):
        return None
    cols = feature_columns(data)
    fill_values = data[cols].mean()
    model = make_models()[model_name]
    model.fit(fill_missing(data[cols], fill_values), data["e1rm"] - data["prev_e1rm"])

    # Features for a session that hasn't happened yet: the latest history,
    # a typical rest gap, and the recovery numbers passed in.
    best = best_e1rm_per_day(workouts)
    s = best[best["exercise"] == lift].sort_values("date")
    gaps = s["date"].diff().dt.days.dropna()
    last_day = s["date"].iloc[-1]
    v = workouts[(workouts["exercise"] == lift) & (workouts["date"] > last_day - pd.Timedelta(days=7))]
    row = {
        "prev_e1rm": s["e1rm"].iloc[-1],
        "prev3_mean": s["e1rm"].tail(3).mean(),
        "days_since_last": gaps.median() if len(gaps) else 3,
        "volume_last_7d": v["volume"].sum(),
        "sleep_hours": sleep_hours,
        "soreness": soreness,
        "energy": energy,
        "is_morning": int(morning),
    }
    for c in cols:
        if c.startswith("lift_"):
            row[c] = int(c == f"lift_{lift}")
    X = pd.DataFrame([row])[cols]
    return float(row["prev_e1rm"] + model.predict(X)[0])
