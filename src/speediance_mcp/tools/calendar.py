from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from ._common import parse_date
from .workouts import _row_by_code


def _reserve(app, date: str, code: str, status: int) -> dict:
    date = parse_date(date)
    row = _row_by_code(app, code)
    app.api.reserve(date, row["code"], status)
    return {"date": date, "code": row["code"], "name": row.get("name")}


def _booked_courses(app, date: str) -> list[dict]:
    """Course bookings on one day. Verified live 2026-09-30: a booked course is a calendar entry with
    isReservation:true, courseReservationId, courseId and the course's `code` (and type 1, whereas a
    completed course is type 2 — so key on courseReservationId, not type)."""
    for day in app.api.calendar(date[:7]):
        if isinstance(day, dict) and day.get("date") == date:
            return [p for p in day.get("trainingPlanList") or []
                    if p.get("isReservation") and p.get("courseReservationId")]
    return []


def _course(app, handle) -> dict:
    """An official course by its `code` (preferred — its numeric id changes between course versions,
    the code doesn't) or its current id."""
    key = str(handle if handle is not None else "").strip()
    for row in app.api.courses():
        if row.get("code") == key or str(row.get("id")) == key:
            return row
    raise ToolError(f"No official course {key!r}. Find one with browse_programs (a query lists matching "
                    "courses; a program_id lists each day's courses).")


def _reserve_course(app, date: str, course_code: str, add: bool) -> dict:
    date = parse_date(date)
    if add:
        row = _course(app, course_code)
        code, name = row["code"], row.get("courseTitle")
    else:
        # A booked course may have left the catalogue since, so the calendar entry is enough to remove it.
        key = str(course_code).strip()
        booked = [p for p in _booked_courses(app, date) if key in (p.get("code"), str(p.get("courseId")))]
        if not booked:
            raise ToolError(f"No course {key!r} is booked on {date}. Check get_calendar for what's there.")
        code, name = booked[0]["code"], booked[0].get("title")
    if not app.api.reserve_course(date, code, 1 if add else 0):
        raise ToolError(f"Speediance didn't accept the course {'booking' if add else 'removal'} for {date}.")
    present = any(p.get("code") == code for p in _booked_courses(app, date))
    return {"date": date, "courseCode": code, "name": name, "verified": present == add}


def schedule_workout(app, date: str, code: str = "", add: bool = True, course_code: str = "") -> dict:
    """Put a saved template (by `code`, from list_my_workouts) on a day (YYYY-MM-DD), or take it off
    with add=false. To book one of Speediance's official courses instead, pass `course_code` (from
    browse_programs) and leave `code` empty. A course booking is read back from the calendar: check
    `verified`."""
    if bool(str(code or "").strip()) == bool(str(course_code or "").strip()):
        raise ToolError("Pass exactly one of `code` (your template) or `course_code` (an official course).")
    if not add:
        return unschedule_workout(app, date, code, course_code)
    if course_code:
        return {"scheduled": True, **_reserve_course(app, date, course_code, True)}
    return {"scheduled": True, **_reserve(app, date, code, 1)}


def unschedule_workout(app, date: str, code: str = "", course_code: str = "") -> dict:
    """Take a scheduled template (`code`) or official course (`course_code`, as get_calendar shows it
    on that day) off a day (YYYY-MM-DD)."""
    if bool(str(code or "").strip()) == bool(str(course_code or "").strip()):
        raise ToolError("Pass exactly one of `code` (your template) or `course_code` (an official course).")
    if course_code:
        return {"unscheduled": True, **_reserve_course(app, date, course_code, False)}
    return {"unscheduled": True, **_reserve(app, date, code, 0)}


def _scalars(record: dict) -> dict:
    return {k: v for k, v in record.items() if not isinstance(v, (dict, list)) and v not in (None, "")}


def _names(items: list) -> list:
    return [i.get("name") or i.get("title") or i.get("id") for i in items[:50] if isinstance(i, dict)]


def _course_row(c: dict) -> dict:
    return {"courseCode": c.get("code"), "title": c.get("courseTitle"), "minutes": c.get("durationMinute"),
            "difficulty": c.get("difficultyId")}


def _schedule(program: dict) -> list:
    """Week by week, day by day, the courses a program books — each with the courseCode to schedule it."""
    weeks = []
    for w, week in enumerate(program.get("exclusivePlanWeekList") or [], 1):
        days = [{"day": d.get("day"), "courses": [_course_row(c) for c in d.get("courseList") or []]}
                for d in week.get("exclusivePlanDayList") or [] if isinstance(d, dict)]
        weeks.append({"week": w, "days": days})
    return weeks


def browse_programs(app, query: str = "", program_id: int = 0) -> dict:
    """Speediance's official multi-week programs and single courses. Without program_id, lists programs
    (filtered by `query`) and, when `query` is given, the single courses whose title or category match;
    with program_id, returns that program's details and a week-by-week schedule of its courses. Book a
    course with schedule_workout(course_code=...)."""
    if program_id:
        program = app.api.program(program_id)
        if not program:
            raise ToolError(f"No program with id {program_id}.")
        return {"program": _scalars(program),
                "structure": {k: _names(v) for k, v in program.items() if isinstance(v, list)},
                "schedule": _schedule(program)}
    needle = str(query or "").lower().strip()
    rows = []
    for p in app.api.programs():
        name, description = str(p.get("name") or ""), str(p.get("description") or "")
        if needle and needle not in name.lower() and needle not in description.lower():
            continue
        rows.append({"id": p.get("id"), "name": name, "description": description[:240],
                     "weeks": p.get("weekCount"), "sessionsPerWeek": p.get("weekTrainingFrequency"),
                     "difficulty": p.get("difficultyId"), "available": p.get("isPermission")})
    out = {"count": len(rows), "programs": rows[:50]}
    if needle:
        courses = [c for c in app.api.courses()
                   if needle in str(c.get("courseTitle") or "").lower()
                   or any(needle in str(k.get("categoryName") or "").lower() for k in c.get("categoryList") or [])]
        out["courseCount"] = len(courses)
        out["courses"] = [{**_course_row(c), "categories": [k.get("categoryName") for k in c.get("categoryList") or []]}
                          for c in courses[:50]]
    return out
