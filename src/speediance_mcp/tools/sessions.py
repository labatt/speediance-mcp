from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from ..speediance.api import SessionNotFound
from ..speediance.parsing import (
    derive_cardio_stats, downsample, find_uuid, heart_rate_values, intervals_from, is_cardio,
    rowing_telemetry, session_uuid,
)
from ..speediance.routes import FREE_ROUTE
from ._common import is_health_import, other_activity, parse_date, parse_month, session_exercises

ROWING_GAP_NOTE = ("No per-interval detail for this session: it has no rowing telemetry and isn't a "
                   "guided cardio session, so only the totals above are available.")
HR_POINTS = 600


def get_calendar(app, month: str) -> dict:
    """What's scheduled and trained in a month (`month` = 'YYYY-MM'). Each day carries its
    trainingPlanList. The raw calendar feed hides completed custom-template sessions, so completed
    sessions from the history feed are merged in with source:"history"; pass their trainingId to
    get_session_detail. Reservations (isReservation:true) are scheduled templates. Activity imported
    from the user's phone health app (walks, rides, other off-machine work that reached Speediance)
    is listed per day under otherActivities — count it in weekly load, but it isn't a gym session.
    Speediance only holds what the phone synced; other sources (e.g. a wellness app) may have more."""
    month, first, last = parse_month(month)
    days = [d for d in app.api.calendar(month) if isinstance(d, dict) and d.get("date")]
    by_date = {d.get("date"): d for d in days}
    for record in app.api.history(first, last):
        date = str(record.get("startTime", ""))[:10]
        if is_health_import(record):
            if date:
                day = by_date.setdefault(date, {"date": date, "trainingPlanList": []})
                day.setdefault("otherActivities", []).append(other_activity(record))
            continue
        day = by_date.setdefault(date, {"date": date, "trainingPlanList": []})
        plans = day.setdefault("trainingPlanList", [])
        if any(p.get("trainingId") == record.get("trainingId") for p in plans):
            continue
        plans.append({"trainingId": record.get("trainingId"), "type": record.get("type"),
                      "title": record.get("title"), "isFinish": 1, "source": "history",
                      "trainingTime": record.get("trainingTime"), "totalCapacity": record.get("totalCapacity"),
                      "calorie": record.get("calorie")})
    return {"month": month, "displayUnit": app.api.unit, "days": [by_date[k] for k in sorted(by_date) if k]}


def _heart_rate_present(exercises: list[dict]) -> bool:
    return any(entry.get("maxHeartRate") for ex in exercises for entry in ex["setLog"])


def get_session_detail(app, training_id: int, type: int = 0) -> dict:
    """The actual per-exercise log — sets, reps and weight per movement — for ONE completed session.
    `type` is optional and advisory: the session type is always resolved from this account's own
    history, so only sessions you own can be read. resolvedType:false means the id isn't in your
    history. Rowing/ski sessions add `cardio` (pace per 500m, speed, watts, calories/min); guided
    cardio adds per-interval rows, and rowing with recorded telemetry adds `rowing` — per-block
    stroke rate, pace, watts and how much of each block stayed inside its target stroke-rate band.
    Weights are already in displayUnit — never convert."""
    try:
        record = app.api.find_session(training_id)
    except SessionNotFound:
        return {"trainingId": int(training_id), "resolvedType": False, "displayUnit": app.api.unit,
                "exercises": [],
                "message": f"Session {training_id} isn't in this account's training history, so no detail was "
                           "fetched. Use a trainingId from get_calendar or get_athlete_snapshot."}
    route, payload = app.api.session_payload(record)
    parsed = session_exercises(app, route, payload, record["trainingId"])
    if route == FREE_ROUTE and isinstance(payload, dict):
        summary = payload
    else:
        summary = app.api.session_summary(route, record["trainingId"])
    out = {"trainingId": int(record["trainingId"]), "title": record.get("title"),
           "date": str(record.get("startTime", ""))[:10], "detailType": route, "resolvedType": True,
           "sessionType": record.get("type"), "displayUnit": app.api.unit,
           "exercises": parsed["exercises"], "warnings": parsed["warnings"],
           "heartRateAvailable": bool(find_uuid(route, payload)) and _heart_rate_present(parsed["exercises"])}
    if isinstance(payload, dict) and payload.get("showHeartGraph") and find_uuid(route, payload):
        out["heartRateAvailable"] = True
    if is_cardio(summary) or is_cardio(record):
        out["cardio"] = derive_cardio_stats({**record, **summary})
        guided = record.get("type") == 7
        if guided:
            out["intervals"] = intervals_from(app.api.free_intervals(record["trainingId"]))
        # Course and custom rowing carry per-point telemetry rather than interval rows. It is
        # keyed on the session uuid, and `existBoatingSkiDataGraph` says whether any was recorded.
        if not out.get("intervals") and _rowing_present(summary, payload, record):
            uuid = session_uuid(payload) or session_uuid(summary)
            if uuid:
                rowing = rowing_telemetry(app.api.rowing_graph(uuid))
                if rowing.get("available"):
                    out["rowing"] = rowing
        # A guided session already says what it has; the note is for the rest.
        if not guided and not out.get("intervals") and not out.get("rowing"):
            out["note"] = ROWING_GAP_NOTE
    return out


def _rowing_present(*sources) -> bool:
    """True when any session payload flags rowing/ski telemetry for this session."""
    return any(source.get("existBoatingSkiDataGraph") is True
               for source in sources if isinstance(source, dict))


def get_heart_rate(app, training_id: int) -> dict:
    """Second-by-second heart rate for a watch-paired session: average, max, min and a series
    (downsampled to at most 600 points). available:false when no watch recorded it."""
    record = app.api.find_session(training_id)
    route, payload = app.api.session_payload(record)
    uuid = find_uuid(route, payload)
    if not uuid:
        return {"trainingId": int(training_id), "available": False,
                "reason": "No heart-rate recording for this session (no paired watch)."}
    values = heart_rate_values(app.api.heart_rate(uuid))
    if not values:
        return {"trainingId": int(training_id), "available": False,
                "reason": "Speediance returned no heart-rate samples for this session."}
    return {"trainingId": int(training_id), "available": True, "samples": len(values),
            "avg": round(sum(values) / len(values), 1), "max": max(values), "min": min(values),
            "series": downsample(values, HR_POINTS)}


def get_training_stats(app, start: str, end: str) -> dict:
    """Totals between two dates (YYYY-MM-DD, inclusive): gym sessions, strength sessions, training
    minutes, calories, volume and energy — for questions like "how was my week?"."""
    start = parse_date(start, "start")
    end = parse_date(end, "end")
    if start > end:
        raise ToolError("start must be on or before end.")
    stats = app.api.range_stats(start, end)
    rows = [r for r in app.api.history(start, end) if not is_health_import(r)]
    return {"start": start, "end": end, "displayUnit": app.api.unit,
            "sessions": len(rows),
            "strengthSessions": sum(1 for r in rows if (r.get("totalCapacity") or 0) > 0),
            "trainingMinutes": round((stats.get("trainingTime") or 0) / 60),
            "calories": stats.get("calorie") or 0,
            "volume": stats.get("totalCapacity") or 0,
            "energyKJ": round((stats.get("totalEnergy") or 0) / 1000, 1),
            "note": "Minutes, calories, volume and energy are Speediance's range totals, which include activity "
                    "imported from a connected phone health app. The session counts are gym sessions only."}
