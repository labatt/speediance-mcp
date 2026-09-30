from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from speediance_mcp.speediance import routes
from speediance_mcp.speediance.api import SessionNotFound, SpeedianceAPI
from speediance_mcp.speediance.client import SpeedianceClient
from tests import fixtures as fx
from tests.helpers import CREDS, FakeSpeediance, api_error


class TestRoutes(unittest.TestCase):
    def test_known_types(self):
        self.assertEqual(routes.routes_to_try(1), ("freeTraining",))
        self.assertEqual(routes.routes_to_try(7), ("freeTraining",))
        self.assertEqual(routes.routes_to_try(2), ("courseTrainingInfoDetail",))
        self.assertEqual(routes.routes_to_try(5), ("cttTrainingInfoDetail",))
        self.assertEqual(routes.routes_to_try(9), ("aiCourseTrainingInfoDetail",))

    def test_unknown_type_tries_everything(self):
        self.assertEqual(routes.routes_to_try(42), routes.ALL_DETAIL_ROUTES)
        self.assertEqual(routes.routes_to_try(None), routes.ALL_DETAIL_ROUTES)

    def test_detail_path(self):
        self.assertEqual(routes.detail_path("freeTraining", "6001"), "/api/app/trainingInfo/freeTraining/6001")


class TestAPI(unittest.TestCase):
    def make(self, route_overrides=None, clock=None):
        table = fx.standard_routes()
        table.update(route_overrides or {})
        fake = FakeSpeediance(table)
        client = SpeedianceClient(CREDS, transport=fake.transport(), min_interval=0)
        self.addCleanup(client.close)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        kwargs = {"today": lambda: fx.TODAY}
        if clock:
            kwargs["clock"] = clock
        return SpeedianceAPI(client, Path(tmp.name), **kwargs), fake, Path(tmp.name)

    def test_history_passes_dates(self):
        api, fake, _ = self.make()
        rows = api.history("2026-08-01", "2026-08-31")
        self.assertEqual({r["trainingId"] for r in rows}, {5000, 5001, 7001, 7002, None})
        self.assertEqual(fake.requests[0].url.params["startDate"], "2026-08-01")

    def test_history_index_is_cached(self):
        api, fake, _ = self.make(clock=lambda: 1000.0)
        api.history_index()
        api.history_index()
        self.assertEqual(len(fake.calls("GET", fx.HISTORY_PATH)), 1)
        self.assertNotIn(None, api.history_index())

    def test_find_session_owned_and_missing(self):
        api, fake, _ = self.make()
        self.assertEqual(api.find_session(5001)["title"], "Pull Day")
        with self.assertRaises(SessionNotFound):
            api.find_session(99999)
        self.assertFalse([r for r in fake.requests if "/trainingInfo/" in r.url.path])

    def test_session_payload_uses_the_type_route(self):
        api, fake, _ = self.make()
        route, payload = api.session_payload(api.find_session(6001))
        self.assertEqual(route, "freeTraining")
        self.assertEqual(payload["actionList"][0]["groupId"], 424)

    def test_unknown_type_falls_back_past_wrong_namespace(self):
        record = {"trainingId": 5001, "type": 42}
        api, fake, _ = self.make({("GET", fx.DETAIL + "cttTrainingInfoDetail/5001"):
                                  api_error(1, "Sorry. You do not have access."),
                                  ("GET", fx.DETAIL + "courseTrainingInfoDetail/5001"): fx.CTT_5001})
        route, payload = api.session_payload(record)
        self.assertEqual(route, "courseTrainingInfoDetail")
        self.assertEqual(payload, fx.CTT_5001)

    def test_session_summary(self):
        api, _, _ = self.make()
        self.assertEqual(api.session_summary("courseTrainingInfoDetail", 7001), fx.ROWING_SUMMARY_7001)
        self.assertEqual(api.session_summary("freeTraining", 6001), {})
        self.assertEqual(api.session_summary("cttTrainingInfoDetail", 404), {})

    def test_exercise_stats_pages_until_short_page(self):
        rows = [{"dayStr": f"2026-01-{d:02d}", "maxWeight": 10.0} for d in range(1, 29)] * 2  # 56 rows
        api, fake, _ = self.make({("GET", fx.STATS_PATH): fx.stats_route({321: rows})})
        self.assertEqual(len(api.exercise_stats(321, max_weeks=100)), 56)
        self.assertEqual(len(fake.calls("GET", fx.STATS_PATH)), 2)
        self.assertEqual(len(api.exercise_stats(321, max_weeks=10)), 10)

    def test_library_fetches_once_then_uses_disk_cache(self):
        api, fake, home = self.make(clock=lambda: 5000.0)
        items = api.library()
        self.assertEqual({i["id"] for i in items}, {321, 416, 294, 600, 700, 900, 424})
        batch = fake.calls("GET", "/api/app/actionLibraryGroup/list")
        self.assertEqual(len(batch), 1)
        self.assertEqual(len(batch[0].url.params.get_list("ids")), 7)
        api.library()
        self.assertEqual(len(fake.calls("GET", "/api/app/actionLibraryTab/list")), 1)
        cached = json.loads(next(home.glob("library-*.json")).read_text(encoding="utf-8"))
        self.assertEqual(len(cached["items"]), 7)

    def test_stale_library_cache_is_refetched(self):
        now = {"t": 0.0}
        api, fake, _ = self.make(clock=lambda: now["t"])
        api.library()
        now["t"] = 25 * 3600.0
        api.library()
        self.assertEqual(len(fake.calls("GET", "/api/app/actionLibraryTab/list")), 2)

    def test_concurrent_cold_library_calls_share_one_download(self):
        import threading
        import time as _time
        tabs = fx.standard_routes()[("GET", "/api/app/actionLibraryTab/list")]

        def slow_tabs(request):
            _time.sleep(0.2)  # hold the download open so the second caller overlaps it
            return tabs(request) if callable(tabs) else tabs

        api, fake, _ = self.make({("GET", "/api/app/actionLibraryTab/list"): slow_tabs}, clock=lambda: 5000.0)
        barrier = threading.Barrier(2)
        results = []

        def call():
            barrier.wait(timeout=5)
            results.append(api.library())

        threads = [threading.Thread(target=call) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual([len(r) for r in results], [7, 7])
        self.assertEqual(len(fake.calls("GET", "/api/app/actionLibraryTab/list")), 1)

    def test_accessories_are_memoized(self):
        api, fake, _ = self.make()
        api.accessories()
        api.accessories()
        self.assertEqual(len(fake.calls("GET", "/api/app/accessories/list")), 1)

    def test_reserve_body(self):
        api, fake, _ = self.make()
        api.reserve("2026-09-01", "a" * 24, 1)
        body = json.loads(fake.calls("POST", fx.RESERVE_PATH)[0].content)
        self.assertEqual(body, {"status": 1, "deviceType": 1, "thatDay": "2026-09-01", "templateCode": "a" * 24})
