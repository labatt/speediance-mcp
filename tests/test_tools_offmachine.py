"""Off-machine training: logging it, reading it back, and where it shows up.

Speediance's manual log counts a day but stores no exercises, and there is no way to
write them there. So these tools are the only place that detail exists — which means the
tests that matter are the ones proving it reaches the places a user would expect: a
manual session's detail, and volume by muscle.
"""

from __future__ import annotations

import unittest

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp.speediance import offmachine as adapt
from speediance_mcp.speediance.muscles import attribute, muscle_index
from speediance_mcp.tools import coaching, offmachine, sessions
from tests import fixtures as fx
from tests.helpers import make_app


class TestLogging(unittest.TestCase):
    def test_a_workout_is_logged_and_sets_expand(self):
        app, _ = make_app(self)
        out = offmachine.log_off_machine_workout(app, fx.TODAY.isoformat(), [
            {"name": "Barbell Bent Over Row", "sets": 3, "reps": 10, "weight": 40},
        ], location="hotel")
        self.assertEqual(out["setsStored"], 3, "three sets of ten is three stored sets")
        self.assertEqual(out["volume"], 3 * 10 * 40)
        self.assertEqual(out["location"], "hotel")
        self.assertNotIn("unmatched", out)

    def test_a_name_resolves_to_the_library_so_it_can_reach_muscles(self):
        app, _ = make_app(self)
        out = offmachine.log_off_machine_workout(app, fx.TODAY.isoformat(), [
            {"name": "bent over row", "sets": 1, "reps": 10, "weight": 40}])
        exercise = out["exercises"][0]
        self.assertEqual(exercise["groupId"], 321, "matched loosely by name")
        self.assertEqual(exercise["name"], "Barbell Bent Over Row", "stored under the library's name")

    def test_a_movement_speediance_does_not_stock_is_logged_and_flagged(self):
        """Not being in the library is not a reason to refuse the workout."""
        app, _ = make_app(self)
        out = offmachine.log_off_machine_workout(app, fx.TODAY.isoformat(), [
            {"name": "Hotel Stairwell Carry", "sets": 2, "reps": 5, "weight": 60}])
        self.assertEqual(out["setsStored"], 2)
        self.assertIsNone(out["exercises"][0]["groupId"])
        self.assertEqual([u["name"] for u in out["unmatched"]], ["Hotel Stairwell Carry"])
        self.assertIn("cannot be attributed to muscles", out["unmatchedNote"])

    def test_bodyweight_work_is_recorded_rather_than_refused(self):
        app, _ = make_app(self)
        out = offmachine.log_off_machine_workout(app, fx.TODAY.isoformat(), [
            {"name": "Push-Up", "sets": 3, "reps": 20}])
        self.assertEqual(out["setsStored"], 3)
        self.assertEqual(out["volume"], 0.0, "no load means no volume, which is not an error")

    def test_a_single_arm_set_keeps_its_side(self):
        app, _ = make_app(self)
        out = offmachine.log_off_machine_workout(app, fx.TODAY.isoformat(), [
            {"name": "Single Arm Cable Row", "sets": 1, "reps": 10, "weight": 30, "side": "left"}])
        self.assertEqual(out["exercises"][0]["setLog"][0]["side"], "left")

    def test_bad_input_is_refused_and_nothing_is_written(self):
        app, _ = make_app(self)
        for bad in ([{"name": "Push-Up", "sets": 1, "reps": 0}],
                    [{"name": "Push-Up", "sets": 1, "reps": 10, "weight": -5}],
                    [{"name": "", "sets": 1, "reps": 10}],
                    [{"name": "Push-Up", "sets": 0, "reps": 10}],
                    []):
            with self.subTest(bad=bad), self.assertRaises(ToolError):
                offmachine.log_off_machine_workout(app, fx.TODAY.isoformat(), bad)
        self.assertEqual(app.memory.offmachine_sets("2000-01-01", "2099-01-01"), [])

    def test_one_bad_set_in_a_batch_stores_none_of_it(self):
        """A half-logged session understates volume silently — worse than no log at all."""
        app, _ = make_app(self)
        with self.assertRaises(ToolError):
            offmachine.log_off_machine_workout(app, fx.TODAY.isoformat(), [
                {"name": "Barbell Bent Over Row", "sets": 3, "reps": 10, "weight": 40},
                {"name": "Push-Up", "sets": 1, "reps": -2},
            ])
        self.assertEqual(app.memory.offmachine_sets("2000-01-01", "2099-01-01"), [])

    def test_a_malformed_date_is_refused(self):
        app, _ = make_app(self)
        with self.assertRaises(ToolError):
            offmachine.log_off_machine_workout(app, "last tuesday",
                                               [{"name": "Push-Up", "sets": 1, "reps": 10}])


class TestReadingBack(unittest.TestCase):
    def test_the_log_groups_by_day_newest_first(self):
        app, _ = make_app(self)
        offmachine.log_off_machine_workout(app, "2026-08-20",
                                           [{"name": "Push-Up", "sets": 2, "reps": 20}])
        offmachine.log_off_machine_workout(app, "2026-08-22", [
            {"name": "Barbell Bent Over Row", "sets": 3, "reps": 10, "weight": 40},
            {"name": "Standing Barbell Biceps Curl", "sets": 2, "reps": 12, "weight": 25}])
        out = offmachine.get_off_machine_log(app, "2026-08-01", "2026-08-31")
        self.assertEqual(out["days"], 2)
        self.assertEqual(out["sets"], 7)
        self.assertEqual([s["day"] for s in out["sessions"]], ["2026-08-22", "2026-08-20"])
        self.assertEqual(out["sessions"][0]["exerciseCount"], 2)
        self.assertEqual(out["sessions"][0]["volume"], 3 * 10 * 40 + 2 * 12 * 25)

    def test_an_empty_window_says_so_rather_than_looking_broken(self):
        app, _ = make_app(self)
        out = offmachine.get_off_machine_log(app, "2026-08-01", "2026-08-31")
        self.assertEqual(out["sessions"], [])
        self.assertIn("Nothing logged", out["note"])

    def test_a_backwards_range_is_refused(self):
        app, _ = make_app(self)
        with self.assertRaises(ToolError):
            offmachine.get_off_machine_log(app, "2026-08-31", "2026-08-01")

    def test_a_day_can_be_removed_and_says_speediance_is_untouched(self):
        app, _ = make_app(self)
        offmachine.log_off_machine_workout(app, "2026-08-20",
                                           [{"name": "Push-Up", "sets": 2, "reps": 20}])
        out = offmachine.delete_off_machine_day(app, "2026-08-20")
        self.assertEqual(out["deleted"], 2)
        self.assertIn("still counts as trained", out["note"])
        self.assertEqual(offmachine.get_off_machine_log(app, "2026-08-01", "2026-08-31")["sets"], 0)


class TestItReachesTheDerivations(unittest.TestCase):
    """The point of the feature: off-machine work counts where machine work counts."""

    def test_off_machine_volume_lands_in_muscle_balance(self):
        app, _ = make_app(self)
        before = coaching.get_muscle_balance(app, days=30)
        offmachine.log_off_machine_workout(app, fx.TODAY.isoformat(), [
            {"name": "Barbell Bent Over Row", "sets": 3, "reps": 10, "weight": 40}])
        after = coaching.get_muscle_balance(app, days=30)

        self.assertEqual(after["offMachineDays"], 1)
        self.assertEqual(after["offMachineSets"], 3)
        self.assertEqual(after["sessions"], before["sessions"],
                         "off-machine work is not counted as a machine session")
        lats_before = dict((m["muscle"], m["volume"]) for m in before["byMuscle"]).get("Lats", 0)
        lats_after = dict((m["muscle"], m["volume"]) for m in after["byMuscle"])["Lats"]
        self.assertEqual(round(lats_after - lats_before, 1), 1200.0,
                         "3x10x40 reaches the main muscle at full share")

    def test_an_adapted_set_attributes_exactly_like_a_machine_set(self):
        app, _ = make_app(self)
        index = muscle_index(app.api.library())
        sets = app.memory.log_offmachine_sets(fx.TODAY.isoformat(), [
            {"name": "Barbell Bent Over Row", "groupId": 321, "reps": 10, "weight": 40}])
        spread = attribute(adapt.as_exercises(sets), index)
        # Main muscle full, assisting half — the standing convention, applied by the real function.
        self.assertEqual(spread["byMuscle"]["Lats"], 400.0)
        self.assertEqual(spread["byMuscle"]["Rear Delts"], 200.0)

    def test_a_manual_session_serves_our_exercises_instead_of_nothing(self):
        routes = fx.standard_routes()
        routes[("GET", fx.HISTORY_PATH)] = [
            {"trainingId": 9100, "type": 10, "title": "Free Strength Training",
             "startTime": "2026-08-22 14:00:00", "trainingTime": 2100, "calorie": 280,
             "totalCapacity": 0, "isFinish": 1},
        ]
        app, _ = make_app(self, routes)

        empty = sessions.get_session_detail(app, 9100)
        self.assertEqual(empty["exercises"], [])
        self.assertEqual(empty["exerciseSource"], "none")
        self.assertIn("log_off_machine_workout", empty["note"],
                      "an empty manual session should point at the fix")

        offmachine.log_off_machine_workout(app, "2026-08-22", [
            {"name": "Barbell Bent Over Row", "sets": 3, "reps": 10, "weight": 40}],
            training_id=9100, location="hotel")
        filled = sessions.get_session_detail(app, 9100)
        self.assertEqual(filled["exerciseSource"], "offmachine")
        self.assertEqual(len(filled["exercises"]), 1)
        self.assertEqual(filled["exercises"][0]["volume"], 1200.0)
        self.assertEqual(filled["exercises"][0]["source"], "offmachine")
        self.assertIn("does count", filled["note"].lower())
        # Speediance's own numbers still come from Speediance.
        self.assertEqual(filled["durationSec"], 2100)
        self.assertEqual(filled["calories"], 280)

    def test_sets_logged_before_the_manual_record_still_attach_by_date(self):
        routes = fx.standard_routes()
        routes[("GET", fx.HISTORY_PATH)] = [
            {"trainingId": 9100, "type": 10, "title": "Free Strength Training",
             "startTime": "2026-08-22 14:00:00", "trainingTime": 2100, "calorie": 280,
             "totalCapacity": 0, "isFinish": 1},
        ]
        app, _ = make_app(self, routes)
        # No training_id: the user logged the workout before adding it in the Speediance app.
        offmachine.log_off_machine_workout(app, "2026-08-22",
                                          [{"name": "Push-Up", "sets": 2, "reps": 20}])
        detail = sessions.get_session_detail(app, 9100)
        self.assertEqual(detail["exerciseSource"], "offmachine")
        self.assertEqual(detail["exercises"][0]["name"], "Push-Up")


if __name__ == "__main__":
    unittest.main()
