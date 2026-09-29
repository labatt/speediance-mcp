"""Session type -> detail route.

The history feed and the calendar number session types differently (history: 1, 2, 5, 7, 9;
calendar: 3, 4, 6 for the same categories). This map is the union and must only be applied to a
type read from the feed it came from. Source: pookey/speediance-cli docs (MIT), verified live.
"""

from __future__ import annotations

FREE_ROUTE = "freeTraining"
FREE_INTERVALS_ROUTE = "freeTrainingDetail"

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
