"""Build, read back and verify custom-template bodies (spec §8.3). Pure.

Write-fault rules: totalCapacity is never null; templatePresetId and totalCapacity depend on the
account unit (lb: -1 and the raw sum — verified live; kg: 1 and x2.2 — per pookey/speediance-cli);
unilateral movements auto-alternate sides; counterweight2 is empty on a movement we build, but an
update carries a stored one through on every movement whose prescription it leaves unchanged
(carry_unmodelled); every write is verified by reading the template back.
"""

from __future__ import annotations

import math

KG_LB_SCALE = 2.2
MAX_WEIGHT = 1000.0


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


def validate_sets(kind: str, sets, default_rest: int = 60) -> list[dict]:
    if not isinstance(sets, list) or not sets:
        raise ValueError("needs at least one set")
    if len(sets) > 20:
        raise ValueError("at most 20 sets per exercise")
    out = []
    for number, raw in enumerate(sets, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"set {number} must be an object")
        side = _set_side(raw, number)
        rest = _int(raw.get("rest", raw.get("rest_seconds", default_rest)), default_rest)
        if not 0 <= rest <= 600:
            raise ValueError(f"set {number}: rest must be 0-600 seconds")
        if kind == "reps":
            reps = _whole(raw, "reps", number)
            weight = _weight(raw, number)
            if not 1 <= reps <= 100:
                raise ValueError(f"set {number}: reps must be 1-100")
            out.append({"reps": reps, "weight": weight, "side": side, "rest": rest})
        else:
            seconds = _whole(raw, "seconds", number)
            if not 1 <= seconds <= 3600:
                raise ValueError(f"set {number}: this movement is timed, so give seconds (1-3600)")
            level = None
            if kind == "level":
                level = _whole(raw, "level", number)
                if level < 1:
                    raise ValueError(f"set {number}: Vita movements need a level of 1 or more")
            out.append({"seconds": seconds, "level": level, "side": side, "rest": rest})
    return out


def _side(spec: dict, raw: dict, index: int) -> str:
    if raw.get("side"):
        return str(raw["side"])
    if spec["unilateral"]:
        return "1" if index % 2 == 0 else "2"
    return "0"


def _stored_mode_csv(spec: dict, key: str, count: int) -> str:
    """A spec's optional stored `sportMode`/`selectCompletionMethod` CSV, when its entry count
    matches the set count; otherwise the "1" per set default."""
    values = _csv(spec.get(key))
    if len(values) == count:
        return ",".join(values)
    return ",".join("1" for _ in range(count))


def build_template(name: str, specs: list[dict], *, unit: str, device_type: int, template_id=None) -> dict:
    preset = -1 if unit == "lb" else 1
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
            "templatePresetId": preset,
            "setsAndReps": ",".join(str(s["seconds"] if timed else s["reps"]) for s in sets),
            "breakTime": rests,
            "breakTime2": rests,
            "sportMode": _stored_mode_csv(spec, "sportMode", len(sets)),
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
            "totalCapacity": round(total * (KG_LB_SCALE if unit == "kg" else 1.0), 1),
            "deviceType": int(device_type), "bgColor": 0}
    if template_id is not None:
        body["id"] = int(template_id)
    return body


def _same_prescription(sent: dict, stored: dict) -> bool:
    """Whether a built movement asks for exactly what the stored one does: same exercise, counts,
    loads, sides, levels and rests."""
    if sent["actionLibraryId"] != stored.get("actionLibraryId"):
        return False
    if _csv(sent["setsAndReps"]) != _csv(stored.get("setsAndReps")):
        return False
    sent_w = [_float(x) or 0.0 for x in _csv(sent["weights"])]
    got_w = [_float(x) or 0.0 for x in _csv(stored.get("weights"))]
    if len(sent_w) != len(got_w) or any(abs(a - b) > 0.05 for a, b in zip(sent_w, got_w)):
        return False
    for key in ("leftRight", "level", "breakTime2"):
        if [_int(x) for x in _csv(sent[key])] != [_int(x) for x in _csv(stored.get(key))]:
            return False
    return True


def carry_unmodelled(body: dict, detail: dict | None) -> list[str]:
    """Carry a stored movement's counterweight setting into an update body. Mutates `body`.

    `counterweight2` is a machine setting the tools don't model: on the templates seen live it
    holds the `weight` of the movement's app preset (13 = Gain Muscle, preset 1; 17 = Stamina,
    preset 3) and the stored `weights` are that preset's %1RM of the user's 1RM. A rebuilt body
    sends it empty, which strips it. So for each built movement whose prescription is unchanged,
    the stored `counterweight2`/`counterweight` and positive `templatePresetId` are sent back as
    they were — verified live 2026-09-30: preset 3 + counterweight2 "17,..." + weights "33,..."
    re-posted is stored verbatim, loads included. A movement whose sets changed still goes out
    empty: the server resolves a sent counterweight2 into a load of its own, so keeping it could
    override the new weights. Returns the names of movements that lost a counterweight that way.
    """
    stored = sorted((detail or {}).get("actionLibraryList") or [], key=lambda a: a.get("sort") or 0)
    with_counterweight = [a for a in stored if a.get("counterweight2") or a.get("counterweight")]
    unused = list(with_counterweight)
    for sent in body["actionLibraryList"]:
        match = next((a for a in unused if _same_prescription(sent, a)), None)
        if match is None:
            continue
        unused.remove(match)
        sent["counterweight2"] = match.get("counterweight2") or ""
        sent["counterweight"] = match.get("counterweight") or ""
        # Only positive presets are carried: every counterweighted movement seen live has one, and
        # a positive preset stores `weights` verbatim. A non-positive one would rescale the loads.
        if _int(match.get("templatePresetId")) > 0:
            sent["templatePresetId"] = int(match["templatePresetId"])
    return [a.get("title") or str(a.get("actionLibraryId")) for a in unused]


def read_template(detail: dict) -> dict:
    actions = sorted((detail or {}).get("actionLibraryList") or [], key=lambda a: a.get("sort") or 0)
    exercises = []
    for action in actions:
        counts, weights = _csv(action.get("setsAndReps")), _csv(action.get("weights"))
        levels, sides = _csv(action.get("level")), _csv(action.get("leftRight"))
        rests = _csv(action.get("breakTime2") or action.get("breakTime"))
        sets = []
        for i, count in enumerate(counts):
            side = _int(sides[i]) if i < len(sides) else 0
            sets.append({"count": _int(count),
                         "weight": _float(weights[i]) if i < len(weights) else None,
                         "level": _int(levels[i]) if i < len(levels) else 0,
                         "side": side if side in (1, 2) else None,
                         "rest": _int(rests[i], None) if i < len(rests) else None})
        exercises.append({"name": action.get("title") or "", "actionLibraryId": action.get("actionLibraryId"),
                          "presetId": action.get("templatePresetId"), "sets": sets,
                          "sportMode": action.get("sportMode"),
                          "selectCompletionMethod": action.get("selectCompletionMethod")})
    return {"id": detail.get("id"), "code": detail.get("code"), "name": detail.get("name"),
            "durationMinute": detail.get("durationMinute"), "exercises": exercises}


def sets_for_kind(kind: str, stored_sets: list[dict], default_rest: int = 60) -> list[dict]:
    out = []
    for s in stored_sets:
        rest = s["rest"] if s["rest"] is not None else default_rest
        if kind == "reps":
            out.append({"reps": s["count"], "weight": s["weight"] or 0.0, "side": s["side"], "rest": rest})
        else:
            out.append({"seconds": s["count"], "level": (s["level"] or None) if kind == "level" else None,
                        "side": s["side"], "rest": rest})
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
        if _csv(s["breakTime2"]) != _csv(g.get("breakTime2")):
            problems.append(f"{label}: rest sent {s['breakTime2']} but stored {g.get('breakTime2')}")
        if s.get("counterweight2") and _csv(s["counterweight2"]) != _csv(g.get("counterweight2")):
            problems.append(f"{label}: counterweight sent {s['counterweight2']} but stored {g.get('counterweight2')}")
        if g.get("templatePresetId") is not None and s["templatePresetId"] != g.get("templatePresetId"):
            problems.append(f"{label}: preset sent {s['templatePresetId']} but stored {g.get('templatePresetId')}")
    return problems
