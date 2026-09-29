from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from ..library import accessory_names, filter_exercises, summarize_exercise
from ..speediance.client import NotFound
from ._common import AmbiguousExercise, library_items, resolve_group

MAX_HISTORY = 500
LIST_FIELDS = ("groupId", "name", "category", "bodyParts", "muscles", "equipment", "unilateral", "kind", "mark")


def list_exercises(app, query: str = "", muscle: str = "", category: str = "", equipment: str = "",
                   kind: str = "", owned_only: bool = False, include_avoided: bool = False, limit: int = 60) -> dict:
    """Search the Speediance exercise library. Filters: name words (`query`), body part or muscle
    (`muscle`, e.g. "chest", "biceps"), library tab (`category`), equipment name, `kind`
    ("reps", "timed" or "level" for Vita), and
    `owned_only` (only moves whose equipment you own — set it with set_preferences(owned_equipment)).
    Movements needing equipment listed in set_preferences(unusable_equipment) are ALWAYS hidden.
    ⊘avoided movements are hidden unless include_avoided=true; ★preferred ones sort first.
    The first call downloads the library (~30 s); later calls use a 24-hour cache."""
    items = library_items(app)
    marks = app.memory.marks()
    preferences = app.memory.preferences()
    owned = preferences["owned_equipment"]
    unusable = preferences["unusable_equipment"]
    if owned_only and not owned:
        raise ToolError("No owned equipment is saved yet. Call list_accessories, then "
                        "set_preferences(owned_equipment=[names]).")
    rows = filter_exercises(items, query=query, muscle=muscle, category=category, equipment=equipment,
                            kind=kind, owned=owned, owned_only=owned_only, marks=marks, include_avoided=include_avoided,
                            limit=max(1, min(int(limit), 200)), unusable=unusable)
    return {"count": len(rows), "displayUnit": app.api.unit,
            "exercises": [{k: row[k] for k in LIST_FIELDS} for row in rows]}


def get_exercise(app, group_id: int) -> dict:
    """One movement's details: muscles, equipment, whether it's unilateral (one side at a time —
    matters when building workouts), form description and cues, image and video."""
    try:
        raw = app.api.exercise(group_id)
    except NotFound:
        raw = None
    if not raw:
        raise ToolError(f"No exercise with group_id {group_id}.")
    item = summarize_exercise(raw, accessory_names(app.api.accessories()))
    variants = raw.get("actionLibraryList") or []
    item.pop("equipmentIds")
    return {**item,
            "description": raw.get("context") or raw.get("showDetails") or "",
            "cues": {k: raw[k] for k in ("motionFeeling", "breathingRate", "errorCorrection") if raw.get(k)},
            "image": raw.get("img"),
            "video": variants[0].get("videoPath") if variants else None,
            "mark": (app.memory.marks().get(int(group_id)) or {}).get("mark")}


def mark_exercise(app, mark: str, group_id: int = 0, name: str = "", reason: str = "") -> dict:
    """Mark a movement ★preferred, ⊘avoided, or clear it ("none"). Identify it by group_id, or by
    `name` when you have no id (an ambiguous name marks nothing and lists candidates). Use when the
    user makes a LASTING per-exercise preference clear (it always hurts / they love it). Avoided moves
    are never programmed unless the user asks for them by name.
    reason: optional short note on why (e.g. 'left shoulder'), shown to you and in the web app."""
    if str(mark).strip().lower() not in ("preferred", "avoided", "none"):
        raise ToolError("mark must be 'preferred', 'avoided' or 'none'.")
    item = resolve_group(app, name, group_id)
    try:
        return app.memory.mark_exercise(item["groupId"], mark, item["name"], reason)
    except ValueError as exc:
        raise ToolError(str(exc)) from None


def list_accessories(app) -> dict:
    """Speediance's accessory catalog (bars, handles, rope, benches, AeroRow...), deduplicated by name,
    each flagged `owned` and `usable`. Save what the user owns with set_preferences(owned_equipment=[names])."""
    preferences = app.memory.preferences()
    owned = {o.lower() for o in preferences["owned_equipment"]}
    unusable = {u.lower() for u in preferences["unusable_equipment"]}
    by_name: dict[str, dict] = {}
    for item in app.api.accessories():
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        entry = by_name.setdefault(name.lower(), {"name": name, "ids": [],
                                                  "type": "furniture" if item.get("type") == 1 else "attachment"})
        entry["ids"].append(item.get("id"))
    rows = [{**e, "owned": e["name"].lower() in owned, "usable": e["name"].lower() not in unusable}
            for e in sorted(by_name.values(), key=lambda e: e["name"].lower())]
    return {"accessories": rows,
            "hint": "Save owned items with set_preferences(owned_equipment=[names]); "
                    "list_exercises(owned_only=true) then hides moves needing anything else. "
                    "Gear owned but unusable goes in set_preferences(unusable_equipment=[names]) — "
                    "those movements are hidden everywhere and must never be planned."}


def get_exercise_history(app, exercise: str = "", groupId: int = 0, limit: int = 50) -> dict:
    """EVERY time the user has done ONE movement, oldest -> newest: the "how is my bench press going?"
    tool. Give the name as the user says it, or a groupId. If several exercises match, nothing is
    fetched and the reply is {needsPick:true, matches:[...]} — ask which one. One entry per training
    DAY (same-day sessions combined): topWeight, volume, and minWeight when the day had a range."""
    try:
        item = resolve_group(app, exercise, groupId)
    except AmbiguousExercise as exc:
        return {"needsPick": True, "matches": exc.matches}
    rows = sorted(app.api.exercise_stats(item["groupId"], max_days=max(1, min(int(limit), MAX_HISTORY))),
                  key=lambda r: str(r.get("dayStr", "")))
    sessions = []
    for row in rows:
        entry = {"date": row.get("dayStr"), "topWeight": row.get("maxWeight"), "volume": row.get("totalCapacity")}
        if row.get("minWeight") not in (None, row.get("maxWeight")):
            entry["minWeight"] = row.get("minWeight")
        sessions.append(entry)
    head = {"exercise": {"groupId": item["groupId"], "name": item["name"],
                         "muscle": (item["muscles"] or [None])[0], "equipment": item["equipment"],
                         "kind": item["kind"]},
            "displayUnit": app.api.unit, "source": "userActionStatPage"}
    if not sessions:
        return {**head, "sessions": [], "summary": {"sessions": 0}, "note": "No logged history for this movement yet."}
    best = max(sessions, key=lambda s: s["topWeight"] or 0)
    best_volume = max(sessions, key=lambda s: s["volume"] or 0)
    return {**head, "sessions": sessions,
            "summary": {"sessions": len(sessions), "firstSeen": sessions[0]["date"], "lastSeen": sessions[-1]["date"],
                        "latestTopWeight": sessions[-1]["topWeight"],
                        "bestWeight": {"value": best["topWeight"], "date": best["date"]},
                        "bestVolume": {"value": best_volume["volume"], "date": best_volume["date"]}},
            "note": "One entry per training day; several sessions on the same day are combined."}
