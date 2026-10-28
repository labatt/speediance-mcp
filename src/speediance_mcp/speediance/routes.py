"""Session type -> detail route.

The history feed and the calendar number session types differently (history: 1, 2, 5, 7, 9;
calendar: 3, 4, 6 for the same categories; official courses show as 1 or 2 on the calendar). This map is the union and must only be applied to a
type read from the feed it came from. Source: pookey/speediance-cli docs (MIT), verified live.
"""

from __future__ import annotations

FREE_ROUTE = "freeTraining"
FREE_INTERVALS_ROUTE = "freeTrainingDetail"

# Type 10 is a workout logged off the machine through the app's own manual entry.
# Verified live 2026-09-29: the record carries title, sport type, duration, calories,
# heart rate and distance, and NO exercises — Speediance's manual record has no field for
# them. It is a real session (not a phone-health import), so it counts towards days
# trained and streaks, but contributes no volume and can set no personal best.
MANUAL_TYPE = 10

DETAIL_ROUTES = {
    1: FREE_ROUTE, 6: FREE_ROUTE, 7: FREE_ROUTE,          # Free Lift, guided/quick cardio
    2: "courseTrainingInfoDetail",                        # course / program
    3: "cttTrainingInfoDetail", 5: "cttTrainingInfoDetail",  # custom template
    4: "aiCourseTrainingInfoDetail", 9: "aiCourseTrainingInfoDetail",  # AI / Goal-Focused
}
ALL_DETAIL_ROUTES = ("cttTrainingInfoDetail", "courseTrainingInfoDetail", FREE_ROUTE,
                     "aiCourseTrainingInfoDetail")
SUMMARY_ROUTES = {"cttTrainingInfoDetail": "cttTrainingInfo",
                  "courseTrainingInfoDetail": "courseTrainingInfo"}


def routes_to_try(session_type) -> tuple[str, ...]:
    try:
        route = DETAIL_ROUTES.get(int(session_type))
    except (TypeError, ValueError):
        route = None
    return (route,) if route else ALL_DETAIL_ROUTES


def detail_path(route: str, training_id) -> str:
    return f"/api/app/trainingInfo/{route}/{int(training_id)}"


# --- planned (booked, not yet trained) courses ---------------------------------------------
# A booked course has no trainingId yet, so the routes above can't reach it. Its prescription
# is keyed by the calendar entry's `courseId` instead. Both routes take ?weightConfig=1, as the
# app sends it.
#
# Verified live 2026-09-30 against a booked official course ("All Around the Chest", calendar
# type 1, courseId 2577): `v2/course/info` returns the full prescription. With and without
# weightConfig=1 the payloads were identical on this account.
#
# NOT verified: the AI route. It is as designed in pookey/speediance-cli. The account's AI
# subscription has lapsed, and every AI courseId from its history (calendar type 4) answers
# code 100026 on both routes. So does a made-up id on the course route, and an official course
# id on the AI route. 100026 ("Resource has been removed.") is therefore this API's "no such
# course on this route", not a verdict on the course. Take it as "try the other route".
COURSE_PLAN_ROUTE = "/api/app/v2/course/info/{cid}"
AI_PLAN_ROUTE = "/api/app/aiCourse/info/{cid}"  # no version prefix, unlike the course route
PLAN_MISSING_CODE = 100026
# Calendar type of an AI / Goal-Focused entry. Official courses showed as calendar type 1 or 2,
# so every other type starts on the course route.
AI_CALENDAR_TYPE = 4


def plan_routes(calendar_type) -> tuple[str, str]:
    """Both plan routes, the likelier one for this calendar `type` first."""
    try:
        is_ai = int(calendar_type) == AI_CALENDAR_TYPE
    except (TypeError, ValueError):
        is_ai = False
    return (AI_PLAN_ROUTE, COURSE_PLAN_ROUTE) if is_ai else (COURSE_PLAN_ROUTE, AI_PLAN_ROUTE)
