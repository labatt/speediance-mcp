from __future__ import annotations

import unittest

from speediance_mcp.speediance import health
from speediance_mcp.tools import coaching, health as health_tools
from tests import fixtures as fx
from tests.helpers import make_app

# Trimmed from a live account, 2026-09-30.
CONDITION = {
    "physicalConditionResp": {"dateStr": "2026-08-31", "physicalConditionValue": 0.67, "physicalConditionScore": 62.2,
                              "fitness": 96.86, "fatigue": 81.92, "trainingStatus": 0.85},
    "physicalConditionEvaluateTitle": {"physicalConditionEvaluateTitle": 22},
    "physicalConditionParam": {"trainingStatusScore": 82.0, "trainingStatusScoreAvg": 73.88,
                               "sleepScore": 90.0, "sleepScoreAvg": 86.8},
}
SLEEP = {
    "sleep": 31248.0, "startTimeStamp": 1790717970, "endTimeStamp": 1790750873, "sleepScore": 90,
    "dtos": [{"sleepType": 7, "sleep": 0.0}, {"sleepType": 6, "sleep": 0.0}, {"sleepType": 2, "sleep": 1620.0},
             {"sleepType": 5, "sleep": 3852.0}, {"sleepType": 4, "sleep": 18792.0}, {"sleepType": 3, "sleep": 8604.0},
             {"sleepType": 1, "sleep": 31248.0}],
}
SLEEP_STANDARD = {"remList": [0.16, 0.28], "awakeList": [0.02, 0.08], "deepList": [0.09, 0.21],
                  "coreList": [0.48, 0.75]}
FULL_DAY = {
    "stress": {"type": 57, "value": 31.0, "createTimestamp": 1790685941,
               "extData": "{\"isAbnormal\":true,\"upperBound\":88,\"lowerBound\":42,\"hasBasicData\":true}"},
    "restingHeartRate": {"type": 23, "value": 52.0, "createTimestamp": 1790669364,
                         "extData": "{\"isAbnormal\":false,\"upperBound\":59,\"lowerBound\":51,\"hasBasicData\":true}"},
    "bloodOxygen": {"type": 25, "value": 0.95, "createTimestamp": 1790685946,
                    "extData": "{\"upperBound\":1.0,\"lowerBound\":0.9,\"hasBasicData\":true}"},
    "respiratoryRate": {"type": 26, "value": 17.0, "createTimestamp": 1790667650,
                        "extData": "{\"isAbnormal\":true,\"upperBound\":16.4,\"lowerBound\":13.2,\"hasBasicData\":true}"},
}
OVERNIGHT = {
    "stress": {"type": 62, "extData": "{\"hasBasicData\":false,\"missingDays\":4}",
               "trendList": [{"date": "2026-09-29 06:45:30", "val": 51.5}, {"date": "2026-09-28 06:56:53", "val": 84.43}]},
    "bloodOxygen": {"type": 60, "extData": "{\"hasBasicData\":false,\"missingDays\":0}",
                    "trendList": [{"date": "2026-09-29 12:45:46", "minVal": 0.91, "maxVal": 0.98}]},
    "skinTemperature": {"type": 64, "trendList": [{"date": "2026-09-28 06:56:53", "val": 36.04}]},
}
MUSCLES = [
    {"trainingPartId2": 11, "muscleDetailList": [
        {"isTrained": True, "intensityLevel": 3, "fatigue": 3, "muscleGroupName": "Pecs"}]},
    {"trainingPartId2": 12, "muscleDetailList": [
        {"isTrained": True, "intensityLevel": 2, "fatigue": 2, "muscleGroupName": "Side Delts"},
        {"isTrained": False, "fatigue": 1, "muscleGroupName": "Rear Delts"}]},
]
PARTS = [{"trainingPartId2": 11, "fatigue": 3, "muscleLoadStatus": 5},
         {"trainingPartId2": 12, "fatigue": 2, "muscleLoadStatus": 4}]
HOME = {"trainingLoadResp": {"trainingLoadUnit": "TRIMP", "recommendMin": 12, "recommendMax": 130}}
BODY = {"weight": {"value": 71.4, "createTimestamp": 1790315659}, "bodyFatPercent": {"value": 16.3},
        "bodyFatScaleWeight": {"type": 40},
        "weightWithOutFat": {"value": 59.8, "trendList": [{"date": "2026-09-25", "val": 131.5}]}}
SCORES = {"crfScore": {"value": 65.42}, "walking": {"value": 888.0},
          "bodyAge": {"value": 36.2, "extData": "{\"date\":\"2026-09-27\",\"age\":45.50}"}}

HEALTH = "/api/mobile/userHealth/"


def health_routes(overrides=None):
    routes = fx.standard_routes()
    routes.update({
        ("GET", HEALTH + "physicalCondition/detailByDate"): CONDITION,
        ("GET", HEALTH + "sleep"): SLEEP,
        ("GET", HEALTH + "sleepStandard"): SLEEP_STANDARD,
        ("GET", HEALTH + "monitor/detail"): FULL_DAY,
        ("GET", HEALTH + "monitor/index"): OVERNIGHT,
        ("GET", "/api/app/userDataStat/trainingMuscleDetail"): MUSCLES,
        ("GET", "/api/app/userDataStat/trainingPartFatigueInfo"): PARTS,
        ("GET", "/api/mobile/homePageInfo/topIndicatorVal"): HOME,
        ("GET", HEALTH + "newIndex/healthData"): BODY,
        ("GET", HEALTH + "newIndex/healthScore"): SCORES,
    })
    routes.update(overrides or {})
    return routes


class TestParsing(unittest.TestCase):
    def test_training_status_ratio_is_fatigue_over_fitness(self):
        got = health.training_status(CONDITION)
        self.assertEqual(got["loadRatio"], round(81.92 / 96.86, 2))
        self.assertEqual(got["loadBand"], "optimal")
        self.assertEqual((got["conditionScore"], got["trainingStatusScore"], got["sleepScoreAvg"]), (62.2, 82.0, 86.8))
        self.assertIsNone(health.training_status({}))

    def test_load_bands(self):
        self.assertEqual([health.load_band(r) for r in (0.27, 0.8, 1.3, 1.31, 1.6, None)],
                         ["detraining", "optimal", "optimal", "high", "very high", None])

    def test_sleep_stages_against_the_healthy_ranges(self):
        got = health.sleep_night(SLEEP, SLEEP_STANDARD)
        self.assertEqual((got["asleep"], got["asleepMinutes"], got["score"]), ("8h40m", 521, 90))
        self.assertEqual({k: v["minutes"] for k, v in got["stages"].items()},
                         {"deep": 64, "core": 313, "rem": 143, "awake": 27})
        in_bed = 31248 + 1620
        self.assertEqual(got["stages"]["rem"]["share"], round(8604 / in_bed, 3))
        self.assertEqual(got["stages"]["deep"]["normalRange"], [0.09, 0.21])
        self.assertTrue(all(s["inRange"] for s in got["stages"].values()))

    def test_no_sleep_is_none_not_zero(self):
        self.assertIsNone(health.sleep_night({"sleep": 0, "dtos": []}))
        self.assertIsNone(health.sleep_night({}))

    def test_wellness_reads_against_the_users_own_band(self):
        got = {e["key"]: e for e in health.wellness(FULL_DAY, OVERNIGHT)}
        self.assertEqual(got["stress"]["label"], "HRV")
        self.assertEqual(got["stress"]["fullDay"]["status"], "below")
        self.assertEqual(got["restingHeartRate"]["fullDay"]["status"], "normal")
        self.assertEqual(got["respiratoryRate"]["fullDay"]["status"], "above")
        self.assertEqual(got["stress"]["overnight"]["latest"], 51.5)
        self.assertFalse(got["stress"]["overnight"]["baselineReady"])
        self.assertEqual(got["stress"]["overnight"]["missingDays"], 4)

    def test_blood_oxygen_shown_as_a_percentage(self):
        got = {e["key"]: e for e in health.wellness(FULL_DAY, OVERNIGHT)}["bloodOxygen"]
        self.assertEqual((got["fullDay"]["value"], got["fullDay"]["low"], got["fullDay"]["high"]), (95.0, 90.0, 100.0))
        self.assertEqual(got["overnight"]["latest"], {"min": 91.0, "max": 98.0})

    def test_indicator_with_only_an_overnight_reading_has_no_status(self):
        got = {e["key"]: e for e in health.wellness(FULL_DAY, OVERNIGHT)}["skinTemperature"]
        self.assertNotIn("fullDay", got)
        self.assertEqual(got["overnight"]["latest"], 36.04)

    def test_flags(self):
        self.assertEqual(health.flags(health.wellness(FULL_DAY, {})),
                         ["HRV 31.0 ms is below its usual 42.0-88.0",
                          "Respiratory rate 17.0 breaths/min is above its usual 13.2-16.4"])

    def test_muscle_recovery(self):
        got = health.muscle_recovery(MUSCLES, PARTS)
        self.assertEqual([(p["bodyPart"], p["loadStatus"]) for p in got], [("Chest", 5), ("Shoulders", 4)])
        self.assertEqual(got[1]["muscles"][1], {"muscle": "Rear Delts", "fatigue": 1, "intensity": None,
                                                "trained": False})
        self.assertEqual(health.fatigued(got), ["Pecs"])

    def test_body_metrics_use_latest_values_only(self):
        got = health.body_metrics(BODY)
        self.assertEqual(got["leanMass"]["value"], 59.8)
        self.assertEqual(got["bodyFatPercent"], {"value": 16.3, "at": None, "unit": "%"})
        self.assertNotIn("bodyFatScaleWeight", got)

    def test_scores(self):
        self.assertEqual(health.scores(SCORES), {"cardioFitness": 65.4,
                                                 "bodyAge": {"value": 36.2, "actualAge": 45.5, "youngerBy": 9.3}})
        self.assertEqual(health.scores({}), {})


class TestTools(unittest.TestCase):
    def test_get_recovery(self):
        app, fake = make_app(self, health_routes())
        got = health_tools.get_recovery(app)
        self.assertEqual(got["date"], fx.TODAY.isoformat())
        self.assertEqual(fake.calls("GET", HEALTH + "sleep")[0].url.params["dateStr"], fx.TODAY.isoformat())
        self.assertEqual(got["trainingStatus"]["loadBand"], "optimal")
        self.assertEqual(got["sleep"]["score"], 90)
        self.assertEqual(got["fatiguedMuscles"], ["Pecs"])
        self.assertEqual(len(got["outOfBaseline"]), 2)
        self.assertEqual(got["trainingLoadTarget"], {"unit": "TRIMP", "recommendMin": 12, "recommendMax": 130})
        self.assertEqual(set(got["feeds"].values()), {"ok"})
        self.assertNotIn("dataWarning", got)
        self.assertEqual(len(got["wellnessMonitor"][0]["overnight"]["trend"]), 2)

    def test_get_recovery_for_a_date(self):
        app, fake = make_app(self, health_routes())
        health_tools.get_recovery(app, date="2026-08-20")
        self.assertEqual(fake.calls("GET", HEALTH + "physicalCondition/detailByDate")[0].url.params["dateStr"],
                         "2026-08-20")

    def test_a_failed_feed_is_reported_not_hidden(self):
        routes = health_routes()
        del routes[("GET", HEALTH + "sleep")]
        app, _ = make_app(self, routes)
        got = health_tools.get_recovery(app)
        self.assertIsNone(got["sleep"])
        self.assertEqual(got["feeds"]["sleep"], "error")
        self.assertIn("sleep", got["dataWarning"])
        self.assertEqual(got["trainingStatus"]["fitness"], 96.9)

    def test_no_wearable_reads_as_empty(self):
        app, _ = make_app(self, health_routes({("GET", HEALTH + "sleep"): None,
                                               ("GET", HEALTH + "monitor/detail"): {}}))
        got = health_tools.get_recovery(app)
        self.assertEqual((got["feeds"]["sleep"], got["feeds"]["wellnessFullDay"]), ("empty", "empty"))
        self.assertNotIn("dataWarning", got)

    def test_readiness_trend_is_oldest_first(self):
        app, fake = make_app(self, health_routes())
        got = health_tools.get_readiness_trend(app, days=3)
        self.assertEqual([r["date"] for r in got["trend"]], ["2026-08-29", "2026-08-30", "2026-08-31"])
        self.assertEqual(got["trend"][0]["sleepAsleep"], "8h40m")
        self.assertEqual(got["trend"][0]["deepMinutes"], 64)
        self.assertEqual([r.url.params["dateStr"] for r in fake.calls("GET", HEALTH + "sleep")],
                         ["2026-08-29", "2026-08-30", "2026-08-31"])
        self.assertEqual(health_tools.get_readiness_trend(app, days=99)["days"], 14)

    def test_body_metrics_tool(self):
        app, _ = make_app(self, health_routes())
        got = health_tools.get_body_metrics(app)
        self.assertEqual(got["scores"]["bodyAge"]["youngerBy"], 9.3)
        self.assertEqual(got["body"]["weight"]["value"], 71.4)

    def test_snapshot_carries_compact_recovery(self):
        app, _ = make_app(self, health_routes())
        got = coaching.get_athlete_snapshot(app)["recovery"]
        self.assertEqual(got["fatiguedMuscles"], ["Pecs"])
        self.assertNotIn("trend", got["wellnessMonitor"][0]["overnight"])

    def test_snapshot_survives_health_outage(self):
        app, _ = make_app(self)  # standard fixtures: no health routes at all
        got = coaching.get_athlete_snapshot(app)
        self.assertEqual([h["trainingId"] for h in got["history"]], [5001, 5000])
        self.assertIn("dataWarning", got["recovery"])
