"""Muscle attribution and balance.

Every movement in the Speediance library names the muscles it works: a main list and
an assisting list. This module spreads each exercise's volume over those muscles and
reports the resulting balance — which muscles carried the work, how push compares to
pull, upper to lower, and what hasn't been trained lately.

Two things are deliberate and must stay visible to whoever reads the numbers:

* A main muscle takes the exercise's FULL volume and an assisting muscle HALF. So the
  attributed total is larger than the volume actually lifted — a bench press counts
  fully towards the pecs and half towards the triceps. These are shares of attention,
  not a decomposition of the load.
* Only exercises measured in reps and weight carry volume. Timed and level-based work
  (Vita, planks, rowing) has no weight to multiply, so it contributes nothing here and
  is counted separately as `unweightedExercises`.
"""

from __future__ import annotations

MAIN_SHARE = 1.0
ASSIST_SHARE = 0.5

# The library's own body-part ids (`trainingPartId2`), named from the muscles it files
# under each. 18 is the catch-all the library uses for whole-body movements.
BODY_PARTS = {11: "Chest", 12: "Shoulders", 13: "Back", 14: "Glutes",
              15: "Legs", 16: "Arms", 17: "Core", 18: "Full body"}

# Movement-chain classification. "Full Body" belongs to neither chain: counting it as
# both would flatter every ratio, and picking one would be arbitrary.
PUSH_MUSCLES = {"pecs", "front delts", "side delts", "triceps"}
PULL_MUSCLES = {"lats", "traps", "rear delts", "biceps", "forearms", "back extensors"}
UPPER_MUSCLES = PUSH_MUSCLES | PULL_MUSCLES
LOWER_MUSCLES = {"glutes", "quads", "hamstrings", "calves", "adductors"}


def _key(name) -> str:
    return str(name or "").strip().lower()


def muscle_index(library: list[dict]) -> dict[int, dict]:
    """groupId -> {'main': [names], 'assist': [names], 'part': body-part name}.

    Built from the cached exercise catalog, so it costs no API calls.
    """
    index: dict[int, dict] = {}
    for raw in library or []:
        group_id = raw.get("id")
        if group_id is None:
            continue
        main = [m.get("muscleGroupName") for m in (raw.get("mainMuscleGroupList") or [])
                if m.get("muscleGroupName")]
        assist = [m.get("muscleGroupName") for m in (raw.get("auxiliaryMuscleGroupList") or [])
                  if m.get("muscleGroupName")]
        index[int(group_id)] = {
            "main": main,
            "assist": [a for a in assist if a not in main],
            "part": BODY_PARTS.get(raw.get("trainingPartId2")),
        }
    return index


def attribute(exercises: list[dict], index: dict[int, dict]) -> dict:
    """Spread each exercise's volume over the muscles it works.

    `exercises` are parsed session exercises: each needs `groupId` and `volume`.
    Returns per-muscle and per-body-part volume plus what could not be attributed.
    """
    by_muscle: dict[str, float] = {}
    by_part: dict[str, float] = {}
    unknown: list[str] = []
    unweighted = 0
    for exercise in exercises or []:
        volume = float(exercise.get("volume") or 0)
        entry = index.get(int(exercise["groupId"])) if exercise.get("groupId") is not None else None
        if entry is None:
            name = exercise.get("name")
            if name and name not in unknown:
                unknown.append(name)
            continue
        if volume <= 0:
            unweighted += 1
            continue
        for muscle in entry["main"]:
            by_muscle[muscle] = by_muscle.get(muscle, 0.0) + volume * MAIN_SHARE
        for muscle in entry["assist"]:
            by_muscle[muscle] = by_muscle.get(muscle, 0.0) + volume * ASSIST_SHARE
        if entry["part"]:
            by_part[entry["part"]] = by_part.get(entry["part"], 0.0) + volume
    return {"byMuscle": {k: round(v, 1) for k, v in by_muscle.items()},
            "byBodyPart": {k: round(v, 1) for k, v in by_part.items()},
            "unweightedExercises": unweighted,
            "exercisesNotInLibrary": unknown}


def _sum(by_muscle: dict[str, float], names: set[str]) -> float:
    return sum(v for k, v in by_muscle.items() if _key(k) in names)


def _ratio(left: float, right: float) -> float | None:
    """left:right as a single number, or None when there is nothing to compare."""
    if right <= 0:
        return None
    return round(left / right, 2)


def ratios(by_muscle: dict[str, float]) -> dict:
    """Push:pull and upper:lower, with the volumes they were computed from."""
    push, pull = _sum(by_muscle, PUSH_MUSCLES), _sum(by_muscle, PULL_MUSCLES)
    upper, lower = _sum(by_muscle, UPPER_MUSCLES), _sum(by_muscle, LOWER_MUSCLES)
    return {
        "push": round(push, 1), "pull": round(pull, 1), "pushPull": _ratio(push, pull),
        "upper": round(upper, 1), "lower": round(lower, 1), "upperLower": _ratio(upper, lower),
    }


def untrained(by_muscle: dict[str, float], index: dict[int, dict]) -> list[str]:
    """Muscles the library knows about that took no volume in the window.

    "Full Body" is excluded: it is a filing category, not a muscle someone can skip.
    """
    known: set[str] = set()
    for entry in index.values():
        known.update(entry["main"])
    trained = {_key(m) for m, v in by_muscle.items() if v > 0}
    return sorted(m for m in known if _key(m) not in trained and _key(m) != "full body")
