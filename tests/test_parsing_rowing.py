"""Rowing/ski telemetry parsing (`app/boatingSkiDataGraph/{uuid}`).

Fixtures are synthetic, but their shape follows what the route returns: one sample
every few seconds carrying the stroke rate, pace, watts and the target band that the
programmed workout is asking for at that moment.
"""

from __future__ import annotations

import unittest

from speediance_mcp.speediance import parsing


def sample(time, spm, pace=None, power=None, band=(20, 24, 2, 3)):
    min_spm, max_spm, min_res, max_res = band
    return {"time": time, "spm": spm, "pace": pace, "power": power, "resistance": min_res,
            "minSpm": min_spm, "maxSpm": max_spm, "minResistance": min_res, "maxResistance": max_res}


class TestSessionUuid(unittest.TestCase):
    def test_uuid_survives_a_session_with_no_watch_recording(self):
        # A rowing session normally has showHeartGraph 0; its uuid is still what the
        # telemetry route needs, so session_uuid must not apply the heart-rate gate.
        payload = {"uuid": "abc-123", "showHeartGraph": 0}
        self.assertEqual(parsing.session_uuid(payload), "abc-123")
        self.assertIsNone(parsing.find_uuid("courseTrainingInfo", payload))

    def test_uuid_from_a_list_payload_and_when_absent(self):
        listed = [{"finishedReps": [{"trainingInfoDetail": {"uuid": "u-9"}}]}]
        self.assertEqual(parsing.session_uuid(listed), "u-9")
        self.assertIsNone(parsing.session_uuid([{"finishedReps": []}]))
        self.assertIsNone(parsing.session_uuid({}))


class TestRowingTelemetry(unittest.TestCase):
    def test_empty_or_malformed_payload_is_reported_unavailable(self):
        for payload in (None, {}, {"pointDataList": []}, {"pointDataList": [None, "x"]}):
            result = parsing.rowing_telemetry(payload)
            self.assertFalse(result["available"], payload)
            self.assertIn("no samples", result["reason"])

    def test_blocks_split_where_the_target_band_changes(self):
        points = ([sample(t, 22, pace=250.0, power=150) for t in range(0, 30, 3)]
                  + [sample(t, 26, pace=210.0, power=200, band=(24, 28, 3, 4)) for t in range(30, 60, 3)]
                  + [sample(t, 21, pace=255.0, power=140) for t in range(60, 90, 3)])
        result = parsing.rowing_telemetry({"pointDataList": points})

        self.assertTrue(result["available"])
        self.assertEqual(result["sampleSeconds"], 3)
        self.assertEqual(result["samples"], 30)
        self.assertEqual([b["block"] for b in result["blocks"]], [1, 2, 3])
        self.assertEqual([b["targetStrokeRate"] for b in result["blocks"]], ["20-24", "24-28", "20-24"])
        self.assertEqual([b["targetResistance"] for b in result["blocks"]], ["2-3", "3-4", "2-3"])
        middle = result["blocks"][1]
        self.assertEqual((middle["startSec"], middle["endSec"], middle["seconds"]), (30, 60, 30))

    def test_non_stroking_samples_count_as_rest_and_never_skew_the_rates(self):
        # The opening samples are the flywheel spinning up: spm 0 and an absurd pace.
        # Including them would report a pace the athlete never rowed.
        points = ([sample(t, 0, pace=590.0, power=5) for t in range(0, 12, 3)]
                  + [sample(t, 24, pace=240.0, power=200) for t in range(12, 24, 3)])
        result = parsing.rowing_telemetry({"pointDataList": points})

        self.assertEqual(result["restingSec"], 12)
        self.assertEqual(result["workingSec"], 12)
        self.assertEqual(result["avgPace500"], 240.0)
        self.assertEqual(result["bestPace500"], 240.0)
        self.assertEqual(result["avgStrokeRate"], 24.0)
        self.assertEqual(result["avgWatts"], 200)

    def test_best_pace_is_the_fastest_not_the_largest_number(self):
        points = [sample(0, 20, pace=260.0, power=100), sample(3, 30, pace=205.0, power=300)]
        result = parsing.rowing_telemetry({"pointDataList": points})
        self.assertEqual(result["bestPace500"], 205.0)
        self.assertEqual(result["maxStrokeRate"], 30.0)
        self.assertEqual(result["maxWatts"], 300)

    def test_in_target_percent_counts_only_stroking_samples(self):
        # Four stroking samples, three inside the 20-24 band; the rest sample is ignored
        # on both sides of the fraction, so the answer is 75%, not 60%.
        points = [sample(0, 0, pace=500.0), sample(3, 22, pace=250.0), sample(6, 23, pace=250.0),
                  sample(9, 21, pace=250.0), sample(12, 30, pace=220.0)]
        block = parsing.rowing_telemetry({"pointDataList": points})["blocks"][0]
        self.assertEqual(block["inTargetPercent"], 75.0)

    def test_a_block_with_no_stroking_samples_reports_no_target_percent(self):
        points = [sample(0, 0), sample(3, 0)]
        block = parsing.rowing_telemetry({"pointDataList": points})["blocks"][0]
        self.assertIsNone(block["inTargetPercent"])
        self.assertIsNone(block["avgStrokeRate"])
        self.assertIsNone(block["avgPace500"])

    def test_samples_are_ordered_by_time_and_duration_covers_the_last_one(self):
        points = [sample(6, 22, pace=250.0), sample(0, 20, pace=260.0), sample(3, 21, pace=255.0)]
        result = parsing.rowing_telemetry({"pointDataList": points})
        self.assertEqual(result["durationSec"], 9)
        self.assertEqual(result["blocks"][0]["startSec"], 0)

    def test_a_bare_list_payload_is_accepted(self):
        result = parsing.rowing_telemetry([sample(0, 22, pace=250.0, power=100)])
        self.assertTrue(result["available"])
        self.assertEqual(result["samples"], 1)

    def test_missing_band_fields_leave_targets_unset_without_failing(self):
        points = [{"time": 0, "spm": 22, "pace": 250.0, "power": 100},
                  {"time": 3, "spm": 23, "pace": 248.0, "power": 110}]
        block = parsing.rowing_telemetry({"pointDataList": points})["blocks"][0]
        self.assertIsNone(block["targetStrokeRate"])
        self.assertIsNone(block["targetResistance"])
        self.assertIsNone(block["inTargetPercent"])
        self.assertEqual(block["avgStrokeRate"], 22.5)


if __name__ == "__main__":
    unittest.main()
