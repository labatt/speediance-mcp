"""Build, read back and verify custom-template bodies (spec §8.3). Pure.

Write-fault rules: totalCapacity is never null; every movement is sent with templatePresetId -1
(the app's "Customize" mode, where the machine runs the per-set weights and modes); on a kg account
the server reads a -1 movement's weights and capacity as pounds, so wire_body sends them x2.2, and
totalCapacity x2.2 always; unilateral movements auto-alternate sides; counterweight2 is always
empty; a kg account's loads are 0.5 kg steps below 10 kg and whole kg from 10 up to 100
(validate_sets with unit="kg"); every write is verified by reading the template back.
"""

from __future__ import annotations

import math

KG_LB_SCALE = 2.2
MAX_WEIGHT = 1000.0
# A kg template load moves in 0.5 kg steps below 10 kg and whole kg from 10 kg, up to the machine's
# 100 kg. Verified live 2026-10-05: 7.5, 8.5 and 9.5 were stored as 7.50, 8.50 and 9.50, while 12.5,
# 20.5, 22.5, 30.5 and 54.5 were cut to whole kg. App-authored templates hold the same half kilos,
# and the machine runs them: an 8.50 template ran 8.5 on every set across four sessions.
MAX_KG = 100
HALF_KG_BELOW = 10
# templatePresetId for every written movement. -1 is the app's "Customize" mode (confirmed on the
# machine, 2026-09-30): the machine runs the stored per-set weights. A positive id is one of the
# app's presets, where the load comes from the preset and the user's 1RM instead; the machine
# ignored the stored weights at preset 1 (pookey's coach client, 2026-08-14). Every movement of an
# app-authored template is -1. The dynamic-weight mode (sportMode) applies under any preset.
CUSTOMIZE_PRESET = -1
# Preset names, from the `templatePresetList` Speediance attaches to every template movement
# (read live 2026-09-30). -1 is not in that list; its name is from the machine.
PRESET_NAMES = {-1: "Customize", 1: "Gain Muscle", 3: "Stamina", 5: "Strength"}
# Per-set sportMode codes, as the machine's edit screen writes them (verified live 2026-09-30:
# Standard / Chain / Eccentric on sets 1-3 stored "1,2,3"). The overload amount of a chain or
# eccentric set is not stored anywhere: the user dials it in on the machine.
SET_MODES = {"standard": 1, "chain": 2, "eccentric": 3}
MODE_NAMES = {code: name for name, code in SET_MODES.items()}


def _int(value, default=0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _csv(value) -> list[str]:
    text = str(value or "").strip()
    return [part.strip() for part in text.split(",")] if text else []


def _weight(raw: dict, number: int) -> float:
    """A reps set's weight: int/float or a plain numeric string, finite, 0-1000, at most one
    decimal place. Anything else — missing, a bool, "50 lb", NaN, inf, too many decimals — is a
    ValueError rather than a silent coercion to 0 or a silently rounded value."""
    value = raw.get("weight")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"set {number}: weight must be a number")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"set {number}: weight must be a number") from None
    if not math.isfinite(value):
        raise ValueError(f"set {number}: weight must be a number")
    if value < 0 or value > MAX_WEIGHT:
        raise ValueError(f"set {number}: weight must be 0-{MAX_WEIGHT:g}")
    if abs(value * 10 - round(value * 10)) > 1e-9:
        raise ValueError(f"set {number}: weights have at most one decimal place")
    return value


def _whole(raw: dict, key: str, number: int) -> int:
    """A whole-number field (reps, seconds, level): an int, an integral float or a numeric string.
    Bools and fractional values (8.9 reps) are a ValueError, never truncated. Missing -> 0."""
    value = raw.get(key)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"set {number}: {key} must be a whole number")
    try:
        as_float = float(value)
    except ValueError:
        raise ValueError(f"set {number}: {key} must be a whole number") from None
    if not math.isfinite(as_float) or as_float != int(as_float):
        raise ValueError(f"set {number}: {key} must be a whole number")
    return int(as_float)


def _set_side(raw: dict, number: int) -> int | None:
    """None/0 (no side), or 1 (left) / 2 (right) as an int."""
    value = raw.get("side")
    error = ValueError(f"set {number}: side must be 1 (left) or 2 (right)")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise error
    try:
        as_float = float(value)
    except ValueError:
        raise error from None
    if as_float not in (0, 1, 2):
        raise error
    return int(as_float) or None


def _set_mode(raw: dict, number: int) -> int:
    """A set's sportMode code: a name from SET_MODES, or a positive code as get_workout returns an
    unknown one. Missing -> 1 (standard)."""
    value = raw.get("mode")
    if value is None:
        return 1
    if isinstance(value, str) and value.strip().lower() in SET_MODES:
        return SET_MODES[value.strip().lower()]
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 99:
        return value
    raise ValueError(f"set {number}: mode must be one of {', '.join(SET_MODES)}")


def mode_name(code) -> str | int:
    """A sportMode code as its name; an unknown code stays a number."""
    code = _int(code, 1) or 1
    return MODE_NAMES.get(code, code)


def _kg_load(weight: float, number: int) -> None:
    """A kg template load is a 0.5 kg step below HALF_KG_BELOW and whole kg from it, up to MAX_KG.
    Refused up front with the nearest loads, rather than letting Speediance cut it and the read-back
    fail."""
    if weight > MAX_KG:
        raise ValueError(f"set {number}: the machine's maximum is {MAX_KG} kg")
    if weight < HALF_KG_BELOW:
        if weight * 2 != int(weight * 2):
            low = int(weight * 2) / 2
            raise ValueError(f"set {number}: {weight:g} kg can't be saved in a template — below "
                             f"{HALF_KG_BELOW} kg loads go in 0.5 kg steps, so use {low:g} or {low + 0.5:g}")
    elif weight != int(weight):
        low, high = int(weight), int(weight) + 1
        raise ValueError(f"set {number}: {weight:g} kg can't be saved in a template — from {HALF_KG_BELOW} kg "
                         f"templates hold whole kg only (Speediance cuts {weight:g} to {low}), so use {low} or "
                         f"{high}, and the user can fine-tune the load on the machine")


def validate_sets(kind: str, sets, default_rest: int = 60, *, unit: str | None = None) -> list[dict]:
    """Sets as the builder takes them. unit="kg" also enforces the kg template grid; the rebuild of a
    stored template leaves it out, so loads Speediance already holds go back unchanged."""
    if not isinstance(sets, list) or not sets:
        raise ValueError("needs at least one set")
    if len(sets) > 20:
        raise ValueError("at most 20 sets per exercise")
    out = []
    for number, raw in enumerate(sets, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"set {number} must be an object")
        side = _set_side(raw, number)
        mode = _set_mode(raw, number)
        rest = _int(raw.get("rest", raw.get("rest_seconds", default_rest)), default_rest)
        if not 0 <= rest <= 600:
            raise ValueError(f"set {number}: rest must be 0-600 seconds")
        if kind == "reps":
            reps = _whole(raw, "reps", number)
            weight = _weight(raw, number)
            if not 1 <= reps <= 100:
                raise ValueError(f"set {number}: reps must be 1-100")
            if unit == "kg":
                _kg_load(weight, number)
            out.append({"reps": reps, "weight": weight, "side": side, "rest": rest, "mode": mode})
        else:
            seconds = _whole(raw, "seconds", number)
            if not 1 <= seconds <= 3600:
                raise ValueError(f"set {number}: this movement is timed, so give seconds (1-3600)")
            level = None
            if kind == "level":
                level = _whole(raw, "level", number)
                if level < 1:
                    raise ValueError(f"set {number}: Vita movements need a level of 1 or more")
            out.append({"seconds": seconds, "level": level, "side": side, "rest": rest, "mode": mode})
    return out


def _side(spec: dict, raw: dict, index: int) -> str:
    if raw.get("side"):
        return str(raw["side"])
    if spec["unilateral"]:
        return "1" if index % 2 == 0 else "2"
    return "0"


def _stored_mode_csv(spec: dict, key: str, count: int) -> str:
    """A spec's optional stored `selectCompletionMethod` CSV, when its entry count matches the set
    count; otherwise the "1" per set default."""
    values = _csv(spec.get(key))
    if len(values) == count:
        return ",".join(values)
    return ",".join("1" for _ in range(count))


def build_template(name: str, specs: list[dict], *, unit: str, device_type: int, template_id=None) -> dict:
    """The body in the account's display unit, which is what verify compares against. `unit` is
    not applied here: the server's unit handling is wire_body's, at save time."""
    actions, total = [], 0.0
    for spec in specs:
        sets, kind = spec["sets"], spec["kind"]
        timed = kind != "reps"
        capacity = 0.0 if timed else sum(s["reps"] * s["weight"] for s in sets)
        total += capacity
        rests = ",".join(str(s["rest"]) for s in sets)
        actions.append({
            "groupId": int(spec["groupId"]),
            "actionLibraryId": int(spec["variantId"]),
            "templatePresetId": CUSTOMIZE_PRESET,
            "setsAndReps": ",".join(str(s["seconds"] if timed else s["reps"]) for s in sets),
            "breakTime": rests,
            "breakTime2": rests,
            "sportMode": ",".join(str(s.get("mode") or 1) for s in sets),
            "leftRight": ",".join(_side(spec, s, i) for i, s in enumerate(sets)),
            "selectCompletionMethod": _stored_mode_csv(spec, "selectCompletionMethod", len(sets)),
            "completionMethod": ",".join(("2" if timed else "1") for _ in sets),
            "countType": ",".join(("2" if timed else "1") for _ in sets),
            "weights": ",".join(("0" if timed else f"{s['weight']:.1f}") for s in sets),
            "counterweight2": "",
            "counterweight": "",
            "level": ",".join((str(s["level"]) if kind == "level" else "0") for s in sets),
            "capacity": round(capacity, 1),
        })
    body = {"name": name, "actionLibraryList": actions,
            "totalCapacity": round(total, 1),
            "deviceType": int(device_type), "bgColor": 0}
    if template_id is not None:
        body["id"] = int(template_id)
    return body


def wire_body(body: dict, unit: str) -> dict:
    """The body as it goes over the wire. build_template's body is in the account's display unit,
    which is what verify compares the read-back against. On a kg account the server reads a
    non-positive preset's `weights` and `capacity` as pounds and stores them divided by 2.2, and
    reads `totalCapacity` that way whatever the preset, so those go out x2.2. lb accounts go out
    as built. Returns a copy."""
    if unit != "kg":
        return body
    actions = []
    for action in body["actionLibraryList"]:
        action = dict(action)
        if _int(action.get("templatePresetId")) <= 0:
            action["weights"] = ",".join(f"{(_float(w) or 0.0) * KG_LB_SCALE:.2f}" for w in _csv(action["weights"]))
            action["capacity"] = round((action.get("capacity") or 0.0) * KG_LB_SCALE, 2)
        actions.append(action)
    return {**body, "actionLibraryList": actions,
            "totalCapacity": round((body.get("totalCapacity") or 0.0) * KG_LB_SCALE, 1)}


def read_template(detail: dict) -> dict:
    actions = sorted((detail or {}).get("actionLibraryList") or [], key=lambda a: a.get("sort") or 0)
    exercises = []
    for action in actions:
        counts, weights = _csv(action.get("setsAndReps")), _csv(action.get("weights"))
        levels, sides = _csv(action.get("level")), _csv(action.get("leftRight"))
        modes = _csv(action.get("sportMode"))
        rests = _csv(action.get("breakTime2") or action.get("breakTime"))
        sets = []
        for i, count in enumerate(counts):
            side = _int(sides[i]) if i < len(sides) else 0
            sets.append({"count": _int(count),
                         "weight": _float(weights[i]) if i < len(weights) else None,
                         "level": _int(levels[i]) if i < len(levels) else 0,
                         "side": side if side in (1, 2) else None,
                         "rest": _int(rests[i], None) if i < len(rests) else None,
                         "mode": _int(modes[i], 1) or 1 if i < len(modes) else 1})
        exercises.append({"name": action.get("title") or "", "actionLibraryId": action.get("actionLibraryId"),
                          "presetId": action.get("templatePresetId"),
                          "presetName": PRESET_NAMES.get(_int(action.get("templatePresetId"), None)),
                          "sets": sets,
                          "selectCompletionMethod": action.get("selectCompletionMethod")})
    return {"id": detail.get("id"), "code": detail.get("code"), "name": detail.get("name"),
            "durationMinute": detail.get("durationMinute"), "exercises": exercises}


def sets_for_kind(kind: str, stored_sets: list[dict], default_rest: int = 60) -> list[dict]:
    out = []
    for s in stored_sets:
        rest = s["rest"] if s["rest"] is not None else default_rest
        if kind == "reps":
            out.append({"reps": s["count"], "weight": s["weight"] or 0.0, "side": s["side"], "rest": rest,
                        "mode": s["mode"]})
        else:
            out.append({"seconds": s["count"], "level": (s["level"] or None) if kind == "level" else None,
                        "side": s["side"], "rest": rest, "mode": s["mode"]})
    return out


def verify(body: dict, stored: dict | None) -> list[str]:
    """Compare what was sent with what Speediance stored. An empty list means verified."""
    sent = body["actionLibraryList"]
    got = sorted((stored or {}).get("actionLibraryList") or [], key=lambda a: a.get("sort") or 0)
    if len(sent) != len(got):
        return [f"sent {len(sent)} exercises, Speediance stored {len(got)}"]
    problems = []
    for number, (s, g) in enumerate(zip(sent, got), 1):
        label = g.get("title") or f"exercise {number}"
        if s["actionLibraryId"] != g.get("actionLibraryId"):
            problems.append(f"{label}: exercise sent {s['actionLibraryId']} but stored {g.get('actionLibraryId')}")
        if _csv(s["setsAndReps"]) != _csv(g.get("setsAndReps")):
            problems.append(f"{label}: reps/seconds sent {s['setsAndReps']} but stored {g.get('setsAndReps')}")
        sent_w = [_float(x) or 0.0 for x in _csv(s["weights"])]
        got_w = [_float(x) or 0.0 for x in _csv(g.get("weights"))]
        if got_w or any(sent_w):
            if len(sent_w) != len(got_w) or any(abs(a - b) > 0.05 for a, b in zip(sent_w, got_w)):
                problems.append(f"{label}: weights sent {s['weights']} but stored {g.get('weights')}")
        sent_sides, got_sides = _csv(s["leftRight"]), _csv(g.get("leftRight"))
        if (got_sides or any(_int(x) for x in sent_sides)) and sent_sides != got_sides:
            problems.append(f"{label}: sides sent {s['leftRight']} but stored {g.get('leftRight')}")
        sent_levels_raw, got_levels_raw = _csv(s["level"]), _csv(g.get("level"))
        sent_levels = [_int(x) for x in sent_levels_raw]
        got_levels = [_int(x) for x in got_levels_raw]
        if (got_levels_raw or any(sent_levels)) and sent_levels != got_levels:
            problems.append(f"{label}: levels sent {s['level']} but stored {g.get('level')}")
        sent_modes = [_int(x, 1) for x in _csv(s["sportMode"])]
        got_modes = [_int(x, 1) for x in _csv(g.get("sportMode"))]
        if (got_modes or any(m != 1 for m in sent_modes)) and sent_modes != got_modes:
            problems.append(f"{label}: set modes sent {s['sportMode']} but stored {g.get('sportMode')}")
        if _csv(s["breakTime2"]) != _csv(g.get("breakTime2")):
            problems.append(f"{label}: rest sent {s['breakTime2']} but stored {g.get('breakTime2')}")
        if g.get("templatePresetId") is not None and s["templatePresetId"] != g.get("templatePresetId"):
            problems.append(f"{label}: preset sent {s['templatePresetId']} but stored {g.get('templatePresetId')}")
    return problems
