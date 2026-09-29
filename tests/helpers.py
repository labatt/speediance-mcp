"""Offline fake of the Speediance API for tests."""

from __future__ import annotations

import httpx2
import shutil
import tempfile
from pathlib import Path

from speediance_mcp.config import Credentials

CREDS = Credentials(email="athlete@example.com", token="tok-1", user_id="1001", unit="lb")


def api_error(code: int, message: str, status: int = 200) -> httpx2.Response:
    return httpx2.Response(status, json={"code": code, "message": message})


class FakeSpeediance:
    """Routes (METHOD, path) -> data | httpx2.Response | callable(request). Records requests."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key not in self.routes:
            return httpx2.Response(404, json={"code": 404, "message": "no such route"})
        value = self.routes[key]
        if callable(value):
            value = value(request)
        if isinstance(value, httpx2.Response):
            return value
        return httpx2.Response(200, json={"code": 0, "data": value})

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self)

    def calls(self, method: str, path: str) -> list[httpx2.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]


def make_app(test, routes=None, *, creds=CREDS, mode="local"):
    """An App over a fresh temp data dir and a FakeSpeediance (standard fixtures by default)."""
    from speediance_mcp.config import save_credentials
    from speediance_mcp.tools.context import App
    from tests.fixtures import TODAY, standard_routes

    home = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, home, True)
    if creds is not None:
        save_credentials(creds, home)
    fake = FakeSpeediance(standard_routes() if routes is None else routes)
    app = App(home, mode=mode, transport=fake.transport(), min_interval=0, today=lambda: TODAY)
    test.addCleanup(app.close)
    return app, fake
