"""Helpers shared by the tool modules."""

from __future__ import annotations

import datetime as dt

from mcp.server.mcpserver.exceptions import ToolError

from ..library import accessory_names, resolve_exercise, summarize_exercise
from ..speediance.parsing import is_cardio, normalize_session, parse_list_exercises
from ..speediance.routes import FREE_ROUTE


def parse_date(value, field: str = "date") -> str:
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise ToolError(f"{field} must be a real date in YYYY-MM-DD form, got {value!r}.") from None


def parse_month(value) -> tuple[str, str, str]:
    try:
        if not isinstance(value, str) or len(value) != 7:
            raise ValueError
        year, month = value.split("-")
        first = dt.date(int(year), int(month), 1)
    except ValueError:
        raise ToolError(f"month must be YYYY-MM, got {value!r}.") from None
    following = dt.date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return value, first.isoformat(), (following - dt.timedelta(days=1)).isoformat()


def is_health_import(record: dict) -> bool:
    """Walks and rides synced from a phone health app — not machine sessions."""
    return not record.get("trainingId") or bool(record.get("belongUserHealth"))


def session_exercises(app, route: str, payload, training_id) -> dict:
    """Parse a session's exercises, falling back to freeTrainingDetail for a quick single-exercise
    session: `freeTraining` on a type-7/1/6 session sometimes has no `actionList` at all (a strength
    session logged as one movement), while `freeTrainingDetail` still has the real per-set data in
    the same list-route shape as custom-template/course sessions. Guided cardio already gets its
    data from the separate `intervals` route, so this never fires for it."""
    parsed = normalize_session(route, payload)
    if (route == FREE_ROUTE and isinstance(payload, dict) and not parsed["exercises"]
            and not is_cardio(payload)):
        rows = app.api.free_intervals(training_id)
        if rows:
            parsed = {**parsed, "exercises": parse_list_exercises(rows)}
    return parsed


def other_activity(record: dict) -> dict:
    """A phone-health import (walk, ride...) as a small summary. Not a gym session."""
    out = {"title": record.get("title") or "Phone health app activity",
           "minutes": round((record.get("trainingTime") or 0) / 60),
           "calorie": record.get("calorie"), "source": "phone health app"}
    try:
        miles = float(record.get("mileage") or 0)
    except (TypeError, ValueError):
        miles = 0.0
    if miles >= 0.01:
        out["distanceMiles"] = round(miles, 2)
    return out


def record_summary(record: dict | None) -> dict | None:
    if not record:
        return None
    return {"trainingId": record.get("trainingId"), "type": record.get("type"), "title": record.get("title"),
            "date": str(record.get("startTime", ""))[:10],
            "minutes": round((record.get("trainingTime") or 0) / 60),
            "calorie": record.get("calorie"), "volume": record.get("totalCapacity")}


class AmbiguousExercise(ToolError):
    def __init__(self, name: str, matches: list[dict]):
        self.matches = [{"groupId": m["groupId"], "name": m["name"],
                         "muscle": (m["muscles"] or [None])[0], "equipment": m["equipment"]} for m in matches]
        listing = "; ".join(f"{m['name']} (group_id {m['groupId']})" for m in self.matches)
        super().__init__(f"'{name}' matches several exercises: {listing}. Call again with the group_id you mean.")


def library_items(app) -> list[dict]:
    names = accessory_names(app.api.accessories())
    return [summarize_exercise(raw, names) for raw in app.api.library()]


def resolve_group(app, exercise: str = "", group_id: int = 0) -> dict:
    items = library_items(app)
    if group_id:
        for item in items:
            if item["groupId"] == int(group_id):
                return item
        raise ToolError(f"No exercise with group_id {group_id} in the library.")
    if not str(exercise or "").strip():
        raise ToolError("Give an exercise name or a group_id.")
    item, candidates = resolve_exercise(items, exercise)
    if item:
        return item
    if candidates:
        raise AmbiguousExercise(exercise, candidates)
    raise ToolError(f"No exercise matches '{exercise}'. Try list_exercises with a shorter query.")
