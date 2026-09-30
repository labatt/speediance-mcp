from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from ..library import accessory_names, summarize_exercise, variant_id
from ..speediance.client import Rejected
from ..speediance.writes import build_template, mode_name, read_template, sets_for_kind, validate_sets, verify
from ._common import resolve_group

MAX_NAME = 60
# templatePresetId values whose stored weights are the real, explicit loads: -1 is the app's
# "Customize" mode, and 0/1 are what older writes of this server sent. Any other preset is a
# Speediance preset/RM load whose stored weights can't be rebuilt safely.
EXPLICIT_PRESETS = (-1, 0, 1)


def _explicit_preset(preset) -> bool:
    if preset is None or preset == "":
        return True
    try:
        return int(preset) in EXPLICIT_PRESETS
    except (TypeError, ValueError):
        return False


def _row_by_code(app, handle) -> dict:
    """A template by its `code` (preferred — it survives edits) or its current numeric id."""
    key = str(handle if handle is not None else "").strip()
    for row in app.api.templates():
        if row.get("code") == key or str(row.get("id")) == key:
            return row
    raise ToolError(f"No workout template {key!r}. Call list_my_workouts for current codes.")


def _by_variant(app) -> dict:
    out = {}
    for raw in app.api.library():
        for variant in raw.get("actionLibraryList") or []:
            out[variant.get("id")] = raw
    return out


def list_my_workouts(app) -> dict:
    """The user's saved custom workout templates. Use a template's `code` with get_workout,
    update_workout, delete_workout or schedule_workout."""
    rows = app.api.templates()
    return {"workouts": [{"id": r.get("id"), "code": r.get("code"), "name": r.get("name"),
                          "exercises": r.get("actionNum"), "durationMinute": r.get("durationMinute")} for r in rows],
            "slots": {"used": len(rows), "limit": None, "left": None},
            "note": "Speediance caps how many custom workouts an account holds, but no known endpoint reports the "
                    "cap. If a create is rejected for being over it, update or delete an existing template — "
                    "never delete one without asking the user."}


def get_workout(app, code: str) -> dict:
    """A template's full prescription: exercises in order, each set's reps (or seconds), weight,
    Vita level, side, rest and mode ("standard", "chain" or "eccentric"; an unknown code stays a
    number). Weights are in displayUnit. The chain/eccentric overload isn't stored — the user dials
    it in on the machine. After a session Speediance rewrites the template to what was actually run,
    modes included. presetId -1 (presetName "Customize") is where the machine runs these weights; a
    positive presetId is an app preset (Gain Muscle, Stamina, Strength), where the machine sets the
    load from the preset and the user's 1RM, so the stored weights are not what it runs."""
    row = _row_by_code(app, code)
    detail = app.api.template(row["code"])
    if not detail:
        raise ToolError(f"Speediance returned no detail for template {code!r}.")
    workout = read_template(detail)
    variants = _by_variant(app)
    for exercise in workout["exercises"]:
        raw = variants.get(exercise["actionLibraryId"])
        if raw:
            exercise["kind"] = summarize_exercise(raw)["kind"]
            exercise["sets"] = sets_for_kind(exercise["kind"], exercise["sets"])
        for s in exercise["sets"]:
            s["mode"] = mode_name(s["mode"])
    return {**workout, "displayUnit": app.api.unit}


def _as_int(value, field: str, number: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ToolError(f"exercises[{number}].{field} must be a whole number.") from None


def _resolve_specs(app, exercises) -> list[dict]:
    if not isinstance(exercises, list) or not exercises:
        raise ToolError("exercises must be a non-empty list.")
    raw_by_id = {raw.get("id"): raw for raw in app.api.library()}
    specs = []
    for number, exercise in enumerate(exercises, 1):
        if not isinstance(exercise, dict):
            raise ToolError(f"exercises[{number}] must be an object.")
        raw_group_id = exercise.get("group_id") or exercise.get("groupId") or 0
        group_id = _as_int(raw_group_id, "group_id", number)
        item = resolve_group(app, str(exercise.get("name") or ""), group_id)
        vid = variant_id(raw_by_id.get(item["groupId"]) or {})
        if vid is None:
            raise ToolError(f"{item['name']} has no playable variant in the library.")
        raw_rest = exercise.get("rest_seconds")
        rest_seconds = 60 if raw_rest is None else _as_int(raw_rest, "rest_seconds", number)
        try:
            sets = validate_sets(item["kind"], exercise.get("sets"), rest_seconds, unit=app.api.unit)
        except ValueError as exc:
            raise ToolError(f"{item['name']}: {exc}.") from None
        specs.append({"groupId": item["groupId"], "variantId": vid, "name": item["name"], "kind": item["kind"],
                      "unilateral": item["unilateral"], "sets": sets})
    return specs


def _specs_from_stored(app, detail: dict) -> list[dict]:
    variants = _by_variant(app)
    names = accessory_names(app.api.accessories())
    specs = []
    for exercise in read_template(detail)["exercises"]:
        raw = variants.get(exercise["actionLibraryId"])
        if raw is None:
            raise ToolError(f"Can't rebuild '{exercise['name']}' — it's no longer in the library. "
                            "Pass the full exercises list instead.")
        item = summarize_exercise(raw, names)
        preset_load = not _explicit_preset(exercise.get("presetId"))
        if item["kind"] == "reps" and not preset_load:
            preset_load = any(not s["weight"] for s in exercise["sets"])
        if preset_load:
            raise ToolError(f"Can't safely rebuild '{item['name']}' — it was built with a Speediance preset/RM load "
                            "(or has no stored weight), so a name-only update could zero its loads; "
                            "pass the full exercises list instead.")
        sets = sets_for_kind(item["kind"], exercise["sets"])
        try:
            validated = validate_sets(item["kind"], sets)
        except ValueError as exc:
            raise ToolError(f"Can't safely rebuild '{item['name']}' from its stored form ({exc}) — "
                            "pass the full exercises list instead.") from None
        specs.append({"groupId": item["groupId"], "variantId": exercise["actionLibraryId"], "name": item["name"],
                      "kind": item["kind"], "unilateral": item["unilateral"], "sets": validated,
                      "selectCompletionMethod": exercise.get("selectCompletionMethod")})
    return specs


def _avoided_flags(app, specs: list[dict]) -> dict:
    """Any spec whose groupId the user has marked ⊘avoided, so the caller can tell them."""
    marks = app.memory.marks()
    avoided = [{"groupId": s["groupId"], "name": s["name"], "reason": marks[s["groupId"]]["reason"]}
              for s in specs if marks.get(s["groupId"], {}).get("mark") == "avoided"]
    if not avoided:
        return {}
    names = ", ".join(a["name"] for a in avoided)
    return {"avoidedExercises": avoided,
            "avoidedWarning": f"This workout includes exercises the user marked as avoided: {names}. "
                              "Tell the user."}


def _fact_guard(app) -> dict:
    """The user's hard constraints and uncurated legacy facts, so the caller re-checks the workout."""
    digest = app.memory.fact_digest()
    out = {}
    hard = [{"id": f["id"], "text": f["text"], **({"scope": f["scope"]} if f.get("scope") else {})}
            for f in digest["constraints"]["hard"]]
    if hard:
        out["hardConstraints"] = hard
    if digest["legacyUnreviewed"]:
        out["legacyUnreviewed"] = digest["legacyUnreviewed"]
    return out


def _verified(app, body: dict, row: dict) -> dict:
    mismatches = verify(body, app.api.template(row["code"]))
    out = {"id": row.get("id"), "code": row.get("code"), "name": row.get("name"),
           "verified": not mismatches, "exercises": len(body["actionLibraryList"])}
    if mismatches:
        out["mismatches"] = mismatches
        out["warning"] = "The saved template doesn't match what was sent — check it in the Speediance app before training."
    return out


def _clean_name(name) -> str:
    name = str(name or "").strip()
    if not name or len(name) > MAX_NAME:
        raise ToolError(f"name must be 1-{MAX_NAME} characters.")
    return name


def create_workout(app, name: str, exercises: list[dict]) -> dict:
    """Create a custom workout template. `exercises` is an ordered list of
    {"name": "..." or "group_id": N, "sets": [...], "rest_seconds": 60}. Sets by movement kind:
    reps -> {"reps": 10, "weight": 50}; timed -> {"seconds": 45}; Vita (level) -> {"seconds": 30, "level": 12}.
    Optional per set: "side" 1=left / 2=right (unilateral moves alternate automatically), "rest",
    "mode" "standard" (default) / "chain" / "eccentric". The chain or eccentric overload amount can't
    be set through the API or stored in the template — tell the user to dial it in on the machine.
    Weights are in displayUnit, and the template is saved in the app's "Customize" mode, so the
    machine runs exactly these weights and modes. On a kg account a load must be whole kg, up to
    100: templates can't hold half kilos, so round and tell the user they can fine-tune on the machine. The template is read back after saving; verified:false means Speediance
    stored something different — tell the user. Never program a ⊘avoided movement unless asked by name.
    The reply carries the user's hardConstraints and legacyUnreviewed facts when there are any.
    Re-check these against the workout before telling the user it's done."""
    name = _clean_name(name)
    specs = _resolve_specs(app, exercises)
    avoided = _avoided_flags(app, specs)
    before = {r.get("id") for r in app.api.templates()}
    body = build_template(name, specs, unit=app.api.unit, device_type=app.api.device_type)
    try:
        app.api.save_template(body)
    except Rejected as exc:
        raise ToolError(f"Speediance rejected the workout: {exc.api_message or f'code {exc.code}'}. "
                        "If your account is at its custom-workout limit, update or delete an existing template "
                        "(ask the user before deleting).") from None
    created = [r for r in app.api.templates() if r.get("id") not in before and r.get("name") == name]
    if not created:
        raise ToolError("Speediance accepted the workout but it isn't in your list yet — check list_my_workouts "
                        "before retrying, to avoid a duplicate.")
    return {**_verified(app, body, max(created, key=lambda r: r.get("id") or 0)), **avoided, **_fact_guard(app)}


def update_workout(app, template_id: str | int, name: str | None = None, exercises: list[dict] | None = None) -> dict:
    """Edit a template in place (an edit never uses a new slot). `template_id` is its `code` (preferred)
    or numeric id. Omitted fields keep their current value; `exercises`, when given, replaces the whole
    list (same format as create_workout) — start from get_workout. Verified by read-back.
    The reply carries the user's hardConstraints and legacyUnreviewed facts when there are any.
    Re-check these against the workout before telling the user it's done."""
    row = _row_by_code(app, template_id)
    if exercises is None:
        detail = app.api.template(row["code"])
        if not detail:
            raise ToolError(f"Speediance returned no detail for template {template_id!r}.")
        specs = _specs_from_stored(app, detail)
    else:
        specs = _resolve_specs(app, exercises)
    avoided = _avoided_flags(app, specs)
    # Only a newly-supplied name is cleaned/validated; an unchanged existing name (possibly
    # longer than MAX_NAME, e.g. from the app) must not block an exercises-only update.
    new_name = _clean_name(name) if name is not None else str(row.get("name") or "")
    # An in-place edit keeps the code but re-inserts the row under a NEW numeric id (verified live
    # 2026-09-27), so a duplicate shows up as a new CODE, not a new id.
    before_codes = {r.get("code") for r in app.api.templates()}
    body = build_template(new_name, specs, unit=app.api.unit, device_type=app.api.device_type, template_id=row["id"])
    app.api.save_template(body)
    rows_after = app.api.templates()
    matching = next((r for r in rows_after if r.get("code") == row["code"]), None)
    new_rows = [r for r in rows_after if r.get("code") not in before_codes]

    problems = []
    if matching is None:
        problems.append(f"template {row['code']!r} is no longer listed after the update — it may have "
                        "been replaced.")
    elif matching.get("name") != new_name:
        problems.append(f"name sent {new_name!r} but Speediance shows {matching.get('name')!r}.")
    if new_rows:
        problems.append("Speediance created a new template instead of editing this one (new code "
                        f"{new_rows[0].get('code')}) — check list_my_workouts for a duplicate.")

    # Don't fabricate the name when the row is missing; report the mismatch instead.
    result_row = {"id": matching.get("id") if matching else row["id"], "code": row["code"],
                  "name": matching.get("name") if matching else None}
    out = {**_verified(app, body, result_row), **avoided, **_fact_guard(app)}
    if problems:
        out["verified"] = False
        out["mismatches"] = problems + out.get("mismatches", [])
        out["warning"] = ("The update didn't apply as expected — check list_my_workouts and the "
                          "Speediance app before training.")
    return out


def delete_workout(app, template_id: str | int) -> dict:
    """Permanently delete a custom template, by `code` (preferred) or numeric id. Not reversible —
    only when the user asked for it."""
    row = _row_by_code(app, template_id)
    app.api.delete_template(row["id"])
    return {"deleted": True, "code": row["code"], "name": row.get("name")}
