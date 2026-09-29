from __future__ import annotations

import unittest

from speediance_mcp.speediance import parsing
from tests import fixtures as fx


class TestFreeLift(unittest.TestCase):
    def test_lb_account_sets_are_unscaled(self):
        self.assertEqual(parsing.free_scale(fx.FREE_6001), (1.0, None))
        exercises, warnings = parsing.parse_free_exercises(fx.FREE_6001)
        self.assertEqual(warnings, [])
        self.assertEqual(exercises[0]["groupId"], 424)
        self.assertEqual(exercises[0]["weights"], [100.0, 100.0])
        self.assertEqual(exercises[0]["volume"], 2000.0)

    def test_kg_account_sets_are_divided_by_2_2(self):
        self.assertEqual(parsing.free_scale(fx.FREE_KG_SCALED)[0], 2.2)
        exercises, _ = parsing.parse_free_exercises(fx.FREE_KG_SCALED)
        self.assertEqual(exercises[0]["weights"], [45.5, 45.5])

    def test_unreconciled_sessions_keep_raw_values_and_warn(self):
        exercises, warnings = parsing.parse_free_exercises(fx.FREE_MISMATCH)
        self.assertEqual(exercises[0]["weights"], [100.0, 100.0])
        self.assertEqual(len(warnings), 1)
        self.assertIn("didn't reconcile", warnings[0])

    def test_normalize_dispatches_on_route(self):
        self.assertEqual(parsing.normalize_session("freeTraining", fx.FREE_6001)["exercises"][0]["name"],
                         "Seated Barbell Row")
        self.assertEqual(len(parsing.normalize_session("cttTrainingInfoDetail", fx.CTT_5001)["exercises"]), 2)
        self.assertEqual(parsing.normalize_session("courseTrainingInfoDetail", None),
                         {"exercises": [], "warnings": []})

    def test_find_uuid(self):
        self.assertEqual(parsing.find_uuid("cttTrainingInfoDetail", fx.CTT_5001), "hr-uuid-5001")
        self.assertIsNone(parsing.find_uuid("freeTraining", fx.FREE_6001))
        self.assertEqual(parsing.find_uuid("freeTraining", {"uuid": "u", "showHeartGraph": 1}), "u")


class TestCardio(unittest.TestCase):
    def test_rowing_oracle(self):
        stats = parsing.derive_cardio_stats(fx.ROWING_SUMMARY_7001)
        self.assertEqual(stats["durationSec"], 530)
        self.assertEqual(stats["distanceM"], 892.71)
        self.assertEqual(stats["pace500"], 296.8)
        self.assertEqual(stats["speedMs"], 1.68)
        self.assertEqual(stats["calPerMin"], 18.2)
        self.assertEqual(stats["energyKJ"], 29.6)
        self.assertEqual(stats["avgWatts"], 56)
        self.assertEqual((stats["completion"], stats["rpe"]), (29.0, 6.0))

    def test_missing_inputs_give_none_never_nan(self):
        stats = parsing.derive_cardio_stats({"trainingTime": 0})
        self.assertIsNone(stats["pace500"])
        self.assertIsNone(stats["avgWatts"])
        self.assertIsNone(stats["calPerMin"])

    def test_is_cardio(self):
        self.assertTrue(parsing.is_cardio(fx.ROWING_SUMMARY_7001))
        self.assertTrue(parsing.is_cardio({"courseType": 2}))
        self.assertFalse(parsing.is_cardio(fx.SUMMARY_5001))

    def test_intervals(self):
        rows = parsing.intervals_from(fx.AEROBIC_INTERVALS)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"interval": 1, "seconds": 300, "distanceM": 1000.0, "pace500": 150.0,
                                   "strokeRate": 24.0, "maxHeartRate": None})


class TestHeartRateAndStrength(unittest.TestCase):
    def test_heart_rate_shapes(self):
        self.assertEqual(parsing.heart_rate_values(fx.HEART_RATE), [120.0, 150.0])
        self.assertEqual(parsing.heart_rate_values([100, 0, 110]), [100.0, 110.0])
        self.assertEqual(parsing.heart_rate_values({"list": [{"bpm": 90}]}), [90.0])
        self.assertEqual(parsing.heart_rate_values(None), [])

    def test_downsample(self):
        self.assertEqual(len(parsing.downsample(list(range(1000)), 600)), 500)
        self.assertEqual(parsing.downsample([1, 2, 3], 600), [1, 2, 3])

    def test_epley_and_round_half(self):
        self.assertAlmostEqual(parsing.epley(50, 8), 63.333, places=2)
        self.assertEqual(parsing.epley(80, 1), 80)
        self.assertEqual(parsing.epley(0, 10), 0.0)
        self.assertEqual(parsing.round_half(47.49), 47.5)
        self.assertEqual(parsing.round_half(47.2), 47.0)
