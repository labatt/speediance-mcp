"""Off-machine training tools: log and read the exercise detail Speediance cannot hold."""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from ..library import resolve_exercise
from ..speediance import offmachine as adapt
from ._common import library_items, parse_date

MAX_EXERCISES = 40
MAX_SETS_PER_EXERCISE = 50


def _soft_resolve(items: list[dict], name: str, group_id) -> tuple[int | None, str, str]:
    """Resolve a movement to a Speediance group id WITHOUT failing when there is none.

    Unlike resolve_group, an unmatched name is not an error here: a movement Speediance
    does not stock is still worth logging — it counts towards the day and the log, just
    not towards per-muscle attribution. Returns (groupId or None, name to store, why).
    """
    if group_id not in (None, "", 0):
        for item in items:
            if item["groupId"] == int(group_id):
                return item["groupId"], item["name"], "matched by group_id"
        raise ToolError(f"No exercise with group_id {group_id} in the library.")
    cleaned = str(name or "").strip()
    if not cleaned:
        raise ToolError("Every exercise needs a name.")
    item, candidates = resolve_exercise(items, cleaned)
    if item:
        return item["groupId"], item["name"], "matched by name"
    # Ambiguity is NOT fatal either: logging the workout matters more than picking the
    # exact library row, and the reply says which movements went unmatched so the user or
    # the model can refine them later.
    why = ("several library movements matched that name, so it was stored unmatched"
           if candidates else "not in the Speediance library")
    return None, cleaned, why


def log_off_machine_workout(app, day: str, exercises: list[dict], location: str = "",
                            training_id: int | None = None) -> dict:
    """Record a workout done AWAY from the Gym Monster — a hotel gym, free weights, anywhere else.

    Use this when the user describes training that did not happen on the machine. Speediance's
    own manual log (which they add in the app) already makes the day count towards their streak,
    days trained, minutes and calories — but it stores NO exercises, and there is no way to put
    them there. This stores the movements so the work also counts towards volume-by-muscle and
    personal bests instead of being an inert entry.

    `day` is YYYY-MM-DD and may be in the past, so a trip can be backfilled. `exercises` is a
    list of {"name": "Dumbbell Bench Press", "sets": 3, "reps": 10, "weight": 40}. Optional per
    exercise: "group_id" to name a library movement exactly, "side" ("both"/"left"/"right" — use
    left or right for a single-arm or single-leg set, so it isn't counted as both), and "note".
    "sets" is how many identical sets were done; for sets that differ, list the movement more
    than once. Weight 0 means bodyweight, which is recorded rather than rejected.

    Weights are in the account's displayUnit — never convert. Optional `location` (e.g. "hotel")
    and `training_id` (the Speediance manual session this belongs to) are stored as given.

    Each movement is matched to the Speediance library so it resolves to muscles; the reply's
    `unmatched` lists any that could not be, which still count as trained but cannot be
    attributed to muscles. Nothing is written unless EVERY set validates."""
    day = parse_date(day, "day")
    if not isinstance(exercises, list) or not exercises:
        raise ToolError("exercises must be a non-empty list.")
    if len(exercises) > MAX_EXERCISES:
        raise ToolError(f"That is more than {MAX_EXERCISES} exercises for one session.")

    items = library_items(app)
    rows, unmatched = [], []
    for number, exercise in enumerate(exercises, 1):
        if not isinstance(exercise, dict):
            raise ToolError(f"exercises[{number}] must be an object.")
        group_id, name, why = _soft_resolve(items, exercise.get("name"),
                                            exercise.get("group_id") or exercise.get("groupId"))
        if group_id is None:
            unmatched.append({"name": name, "reason": why})
        # `or 1` would be wrong here: it cannot tell an absent `sets` from an explicit 0,
        # and silently logging one set for "sets": 0 hides a caller mistake.
        raw_sets = exercise.get("sets")
        try:
            count = 1 if raw_sets in (None, "") else int(raw_sets)
        except (TypeError, ValueError):
            raise ToolError(f"{name}: sets must be a whole number, got {raw_sets!r}.")
        if not 1 <= count <= MAX_SETS_PER_EXERCISE:
            raise ToolError(f"{name}: sets must be between 1 and {MAX_SETS_PER_EXERCISE}.")
        for _ in range(count):
            rows.append({"name": name, "groupId": group_id, "reps": exercise.get("reps"),
                         "weight": exercise.get("weight"), "side": exercise.get("side"),
                         "note": exercise.get("note")})

    try:
        stored = app.memory.log_offmachine_sets(day, rows, training_id=training_id,
                                                location=location)
    except ValueError as exc:
        # Validation refused the batch and wrote nothing; say what was wrong.
        raise ToolError(str(exc)) from None

    session = (adapt.sessions(stored) or [{}])[0]
    out = {"day": day, "setsStored": len(stored), "displayUnit": app.api.unit,
           "exercises": session.get("exercises", []), "volume": session.get("volume"),
           "location": location or "", "trainingId": training_id,
           "note": ("Stored as off-machine work. It now counts towards volume by muscle and can "
                    "set a personal best. Speediance itself still holds the day, duration and "
                    "calories — this does not change anything on the machine.")}
    if unmatched:
        out["unmatched"] = unmatched
        out["unmatchedNote"] = ("These movements are not matched to the Speediance library, so they "
                                "count as trained but cannot be attributed to muscles. Pass a "
                                "group_id, or use list_exercises to find the right name, to fix that.")
    return out


def get_off_machine_log(app, start: str, end: str) -> dict:
    """Off-machine workouts (hotel gyms, free weights) logged between two dates, inclusive.

    This is the exercise detail for days trained away from the machine — Speediance stores none
    for its own manual log, so this is the only place it exists. Weights are in displayUnit.
    Read it alongside get_calendar or get_training_stats: those report what Speediance knows
    about those days (that they happened, for how long), while this reports what was done."""
    start = parse_date(start, "start")
    end = parse_date(end, "end")
    if start > end:
        raise ToolError("start must be on or before end.")
    sets = app.memory.offmachine_sets(start, end)
    sessions = adapt.sessions(sets)
    return {"start": start, "end": end, "displayUnit": app.api.unit,
            "days": len(sessions), "sets": len(sets), "sessions": sessions,
            "note": ("Logged off the machine. These days also appear in Speediance's own history "
                     "when the user added a manual entry there, which is what makes them count "
                     "towards streaks and days trained.") if sessions else
                    "Nothing logged off the machine in this window."}


def delete_off_machine_day(app, day: str) -> dict:
    """Remove every off-machine set logged for one day.

    Only touches our own store — it cannot and does not delete anything from Speediance, so a
    manual entry the user made in the app stays, and the day still counts as trained. Ask the
    user before calling this: the sets are not recoverable."""
    day = parse_date(day, "day")
    removed = app.memory.delete_offmachine_day(day)
    return {"day": day, "deleted": removed,
            "note": ("Removed from the off-machine log only. Anything the user logged in the "
                     "Speediance app itself is untouched, so the day still counts as trained.")}
