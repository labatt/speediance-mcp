"""Adapt off-machine sets into the shape parsed sessions already have.

Speediance's manual log (session type 10) counts a day towards streaks, days trained,
minutes and calories, but stores no exercises, and there is no way to put them there:
app/freetraining/save is an UPDATE into a session row the machine itself created, so a
workout that never ran on the hardware cannot be written. The detail lives in our own
store (memory.offmachine_sets) instead.

This module's whole job is that nothing downstream has to know about it. A parsed
exercise in this codebase looks like `{name, groupId, kind, sets, reps, weights, setLog,
topWeight, volume, avgLoad}` — muscles.attribute() reads `groupId` and `volume` from it,
and get_session_detail returns it as-is. So we emit exactly that, and a hotel dumbbell
press counts towards volume-by-muscle like any machine set.

`source: "offmachine"` is stamped on every exercise so a caller can say where a number
came from. Nothing here may pass off-machine work off as machine data.
"""

from __future__ import annotations

SOURCE = "offmachine"


def _grouped(sets: list[dict]) -> dict:
    """Sets grouped by movement, first-seen order preserved.

    Keyed on (groupId, lowercased name): the same movement logged twice in a day collects
    into one exercise, while two different movements that both lack a group id stay apart.
    """
    grouped: dict[tuple, list[dict]] = {}
    for row in sets or []:
        key = (row.get("groupId"), str(row.get("name") or "").strip().lower())
        grouped.setdefault(key, []).append(row)
    return grouped


def _entry(row: dict, index: int) -> dict:
    """One set in the parsed-setLog shape."""
    return {
        "setIndex": index,
        "reps": int(row.get("reps") or 0),
        # Nothing prescribed these reps, so there is no target to compare against.
        "targetReps": None,
        "seconds": 0,
        "weight": float(row.get("weight") or 0) or None,
        "level": None,
        "side": str(row.get("side") or "both"),
        "maxHeartRate": None,
    }


def as_exercises(sets: list[dict]) -> list[dict]:
    """Off-machine sets -> parsed exercises, ready for muscles.attribute or a detail reply.

    A movement with no Speediance equivalent keeps groupId None: attribute() then reports
    it as unattributable rather than dropping it, which is the honest outcome — we know it
    was trained, but not which muscles it worked.

    Volume is reps x load, the same definition the machine's own parsing uses, so the two
    are comparable and can be summed.
    """
    out = []
    for (group_id, _), rows in _grouped(sets).items():
        ordered = sorted(rows, key=lambda r: (r.get("setIndex") or 0, r.get("id") or 0))
        entries = [_entry(r, i) for i, r in enumerate(ordered, 1)]
        weights = [e["weight"] for e in entries if e["weight"] is not None]
        volume = sum((e["reps"] or 0) * (e["weight"] or 0) for e in entries)
        notes = [r["note"] for r in ordered if r.get("note")]
        out.append({
            "name": ordered[0].get("name") or "Exercise",
            "groupId": group_id,
            "kind": "reps",
            "sets": len(entries),
            "skippedSets": 0,
            "reps": [e["reps"] for e in entries],
            "weights": weights,
            "setLog": entries,
            "topWeight": max(weights) if weights else None,
            "volume": round(volume, 1),
            "avgLoad": round(sum(weights) / len(weights), 1) if weights else None,
            "avgLoadEstimated": False,
            "source": SOURCE,
            "location": ordered[0].get("location") or "",
            "notes": notes,
        })
    return out


def sessions(sets: list[dict]) -> list[dict]:
    """One summary per day that has off-machine sets, newest first.

    Duration and calories are deliberately absent: Speediance's own manual record holds
    those, and inventing a second answer would put two numbers on one workout.
    """
    by_day: dict[str, list[dict]] = {}
    for row in sets or []:
        by_day.setdefault(row.get("day"), []).append(row)
    out = []
    for day in sorted(by_day, reverse=True):
        rows = by_day[day]
        exercises = as_exercises(rows)
        locations = [loc for loc in {r.get("location") or "" for r in rows} if loc]
        out.append({
            "day": day,
            "trainingId": next((r["trainingId"] for r in rows if r.get("trainingId")), None),
            "exercises": exercises,
            "exerciseCount": len(exercises),
            "setCount": len(rows),
            "volume": round(sum(e["volume"] for e in exercises), 1),
            "location": locations[0] if len(locations) == 1 else "",
            "source": SOURCE,
        })
    return out
