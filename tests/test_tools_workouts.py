from __future__ import annotations

import copy
import json
import unittest

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp.tools import workouts
from tests import fixtures as fx
from tests.helpers import api_error, make_app


class TemplateStore:
    """Stateful fake of the template endpoints. `weight_divisor` simulates a server that shrinks loads."""

    def __init__(self, weight_divisor=1.0):
        self.rows = copy.deepcopy(fx.TEMPLATES)
        self.details = {fx.TEMPLATES[0]["code"]: copy.deepcopy(fx.TEMPLATE_9001)}
        self.next_id = 9002
        self.weight_divisor = weight_divisor
        self.posts = []

    def save(self, request):
        body = json.loads(request.content)
        self.posts.append(body)
        if "id" in body:
            row = next(r for r in self.rows if r["id"] == body["id"])
            row["name"] = body["name"]
        else:
            row = {"id": self.next_id, "code": f"{self.next_id:024d}", "name": body["name"],
                   "actionNum": len(body["actionLibraryList"]), "durationMinute": 20}
            self.rows.append(row)
            self.next_id += 1
        self.details[row["code"]] = {"id": row["id"], "code": row["code"], "name": body["name"], "actionLibraryList": [
            {"sort": i + 1, "title": f"ex {i + 1}", "actionLibraryId": a["actionLibraryId"],
             "templatePresetId": a["templatePresetId"], "setsAndReps": a["setsAndReps"],
             "weights": ",".join(f"{float(w) / self.weight_divisor:.1f}" for w in a["weights"].split(",")),
             "level": a["level"], "leftRight": a["leftRight"], "breakTime2": a["breakTime2"]}
            for i, a in enumerate(body["actionLibraryList"])]}
        return True

    def delete(self, request):
        target = int(request.url.params["ids"])
        self.rows = [r for r in self.rows if r["id"] != target]
        return True

    def routes(self):
        table = fx.standard_routes()
        table[("GET", fx.TEMPLATES_PATH)] = lambda req: self.rows
        table[("GET", fx.TEMPLATE_DETAIL_PATH)] = lambda req: self.details.get(req.url.params.get("code"))
        table[("POST", fx.SAVE_TEMPLATE_PATH)] = self.save
        table[("DELETE", fx.DELETE_TEMPLATE_PATH)] = self.delete
        return table


PUSH = [{"name": "bent over row", "sets": [{"reps": 8, "weight": 50}, {"reps": 8, "weight": 50}]},
        {"group_id": 600, "sets": [{"reps": 10, "weight": 20}, {"reps": 10, "weight": 20}]},
        {"name": "vita row", "sets": [{"seconds": 30, "level": 12}]}]


class TestWorkoutTools(unittest.TestCase):
    def test_list_and_get(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        listed = workouts.list_my_workouts(app)
        self.assertEqual(listed["slots"], {"used": 1, "limit": None, "left": None})
        got = workouts.get_workout(app, "a" * 24)
        self.assertEqual(got["exercises"][0]["kind"], "reps")
        self.assertEqual(got["exercises"][0]["sets"][0], {"reps": 12, "weight": 30.0, "side": None, "rest": 60})
        with self.assertRaises(ToolError):
            workouts.get_workout(app, "nope")

    def test_create_verified(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        got = workouts.create_workout(app, "Push", PUSH)
        self.assertTrue(got["verified"])
        self.assertEqual((got["id"], got["exercises"]), (9002, 3))
        actions = store.posts[0]["actionLibraryList"]
        self.assertEqual((actions[0]["actionLibraryId"], actions[0]["weights"]), (3210, "50.0,50.0"))
        self.assertEqual(actions[1]["leftRight"], "1,2")
        self.assertEqual(actions[2]["level"], "12")

    def test_create_flags_avoided_exercises(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        app.memory.mark_exercise(600, "avoided", "Single Arm Cable Row", reason="left shoulder")
        got = workouts.create_workout(app, "Push", PUSH)
        self.assertEqual(got["avoidedExercises"],
                         [{"groupId": 600, "name": "Single Arm Cable Row", "reason": "left shoulder"}])
        self.assertIn("Single Arm Cable Row", got["avoidedWarning"])
        self.assertIn("Tell the user", got["avoidedWarning"])

    def test_create_no_avoided_key_when_nothing_avoided(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        got = workouts.create_workout(app, "Push", PUSH)
        self.assertNotIn("avoidedExercises", got)
        self.assertNotIn("avoidedWarning", got)

    def test_create_carries_hard_constraints_and_legacy_unreviewed(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        hard = app.memory.remember_fact("No overhead pressing", kind="constraint", severity="hard",
                                        category="injury")["fact"]
        app.memory.remember_fact("Prefers cables", kind="preference", category="equipment")
        app.memory._db.execute("INSERT INTO facts (fact, category, created_at) VALUES "
                               "('Knee tweak', 'injury', '2026-08-01T10:00:00Z')")
        app.memory._db.commit()
        app.memory.migrate_legacy()
        got = workouts.create_workout(app, "Push", PUSH)
        self.assertEqual(got["hardConstraints"], [{"id": hard["id"], "text": "No overhead pressing"}])
        self.assertEqual([(f["text"], f["category"]) for f in got["legacyUnreviewed"]], [("Knee tweak", "injury")])
        self.assertEqual(set(got["legacyUnreviewed"][0]), {"id", "text", "category", "createdAt"})
        self.assertIn("Re-check these against the workout before telling the user it's done.",
                      workouts.create_workout.__doc__)

    def test_create_no_constraint_keys_when_there_are_none(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        app.memory.remember_fact("Keep it short", kind="constraint", severity="soft", category="schedule")
        got = workouts.create_workout(app, "Push", PUSH)
        self.assertNotIn("hardConstraints", got)
        self.assertNotIn("legacyUnreviewed", got)

    def test_create_flags_shrunken_weights(self):
        store = TemplateStore(weight_divisor=2.2)
        app, _ = make_app(self, store.routes())
        got = workouts.create_workout(app, "Push", PUSH[:1])
        self.assertFalse(got["verified"])
        self.assertIn("weights", got["mismatches"][0])
        self.assertIn("warning", got)

    def test_ambiguous_name_writes_nothing(self):
        store = TemplateStore()
        app, fake = make_app(self, store.routes())
        with self.assertRaises(ToolError):
            workouts.create_workout(app, "Push", [{"name": "row", "sets": [{"reps": 8, "weight": 50}]}])
        self.assertEqual(fake.calls("POST", fx.SAVE_TEMPLATE_PATH), [])

    def test_bad_sets_write_nothing(self):
        store = TemplateStore()
        app, fake = make_app(self, store.routes())
        with self.assertRaises(ToolError):
            workouts.create_workout(app, "Push", [{"name": "vita row", "sets": [{"seconds": 30}]}])
        self.assertEqual(fake.calls("POST", fx.SAVE_TEMPLATE_PATH), [])

    def test_server_rejection_passes_through(self):
        store = TemplateStore()
        routes = store.routes()
        routes[("POST", fx.SAVE_TEMPLATE_PATH)] = api_error(5001, "Template limit reached")
        app, _ = make_app(self, routes)
        with self.assertRaises(ToolError) as cm:
            workouts.create_workout(app, "Push", PUSH[:1])
        self.assertIn("Template limit reached", str(cm.exception))
        self.assertIn("custom-workout limit", str(cm.exception))
        self.assertIn("ask the user before deleting", str(cm.exception))

    def test_rename_rebuilds_from_stored_detail(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        got = workouts.update_workout(app, "a" * 24, name="Pull Day v2")
        self.assertTrue(got["verified"])
        body = store.posts[0]
        self.assertEqual((body["id"], body["name"]), (9001, "Pull Day v2"))
        self.assertEqual(body["actionLibraryList"][0]["setsAndReps"], "12,10")

    def test_update_with_new_exercises(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        workouts.update_workout(app, "a" * 24, exercises=PUSH[:1])
        self.assertEqual(store.posts[0]["name"], "Pull Day")
        self.assertEqual(store.posts[0]["actionLibraryList"][0]["setsAndReps"], "8,8")

    def test_update_flags_avoided_exercises(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        app.memory.mark_exercise(600, "avoided", "Single Arm Cable Row", reason="left shoulder")
        got = workouts.update_workout(app, "a" * 24, exercises=PUSH)
        self.assertEqual(got["avoidedExercises"],
                         [{"groupId": 600, "name": "Single Arm Cable Row", "reason": "left shoulder"}])
        self.assertIn("Tell the user", got["avoidedWarning"])

    def test_update_carries_hard_constraints(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        hard = app.memory.remember_fact("No overhead pressing", kind="constraint", severity="hard",
                                        category="injury")["fact"]
        got = workouts.update_workout(app, "a" * 24, name="Pull Day v2")
        self.assertEqual(got["hardConstraints"], [{"id": hard["id"], "text": "No overhead pressing"}])
        self.assertNotIn("legacyUnreviewed", got)
        self.assertIn("Re-check these against the workout before telling the user it's done.",
                      workouts.update_workout.__doc__)

    def test_update_no_avoided_key_when_nothing_avoided(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        got = workouts.update_workout(app, "a" * 24, name="Pull Day v2")
        self.assertNotIn("avoidedExercises", got)
        self.assertNotIn("avoidedWarning", got)

    def test_delete(self):
        store = TemplateStore()
        app, fake = make_app(self, store.routes())
        self.assertEqual(workouts.delete_workout(app, "a" * 24), {"deleted": True, "code": "a" * 24, "name": "Pull Day"})
        self.assertEqual(fake.calls("DELETE", fx.DELETE_TEMPLATE_PATH)[0].url.params["ids"], "9001")
        with self.assertRaises(ToolError):
            workouts.delete_workout(app, "a" * 24)

    def test_numeric_id_handle(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        self.assertEqual(workouts.delete_workout(app, 9001)["code"], "a" * 24)

    def test_bad_weights_write_nothing(self):
        store = TemplateStore()
        app, fake = make_app(self, store.routes())
        for weight in ("50 lb", None, float("nan"), True, 22.25):
            with self.assertRaises(ToolError):
                workouts.create_workout(app, "Push", [{"name": "row", "sets": [{"reps": 8, "weight": weight}]}])
        self.assertEqual(fake.calls("POST", fx.SAVE_TEMPLATE_PATH), [])

    def test_create_row_never_appears_raises_and_posts_once(self):
        store = TemplateStore()
        routes = store.routes()
        posts = []

        def swallow(request):
            posts.append(request)
            return True  # server accepts the save, but the row never shows up in the list

        routes[("POST", fx.SAVE_TEMPLATE_PATH)] = swallow
        app, fake = make_app(self, routes)
        with self.assertRaises(ToolError):
            workouts.create_workout(app, "Push", PUSH[:1])
        self.assertEqual(len(fake.calls("POST", fx.SAVE_TEMPLATE_PATH)), 1)

    def test_update_happy_path_keeps_code_and_verifies(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        got = workouts.update_workout(app, "a" * 24, name="Pull Day v2")
        self.assertTrue(got["verified"])
        self.assertEqual(got["code"], "a" * 24)

    def test_update_turns_into_create_flags_new_template(self):
        store = TemplateStore()
        routes = store.routes()
        original_save = routes[("POST", fx.SAVE_TEMPLATE_PATH)]

        def save_as_create(request):
            body = json.loads(request.content)
            body.pop("id", None)
            fake_request = type("FakeRequest", (), {"content": json.dumps(body).encode()})()
            return original_save(fake_request)

        routes[("POST", fx.SAVE_TEMPLATE_PATH)] = save_as_create
        app, _ = make_app(self, routes)
        got = workouts.update_workout(app, "a" * 24, name="Pull Day v2")
        self.assertFalse(got["verified"])
        self.assertTrue(any("new template" in m for m in got["mismatches"]))

    def test_update_that_reissues_the_id_under_the_same_code_is_verified(self):
        # Live behaviour (2026-09-27): an edit re-inserts the row, so the template keeps its
        # code but gets a NEW numeric id. That is an in-place edit, not a duplicate.
        store = TemplateStore()
        routes = store.routes()
        original_save = routes[("POST", fx.SAVE_TEMPLATE_PATH)]

        def save_reissuing_id(request):
            result = original_save(request)
            row = next(r for r in store.rows if r["code"] == "a" * 24)
            store.details[row["code"]]["id"] = row["id"] = 9999
            return result

        routes[("POST", fx.SAVE_TEMPLATE_PATH)] = save_reissuing_id
        app, _ = make_app(self, routes)
        got = workouts.update_workout(app, "a" * 24, name="Pull Day v2")
        self.assertTrue(got["verified"], got.get("mismatches"))
        self.assertEqual(got["code"], "a" * 24)
        self.assertEqual(len(store.rows), 1)

    def test_rename_ignored_by_server_flags_mismatch(self):
        store = TemplateStore()
        routes = store.routes()

        def save_ignore_name(request):
            body = json.loads(request.content)
            body["name"] = "Pull Day"  # server silently keeps the old name
            fake_request = type("FakeRequest", (), {"content": json.dumps(body).encode()})()
            return store.save(fake_request)

        routes[("POST", fx.SAVE_TEMPLATE_PATH)] = save_ignore_name
        app, _ = make_app(self, routes)
        got = workouts.update_workout(app, "a" * 24, name="Pull Day v2")
        self.assertFalse(got["verified"])
        self.assertTrue(any("Pull Day v2" in m for m in got["mismatches"]))

    def test_rebuild_vita_empty_level_raises_before_write(self):
        store = TemplateStore()
        store.rows.append({"id": 9050, "code": "b" * 24, "name": "Vita Day", "actionNum": 1, "durationMinute": 10})
        store.details["b" * 24] = {
            "id": 9050, "code": "b" * 24, "name": "Vita Day", "durationMinute": 10,
            "actionLibraryList": [{"sort": 1, "title": "Vita Row", "actionLibraryId": 7000,
                                   "templatePresetId": -1, "setsAndReps": "30", "weights": "0", "level": "",
                                   "leftRight": "0", "breakTime2": "60"}],
        }
        app, fake = make_app(self, store.routes())
        with self.assertRaises(ToolError):
            workouts.update_workout(app, "b" * 24, name="Vita Day v2")
        self.assertEqual(fake.calls("POST", fx.SAVE_TEMPLATE_PATH), [])

    def test_sport_mode_preserved_through_rename(self):
        store = TemplateStore()
        detail = copy.deepcopy(fx.TEMPLATE_9001)
        detail["actionLibraryList"][0]["sportMode"] = "3,3"
        detail["actionLibraryList"][0]["selectCompletionMethod"] = "4,4"
        store.details[fx.TEMPLATES[0]["code"]] = detail
        app, _ = make_app(self, store.routes())
        workouts.update_workout(app, "a" * 24, name="Pull Day v2")
        body = store.posts[0]
        self.assertEqual(body["actionLibraryList"][0]["sportMode"], "3,3")
        self.assertEqual(body["actionLibraryList"][0]["selectCompletionMethod"], "4,4")

    def test_update_exercises_only_ignores_long_existing_name(self):
        store = TemplateStore()
        long_name = "x" * 90  # longer than MAX_NAME=60, as the app itself might allow
        for row in store.rows:
            if row["code"] == "a" * 24:
                row["name"] = long_name
        store.details["a" * 24]["name"] = long_name
        app, _ = make_app(self, store.routes())
        got = workouts.update_workout(app, "a" * 24, exercises=PUSH[:1])
        self.assertEqual(store.posts[0]["name"], long_name)
        self.assertTrue(got["verified"])

    def test_rest_seconds_zero_is_honoured(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        exercises = [{"name": "bent over row", "rest_seconds": 0,
                      "sets": [{"reps": 8, "weight": 50}, {"reps": 8, "weight": 50}]}]
        workouts.create_workout(app, "Push", exercises)
        self.assertEqual(store.posts[0]["actionLibraryList"][0]["breakTime2"], "0,0")

    def test_non_numeric_group_id_raises_tool_error(self):
        store = TemplateStore()
        app, fake = make_app(self, store.routes())
        with self.assertRaises(ToolError):
            workouts.create_workout(app, "Push", [{"group_id": "abc", "sets": [{"reps": 8, "weight": 50}]}])
        self.assertEqual(fake.calls("POST", fx.SAVE_TEMPLATE_PATH), [])

    def test_non_numeric_rest_seconds_raises_tool_error(self):
        store = TemplateStore()
        app, fake = make_app(self, store.routes())
        exercises = [{"name": "bent over row", "rest_seconds": "soon",
                      "sets": [{"reps": 8, "weight": 50}]}]
        with self.assertRaises(ToolError):
            workouts.create_workout(app, "Push", exercises)
        self.assertEqual(fake.calls("POST", fx.SAVE_TEMPLATE_PATH), [])


class TestRebuildRefusesPresetLoads(unittest.TestCase):
    def store_with(self, **action_overrides):
        store = TemplateStore()
        detail = copy.deepcopy(fx.TEMPLATE_9001)
        detail["actionLibraryList"][0].update(action_overrides)
        store.details[fx.TEMPLATES[0]["code"]] = detail
        return store

    def assert_refused(self, store):
        app, fake = make_app(self, store.routes())
        with self.assertRaises(ToolError) as cm:
            workouts.update_workout(app, "a" * 24, name="Pull Day v2")
        self.assertIn("pass the full exercises list", str(cm.exception))
        self.assertEqual(fake.calls("POST", fx.SAVE_TEMPLATE_PATH), [])
        return str(cm.exception)

    def test_app_preset_load_refuses_rename(self):
        message = self.assert_refused(self.store_with(templatePresetId=8, weights="0,0"))
        self.assertIn("preset", message)

    def test_app_preset_refuses_even_with_weights(self):
        self.assert_refused(self.store_with(templatePresetId=8))

    def test_missing_or_zero_weight_refuses_rename(self):
        for weights in ("0,0", "", "30.0,", "30.0,0"):
            with self.subTest(weights=weights):
                self.assert_refused(self.store_with(weights=weights))

    def test_explicit_presets_still_rebuild(self):
        for preset in (-1, 0, 1, None):
            with self.subTest(preset=preset):
                store = self.store_with(templatePresetId=preset)
                app, _ = make_app(self, store.routes())
                workouts.update_workout(app, "a" * 24, name="Pull Day v2")
                self.assertEqual(len(store.posts), 1)
