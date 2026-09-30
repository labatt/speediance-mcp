"""Coaching memory: facts, preferences and exercise marks in a local SQLite file."""

from __future__ import annotations

import collections
import contextlib
import datetime as dt
import difflib
import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version (version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fact TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'note',
  created_at TEXT NOT NULL,
  expires_at TEXT
);
CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exercise_marks (
  group_id INTEGER PRIMARY KEY,
  mark TEXT NOT NULL CHECK (mark IN ('preferred', 'avoided')),
  name TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT ''
);
-- Cached per-session exercise stats. A DERIVED cache, not a source of truth: every row is
-- recomputable from Speediance, which is what makes it safe to rebuild. (Contrast
-- offmachine_sets below, which holds the ONLY copy of its data.)
--
-- It exists because userActionStatPage buckets by WEEK, so it cannot say what a single
-- day held; session detail is the only daily source, and deriving it on every read was
-- slow and bounded personal bests to a short window. `derived_version` is stamped on every
-- row so that correcting the volume/max-weight derivation invalidates what the old logic
-- produced instead of preserving it silently.
--
-- SHARED TABLES: the companion web app's session_stats_store.py declares these identically
-- in this same file. Whichever process opens first creates them — change both together.
CREATE TABLE IF NOT EXISTS session_exercise_stats (
  training_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  group_id INTEGER,
  day TEXT NOT NULL,
  volume REAL NOT NULL DEFAULT 0,
  max_weight REAL NOT NULL DEFAULT 0,
  sets INTEGER NOT NULL DEFAULT 0,
  reps INTEGER NOT NULL DEFAULT 0,
  derived_version INTEGER NOT NULL,
  cached_at TEXT NOT NULL,
  PRIMARY KEY (training_id, name)
);
CREATE INDEX IF NOT EXISTS session_exercise_stats_day ON session_exercise_stats (day);
-- Every scanned session is recorded here, INCLUDING ones that parsed to no exercises
-- (cardio, rowing, an unreadable payload). Without that marker an empty session would be
-- re-fetched on every reconcile forever.
CREATE TABLE IF NOT EXISTS session_stats_scanned (
  training_id INTEGER PRIMARY KEY,
  day TEXT NOT NULL,
  session_type INTEGER,
  exercise_count INTEGER NOT NULL DEFAULT 0,
  unreadable TEXT NOT NULL DEFAULT '',
  derived_version INTEGER NOT NULL,
  cached_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS session_stats_scanned_day ON session_stats_scanned (day);
-- Off-machine training: the exercise detail Speediance cannot hold.
--
-- Speediance's manual log (session type 10) counts a day towards streaks, days trained,
-- minutes and calories, but stores NO exercises — and the detail cannot be pushed in:
-- app/freetraining/save is an UPDATE into a session row the machine itself created
-- (getFreeTrainingId for a client-invented uuid returns null), so a workout that never
-- ran on the hardware cannot be written at all. The detail therefore lives here.
--
-- SHARED TABLE: the companion web app's offmachine_store.py declares this same table in
-- this same file. Whichever process opens first creates it, so the two definitions must
-- stay identical — change both together.
--
-- One row per SET. group_id is the Speediance exercise GROUP id, which is what lets a
-- hotel set resolve through the library to muscles and count towards volume; NULL means
-- the movement has no Speediance equivalent (still logged, just not attributable).
-- Weights are in the account's DISPLAY unit, matching Speediance's own no-conversion
-- convention on read and write alike.
CREATE TABLE IF NOT EXISTS offmachine_sets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  training_id INTEGER,
  group_id INTEGER,
  name TEXT NOT NULL,
  set_index INTEGER NOT NULL,
  reps INTEGER NOT NULL,
  weight REAL NOT NULL DEFAULT 0,
  side TEXT NOT NULL DEFAULT 'both' CHECK (side IN ('both', 'left', 'right')),
  location TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS offmachine_sets_day ON offmachine_sets (day);
CREATE TABLE IF NOT EXISTS curated_facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('constraint', 'preference', 'observation', 'goal')),
  severity TEXT CHECK (severity IN ('hard', 'soft')),
  category TEXT NOT NULL CHECK (category IN ('injury', 'equipment', 'schedule', 'body', 'nutrition', 'note')),
  scope TEXT,
  supersedes_json TEXT NOT NULL DEFAULT '[]',
  superseded_by INTEGER,
  source TEXT NOT NULL CHECK (source IN ('user', 'inferred')),
  created_at TEXT NOT NULL,
  expires_at TEXT,
  archived_at TEXT,
  legacy INTEGER NOT NULL DEFAULT 0,
  legacy_id INTEGER UNIQUE,
  CHECK (kind != 'constraint' OR severity IS NOT NULL)
);
"""

# Copy every legacy `facts` row into curated_facts exactly once (legacy_id is UNIQUE, and rows already
# copied are skipped), archived for review. The legacy table itself is never modified.
MIGRATE_LEGACY = """
INSERT OR IGNORE INTO curated_facts
  (text, kind, severity, category, scope, supersedes_json, source, created_at, expires_at, legacy, legacy_id)
SELECT fact, 'observation', NULL,
       CASE WHEN category IN ('injury', 'equipment', 'schedule', 'body', 'note') THEN category ELSE 'note' END,
       NULL, '[]', 'inferred', created_at, expires_at, 1, id
FROM facts WHERE id NOT IN (SELECT legacy_id FROM curated_facts WHERE legacy_id IS NOT NULL)
ORDER BY id
"""

MAX_REASON = 200

FACT_TEXT_LIMIT = 600
FACT_KINDS = ("constraint", "preference", "observation", "goal")
FACT_SEVERITIES = ("hard", "soft")
FACT_CATEGORIES = ("injury", "equipment", "schedule", "body", "nutrition", "note")
FACT_SOURCES = ("user", "inferred")
FACT_ITEM_KEYS = ("text", "kind", "category", "severity", "scope", "supersedes", "expires_days", "source")
MAX_SCOPE = 100
MAX_IMPORT = 200
SIMILARITY_THRESHOLD = 0.85
CONFLICT_OVERLAP = 0.5
RECENT_OBSERVATIONS = 10
MAX_EXPIRES_DAYS = 3650
LEGACY_HINT = ("legacyUnreviewed lists facts from the old free-form store that nobody has curated yet. They may "
               "still bind: treat injury ones as hard constraints until curated. Curate them promptly with the "
               "user: re-save each one that still holds with the right kind/category via import_facts (dry_run "
               "first) or remember_fact with supersedes=[its id] — a fact over 600 characters can be split into "
               "several pieces that each list supersedes=[its id] in ONE import_facts call — and forget_fact the "
               "rest.")
PREF_DEFAULTS = {"goal": None, "training_days": [], "session_minutes": None,
                 "load_anchors": {}, "owned_equipment": [], "unusable_equipment": []}
MARKS = ("preferred", "avoided")


def _iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Memory:
    def __init__(self, path: Path, *, now: Callable[[], dt.datetime] | None = None):
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._lock = threading.Lock()
        # Tools run in worker threads, so the connection is shared under a lock. A short
        # lock timeout lets a momentary cross-process lock (the companion web app writing
        # to the same file) wait briefly instead of failing outright.
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(SCHEMA)
            columns = {row["name"] for row in self._db.execute("PRAGMA table_info(exercise_marks)")}
            if "reason" not in columns:
                self._db.execute("ALTER TABLE exercise_marks ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
            # CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so a
            # column added after first ship needs an explicit migration on both sides of
            # the shared file — the web app's session_stats_store.py carries the twin.
            scanned = {row["name"] for row in self._db.execute("PRAGMA table_info(session_stats_scanned)")}
            if scanned and "unreadable" not in scanned:
                self._db.execute("ALTER TABLE session_stats_scanned "
                                 "ADD COLUMN unreadable TEXT NOT NULL DEFAULT ''")
            self._db.execute(MIGRATE_LEGACY)

    def migrate_legacy(self) -> None:
        """Copy any legacy `facts` rows not yet copied (idempotent; also run on every open)."""
        with self._lock, self._db:
            self._db.execute(MIGRATE_LEGACY)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- curated facts (docs/specs/2026-09-28-curated-facts.md) ---------------------------------------
    @contextlib.contextmanager
    def _transaction(self, commit: bool = True):
        """One IMMEDIATE transaction under the lock; rolled back on error or when commit is False."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.rollback()
                raise
            if commit:
                self._db.commit()
            else:
                self._db.rollback()

    def remember_fact(self, text: str, *, kind: str, category: str, severity: str | None = None,
                      scope: str | None = None, supersedes=None, expires_days: int | None = None,
                      source: str = "inferred") -> dict:
        """Validate and store one fact. Returns {saved: True, fact, superseded} or, for a near-duplicate of
        an active fact, {saved: False, nearMatch, message} without writing. Invalid input raises ValueError."""
        item = {"text": text, "kind": kind, "category": category, "severity": severity, "scope": scope,
                "supersedes": supersedes, "expires_days": expires_days, "source": source}
        now = self._now()
        with self._transaction() as db:
            clean = _clean_fact(item)
            outcome, payload, similarity = self._write_fact(db, clean, now)
        if outcome == "deduped":
            match, pct = payload["id"], int(similarity * 100)
            diffs = _differences(payload, clean)
            if diffs:
                message = (f"Not saved: fact #{match} is {pct}% similar, but {'; '.join(diffs)}. If the new fact "
                           f"updates, upgrades or corrects it, call remember_fact again with supersedes=[{match}].")
            else:
                message = (f"Not saved: fact #{match} already says nearly the same thing ({pct}% similar). If the "
                           f"new fact replaces it, call remember_fact again with supersedes=[{match}]; if it says "
                           "the same thing, nothing needs saving.")
            return {"saved": False, "nearMatch": {**payload, "similarity": round(similarity, 2)},
                    "differences": diffs, "message": message}
        return {"saved": True, "fact": payload, "superseded": clean["supersedes"]}

    def import_facts(self, items, dry_run: bool = False) -> dict:
        """Run every single-write rule on each item, in order, in one transaction (rolled back for a dry
        run, so the dry-run report is exactly what the live run would produce)."""
        if not isinstance(items, list):
            raise ValueError("facts must be a list of fact objects")
        if len(items) > MAX_IMPORT:
            raise ValueError(f"Import is {len(items):,} facts; the limit is {MAX_IMPORT} per call. Split it.")
        report = {"accepted": [], "deduped": [], "rejected": [], "superseded": [], "dryRun": bool(dry_run)}
        now = self._now()
        batch_superseded: set[int] = set()
        with self._transaction(commit=not dry_run) as db:
            for item in items:
                try:
                    if not isinstance(item, dict):
                        raise ValueError("each fact must be an object with text, kind and category")
                    unknown = sorted(set(item) - set(FACT_ITEM_KEYS))
                    if unknown:
                        raise ValueError(f"unknown field(s): {', '.join(unknown)} "
                                         f"(allowed: {', '.join(FACT_ITEM_KEYS)})")
                    clean = _clean_fact(item)
                    outcome, payload, _ = self._write_fact(db, clean, now, batch_superseded)
                except ValueError as exc:
                    report["rejected"].append({"input": item, "reason": str(exc)})
                    continue
                if outcome == "deduped":
                    entry = {"input": item, "matched_existing_id": payload["id"]}
                    diffs = _differences(payload, clean)
                    if diffs:
                        entry["differences"] = diffs
                    report["deduped"].append(entry)
                else:
                    report["accepted"].append(payload)
                    report["superseded"].extend(t for t in clean["supersedes"] if t not in report["superseded"])
                    batch_superseded.update(clean["supersedes"])
        return report

    def _write_fact(self, db: sqlite3.Connection, clean: dict, now: dt.datetime,
                    batch_superseded: set[int] | frozenset = frozenset()):
        """Inside an open transaction: check supersedes targets, then similarity, then insert and archive.
        Returns ("accepted", fact, None) or ("deduped", near_match_fact, score); raises ValueError.
        A target superseded earlier in the same import (batch_superseded) may be superseded again, so a
        long fact can be replaced by several pieces; it keeps the first piece as superseded_by."""
        now_iso = _iso(now)
        for target in clean["supersedes"]:
            row = db.execute("SELECT * FROM curated_facts WHERE id = ?", (target,)).fetchone()
            if row is None:
                raise ValueError(f"supersedes: there is no fact #{target}.")
            if target in batch_superseded:
                continue
            if not (_is_active(row, now_iso) or _legacy_pending(row, now_iso)):
                raise ValueError(f"supersedes: fact #{target} is not active ({_why_archived(row, now_iso)}); "
                                 "only active facts can be superseded.")
        best, best_score = None, 0.0
        for row in db.execute("SELECT * FROM curated_facts ORDER BY id").fetchall():
            if not _is_active(row, now_iso) or row["id"] in clean["supersedes"]:
                continue
            if _different_scopes(row["scope"], clean["scope"]):
                continue
            score = similarity(clean["text"], row["text"])
            if score >= SIMILARITY_THRESHOLD and score > best_score:
                best, best_score = row, score
        if best is not None:
            return "deduped", _fact_dict(best, now_iso), best_score
        days = clean["expires_days"]
        expires = _iso(now + dt.timedelta(days=days)) if days else None
        cur = db.execute(
            "INSERT INTO curated_facts (text, kind, severity, category, scope, supersedes_json, source, created_at, "
            "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (clean["text"], clean["kind"], clean["severity"], clean["category"], clean["scope"],
             json.dumps(clean["supersedes"]), clean["source"], now_iso, expires))
        new_id = cur.lastrowid
        for target in clean["supersedes"]:
            db.execute("UPDATE curated_facts SET superseded_by = ? WHERE id = ? AND superseded_by IS NULL",
                       (new_id, target))
        row = db.execute("SELECT * FROM curated_facts WHERE id = ?", (new_id,)).fetchone()
        return "accepted", _fact_dict(row, now_iso), None

    def forget_fact(self, fact_id: int) -> dict:
        """Archive (never delete) a fact. A legacy fact awaiting review is archived as reviewed."""
        now_iso = _iso(self._now())
        with self._transaction() as db:
            row = db.execute("SELECT * FROM curated_facts WHERE id = ?", (int(fact_id),)).fetchone()
            if row is None:
                raise LookupError(f"No fact with id {int(fact_id)}.")
            if not (_is_active(row, now_iso) or _legacy_pending(row, now_iso)):
                raise ValueError(f"Fact #{row['id']} is already archived ({_why_archived(row, now_iso)}).")
            db.execute("UPDATE curated_facts SET archived_at = ? WHERE id = ?", (now_iso, row["id"]))
            row = db.execute("SELECT * FROM curated_facts WHERE id = ?", (row["id"],)).fetchone()
        return _fact_dict(row, now_iso)

    def list_facts(self, kind: str | None = None, category: str | None = None,
                   include_archived: bool = False) -> list[dict]:
        """The audit surface: facts oldest first, active only unless include_archived."""
        kind = _enum("kind", kind, FACT_KINDS) if kind else None
        category = _enum("category", category, FACT_CATEGORIES) if category else None
        now_iso = _iso(self._now())
        with self._lock:
            rows = self._db.execute("SELECT * FROM curated_facts ORDER BY id").fetchall()
        return [_fact_dict(r, now_iso) for r in rows
                if (include_archived or _is_active(r, now_iso))
                and (kind is None or r["kind"] == kind) and (category is None or r["category"] == category)]

    def fact_digest(self) -> dict:
        """What a planning read gets: active facts grouped by kind, the 10 most recent observations,
        read-time conflicts, and the legacy facts still awaiting review (legacyUnreviewed) with their count."""
        now_iso = _iso(self._now())
        with self._lock:
            rows = self._db.execute("SELECT * FROM curated_facts ORDER BY id").fetchall()
        active = [r for r in rows if _is_active(r, now_iso)]
        by_kind = {k: [_read_item(r) for r in active if r["kind"] == k] for k in FACT_KINDS}
        observations = sorted((r for r in active if r["kind"] == "observation"),
                              key=lambda r: (r["created_at"], r["id"]), reverse=True)[:RECENT_OBSERVATIONS]
        pending = [{"id": r["id"], "text": r["text"], "category": r["category"], "createdAt": r["created_at"]}
                   for r in rows if _legacy_pending(r, now_iso)]
        digest = {
            "constraints": {"hard": [f for f in by_kind["constraint"] if f["severity"] == "hard"],
                            "soft": [f for f in by_kind["constraint"] if f["severity"] == "soft"]},
            "preferences": by_kind["preference"],
            "goals": by_kind["goal"],
            "observations": [_read_item(r) for r in observations],
            "conflicts": find_conflicts([dict(r) for r in active]),
            "legacyToReview": len(pending),
            "legacyUnreviewed": pending,
        }
        if pending:
            digest["legacyHint"] = LEGACY_HINT
        return digest

    # --- preferences ---------------------------------------------------------------
    def preferences(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT key, value_json FROM preferences").fetchall()
        prefs = json.loads(json.dumps(PREF_DEFAULTS))
        prefs.update({r["key"]: json.loads(r["value_json"]) for r in rows if r["key"] in PREF_DEFAULTS})
        return prefs

    def set_preferences(self, **fields) -> dict:
        unknown = set(fields) - set(PREF_DEFAULTS)
        if unknown:
            raise ValueError(f"unknown preference(s): {', '.join(sorted(unknown))}")
        clean = {key: _validate(key, value) for key, value in fields.items() if value is not None}
        with self._lock, self._db:
            if "load_anchors" in clean:
                # Merge into the saved anchors; a None value removes that anchor.
                row = self._db.execute("SELECT value_json FROM preferences WHERE key = 'load_anchors'").fetchone()
                anchors = json.loads(row["value_json"]) if row else {}
                for group_id, weight in clean["load_anchors"].items():
                    if weight is None:
                        anchors.pop(group_id, None)
                    else:
                        anchors[group_id] = weight
                clean["load_anchors"] = anchors
            for key, value in clean.items():
                self._db.execute("INSERT INTO preferences (key, value_json) VALUES (?, ?) "
                                 "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                                 (key, json.dumps(value)))
        return self.preferences()

    # --- exercise marks ------------------------------------------------------------
    def mark_exercise(self, group_id: int, mark: str, name: str = "", reason: str = "") -> dict:
        mark = (mark or "").strip().lower()
        if mark not in MARKS + ("none",):
            raise ValueError("mark must be 'preferred', 'avoided' or 'none'")
        reason = (reason or "").strip()
        if len(reason) > MAX_REASON:
            raise ValueError(f"reason can't be longer than {MAX_REASON} characters")
        with self._lock, self._db:
            if mark == "none":
                self._db.execute("DELETE FROM exercise_marks WHERE group_id = ?", (int(group_id),))
            else:
                self._db.execute(
                    "INSERT INTO exercise_marks (group_id, mark, name, updated_at, reason) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET mark = excluded.mark, name = excluded.name, "
                    "updated_at = excluded.updated_at, reason = excluded.reason",
                    (int(group_id), mark, name or "", _iso(self._now()), reason))
        return {"groupId": int(group_id), "mark": mark, "name": name or "", "reason": reason}

    def marks(self) -> dict[int, dict]:
        with self._lock:
            rows = self._db.execute("SELECT group_id, mark, name, reason FROM exercise_marks").fetchall()
        return {r["group_id"]: {"mark": r["mark"], "name": r["name"], "reason": r["reason"]} for r in rows}

    # --- off-machine training ---------------------------------------------------------------------
    def log_offmachine_sets(self, day: str, sets: list[dict], *, training_id: int | None = None,
                            location: str = "") -> list[dict]:
        """Record the sets of one off-machine session; returns that day's stored rows.

        Every set is validated BEFORE anything is written, inside one transaction, so a
        batch with one bad entry stores nothing. A half-logged session is worse than an
        unlogged one: it silently understates volume and can fake a personal best.
        """
        day = _offmachine_day(day)
        if not sets:
            raise ValueError("no sets given")
        location = (location or "").strip()[:OFFMACHINE_MAX_LOCATION]
        linked = int(training_id) if training_id not in (None, "") else None
        prepared, counters = [], {}
        for entry in sets:
            entry = entry or {}
            name = str(entry.get("name") or "").strip()[:OFFMACHINE_MAX_NAME]
            if not name:
                raise ValueError("every set needs an exercise name")
            group_id = entry.get("groupId")
            group_id = int(group_id) if group_id not in (None, "") else None
            # Set numbers run per movement, so "set 2" is the second set of THAT exercise.
            key = (group_id, name.lower())
            counters[key] = counters.get(key, 0) + 1
            prepared.append((day, linked, group_id, name, counters[key],
                             _offmachine_reps(entry.get("reps")),
                             _offmachine_weight(entry.get("weight")),
                             _offmachine_side(entry.get("side")), location,
                             str(entry.get("note") or "").strip()[:OFFMACHINE_MAX_NOTE],
                             _iso(self._now())))
        with self._transaction() as db:
            db.executemany(
                "INSERT INTO offmachine_sets (day, training_id, group_id, name, set_index, reps, "
                "weight, side, location, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                prepared)
        return self.offmachine_sets(day, day)

    def offmachine_sets(self, start: str, end: str) -> list[dict]:
        """Every off-machine set between two dates, inclusive, oldest first."""
        start, end = _offmachine_day(start), _offmachine_day(end)
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM offmachine_sets WHERE day >= ? AND day <= ? "
                "ORDER BY day, name, set_index, id", (start, end)).fetchall()
        return [_offmachine_row(r) for r in rows]

    def offmachine_for_session(self, training_id: int | None = None,
                               day: str | None = None) -> list[dict]:
        """Sets belonging to one Speediance manual session, by link then by date.

        A set may predate the Speediance manual record it belongs to, so the day is a
        fallback. An explicit training_id match wins, so linked sets are never mixed with
        unrelated work that merely shares the date.
        """
        with self._lock:
            if training_id not in (None, ""):
                rows = self._db.execute(
                    "SELECT * FROM offmachine_sets WHERE training_id = ? ORDER BY name, set_index, id",
                    (int(training_id),)).fetchall()
                if rows:
                    return [_offmachine_row(r) for r in rows]
            if day in (None, ""):
                return []
            rows = self._db.execute(
                "SELECT * FROM offmachine_sets WHERE day = ? ORDER BY name, set_index, id",
                (_offmachine_day(day),)).fetchall()
        return [_offmachine_row(r) for r in rows]

    def delete_offmachine_day(self, day: str) -> int:
        """Remove every set logged for one day; returns how many rows went."""
        day = _offmachine_day(day)
        with self._transaction() as db:
            return db.execute("DELETE FROM offmachine_sets WHERE day = ?", (day,)).rowcount


# --- off-machine helpers ----------------------------------------------------------------------
OFFMACHINE_MAX_NAME = 120
OFFMACHINE_MAX_NOTE = 300
OFFMACHINE_MAX_LOCATION = 60
OFFMACHINE_SIDES = ("both", "left", "right")
# The machine's own leftRight vocabulary, so an adapted set can be handed to code that
# already knows how to read one: 1 = left only, 2 = right only, 0 = both together.
OFFMACHINE_SIDE_CODES = {"left": 1, "right": 2, "both": 0}


def _offmachine_day(value) -> str:
    """A YYYY-MM-DD string, or ValueError. Accepts a date/datetime or an ISO string."""
    if isinstance(value, (dt.date, dt.datetime)):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()[:10]
    dt.date.fromisoformat(text)  # raises ValueError on anything malformed
    return text


def _offmachine_reps(value) -> int:
    try:
        reps = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"reps must be a whole number, got {value!r}")
    if reps <= 0:
        raise ValueError(f"reps must be greater than zero, got {reps}")
    return reps


def _offmachine_weight(value) -> float:
    """Load for one set. Zero is legitimate — a bodyweight movement is not an error."""
    if value in (None, ""):
        return 0.0
    try:
        weight = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"weight must be a number, got {value!r}")
    if weight < 0:
        raise ValueError(f"weight cannot be negative, got {weight}")
    return round(weight, 2)


def _offmachine_side(value) -> str:
    side = str(value or "both").strip().lower()
    if side not in OFFMACHINE_SIDES:
        raise ValueError(f"side must be one of {OFFMACHINE_SIDES}, got {value!r}")
    return side


def _offmachine_row(row) -> dict:
    return {"id": row["id"], "day": row["day"], "trainingId": row["training_id"],
            "groupId": row["group_id"], "name": row["name"], "setIndex": row["set_index"],
            "reps": row["reps"], "weight": row["weight"], "side": row["side"],
            "location": row["location"], "note": row["note"]}


def _validate(key: str, value):
    if key == "goal":
        return str(value).strip()
    if key == "training_days":
        if not isinstance(value, list):
            raise ValueError("training_days must be a list of day names")
        return [str(d).strip() for d in value if str(d).strip()]
    if key == "session_minutes":
        minutes = int(value)
        if minutes <= 0:
            raise ValueError("session_minutes must be positive")
        return minutes
    if key == "load_anchors":
        if not isinstance(value, dict):
            raise ValueError("load_anchors must map group_id -> weight")
        try:
            # None or 0 marks an anchor for removal (see Memory.set_preferences).
            return {str(int(k)): (None if v is None or float(v) == 0 else float(v)) for k, v in value.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError("load_anchors must map a numeric group_id to a numeric weight") from exc
    if key in ("owned_equipment", "unusable_equipment"):
        # unusable_equipment is the "own it, can't use it" list: a flat bench someone
        # cannot lie on is still owned, but must never be planned into a workout.
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list of accessory names")
        return [str(n).strip() for n in value if str(n).strip()]
    raise ValueError(f"unknown preference: {key}")


# --- curated fact helpers ---------------------------------------------------------------------------
def _enum(name: str, value, allowed: tuple[str, ...]) -> str:
    clean = str(value if value is not None else "").strip().lower()
    if clean not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)} (got {value!r}).")
    return clean


def _clean_fact(item: dict) -> dict:
    """Apply every single-write validation rule; raise ValueError with a caller-facing message."""
    text = item.get("text")
    if text is not None and not isinstance(text, str):
        raise ValueError(f"Fact text must be a string (got {type(text).__name__}).")
    text = (text or "").strip()
    if not text:
        raise ValueError("Fact text can't be empty.")
    if len(text) > FACT_TEXT_LIMIT:
        raise ValueError(f"Fact is {len(text):,} characters; the limit is {FACT_TEXT_LIMIT}. "
                         "Split it into smaller facts.")
    kind = _enum("kind", item.get("kind"), FACT_KINDS)
    category = _enum("category", item.get("category"), FACT_CATEGORIES)
    severity = item.get("severity")
    if kind == "constraint":
        if severity in (None, ""):
            raise ValueError("A constraint needs a severity: 'hard' (must never be violated) or 'soft'.")
        severity = _enum("severity", severity, FACT_SEVERITIES)
    elif severity not in (None, ""):
        raise ValueError(f"severity applies only to constraints (this fact's kind is {kind}).")
    else:
        severity = None
    source = _enum("source", item.get("source") or "inferred", FACT_SOURCES)
    scope = item.get("scope")
    scope = str(scope).strip().lower() or None if scope is not None else None
    if scope and len(scope) > MAX_SCOPE:
        raise ValueError(f"scope can't be longer than {MAX_SCOPE} characters.")
    raw = item.get("supersedes")
    raw = [] if raw is None else raw if isinstance(raw, list) else [raw]
    supersedes: list[int] = []
    for value in raw:
        if isinstance(value, bool) or not (isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit())):
            raise ValueError(f"supersedes must be a list of fact ids (got {value!r}).")
        if int(value) not in supersedes:
            supersedes.append(int(value))
    days = item.get("expires_days")
    if days is None:
        days = 0
    elif isinstance(days, bool) or not isinstance(days, int):
        raise ValueError(f"expires_days must be a whole number of days (got {days!r}); leave it empty for a "
                         "durable fact.")
    if days < 0:
        raise ValueError("expires_days can't be negative (leave it empty for a durable fact).")
    if days > MAX_EXPIRES_DAYS:
        raise ValueError(f"expires_days can't be more than {MAX_EXPIRES_DAYS} (leave it empty for a durable fact).")
    return {"text": text, "kind": kind, "category": category, "severity": severity, "scope": scope,
            "supersedes": supersedes, "expires_days": days, "source": source}


def _expired(row, now_iso: str) -> bool:
    return row["expires_at"] is not None and row["expires_at"] <= now_iso


def _is_active(row, now_iso: str) -> bool:
    return (row["superseded_by"] is None and row["archived_at"] is None and not row["legacy"]
            and not _expired(row, now_iso))


def _legacy_pending(row, now_iso: str) -> bool:
    """A migrated legacy fact nobody has superseded or dismissed yet (and that hasn't expired)."""
    return (bool(row["legacy"]) and row["superseded_by"] is None and row["archived_at"] is None
            and not _expired(row, now_iso))


def _why_archived(row, now_iso: str) -> str:
    if row["superseded_by"] is not None:
        return f"superseded by #{row['superseded_by']}"
    if row["archived_at"] is not None:
        return "forgotten"
    if _expired(row, now_iso):
        return "expired"
    return "legacy"


def _fact_dict(row, now_iso: str) -> dict:
    return {"id": row["id"], "text": row["text"], "kind": row["kind"], "severity": row["severity"],
            "category": row["category"], "scope": row["scope"], "supersedes": json.loads(row["supersedes_json"]),
            "supersededBy": row["superseded_by"], "status": "active" if _is_active(row, now_iso) else "archived",
            "source": row["source"], "createdAt": row["created_at"], "expiresAt": row["expires_at"],
            "archivedAt": row["archived_at"], "legacy": bool(row["legacy"])}


def _read_item(row) -> dict:
    """The compact shape planning reads return (every item is active, its kind is its group)."""
    item = {"id": row["id"], "text": row["text"], "category": row["category"]}
    if row["severity"]:
        item["severity"] = row["severity"]
    if row["scope"]:
        item["scope"] = row["scope"]
    item.update({"source": row["source"], "createdAt": row["created_at"]})
    if row["expires_at"]:
        item["expiresAt"] = row["expires_at"]
    supersedes = json.loads(row["supersedes_json"])
    if supersedes:
        item["supersedes"] = supersedes
    return item


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def similarity(a: str, b: str) -> float:
    """max(SequenceMatcher ratio, Jaccard of word sets) over normalised text."""
    na, nb = _normalise(a), _normalise(b)
    if not na or not nb:
        return 0.0
    wa, wb = set(na.split()), set(nb.split())
    jaccard = len(wa & wb) / len(wa | wb)
    return max(difflib.SequenceMatcher(None, na, nb).ratio(), jaccard)


NEGATIONS = {"not", "no", "never", "cannot", "can't", "don't", "without", "unable", "cant", "dont"}
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "am", "of", "to", "in", "on", "at",
    "for", "with", "and", "or", "but", "by", "from", "as", "it", "its", "it's", "this", "that", "these", "those",
    "i", "i'm", "my", "me", "he", "she", "they", "their", "his", "her", "we", "our", "you", "your", "user",
    "has", "have", "had", "do", "does", "did", "can", "will", "would", "should", "could", "may", "might", "so",
    "if", "than", "then", "after", "before", "into", "over", "per", "about", "now", "just", "also", "very",
    "s", "t",
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_UNIT_AFTER = re.compile(r" ?(?:(?:m|km|mi|cal|kcal|kj|lb|lbs|kg|s|sec|min|reps|spm|w|watts)\b|%)")
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")


def _numbers(text: str) -> collections.Counter:
    """The numbers in a text as a multiset, ignoring 4-digit years (1900-2100)."""
    found = collections.Counter()
    lower = text.lower()
    for match in _NUMBER.finditer(lower):
        token = match.group()
        if ("." not in token and len(token) == 4 and 1900 <= int(token) <= 2100
                and not _UNIT_AFTER.match(lower, match.end())):
            continue  # a year; the same digits followed by a unit (2000 m) are a quantity
        found[format(float(token), "g")] += 1
    return found


def _number_difference(a: str, b: str) -> str | None:
    """"185 vs 205" when the numbers differ (symmetric multiset difference), else None."""
    na, nb = _numbers(a), _numbers(b)
    only_a, only_b = na - nb, nb - na
    if not only_a and not only_b:
        return None
    show = lambda nums: ", ".join(sorted(nums.elements(), key=float)) or "none"  # noqa: E731
    return f"{show(only_a)} vs {show(only_b)}"


def _analyse(text: str) -> tuple[set[str], bool]:
    lower = text.lower().replace("\u2019", "'")
    words = _WORD.findall(lower)
    terms = {w for w in words if w not in STOPWORDS and w not in NEGATIONS}
    return terms, any(w in NEGATIONS for w in words)


def _different_scopes(a: str | None, b: str | None) -> bool:
    """Both facts are scoped, to different contexts: they can't duplicate or contradict each other."""
    return bool(a) and bool(b) and a != b


def _label(kind: str, severity: str | None) -> str:
    if kind == "constraint":
        return f"a {severity} constraint"
    return "an observation" if kind == "observation" else f"a {kind}"


def _differences(existing: dict, new: dict) -> list[str]:
    """What separates a near-match from the fact being saved (so an upgrade isn't mistaken for a repeat)."""
    diffs = []
    ref = f"existing #{existing['id']}"
    if (existing["kind"], existing["severity"]) != (new["kind"], new["severity"]):
        diffs.append(f"{ref} is {_label(existing['kind'], existing['severity'])}; "
                     f"you're saving {_label(new['kind'], new['severity'])}")
    if existing["category"] != new["category"]:
        diffs.append(f"{ref} is category {existing['category']}; you're saving {new['category']}")
    if (existing["scope"] or None) != (new["scope"] or None):
        diffs.append(f"{ref} has {'scope ' + existing['scope'] if existing['scope'] else 'no scope'}; "
                     f"yours has {'scope ' + new['scope'] if new['scope'] else 'no scope'}")
    numbers = _number_difference(existing["text"], new["text"])
    if numbers:
        diffs.append(f"numbers differ: {numbers}")
    neg_existing, neg_new = _analyse(existing["text"])[1], _analyse(new["text"])[1]
    if neg_existing != neg_new:
        diffs.append(f"{ref} is negated and yours isn't" if neg_existing else f"yours is negated and {ref} isn't")
    return diffs


def find_conflicts(facts: list[dict]) -> list[dict]:
    """Pairs of active facts in the same category (and not scoped to different contexts) that share most
    of their terms (Jaccard >= 0.5) but differ on a number (years ignored) or on exactly one being negated.
    Both ids and texts are surfaced; neither is picked."""
    analysed = [(f, *_analyse(f["text"])) for f in sorted(facts, key=lambda f: f["id"])]
    conflicts = []
    for i, (a, terms_a, neg_a) in enumerate(analysed):
        for b, terms_b, neg_b in analysed[i + 1:]:
            if a["category"] != b["category"] or not terms_a or not terms_b:
                continue
            if _different_scopes(a.get("scope"), b.get("scope")):
                continue
            if len(terms_a & terms_b) / len(terms_a | terms_b) < CONFLICT_OVERLAP:
                continue
            reasons = []
            numbers = _number_difference(a["text"], b["text"])
            if numbers:
                reasons.append(f"numbers differ: {numbers}")
            if neg_a != neg_b:
                reasons.append(f"only #{a['id'] if neg_a else b['id']} is negated")
            if reasons:
                conflicts.append({"ids": [a["id"], b["id"]], "texts": [a["text"], b["text"]],
                                  "reason": "same topic, " + "; ".join(reasons)})
    return conflicts
