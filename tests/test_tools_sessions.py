from __future__ import annotations

import dataclasses
import unittest

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp.speediance.api import SessionNotFound
from speediance_mcp.tools import account, sessions
from tests import fixtures as fx
from tests.helpers import CREDS, api_error, make_app


class TestCheckConnection(unittest.TestCase):
    def test_connected(self):
        app, _ = make_app(self)
        got = account.check_connection(app)
        self.assertTrue(got["connected"])
        self.assertEqual((got["account"], got["displayUnit"], got["spUserId"]), ("athlete@example.com", "lb", "1001"))

    def test_not_logged_in(self):
        app, fake = make_app(self, creds=None)
        got = account.check_connection(app)
        self.assertFalse(got["connected"])
        self.assertIn("speediance-mcp login", got["message"])
        self.assertEqual(fake.requests, [])

    def test_expired_without_password(self):
        routes = fx.standard_routes()
        routes[("GET", "/api/app/userinfo/info")] = api_error(91, "Login expired")
        app, _ = make_app(self, routes)
        got = account.check_connection(app)
        self.assertEqual((got["connected"], got["action"]), (False, "login"))


class TestCalendar(unittest.TestCase):
    def test_merges_completed_sessions_and_keeps_reservations(self):
        app, _ = make_app(self)
        days = {d["date"]: d for d in sessions.get_calendar(app, "2026-08")["days"]}
        self.assertEqual(days["2026-08-29"]["trainingPlanList"][0]["source"], "history")
        self.assertEqual(days["2026-08-22"]["trainingPlanList"][0]["trainingId"], 5000)
        self.assertTrue(days["2026-08-30"]["trainingPlanList"][0]["isReservation"])
        walk_day = days["2026-08-20"]  # phone-health walks aren't gym sessions, but aren't dropped either
        self.assertEqual(walk_day.get("trainingPlanList", []), [])
        self.assertEqual(walk_day["otherActivities"],
                         [{"title": "Walk", "minutes": 11, "calorie": 30, "source": "phone health app"}])
        self.assertEqual(list(days), sorted(days))

    def test_days_without_a_date_are_skipped(self):
        routes = fx.standard_routes()
        routes[("GET", "/api/app/v5/trainingCalendar/monthNew")] = fx.CALENDAR_2026_08 + [
            {"isAllFinish": False, "trainingPlanList": []}, {"date": None, "trainingPlanList": []}]
        app, _ = make_app(self, routes)
        days = sessions.get_calendar(app, "2026-08")["days"]
        self.assertTrue(all(d["date"] for d in days))
        self.assertIn("2026-08-30", [d["date"] for d in days])

    def test_bad_month(self):
        app, _ = make_app(self)
        for bad in ("2026-13", "Aug", "2026-8"):
            with self.assertRaises(ToolError):
                sessions.get_calendar(app, bad)


class TestSessionDetail(unittest.TestCase):
    def test_strength_session(self):
        app, _ = make_app(self)
        got = sessions.get_session_detail(app, 5001)
        self.assertTrue(got["resolvedType"])
        self.assertEqual(got["detailType"], "cttTrainingInfoDetail")
        self.assertEqual(got["displayUnit"], "lb")
        self.assertEqual([e["name"] for e in got["exercises"]], ["Barbell Bent Over Row", "Cable Fly"])
        self.assertTrue(got["heartRateAvailable"])
        self.assertNotIn("cardio", got)

    def test_session_not_in_history_reads_nothing(self):
        app, fake = make_app(self)
        got = sessions.get_session_detail(app, 99999)
        self.assertFalse(got["resolvedType"])
        self.assertEqual(got["exercises"], [])
        self.assertIn("isn't in this account's training history", got["message"])
        self.assertFalse([r for r in fake.requests if "/trainingInfo/" in r.url.path])

    def test_caller_type_is_ignored_for_routing(self):
        app, _ = make_app(self)
        self.assertEqual(sessions.get_session_detail(app, 6001, type=5)["detailType"], "freeTraining")

    def test_course_rowing_has_cardio_and_per_block_telemetry(self):
        app, _ = make_app(self)
        got = sessions.get_session_detail(app, 7001)
        self.assertEqual(got["cardio"]["pace500"], 296.8)
        self.assertNotIn("note", got)

        rowing = got["rowing"]
        self.assertTrue(rowing["available"])
        self.assertEqual([b["targetStrokeRate"] for b in rowing["blocks"]], ["20-24", "24-28"])
        # The two spm-0 samples are the flywheel spinning up: rest, excluded from the rates.
        self.assertEqual((rowing["workingSec"], rowing["restingSec"]), (12, 6))
        self.assertEqual(rowing["bestPace500"], 215.0)
        self.assertEqual(rowing["blocks"][0]["inTargetPercent"], 100.0)

    def test_rowing_telemetry_is_fetched_by_uuid_not_training_id(self):
        app, fake = make_app(self)
        sessions.get_session_detail(app, 7001)
        graph = [r.url.path for r in fake.requests if "boatingSkiDataGraph" in r.url.path]
        self.assertEqual(graph, ["/api/app/boatingSkiDataGraph/row-uuid-7001"])

    def test_rowing_flagged_but_nothing_recorded_falls_back_to_the_note(self):
        app, _ = make_app(self)
        got = sessions.get_session_detail(app, 7004)
        self.assertNotIn("rowing", got)
        self.assertEqual(got["note"], sessions.ROWING_GAP_NOTE)

    def test_guided_rowing_has_intervals(self):
        app, _ = make_app(self)
        got = sessions.get_session_detail(app, 7002)
        self.assertEqual(len(got["intervals"]), 2)
        self.assertNotIn("note", got)

    def test_guided_rowing_without_intervals_gets_no_course_note(self):
        routes = fx.standard_routes()
        routes[("GET", fx.DETAIL + "freeTrainingDetail/7002")] = []
        app, _ = make_app(self, routes)
        got = sessions.get_session_detail(app, 7002)
        self.assertIn("cardio", got)
        self.assertNotIn("note", got)

    def test_personal_bests_are_listed_per_exercise_and_summarised(self):
        routes = fx.standard_routes()
        routes[("GET", fx.DETAIL + "cttTrainingInfoDetail/5001")] = fx.CTT_5001_PB
        app, _ = make_app(self, routes)
        got = sessions.get_session_detail(app, 5001)
        self.assertEqual(got["exercises"][0]["personalBests"], ["weight", "volume"])
        self.assertEqual(got["personalBests"], [{"exercise": "Barbell Bent Over Row",
                                                 "kinds": ["weight", "volume"]}])

    def test_session_without_flags_has_an_empty_summary(self):
        app, _ = make_app(self)
        for training_id in (5001, 6001):
            self.assertEqual(sessions.get_session_detail(app, training_id)["personalBests"], [])

    def test_free_lift(self):
        app, _ = make_app(self)
        got = sessions.get_session_detail(app, 6001)
        self.assertEqual(got["exercises"][0]["weights"], [100.0, 100.0])
        self.assertFalse(got["heartRateAvailable"])

    def test_quick_single_exercise_session_falls_back_to_free_training_detail(self):
        # freeTraining for a type-7 quick strength session has no actionList at all; the real
        # per-set data only shows up at freeTrainingDetail, shaped like the list routes.
        app, _ = make_app(self)
        got = sessions.get_session_detail(app, 6002)
        self.assertEqual(got["detailType"], "freeTraining")
        self.assertEqual(len(got["exercises"]), 1)
        exercise = got["exercises"][0]
        self.assertEqual(exercise["name"], "Standing Dumbbell Curl")
        self.assertEqual(exercise["groupId"], 950)
        self.assertEqual(exercise["weights"], [20.0, 25.0])
        self.assertEqual(exercise["topWeight"], 25.0)
        self.assertEqual(exercise["volume"], 490.0)
        self.assertNotIn("cardio", got)
        self.assertNotIn("intervals", got)
        self.assertNotIn("note", got)


class TestHeartRateAndStats(unittest.TestCase):
    def test_heart_rate(self):
        app, fake = make_app(self)
        got = sessions.get_heart_rate(app, 5001)
        self.assertEqual((got["available"], got["samples"], got["avg"], got["max"]), (True, 2, 135.0, 150.0))
        self.assertEqual(fake.calls("GET", "/api/app/watchMsg/getHeartRateGraph")[0].url.params["uuid"], "hr-uuid-5001")

    def test_heart_rate_unavailable(self):
        app, _ = make_app(self)
        self.assertFalse(sessions.get_heart_rate(app, 6001)["available"])

    def test_heart_rate_unknown_session(self):
        app, _ = make_app(self)
        with self.assertRaises(SessionNotFound):
            sessions.get_heart_rate(app, 99999)

    def test_training_stats(self):
        app, _ = make_app(self)
        got = sessions.get_training_stats(app, "2026-08-01", "2026-08-31")
        self.assertEqual((got["sessions"], got["strengthSessions"], got["trainingMinutes"]), (4, 2, 90))
        self.assertIn("phone health app", got["note"])

    def test_training_stats_validation(self):
        app, _ = make_app(self)
        with self.assertRaises(ToolError):
            sessions.get_training_stats(app, "2026-08-31", "2026-08-01")
        with self.assertRaises(ToolError):
            sessions.get_training_stats(app, "yesterday", "2026-08-01")

    def test_no_credentials_raises_login_hint(self):
        app, _ = make_app(self, creds=None)
        with self.assertRaises(ToolError) as cm:
            sessions.get_calendar(app, "2026-08")
        self.assertIn("speediance-mcp login", str(cm.exception))
