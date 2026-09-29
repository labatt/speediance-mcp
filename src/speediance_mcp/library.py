"""The exercise catalog: summarize raw library items, filter them, resolve names. Pure."""

from __future__ import annotations

from .speediance.parsing import kind_of


def accessory_names(catalog: list[dict]) -> dict[int, str]:
    """accessory id -> name. Exercises reference the small ids (1 Flat Bench, 4 Barbell, 5 Handles...)."""
    out = {}
    for item in catalog or []:
        try:
            out[int(item["id"])] = str(item.get("name", "")).strip()
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _ids(csv) -> list[int]:
    return [int(x) for x in str(csv or "").split(",") if x.strip().isdigit()]


def summarize_exercise(raw: dict, names: dict[int, str] | None = None) -> dict:
    names = names or {}
    main = raw.get("mainMuscleGroupList") or []
    aux = raw.get("auxiliaryMuscleGroupList") or []
    equipment_ids = _ids(raw.get("accessories"))
    return {
        "groupId": raw.get("id"),
        "name": raw.get("title") or "",
        "category": raw.get("tabName") or "",
        "bodyParts": sorted({m.get("categoryName") for m in main if m.get("categoryName")}),
        "muscles": [m.get("muscleGroupName") for m in main if m.get("muscleGroupName")],
        "secondaryMuscles": [m.get("muscleGroupName") for m in aux if m.get("muscleGroupName")],
        "equipmentIds": equipment_ids,
        "equipment": [names.get(i, f"accessory {i}") for i in equipment_ids],
        "unilateral": raw.get("isLeftRight") == 1,
        "kind": kind_of(raw),
        "recommendedWeightKg": raw.get("recommendedWeight"),  # the library's recommendation is in kg
    }


def variant_id(raw: dict) -> int | None:
    variants = raw.get("actionLibraryList") or []
    return variants[0].get("id") if variants else None


def _norm(text) -> str:
    return " ".join(str(text or "").lower().split())


def resolve_exercise(items: list[dict], name: str) -> tuple[dict | None, list[dict]]:
    """Exact name, then a unique prefix, then a unique all-words match. Ambiguity returns candidates."""
    query = _norm(name)
    if not query:
        return None, []
    exact = [i for i in items if _norm(i["name"]) == query]
    if exact:
        return exact[0], []
    words = query.split()
    for group in ([i for i in items if _norm(i["name"]).startswith(query)],
                  [i for i in items if all(w in _norm(i["name"]) for w in words)]):
        if len(group) == 1:
            return group[0], []
        if group:
            return None, group[:10]
    return None, []


def filter_exercises(items: list[dict], *, query: str = "", muscle: str = "", category: str = "",
                     equipment: str = "", kind: str = "", owned: list[str] | None = None, owned_only: bool = False,
                     marks: dict | None = None, include_avoided: bool = False, limit: int = 60,
                     unusable: list[str] | None = None) -> list[dict]:
    marks = marks or {}
    owned_set = {_norm(o) for o in (owned or [])}
    # Equipment owned but unusable is worse than unowned: the move looks available.
    unusable_set = {_norm(u) for u in (unusable or [])}
    words = _norm(query).split()
    out = []
    for item in items:
        mark = (marks.get(item["groupId"]) or {}).get("mark")
        if mark == "avoided" and not include_avoided:
            continue
        if words and not all(w in _norm(item["name"]) for w in words):
            continue
        if muscle and not any(_norm(muscle) in _norm(x) for x in item["bodyParts"] + item["muscles"]):
            continue
        if kind and _norm(kind) != item["kind"]:
            continue
        if category and _norm(category) not in _norm(item["category"]):
            continue
        if equipment and not any(_norm(equipment) in _norm(e) for e in item["equipment"]):
            continue
        if owned_only and not all(_norm(e) in owned_set for e in item["equipment"]):
            continue
        if unusable_set and any(_norm(e) in unusable_set for e in item["equipment"]):
            continue
        out.append({**item, "mark": mark})
    out.sort(key=lambda i: (i["mark"] != "preferred", i["name"].lower()))
    return out[:limit]
