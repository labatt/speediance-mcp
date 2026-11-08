"""RM loads: per-set "rm" at one of the app's RM presets, resolved to a load by Speediance."""
from __future__ import annotations

import copy
import dataclasses
import json
import unittest

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp.speediance import writes
from speediance_mcp.tools import workouts
from tests import fixtures as fx
from tests.helpers import CREDS, make_app
from tests.test_tools_workouts import TemplateStore

ROW = {"groupId": 321, "variantId": 3210, "name": "Row", "kind": "reps", "unilateral": False}
RM_SETS = [{"reps": 5, "weight": 3.5, "side": None, "rest": 150, "mode": 1, "rm": 8},
           {"reps": 5, "weight": 3.5, "side": None, "rest": 150, "mode": 2, "rm": 8}]


def echo(body, **overrides):
    """What Speediance stores for a display-unit body, as the detail endpoint returns it."""
    return {"actionLibraryList": [dict({
        "sort": i + 1, "title": f"ex {i + 1}", "actionLibraryId": a["actionLibraryId"],
        "templatePresetId": a["templatePresetId"], "setsAndReps": a["setsAndReps"], "weights": a["weights"],
        "level": a["level"], "leftRight": a["leftRight"], "breakTime2": a["breakTime2"],
        "sportMode": a["sportMode"], "counterweight2": a["counterweight2"]}, **overrides)
        for i, a in enumerate(body["actionLibraryList"])]}


class TestValidate(unittest.TestCase):
    def test_rm_set_gets_a_placeholder_or_rounded_fallback_weight(self):
        got = writes.validate_sets("reps", [{"reps": 5, "rm": 8}, {"reps": 5, "rm": "8", "weight": 61.27}], unit="kg")
        self.assertEqual(got[0], {"reps": 5, "weight": writes.RM_PLACEHOLDER_WEIGHT, "side": None, "rest": 60,
                                  "mode": 1, "rm": 8})
        self.assertEqual(got[1]["weight"], 61.3)  # a read-back load; the kg grid doesn't apply to a fallback

    def test_rm_errors(self):
        for raw in ({"reps": 8, "rm": 6}, {"reps": 5, "rm": 0}, {"reps": 5, "rm": 31}, {"reps": 5, "rm": 8.5}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                writes.validate_sets("reps", [raw])
        with self.assertRaises(ValueError):
            writes.validate_sets("timed", [{"seconds": 30, "rm": 8}])

    def test_weight_sets_unchanged(self):
        self.assertNotIn("rm", writes.validate_sets("reps", [{"reps": 8, "weight": 50}])[0])


class TestPreset(unittest.TestCase):
    def pick(self, reps, rm, requested=None):
        return writes.rm_preset_for([{"reps": reps, "rm": rm}], requested)

    def test_picked_from_the_ranges(self):
        self.assertEqual(self.pick(5, 8), 5)      # strength
        self.assertEqual(self.pick(10, 12), 1)    # gain muscle
        self.assertEqual(self.pick(15, 17), 3)    # stamina
        self.assertEqual(self.pick(8, 9), 1)      # fits two: first in RM_PRESETS order
        self.assertEqual(self.pick(8, 9, "strength"), 5)
        self.assertEqual(self.pick(8, 9, "5"), 5)
        self.assertIsNone(writes.rm_preset_for([{"reps": 8, "weight": 50.0}]))

    def test_errors(self):
        cases = [([{"reps": 12, "rm": 14}], None),                          # gain muscle tops out at 13RM
                 ([{"reps": 5, "rm": 8}, {"reps": 5, "weight": 50.0}], None),  # mixed RM and weight sets
                 ([{"reps": 5, "rm": 8}], "gain_muscle"),                   # 5 reps isn't a gain-muscle set
                 ([{"reps": 5, "rm": 8}], "power"),
                 ([{"reps": 5, "weight": 50.0}], "strength")]
        for sets, requested in cases:
            with self.subTest(sets=sets, requested=requested), self.assertRaises(ValueError):
                writes.rm_preset_for(sets, requested)


class TestBuildReadVerify(unittest.TestCase):
    def body(self, unit="kg"):
        spec = dict(ROW, sets=RM_SETS, rmPreset=5)
        return writes.build_template("x", [spec], unit=unit, device_type=1)

    def test_build_writes_the_preset_and_rms(self):
        action = self.body()["actionLibraryList"][0]
        self.assertEqual((action["templatePresetId"], action["counterweight2"], action["sportMode"]), (5, "8,8", "1,2"))

    def test_wire_body_leaves_rm_movements_in_kg(self):
        wired = writes.wire_body(self.body(), "kg")["actionLibraryList"][0]
        self.assertEqual(wired["weights"], "3.5,3.5")  # only non-positive presets go out as pounds

    def test_weight_movements_keep_customize_and_no_rm(self):
        spec = dict(ROW, sets=[{"reps": 8, "weight": 50.0, "side": None, "rest": 60, "mode": 1}])
        action = writes.build_template("x", [spec], unit="kg", device_type=1)["actionLibraryList"][0]
        self.assertEqual((action["templatePresetId"], action["counterweight2"]), (writes.CUSTOMIZE_PRESET, ""))

    def test_verify_checks_the_rm_not_the_load_speediance_worked_out(self):
        body = self.body()
        self.assertEqual(writes.verify(body, echo(body, weights="9.0,9.0")), [])
        self.assertTrue(any("RM" in p for p in writes.verify(body, echo(body, counterweight2=""))))

    def test_read_back_shows_the_rm(self):
        body = self.body()
        exercise = writes.read_template(echo(body, weights="61.27,61.27"))["exercises"][0]
        self.assertEqual((exercise["presetName"], exercise["sets"][0]["rm"]), ("Strength", 8))
        self.assertEqual(writes.sets_for_kind("reps", exercise["sets"])[1],
                         {"reps": 5, "weight": 61.27, "side": None, "rest": 150, "mode": 2, "rm": 8})


class RMStore(TemplateStore):
    """Like the server: keeps counterweight2 and replaces an RM movement's weights with a load."""

    def save(self, request):
        TemplateStore.save(self, request)
        body = json.loads(request.content)
        row = next(r for r in self.rows if r["name"] == body["name"])
        for sent, stored in zip(body["actionLibraryList"], self.details[row["code"]]["actionLibraryList"]):
            stored["counterweight2"] = sent["counterweight2"]
            if sent["counterweight2"]:
                stored["weights"] = ",".join("61.27" for _ in sent["counterweight2"].split(","))
        return True


RM_ROW = [{"name": "bent over row", "sets": [{"reps": 6, "rm": 9}, {"reps": 6, "rm": 9, "mode": "eccentric"}]}]


class TestTools(unittest.TestCase):
    def make(self, unit="kg"):
        store = RMStore(kg_server=unit == "kg")
        app, fake = make_app(self, store.routes(), creds=dataclasses.replace(CREDS, unit=unit))
        return store, app, fake

    def test_create_with_rm_is_verified_and_reads_back(self):
        store, app, _ = self.make()
        got = workouts.create_workout(app, "Pull RM", RM_ROW)
        self.assertTrue(got["verified"], got.get("mismatches"))
        action = store.posts[0]["actionLibraryList"][0]
        self.assertEqual((action["templatePresetId"], action["counterweight2"], action["sportMode"]), (5, "9,9", "1,3"))
        shown = workouts.get_workout(app, got["code"])["exercises"][0]
        self.assertEqual((shown["presetName"], shown["sets"][1]["rm"], shown["sets"][1]["mode"]),
                         ("Strength", 9, "eccentric"))

    def test_get_workout_output_round_trips_through_update(self):
        store, app, _ = self.make()
        code = workouts.create_workout(app, "Pull RM", RM_ROW)["code"]
        sets = workouts.get_workout(app, code)["exercises"][0]["sets"]
        got = workouts.update_workout(app, code, exercises=[{"group_id": 321, "sets": sets}])
        self.assertTrue(got["verified"], got.get("mismatches"))
        self.assertEqual(store.posts[1]["actionLibraryList"][0]["counterweight2"], "9,9")

    def test_outside_every_preset_is_refused_before_writing(self):
        _, app, fake = self.make()
        with self.assertRaises(ToolError) as cm:
            workouts.create_workout(app, "Bad", [{"name": "bent over row", "sets": [{"reps": 12, "rm": 14}]}])
        self.assertIn("RM preset", str(cm.exception))
        self.assertEqual(fake.calls("POST", fx.SAVE_TEMPLATE_PATH), [])

    def test_rename_rebuilds_an_rm_movement(self):
        store, app, _ = self.make()
        detail = copy.deepcopy(fx.TEMPLATE_9001)
        detail["actionLibraryList"][0].update(templatePresetId=5, counterweight2="8,8", weights="61.27,61.27",
                                              setsAndReps="5,5", sportMode="1,2")
        store.details[fx.TEMPLATES[0]["code"]] = detail
        got = workouts.update_workout(app, "a" * 24, name="Pull Day v2")
        self.assertTrue(got["verified"], got.get("mismatches"))
        action = store.posts[0]["actionLibraryList"][0]
        self.assertEqual((action["templatePresetId"], action["counterweight2"], action["sportMode"]), (5, "8,8", "1,2"))

    def test_rename_still_refuses_a_partial_or_unknown_rm(self):
        for overrides in ({"templatePresetId": 5, "counterweight2": "8,"},
                          {"templatePresetId": 8, "counterweight2": "8,8"},
                          {"templatePresetId": 1, "counterweight2": "8,"}):
            with self.subTest(overrides=overrides):
                store, app, fake = self.make()
                detail = copy.deepcopy(fx.TEMPLATE_9001)
                detail["actionLibraryList"][0].update(overrides)
                store.details[fx.TEMPLATES[0]["code"]] = detail
                with self.assertRaises(ToolError):
                    workouts.update_workout(app, "a" * 24, name="Pull Day v2")
                self.assertEqual(fake.calls("POST", fx.SAVE_TEMPLATE_PATH), [])
