"""Synthetic Speediance data for tests. Shapes mirror the live API; values are invented."""

from __future__ import annotations

import copy
import datetime as dt
import json

TODAY = dt.date(2026, 8, 31)

HISTORY_PATH = "/api/mobile/v2/report/userTrainingDataRecord"
STATS_PATH = "/api/app/actionLibraryGroup/userActionStatPage"
TEMPLATES_PATH = "/api/app/v4/customTrainingTemplate/appPage"
TEMPLATE_DETAIL_PATH = "/api/app/v3/customTrainingTemplate/detailByCode"
SAVE_TEMPLATE_PATH = "/api/app/v2/customTrainingTemplate"
DELETE_TEMPLATE_PATH = "/api/app/customTrainingTemplate"
RESERVE_PATH = "/api/app/templateReservation"
COURSE_RESERVE_PATH = "/api/app/courseReservation"
COURSES_PATH = "/api/app/v2/course/page"
CALENDAR_PATH = "/api/app/v5/trainingCalendar/monthNew"
DETAIL = "/api/app/trainingInfo/"

PROFILE = {"appUserId": 1001, "email": "athlete@example.com", "sex": 1, "weight": 80.0,
           "weightUnit": 0, "height": 180, "birthday": "1990-05-01", "trainingDays": 42,
           "totalTrainingTime": 90000, "totalCapacity": 250000.0, "totalCalorie": 30000, "isWatch": 1}

HISTORY = [
    {"trainingId": 5000, "type": 5, "courseType": 0, "title": "Pull Day", "calorie": 320,
     "totalCapacity": 720.0, "totalEnergy": 0.0, "startTime": "2026-08-22 10:00:00", "trainingTime": 1800, "mileage": 0},
    {"trainingId": 5001, "type": 5, "courseType": 0, "title": "Pull Day", "calorie": 341,
     "totalCapacity": 7745.0, "totalEnergy": 0.0, "startTime": "2026-08-29 13:13:17", "trainingTime": 1949, "mileage": 0},
    {"trainingId": 6001, "type": 1, "courseType": 0, "title": "Free Lift", "calorie": 120,
     "totalCapacity": 2000.0, "totalEnergy": 0.0, "startTime": "2026-07-20 09:00:00", "trainingTime": 900, "mileage": 0},
    {"trainingId": 7001, "type": 2, "courseType": 2, "title": "Rowing Intervals", "calorie": 161,
     "totalCapacity": 0.0, "totalEnergy": 29580.29, "startTime": "2026-08-08 08:00:00", "trainingTime": 530, "mileage": 0},
    {"trainingId": 7002, "type": 7, "courseType": 0, "title": "Aerobic Rowing", "calorie": 150,
     "totalCapacity": 0.0, "totalEnergy": 60000.0, "startTime": "2026-08-10 08:00:00", "trainingTime": 600, "mileage": 0},
    # Dated outside every test window (see 6002 below) so it shifts no snapshot or stats
    # expectation; it is found directly by trainingId.
    {"trainingId": 7004, "type": 2, "courseType": 2, "title": "Rowing, nothing recorded", "calorie": 120,
     "totalCapacity": 0.0, "totalEnergy": 20000.0, "startTime": "2026-01-16 08:00:00", "trainingTime": 400, "mileage": 0},
    {"trainingId": None, "title": "Walk", "belongUserHealth": 1, "calorie": 30,
     "startTime": "2026-08-20 15:19:05", "trainingTime": 632},
    # Outside every existing test window (14/30/90-day snapshots, the 2026-08 stats/calendar range)
    # so it doesn't shift any of those expectations; found directly by trainingId regardless.
    {"trainingId": 6002, "type": 7, "courseType": 0, "title": "Standing Dumbbell Curl", "calorie": 40,
     "totalCapacity": 550.0, "totalEnergy": 0.0, "startTime": "2026-01-15 09:00:00", "trainingTime": 300, "mileage": 0},
]

CTT_5001 = [
    {"actionLibraryName": "Barbell Bent Over Row", "actionLibraryGroupId": 321, "completionMethod": 1,
     "finishedReps": [
         {"finishedCount": 12, "targetCount": 12, "time": 30, "maxHeartRate": 142.0,
          "trainingInfoDetail": {"weights": [30.0] * 12, "uuid": "hr-uuid-5001"}},
         {"finishedCount": 8, "targetCount": 8, "time": 25, "maxHeartRate": 0.0,
          "trainingInfoDetail": {"weights": [50.0] * 8}},
         {"finishedCount": 0, "targetCount": 8, "time": 0, "trainingInfoDetail": {"weights": []}},
     ]},
    {"actionLibraryName": "Cable Fly", "actionLibraryGroupId": 500, "completionMethod": 1,
     "finishedReps": [
         {"finishedCount": 10, "targetCount": 10, "time": 30,
          "trainingInfoDetail": {"weights": [48.5, 47.0, 30.5],   # derived force, NOT the resistance
                                 "leftWeights": [12.0, 12.0], "rightWeights": [12.5, 12.0, 12.0]}},
     ]},
]

CTT_5000 = [
    {"actionLibraryName": "Barbell Bent Over Row", "actionLibraryGroupId": 321, "completionMethod": 1,
     "finishedReps": [
         {"finishedCount": 12, "targetCount": 12, "time": 30, "trainingInfoDetail": {"weights": [30.0] * 12}},
         {"finishedCount": 8, "targetCount": 8, "time": 25, "trainingInfoDetail": {"weights": [45.0] * 8}},
     ]},
]

SUMMARY_5001 = {"trainingTime": 1949, "calorie": 341, "totalCapacity": 7745.0}

# A quick single-exercise strength session (type 7 via freeTraining, no cardio data): the
# freeTraining payload has no actionList at all, so the real per-set data only shows up at
# freeTrainingDetail, shaped like the list routes (actionLibraryName/finishedReps).
QUICK_6002 = {"id": 6002, "type": 7, "trainingTime": 300, "totalCapacity": 550.0, "calorie": 40,
              "uuid": "", "showHeartGraph": 0, "actionLibraryId": 9999, "name": "Standing Dumbbell Curl"}
QUICK_6002_DETAIL = [
    {"actionLibraryName": "Standing Dumbbell Curl", "actionLibraryGroupId": 950, "completionMethod": 1,
     "finishedReps": [
         {"finishedCount": 12, "targetCount": 12, "time": 20, "trainingInfoDetail": {"weights": [20.0] * 12}},
         {"finishedCount": 10, "targetCount": 12, "time": 18, "trainingInfoDetail": {"weights": [25.0] * 10}},
     ]},
]


def free_lift(session_total: float, set_capacity: float, weight: float) -> dict:
    return {"id": 6001, "type": 1, "totalCapacity": session_total, "trainingTime": 900, "calorie": 120,
            "uuid": "", "showHeartGraph": 0, "existBoatingSkiDataGraph": False,
            "actionList": [{"actionLibraryName": "Seated Barbell Row", "groupId": 424, "completionMethod": 1,
                            "setList": [
                                {"summary": {"finishedCount": 10, "weight": weight, "time": 40, "leftRight": 0,
                                             "maxHeartRate": 0.0, "totalCapacity": set_capacity}, "rawRepList": []},
                                {"summary": {"finishedCount": 10, "weight": weight, "time": 40, "leftRight": 0,
                                             "maxHeartRate": 0.0, "totalCapacity": set_capacity}, "rawRepList": []},
                            ]}]}


FREE_6001 = free_lift(2000.0, 1000.0, 100)          # lb account: sets sum to the session total
FREE_KG_SCALED = free_lift(909.09, 1000.0, 100)     # kg account: sets are 2.2x the session total
FREE_MISMATCH = free_lift(1500.0, 1000.0, 100)      # reconciles neither way

ROWING_SUMMARY_7001 = {"trainingTime": 530, "totalDistance": 892.71, "calorie": 161, "totalEnergy": 29580.29,
                       "completionRate": 29.0, "rpe": 6, "existBoatingSkiDataGraph": True,
                       # Rowing has no watch recording, but still carries the uuid the telemetry
                       # route is keyed on — hence showHeartGraph 0 alongside a real uuid.
                       "uuid": "row-uuid-7001", "showHeartGraph": 0}

# Two programmed blocks: a 20-24 spm warm-up, then a 24-28 spm piece. The opening samples
# have spm 0 — the flywheel spinning up — exactly as the live route returns.
ROWING_GRAPH_7001 = {"id": "row-uuid-7001", "type": 2, "pointDataList": (
    [{"time": t, "spm": 0, "pace": 590.0, "power": 6, "resistance": 2,
      "minSpm": 20, "maxSpm": 24, "minResistance": 2, "maxResistance": 3} for t in (0, 3)]
    + [{"time": t, "spm": 22, "pace": 250.0, "power": 180, "resistance": 2,
        "minSpm": 20, "maxSpm": 24, "minResistance": 2, "maxResistance": 3} for t in (6, 9)]
    + [{"time": t, "spm": 26, "pace": 215.0, "power": 300, "resistance": 3,
        "minSpm": 24, "maxSpm": 28, "minResistance": 3, "maxResistance": 4} for t in (12, 15)])}

# Flagged as having telemetry, but the machine recorded none.
ROWING_SUMMARY_7004 = {"trainingTime": 400, "totalDistance": 700.0, "calorie": 120, "totalEnergy": 20000.0,
                       "completionRate": 50.0, "rpe": 5, "existBoatingSkiDataGraph": True,
                       "uuid": "row-uuid-7004", "showHeartGraph": 0}

AEROBIC_7002 = {"id": 7002, "type": 7, "trainingTime": 600, "totalDistance": 2000.0, "totalEnergy": 60000.0,
                "calorie": 150, "completionRate": 100.0, "rpe": 5, "uuid": "", "showHeartGraph": 0,
                "existBoatingSkiDataGraph": True, "actionList": []}

AEROBIC_INTERVALS = [
    {"actionLibraryName": "Aerobic Rowing", "actionLibraryGroupId": 900, "completionMethod": 2,
     "finishedReps": [
         {"finishedCount": 0, "targetCount": 0, "time": 300, "distance": 1000.0, "pace": 150.0, "spm": 24,
          "trainingInfoDetail": {}},
         {"finishedCount": 0, "targetCount": 0, "time": 300, "distance": 1000.0, "pace": 150.0, "spm": 26,
          "trainingInfoDetail": {}},
     ]},
]

HEART_RATE = [{"time": 0, "heartRate": 120}, {"time": 1, "heartRate": 0}, {"time": 2, "heartRate": 150}]
RANGE_STAT = {"trainingTime": 5400, "calorie": 900, "totalCapacity": 15000.0, "totalEnergy": 89580.29}

CALENDAR_2026_08 = [
    {"date": "2026-08-29", "isAllFinish": False, "isReservationDay": False, "trainingDailyGoal": {},
     "trainingPlanList": []},
    {"date": "2026-08-30", "isAllFinish": False, "isReservationDay": True, "trainingDailyGoal": {},
     "trainingPlanList": [{"type": 3, "title": "Pull Day", "isFinish": 0, "isReservation": True,
                           "code": "a" * 24, "templateId": 9001}]},
]

TABS = [{"id": 10, "name": "Training"}, {"id": 11, "name": "Row & Ski"}]
GROUPS_BY_TAB = {
    10: [{"trainingPartId2": 11, "actionLibraryGroupList": [{"id": g} for g in (321, 416, 294, 600, 700, 424)]}],
    11: [{"trainingPartId2": 18, "actionLibraryGroupList": [{"id": 900}]}],
}


def _exercise(gid, title, *, tab="Training", accessories="4", body="Back", muscle="Lats",
               unilateral=0, completion=1, stat=0):
    return {"id": gid, "title": title, "tabName": tab, "accessories": accessories, "isLeftRight": unilateral,
            "completionMethod": completion, "dataStatType": stat, "recommendedWeight": 20.0,
            "context": f"How to do {title}.", "motionFeeling": "Squeeze at the top.",
            "img": f"https://example.test/{gid}.png",
            "mainMuscleGroupList": [{"muscleGroupName": muscle, "categoryName": body}],
            "auxiliaryMuscleGroupList": [{"muscleGroupName": "Rear Delts", "categoryName": "Shoulders"}],
            "actionLibraryList": [{"id": gid * 10, "videoPath": f"https://example.test/{gid}.mp4"}]}


LIBRARY = [
    _exercise(321, "Barbell Bent Over Row"),
    _exercise(416, "Seated Barbell Lat Pulldown", accessories="4,1"),
    _exercise(294, "Standing Barbell Biceps Curl", body="Arms", muscle="Biceps"),
    _exercise(600, "Single Arm Cable Row", accessories="5", unilateral=1),
    _exercise(700, "Vita Row", accessories="5", completion=5, stat=6),
    _exercise(900, "Aerobic Rowing", tab="Row & Ski", accessories="8", body="Full Body", muscle="Cardio",
              completion=2),
    _exercise(424, "Seated Barbell Row"),
]

ACCESSORIES = [
    {"id": 4, "name": "Barbell", "type": 0},
    {"id": 5, "name": "Handles", "type": 0},
    {"id": 1, "name": "Flat Bench", "type": 1},
    {"id": 8, "name": "AeroRow", "type": 1},
    {"id": 882627450798080, "name": "Handles", "type": 0},
]

STATS = {321: [
    {"dayStr": "2026-08-29", "totalCapacity": 1120.0, "maxWeight": 50.0, "minWeight": 30.0, "actionLibraryGroupId": 321},
    {"dayStr": "2026-08-22", "totalCapacity": 720.0, "maxWeight": 45.0, "minWeight": 30.0, "actionLibraryGroupId": 321},
]}

TEMPLATES = [{"id": 9001, "code": "a" * 24, "name": "Pull Day", "actionNum": 1, "durationMinute": 30}]
TEMPLATE_9001 = {"id": 9001, "code": "a" * 24, "name": "Pull Day", "durationMinute": 30,
                 "actionLibraryList": [{"sort": 1, "actionLibraryId": 3210, "title": "Barbell Bent Over Row",
                                        "templatePresetId": -1, "setsAndReps": "12,10", "weights": "30.0,40.0",
                                        "level": "0,0", "leftRight": "0,0", "breakTime2": "60,60"}]}

PROGRAMS = [{"id": 77, "name": "Strength Foundations", "description": "Eight weeks of basics.", "weekCount": 8,
             "weekTrainingFrequency": 3, "difficultyId": 1, "isPermission": True}]
PROGRAM_77 = {"id": 77, "name": "Strength Foundations", "weekCount": 8,
              "weekList": [{"name": "Week 1"}, {"name": "Week 2"}],
              "exclusivePlanWeekList": [{"id": 1, "exclusivePlanDayList": [
                  {"id": 11, "day": 1, "courseList": [{"id": 2577, "code": "c" * 24, "courseTitle": "All Around the Chest",
                                                       "durationMinute": 53, "difficultyId": 3}]},
                  {"id": 12, "day": 2, "courseList": []}]}]}

# Official course catalogue rows, trimmed from /api/app/v2/course/page (ids and codes altered).
COURSES = [
    {"id": 2577, "code": "c" * 24, "courseTitle": "All Around the Chest", "durationMinute": 53, "difficultyId": 3,
     "deviceType": 1, "categoryList": [{"id": 24, "categoryName": "Strength Training"}]},
    {"id": 2816, "code": "d" * 24, "courseTitle": "Base & Engine #1", "durationMinute": 37, "difficultyId": 1,
     "deviceType": 1, "categoryList": [{"id": 30, "categoryName": "Hyrox"}]},
]

# A booked course as the month calendar shows it (live 2026-09-30): type 1, courseReservationId, code.
def booked_course(course: dict) -> dict:
    return {"type": 1, "courseId": course["id"], "title": course["courseTitle"], "isFinish": 0,
            "durationMinute": course["durationMinute"], "isReservation": True, "courseReservationId": 213000,
            "code": course["code"], "courseMode": 2, "deviceType": 1}


def course_page_route(courses):
    def handler(request):
        params = request.url.params
        page, size = int(params.get("pageNo", 1)), int(params.get("pageSize", 10))
        return courses[(page - 1) * size: page * size]
    return handler


def course_booking_routes(routes: dict, *, accept=True, sticks=True) -> dict:
    """Make course reservations stateful: a booking POST adds the course to that day's calendar and a
    removal takes it off. accept=False answers data:false; sticks=False accepts but never shows it."""
    booked: dict[str, list] = {}
    by_code = {c["code"]: c for c in COURSES}

    def reserve(request):
        body = json.loads(request.content)
        day = booked.setdefault(body["thatDay"], [])
        if sticks and accept and body["status"] == 1:
            day.append(booked_course(by_code[body["courseCode"]]))
        elif sticks and accept:
            day[:] = [p for p in day if p["code"] != body["courseCode"]]
        return accept

    def calendar(request):
        month = request.url.params["date"]
        return [{"date": d, "trainingPlanList": list(plans)} for d, plans in sorted(booked.items())
                if d.startswith(month)]

    routes[("POST", COURSE_RESERVE_PATH)] = reserve
    routes[("GET", CALENDAR_PATH)] = calendar
    return routes


def history_route(records):
    def handler(request):
        params = request.url.params
        start, end = params.get("startDate", "0000-00-00"), params.get("endDate", "9999-12-31")
        return [r for r in records if start <= str(r.get("startTime", ""))[:10] <= end]
    return handler


def batch_route(items):
    def handler(request):
        wanted = {int(x) for x in request.url.params.get_list("ids")}
        return [i for i in items if i["id"] in wanted]
    return handler


def stats_route(by_group):
    def handler(request):
        params = request.url.params
        rows = by_group.get(int(params["id"]), [])
        page, size = int(params.get("pageNo", 1)), int(params.get("pageSize", 50))
        return rows[(page - 1) * size: page * size]
    return handler


def standard_routes() -> dict:
    routes = {
        ("GET", "/api/app/userinfo/info"): PROFILE,
        ("GET", HISTORY_PATH): history_route(HISTORY),
        ("GET", DETAIL + "cttTrainingInfoDetail/5001"): CTT_5001,
        ("GET", DETAIL + "cttTrainingInfoDetail/5000"): CTT_5000,
        ("GET", DETAIL + "cttTrainingInfo/5001"): SUMMARY_5001,
        ("GET", DETAIL + "freeTraining/6001"): FREE_6001,
        ("GET", DETAIL + "freeTraining/6002"): QUICK_6002,
        ("GET", DETAIL + "freeTrainingDetail/6002"): QUICK_6002_DETAIL,
        ("GET", DETAIL + "courseTrainingInfoDetail/7001"): [],
        ("GET", DETAIL + "courseTrainingInfo/7001"): ROWING_SUMMARY_7001,
        ("GET", "/api/app/boatingSkiDataGraph/row-uuid-7001"): ROWING_GRAPH_7001,
        ("GET", DETAIL + "courseTrainingInfoDetail/7004"): [],
        ("GET", DETAIL + "courseTrainingInfo/7004"): ROWING_SUMMARY_7004,
        ("GET", "/api/app/boatingSkiDataGraph/row-uuid-7004"): {"pointDataList": []},
        ("GET", DETAIL + "freeTraining/7002"): AEROBIC_7002,
        ("GET", DETAIL + "freeTrainingDetail/7002"): AEROBIC_INTERVALS,
        ("GET", "/api/app/watchMsg/getHeartRateGraph"): HEART_RATE,
        ("GET", "/api/mobile/v2/report/userTrainingDataStat"): RANGE_STAT,
        ("GET", CALENDAR_PATH): CALENDAR_2026_08,
        ("GET", "/api/app/actionLibraryTab/list"): TABS,
        ("GET", "/api/app/actionLibraryGroup/trainingPartGroup"):
            lambda req: GROUPS_BY_TAB.get(int(req.url.params["tabId"]), []),
        ("GET", "/api/app/actionLibraryGroup/list"): batch_route(LIBRARY),
        ("GET", STATS_PATH): stats_route(STATS),
        ("GET", "/api/app/accessories/list"): ACCESSORIES,
        ("GET", TEMPLATES_PATH): TEMPLATES,
        ("GET", TEMPLATE_DETAIL_PATH):
            lambda req: TEMPLATE_9001 if req.url.params.get("code") == "a" * 24 else None,
        ("POST", RESERVE_PATH): True,
        ("GET", "/api/mobile/exclusivePlan/page"): PROGRAMS,
        ("GET", "/api/app/exclusivePlan/77"): PROGRAM_77,
        ("GET", COURSES_PATH): course_page_route(COURSES),
    }
    for item in LIBRARY:
        routes[("GET", f"/api/app/actionLibraryGroup/{item['id']}")] = item
    return copy.deepcopy(routes)
