"""Equipment owned but unusable.

Owning a bench you cannot lie on is not the same as not owning one: the movement looks
available in every list, so it has to be hidden explicitly rather than left to the
owned-equipment filter.
"""

from __future__ import annotations

import unittest

from speediance_mcp.library import filter_exercises
from speediance_mcp.tools import exercises as exercise_tools
from tests.helpers import make_app


def item(group_id, name, equipment):
    return {"groupId": group_id, "name": name, "category": "", "bodyParts": [], "muscles": [],
            "secondaryMuscles": [], "equipment": equipment, "equipmentIds": [], "unilateral": False,
            "kind": "reps", "recommendedWeightKg": None}


ITEMS = [item(1, "Flat Bench Press", ["Flat Bench", "Barbell"]),
         item(2, "Standing Row", ["Handles"]),
         item(3, "Incline Press", ["Incline Bench", "Barbell"])]


class TestFilter(unittest.TestCase):
    def test_unusable_equipment_hides_a_movement_even_when_it_is_owned(self):
        rows = filter_exercises(ITEMS, owned=["Flat Bench", "Barbell", "Handles", "Incline Bench"],
                                owned_only=True, unusable=["Flat Bench"])
        self.assertEqual([r["name"] for r in rows], ["Incline Press", "Standing Row"])

    def test_it_hides_the_movement_without_owned_only_too(self):
        # The list is about what can be done, not about what was bought.
        rows = filter_exercises(ITEMS, unusable=["Flat Bench"])
        self.assertNotIn("Flat Bench Press", [r["name"] for r in rows])

    def test_matching_is_case_insensitive(self):
        rows = filter_exercises(ITEMS, unusable=["flat bench"])
        self.assertNotIn("Flat Bench Press", [r["name"] for r in rows])

    def test_an_empty_list_hides_nothing(self):
        self.assertEqual(len(filter_exercises(ITEMS, unusable=[])), 3)
        self.assertEqual(len(filter_exercises(ITEMS, unusable=None)), 3)


class TestPreference(unittest.TestCase):
    def test_it_round_trips_and_defaults_to_empty(self):
        app, _ = make_app(self)
        self.assertEqual(app.memory.preferences()["unusable_equipment"], [])
        app.memory.set_preferences(unusable_equipment=["Flat Bench", "  "])
        self.assertEqual(app.memory.preferences()["unusable_equipment"], ["Flat Bench"])

    def test_a_non_list_is_rejected_by_name(self):
        app, _ = make_app(self)
        with self.assertRaises(ValueError) as caught:
            app.memory.set_preferences(unusable_equipment="Flat Bench")
        self.assertIn("unusable_equipment", str(caught.exception))

    def test_list_accessories_flags_usable_separately_from_owned(self):
        app, _ = make_app(self)
        app.memory.set_preferences(owned_equipment=["Flat Bench"], unusable_equipment=["Flat Bench"])
        rows = exercise_tools.list_accessories(app)["accessories"]
        bench = next((r for r in rows if r["name"].lower() == "flat bench"), None)
        if bench is not None:            # only if the fixture catalog carries one
            self.assertTrue(bench["owned"])
            self.assertFalse(bench["usable"])
        self.assertTrue(all(r["usable"] for r in rows if r["name"].lower() != "flat bench"))


if __name__ == "__main__":
    unittest.main()
