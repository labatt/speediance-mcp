from __future__ import annotations

import unittest

from speediance_mcp.speediance import writes

ROW = {"groupId": 321, "variantId": 3210, "name": "Row", "kind": "reps", "unilateral": False,
       "sets": [{"reps": 8, "weight": 50.0, "side": None, "rest": 60}] * 2}
ONE_ARM = {"groupId": 600, "variantId": 6000, "name": "One Arm", "kind": "reps", "unilateral": True,
           "sets": [{"reps": 10, "weight": 20.0, "side": None, "rest": 45}] * 3}
VITA = {"groupId": 700, "variantId": 7000, "name": "Vita", "kind": "level", "unilateral": False,
        "sets": [{"seconds": 30, "level": 12, "side": None, "rest": 60}]}


class TestBuild(unittest.TestCase):
    def test_lb_account(self):
        body = writes.build_template("Pull", [ROW], unit="lb", device_type=1)
        action = body["actionLibraryList"][0]
        self.assertEqual((action["groupId"], action["actionLibraryId"], action["templatePresetId"]), (321, 3210, -1))
        self.assertEqual((action["setsAndReps"], action["weights"]), ("8,8", "50.0,50.0"))
        self.assertEqual((action["counterweight2"], action["capacity"]), ("", 800.0))
        self.assertEqual(body["totalCapacity"], 800.0)
        self.assertNotIn("id", body)

    def test_kg_account_uses_preset_1_and_scales_total(self):
        body = writes.build_template("Pull", [ROW], unit="kg", device_type=1, template_id=9001)
        self.assertEqual(body["actionLibraryList"][0]["templatePresetId"], 1)
        self.assertEqual(body["totalCapacity"], 1760.0)
        self.assertEqual(body["id"], 9001)

    def test_unilateral_sides_alternate_but_explicit_sides_win(self):
        action = writes.build_template("x", [ONE_ARM], unit="lb", device_type=1)["actionLibraryList"][0]
        self.assertEqual(action["leftRight"], "1,2,1")
        pinned = dict(ONE_ARM, sets=[{"reps": 10, "weight": 20.0, "side": 2, "rest": 45}])
        self.assertEqual(writes.build_template("x", [pinned], unit="lb", device_type=1)["actionLibraryList"][0]["leftRight"], "2")

    def test_vita(self):
        action = writes.build_template("x", [VITA], unit="lb", device_type=1)["actionLibraryList"][0]
        self.assertEqual((action["setsAndReps"], action["level"], action["weights"], action["completionMethod"]),
                         ("30", "12", "0", "2"))


class TestValidate(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(writes.validate_sets("reps", [{"reps": 8, "weight": 50}]),
                         [{"reps": 8, "weight": 50.0, "side": None, "rest": 60}])
        self.assertEqual(writes.validate_sets("timed", [{"seconds": 45, "rest_seconds": 30}]),
                         [{"seconds": 45, "level": None, "side": None, "rest": 30}])

    def test_invalid(self):
        for kind, sets in (("reps", []), ("reps", [{"reps": 0, "weight": 5}]), ("reps", [{"reps": 5, "weight": -1}]),
                           ("timed", [{"reps": 5}]), ("level", [{"seconds": 30}]), ("reps", [{"reps": 5, "side": 3}])):
            with self.assertRaises(ValueError):
                writes.validate_sets(kind, sets)


class TestValidateWholeNumbers(unittest.TestCase):
    def test_bools_rejected(self):
        for kind, raw in (("reps", {"reps": True, "weight": 50}),
                          ("timed", {"seconds": True}),
                          ("level", {"seconds": 30, "level": True}),
                          ("reps", {"reps": 8, "weight": 50, "side": True}),
                          ("timed", {"seconds": 30, "side": False})):
            with self.subTest(kind=kind, raw=raw), self.assertRaises(ValueError):
                writes.validate_sets(kind, [raw])

    def test_fractional_counts_rejected_not_truncated(self):
        for kind, raw in (("reps", {"reps": 8.9, "weight": 50}),
                          ("reps", {"reps": "8.5", "weight": 50}),
                          ("timed", {"seconds": 30.5}),
                          ("level", {"seconds": 30, "level": 12.5})):
            with self.subTest(kind=kind, raw=raw), self.assertRaises(ValueError):
                writes.validate_sets(kind, [raw])

    def test_integral_values_still_accepted(self):
        self.assertEqual(writes.validate_sets("reps", [{"reps": 8.0, "weight": 50}])[0]["reps"], 8)
        self.assertEqual(writes.validate_sets("reps", [{"reps": "10", "weight": 50}])[0]["reps"], 10)
        got = writes.validate_sets("level", [{"seconds": 30.0, "level": 12.0}])[0]
        self.assertEqual((got["seconds"], got["level"]), (30, 12))
        self.assertIs(type(got["seconds"]), int)

    def test_side_normalized_to_int(self):
        for side in (1, 1.0, "1"):
            got = writes.validate_sets("reps", [{"reps": 8, "weight": 50, "side": side}])[0]["side"]
            self.assertEqual(got, 1)
            self.assertIs(type(got), int)
        self.assertEqual(writes.validate_sets("timed", [{"seconds": 30, "side": "2"}])[0]["side"], 2)
        self.assertIsNone(writes.validate_sets("timed", [{"seconds": 30, "side": 0}])[0]["side"])
        for side in (1.5, "left", 3):
            with self.subTest(side=side), self.assertRaises(ValueError):
                writes.validate_sets("reps", [{"reps": 8, "weight": 50, "side": side}])

    def test_float_side_builds_a_clean_left_right_csv(self):
        sets = writes.validate_sets("reps", [{"reps": 8, "weight": 50, "side": 2.0}])
        spec = {"groupId": 600, "variantId": 6000, "kind": "reps", "unilateral": True, "sets": sets}
        body = writes.build_template("x", [spec], unit="lb", device_type=1)
        self.assertEqual(body["actionLibraryList"][0]["leftRight"], "2")


class TestReadAndVerify(unittest.TestCase):
    def stored(self, body, weight_divisor=1.0):
        return {"actionLibraryList": [{
            "sort": i + 1, "title": f"ex {i + 1}", "actionLibraryId": a["actionLibraryId"],
            "templatePresetId": a["templatePresetId"], "setsAndReps": a["setsAndReps"],
            "weights": ",".join(f"{float(w) / weight_divisor:.1f}" for w in a["weights"].split(",")),
            "level": a["level"], "leftRight": a["leftRight"], "breakTime2": a["breakTime2"]}
            for i, a in enumerate(body["actionLibraryList"])]}

    def test_verified_round_trip(self):
        body = writes.build_template("x", [ROW, ONE_ARM, VITA], unit="lb", device_type=1)
        self.assertEqual(writes.verify(body, self.stored(body)), [])

    def test_shrunken_weights_are_caught(self):
        body = writes.build_template("x", [ROW], unit="lb", device_type=1)
        problems = writes.verify(body, self.stored(body, weight_divisor=2.2))
        self.assertEqual(len(problems), 1)
        self.assertIn("weights", problems[0])

    def test_count_mismatch(self):
        body = writes.build_template("x", [ROW], unit="lb", device_type=1)
        self.assertIn("stored 0", writes.verify(body, {"actionLibraryList": []})[0])

    def test_read_template_and_sets_for_kind(self):
        detail = {"id": 1, "code": "c", "name": "n", "durationMinute": 20, "actionLibraryList": [
            {"sort": 1, "title": "Row", "actionLibraryId": 3210, "templatePresetId": -1, "setsAndReps": "12,10",
             "weights": "30.0,40.0", "level": "0,0", "leftRight": "0,0", "breakTime2": "60,90"}]}
        exercise = writes.read_template(detail)["exercises"][0]
        self.assertEqual(exercise["sets"][1], {"count": 10, "weight": 40.0, "level": 0, "side": None, "rest": 90})
        self.assertEqual(writes.sets_for_kind("reps", exercise["sets"])[0],
                         {"reps": 12, "weight": 30.0, "side": None, "rest": 60})

    def test_read_template_preserves_sport_mode_and_completion_method(self):
        detail = {"id": 1, "code": "c", "name": "n", "durationMinute": 20, "actionLibraryList": [
            {"sort": 1, "title": "Row", "actionLibraryId": 3210, "templatePresetId": -1, "setsAndReps": "12,10",
             "weights": "30.0,40.0", "level": "0,0", "leftRight": "0,0", "breakTime2": "60,90",
             "sportMode": "3,3", "selectCompletionMethod": "4,4"}]}
        exercise = writes.read_template(detail)["exercises"][0]
        self.assertEqual(exercise["sportMode"], "3,3")
        self.assertEqual(exercise["selectCompletionMethod"], "4,4")

    def test_build_template_preserves_matching_sport_mode(self):
        spec = dict(ROW, sportMode="3,3", selectCompletionMethod="4,4")
        action = writes.build_template("x", [spec], unit="lb", device_type=1)["actionLibraryList"][0]
        self.assertEqual((action["sportMode"], action["selectCompletionMethod"]), ("3,3", "4,4"))

    def test_build_template_defaults_sport_mode_when_count_mismatches(self):
        spec = dict(ROW, sportMode="3", selectCompletionMethod="4")  # only 1 entry, ROW has 2 sets
        action = writes.build_template("x", [spec], unit="lb", device_type=1)["actionLibraryList"][0]
        self.assertEqual((action["sportMode"], action["selectCompletionMethod"]), ("1,1", "1,1"))

    def test_build_template_defaults_sport_mode_when_absent(self):
        action = writes.build_template("x", [ROW], unit="lb", device_type=1)["actionLibraryList"][0]
        self.assertEqual((action["sportMode"], action["selectCompletionMethod"]), ("1,1", "1,1"))


class TestValidateWeights(unittest.TestCase):
    def test_valid_weights_keep_working(self):
        for weight in (50, 50.0, "50", 12.5):
            out = writes.validate_sets("reps", [{"reps": 8, "weight": weight}])
            self.assertEqual(out[0]["weight"], float(weight))

    def test_non_numeric_weight_string_rejected(self):
        with self.assertRaises(ValueError):
            writes.validate_sets("reps", [{"reps": 8, "weight": "50 lb"}])

    def test_missing_weight_rejected(self):
        with self.assertRaises(ValueError):
            writes.validate_sets("reps", [{"reps": 8}])

    def test_none_weight_rejected(self):
        with self.assertRaises(ValueError):
            writes.validate_sets("reps", [{"reps": 8, "weight": None}])

    def test_nan_weight_rejected(self):
        with self.assertRaises(ValueError):
            writes.validate_sets("reps", [{"reps": 8, "weight": float("nan")}])

    def test_inf_weight_rejected(self):
        with self.assertRaises(ValueError):
            writes.validate_sets("reps", [{"reps": 8, "weight": float("inf")}])

    def test_bool_weight_rejected(self):
        with self.assertRaises(ValueError):
            writes.validate_sets("reps", [{"reps": 8, "weight": True}])

    def test_weight_over_1000_rejected(self):
        with self.assertRaises(ValueError):
            writes.validate_sets("reps", [{"reps": 8, "weight": 1000.1}])

    def test_weight_below_zero_rejected(self):
        with self.assertRaises(ValueError):
            writes.validate_sets("reps", [{"reps": 8, "weight": -0.1}])

    def test_weight_with_two_decimal_places_rejected(self):
        with self.assertRaises(ValueError) as cm:
            writes.validate_sets("reps", [{"reps": 8, "weight": 22.25}])
        self.assertIn("decimal place", str(cm.exception))


class TestVerifyStricter(unittest.TestCase):
    def test_wrong_action_library_id_is_caught(self):
        body = writes.build_template("x", [ROW], unit="lb", device_type=1)
        a = body["actionLibraryList"][0]
        stored = {"actionLibraryList": [{
            "sort": 1, "title": "ex 1", "actionLibraryId": 9999, "templatePresetId": a["templatePresetId"],
            "setsAndReps": a["setsAndReps"], "weights": a["weights"], "level": a["level"],
            "leftRight": a["leftRight"], "breakTime2": a["breakTime2"]}]}
        problems = writes.verify(body, stored)
        self.assertTrue(any("9999" in p for p in problems))

    def test_rest_change_is_caught(self):
        body = writes.build_template("x", [ROW], unit="lb", device_type=1)
        a = dict(body["actionLibraryList"][0])
        stored = {"actionLibraryList": [{
            "sort": 1, "title": "ex 1", "actionLibraryId": a["actionLibraryId"],
            "templatePresetId": a["templatePresetId"], "setsAndReps": a["setsAndReps"],
            "weights": a["weights"], "level": a["level"], "leftRight": a["leftRight"],
            "breakTime2": "90,90"}]}
        problems = writes.verify(body, stored)
        self.assertTrue(any("rest" in p for p in problems))

    def test_dropped_vita_level_is_caught(self):
        body = writes.build_template("x", [VITA], unit="lb", device_type=1)
        a = body["actionLibraryList"][0]
        stored = {"actionLibraryList": [{
            "sort": 1, "title": "ex 1", "actionLibraryId": a["actionLibraryId"],
            "templatePresetId": a["templatePresetId"], "setsAndReps": a["setsAndReps"],
            "weights": a["weights"], "level": "", "leftRight": a["leftRight"], "breakTime2": a["breakTime2"]}]}
        problems = writes.verify(body, stored)
        self.assertTrue(any("level" in p for p in problems))

    def test_dropped_sides_is_caught(self):
        body = writes.build_template("x", [ONE_ARM], unit="lb", device_type=1)
        a = body["actionLibraryList"][0]
        stored = {"actionLibraryList": [{
            "sort": 1, "title": "ex 1", "actionLibraryId": a["actionLibraryId"],
            "templatePresetId": a["templatePresetId"], "setsAndReps": a["setsAndReps"],
            "weights": a["weights"], "level": a["level"], "leftRight": "", "breakTime2": a["breakTime2"]}]}
        problems = writes.verify(body, stored)
        self.assertTrue(any("side" in p for p in problems))

    def test_preset_id_change_is_caught_when_stored_field_present(self):
        body = writes.build_template("x", [ROW], unit="lb", device_type=1)
        a = body["actionLibraryList"][0]
        stored = {"actionLibraryList": [{
            "sort": 1, "title": "ex 1", "actionLibraryId": a["actionLibraryId"], "templatePresetId": 1,
            "setsAndReps": a["setsAndReps"], "weights": a["weights"], "level": a["level"],
            "leftRight": a["leftRight"], "breakTime2": a["breakTime2"]}]}
        problems = writes.verify(body, stored)
        self.assertTrue(any("preset" in p for p in problems))
