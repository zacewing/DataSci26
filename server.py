"""Local web server for the project website, including the Log tab.

Run from the project folder with:  .venv/bin/python server.py
Then open http://localhost:8000

Why a server: a web page can't write files on your computer by itself (browsers
block that for security). This small program serves the website AND receives
form entries from the Log tab, saving them to data/workouts.csv and
data/daily.csv. It also sends the Dashboard tab its numbers, which are
calculated by the code in src/ (so run it with the project's .venv Python).
"""
import csv
import json
import threading
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src import dashboard_data
from src.summary import SummaryError, ai_summary, has_api_credentials, weekly_stats

PROJECT_ROOT = Path(__file__).resolve().parent
WEBSITE_DIR = PROJECT_ROOT / "website"
DATA_DIR = PROJECT_ROOT / "data"
WORKOUTS_CSV = DATA_DIR / "workouts.csv"
DAILY_CSV = DATA_DIR / "daily.csv"

WORKOUT_COLUMNS = ["date", "exercise", "weight_lbs", "reps", "rpe", "set_type"]
DAILY_COLUMNS = ["date", "sleep_hours", "bodyweight_lbs", "soreness", "energy", "protein_g", "train_time"]
TRAIN_TIMES = {"", "morning", "afternoon", "evening"}

# 127.0.0.1 means only this Mac can connect, so the data stays private.
HOST, PORT = "127.0.0.1", 8000

# Only one save at a time, so two quick submits can't overwrite each other.
SAVE_LOCK = threading.Lock()


# ---------- Reading and writing the CSV logs ----------

def read_rows(path, columns):
    """Return every row of a log as a list of dicts (empty if no file yet)."""
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return [{c: row.get(c) or "" for c in columns} for row in csv.DictReader(f)]


def write_rows(path, columns, rows):
    """Rewrite a log from scratch. Used by undo and by daily-log updates."""
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def append_row(path, columns, row):
    """Add one row to the end of a log, creating the file if needed."""
    rows = read_rows(path, columns)
    rows.append(row)
    write_rows(path, columns, rows)


# ---------- Checking form input ----------
# The browser form already checks most of this, but the server checks again
# so a bad entry can never end up in the data, whatever sends it.

class BadInput(ValueError):
    pass


def as_date(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise BadInput("Date must look like 2026-10-14.")


def as_number(value, name, low, high, required=True):
    """Return the value as clean text ('225', '8.5'), or '' if optional and blank."""
    if value in (None, ""):
        if required:
            raise BadInput(f"{name} is required.")
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BadInput(f"{name} must be a number.")
    if not low <= number <= high:
        raise BadInput(f"{name} must be between {low} and {high}.")
    return str(int(number)) if number.is_integer() else str(number)


def clean_workout(data):
    exercise = str(data.get("exercise", "")).strip().lower()
    if not exercise:
        raise BadInput("Exercise is required.")
    reps = as_number(data.get("reps"), "Reps", 1, 100)
    if "." in reps:
        raise BadInput("Reps must be a whole number.")
    return {
        "date": as_date(data.get("date")),
        "exercise": exercise,
        "weight_lbs": as_number(data.get("weight_lbs"), "Weight", 1, 2000),
        "reps": reps,
        "rpe": as_number(data.get("rpe"), "RPE", 1, 10, required=False),
        "set_type": "fatigued" if data.get("fatigued") else "",
    }


def clean_daily(data):
    train_time = str(data.get("train_time", "")).strip().lower()
    if train_time not in TRAIN_TIMES:
        raise BadInput("Training time must be morning, afternoon, evening, or blank.")
    row = {
        "date": as_date(data.get("date")),
        "sleep_hours": as_number(data.get("sleep_hours"), "Sleep", 0, 24, required=False),
        # Recommended but optional: not everyone has a scale every day.
        "bodyweight_lbs": as_number(data.get("bodyweight_lbs"), "Bodyweight", 50, 600, required=False),
        "soreness": as_number(data.get("soreness"), "Soreness", 1, 10, required=False),
        "energy": as_number(data.get("energy"), "Energy", 1, 10, required=False),
        "protein_g": as_number(data.get("protein_g"), "Protein", 0, 1000, required=False),
        "train_time": train_time,
    }
    # A check-in with only a date adds a blank row and would hide the
    # "no check-in today" reminder, so at least one value is required.
    if not any(value != "" for key, value in row.items() if key != "date"):
        raise BadInput("Fill in at least one value (like bodyweight or sleep).")
    return row


# ---------- Web server ----------

class Handler(SimpleHTTPRequestHandler):
    """Serves files from website/, plus a few /api/ addresses for the Log tab."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEBSITE_DIR), **kwargs)

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            raise BadInput("Couldn't read the form data.")

    def do_GET(self):
        url = urlparse(self.path)
        # Everything the Dashboard tab draws, calculated fresh from the logs.
        if url.path == "/api/dashboard":
            source = parse_qs(url.query).get("source", ["real"])[0]
            if source not in dashboard_data.SOURCES:
                self.send_json({"error": "Unknown data source."}, status=400)
                return
            data = dashboard_data.build(source)
            data["ai_available"] = has_api_credentials()
            self.send_json(data)
        # Recent entries, so the Log tab can show what was just saved.
        elif self.path == "/api/recent":
            workouts = read_rows(WORKOUTS_CSV, WORKOUT_COLUMNS)
            daily = read_rows(DAILY_CSV, DAILY_COLUMNS)
            exercises = sorted({r["exercise"].strip().lower() for r in workouts if r["exercise"]})
            self.send_json({
                "workouts": workouts[-15:][::-1],  # newest first
                "daily": sorted(daily, key=lambda r: r["date"], reverse=True)[:7],
                "exercises": exercises,
            })
        # The analysis code, for the Dashboard's "upload your own log" feature, which
        # runs it in the browser. Online, the GitHub Pages workflow copies src/ to py/src/.
        elif url.path.startswith("/py/src/") and url.path.endswith(".py"):
            path = PROJECT_ROOT / "src" / url.path.rsplit("/", 1)[1]
            if path.is_file():
                body = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/x-python; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)
        else:
            super().do_GET()  # a normal website file

    def do_POST(self):
        if self.path == "/api/summary":
            self.handle_summary()  # doesn't save anything, so no lock needed
            return
        with SAVE_LOCK:
            self.handle_save()

    def handle_summary(self):
        """AI weekly summary, made only when asked (it's a paid API call)."""
        try:
            request = self.read_json()
            source = request.get("source", "real")
            if source not in dashboard_data.SOURCES:
                raise BadInput("Unknown data source.")
            workouts, daily = dashboard_data.load_source(source)
            stats = weekly_stats(workouts, daily, as_date(request.get("week_end")))
            self.send_json({"summary": ai_summary(stats)})
        except BadInput as e:
            self.send_json({"error": str(e)}, status=400)
        except SummaryError as e:
            self.send_json({"error": str(e)}, status=502)

    def handle_save(self):
        try:
            if self.path == "/api/workouts":
                row = clean_workout(self.read_json())
                append_row(WORKOUTS_CSV, WORKOUT_COLUMNS, row)
                self.send_json({"saved": row})

            elif self.path == "/api/daily":
                row = clean_daily(self.read_json())
                # One row per day. Logging a date again only updates the fields
                # filled in, so sleep in the morning + protein at night both stick.
                rows = read_rows(DAILY_CSV, DAILY_COLUMNS)
                old = next((r for r in rows if r["date"] == row["date"]), {})
                row = {c: row[c] or old.get(c, "") for c in DAILY_COLUMNS}
                rows = [r for r in rows if r["date"] != row["date"]] + [row]
                rows.sort(key=lambda r: r["date"])
                write_rows(DAILY_CSV, DAILY_COLUMNS, rows)
                self.send_json({"saved": row})

            elif self.path == "/api/workouts/undo":
                rows = read_rows(WORKOUTS_CSV, WORKOUT_COLUMNS)
                if not rows:
                    raise BadInput("There's nothing to undo.")
                removed = rows.pop()
                write_rows(WORKOUTS_CSV, WORKOUT_COLUMNS, rows)
                self.send_json({"removed": removed})

            else:
                self.send_json({"error": "Unknown address."}, status=404)
        except BadInput as e:
            self.send_json({"error": str(e)}, status=400)


if __name__ == "__main__":
    print(f"Website running at http://localhost:{PORT}  (press Ctrl+C to stop)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
