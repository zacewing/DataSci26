"""Weekly summary: the week's numbers, explained in plain English.

Two ways to write it:
  ai_summary()       sends the numbers to Claude (needs an Anthropic API key)
  offline_summary()  simple rules, no API key or internet needed

Either way, the summary is a suggestion, not medical advice.
"""
import pandas as pd

from src.analysis import best_e1rm_per_day, weekly_volume
from src.plateau import current_status, weekly_trend

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You are a strength coach writing a short weekly check-in for a \
lifter who tracks their training in a class data-science project. You'll get the \
week's numbers as JSON.

Write in plain, friendly English for the lifter, in second person. Use three short \
sections with these headings: **This week**, **What stands out**, **Next week**. \
Keep it under 200 words in total.

Ground every claim in the numbers you were given; don't invent lifts, sessions or \
figures. Plateau status comes from a trend detector and can lag reality by a few \
weeks, so treat it as a signal, not a verdict. Suggestions can include progressing \
the load, holding steady, changing rep ranges or taking a deload, framed as options \
rather than prescriptions. This is not medical advice: if pain or injury is \
mentioned, suggest seeing a professional instead of programming around it."""


def week_bounds(week_end):
    end = pd.Timestamp(week_end).normalize()
    return end - pd.Timedelta(days=6), end


def weekly_stats(workouts, daily, week_end):
    """Collect the numbers for one week (Monday to Sunday ending on week_end)."""
    start, end = week_bounds(week_end)
    prev_start, prev_end = start - pd.Timedelta(days=7), start - pd.Timedelta(days=1)

    # Only use data up to the end of this week, so a past week's summary
    # doesn't "know" what happened later.
    w = workouts[workouts["date"] <= end]
    best = best_e1rm_per_day(w)
    volume = weekly_volume(w)
    statuses = current_status(weekly_trend(best), sorted(best["exercise"].unique()))

    lifts = {}
    for lift in sorted(best["exercise"].unique()):
        b = best[best["exercise"] == lift]
        this_week = b[b["date"].between(start, end)]
        last_week = b[b["date"].between(prev_start, prev_end)]
        vol = volume[volume["exercise"] == lift].set_index("week")["total_volume"]
        if this_week.empty and last_week.empty:
            continue
        lifts[lift] = {
            "sessions_this_week": len(this_week),
            "best_e1rm_this_week": round(this_week["e1rm"].max(), 1) if len(this_week) else None,
            "best_e1rm_last_week": round(last_week["e1rm"].max(), 1) if len(last_week) else None,
            "all_time_best_e1rm": round(b["e1rm"].max(), 1),
            "volume_this_week": int(vol.get(start, 0)),
            "volume_last_week": int(vol.get(prev_start, 0)),
            "plateau_status": statuses[lift]["status"],
            "trend": statuses[lift]["text"],
        }

    d = daily[daily["date"].between(start, end)]

    def avg(col):
        return round(float(d[col].mean()), 1) if col in d and d[col].notna().any() else None

    return {
        "week": f"{start:%b %d} – {end:%b %d, %Y}",
        "training_days": int(w[w["date"].between(start, end)]["date"].nunique()),
        "fatigued_sets": int((w[w["date"].between(start, end)]["set_type"] == "fatigued").sum()),
        "lifts": lifts,
        "recovery": {
            "days_logged": len(d),
            "avg_sleep_hours": avg("sleep_hours"),
            "avg_soreness": avg("soreness"),
            "avg_energy": avg("energy"),
            "avg_protein_g": avg("protein_g"),
            "bodyweight_start": float(d["bodyweight_lbs"].dropna().iloc[0]) if "bodyweight_lbs" in d and d["bodyweight_lbs"].notna().any() else None,
            "bodyweight_end": float(d["bodyweight_lbs"].dropna().iloc[-1]) if "bodyweight_lbs" in d and d["bodyweight_lbs"].notna().any() else None,
        },
    }


def has_api_credentials():
    """True if an API key (or other Anthropic login) is available.

    The key is read from the ANTHROPIC_API_KEY environment variable by the SDK;
    it's never written in the code.
    """
    # Imported here, not at the top, so the rest of this file also runs in a web
    # browser (website/upload.js), where the Anthropic library isn't available.
    import anthropic

    client = anthropic.Anthropic()
    return bool(client.api_key or client.auth_token or client.credentials)


def ai_summary(stats):
    """Ask Claude to write the summary. Raises SummaryError if it can't."""
    import json

    import anthropic

    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            # A short summary of given numbers doesn't need deep reasoning,
            # so low effort keeps it fast and cheap.
            output_config={"effort": "low"},
            # If Claude declines, the API retries on a fallback model automatically.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(stats, indent=2)}],
        )
    except anthropic.AuthenticationError:
        raise SummaryError("The API key was rejected. Check ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        raise SummaryError("Rate limited by the API. Try again in a minute.")
    except anthropic.APIStatusError as e:
        raise SummaryError(f"API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise SummaryError("Couldn't reach the Anthropic API. Check your internet connection.")
    except TypeError as e:
        # The SDK raises this when no API key or login is set up at all.
        if "authentication" in str(e):
            raise SummaryError("No API key set. Set ANTHROPIC_API_KEY to use the AI summary.")
        raise

    if response.stop_reason == "refusal":
        raise SummaryError("The model declined to write this summary.")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise SummaryError("The model returned an empty summary.")
    return text


class SummaryError(Exception):
    pass


def join_names(lifts):
    """['bench', 'squat', 'deadlift'] -> 'Bench, Squat and Deadlift'."""
    names = [l.title() for l in lifts]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def offline_summary(stats):
    """Rule-based summary for when there's no API key. Same three sections."""
    lines = ["**This week**"]
    if not stats["lifts"]:
        return "**This week**\nNo training logged this week."
    lines.append(f"{stats['training_days']} training day(s), {stats['week']}.")
    for lift, s in stats["lifts"].items():
        now, before = s["best_e1rm_this_week"], s["best_e1rm_last_week"]
        if now and before:
            change = f"{now - before:+.1f} lbs vs last week"
        elif now:
            change = "no session last week to compare"
        else:
            change = "not trained this week"
        best = f"{now:.1f}" if now else "–"
        lines.append(f"- **{lift.title()}**: best e1RM {best} lbs ({change}). {s['trend']}")

    lines.append("\n**What stands out**")
    notes = []
    stalled = [l for l, s in stats["lifts"].items() if s["plateau_status"] in ("plateau", "stalling")]
    if stalled:
        notes.append(f"{join_names(stalled)} {'has' if len(stalled) == 1 else 'have'} flattened out.")
    prs = [l for l, s in stats["lifts"].items()
           if s["best_e1rm_this_week"] and s["best_e1rm_this_week"] >= s["all_time_best_e1rm"]]
    if prs:
        notes.append(f"New best e1RM on {join_names(prs)}.")
    sleep = stats["recovery"]["avg_sleep_hours"]
    if sleep is not None:
        notes.append(f"Average sleep was {sleep} hours" + (", on the low side." if sleep < 7 else "."))
    lines.append(" ".join(notes) or "A steady week with nothing unusual.")

    lines.append("\n**Next week**")
    if all(s["plateau_status"] == "not enough data" for s in stats["lifts"].values()):
        lines.append("Keep logging. Trends and plateau checks start after about 6 sessions per lift.")
    elif any(s["plateau_status"] == "plateau" for s in stats["lifts"].values()):
        lines.append("For a stalled lift, consider a lighter deload week or a change in rep range before pushing the weight again.")
    elif sleep is not None and sleep < 7:
        lines.append("Prioritizing sleep is likely the easiest win right now.")
    else:
        lines.append("Keep progressing the load gradually while the trend is still moving up.")
    lines.append("\n_Rule-based summary (no API key set). Suggestions, not medical advice._")
    return "\n".join(lines)
