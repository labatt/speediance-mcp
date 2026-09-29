from __future__ import annotations

import unittest

from speediance_mcp import library
from tests import fixtures as fx

NAMES = library.accessory_names(fx.ACCESSORIES)
ITEMS = [library.summarize_exercise(raw, NAMES) for raw in fx.LIBRARY]


class TestLibrary(unittest.TestCase):
    def test_summarize(self):
        pulldown = next(i for i in ITEMS if i["groupId"] == 416)
        self.assertEqual(pulldown["name"], "Seated Barbell Lat Pulldown")
        self.assertEqual(pulldown["equipment"], ["Barbell", "Flat Bench"])
        self.assertEqual((pulldown["bodyParts"], pulldown["muscles"]), (["Back"], ["Lats"]))
        vita = next(i for i in ITEMS if i["groupId"] == 700)
        self.assertEqual(vita["kind"], "level")
        self.assertTrue(next(i for i in ITEMS if i["groupId"] == 600)["unilateral"])
        self.assertEqual(library.variant_id(fx.LIBRARY[0]), 3210)

    def test_unknown_accessory_id_is_named_generically(self):
        raw = dict(fx.LIBRARY[0], accessories="4,99")
        self.assertEqual(library.summarize_exercise(raw, NAMES)["equipment"], ["Barbell", "accessory 99"])

    def test_resolve(self):
        self.assertEqual(library.resolve_exercise(ITEMS, "vita row")[0]["groupId"], 700)       # exact
        self.assertEqual(library.resolve_exercise(ITEMS, "standing")[0]["groupId"], 294)       # unique prefix
        self.assertEqual(library.resolve_exercise(ITEMS, "bent over row")[0]["groupId"], 321)  # all words
        item, candidates = library.resolve_exercise(ITEMS, "row")
        self.assertIsNone(item)
        self.assertEqual(len(candidates), 5)
        self.assertEqual(library.resolve_exercise(ITEMS, "squat"), (None, []))

    def test_filters(self):
        marks = {600: {"mark": "avoided"}, 294: {"mark": "preferred"}}
        rows = library.filter_exercises(ITEMS, query="row", marks=marks)
        self.assertNotIn(600, [r["groupId"] for r in rows])
        self.assertIn(600, [r["groupId"] for r in library.filter_exercises(ITEMS, query="row", marks=marks,
                                                                           include_avoided=True)])
        self.assertEqual([r["groupId"] for r in library.filter_exercises(ITEMS, muscle="biceps")], [294])
        owned = library.filter_exercises(ITEMS, owned=["barbell"], owned_only=True)
        self.assertEqual(sorted(r["groupId"] for r in owned), [294, 321, 424])
        self.assertEqual(library.filter_exercises(ITEMS, marks=marks)[0]["groupId"], 294)  # preferred first
        self.assertEqual(len(library.filter_exercises(ITEMS, limit=2)), 2)
        self.assertEqual([r["groupId"] for r in library.filter_exercises(ITEMS, kind="level")], [700])
