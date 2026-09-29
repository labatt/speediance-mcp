from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from ._common import parse_date
from .workouts import _row_by_code


def _reserve(app, date: str, code: str, status: int) -> dict:
    date = parse_date(date)
    row = _row_by_code(app, code)
    app.api.reserve(date, row["code"], status)
    return {"date": date, "code": row["code"], "name": row.get("name")}


def schedule_workout(app, date: str, code: str, add: bool = True) -> dict:
    """Put a saved template (by `code`, from list_my_workouts) on a day (YYYY-MM-DD), or take it off
    with add=false."""
    if not add:
        return unschedule_workout(app, date, code)
    return {"scheduled": True, **_reserve(app, date, code, 1)}


def unschedule_workout(app, date: str, code: str) -> dict:
    """Take a scheduled template off a day (YYYY-MM-DD)."""
    return {"unscheduled": True, **_reserve(app, date, code, 0)}


def _scalars(record: dict) -> dict:
    return {k: v for k, v in record.items() if not isinstance(v, (dict, list)) and v not in (None, "")}


def _names(items: list) -> list:
    return [i.get("name") or i.get("title") or i.get("id") for i in items[:50] if isinstance(i, dict)]


def browse_programs(app, query: str = "", program_id: int = 0) -> dict:
    """Speediance's official multi-week programs. Without program_id, lists programs (filtered by
    `query`); with program_id, returns that program's details and structure."""
    if program_id:
        program = app.api.program(program_id)
        if not program:
            raise ToolError(f"No program with id {program_id}.")
        return {"program": _scalars(program),
                "structure": {k: _names(v) for k, v in program.items() if isinstance(v, list)}}
    needle = str(query or "").lower().strip()
    rows = []
    for p in app.api.programs():
        name, description = str(p.get("name") or ""), str(p.get("description") or "")
        if needle and needle not in name.lower() and needle not in description.lower():
            continue
        rows.append({"id": p.get("id"), "name": name, "description": description[:240],
                     "weeks": p.get("weekCount"), "sessionsPerWeek": p.get("weekTrainingFrequency"),
                     "difficulty": p.get("difficultyId"), "available": p.get("isPermission")})
    return {"count": len(rows), "programs": rows[:50]}
