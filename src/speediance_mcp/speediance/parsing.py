"""Pure parsing of Speediance session payloads. No I/O.

Rules (spec §8): weights stay in the account's display unit; on dual-cable movements `weights`
is derived force telemetry, so the side arrays win; telemetry arrays are ragged, so each series
is read on its own; `maxHeartRate: 0` means "no watch".
"""

from __future__ import annotations

import math
from typing import Any

from .routes import FREE_ROUTE


def _num(value) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def nums(value) -> list[float]:
    """A telemetry series as floats; accepts a list or a CSV string. Non-numbers are dropped."""
    if value is None:
        return []
    parts = value.split(",") if isinstance(value, str) else value
    out = []
    for part in parts:
        number = _num(part)
        if number is not None:
            out.append(number)
    return out


def kind_of(item: dict) -> str:
    """'reps', 'timed', or 'level' (Vita) — decides what a set's numbers mean."""
    method = item.get("completionMethod")
    if method == 5 or item.get("dataStatType") == 6:
        return "level"
    if method in (0, 2):
        return "timed"
    return "reps"


def _side(value) -> int | None:
    try:
        side = int(value)
    except (TypeError, ValueError):
        return None
    return side if side in (1, 2) else None


def _heart(value) -> float | None:
    number = _num(value)
    return number if number and number > 0 else None


def set_load(detail: dict, side: int | None) -> float | None:
    """The resistance of one set: side arrays first, `weights` only as a fallback.

    A pinned side (1/2) is a unilateral set: that side's array max, alone. Otherwise, when both
    cables are populated, both carry the load, so the set's resistance is max(left) + max(right) —
    never zip the two arrays by index, they're independent ragged telemetry series.
    """
    left = nums(detail.get("leftWeights"))
    right = nums(detail.get("rightWeights"))
    if side == 1 and left:
        return max(left)
    if side == 2 and right:
        return max(right)
    if left and right:
        return max(left) + max(right)
    if left or right:
        return max(left or right)
    weights = nums(detail.get("weights"))
    return max(weights) if weights else None


def _list_set(kind: str, raw: dict, index: int) -> dict:
    detail = raw.get("trainingInfoDetail") or {}
    done = int(_num(raw.get("finishedCount")) or 0)
    target = int(_num(raw.get("targetCount")) or 0)
    seconds = int(_num(raw.get("time")) or 0)
    side = _side(raw.get("leftRight", detail.get("leftRight")))
    entry = {
        "setIndex": index,
        "reps": done if kind != "timed" else None,
        "targetReps": target if kind == "reps" else None,
        "seconds": seconds,
        "weight": None,
        "level": None,
        "side": side,
        "maxHeartRate": _heart(raw.get("maxHeartRate")),
        "skipped": (done == 0) if kind == "reps" else (seconds == 0),
    }
    if kind == "reps":
        load = set_load(detail, side)
        entry["weight"] = round(load, 1) if load is not None else None
    if kind == "level":
        levels = nums(raw.get("level")) or nums(detail.get("level"))
        entry["level"] = int(max(levels)) if levels else None
    for source, target_key in (("distance", "distance"), ("pace", "pace"), ("spm", "strokeRate")):
        value = _num(raw.get(source))
        if value:
            entry[target_key] = value
    return entry


# Speediance's own personal-best flags, one 0/1 per exercise on the custom-template route
# (cttTrainingInfoDetail), verified live. Never seen on freeTraining or freeTrainingDetail
# payloads, and never per set. Course and AI routes are unverified.
PB_FLAGS = (("maxWeightPr", "weight"), ("totalCapacityPr", "volume"), ("oneRepMaxPr", "1RM"))


def personal_bests(raw: dict) -> list[str]:
    """The kinds of personal best Speediance flagged on one exercise, [] when none or absent."""
    return [kind for key, kind in PB_FLAGS if _num(raw.get(key))]


def _exercise(name, group_id, kind: str, entries: list[dict], pbs: list[str] | None = None) -> dict:
    worked = [e for e in entries if not e["skipped"]]
    for number, entry in enumerate(worked, 1):
        entry["setIndex"] = number
        entry.pop("skipped")
    weights = [e["weight"] for e in worked if e["weight"] is not None]
    volume = sum((e["reps"] or 0) * (e["weight"] or 0) for e in worked) if kind == "reps" else 0.0
    return {
        "name": name or "Exercise (not picked in app)",
        "groupId": group_id,
        "kind": kind,
        "sets": len(worked),
        "skippedSets": len(entries) - len(worked),
        "reps": [e["reps"] for e in worked],
        "weights": weights,
        "setLog": worked,
        "topWeight": max(weights) if weights else None,
        "volume": round(volume, 1),
        "avgLoad": round(sum(weights) / len(weights), 1) if weights else None,
        "avgLoadEstimated": False,
        "personalBests": pbs or [],
    }


def parse_list_exercises(payload) -> list[dict]:
    """Custom-template, course and AI routes: a list of exercises with `finishedReps`."""
    out = []
    for raw in payload or []:
        if not isinstance(raw, dict):
            continue
        kind = kind_of(raw)
        entries = [_list_set(kind, s, i) for i, s in enumerate(raw.get("finishedReps") or [], 1)
                   if isinstance(s, dict)]
        out.append(_exercise(raw.get("actionLibraryName"), raw.get("actionLibraryGroupId"), kind, entries,
                              personal_bests(raw)))
    return out


# --- Free Lift -----------------------------------------------------------------

SCALE_TOLERANCE = 0.01
KG_LB_SCALE = 2.2


def free_scale(payload: dict) -> tuple[float, str | None]:
    """Free Lift set figures are x2.2 on kg accounts and unscaled on lb accounts.

    Reconcile the sets' capacity sum against the session's own total instead of guessing.
    """
    total = _num(payload.get("totalCapacity")) or 0.0
    raw = sum(_num((st.get("summary") or {}).get("totalCapacity")) or 0.0
              for action in payload.get("actionList") or [] for st in action.get("setList") or [])
    if total <= 0 or raw <= 0:
        return 1.0, None
    if abs(raw - total) <= SCALE_TOLERANCE * total:
        return 1.0, None
    if abs(raw / KG_LB_SCALE - total) <= SCALE_TOLERANCE * total:
        return KG_LB_SCALE, None
    return 1.0, (f"Free Lift loads didn't reconcile with the session total (sets sum to {raw:.1f}, "
                 f"session reports {total:.1f}); showing the raw values.")


def parse_free_exercises(payload) -> tuple[list[dict], list[str]]:
    """`freeTraining`: one object with actionList[].setList[] (summary + rawRepList)."""
    if not isinstance(payload, dict):
        return [], []
    scale, warning = free_scale(payload)
    out = []
    for action in payload.get("actionList") or []:
        kind = kind_of(action)
        entries = []
        for index, st in enumerate(action.get("setList") or [], 1):
            summary = st.get("summary") or {}
            done = int(_num(summary.get("finishedCount")) or 0)
            seconds = int(_num(summary.get("time")) or 0)
            weight = _num(summary.get("weight")) if kind == "reps" else None
            entries.append({
                "setIndex": index,
                "reps": done if kind != "timed" else None,
                "targetReps": None,
                "seconds": seconds,
                "weight": round(weight / scale, 1) if weight is not None else None,
                "level": None,
                "side": _side(summary.get("leftRight")),
                "maxHeartRate": _heart(summary.get("maxHeartRate")),
                "skipped": (done == 0) if kind == "reps" else (seconds == 0),
            })
        out.append(_exercise(action.get("actionLibraryName"), action.get("groupId"), kind, entries))
    return out, ([warning] if warning else [])


def normalize_session(route: str, payload) -> dict:
    if route == FREE_ROUTE:
        exercises, warnings = parse_free_exercises(payload)
    else:
        exercises, warnings = parse_list_exercises(payload if isinstance(payload, list) else []), []
    return {"exercises": exercises, "warnings": warnings}


def session_uuid(payload) -> str | None:
    """The session's recording id, whatever it is used for.

    Unlike `find_uuid`, this does NOT consult `showHeartGraph`. A rowing session
    routinely has no watch recording (`showHeartGraph` 0) while still carrying the
    uuid that the rowing-telemetry endpoint is keyed on.
    """
    if isinstance(payload, dict):
        return payload.get("uuid") or None
    for raw in payload or []:
        if not isinstance(raw, dict):
            continue
        for st in raw.get("finishedReps") or []:
            uuid = (st.get("trainingInfoDetail") or {}).get("uuid") if isinstance(st, dict) else None
            if uuid:
                return uuid
    return None


def find_uuid(route: str, payload) -> str | None:
    """The watch recording id used by the heart-rate graph endpoint, if any."""
    if isinstance(payload, dict):
        if payload.get("showHeartGraph") in (0, False):
            return None
        return payload.get("uuid") or None
    for raw in payload or []:
        if not isinstance(raw, dict):
            continue
        for st in raw.get("finishedReps") or []:
            uuid = (st.get("trainingInfoDetail") or {}).get("uuid") if isinstance(st, dict) else None
            if uuid:
                return uuid
    return None


# --- cardio --------------------------------------------------------------------

def _round(value: float, digits: int) -> float:
    """Round half up."""
    factor = 10 ** digits
    return math.floor(value * factor + 0.5) / factor


def is_cardio(data: dict) -> bool:
    return ((_num(data.get("totalDistance")) or 0) > 0
            or data.get("existBoatingSkiDataGraph") is True
            or data.get("courseType") == 2)


def derive_cardio_stats(summary: dict) -> dict:
    """Session totals -> rowing/ski metrics. Missing or zero inputs give None, never NaN."""
    duration = _num(summary.get("trainingTime"))
    distance = _num(summary.get("totalDistance"))
    calories = _num(summary.get("calorie"))
    energy = _num(summary.get("totalEnergy"))
    has_duration = duration is not None and duration > 0
    has_distance = distance is not None and distance > 0
    return {
        "durationSec": int(duration) if duration is not None else None,
        "distanceM": _round(distance, 2) if distance is not None else None,
        "pace500": _round(duration / (distance / 500.0), 1) if has_duration and has_distance else None,
        "speedMs": _round(distance / duration, 2) if has_duration and has_distance else None,
        "calorie": calories,
        "calPerMin": _round(calories / (duration / 60.0), 1) if has_duration and calories is not None else None,
        "energyKJ": _round(energy / 1000.0, 1) if energy is not None else None,
        "avgWatts": int(_round(energy / duration, 0)) if has_duration and energy and energy > 0 else None,
        "completion": _num(summary.get("completionRate")),
        "rpe": _num(summary.get("rpe")),
    }


def intervals_from(payload) -> list[dict]:
    """Per-interval rows for guided cardio (the `freeTrainingDetail` route)."""
    out = []
    for raw in payload or []:
        if not isinstance(raw, dict):
            continue
        for st in raw.get("finishedReps") or []:
            seconds = _num(st.get("time")) or 0
            distance = _num(st.get("distance")) or 0
            if seconds <= 0:
                continue
            out.append({
                "interval": len(out) + 1,
                "seconds": int(seconds),
                "distanceM": _round(distance, 1) if distance else None,
                "pace500": _round(seconds / (distance / 500.0), 1) if distance > 0 else None,
                "strokeRate": _num(st.get("spm")) or None,
                "maxHeartRate": _heart(st.get("maxHeartRate")),
            })
    return out


# --- rowing / ski telemetry ------------------------------------------------------

# Fields of one `pointDataList` sample from `app/boatingSkiDataGraph/{uuid}`.
_ROW_BAND = ("minSpm", "maxSpm", "minResistance", "maxResistance")


def _rowing_points(data) -> list[dict]:
    """Normalise `pointDataList` into time-ordered samples, dropping unusable ones."""
    raw = data.get("pointDataList") if isinstance(data, dict) else data
    points = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        seconds = _num(item.get("time"))
        if seconds is None:
            continue
        points.append({
            "time": int(seconds),
            "strokeRate": _num(item.get("spm")) or 0,
            "pace500": _num(item.get("pace")),
            "watts": _num(item.get("power")),
            "resistance": _num(item.get("resistance")),
            "band": tuple(_num(item.get(key)) for key in _ROW_BAND),
        })
    points.sort(key=lambda p: p["time"])
    return points


def _sample_seconds(points: list[dict]) -> int:
    """The gap between samples, as the smallest positive step seen (they are evenly spaced)."""
    gaps = [b["time"] - a["time"] for a, b in zip(points, points[1:]) if b["time"] > a["time"]]
    return int(min(gaps)) if gaps else 0


def _rowing_stats(points: list[dict], sample_seconds: int) -> dict:
    """Averages over STROKING samples only.

    A sample with `strokeRate` 0 is the athlete not pulling — the flywheel spin-up at
    the start, a pause, or the cool-down. Its `pace` reads slow (the wheel coasting)
    and would drag every average toward a number never actually rowed, so those
    samples are counted as rest and excluded from the rates below.
    """
    working = [p for p in points if p["strokeRate"] > 0]
    rates = [p["strokeRate"] for p in working]
    paces = [p["pace500"] for p in working if p["pace500"] and p["pace500"] > 0]
    watts = [p["watts"] for p in working if p["watts"] is not None]
    return {
        "workingSec": len(working) * sample_seconds,
        "restingSec": (len(points) - len(working)) * sample_seconds,
        "avgStrokeRate": _round(sum(rates) / len(rates), 1) if rates else None,
        "maxStrokeRate": _round(max(rates), 1) if rates else None,
        # Lower pace is faster, so the best pace is the minimum.
        "avgPace500": _round(sum(paces) / len(paces), 1) if paces else None,
        "bestPace500": _round(min(paces), 1) if paces else None,
        "avgWatts": int(_round(sum(watts) / len(watts), 0)) if watts else None,
        "maxWatts": int(_round(max(watts), 0)) if watts else None,
    }


def rowing_telemetry(data) -> dict:
    """`app/boatingSkiDataGraph/{uuid}` -> per-block rowing/ski detail.

    The samples carry the workout's programmed target band (`minSpm`/`maxSpm` and
    the resistance pair) at each moment, so a change in that band marks the
    boundary between one programmed piece and the next. Blocks are cut there, and
    each reports how much of its stroking time was actually inside the band.
    """
    points = _rowing_points(data)
    if not points:
        return {"available": False,
                "reason": "No rowing telemetry for this session (the machine recorded no samples)."}
    sample_seconds = _sample_seconds(points)
    blocks: list[dict] = []
    for point in points:
        if not blocks or blocks[-1]["band"] != point["band"]:
            blocks.append({"band": point["band"], "points": []})
        blocks[-1]["points"].append(point)

    out_blocks = []
    for number, block in enumerate(blocks, 1):
        members = block["points"]
        min_spm, max_spm, min_res, max_res = block["band"]
        stats = _rowing_stats(members, sample_seconds)
        # No band means no target to be inside of — distinct from being outside one,
        # so this stays None rather than collapsing to 0%.
        has_band = min_spm is not None and max_spm is not None
        in_band = [p for p in members
                   if p["strokeRate"] > 0 and has_band and min_spm <= p["strokeRate"] <= max_spm]
        working_samples = len([p for p in members if p["strokeRate"] > 0]) if has_band else 0
        out_blocks.append({
            "block": number,
            "startSec": members[0]["time"],
            "endSec": members[-1]["time"] + sample_seconds,
            "seconds": len(members) * sample_seconds,
            "targetStrokeRate": f"{int(min_spm)}-{int(max_spm)}" if None not in (min_spm, max_spm) else None,
            "targetResistance": f"{int(min_res)}-{int(max_res)}" if None not in (min_res, max_res) else None,
            "inTargetPercent": _round(100.0 * len(in_band) / working_samples, 1) if working_samples else None,
            **stats,
        })

    overall = _rowing_stats(points, sample_seconds)
    return {"available": True, "samples": len(points), "sampleSeconds": sample_seconds,
            "durationSec": points[-1]["time"] + sample_seconds, **overall, "blocks": out_blocks}


# --- heart rate and strength math ------------------------------------------------

_HR_KEYS = ("heart", "hr", "bpm", "value", "rate")


def heart_rate_values(data: Any) -> list[float]:
    """Pull positive heart-rate samples out of the graph payload, whatever its exact shape."""
    items = data
    if isinstance(data, dict):
        items = next((v for v in data.values() if isinstance(v, list)), [])
    out = []
    for item in items or []:
        value = None
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            value = float(item)
        elif isinstance(item, dict):
            for key, raw in item.items():
                if isinstance(raw, (int, float)) and not isinstance(raw, bool) \
                        and any(k in key.lower() for k in _HR_KEYS):
                    value = float(raw)
                    break
        if value is not None and value > 0:
            out.append(value)
    return out


def downsample(values: list, limit: int) -> list:
    if len(values) <= limit:
        return list(values)
    step = math.ceil(len(values) / limit)
    return list(values[::step])


def epley(weight, reps) -> float:
    """Estimated one-rep max: weight x (1 + reps/30)."""
    w = _num(weight) or 0.0
    r = int(reps or 0)
    if w <= 0 or r <= 0:
        return 0.0
    return w if r == 1 else w * (1 + r / 30.0)


def round_half(value: float) -> float:
    """Round to the nearest 0.5 (the machine's load increment)."""
    return math.floor(value * 2 + 0.5) / 2
