"""The curated fact tools: remember_fact, forget_fact, list_facts, import_facts and the grouped reads."""

from __future__ import annotations

import asyncio
import unittest

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp.server import build_server
from speediance_mcp.tools import coaching, memory_tools
from tests.helpers import make_app

HOTEL = "the hotel stack moves in 5 lb steps to 60"
GROUPS = {"constraints", "preferences", "goals", "observations", "conflicts", "legacyToReview", "legacyUnreviewed"}


def add_legacy(app, text, category="injury", created="2026-08-01T10:00:00Z") -> int:
    """Put a row in the old `facts` table and run the (idempotent) migration, as a server restart would."""
    app.memory._db.execute("INSERT INTO facts (fact, category, created_at) VALUES (?, ?, ?)", (text, category, created))
    app.memory._db.commit()
    app.memory.migrate_legacy()
    return next(f["id"] for f in app.memory.list_facts(include_archived=True) if f["text"] == text)


def call(app, name, args):
    """Call a tool through the MCP server, as a client would."""
    return asyncio.run(build_server(app).call_tool(name, args))


class TestFactTools(unittest.TestCase):
    def setUp(self):
        self.app, _ = make_app(self)

    def remember(self, text, kind="observation", category="note", **kw):
        return memory_tools.remember_fact(self.app, text, kind=kind, category=category, **kw)

    def test_acceptance_1_tool_rejects_oversized_fact_with_count(self):
        with self.assertRaises(ToolError) as ctx:
            self.remember("z" * 1213)
        self.assertEqual(str(ctx.exception),
                         "Fact is 1,213 characters; the limit is 600. Split it into smaller facts.")
        self.assertEqual(memory_tools.list_facts(self.app, include_archived=True)["count"], 0)

    def test_acceptance_1_error_reaches_the_client(self):
        with self.assertRaises(Exception) as ctx:
            call(self.app, "remember_fact", {"text": "z" * 1200, "kind": "goal", "category": "note"})
        self.assertIn("Fact is 1,200 characters; the limit is 600", str(ctx.exception))

    def test_acceptance_2_tool_returns_near_match_not_a_second_fact(self):
        first = self.remember(HOTEL, kind="constraint", severity="hard", category="equipment", source="user")
        second = self.remember(HOTEL, kind="constraint", severity="hard", category="equipment", source="user")
        self.assertTrue(first["saved"])
        self.assertFalse(second["saved"])
        self.assertEqual(second["nearMatch"]["id"], first["fact"]["id"])
        self.assertIn(f"supersedes=[{first['fact']['id']}]", second["message"])
        self.assertEqual(memory_tools.list_facts(self.app)["count"], 1)

    def test_acceptance_3_tool_supersede_then_default_read(self):
        a = self.remember("Goal: bench 135 lb", kind="goal")["fact"]
        b = self.remember("Goal: bench 155 lb by March", kind="goal", supersedes=[a["id"]])
        self.assertEqual(b["superseded"], [a["id"]])
        goals = memory_tools.get_preferences(self.app)["facts"]["goals"]
        self.assertEqual([g["id"] for g in goals], [b["fact"]["id"]])

    def test_acceptance_4_tool_rejects_constraint_without_severity(self):
        with self.assertRaises(ToolError) as ctx:
            self.remember("No overhead pressing", kind="constraint", category="injury")
        self.assertIn("severity", str(ctx.exception))

    def test_acceptance_5_import_tool_dry_run(self):
        batch = [{"text": HOTEL, "kind": "constraint", "severity": "hard", "category": "equipment"},
                 {"text": "x" * 601, "kind": "goal", "category": "note"}]
        dry = memory_tools.import_facts(self.app, batch, dry_run=True)
        self.assertEqual(memory_tools.list_facts(self.app, include_archived=True)["count"], 0)
        live = memory_tools.import_facts(self.app, batch)
        self.assertEqual({k: v for k, v in dry.items() if k != "dryRun"},
                         {k: v for k, v in live.items() if k != "dryRun"})
        self.assertEqual((dry["dryRun"], live["dryRun"]), (True, False))
        self.assertEqual(memory_tools.list_facts(self.app)["count"], 1)

    def test_acceptance_6_import_tool_dedupes_within_batch(self):
        report = memory_tools.import_facts(self.app, [
            {"text": HOTEL, "kind": "constraint", "severity": "hard", "category": "equipment"},
            {"text": "The hotel stack moves in 5-lb steps to 60!", "kind": "constraint", "severity": "hard",
             "category": "equipment"}])
        self.assertEqual((len(report["accepted"]), len(report["deduped"])), (1, 1))

    def test_acceptance_7_default_reads_exclude_archived_and_expired(self):
        gone = self.remember("Prefers mornings", kind="preference", category="schedule")["fact"]
        memory_tools.forget_fact(self.app, gone["id"])
        self.remember("Hotel gym only this week", kind="constraint", severity="hard", category="equipment",
                      expires_days=0)
        prefs = memory_tools.get_preferences(self.app)["facts"]
        self.assertEqual(prefs["preferences"], [])
        self.assertEqual(len(prefs["constraints"]["hard"]), 1)
        self.assertEqual(memory_tools.list_facts(self.app)["count"], 1)
        self.assertEqual(memory_tools.list_facts(self.app, include_archived=True)["count"], 2)

    def test_acceptance_8_conflicts_surface_in_both_reads(self):
        a = self.remember("Pallof press score is 6 of 8", category="body")["fact"]
        b = self.remember("Pallof press score after the clinical screen: 8 of 8", category="body")["fact"]
        for facts in (memory_tools.get_preferences(self.app)["facts"],
                      coaching.get_athlete_snapshot(self.app)["memory"]["facts"]):
            self.assertEqual([c["ids"] for c in facts["conflicts"]], [[a["id"], b["id"]]])

    def test_acceptance_9_list_facts_tool_full_history(self):
        a = self.remember("Rows 40 lb", category="body")["fact"]
        b = self.remember("Rows 50 lb for eights", category="body", supersedes=[a["id"]])["fact"]
        got = memory_tools.list_facts(self.app, include_archived=True)
        self.assertEqual([(f["id"], f["supersededBy"]) for f in got["facts"]], [(a["id"], b["id"]), (b["id"], None)])
        self.assertEqual(memory_tools.list_facts(self.app, kind="observation")["facts"][0]["id"], b["id"])
        with self.assertRaises(ToolError):
            memory_tools.list_facts(self.app, kind="rule")

    def test_grouped_blocks_in_both_reads(self):
        self.remember("No overhead pressing", kind="constraint", severity="hard", category="injury")
        prefs = memory_tools.get_preferences(self.app)
        snap = coaching.get_athlete_snapshot(self.app)
        self.assertEqual(set(prefs["facts"]), GROUPS)
        self.assertEqual(prefs["facts"], snap["memory"]["facts"])
        self.assertEqual(set(prefs["facts"]["constraints"]), {"hard", "soft"})
        # structured preferences and exercise marks are untouched
        self.assertIn("goal", prefs)
        self.assertIn("avoidedExercises", prefs)
        self.assertIn("load_anchors", snap["memory"]["preferences"])

    def test_forget_fact_errors(self):
        with self.assertRaises(ToolError) as ctx:
            memory_tools.forget_fact(self.app, 404)
        self.assertEqual(str(ctx.exception), "No fact with id 404.")
        with self.assertRaises(ToolError):
            memory_tools.forget_fact(self.app, "abc")
        fact = self.remember("Something", kind="goal")["fact"]
        memory_tools.forget_fact(self.app, fact["id"])
        with self.assertRaises(ToolError) as ctx:
            memory_tools.forget_fact(self.app, fact["id"])
        self.assertIn("already archived", str(ctx.exception))

    def test_forget_fact_by_id_through_the_server(self):
        fact = self.remember("Something", kind="goal")["fact"]
        call(self.app, "forget_fact", {"id": fact["id"]})
        self.assertEqual(memory_tools.list_facts(self.app)["count"], 0)

    def test_import_tool_errors(self):
        with self.assertRaises(ToolError):
            memory_tools.import_facts(self.app, [{"text": "x"}] * 201)

    def test_tool_schemas(self):
        tools = {t.name: t for t in asyncio.run(build_server(self.app).list_tools())}

        def schema(name):
            tool = tools[name]
            return tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema

        remember = schema("remember_fact")
        self.assertEqual(set(remember["properties"]),
                         {"text", "kind", "category", "severity", "scope", "supersedes", "expires_days", "source"})
        self.assertEqual(set(remember["required"]), {"text", "kind", "category"})
        self.assertEqual(set(schema("forget_fact")["properties"]), {"id"})
        self.assertEqual(set(schema("list_facts")["properties"]), {"kind", "category", "include_archived"})
        self.assertEqual(set(schema("import_facts")["properties"]), {"facts", "dry_run"})


class TestFixRound1Tools(unittest.TestCase):
    def setUp(self):
        self.app, _ = make_app(self)

    def reads(self):
        return (memory_tools.get_preferences(self.app)["facts"],
                coaching.get_athlete_snapshot(self.app)["memory"]["facts"])

    def test_item1_pending_legacy_injury_in_both_reads_until_superseded(self):
        legacy = add_legacy(self.app, "Left shoulder impingement - no overhead pressing")
        for facts in self.reads():
            self.assertEqual(facts["legacyUnreviewed"],
                             [{"id": legacy, "text": "Left shoulder impingement - no overhead pressing",
                               "category": "injury", "createdAt": "2026-08-01T10:00:00Z"}])
        memory_tools.remember_fact(self.app, "No overhead pressing (left shoulder impingement)", kind="constraint",
                                   severity="hard", category="injury", source="user", supersedes=[legacy])
        for facts in self.reads():
            self.assertEqual(facts["legacyUnreviewed"], [])

    def test_item1_pending_legacy_injury_disappears_after_forget(self):
        legacy = add_legacy(self.app, "Right knee tweak")
        self.assertEqual(len(self.reads()[1]["legacyUnreviewed"]), 1)
        memory_tools.forget_fact(self.app, legacy)
        for facts in self.reads():
            self.assertEqual(facts["legacyUnreviewed"], [])

    def test_item1_docstrings_say_legacy_may_still_bind(self):
        for fn in (memory_tools.get_preferences, coaching.get_athlete_snapshot):
            self.assertIn("legacyUnreviewed", fn.__doc__)
            self.assertIn("injury", fn.__doc__)

    def test_item2_tool_refusal_names_a_changed_number(self):
        a = memory_tools.remember_fact(self.app, "Goal: squat 185 lb by December", kind="goal", category="note")
        got = memory_tools.remember_fact(self.app, "Goal: squat 205 lb by December", kind="goal", category="note")
        self.assertFalse(got["saved"])
        self.assertIn("numbers differ: 185 vs 205", got["message"])
        self.assertIn(f"supersedes=[{a['fact']['id']}]", got["message"])

    def test_item5_tool_rejects_bad_expires_days(self):
        for bad in (True, 4000, "soon"):
            with self.subTest(bad=bad), self.assertRaises(ToolError) as ctx:
                memory_tools.remember_fact(self.app, "Temp", kind="goal", category="note", expires_days=bad)
            self.assertIn("expires_days", str(ctx.exception))

    def test_item10_tool_rejects_non_string_text(self):
        with self.assertRaises(ToolError) as ctx:
            memory_tools.remember_fact(self.app, 123, kind="goal", category="note")
        self.assertIn("text must be a string", str(ctx.exception))
        report = memory_tools.import_facts(self.app, [{"text": ["x"], "kind": "goal", "category": "note"}])
        self.assertEqual(len(report["rejected"]), 1)


class TestFixRound2Tools(unittest.TestCase):
    def test_r2_item2_expires_days_is_strict_at_the_tool_boundary(self):
        app, _ = make_app(self)
        for bad in (True, "7", 2.5):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                call(app, "remember_fact", {"text": "Travelling next week", "kind": "observation",
                                            "category": "schedule", "expires_days": bad})
        self.assertEqual(memory_tools.list_facts(app, include_archived=True)["count"], 0)
        call(app, "remember_fact", {"text": "Travelling next week", "kind": "observation", "category": "schedule",
                                    "expires_days": 7})
        self.assertEqual(memory_tools.list_facts(app)["facts"][0]["expiresAt"] is not None, True)


if __name__ == "__main__":
    unittest.main()
