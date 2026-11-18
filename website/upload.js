// "Try it with your own log": runs the project's real Python analysis (src/) in the
// visitor's browser with Pyodide, so it works on GitHub Pages with no server, and the
// file never leaves their computer. Uses the same build_from() as the live Dashboard,
// so there's one version of the analysis, not a JavaScript copy.

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
// The analysis modules the Dashboard needs. Online, the GitHub Pages workflow copies
// src/ to py/src/; locally, server.py serves them at the same address.
const SRC_FILES = ["__init__.py", "analysis.py", "plateau.py", "model.py", "summary.py", "drivers.py", "dashboard_data.py"];
const KG_TO_LBS = 2.20462;

let python = null;  // loading Python takes a while, so it's done once and reused

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error("Couldn't load Python (check your internet connection)."));
    document.head.append(s);
  });
}

function startPython(status) {
  python ??= (async () => {
    status("Starting Python in your browser…");
    await loadScript(PYODIDE_URL + "pyodide.js");
    const py = await loadPyodide({ indexURL: PYODIDE_URL });
    status("Loading pandas and scikit-learn (about 20 MB, only the first time)…");
    await py.loadPackage(["numpy", "pandas", "scikit-learn"]);
    status("Loading the analysis code…");
    py.FS.mkdirTree("/home/pyodide/src");
    for (const f of SRC_FILES) {
      const res = await fetch("py/src/" + f);
      if (!res.ok) throw new Error(`Couldn't load the analysis code (${f}).`);
      py.FS.writeFile("/home/pyodide/src/" + f, await res.text());
    }
    return py;
  })();
  // If loading failed, forget it so the next click can try again.
  python.catch(() => { python = null; });
  return python;
}

// The Python side: read the CSVs, check the columns, and run the Dashboard's analysis.
const ANALYZE = `
import json
import pandas as pd
from src.analysis import load_workouts, load_daily
from src.dashboard_data import build_from

def analyze(unit, label, has_daily):
    raw = pd.read_csv("/tmp/workouts.csv")
    raw.columns = [str(c).strip().lower() for c in raw.columns]
    if "weight" in raw.columns and "weight_lbs" not in raw.columns:
        raw = raw.rename(columns={"weight": "weight_lbs"})
    missing = [c for c in ["date", "exercise", "weight_lbs", "reps"] if c not in raw.columns]
    if missing:
        return json.dumps({"error": "The workouts file is missing these columns: " + ", ".join(missing)
                                    + ". Download the sample to see the format."})
    for col in ["rpe", "set_type"]:
        if col not in raw.columns:
            raw[col] = None
    if unit == "kg":
        raw["weight_lbs"] = pd.to_numeric(raw["weight_lbs"], errors="coerce") * ${KG_TO_LBS}
    raw.to_csv("/tmp/workouts_clean.csv", index=False)
    workouts = load_workouts("/tmp/workouts_clean.csv")
    if workouts.empty:
        return json.dumps({"error": "No usable sets found. Each row needs a date, exercise, weight and reps."})

    cols = ["date", "sleep_hours", "bodyweight_lbs", "soreness", "energy", "protein_g", "train_time"]
    if has_daily:
        d = pd.read_csv("/tmp/daily.csv")
        d.columns = [str(c).strip().lower() for c in d.columns]
        if "date" not in d.columns:
            return json.dumps({"error": "The check-in file needs a 'date' column."})
        for col in cols:
            if col not in d.columns:
                d[col] = None
        d[cols].to_csv("/tmp/daily_clean.csv", index=False)
    else:
        pd.DataFrame(columns=cols).to_csv("/tmp/daily_clean.csv", index=False)
    daily = load_daily("/tmp/daily_clean.csv")
    return json.dumps(build_from(workouts, daily, "upload", label))
`;

const SAMPLE_WORKOUTS = `date,exercise,weight_lbs,reps,rpe,set_type
2026-09-01,squat,315,5,8,
2026-09-01,squat,285,5,7,
2026-09-01,bench,225,5,8,
2026-09-03,deadlift,405,3,8,
2026-09-03,deadlift,445,1,9,fatigued
`;
const SAMPLE_DAILY = `date,sleep_hours,bodyweight_lbs,soreness,energy,protein_g,train_time
2026-09-01,7.5,182,4,7,170,evening
2026-09-03,6.5,181.6,5,6,160,morning
`;

function download(name, text) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function setUpUpload() {
  const form = document.getElementById("upload-form");
  const status = document.getElementById("upload-status");
  const say = (text, kind = "") => { status.textContent = text; status.className = "status " + kind; };

  document.getElementById("sample-workouts").addEventListener("click", () => download("workouts_sample.csv", SAMPLE_WORKOUTS));
  document.getElementById("sample-daily").addEventListener("click", () => download("daily_sample.csv", SAMPLE_DAILY));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const workoutsFile = form.workouts.files[0];
    const dailyFile = form.daily.files[0];
    if (!workoutsFile) { say("Choose your workouts CSV first.", "error"); return; }
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const py = await startPython((t) => say(t));
      say("Analyzing your log…");
      py.FS.writeFile("/tmp/workouts.csv", await workoutsFile.text());
      if (dailyFile) py.FS.writeFile("/tmp/daily.csv", await dailyFile.text());
      py.runPython(ANALYZE);
      const json = py.globals.get("analyze")(form.unit.value, workoutsFile.name, Boolean(dailyFile));
      const data = JSON.parse(json);
      if (data.error) { say(data.error, "error"); return; }
      say(`Done: ${data.lifts.length} lift(s) analyzed.`, "ok");
      showUploaded(data, workoutsFile.name);  // in dashboard.js
    } catch (err) {
      console.error(err);
      // Python errors are long; show the last line, which says what went wrong.
      const msg = String(err.message || err).trim().split("\n").pop();
      say(`Couldn't analyze the file: ${msg}`, "error");
    } finally {
      button.disabled = false;
    }
  });
}

setUpUpload();
