"""Booked courses and AI sessions: the plan routes, their parser, and calendar dispatch."""

from __future__ import annotations

import copy
import unittest

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp.speediance.api import PlanNotFound
from speediance_mcp.speediance.client import Rejected
from speediance_mcp.speediance.writes import read_course
from speediance_mcp.tools import sessions, workouts
from speediance_mcp.tools.sessions import open_with
from tests import fixtures as fx
from tests.helpers import api_error, make_app

COURSE_ID = 3100
AI_ID = 512000000000001
COURSE_PATH = f"/api/app/v2/course/info/{COURSE_ID}"
AI_PATH = f"/api/app/aiCourse/info/{AI_ID}"
AI_ON_COURSE_ROUTE = f"/api/app/v2/course/info/{AI_ID}"
COURSE_ON_AI_ROUTE = f"/api/app/aiCourse/info/{COURSE_ID}"
REMOVED = api_error(100026, "Resource has been removed.")

# Trimmed from a live v2/course/info payload (2026-09-30), with ids and loads altered. A course
# movement has no sort and no actionLibraryId, and its `id` belongs to the course.
COURSE = {"id": COURSE_ID, "courseTitle": "Chest Builder", "courseContext": "A 5x5 chest session.",
          "categoryName": "Strength Training", "difficultyId": 3, "durationMinute": 50, "isAICourse": False,
          "code": "c" * 24, "weightConfig": 1,
          "actionLibraryList": [
              {"id": 41, "groupId": 321, "title": "Barbell Bent Over Row", "countType": 2,
               "setsAndReps": "15,5,5", "weights": "30,45,45", "myRecommendedWeight2": "30,45,45",
               "recommendedWeight": 52, "counterweight2": "17,7,7", "breakTime2": "50,90,90",
               "level": "1,1,1", "leftRight": "0,0,0", "sportMode": "1,1,1", "restMode": "0,0,1",
               "selectCompletionMethod": "1,1,1"},
              {"id": 42, "groupId": 900, "title": "Chest Stretch", "countType": 2, "setsAndReps": "30,30",
               "weights": "0,0", "breakTime2": "0,5", "level": "1,1", "leftRight": "1,2",
               "sportMode": "1,1", "selectCompletionMethod": "1,1"},
              {"id": 43, "groupId": 555, "title": "Not In Library", "countType": 2, "setsAndReps": "10",
               "weights": "12", "breakTime2": "60", "level": "1", "leftRight": "0"},
          ]}
AI_PLAN = {**copy.deepcopy(COURSE), "id": AI_ID, "courseTitle": "Goal-Focused Workout", "isAICourse": True}


def plan_routes(overrides=None):
    routes = fx.standard_routes()
    routes.update({("GET", COURSE_PATH): COURSE, ("GET", COURSE_ON_AI_ROUTE): REMOVED,
                   ("GET", AI_PATH): AI_PLAN, ("GET", AI_ON_COURSE_ROUTE): REMOVED})
    routes.update(overrides or {})
    return routes


class TestReadCourse(unittest.TestCase):
    def test_movements_in_list_order_keyed_by_group(self):
        plan = read_course(COURSE)
        self.assertEqual(plan["courseId"], COURSE_ID)
        self.assertEqual(plan["name"], "Chest Builder")
        self.assertEqual([e["groupId"] for e in plan["exercises"]], [321, 900, 555])
        self.assertNotIn("actionLibraryId", plan["exercises"][0])

    def test_sets_share_the_template_columns(self):
        row = read_course(COURSE)["exercises"][0]
        self.assertEqual([(s["count"], s["weight"], s["rest"]) for s in row["sets"]],
                         [(15, 30.0, 50), (5, 45.0, 90), (5, 45.0, 90)])
        stretch = read_course(COURSE)["exercises"][1]
        self.assertEqual([s["side"] for s in stretch["sets"]], [1, 2])

    def test_empty_payload(self):
        self.assertEqual(read_course({})["exercises"], [])


class TestCoursePlanRoutes(unittest.TestCase):
    def test_course_type_reads_the_course_route(self):
        app, fake = make_app(self, plan_routes())
        route, data = app.api.course_plan(COURSE_ID, 1)
        self.assertEqual(data["courseTitle"], "Chest Builder")
        self.assertEqual(fake.calls("GET", COURSE_PATH)[0].url.params["weightConfig"], "1")
        self.assertEqual(fake.calls("GET", COURSE_ON_AI_ROUTE), [])

    def test_ai_type_reads_the_ai_route_first(self):
        app, fake = make_app(self, plan_routes())
        route, data = app.api.course_plan(AI_ID, 4)
        self.assertEqual(route, "/api/app/aiCourse/info/{cid}")
        self.assertEqual(fake.calls("GET", AI_ON_COURSE_ROUTE), [])

    def test_ai_id_on_the_course_route_falls_through_on_100026(self):
        # Called with a course's calendar type, an AI id first hits v2/course/info, which answers 100026.
        app, fake = make_app(self, plan_routes())
        route, data = app.api.course_plan(AI_ID, 2)
        self.assertEqual(len(fake.calls("GET", AI_ON_COURSE_ROUTE)), 1)
        self.assertEqual(route, "/api/app/aiCourse/info/{cid}")
        self.assertTrue(data["isAICourse"])

    def test_course_id_with_an_ai_type_falls_through_too(self):
        app, _ = make_app(self, plan_routes())
        route, data = app.api.course_plan(COURSE_ID, 4)
        self.assertEqual(route, "/api/app/v2/course/info/{cid}")

    def test_neither_route_serves_the_id(self):
        app, _ = make_app(self, plan_routes({("GET", COURSE_PATH): REMOVED}))
        with self.assertRaises(PlanNotFound):
            app.api.course_plan(COURSE_ID, 1)

    def test_other_rejections_are_not_swallowed(self):
        app, fake = make_app(self, plan_routes({("GET", COURSE_PATH): api_error(500123, "Busy")}))
        with self.assertRaises(Rejected):
            app.api.course_plan(COURSE_ID, 1)
        self.assertEqual(fake.calls("GET", COURSE_ON_AI_ROUTE), [])


class TestGetPlannedSession(unittest.TestCase):
    def test_course_reads_like_get_workout(self):
        app, _ = make_app(self, plan_routes())
        got = workouts.get_planned_session(app, COURSE_ID, 1)
        self.assertEqual(got["planType"], "course")
        self.assertEqual(got["displayUnit"], "lb")
        self.assertNotIn("note", got)
        row, stretch, unknown = got["exercises"]
        self.assertEqual(row["kind"], "reps")
        self.assertEqual(row["sets"][1], {"reps": 5, "weight": 45.0, "side": None, "rest": 90})
        self.assertEqual(stretch["kind"], "timed")
        self.assertEqual([(s["seconds"], s["side"]) for s in stretch["sets"]], [(30, 1), (30, 2)])
        # A movement the library doesn't know keeps its raw sets rather than a guessed kind.
        self.assertNotIn("kind", unknown)
        self.assertEqual(unknown["sets"][0]["count"], 10)

    def test_ai_plan_is_flagged_unverified(self):
        app, _ = make_app(self, plan_routes())
        got = workouts.get_planned_session(app, AI_ID, 4)
        self.assertEqual(got["planType"], "ai")
        self.assertIn("note", got)

    def test_unknown_id_is_a_tool_error(self):
        app, _ = make_app(self, plan_routes({("GET", COURSE_PATH): REMOVED}))
        with self.assertRaisesRegex(ToolError, "get_calendar"):
            workouts.get_planned_session(app, COURSE_ID, 1)

    def test_get_workout_points_a_course_code_at_get_planned_session(self):
        app, _ = make_app(self, plan_routes())
        with self.assertRaisesRegex(ToolError, "get_planned_session"):
            workouts.get_workout(app, "c" * 24)


class TestOpenWith(unittest.TestCase):
    # Shapes measured live on 2026-09-30; ids altered.
    TEMPLATE = {"type": 3, "title": "Pull Day", "isFinish": 0, "isReservation": True, "templateId": 9001,
                "templateReservationId": 70001, "code": "a" * 24}
    COURSE = {"type": 1, "courseId": COURSE_ID, "title": "Chest Builder", "isFinish": 0, "isReservation": True,
              "courseReservationId": 200001, "code": "c" * 24}

    def test_template_entry_opens_with_get_workout(self):
        self.assertEqual(open_with(self.TEMPLATE), {"tool": "get_workout", "code": "a" * 24})

    def test_course_entry_opens_with_get_planned_session_despite_its_code(self):
        self.assertEqual(open_with(self.COURSE),
                         {"tool": "get_planned_session", "course_id": COURSE_ID, "type": 1})

    def test_ai_entry_keeps_its_type(self):
        entry = {**self.COURSE, "type": 4, "courseId": AI_ID}
        self.assertEqual(open_with(entry)["type"], 4)

    def test_entry_with_neither_opens_with_nothing(self):
        self.assertIsNone(open_with({"type": 3, "title": "Mystery", "isFinish": 0, "code": "a" * 24}))
        self.assertIsNone(open_with({"type": 6, "title": "Something", "isFinish": 0}))

    def test_completed_entry_opens_its_session(self):
        done = {**self.COURSE, "isFinish": 1, "trainingId": 7001}
        self.assertEqual(open_with(done), {"tool": "get_session_detail", "training_id": 7001})

    def test_get_calendar_annotates_entries(self):
        calendar = copy.deepcopy(fx.CALENDAR_2026_08)
        calendar[0]["trainingPlanList"].append(copy.deepcopy(self.COURSE))
        routes = plan_routes({("GET", "/api/app/v5/trainingCalendar/monthNew"): calendar})
        app, _ = make_app(self, routes)
        days = {d["date"]: d for d in sessions.get_calendar(app, "2026-08")["days"]}
        planned = days["2026-08-29"]["trainingPlanList"][0]
        self.assertEqual(planned["openWith"]["tool"], "get_planned_session")
        template = days["2026-08-30"]["trainingPlanList"][0]
        self.assertEqual(template["openWith"], {"tool": "get_workout", "code": "a" * 24})


if __name__ == "__main__":
    unittest.main()
