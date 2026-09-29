from __future__ import annotations

import datetime as dt

from mcp.server.mcpserver.exceptions import ToolError

from ..speediance.muscles import attribute, muscle_index, ratios, untrained
from ..speediance.parsing import epley, round_half
from ._common import is_health_import, other_activity, record_summary, resolve_group, session_exercises

MAX_SESSIONS_SCANNED = 10


def _age(birthday, today: dt.date) -> int | None:
    try:
        born = dt.date.fromisoformat(str(birthday)[:10])
    except ValueError:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _window(app, days: int) -> tuple[list[dict], list[dict]]:
    """(gym sessions, phone-health imports) in the last `days` days, newest first."""
    today = app.api.today()
    start = (today - dt.timedelta(days=days)).isoformat()
    rows = sorted(app.api.history(start, today.isoformat()), key=lambda r: str(r.get("startTime", "")), reverse=True)
    return [r for r in rows if not is_health_import(r)], [r for r in rows if is_health_import(r)]


def _recent(app, days: int) -> list[dict]:
    return _window(app, days)[0]


def _exercises(app, record: dict) -> list[dict]:
    route, payload = app.api.session_payload(record)
    return session_exercises(app, route, payload, record["trainingId"])["exercises"]


def _best_set(exercise: dict) -> dict | None:
    sets = [s for s in exercise["setLog"] if s.get("weight") and s.get("reps")]
    return max(sets, key=lambda s: epley(s["weight"], s["reps"])) if sets else None


def get_athlete_snapshot(app, days: int = 14) -> dict:
    """Everything needed before planning: profile (bodyweight, age, watch), the coaching memory
    (active facts grouped as constraints{hard, soft} / preferences / goals / observations / conflicts,
    preferences, ★preferred / ⊘avoided exercises, owned equipment) and recent sessions, plus
    otherActivities imported from the phone health app (off-machine work — include it in load).
    Check memory.facts.constraints.hard before building any workout and respect it; raise any
    memory.facts.conflicts with the user rather than picking one. memory.facts.legacyUnreviewed holds old
    free-form facts not yet curated: they may still bind — treat injury ones as hard constraints until
    curated, and curate them promptly with the user."""
    days = max(1, min(int(days), 365))
    sessions, others = _window(app, days)
    profile = app.api.profile()
    memory = app.memory
    marks = memory.marks()
    return {
        "displayUnit": app.api.unit,
        "windowDays": days,
        "profile": {
            "sex": profile.get("sex"),
            "bodyweight": profile.get("weight"),  # as Speediance stores it; userinfo.weightUnit is unreliable
            "height": profile.get("height"),
            "age": _age(profile.get("birthday"), app.api.today()),
            "watchPaired": bool(profile.get("isWatch")),
            "trainingDays": profile.get("trainingDays"),
            "lifetime": {"minutes": round((profile.get("totalTrainingTime") or 0) / 60),
                         "volume": profile.get("totalCapacity"), "calories": profile.get("totalCalorie")},
        },
        "memory": {
            "facts": memory.fact_digest(),
            "preferences": memory.preferences(),
            "preferredExercises": [{"groupId": g, "name": m["name"]} for g, m in marks.items() if m["mark"] == "preferred"],
            "avoidedExercises": [{"groupId": g, "name": m["name"], "reason": m["reason"]}
                                 for g, m in marks.items() if m["mark"] == "avoided"],
        },
        "history": [record_summary(r) for r in sessions],
        "otherActivities": [{"date": str(r.get("startTime", ""))[:10], **other_activity(r)} for r in others],
    }


def get_strength_profile(app, limit: int = 15, days: int = 90) -> dict:
    """Per movement trained recently: estimated 1RM (Epley) from its best set, that set, and how the
    top weight moved across the window. Scans up to the 10 most recent weighted sessions."""
    rows = [r for r in _recent(app, max(1, int(days))) if (r.get("totalCapacity") or 0) > 0][:MAX_SESSIONS_SCANNED]
    movements: dict = {}
    for record in rows:  # newest first
        date = str(record.get("startTime", ""))[:10]
        for exercise in _exercises(app, record):
            best = _best_set(exercise) if exercise["kind"] == "reps" else None
            if not best:
                continue
            estimate = epley(best["weight"], best["reps"])
            key = exercise["groupId"] or exercise["name"]
            entry = movements.setdefault(key, {"groupId": exercise["groupId"], "name": exercise["name"],
                                               "estimated1RM": 0.0, "bestSet": None, "sessions": 0,
                                               "_latest": None, "_oldest": None})
            entry["sessions"] += 1
            if entry["_latest"] is None:
                entry["_latest"] = exercise["topWeight"]
            entry["_oldest"] = exercise["topWeight"]
            if estimate > entry["estimated1RM"]:
                entry["estimated1RM"] = round(estimate, 1)
                entry["bestSet"] = {"date": date, "weight": best["weight"], "reps": best["reps"]}
    ranked = sorted(movements.values(), key=lambda m: m["estimated1RM"], reverse=True)[:max(1, int(limit))]
    for entry in ranked:
        latest, oldest = entry.pop("_latest"), entry.pop("_oldest")
        entry["topWeightChange"] = (round(latest - oldest, 1)
                                    if entry["sessions"] > 1 and latest is not None and oldest is not None else None)
    out = {"displayUnit": app.api.unit, "windowDays": int(days), "sessionsScanned": len(rows), "movements": ranked,
           "method": "Estimated 1RM uses the Epley formula, weight x (1 + reps/30), on each movement's best set."}
    if not ranked:
        out["note"] = "No weighted strength sessions in this window."
    return out


def _movements(app, record: dict) -> dict:
    out = {}
    for exercise in _exercises(app, record):
        out[exercise["groupId"] or exercise["name"]] = {
            "name": exercise["name"], "topWeight": exercise["topWeight"],
            "reps": sum(r or 0 for r in exercise["reps"]), "volume": exercise["volume"],
            "sets": exercise["sets"], "skippedSets": exercise["skippedSets"]}
    return out


def _delta(current, previous):
    return round(current - previous, 1) if current is not None and previous is not None else None


def compare_sessions(app, training_id: int, previous_training_id: int = 0) -> dict:
    """Compare a session with an earlier one, per movement: top weight, total reps and volume deltas.
    previous_training_id=0 picks the most recent earlier session that shares a movement."""
    current = app.api.find_session(training_id)
    current_moves = _movements(app, current)
    previous, previous_moves = None, {}
    if previous_training_id:
        previous = app.api.find_session(previous_training_id)
        previous_moves = _movements(app, previous)
    else:
        earlier = sorted((r for r in app.api.history_index().values()
                          if not is_health_import(r)
                          and str(r.get("startTime", "")) < str(current.get("startTime", ""))),
                         key=lambda r: str(r.get("startTime", "")), reverse=True)[:MAX_SESSIONS_SCANNED]
        for record in earlier:
            moves = _movements(app, record)
            if set(moves) & set(current_moves):
                previous, previous_moves = record, moves
                break
    rows = []
    for key, now in current_moves.items():
        before = previous_moves.get(key)
        row = {"name": now["name"], "current": now, "previous": before}
        if before:
            row["change"] = {"topWeight": _delta(now["topWeight"], before["topWeight"]),
                             "reps": now["reps"] - before["reps"],
                             "volume": round(now["volume"] - before["volume"], 1)}
        rows.append(row)
    out = {"displayUnit": app.api.unit, "current": record_summary(current), "previous": record_summary(previous),
           "movements": rows}
    if previous is None:
        out["note"] = "No earlier session shares a movement with this one."
    return out


def _latest_top_set(app, group_id: int) -> dict | None:
    days = sorted(app.api.exercise_stats(group_id, max_days=10), key=lambda r: str(r.get("dayStr", "")), reverse=True)
    index = app.api.history_index()
    for day in days:
        if not day.get("maxWeight"):
            continue
        for record in (r for r in index.values() if str(r.get("startTime", ""))[:10] == day.get("dayStr")):
            for exercise in _exercises(app, record):
                best = _best_set(exercise) if exercise["groupId"] == group_id else None
                if best:
                    return {"date": day["dayStr"], "weight": best["weight"], "reps": best["reps"],
                            "trainingId": int(record["trainingId"])}
    return None


def suggest_load(app, reps: int, exercise: str = "", groupId: int = 0, rir: int = 2) -> dict:
    """Suggest a working weight for `reps` reps leaving `rir` reps in reserve, from the movement's most
    recent best set (Epley estimate), falling back to the user's saved load anchor. Reports its basis."""
    if not 1 <= int(reps) <= 30:
        raise ToolError("reps must be between 1 and 30.")
    if not 0 <= int(rir) <= 5:
        raise ToolError("rir (reps in reserve) must be between 0 and 5.")
    item = resolve_group(app, exercise, groupId)
    gid = item["groupId"]
    anchor = app.memory.preferences()["load_anchors"].get(str(gid))
    out = {"exercise": {"groupId": gid, "name": item["name"]}, "displayUnit": app.api.unit,
           "reps": int(reps), "rir": int(rir), "anchor": anchor}
    if (app.memory.marks().get(gid) or {}).get("mark") == "avoided":
        out["warning"] = "This movement is marked ⊘ avoided."
    if item["kind"] != "reps":
        return {**out, "suggestedWeight": None, "note": "This movement is timed or level-based, so there's no weight to suggest."}
    basis = _latest_top_set(app, gid)
    if basis:
        estimate = epley(basis["weight"], basis["reps"])
        target = round_half(estimate / (1 + (int(reps) + int(rir)) / 30.0))
        return {**out, "suggestedWeight": target, "basis": {**basis, "estimated1RM": round(estimate, 1)},
                "method": "Epley 1RM from the most recent best set, solved for reps + rir."}
    if anchor is not None:
        return {**out, "suggestedWeight": anchor, "basis": None, "note": "No logged sets found; using the saved load anchor."}
    return {**out, "suggestedWeight": None, "basis": None,
            "note": "No history or load anchor for this movement yet. Start light, or save an anchor with "
                    "set_preferences(load_anchors={group_id: weight})."}


MAX_BALANCE_SESSIONS = 40


def get_muscle_balance(app, days: int = 30) -> dict:
    """Which muscles the recent training actually loaded, and how balanced it was.

    Spreads each weighted set's volume over the muscles the library says a movement works:
    a MAIN muscle takes the full volume, an ASSISTING muscle half. Attributed totals
    therefore exceed the weight actually lifted — they are shares of attention, not a
    decomposition of load. Timed and level work (Vita, planks, rowing) carries no volume
    and is reported as `unweightedExercises` rather than silently counted as zero.

    `pushPull` and `upperLower` are ratios: 1.0 is balanced, above 1.0 favours push/upper.
    `notTrained` lists muscles with no volume in the window — useful, but read it next to
    `unweightedExercises` before concluding a muscle was neglected."""
    days = max(1, min(int(days), 365))
    records = _recent(app, days)[:MAX_BALANCE_SESSIONS]
    index = muscle_index(app.api.library())
    exercises: list[dict] = []
    for record in records:
        exercises.extend(_exercises(app, record))
    spread = attribute(exercises, index)
    by_muscle = spread["byMuscle"]
    ranked = sorted(by_muscle.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "windowDays": days,
        "sessions": len(records),
        "displayUnit": app.api.unit,
        "attribution": "main muscle 100%, assisting muscle 50%",
        "byMuscle": [{"muscle": m, "volume": v} for m, v in ranked],
        "byBodyPart": [{"bodyPart": p, "volume": v} for p, v in
                       sorted(spread["byBodyPart"].items(), key=lambda kv: kv[1], reverse=True)],
        "ratios": ratios(by_muscle),
        "notTrained": untrained(by_muscle, index),
        "unweightedExercises": spread["unweightedExercises"],
        "exercisesNotInLibrary": spread["exercisesNotInLibrary"],
    }
