from __future__ import annotations

import unittest

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp.tools import coaching
from tests import fixtures as fx
from tests.helpers import make_app


def empty_account_routes():
    routes = fx.standard_routes()
    routes[("GET", fx.HISTORY_PATH)] = []
    routes[("GET", fx.STATS_PATH)] = []
    routes[("GET", fx.TEMPLATES_PATH)] = []
    return routes


class TestSnapshot(unittest.TestCase):
    def test_snapshot(self):
        app, _ = make_app(self)
        app.memory.remember_fact("No overhead pressing", kind="constraint", severity="hard", category="injury")
        app.memory.mark_exercise(321, "preferred", "Barbell Bent Over Row")
        self.assertEqual([h["trainingId"] for h in coaching.get_athlete_snapshot(app)["history"]], [5001, 5000])
        got = coaching.get_athlete_snapshot(app, days=30)
        self.assertEqual([h["trainingId"] for h in got["history"]], [5001, 5000, 7002, 7001])
        self.assertEqual(got["otherActivities"],
                         [{"date": "2026-08-20", "title": "Walk", "minutes": 11, "calorie": 30,
                           "source": "phone health app"}])
        self.assertEqual(got["profile"]["age"], 36)
        self.assertEqual(got["memory"]["facts"]["constraints"]["hard"][0]["text"], "No overhead pressing")
        self.assertEqual(got["memory"]["preferredExercises"], [{"groupId": 321, "name": "Barbell Bent Over Row"}])
        self.assertEqual(got["displayUnit"], "lb")

    def test_snapshot_avoided_exercises_carry_reason(self):
        app, _ = make_app(self)
        app.memory.mark_exercise(600, "avoided", "Single Arm Cable Row", reason="left shoulder")
        got = coaching.get_athlete_snapshot(app)
        self.assertEqual(got["memory"]["avoidedExercises"],
                         [{"groupId": 600, "name": "Single Arm Cable Row", "reason": "left shoulder"}])


class TestStrength(unittest.TestCase):
    def test_strength_profile(self):
        app, _ = make_app(self)
        got = coaching.get_strength_profile(app)
        self.assertEqual([m["groupId"] for m in got["movements"]], [424, 321, 500])
        row = got["movements"][1]
        self.assertEqual(row["estimated1RM"], 63.3)
        self.assertEqual(row["bestSet"], {"date": "2026-08-29", "weight": 50.0, "reps": 8})
        self.assertEqual(row["topWeightChange"], 5.0)
        self.assertIsNone(got["movements"][0]["topWeightChange"])

    def test_strength_profile_sees_quick_sessions(self):
        # A quick single-exercise session (freeTraining with no actionList, real data only at
        # freeTrainingDetail) must use the same fallback as get_session_detail, or coaching tools
        # would silently miss it.
        app, _ = make_app(self)
        got = coaching.get_strength_profile(app, days=365)
        self.assertIn(950, [m["groupId"] for m in got["movements"]])

    def test_compare_finds_previous_session(self):
        app, _ = make_app(self)
        got = coaching.compare_sessions(app, 5001)
        self.assertEqual(got["previous"]["trainingId"], 5000)
        row = next(m for m in got["movements"] if m["name"] == "Barbell Bent Over Row")
        self.assertEqual(row["change"], {"topWeight": 5.0, "reps": 0, "volume": 40.0})
        fly = next(m for m in got["movements"] if m["name"] == "Cable Fly")
        self.assertIsNone(fly["previous"])

    def test_compare_skips_health_imports(self):
        routes = fx.standard_routes()
        imported = {"trainingId": 8000, "type": 5, "belongUserHealth": 1, "title": "Synced Workout",
                    "startTime": "2026-08-25 10:00:00", "trainingTime": 600}
        routes[("GET", fx.HISTORY_PATH)] = fx.history_route(fx.HISTORY + [imported])
        routes[("GET", fx.DETAIL + "cttTrainingInfoDetail/8000")] = fx.CTT_5000
        app, fake = make_app(self, routes)
        got = coaching.compare_sessions(app, 5001)
        self.assertEqual(got["previous"]["trainingId"], 5000)
        self.assertEqual(fake.calls("GET", fx.DETAIL + "cttTrainingInfoDetail/8000"), [])

    def test_compare_explicit_previous(self):
        app, _ = make_app(self)
        got = coaching.compare_sessions(app, 5001, previous_training_id=5000)
        self.assertEqual(got["previous"]["trainingId"], 5000)

    def test_suggest_load(self):
        app, _ = make_app(self)
        got = coaching.suggest_load(app, reps=8, exercise="bent over row")
        self.assertEqual(got["suggestedWeight"], 47.5)
        self.assertEqual(got["basis"]["date"], "2026-08-29")
        self.assertEqual((got["basis"]["weight"], got["basis"]["reps"]), (50.0, 8))

    def test_suggest_load_timed_and_ambiguous_and_validation(self):
        app, _ = make_app(self)
        self.assertIsNone(coaching.suggest_load(app, reps=10, groupId=900)["suggestedWeight"])
        with self.assertRaises(ToolError):
            coaching.suggest_load(app, reps=8, exercise="row")
        with self.assertRaises(ToolError):
            coaching.suggest_load(app, reps=0, groupId=321)


class TestEmptyAccount(unittest.TestCase):
    def test_everything_degrades_gracefully(self):
        app, _ = make_app(self, empty_account_routes())
        self.assertEqual(coaching.get_athlete_snapshot(app)["history"], [])
        profile = coaching.get_strength_profile(app)
        self.assertEqual(profile["movements"], [])
        self.assertIn("No weighted strength sessions", profile["note"])
        got = coaching.suggest_load(app, reps=8, groupId=321)
        self.assertIsNone(got["suggestedWeight"])
        self.assertIn("No history", got["note"])
        app.memory.set_preferences(load_anchors={"321": 40})
        self.assertEqual(coaching.suggest_load(app, reps=8, groupId=321)["suggestedWeight"], 40.0)
