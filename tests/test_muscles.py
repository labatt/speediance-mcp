"""Muscle attribution and balance.

The library rows here are synthetic but shaped like the real catalog: each movement
carries a main and an assisting muscle list plus the body-part id it is filed under.
"""

from __future__ import annotations

import unittest

from speediance_mcp.speediance import muscles


def entry(group_id, part, main, assist=()):
    return {"id": group_id, "trainingPartId2": part,
            "mainMuscleGroupList": [{"muscleGroupName": m} for m in main],
            "auxiliaryMuscleGroupList": [{"muscleGroupName": a} for a in assist]}


LIBRARY = [
    entry(1, 11, ["Pecs"], ["Triceps", "Front Delts"]),      # a press
    entry(2, 13, ["Lats"], ["Biceps"]),                      # a row
    entry(3, 15, ["Quads"], ["Glutes"]),                     # a squat
    entry(4, 18, ["Full Body"]),                             # filing category
    entry(5, 17, ["Abs"]),
]
INDEX = muscles.muscle_index(LIBRARY)


def did(group_id, volume, name="Move"):
    return {"groupId": group_id, "name": name, "volume": volume}


class TestMuscleIndex(unittest.TestCase):
    def test_index_names_the_body_part_and_splits_main_from_assisting(self):
        self.assertEqual(INDEX[1]["main"], ["Pecs"])
        self.assertEqual(INDEX[1]["assist"], ["Triceps", "Front Delts"])
        self.assertEqual(INDEX[1]["part"], "Chest")
        self.assertEqual(INDEX[3]["part"], "Legs")

    def test_a_muscle_listed_both_ways_is_only_counted_as_main(self):
        index = muscles.muscle_index([entry(9, 11, ["Pecs"], ["Pecs", "Triceps"])])
        self.assertEqual(index[9]["main"], ["Pecs"])
        self.assertEqual(index[9]["assist"], ["Triceps"])

    def test_rows_without_an_id_are_skipped(self):
        self.assertEqual(muscles.muscle_index([{"trainingPartId2": 11}]), {})


class TestAttribute(unittest.TestCase):
    def test_main_takes_full_volume_and_assisting_takes_half(self):
        got = muscles.attribute([did(1, 100)], INDEX)
        self.assertEqual(got["byMuscle"], {"Pecs": 100.0, "Triceps": 50.0, "Front Delts": 50.0})
        # The body-part total is the real volume, not the attributed sum.
        self.assertEqual(got["byBodyPart"], {"Chest": 100.0})

    def test_volume_accumulates_across_exercises(self):
        got = muscles.attribute([did(1, 100), did(2, 60)], INDEX)
        self.assertEqual(got["byMuscle"]["Pecs"], 100.0)
        self.assertEqual(got["byMuscle"]["Lats"], 60.0)
        self.assertEqual(got["byMuscle"]["Biceps"], 30.0)

    def test_unweighted_work_is_counted_not_silently_zeroed(self):
        got = muscles.attribute([did(1, 0), did(5, 0), did(2, 40)], INDEX)
        self.assertEqual(got["unweightedExercises"], 2)
        self.assertNotIn("Pecs", got["byMuscle"])
        self.assertEqual(got["byMuscle"]["Lats"], 40.0)

    def test_an_exercise_missing_from_the_library_is_named_not_dropped_silently(self):
        got = muscles.attribute([did(999, 50, name="Mystery Move")], INDEX)
        self.assertEqual(got["exercisesNotInLibrary"], ["Mystery Move"])
        self.assertEqual(got["byMuscle"], {})

    def test_an_exercise_with_no_group_id_is_reported_too(self):
        got = muscles.attribute([{"groupId": None, "name": "Unpicked", "volume": 10}], INDEX)
        self.assertEqual(got["exercisesNotInLibrary"], ["Unpicked"])


class TestRatios(unittest.TestCase):
    def test_push_and_pull_split_on_the_muscle_not_the_exercise(self):
        got = muscles.ratios({"Pecs": 100.0, "Triceps": 50.0, "Lats": 100.0, "Biceps": 50.0})
        self.assertEqual((got["push"], got["pull"]), (150.0, 150.0))
        self.assertEqual(got["pushPull"], 1.0)

    def test_upper_lower_ratio(self):
        got = muscles.ratios({"Pecs": 200.0, "Quads": 100.0})
        self.assertEqual((got["upper"], got["lower"]), (200.0, 100.0))
        self.assertEqual(got["upperLower"], 2.0)

    def test_nothing_to_compare_gives_none_rather_than_a_divide_by_zero(self):
        got = muscles.ratios({"Pecs": 100.0})
        self.assertIsNone(got["pushPull"])
        self.assertIsNone(got["upperLower"])
        self.assertEqual(got["pull"], 0.0)

    def test_full_body_belongs_to_neither_chain(self):
        got = muscles.ratios({"Full Body": 500.0})
        self.assertEqual((got["push"], got["pull"], got["upper"], got["lower"]), (0.0, 0.0, 0.0, 0.0))


class TestUntrained(unittest.TestCase):
    def test_lists_known_main_muscles_with_no_volume(self):
        got = muscles.untrained({"Pecs": 100.0}, INDEX)
        self.assertIn("Lats", got)
        self.assertIn("Abs", got)
        self.assertNotIn("Pecs", got)

    def test_full_body_is_never_reported_as_an_untrained_muscle(self):
        self.assertNotIn("Full Body", muscles.untrained({}, INDEX))

    def test_assisting_only_muscles_do_not_count_as_untrained(self):
        # Triceps never appear as a main muscle in this library, so calling them
        # "not trained" would be an artefact of the catalog, not of the training.
        self.assertNotIn("Triceps", muscles.untrained({}, INDEX))


if __name__ == "__main__":
    unittest.main()
