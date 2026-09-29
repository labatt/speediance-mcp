"""The curated fact store (docs/specs/2026-09-28-curated-facts.md), at the Memory level."""

from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path

from speediance_mcp.memory import FACT_TEXT_LIMIT, Memory

HOTEL = "the hotel stack moves in 5 lb steps to 60"


class CuratedBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "m.db"
        self.clock = {"now": dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.timezone.utc)}
        self.mem = self.open(self.path)

    def open(self, path: Path) -> Memory:
        mem = Memory(path, now=lambda: self.clock["now"])
        self.addCleanup(mem.close)
        return mem

    def tick(self, **delta) -> None:
        self.clock["now"] += dt.timedelta(**delta)

    def save(self, text, kind="observation", category="note", **kw) -> dict:
        got = self.mem.remember_fact(text, kind=kind, category=category, **kw)
        self.assertTrue(got["saved"], got)
        return got["fact"]

    def rows(self) -> int:
        return len(self.mem.list_facts(include_archived=True))


class TestAcceptance(CuratedBase):
    """The nine acceptance tests from the spec, one named test each."""

    def test_acceptance_1_oversized_fact_rejected_with_count_nothing_stored(self):
        text = "x" * 1200
        with self.assertRaises(ValueError) as ctx:
            self.mem.remember_fact(text, kind="observation", category="note")
        self.assertIn("1,200 characters", str(ctx.exception))
        self.assertIn(f"limit is {FACT_TEXT_LIMIT}", str(ctx.exception))
        self.assertIn("Split it", str(ctx.exception))
        self.assertEqual(self.rows(), 0)

    def test_acceptance_2_near_duplicate_six_seconds_apart_stores_one(self):
        first = self.mem.remember_fact(HOTEL, kind="constraint", severity="hard", category="equipment")
        self.tick(seconds=6)
        second = self.mem.remember_fact(HOTEL, kind="constraint", severity="hard", category="equipment")
        self.assertTrue(first["saved"])
        self.assertFalse(second["saved"])
        self.assertEqual(second["nearMatch"]["id"], first["fact"]["id"])
        self.assertEqual(second["nearMatch"]["text"], HOTEL)
        self.assertGreaterEqual(second["nearMatch"]["similarity"], 0.85)
        self.assertIn("supersedes", second["message"])
        self.assertEqual(self.rows(), 1)

    def test_acceptance_3_supersedes_archives_target_in_same_transaction(self):
        a = self.save("Pallof press anti-rotation score is 6 of 8", category="body")
        b = self.save("Pallof press anti-rotation score is 8 of 8", category="body", supersedes=[a["id"]])
        active = [f["id"] for f in self.mem.list_facts()]
        self.assertEqual(active, [b["id"]])
        history = {f["id"]: f for f in self.mem.list_facts(include_archived=True)}
        self.assertEqual(history[a["id"]]["supersededBy"], b["id"])
        self.assertEqual(history[a["id"]]["status"], "archived")
        self.assertEqual(history[b["id"]]["supersedes"], [a["id"]])
        digest = self.mem.fact_digest()
        self.assertEqual([o["id"] for o in digest["observations"]], [b["id"]])

    def test_acceptance_3b_supersede_rolls_back_when_the_write_fails(self):
        a = self.save("Squat depth limited by left ankle", category="injury")
        with self.assertRaises(ValueError):
            # the second target doesn't exist, so neither the write nor the archiving happens
            self.mem.remember_fact("Squat depth fine now", kind="observation", category="injury",
                                   supersedes=[a["id"], 999])
        self.assertEqual([f["id"] for f in self.mem.list_facts()], [a["id"]])
        self.assertIsNone(self.mem.list_facts()[0]["supersededBy"])

    def test_acceptance_4_constraint_without_severity_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.mem.remember_fact("No overhead pressing", kind="constraint", category="injury")
        self.assertIn("severity", str(ctx.exception))
        self.assertEqual(self.rows(), 0)

    def test_acceptance_5_import_dry_run_writes_nothing_and_matches_live_report(self):
        existing = self.save("Knee pain on walking lunges", kind="constraint", severity="soft", category="injury")
        batch = [
            {"text": "Trains before work, 45 minutes max", "kind": "constraint", "severity": "soft",
             "category": "schedule"},
            {"text": "Knee pain on walking lunges", "kind": "constraint", "severity": "soft", "category": "injury"},
            {"text": "No overhead pressing", "kind": "constraint", "category": "injury"},
            {"text": "Knee is fine on reverse lunges now", "kind": "observation", "category": "injury",
             "supersedes": [existing["id"]]},
        ]
        before = self.mem.list_facts(include_archived=True)
        dry = self.mem.import_facts(batch, dry_run=True)
        self.assertEqual(self.mem.list_facts(include_archived=True), before)
        live = self.mem.import_facts(batch)
        self.assertEqual(set(dry), {"accepted", "deduped", "rejected", "superseded", "dryRun"})
        self.assertTrue(dry.pop("dryRun"))
        self.assertFalse(live.pop("dryRun"))
        self.assertEqual(dry, live)
        self.assertEqual(len(live["accepted"]), 2)
        self.assertEqual(live["deduped"][0]["matched_existing_id"], existing["id"])
        self.assertIn("severity", live["rejected"][0]["reason"])
        self.assertEqual(live["superseded"], [existing["id"]])

    def test_acceptance_6_import_batch_with_two_near_identical_items(self):
        report = self.mem.import_facts([
            {"text": HOTEL, "kind": "constraint", "severity": "hard", "category": "equipment"},
            {"text": "The hotel stack moves in 5 lb steps to 60.", "kind": "constraint", "severity": "hard",
             "category": "equipment"},
        ])
        self.assertEqual(len(report["accepted"]), 1)
        self.assertEqual(len(report["deduped"]), 1)
        self.assertEqual(report["deduped"][0]["matched_existing_id"], report["accepted"][0]["id"])
        self.assertEqual(report["deduped"][0]["input"]["text"], "The hotel stack moves in 5 lb steps to 60.")
        self.assertEqual(self.rows(), 1)

    def test_acceptance_7_default_read_excludes_archived_and_expired(self):
        keep = self.save("Trains Monday and Thursday", kind="constraint", severity="soft", category="schedule")
        forgotten = self.save("Prefers morning sessions", kind="preference", category="schedule")
        temp = self.save("Travelling to Tampa next week", kind="constraint", severity="hard", category="schedule",
                         expires_days=7, scope="location:tampa-hotel")
        old = self.save("Deadlift form breaks at 100", category="body")
        self.save("Deadlift form breaks down above 120 lb now", category="body", supersedes=[old["id"]])
        self.mem.forget_fact(forgotten["id"])
        self.assertIn(temp["id"], [f["id"] for f in self.mem.list_facts()])
        self.tick(days=8)
        default_ids = {f["id"] for f in self.mem.list_facts()}
        self.assertNotIn(forgotten["id"], default_ids)
        self.assertNotIn(temp["id"], default_ids)
        self.assertNotIn(old["id"], default_ids)
        digest = self.mem.fact_digest()
        read_ids = {f["id"] for f in digest["constraints"]["hard"] + digest["constraints"]["soft"]
                    + digest["preferences"] + digest["goals"] + digest["observations"]}
        self.assertEqual(read_ids, default_ids)
        self.assertIn(keep["id"], read_ids)

    def test_acceptance_8_same_category_facts_differing_on_a_number_conflict(self):
        a = self.save("Pallof press score is 6 of 8", category="body", source="user")
        b = self.save("Pallof press score after the clinical screen: 8 of 8", category="body")
        conflicts = self.mem.fact_digest()["conflicts"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["ids"], [a["id"], b["id"]])
        self.assertEqual(conflicts[0]["reason"], "same topic, numbers differ: 6 vs 8")
        self.assertEqual(conflicts[0]["texts"], [a["text"], b["text"]])

    def test_acceptance_9_list_facts_include_archived_returns_full_history(self):
        v1 = self.save("Goal: squat 150 lb", kind="goal", category="note")
        v2 = self.save("Goal: squat 175 lb by December", kind="goal", category="note", supersedes=[v1["id"]])
        v3 = self.save("Goal: squat 185 lb by the end of December", kind="goal", category="note",
                       supersedes=[v2["id"]])
        gone = self.save("Likes kettlebells", kind="preference", category="equipment")
        self.mem.forget_fact(gone["id"])
        history = self.mem.list_facts(include_archived=True)
        self.assertEqual([f["id"] for f in history], [v1["id"], v2["id"], v3["id"], gone["id"]])
        by_id = {f["id"]: f for f in history}
        self.assertEqual((by_id[v1["id"]]["supersededBy"], by_id[v2["id"]]["supersededBy"]), (v2["id"], v3["id"]))
        self.assertEqual(by_id[v3["id"]]["status"], "active")
        self.assertEqual(by_id[gone["id"]]["status"], "archived")
        self.assertEqual([f["id"] for f in self.mem.list_facts()], [v3["id"]])
        self.assertEqual([g["id"] for g in self.mem.fact_digest()["goals"]], [v3["id"]])


class TestValidation(CuratedBase):
    def test_exactly_the_limit_is_accepted(self):
        self.save("y" * FACT_TEXT_LIMIT)
        with self.assertRaises(ValueError) as ctx:
            self.mem.remember_fact("y" * 1213, kind="observation", category="note")
        self.assertEqual(str(ctx.exception),
                         "Fact is 1,213 characters; the limit is 600. Split it into smaller facts.")

    def test_enums_are_enforced(self):
        bad = [dict(kind="rule", category="note"), dict(kind="observation", category="dislike"),
               dict(kind="constraint", category="injury", severity="medium"),
               dict(kind="observation", category="note", severity="hard"),
               dict(kind="observation", category="note", source="coach")]
        for kw in bad:
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.mem.remember_fact("A fact", **kw)
        with self.assertRaises(ValueError):
            self.mem.remember_fact("   ", kind="observation", category="note")
        with self.assertRaises(ValueError):
            self.mem.remember_fact("A fact", kind="observation", category="note", expires_days=-1)
        self.assertEqual(self.rows(), 0)

    def test_enums_are_case_insensitive_and_defaults(self):
        fact = self.save("  Owns a  barbell  ", kind="Preference", category="EQUIPMENT")
        self.assertEqual((fact["text"], fact["kind"], fact["category"], fact["source"], fact["severity"]),
                         ("Owns a  barbell", "preference", "equipment", "inferred", None))
        self.assertEqual((fact["scope"], fact["supersedes"], fact["expiresAt"], fact["legacy"]), (None, [], None, False))
        self.assertEqual(fact["createdAt"], "2026-09-28T12:00:00Z")
        user = self.save("Left shoulder impingement", kind="constraint", severity="HARD", category="injury",
                         source="user", scope="  location:home ")
        self.assertEqual((user["source"], user["severity"], user["scope"]), ("user", "hard", "location:home"))

    def test_supersedes_targets_must_exist_and_be_active(self):
        a = self.save("Rows at 40 lb", category="body")
        b = self.save("Rows at 50 lb for sets of eight", category="body", supersedes=[a["id"]])
        with self.assertRaises(ValueError) as ctx:
            self.mem.remember_fact("Rows at 60 lb", kind="observation", category="body", supersedes=[a["id"]])
        self.assertIn(f"superseded by #{b['id']}", str(ctx.exception))
        with self.assertRaises(ValueError):
            self.mem.remember_fact("Rows at 60 lb", kind="observation", category="body", supersedes=[404])
        with self.assertRaises(ValueError):
            self.mem.remember_fact("Rows at 60 lb", kind="observation", category="body", supersedes=["x"])
        forgotten = self.save("Bench is flat only", kind="constraint", severity="soft", category="equipment")
        self.mem.forget_fact(forgotten["id"])
        with self.assertRaises(ValueError):
            self.mem.remember_fact("Bench inclines now", kind="observation", category="equipment",
                                   supersedes=[forgotten["id"]])

    def test_a_near_match_listed_in_supersedes_is_not_a_duplicate(self):
        a = self.save(HOTEL, kind="constraint", severity="hard", category="equipment")
        b = self.save("the hotel stack moves in 5 lb steps to 70", kind="constraint", severity="hard",
                      category="equipment", supersedes=[a["id"]])
        self.assertEqual([f["id"] for f in self.mem.list_facts()], [b["id"]])

    def test_near_duplicate_across_categories_is_still_caught(self):
        self.save("Owns a 20 kg barbell and plates", category="equipment")
        got = self.mem.remember_fact("owns a 20kg barbell and plates", kind="observation", category="note")
        self.assertFalse(got["saved"])

    def test_distinct_facts_are_not_duplicates(self):
        self.save("No overhead pressing", kind="constraint", severity="hard", category="injury")
        self.save("No deep knee flexion under load", kind="constraint", severity="hard", category="injury")
        self.assertEqual(self.rows(), 2)

    def test_expired_and_archived_facts_are_not_duplicate_targets(self):
        a = self.save("Travelling this week", kind="constraint", severity="soft", category="schedule", expires_days=2)
        self.tick(days=3)
        b = self.save("Travelling this week", kind="constraint", severity="soft", category="schedule")
        self.assertNotEqual(a["id"], b["id"])

    def test_forget_archives_not_deletes(self):
        fact = self.save("Prefers mornings", kind="preference", category="schedule")
        got = self.mem.forget_fact(fact["id"])
        self.assertEqual((got["id"], got["status"]), (fact["id"], "archived"))
        self.assertIsNotNone(got["archivedAt"])
        self.assertEqual(self.mem.list_facts(), [])
        self.assertEqual(self.rows(), 1)
        with self.assertRaises(ValueError):
            self.mem.forget_fact(fact["id"])
        with self.assertRaises(LookupError):
            self.mem.forget_fact(404)

    def test_list_filters(self):
        self.save("No overhead pressing", kind="constraint", severity="hard", category="injury")
        self.save("Likes cable rows", kind="preference", category="note")
        self.assertEqual([f["kind"] for f in self.mem.list_facts(kind="preference")], ["preference"])
        self.assertEqual([f["category"] for f in self.mem.list_facts(category="injury")], ["injury"])
        with self.assertRaises(ValueError):
            self.mem.list_facts(kind="rule")


class TestImport(CuratedBase):
    def test_import_rejects_malformed_items_without_stopping(self):
        report = self.mem.import_facts([
            "just a string",
            {"text": "Fine fact", "kind": "goal", "category": "note"},
            {"text": "x" * 700, "kind": "goal", "category": "note"},
            {"text": "Unknown key", "kind": "goal", "category": "note", "colour": "red"},
        ])
        self.assertEqual([a["text"] for a in report["accepted"]], ["Fine fact"])
        reasons = [r["reason"] for r in report["rejected"]]
        self.assertEqual(len(reasons), 3)
        self.assertIn("700 characters", reasons[1])
        self.assertIn("colour", reasons[2])
        self.assertEqual(report["rejected"][0]["input"], "just a string")

    def test_import_supersede_chain_reports_superseded(self):
        a = self.save("Bench 3x8 at 95", category="body")
        report = self.mem.import_facts([
            {"text": "Bench 3x8 at 105 after the deload", "kind": "observation", "category": "body",
             "supersedes": [a["id"]], "source": "user", "expires_days": 30},
        ])
        self.assertEqual(report["superseded"], [a["id"]])
        self.assertEqual(report["accepted"][0]["source"], "user")
        self.assertEqual(report["accepted"][0]["expiresAt"], "2026-10-28T12:00:00Z")

    def test_import_rejects_non_list(self):
        with self.assertRaises(ValueError):
            self.mem.import_facts({"text": "x"})


class TestDigest(CuratedBase):
    def test_grouping(self):
        hard = self.save("No overhead pressing", kind="constraint", severity="hard", category="injury")
        soft = self.save("Keep sessions under 45 minutes", kind="constraint", severity="soft", category="schedule")
        pref = self.save("Likes cable rows", kind="preference", category="note")
        goal = self.save("Deadlift 200 lb by spring", kind="goal", category="note")
        digest = self.mem.fact_digest()
        self.assertEqual([f["id"] for f in digest["constraints"]["hard"]], [hard["id"]])
        self.assertEqual([f["id"] for f in digest["constraints"]["soft"]], [soft["id"]])
        self.assertEqual([f["id"] for f in digest["preferences"]], [pref["id"]])
        self.assertEqual([f["id"] for f in digest["goals"]], [goal["id"]])
        self.assertEqual(digest["observations"], [])
        self.assertEqual(digest["conflicts"], [])
        self.assertEqual(digest["legacyToReview"], 0)
        self.assertEqual(digest["constraints"]["hard"][0]["text"], "No overhead pressing")

    def test_observations_are_the_ten_most_recent(self):
        ids = []
        for word in ("squat", "bench", "row", "curl", "press", "lunge", "plank", "dip", "fly", "raise", "shrug",
                     "pull"):
            ids.append(self.save(f"{word} felt {word} strong {word}")["id"])
            self.tick(minutes=1)
        got = [o["id"] for o in self.mem.fact_digest()["observations"]]
        self.assertEqual(got, list(reversed(ids))[:10])

    def test_negation_conflict(self):
        a = self.save("Can do barbell back squats with a low bar position", category="injury")
        b = self.save("Cannot do barbell back squats because of the low bar position", category="injury")
        conflicts = self.mem.fact_digest()["conflicts"]
        self.assertEqual([c["ids"] for c in conflicts], [[a["id"], b["id"]]])
        self.assertIn("negat", conflicts[0]["reason"])

    def test_no_conflict_across_categories_or_unrelated_topics(self):
        self.save("Pallof press score is 6 of 8", category="body")
        self.save("Pallof press score is 8 of 8 at the clinic today", category="note")
        self.save("Rows 3 sets of 10", category="body")
        self.assertEqual(self.mem.fact_digest()["conflicts"], [])


def old_schema_db(path: Path, rows) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE facts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      fact TEXT NOT NULL,
      category TEXT NOT NULL DEFAULT 'note',
      created_at TEXT NOT NULL,
      expires_at TEXT
    )""")
    conn.executemany("INSERT INTO facts (fact, category, created_at, expires_at) VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


LEGACY_ROWS = [
    ("Left shoulder impingement - no overhead pressing", "injury", "2026-08-01T10:00:00Z", None),
    ("Wants to deadlift 200 lb", "goal", "2026-08-02T10:00:00Z", None),
    ("Hates burpees", "dislike", "2026-08-03T10:00:00Z", None),
    ("Likes cable rows", "preference", "2026-08-04T10:00:00Z", None),
    ("L" * 1100, "body", "2026-08-05T10:00:00Z", None),
    ("Travelling in October", "schedule", "2026-08-06T10:00:00Z", "2026-12-01T00:00:00Z"),
    ("Old tweak", "note", "2026-08-07T10:00:00Z", "2026-09-01T00:00:00Z"),
    ("Something odd", "mystery", "2026-08-08T10:00:00Z", None),
]


class TestLegacyMigration(CuratedBase):
    def test_migrates_old_facts_table_once(self):
        old = self.dir / "old.db"
        old_schema_db(old, LEGACY_ROWS)
        mem = self.open(old)
        history = mem.list_facts(include_archived=True)
        self.assertEqual(len(history), len(LEGACY_ROWS))
        self.assertTrue(all(f["legacy"] and f["status"] == "archived" and f["kind"] == "observation"
                            for f in history))
        self.assertEqual([f["category"] for f in history],
                         ["injury", "note", "note", "note", "body", "schedule", "note", "note"])
        self.assertEqual(history[4]["text"], "L" * 1100)  # kept whole even over the limit
        self.assertEqual([f["createdAt"] for f in history], [r[2] for r in LEGACY_ROWS])
        self.assertEqual(history[0]["text"], LEGACY_ROWS[0][0])
        self.assertEqual(mem.list_facts(), [])
        # the expired legacy row (id 7) doesn't need reviewing
        self.assertEqual(mem.fact_digest()["legacyToReview"], 7)
        self.assertIn("supersedes", mem.fact_digest()["legacyHint"])
        # old table untouched
        conn = sqlite3.connect(str(old))
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT fact, category, created_at, expires_at FROM facts ORDER BY id").fetchall(),
                         LEGACY_ROWS)
        mem.close()
        # reopening (the server does it on every start) copies nothing twice
        again = self.open(old)
        again2 = self.open(old)
        self.assertEqual(len(again2.list_facts(include_archived=True)), len(LEGACY_ROWS))
        self.assertEqual(len(again.list_facts(include_archived=True)), len(LEGACY_ROWS))

    def test_legacy_rows_can_be_curated_with_supersedes_or_dismissed(self):
        old = self.dir / "old.db"
        old_schema_db(old, LEGACY_ROWS[:3])
        mem = self.open(old)
        legacy = mem.list_facts(include_archived=True)
        got = mem.remember_fact("No overhead pressing (left shoulder impingement)", kind="constraint",
                                severity="hard", category="injury", source="user", supersedes=[legacy[0]["id"]])
        self.assertTrue(got["saved"])
        self.assertEqual(mem.fact_digest()["legacyToReview"], 2)
        report = mem.import_facts([{"text": "Deadlift 200 lb", "kind": "goal", "category": "note",
                                    "supersedes": [legacy[1]["id"]]}])
        self.assertEqual(report["superseded"], [legacy[1]["id"]])
        mem.forget_fact(legacy[2]["id"])  # dismissing a legacy fact also counts as reviewed
        digest = mem.fact_digest()
        self.assertEqual(digest["legacyToReview"], 0)
        self.assertNotIn("legacyHint", digest)
        self.assertEqual([f["id"] for f in digest["constraints"]["hard"]], [got["fact"]["id"]])
        # a reviewed legacy row can't be superseded again
        with self.assertRaises(ValueError):
            mem.remember_fact("Deadlift 210 lb", kind="goal", category="note", supersedes=[legacy[1]["id"]])

    def test_new_legacy_rows_are_picked_up_on_next_open(self):
        old = self.dir / "old.db"
        old_schema_db(old, LEGACY_ROWS[:1])
        self.open(old).close()
        conn = sqlite3.connect(str(old))
        conn.execute("INSERT INTO facts (fact, category, created_at) VALUES ('Late row', 'note', '2026-09-01T00:00:00Z')")
        conn.commit()
        conn.close()
        mem = self.open(old)
        self.assertEqual([f["text"] for f in mem.list_facts(include_archived=True)],
                         [LEGACY_ROWS[0][0], "Late row"])

    def test_fresh_db_has_nothing_to_review(self):
        digest = self.mem.fact_digest()
        self.assertEqual(digest["legacyToReview"], 0)

    def test_survives_reopen(self):
        fact = self.save("Durable", kind="goal", category="note")
        self.mem.close()
        again = self.open(self.path)
        self.assertEqual(again.list_facts()[0]["id"], fact["id"])


class TestFixRound1(CuratedBase):
    """Review fix round 1 (items 1-3, 5-10 at the store level)."""

    def legacy_db(self, rows) -> Memory:
        old = self.dir / "legacy.db"
        old_schema_db(old, rows)
        return self.open(old)

    # item 1 -------------------------------------------------------------------------------------------
    def test_item1_digest_lists_legacy_unreviewed(self):
        mem = self.legacy_db(LEGACY_ROWS)
        digest = mem.fact_digest()
        pending = digest["legacyUnreviewed"]
        self.assertEqual(len(pending), 7)  # the expired legacy row drops out
        self.assertEqual(pending[0], {"id": 1, "text": LEGACY_ROWS[0][0], "category": "injury",
                                      "createdAt": LEGACY_ROWS[0][2]})
        self.assertNotIn(7, [p["id"] for p in pending])
        mem.remember_fact("No overhead pressing (left shoulder)", kind="constraint", severity="hard",
                          category="injury", supersedes=[1])
        mem.forget_fact(2)
        ids = [p["id"] for p in mem.fact_digest()["legacyUnreviewed"]]
        self.assertNotIn(1, ids)
        self.assertNotIn(2, ids)
        self.assertEqual(len(ids), 5)

    def test_item1_fresh_digest_has_empty_legacy_unreviewed(self):
        self.assertEqual(self.mem.fact_digest()["legacyUnreviewed"], [])

    # item 2 -------------------------------------------------------------------------------------------
    def test_item2_kind_upgrade_refusal_names_the_difference(self):
        a = self.save("No overhead pressing", kind="observation", category="injury")
        got = self.mem.remember_fact("No overhead pressing", kind="constraint", severity="hard", category="injury")
        self.assertFalse(got["saved"])
        self.assertIn(f"existing #{a['id']} is an observation; you're saving a hard constraint", got["message"])
        self.assertIn(f"supersedes=[{a['id']}]", got["message"])
        self.assertNotIn("nothing needs saving", got["message"])

    def test_item2_severity_upgrade_refusal_names_the_difference(self):
        a = self.save("No overhead pressing", kind="constraint", severity="soft", category="injury")
        got = self.mem.remember_fact("No overhead pressing", kind="constraint", severity="hard", category="injury")
        self.assertFalse(got["saved"])
        self.assertIn(f"existing #{a['id']} is a soft constraint; you're saving a hard constraint", got["message"])
        self.assertNotIn("nothing needs saving", got["message"])

    def test_item2_changed_number_refusal_names_the_difference(self):
        a = self.save("Goal: squat 185 lb by December", kind="goal")
        got = self.mem.remember_fact("Goal: squat 205 lb by December", kind="goal", category="note")
        self.assertFalse(got["saved"])
        self.assertIn("numbers differ: 185 vs 205", got["message"])
        self.assertIn(f"supersedes=[{a['id']}]", got["message"])
        self.assertNotIn("nothing needs saving", got["message"])

    def test_item2_negation_and_scope_differences_are_named(self):
        self.save("Barbell available at the hotel gym", kind="constraint", severity="soft", category="equipment")
        got = self.mem.remember_fact("No barbell available at the hotel gym", kind="constraint", severity="soft",
                                     category="equipment")
        self.assertFalse(got["saved"])
        self.assertIn("negat", got["message"])
        self.assertNotIn("nothing needs saving", got["message"])
        self.save("Cable stack tops out at 100", kind="constraint", severity="hard", category="equipment")
        got = self.mem.remember_fact("Cable stack tops out at 100", kind="constraint", severity="hard",
                                     category="equipment", scope="location:tampa-hotel")
        self.assertFalse(got["saved"])  # null scope vs set scope is still compared
        self.assertIn("scope", got["message"])

    def test_item2_true_repeat_keeps_nothing_needs_saving(self):
        self.save(HOTEL, kind="constraint", severity="hard", category="equipment")
        got = self.mem.remember_fact(HOTEL + ".", kind="constraint", severity="hard", category="equipment")
        self.assertIn("nothing needs saving", got["message"])

    def test_item2_import_dedupe_reports_differences(self):
        self.save("Goal: squat 185 lb by December", kind="goal")
        report = self.mem.import_facts([{"text": "Goal: squat 205 lb by December", "kind": "goal",
                                         "category": "note"}])
        self.assertIn("numbers differ: 185 vs 205", report["deduped"][0]["differences"])

    # item 3 -------------------------------------------------------------------------------------------
    def test_item3_different_scopes_are_not_duplicates(self):
        home = self.save("Cable stack tops out at 100", kind="constraint", severity="hard", category="equipment",
                         scope="location:home")
        hotel = self.save("Cable stack tops out at 100", kind="constraint", severity="hard", category="equipment",
                          scope="location:tampa-hotel")
        self.assertNotEqual(home["id"], hotel["id"])
        self.assertEqual(self.rows(), 2)

    def test_item3_different_scopes_are_not_conflicts(self):
        self.save("Cable stack tops out at 200", kind="constraint", severity="hard", category="equipment",
                  scope="location:home")
        self.save("At the hotel the cable stack only tops out at 60", kind="constraint", severity="hard",
                  category="equipment", scope="location:tampa-hotel")
        self.assertEqual(self.mem.fact_digest()["conflicts"], [])

    def test_item3_null_scope_vs_set_scope_still_conflicts(self):
        self.save("Cable stack tops out at 200", kind="constraint", severity="hard", category="equipment")
        self.save("At the hotel the cable stack only tops out at 60", kind="constraint", severity="hard",
                  category="equipment", scope="location:tampa-hotel")
        self.assertEqual(len(self.mem.fact_digest()["conflicts"]), 1)

    # item 5 -------------------------------------------------------------------------------------------
    def test_item5_expires_days_validation(self):
        for bad in (True, False, 2.5, "7", "soon", 3651, -1, [7]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.mem.remember_fact("Temporary thing", kind="observation", category="note", expires_days=bad)
        self.assertEqual(self.rows(), 0)
        fact = self.save("Temporary thing", expires_days=3650)
        self.assertEqual(fact["expiresAt"], "2036-09-25T12:00:00Z")

    def test_item5_import_rejects_bad_expires_days_per_item(self):
        report = self.mem.import_facts([
            {"text": "Travelling next week", "kind": "observation", "category": "schedule", "expires_days": "7"},
            {"text": "Deload this week", "kind": "observation", "category": "schedule", "expires_days": 99999},
            {"text": "Hotel gym only", "kind": "observation", "category": "equipment", "expires_days": 5},
        ])
        self.assertEqual([a["text"] for a in report["accepted"]], ["Hotel gym only"])
        self.assertEqual(len(report["rejected"]), 2)
        self.assertTrue(all("expires_days" in r["reason"] for r in report["rejected"]))

    # item 6 -------------------------------------------------------------------------------------------
    def test_item6_two_pieces_supersede_one_long_legacy_fact_in_one_import(self):
        mem = self.legacy_db([("L" * 1100, "injury", "2026-08-05T10:00:00Z", None)])
        report = mem.import_facts([
            {"text": "Left knee: no deep flexion under load", "kind": "constraint", "severity": "hard",
             "category": "injury", "supersedes": [1]},
            {"text": "Right hip flexor gets tight after long rows", "kind": "observation", "category": "injury",
             "supersedes": [1]},
        ])
        self.assertEqual(len(report["accepted"]), 2, report)
        self.assertEqual(report["rejected"], [])
        self.assertEqual(report["superseded"], [1])
        history = {f["id"]: f for f in mem.list_facts(include_archived=True)}
        self.assertEqual(history[1]["status"], "archived")
        self.assertEqual(history[1]["supersededBy"], report["accepted"][0]["id"])
        self.assertEqual(mem.fact_digest()["legacyToReview"], 0)
        # outside that transaction the target is no longer supersedable
        with self.assertRaises(ValueError):
            mem.remember_fact("Third piece", kind="observation", category="injury", supersedes=[1])

    # item 7/8 -----------------------------------------------------------------------------------------
    def test_item8_years_are_ignored_and_numbers_compare_as_symmetric_difference(self):
        self.save("Knee surgery in 2019, cleared for squats", category="injury")
        self.save("Cleared for squats since the knee surgery (2021)", category="injury")
        self.assertEqual(self.mem.fact_digest()["conflicts"], [])

    def test_item8_reason_shows_only_the_differing_numbers(self):
        self.save("Hotel dumbbells go to 50 in steps of 5", category="equipment")
        self.save("At the hotel the dumbbells go up to 60, steps of 5", category="equipment")
        conflict = self.mem.fact_digest()["conflicts"][0]
        self.assertEqual(conflict["reason"], "same topic, numbers differ: 50 vs 60")
        self.assertEqual(set(conflict), {"ids", "texts", "reason"})

    # item 9 -------------------------------------------------------------------------------------------
    def test_item9_supersede_is_atomic_when_archiving_fails(self):
        a = self.save("Rows at 40 lb", category="body")
        self.mem._db.execute("CREATE TRIGGER boom BEFORE UPDATE OF superseded_by ON curated_facts "
                             "BEGIN SELECT RAISE(ABORT, 'archive failed'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.mem.remember_fact("Rows at 50 lb for sets of eight", kind="observation", category="body",
                                   supersedes=[a["id"]])
        history = self.mem.list_facts(include_archived=True)
        self.assertEqual([f["id"] for f in history], [a["id"]])  # the new row is absent
        self.assertEqual(history[0]["status"], "active")
        self.assertIsNone(history[0]["supersededBy"])

    # item 10 ------------------------------------------------------------------------------------------
    def test_item10_non_string_text_rejected(self):
        for bad in (123, ["No overhead pressing"], {"t": 1}, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.mem.remember_fact(bad, kind="observation", category="note")
        report = self.mem.import_facts([{"text": 42, "kind": "goal", "category": "note"}])
        self.assertIn("text must be a string", report["rejected"][0]["reason"])
        self.assertEqual(self.rows(), 0)


class TestFixRound2(CuratedBase):
    def test_r2_item1_quantities_in_year_range_with_a_unit_are_numbers(self):
        a = self.save("Goal: row 2000 m", kind="goal")
        got = self.mem.remember_fact("Goal: row 2100 m", kind="goal", category="note")
        self.assertFalse(got["saved"])
        self.assertIn("numbers differ: 2000 vs 2100", got["message"])
        self.assertIn(f"supersedes=[{a['id']}]", got["message"])
        self.assertNotIn("nothing needs saving", got["message"])

    def test_r2_item1_unit_variants(self):
        from speediance_mcp.memory import _numbers
        for text in ("2000m", "2000 kcal", "1950 lb", "2010 %", "2000%", "2100 watts", "1999 reps"):
            with self.subTest(text=text):
                self.assertEqual(sum(_numbers(text).values()), 1)
        for text in ("in 2024", "since 2025, cleared", "2019 marathon", "(2021)"):
            with self.subTest(text=text):
                self.assertEqual(sum(_numbers(text).values()), 0)

    def test_r2_item1_years_still_ignored(self):
        self.save("Knee surgery in 2024, cleared for squats", category="injury")
        got = self.mem.remember_fact("Knee surgery in 2025, cleared for squats", kind="observation",
                                     category="injury")
        self.assertFalse(got["saved"])
        self.assertIn("nothing needs saving", got["message"])

    def test_r2_item3_scope_is_normalised(self):
        fact = self.save("Cable stack tops out at 100", kind="constraint", severity="hard", category="equipment",
                         scope="  Location:Tampa-Hotel ")
        self.assertEqual(fact["scope"], "location:tampa-hotel")
        got = self.mem.remember_fact("Cable stack tops out at 100", kind="constraint", severity="hard",
                                     category="equipment", scope="location:tampa-hotel")
        self.assertFalse(got["saved"])
        self.assertIn("nothing needs saving", got["message"])

    def test_r2_item4_category_difference_is_named(self):
        a = self.save("Left knee aches after long rows", category="injury")
        got = self.mem.remember_fact("Left knee aches after long rows", kind="observation", category="body")
        self.assertFalse(got["saved"])
        self.assertIn(f"existing #{a['id']} is category injury; you're saving body", got["message"])
        self.assertIn(f"supersedes=[{a['id']}]", got["message"])
        self.assertNotIn("nothing needs saving", got["message"])


if __name__ == "__main__":
    unittest.main()
