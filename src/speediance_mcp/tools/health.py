from __future__ import annotations

import datetime as dt

from ..speediance import health
from ..speediance.client import AuthExpired, SpeedianceError
from ._common import parse_date

MAX_TREND_DAYS = 14


def _feed(feeds: dict, name: str, fetch, empty):
    """Fetch one upstream feed and record whether it answered. A failed feed must read as
    'we are blind', never as 'no data' — an expired login still raises so the user is told."""
    try:
        data = fetch()
    except AuthExpired:
        raise
    except SpeedianceError:
        feeds[name] = "error"
        return empty
    feeds[name] = "ok" if data else "empty"
    return data or empty


def recovery_block(app, date: str | None = None, *, trend: bool = True) -> dict:
    api = app.api
    day = date or api.today().isoformat()
    feeds: dict = {}
    status = health.training_status(_feed(feeds, "trainingStatus", lambda: api.physical_condition(day), {}))
    standard = _feed(feeds, "sleepStandard", api.sleep_standard, {})
    night = health.sleep_night(_feed(feeds, "sleep", lambda: api.sleep(day), {}), standard)
    indicators = health.wellness(_feed(feeds, "wellnessFullDay", api.wellness_full_day, {}),
                                 _feed(feeds, "wellnessOvernight", api.wellness_overnight, {}))
    muscles = health.muscle_recovery(_feed(feeds, "muscleFatigue", api.muscle_fatigue, []),
                                     _feed(feeds, "partFatigue", api.part_fatigue, []))
    load = health.training_load_today(_feed(feeds, "homeIndicators", api.home_indicators, {}))
    if not trend:
        for entry in indicators:
            entry.get("overnight", {}).pop("trend", None)
    out = {
        "date": day,
        "trainingStatus": status,
        "sleep": night,
        "wellnessMonitor": indicators,
        "outOfBaseline": health.flags(indicators),
        "muscleRecovery": muscles,
        "fatiguedMuscles": health.fatigued(muscles),
        "trainingLoadTarget": load,
        "feeds": feeds,
    }
    failed = [name for name, state in feeds.items() if state == "error"]
    if failed:
        out["dataWarning"] = (f"Could not read {', '.join(failed)} from Speediance — treat those parts as "
                              "unknown, not as normal.")
    return out


def get_recovery(app, date: str = "") -> dict:
    """Readiness for one day (default today): how recovered the user is before training.

    - trainingStatus: Speediance's physical-condition model. fitness = long-window load, fatigue =
      short-window load, loadRatio = fatigue/fitness (an acute:chronic workload ratio) with the
      conventional band (<0.8 detraining, 0.8-1.3 optimal, 1.3-1.5 high, >1.5 very high).
      conditionScore is the app's readiness score; trainingStatusScore and sleepScore come with the
      user's own averages to compare against.
    - sleep: the night that ENDED on `date` — time asleep, score, and deep/core/REM/awake minutes with
      each stage's share against the app's healthy range.
    - wellnessMonitor: HRV, resting heart rate, blood oxygen, respiratory rate and skin temperature.
      fullDay carries the user's OWN baseline band and a below/normal/above status; overnight carries
      the 7-night trend. Name which window you quote and never mix them. Judge values against the
      user's own band, never population norms. outOfBaseline lists the full-day readings outside it.
    - muscleRecovery: per body part and muscle, fatigue 1 (fresh) to 3 (heavily loaded) from recent
      training. fatiguedMuscles lists the ones at 3 — avoid loading them hard today.
    - feeds: which upstream feeds answered. "error" means we could not see that data (say so);
      "empty" means Speediance holds none (usually no wearable synced).
    Wellness Monitor and muscle fatigue are always the latest reading, whatever `date` is."""
    day = parse_date(date) if date else None
    return recovery_block(app, day)


def get_readiness_trend(app, days: int = 7) -> dict:
    """Day-by-day trainingStatus and sleep for the last `days` days (max 14), oldest first — for
    questions like "how has my recovery been this week?" or spotting a run of poor sleep before a
    hard block. One request per day per feed, so keep the window short."""
    days = max(1, min(int(days), MAX_TREND_DAYS))
    api = app.api
    today = api.today()
    rows = []
    for offset in range(days - 1, -1, -1):
        day = (today - dt.timedelta(days=offset)).isoformat()
        feeds: dict = {}
        status = health.training_status(_feed(feeds, "trainingStatus", lambda: api.physical_condition(day), {}))
        night = health.sleep_night(_feed(feeds, "sleep", lambda: api.sleep(day), {}))
        rows.append({
            "date": day,
            "conditionScore": status and status["conditionScore"],
            "fitness": status and status["fitness"],
            "fatigue": status and status["fatigue"],
            "loadRatio": status and status["loadRatio"],
            "loadBand": status and status["loadBand"],
            "sleepAsleep": night and night["asleep"],
            "sleepScore": night and night["score"],
            "deepMinutes": night and night["stages"].get("deep", {}).get("minutes"),
            **({"feedErrors": [k for k, v in feeds.items() if v == "error"]}
               if "error" in feeds.values() else {}),
        })
    return {"days": days, "trend": rows,
            "note": "sleep on a date is the night that ended that morning. null = no data for that day."}


def get_body_metrics(app) -> dict:
    """Latest body composition (weight, body fat %, BMI, lean mass, FFMI, VO2max where the phone
    synced them) and the app's scores: cardioFitness (CRF), strengthScore, and bodyAge against the
    user's actual age. A missing score means it hasn't been calculated — not that it is low."""
    feeds: dict = {}
    body = health.body_metrics(_feed(feeds, "bodyMetrics", app.api.body_metrics, {}))
    scores = health.scores(_feed(feeds, "scores", app.api.health_scores, {}))
    return {"displayUnit": app.api.unit, "body": body, "scores": scores, "feeds": feeds}
