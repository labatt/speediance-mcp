"""Typed wrappers over the Speediance endpoints this server uses."""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .client import NotFound, Rejected, SpeedianceClient, SpeedianceError, WrongNamespace
from .routes import (FREE_INTERVALS_ROUTE, PLAN_MISSING_CODE, SUMMARY_ROUTES, detail_path, plan_routes,
                     routes_to_try)

HISTORY_START = "2020-01-01"
HISTORY_TTL = 600.0
LIBRARY_TTL = 24 * 3600.0
LIBRARY_BATCH = 50
STATS_PAGE = 50


class SessionNotFound(SpeedianceError):
    """The training id is not in this account's history."""


class PlanNotFound(SpeedianceError):
    """Neither plan route serves this course id."""


class SpeedianceAPI:
    def __init__(self, client: SpeedianceClient, cache_dir: Path, *,
                 today: Callable[[], dt.date] | None = None, clock: Callable[[], float] = time.time):
        self.client = client
        self.cache_dir = Path(cache_dir)
        self._today = today or dt.date.today
        self._clock = clock
        self._history_cache: tuple[float, dict[int, dict]] | None = None
        self._accessories: list[dict] | None = None
        self._lock = threading.Lock()
        self._library_lock = threading.Lock()

    @property
    def unit(self) -> str:
        return self.client.creds.unit if self.client.creds else "kg"

    @property
    def device_type(self) -> int:
        return self.client.creds.device_type if self.client.creds else 1

    def today(self) -> dt.date:
        return self._today()

    # --- account and history -------------------------------------------------
    def profile(self) -> dict:
        return self.client.get("/api/app/userinfo/info") or {}

    def history(self, start: str, end: str) -> list[dict]:
        rows = self.client.get("/api/mobile/v2/report/userTrainingDataRecord",
                               params={"startDate": start, "endDate": end}) or []
        return [r for r in rows if isinstance(r, dict)]

    def history_index(self, refresh: bool = False) -> dict[int, dict]:
        with self._lock:
            cached = self._history_cache
        if not refresh and cached and self._clock() - cached[0] < HISTORY_TTL:
            return cached[1]
        rows = self.history(HISTORY_START, self.today().isoformat())
        index = {int(r["trainingId"]): r for r in rows if r.get("trainingId")}
        with self._lock:
            self._history_cache = (self._clock(), index)
        return index

    def find_session(self, training_id) -> dict:
        """The history record for an id — which also proves this account owns the session."""
        tid = int(training_id)
        record = self.history_index().get(tid) or self.history_index(refresh=True).get(tid)
        if record is None:
            raise SessionNotFound(f"Session {tid} isn't in this account's training history.")
        return record

    def session_payload(self, record: dict) -> tuple[str, Any]:
        tid = int(record["trainingId"])
        candidates = routes_to_try(record.get("type"))
        for route in candidates:
            try:
                data = self.client.get(detail_path(route, tid))
            except (WrongNamespace, NotFound):
                continue
            if data:
                return route, data
        return candidates[0], None

    def session_summary(self, route: str, training_id) -> dict:
        summary_route = SUMMARY_ROUTES.get(route)
        if not summary_route:
            return {}
        try:
            return self.client.get(detail_path(summary_route, training_id)) or {}
        except (WrongNamespace, NotFound, Rejected):
            return {}

    def free_intervals(self, training_id) -> list:
        try:
            data = self.client.get(detail_path(FREE_INTERVALS_ROUTE, training_id))
        except (WrongNamespace, NotFound, Rejected):
            return []
        return data if isinstance(data, list) else []

    def heart_rate(self, uuid: str) -> Any:
        return self.client.get("/api/app/watchMsg/getHeartRateGraph", params={"uuid": uuid})

    def rowing_graph(self, uuid: str) -> Any:
        """Per-point rowing/ski telemetry, keyed on the session UUID. The numeric
        trainingId is accepted by the route but answers null, so pass the uuid."""
        try:
            return self.client.get(f"/api/app/boatingSkiDataGraph/{uuid}")
        except (WrongNamespace, NotFound, Rejected):
            return None

    def range_stats(self, start: str, end: str) -> dict:
        return self.client.get("/api/mobile/v2/report/userTrainingDataStat",
                               params={"startDate": start, "endDate": end}) or {}

    def calendar(self, month: str) -> list:
        return self.client.get("/api/app/v5/trainingCalendar/monthNew",
                               params={"date": month, "selectedDeviceType": self.device_type}) or []

    def exercise_stats(self, group_id, max_weeks: int = 50) -> list[dict]:
        """Per-movement stat rows. NOTE: each row is a WEEK, not a day or a session —
        `dayStr` is always the Monday of a Sunday-to-Saturday week, so its totalCapacity
        is that week's volume. Verified live 2026-09-29. There is no daily variant."""
        out: list[dict] = []
        page = 1
        while len(out) < max_weeks:
            rows = self.client.get("/api/app/actionLibraryGroup/userActionStatPage",
                                   params={"id": int(group_id), "pageNo": page, "pageSize": STATS_PAGE}) or []
            out.extend(r for r in rows if isinstance(r, dict))
            if len(rows) < STATS_PAGE:
                break
            page += 1
        return out[:max_weeks]

    # --- templates and calendar ----------------------------------------------
    def templates(self) -> list[dict]:
        return self.client.get("/api/app/v4/customTrainingTemplate/appPage",
                               params={"pageNo": 1, "pageSize": -1, "deviceTypes": self.device_type}) or []

    def template(self, code: str) -> dict | None:
        return self.client.get("/api/app/v3/customTrainingTemplate/detailByCode", params={"code": code})

    def save_template(self, body: dict) -> Any:
        return self.client.post("/api/app/v2/customTrainingTemplate", body)

    def delete_template(self, template_id) -> None:
        self.client.delete("/api/app/customTrainingTemplate", params={"ids": int(template_id)})

    def course_plan(self, course_id, calendar_type=0) -> tuple[str, dict]:
        """(route, payload) for a booked course or AI session, keyed by its calendar `courseId`.

        Tries the route that fits the calendar type first. On code 100026 it tries the other one:
        that code only means "not on this route" (see routes.PLAN_MISSING_CODE).
        """
        for route in plan_routes(calendar_type):
            path = route.format(cid=int(course_id))
            try:
                data = self.client.get(path, params={"weightConfig": 1})
            except Rejected as exc:
                if exc.code == PLAN_MISSING_CODE:
                    continue
                raise
            except (WrongNamespace, NotFound):
                continue
            if isinstance(data, dict) and data:
                return route, data
        raise PlanNotFound(f"No course or AI plan with id {int(course_id)}.")

    def reserve(self, date: str, code: str, status: int) -> Any:
        return self.client.post("/api/app/templateReservation", {
            "status": int(status), "deviceType": self.device_type, "thatDay": date, "templateCode": code})

    # --- exercise library ----------------------------------------------------
    def accessories(self) -> list[dict]:
        if self._accessories is None:
            self._accessories = self.client.get("/api/app/accessories/list") or []
        return self._accessories

    def exercise(self, group_id) -> dict:
        return self.client.get(f"/api/app/actionLibraryGroup/{int(group_id)}", params={"isDisplay": 1}) or {}

    def exercise_batch(self, ids) -> list[dict]:
        rows = self.client.get("/api/app/actionLibraryGroup/list", params=[("ids", int(i)) for i in ids]) or []
        return [r for r in rows if isinstance(r, dict)]

    def library(self, force: bool = False) -> list[dict]:
        """The raw exercise catalog, cached on disk for 24 hours (~1,000 movements)."""
        path = self.cache_dir / f"library-{self.client.region}-{self.device_type}.json"
        if not force:
            cached = self._cached_library(path)
            if cached is not None:
                return cached
        with self._library_lock:
            # Concurrent cold callers wait here and reuse the first caller's download.
            if not force:
                cached = self._cached_library(path)
                if cached is not None:
                    return cached
            items = self._fetch_library()
            try:
                path.write_text(json.dumps({"fetched_at": self._clock(), "items": items}), encoding="utf-8")
            except OSError:
                pass
            return items

    def _cached_library(self, path: Path) -> list[dict] | None:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if self._clock() - float(cached["fetched_at"]) < LIBRARY_TTL:
                return cached["items"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def _fetch_library(self) -> list[dict]:
        device = self.device_type
        tabs = self.client.get("/api/app/actionLibraryTab/list", params={"deviceType": device}) or []
        order: list[int] = []
        tab_of: dict[int, str] = {}
        for tab in tabs:
            groups = self.client.get("/api/app/actionLibraryGroup/trainingPartGroup",
                                     params={"tabId": tab["id"], "deviceTypeList": device}) or []
            for group in groups:
                for action in group.get("actionLibraryGroupList") or []:
                    gid = action.get("id")
                    if gid is not None and gid not in tab_of:
                        tab_of[gid] = tab.get("name", "")
                        order.append(gid)
        items: list[dict] = []
        for start in range(0, len(order), LIBRARY_BATCH):
            for raw in self.exercise_batch(order[start:start + LIBRARY_BATCH]):
                raw.setdefault("tabName", tab_of.get(raw.get("id"), ""))
                items.append(raw)
        return items

    # --- programs --------------------------------------------------------------
    def programs(self) -> list[dict]:
        return self.client.get("/api/mobile/exclusivePlan/page", params={"pageNo": 1, "pageSize": 200}) or []

    def program(self, program_id) -> dict:
        return self.client.get(f"/api/app/exclusivePlan/{int(program_id)}") or {}
