"""Recovery and health: the readiness, sleep and Wellness Monitor data behind the app's Health tab.

Pure functions over the raw payloads from `SpeedianceAPI` — no requests here. Field meanings
were checked against a live account on 2026-09-30:

* physicalCondition: `trainingStatus` is exactly `fatigue / fitness` (81.92 / 96.86 = 0.85), so
  fitness and fatigue are the long- and short-window training loads and the ratio is an
  acute:chronic workload ratio. The band attached to it here is the conventional sports-science
  ACWR band, not a label Speediance shows.
* sleep `dtos[].sleepType`: 1 is the night's total (it equals the sum of 3 + 4 + 5, so it counts
  sleep only, not time awake), 2 awake, 3 REM, 4 core, 5 deep. The split was matched against
  `sleepStandard`, whose ranges are the app's healthy share of each stage. 6 and 7 have always
  read 0 and are left out.
* Wellness Monitor `stress` is heart-rate variability in ms — the app's own label for the tile is
  HRV. Each indicator has two readings with different values and baselines: full-day
  (`monitor/detail`, with the user's own {low, high} band) and overnight (`monitor/index`, the
  7-night trend the phone shows). Never mix the two.
* Muscle `fatigue` is 1 (fresh) to 3 (heavily loaded) and `intensityLevel` how hard the muscle
  was worked. The per-body-part `muscleLoadStatus` code is passed through undecoded: its values
  don't track fatigue consistently enough to name them.
"""

from __future__ import annotations

import datetime as dt
import json

from .muscles import BODY_PARTS

SLEEP_STAGES = {2: "awake", 3: "rem", 4: "core", 5: "deep"}
SLEEP_TOTAL = 1
STANDARD_KEYS = {"awake": "awakeList", "rem": "remList", "core": "coreList", "deep": "deepList"}

WELLNESS = (  # (Speediance key, label, unit)
    ("stress", "HRV", "ms"),
    ("restingHeartRate", "Resting heart rate", "bpm"),
    ("bloodOxygen", "Blood oxygen", "%"),
    ("respiratoryRate", "Respiratory rate", "breaths/min"),
    ("skinTemperature", "Skin temperature", "°C"),
)


def _ext(raw) -> dict:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _when(timestamp) -> str | None:
    """Epoch seconds -> local ISO time, the zone the user's phone reported the reading in."""
    try:
        return dt.datetime.fromtimestamp(int(timestamp)).isoformat(timespec="minutes")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _num(value, digits: int = 1):
    return round(float(value), digits) if isinstance(value, (int, float)) else None


def load_band(ratio) -> str | None:
    """The conventional acute:chronic workload bands."""
    if not isinstance(ratio, (int, float)):
        return None
    if ratio < 0.8:
        return "detraining"
    if ratio <= 1.3:
        return "optimal"
    if ratio <= 1.5:
        return "high"
    return "very high"


def training_status(payload: dict) -> dict | None:
    resp = (payload or {}).get("physicalConditionResp") or {}
    if not resp:
        return None
    params = payload.get("physicalConditionParam") or {}
    ratio = resp.get("trainingStatus")
    return {
        "date": resp.get("dateStr"),
        "conditionScore": _num(resp.get("physicalConditionScore")),
        "fitness": _num(resp.get("fitness")),
        "fatigue": _num(resp.get("fatigue")),
        "loadRatio": _num(ratio, 2),
        "loadBand": load_band(ratio),
        "trainingStatusScore": _num(params.get("trainingStatusScore")),
        "trainingStatusScoreAvg": _num(params.get("trainingStatusScoreAvg")),
        "sleepScore": _num(params.get("sleepScore")),
        "sleepScoreAvg": _num(params.get("sleepScoreAvg")),
    }


def sleep_night(payload: dict, standard: dict | None = None) -> dict | None:
    """One night. Stage shares are of time in bed (asleep + awake), which is what the
    app's healthy ranges in `sleepStandard` are expressed against."""
    payload = payload or {}
    asleep = payload.get("sleep")
    if not asleep:
        return None
    seconds = {SLEEP_STAGES[d["sleepType"]]: d.get("sleep") or 0
               for d in payload.get("dtos") or [] if d.get("sleepType") in SLEEP_STAGES}
    in_bed = asleep + seconds.get("awake", 0)
    stages = {}
    for stage in ("deep", "core", "rem", "awake"):
        if stage not in seconds:
            continue
        share = seconds[stage] / in_bed if in_bed else 0.0
        entry = {"minutes": round(seconds[stage] / 60), "share": round(share, 3)}
        bounds = (standard or {}).get(STANDARD_KEYS[stage])
        if isinstance(bounds, list) and len(bounds) == 2:
            entry["normalRange"] = bounds
            entry["inRange"] = bounds[0] <= share <= bounds[1]
        stages[stage] = entry
    return {
        "asleepMinutes": round(asleep / 60),
        "asleep": f"{int(asleep // 3600)}h{int(asleep % 3600 // 60):02d}m",
        "score": payload.get("sleepScore"),
        "start": _when(payload.get("startTimeStamp")),
        "end": _when(payload.get("endTimeStamp")),
        "stages": stages,
    }


def _scale(key: str, value):
    """Blood oxygen arrives as a fraction; show it as the percentage the app displays."""
    if key == "bloodOxygen" and isinstance(value, (int, float)) and value <= 1:
        return round(value * 100, 1)
    return _num(value, 2)


def _trend_value(key: str, point: dict):
    if "val" in point:
        return _scale(key, point["val"])
    low, high = _scale(key, point.get("minVal")), _scale(key, point.get("maxVal"))
    return {"min": low, "max": high} if low is not None or high is not None else None


def wellness(full_day: dict, overnight: dict) -> list[dict]:
    out = []
    for key, label, unit in WELLNESS:
        full = (full_day or {}).get(key) or {}
        night = (overnight or {}).get(key) or {}
        entry = {"key": key, "label": label, "unit": unit}
        if full.get("value") is not None:
            ext = _ext(full.get("extData"))
            low, high = _scale(key, ext.get("lowerBound")), _scale(key, ext.get("upperBound"))
            value = _scale(key, full["value"])
            reading = {"value": value, "low": low, "high": high,
                       "baselineReady": bool(ext.get("hasBasicData")), "at": _when(full.get("createTimestamp"))}
            if reading["baselineReady"] and low is not None and high is not None:
                reading["status"] = "below" if value < low else "above" if value > high else "normal"
            entry["fullDay"] = reading
        trend = [{"date": str(p.get("date", ""))[:10], "value": _trend_value(key, p)}
                 for p in night.get("trendList") or []]
        if trend:
            ext = _ext(night.get("extData"))
            entry["overnight"] = {"latest": trend[0]["value"], "date": trend[0]["date"],
                                  "baselineReady": bool(ext.get("hasBasicData")),
                                  "missingDays": ext.get("missingDays"), "trend": trend}
        if len(entry) > 3:
            out.append(entry)
    return out


def flags(indicators: list[dict]) -> list[str]:
    """One line per full-day reading outside the user's own baseline."""
    lines = []
    for entry in indicators:
        reading = entry.get("fullDay") or {}
        if reading.get("status") in ("below", "above"):
            lines.append(f"{entry['label']} {reading['value']} {entry['unit']} is {reading['status']} "
                         f"its usual {reading['low']}-{reading['high']}")
    return lines


def muscle_recovery(detail: list, parts: list) -> list[dict]:
    load_status = {p.get("trainingPartId2"): p.get("muscleLoadStatus") for p in parts or [] if isinstance(p, dict)}
    out = []
    for part in detail or []:
        if not isinstance(part, dict):
            continue
        part_id = part.get("trainingPartId2")
        muscles = []
        for m in part.get("muscleDetailList") or []:
            muscles.append({"muscle": m.get("muscleGroupName"), "fatigue": m.get("fatigue"),
                            "intensity": m.get("intensityLevel"), "trained": bool(m.get("isTrained"))})
        out.append({"bodyPart": BODY_PARTS.get(part_id, str(part_id)), "loadStatus": load_status.get(part_id),
                    "muscles": muscles})
    return out


def fatigued(recovery: list[dict]) -> list[str]:
    """Muscles still at the top fatigue level — the ones to go easy on."""
    return [m["muscle"] for part in recovery for m in part["muscles"] if (m.get("fatigue") or 0) >= 3]


BODY_KEYS = (  # (Speediance key, output name, unit)
    ("weight", "weight", None), ("bodyFatPercent", "bodyFatPercent", "%"), ("bmi", "bmi", None),
    ("weightWithOutFat", "leanMass", None), ("ffmi", "ffmi", None), ("vo2max", "vo2max", "ml/kg/min"),
)


def body_metrics(payload: dict) -> dict:
    """Latest body-composition readings. Weights are in the account's weight unit as the app
    stores it — but `weightWithOutFat`'s trend has been seen in a different unit from its
    latest value, so only latest values are reported."""
    out = {}
    for key, name, unit in BODY_KEYS:
        item = (payload or {}).get(key) or {}
        if item.get("value") is None:
            continue
        out[name] = {"value": _num(item["value"]), "at": _when(item.get("createTimestamp"))}
        if unit:
            out[name]["unit"] = unit
    return out


def scores(payload: dict) -> dict:
    payload = payload or {}
    out = {}
    for key, name in (("crfScore", "cardioFitness"), ("pfScore", "strengthScore")):
        if (payload.get(key) or {}).get("value") is not None:
            out[name] = _num(payload[key]["value"])
    body = payload.get("bodyAge") or {}
    if body.get("value") is not None:
        actual = _ext(body.get("extData")).get("age")
        out["bodyAge"] = {"value": _num(body["value"]), "actualAge": _num(actual),
                          "youngerBy": _num(actual - body["value"]) if isinstance(actual, (int, float)) else None}
    return out


def training_load_today(home: dict) -> dict | None:
    load = (home or {}).get("trainingLoadResp") or {}
    if not load:
        return None
    return {"unit": load.get("trainingLoadUnit"), "recommendMin": load.get("recommendMin"),
            "recommendMax": load.get("recommendMax")}
