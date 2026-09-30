from __future__ import annotations

import unittest

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp.tools import exercises
from tests.helpers import make_app


class TestExerciseTools(unittest.TestCase):
    def test_list_exercises_and_library_cache(self):
        app, fake = make_app(self)
        got = exercises.list_exercises(app, query="row")
        self.assertEqual(got["count"], 5)
        exercises.list_exercises(app, muscle="biceps")
        self.assertEqual(len(fake.calls("GET", "/api/app/actionLibraryTab/list")), 1)

    def test_owned_only_needs_owned_equipment(self):
        app, _ = make_app(self)
        with self.assertRaises(ToolError):
            exercises.list_exercises(app, owned_only=True)
        app.memory.set_preferences(owned_equipment=["Barbell"])
        got = exercises.list_exercises(app, owned_only=True)
        self.assertEqual(sorted(e["groupId"] for e in got["exercises"]), [294, 321, 424])

    def test_avoided_hidden_by_default(self):
        app, _ = make_app(self)
        exercises.mark_exercise(app, "avoided", group_id=600)
        names = [e["name"] for e in exercises.list_exercises(app, query="row")["exercises"]]
        self.assertNotIn("Single Arm Cable Row", names)

    def test_get_exercise(self):
        app, _ = make_app(self)
        got = exercises.get_exercise(app, 321)
        self.assertEqual((got["name"], got["equipment"], got["unilateral"]), ("Barbell Bent Over Row", ["Barbell"], False))
        self.assertEqual(got["video"], "https://example.test/321.mp4")
        with self.assertRaises(ToolError):
            exercises.get_exercise(app, 123456)

    def test_mark_exercise(self):
        app, _ = make_app(self)
        got = exercises.mark_exercise(app, "preferred", group_id=321)
        self.assertEqual((got["mark"], got["name"]), ("preferred", "Barbell Bent Over Row"))
        self.assertEqual(exercises.mark_exercise(app, "avoided", name="vita row")["groupId"], 700)
        with self.assertRaises(ToolError):
            exercises.mark_exercise(app, "love", group_id=321)
        with self.assertRaises(ToolError):
            exercises.mark_exercise(app, "avoided", name="row")  # ambiguous: nothing marked
        self.assertNotIn(600, app.memory.marks())

    def test_mark_exercise_with_reason(self):
        app, _ = make_app(self)
        got = exercises.mark_exercise(app, "avoided", group_id=600, reason="left shoulder")
        self.assertEqual(got["reason"], "left shoulder")
        self.assertEqual(app.memory.marks()[600]["reason"], "left shoulder")

    def test_list_accessories_dedupes_by_name(self):
        app, _ = make_app(self)
        app.memory.set_preferences(owned_equipment=["handles"])
        rows = {a["name"]: a for a in exercises.list_accessories(app)["accessories"]}
        self.assertEqual(len(rows["Handles"]["ids"]), 2)
        self.assertTrue(rows["Handles"]["owned"])
        self.assertEqual(rows["Flat Bench"]["type"], "furniture")

    def test_exercise_history_is_reported_as_weeks_not_sessions(self):
        """These rows are WEEKLY buckets, and the reply must not call them sessions.

        userActionStatPage returns one row per Sunday-to-Saturday week, labelled with that
        week's Monday (verified live 2026-09-29). The reply used to name them `sessions`
        and state "one entry per training day", which told the model a week's volume was a
        single workout's.
        """
        app, _ = make_app(self)
        got = exercises.get_exercise_history(app, exercise="bent over row")
        self.assertEqual(got["granularity"], "week")
        self.assertEqual([w["weekStarting"] for w in got["weeks"]], ["2026-08-22", "2026-08-29"])
        self.assertEqual(got["summary"]["bestWeight"], {"value": 50.0, "weekStarting": "2026-08-29"})
        self.assertEqual(got["weeks"][0]["minWeight"], 30.0)
        self.assertIn("weekVolume", got["weeks"][0])
        self.assertNotIn("sessions", got, "a week is not a session")
        self.assertIn("per WEEK", got["note"])

    def test_exercise_history_ambiguous_and_empty(self):
        app, _ = make_app(self)
        got = exercises.get_exercise_history(app, exercise="row")
        self.assertTrue(got["needsPick"])
        self.assertEqual(len(got["matches"]), 5)
        empty = exercises.get_exercise_history(app, groupId=424)
        self.assertEqual((empty["weeks"], empty["summary"]["weeks"]), ([], 0))
        with self.assertRaises(ToolError):
            exercises.get_exercise_history(app, exercise="squat")

    def test_exercise_history_limit_is_capped(self):
        from unittest import mock
        app, _ = make_app(self)
        for limit, expected in ((100000, 500), (0, 1), (-5, 1), (20, 20)):
            with self.subTest(limit=limit), \
                    mock.patch.object(app.api, "exercise_stats", return_value=[]) as stats:
                exercises.get_exercise_history(app, groupId=321, limit=limit)
                self.assertEqual(stats.call_args.kwargs["max_weeks"], expected)
