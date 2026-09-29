from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from speediance_mcp.memory import Memory


class TestMemory(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "m.db"
        self.clock = {"now": dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.timezone.utc)}
        self.mem = Memory(self.path, now=lambda: self.clock["now"])
        self.addCleanup(self.mem.close)

    def test_preferences_defaults_and_updates(self):
        self.assertEqual(self.mem.preferences()["owned_equipment"], [])
        prefs = self.mem.set_preferences(goal="Build strength", training_days=["Mon", "Thu"],
                                         session_minutes=45, load_anchors={321: 50},
                                         owned_equipment=[" Barbell ", "Handles", ""])
        self.assertEqual(prefs["load_anchors"], {"321": 50.0})
        self.assertEqual(prefs["owned_equipment"], ["Barbell", "Handles"])
        self.mem.set_preferences(goal="Hypertrophy")
        self.assertEqual(self.mem.preferences()["session_minutes"], 45)

    def test_preference_validation(self):
        with self.assertRaises(ValueError):
            self.mem.set_preferences(color="red")
        with self.assertRaises(ValueError):
            self.mem.set_preferences(session_minutes=0)
        with self.assertRaises(ValueError):
            self.mem.set_preferences(load_anchors={"321": "heavy"})

    def test_load_anchors_merge_and_remove(self):
        self.mem.set_preferences(load_anchors={"321": 45})
        self.mem.set_preferences(load_anchors={"500": 20})
        self.assertEqual(self.mem.preferences()["load_anchors"], {"321": 45.0, "500": 20.0})
        self.mem.set_preferences(load_anchors={"321": 50, "600": 12.5})
        self.assertEqual(self.mem.preferences()["load_anchors"], {"321": 50.0, "500": 20.0, "600": 12.5})
        self.mem.set_preferences(load_anchors={"321": 0})
        self.assertEqual(self.mem.preferences()["load_anchors"], {"500": 20.0, "600": 12.5})
        self.mem.set_preferences(load_anchors={"500": None, "999": None})
        self.assertEqual(self.mem.preferences()["load_anchors"], {"600": 12.5})

    def test_marks(self):
        self.mem.mark_exercise(321, "preferred", "Barbell Bent Over Row")
        self.mem.mark_exercise(600, "avoided", "Single Arm Cable Row")
        self.assertEqual(self.mem.marks()[321], {"mark": "preferred", "name": "Barbell Bent Over Row", "reason": ""})
        self.mem.mark_exercise(600, "none")
        self.assertNotIn(600, self.mem.marks())
        with self.assertRaises(ValueError):
            self.mem.mark_exercise(1, "love")

    def test_mark_reason_round_trip(self):
        self.mem.mark_exercise(600, "avoided", "Single Arm Cable Row", reason="  left shoulder  ")
        self.assertEqual(self.mem.marks()[600],
                         {"mark": "avoided", "name": "Single Arm Cable Row", "reason": "left shoulder"})
        # un-avoiding deletes the row entirely, reason included.
        self.mem.mark_exercise(600, "none")
        self.assertNotIn(600, self.mem.marks())

    def test_reason_too_long_rejected(self):
        with self.assertRaises(ValueError):
            self.mem.mark_exercise(600, "avoided", "Single Arm Cable Row", reason="x" * 201)
        self.assertNotIn(600, self.mem.marks())
        # exactly 200 is fine
        self.mem.mark_exercise(600, "avoided", "Single Arm Cable Row", reason="x" * 200)
        self.assertEqual(self.mem.marks()[600]["reason"], "x" * 200)

    def test_migrates_old_db_without_reason_column(self):
        import sqlite3
        old_path = self.path.parent / "old.db"
        conn = sqlite3.connect(str(old_path))
        conn.execute("""CREATE TABLE exercise_marks (
          group_id INTEGER PRIMARY KEY,
          mark TEXT NOT NULL CHECK (mark IN ('preferred', 'avoided')),
          name TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL
        )""")
        conn.execute("INSERT INTO exercise_marks (group_id, mark, name, updated_at) VALUES "
                     "(600, 'avoided', 'Single Arm Cable Row', '2026-01-01T00:00:00Z')")
        conn.commit()
        conn.close()
        migrated = Memory(old_path, now=lambda: self.clock["now"])
        self.addCleanup(migrated.close)
        self.assertEqual(migrated.marks()[600],
                         {"mark": "avoided", "name": "Single Arm Cable Row", "reason": ""})
        migrated.mark_exercise(600, "avoided", "Single Arm Cable Row", reason="left shoulder")
        self.assertEqual(migrated.marks()[600]["reason"], "left shoulder")

    def test_survives_reopen(self):
        self.mem.set_preferences(goal="Durable")
        self.mem.close()
        again = Memory(self.path, now=lambda: self.clock["now"])
        self.addCleanup(again.close)
        self.assertEqual(again.preferences()["goal"], "Durable")

    def test_connects_with_a_lock_timeout(self):
        from unittest import mock
        with mock.patch("speediance_mcp.memory.sqlite3.connect", wraps=__import__("sqlite3").connect) as connect:
            mem = Memory(self.path.parent / "timeout.db", now=lambda: self.clock["now"])
            self.addCleanup(mem.close)
            self.assertEqual(connect.call_args.kwargs.get("timeout"), 5)
