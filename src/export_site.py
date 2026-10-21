"""Save the simulated demo into the website, so the Dashboard tab works on
GitHub Pages (which can only host files, not run Python).

Run from the project folder with:  python -m src.export_site
Rerun it whenever the analysis code or the simulated data changes.

Only the simulated data is exported. Real logs stay on this computer.
"""
import json

from src.analysis import PROJECT_ROOT
from src.dashboard_data import build

OUT = PROJECT_ROOT / "website" / "data" / "dashboard-sim.json"

if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    data = build("sim")
    data["ai_available"] = False  # no server online, so no AI summaries
    OUT.write_text(json.dumps(data))
    print(f"Wrote {OUT.relative_to(PROJECT_ROOT)} ({OUT.stat().st_size / 1024:.0f} KB)")
