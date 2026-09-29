from __future__ import annotations

import unittest

from speediance_mcp.speediance import parsing
from tests import fixtures as fx


class TestHelpers(unittest.TestCase):
    def test_nums_accepts_lists_and_csv(self):
        self.assertEqual(parsing.nums("1,2.5,x,"), [1.0, 2.5])
        self.assertEqual(parsing.nums([3, None, "4"]), [3.0, 4.0])
        self.assertEqual(parsing.nums(None), [])

    def test_kind_of(self):
        self.assertEqual(parsing.kind_of({"completionMethod": 1}), "reps")
        self.assertEqual(parsing.kind_of({"completionMethod": 0}), "timed")
        self.assertEqual(parsing.kind_of({"completionMethod": 2}), "timed")
        self.assertEqual(parsing.kind_of({"completionMethod": 5}), "level")
        self.assertEqual(parsing.kind_of({"completionMethod": 1, "dataStatType": 6}), "level")

    def test_side_arrays_beat_derived_force_weights(self):
        detail = {"weights": [48.5, 47.0], "leftWeights": [12.0], "rightWeights": [12.5]}
        # both cables carry the load on an unpinned bilateral set: max(left) + max(right).
        self.assertEqual(parsing.set_load(detail, None), 24.5)
        self.assertEqual(parsing.set_load(detail, 1), 12.0)
        self.assertEqual(parsing.set_load({"weights": [30.0, 30.0]}, None), 30.0)
        self.assertIsNone(parsing.set_load({}, None))

    def test_bilateral_load_is_the_sum_of_both_cables(self):
        # Barbell-shaped data: total load 50, split 25/25 across both cables.
        detail = {"weights": [50.0] * 3, "leftWeights": [25.0] * 3, "rightWeights": [25.0] * 3}
        self.assertEqual(parsing.set_load(detail, None), 50.0)

    def test_bilateral_sum_never_zips_ragged_arrays(self):
        # leftWeights and rightWeights are independent, ragged telemetry series (different lengths);
        # the rule is max(left) + max(right), never a per-index pairing.
        detail = {"leftWeights": [12.0, 14.0], "rightWeights": [10.0, 11.0, 13.0]}
        self.assertEqual(parsing.set_load(detail, None), 27.0)

    def test_pinned_side_uses_only_that_sides_array(self):
        detail = {"leftWeights": [20.0, 22.0], "rightWeights": [24.0, 26.0]}
        self.assertEqual(parsing.set_load(detail, 1), 22.0)
        self.assertEqual(parsing.set_load(detail, 2), 26.0)


class TestListExercises(unittest.TestCase):
    def test_worked_sets_skipped_sets_and_loads(self):
        row, fly = parsing.parse_list_exercises(fx.CTT_5001)
        self.assertEqual(row["name"], "Barbell Bent Over Row")
        self.assertEqual(row["groupId"], 321)
        self.assertEqual((row["sets"], row["skippedSets"]), (2, 1))
        self.assertEqual(row["reps"], [12, 8])
        self.assertEqual(row["weights"], [30.0, 50.0])
        self.assertEqual(row["topWeight"], 50.0)
        self.assertEqual(row["volume"], 760.0)
        self.assertEqual(row["avgLoad"], 40.0)
        self.assertEqual([s["setIndex"] for s in row["setLog"]], [1, 2])
        self.assertEqual(fly["weights"], [24.5])

    def test_zero_heart_rate_means_no_watch(self):
        row = parsing.parse_list_exercises(fx.CTT_5001)[0]
        self.assertEqual([s["maxHeartRate"] for s in row["setLog"]], [142.0, None])

    def test_timed_sets_skip_on_zero_seconds(self):
        plank = {"actionLibraryName": "Plank", "completionMethod": 2, "finishedReps": [
            {"finishedCount": 0, "targetCount": 0, "time": 45, "trainingInfoDetail": {}},
            {"finishedCount": 0, "targetCount": 0, "time": 0, "trainingInfoDetail": {}}]}
        ex = parsing.parse_list_exercises([plank])[0]
        self.assertEqual((ex["kind"], ex["sets"], ex["skippedSets"]), ("timed", 1, 1))
        self.assertEqual(ex["setLog"][0]["seconds"], 45)
        self.assertIsNone(ex["setLog"][0]["weight"])

    def test_vita_level(self):
        vita = {"actionLibraryName": "Vita Pull", "completionMethod": 5, "finishedReps": [
            {"finishedCount": 13, "targetCount": 20, "time": 20, "level": "12", "trainingInfoDetail": {}}]}
        entry = parsing.parse_list_exercises([vita])[0]["setLog"][0]
        self.assertEqual((entry["level"], entry["reps"], entry["weight"]), (12, 13, None))

    def test_cardio_fields_are_kept_when_present(self):
        entry = parsing.parse_list_exercises(fx.AEROBIC_INTERVALS)[0]["setLog"][0]
        self.assertEqual((entry["distance"], entry["pace"], entry["strokeRate"]), (1000.0, 150.0, 24.0))

    def test_unnamed_exercise_and_junk_rows(self):
        out = parsing.parse_list_exercises([None, {"completionMethod": 1, "finishedReps": []}])
        self.assertEqual(out[0]["name"], "Exercise (not picked in app)")
        self.assertEqual(out[0]["sets"], 0)
