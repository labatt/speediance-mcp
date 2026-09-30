from __future__ import annotations

import json
import unittest

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp.tools import calendar, memory_tools
from tests import fixtures as fx
from tests.helpers import make_app


class TestCalendarTools(unittest.TestCase):
    def test_schedule_and_unschedule(self):
        app, fake = make_app(self)
        got = calendar.schedule_workout(app, "2026-09-01", "a" * 24)
        self.assertEqual((got["scheduled"], got["name"]), (True, "Pull Day"))
        calendar.unschedule_workout(app, "2026-09-01", "a" * 24)
        self.assertTrue(calendar.schedule_workout(app, "2026-09-02", "a" * 24, add=False)["unscheduled"])
        bodies = [json.loads(r.content) for r in fake.calls("POST", fx.RESERVE_PATH)]
        self.assertEqual([b["status"] for b in bodies], [1, 0, 0])
        self.assertEqual(bodies[0]["thatDay"], "2026-09-01")

    def test_validation_writes_nothing(self):
        app, fake = make_app(self)
        with self.assertRaises(ToolError):
            calendar.schedule_workout(app, "2026-02-30", "a" * 24)
        with self.assertRaises(ToolError):
            calendar.schedule_workout(app, "2026-09-01", "unknown")
        self.assertEqual(fake.calls("POST", fx.RESERVE_PATH), [])

    def test_programs(self):
        app, _ = make_app(self)
        self.assertEqual(calendar.browse_programs(app, query="strength")["count"], 1)
        self.assertEqual(calendar.browse_programs(app, query="yoga")["count"], 0)
        detail = calendar.browse_programs(app, program_id=77)
        self.assertEqual(detail["program"]["name"], "Strength Foundations")
        self.assertEqual(detail["structure"]["weekList"], ["Week 1", "Week 2"])
        day1, day2 = detail["schedule"][0]["days"]
        self.assertEqual(day1["courses"][0]["courseCode"], "c" * 24)
        self.assertEqual(day2["courses"], [])

    def test_programs_query_also_lists_courses(self):
        app, _ = make_app(self)
        self.assertNotIn("courses", calendar.browse_programs(app))
        got = calendar.browse_programs(app, query="strength")
        self.assertEqual([c["courseCode"] for c in got["courses"]], ["c" * 24])  # by category
        got = calendar.browse_programs(app, query="engine")
        self.assertEqual((got["count"], got["courses"][0]["title"]), (0, "Base & Engine #1"))  # by title


class TestCourseScheduling(unittest.TestCase):
    def make(self, **kw):
        return make_app(self, fx.course_booking_routes(fx.standard_routes(), **kw))

    def bodies(self, fake):
        return [json.loads(r.content) for r in fake.calls("POST", fx.COURSE_RESERVE_PATH)]

    def test_book_then_unbook(self):
        app, fake = self.make()
        got = calendar.schedule_workout(app, "2026-10-14", course_code="c" * 24)
        self.assertEqual(got, {"scheduled": True, "date": "2026-10-14", "courseCode": "c" * 24,
                               "name": "All Around the Chest", "verified": True})
        got = calendar.unschedule_workout(app, "2026-10-14", course_code="c" * 24)
        self.assertEqual((got["unscheduled"], got["verified"]), (True, True))
        self.assertEqual([(b["status"], b["thatDay"], b["courseCode"]) for b in self.bodies(fake)],
                         [(1, "2026-10-14", "c" * 24), (0, "2026-10-14", "c" * 24)])
        self.assertEqual(fake.calls("POST", fx.RESERVE_PATH), [])

    def test_add_false_and_course_id_resolve_to_the_code(self):
        app, fake = self.make()
        calendar.schedule_workout(app, "2026-10-14", course_code="2577")
        self.assertTrue(calendar.schedule_workout(app, "2026-10-14", course_code="c" * 24, add=False)["unscheduled"])
        self.assertEqual([b["courseCode"] for b in self.bodies(fake)], ["c" * 24, "c" * 24])

    def test_unverified_booking_is_reported(self):
        app, _ = self.make(sticks=False)
        self.assertFalse(calendar.schedule_workout(app, "2026-10-14", course_code="c" * 24)["verified"])

    def test_rejected_booking_is_an_error(self):
        app, _ = self.make(accept=False)
        with self.assertRaises(ToolError):
            calendar.schedule_workout(app, "2026-10-14", course_code="c" * 24)

    def test_validation_writes_nothing(self):
        app, fake = self.make()
        for kwargs in ({}, {"code": "a" * 24, "course_code": "c" * 24}, {"code": " ", "course_code": ""}):
            with self.assertRaises(ToolError):
                calendar.schedule_workout(app, "2026-10-14", **kwargs)
            with self.assertRaises(ToolError):
                calendar.unschedule_workout(app, "2026-10-14", **kwargs)
        with self.assertRaises(ToolError):
            calendar.schedule_workout(app, "2026-10-14", course_code="unknown")
        with self.assertRaises(ToolError):
            calendar.schedule_workout(app, "2026-02-30", course_code="c" * 24)
        with self.assertRaises(ToolError):  # nothing booked that day
            calendar.unschedule_workout(app, "2026-10-14", course_code="c" * 24)
        self.assertEqual(self.bodies(fake), [])
        self.assertEqual(fake.calls("POST", fx.RESERVE_PATH), [])


class TestMemoryTools(unittest.TestCase):
    def test_round_trip(self):
        app, _ = make_app(self)
        memory_tools.set_preferences(app, goal="Strength", owned_equipment=["Barbell"], load_anchors={"321": 45})
        saved = memory_tools.remember_fact(app, "Knee pain on lunges", kind="constraint", category="injury",
                                           severity="soft", expires_days=14)
        got = memory_tools.get_preferences(app)
        self.assertEqual((got["goal"], got["owned_equipment"], got["load_anchors"]), ("Strength", ["Barbell"], {"321": 45.0}))
        soft = got["facts"]["constraints"]["soft"]
        self.assertEqual((soft[0]["text"], soft[0]["category"]), ("Knee pain on lunges", "injury"))
        forgotten = memory_tools.forget_fact(app, saved["fact"]["id"])
        self.assertEqual((forgotten["forgotten"], forgotten["fact"]["status"]), (True, "archived"))
        self.assertEqual(memory_tools.get_preferences(app)["facts"]["constraints"]["soft"], [])

    def test_avoided_exercises_carry_reason(self):
        app, _ = make_app(self)
        app.memory.mark_exercise(321, "preferred", "Barbell Bent Over Row")
        app.memory.mark_exercise(600, "avoided", "Single Arm Cable Row", reason="left shoulder")
        got = memory_tools.get_preferences(app)
        self.assertEqual(got["preferredExercises"], [{"groupId": 321, "name": "Barbell Bent Over Row"}])
        self.assertEqual(got["avoidedExercises"],
                         [{"groupId": 600, "name": "Single Arm Cable Row", "reason": "left shoulder"}])

    def test_load_anchors_merge_through_the_tool(self):
        app, _ = make_app(self)
        memory_tools.set_preferences(app, load_anchors={"321": 45})
        memory_tools.set_preferences(app, load_anchors={"500": 20})
        self.assertEqual(memory_tools.get_preferences(app)["load_anchors"], {"321": 45.0, "500": 20.0})
        memory_tools.set_preferences(app, load_anchors={"321": None})
        self.assertEqual(memory_tools.get_preferences(app)["load_anchors"], {"500": 20.0})

    def test_errors_are_tool_errors(self):
        app, _ = make_app(self)
        with self.assertRaises(ToolError):
            memory_tools.set_preferences(app, session_minutes=0)
        with self.assertRaises(ToolError):
            memory_tools.remember_fact(app, "  ", kind="observation", category="note")
        with self.assertRaises(ToolError):
            memory_tools.forget_fact(app, 404)
        with self.assertRaises(ToolError):
            memory_tools.forget_fact(app, "abc")
