# speediance-mcp Phase 1 (Core + Local Mode) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pip-installable MCP server that gives Claude Desktop and Claude Code 26 tools over a user's Speediance Gym Monster account, running locally over stdio.

**Architecture:** A small Speediance HTTP client (`speediance/client.py`) and a typed API layer
(`speediance/api.py`) sit under pure parsing and template-writing modules (`parsing.py`,
`writes.py`, `library.py`) that hold the API's hard-won quirks. A SQLite `Memory` stores
coaching facts and preferences. Each tool is a plain function taking an `App` context; `server.py`
binds them into an `MCPServer` with a generic wrapper that hides the context and turns errors
into friendly `ToolError`s. `cli.py` provides `login` / `logout` / `status` and runs the server.

**Tech Stack:** Python ≥3.10, `mcp>=2.2,<3` (the only runtime dependency; it brings `httpx2`),
standard library `sqlite3`/`json`/`unittest`, hatchling build backend, GitHub Actions.

**Spec:** `docs/specs/2026-09-27-speediance-mcp-design.md` (read §15 amendments). Phase 2
(remote OAuth mode) is a separate plan written after this one ships.

## Global Constraints

- Python `>=3.10`. Every module with annotations starts with `from __future__ import annotations`.
- Only runtime dependency: `mcp>=2.2,<3`. HTTP uses `httpx2` (bundled by `mcp`). Never import `requests` or `httpx`.
- Server class: `from mcp.server.mcpserver import MCPServer`; tool errors: `from mcp.server.mcpserver.exceptions import ToolError`.
- `Versioncode` header is exactly `"41000"` and is never changed by code.
- At most one Speediance request per second per process (`min_interval=1.0` default; tests pass `min_interval=0`).
- Weights are passed verbatim in the account's display unit. The display unit comes from the login response `unit` (`1` → `"lb"`, else `"kg"`), stored in credentials.
- Never print or log tokens or passwords. In stdio server mode nothing but MCP goes to stdout.
- Tests: standard-library `unittest`, fully offline via `httpx2.MockTransport`, synthetic data only (no real account data; example email `athlete@example.com`).
- Test command (from repo root, venv active): `.venv/bin/python -m unittest discover -s tests -t . -v`.
- Tool names are exactly the 26 in spec §6.
- MIT license; hbui3's copyright notice is preserved.

## Review Focus

1. **A phone sign-in invalidates the token mid-conversation and no password was remembered** → every tool fails with a `ToolError` telling the user to run `speediance-mcp login`, never a stack trace. *(Test: Task 13.)*
2. **A `training_id` that isn't in this account's history** (typo, or someone else's id) → `get_session_detail` returns `resolvedType: false` with a clear message, and no detail endpoint is ever requested. *(Test: Task 8.)*
3. **An ambiguous or misspelled exercise name** in `create_workout` / `suggest_load` / `get_exercise_history` → candidates are listed and nothing is written. *(Tests: Tasks 9, 10, 11.)*
4. **A brand-new account** (no history, no templates, no preferences) → snapshot, strength profile, exercise history and `suggest_load` return empty results with an explanatory note instead of crashing. *(Test: Task 10.)*
5. **Windows**: no `APPDATA`, and `chmod` is a no-op → the data dir falls back to the profile folder and credentials still save and load. *(Tests: Tasks 1, 2.)*

## File Structure

```
pyproject.toml, README.md, LICENSE, .gitignore, .github/workflows/ci.yml
src/speediance_mcp/
  __init__.py            __version__
  __main__.py            python -m speediance_mcp
  paths.py               per-OS data dir
  config.py              Credentials + credentials.json
  memory.py              SQLite coaching memory
  library.py             exercise catalog: summarize / filter / resolve names (pure)
  server.py              MCPServer, INSTRUCTIONS, bind(), build_server()
  cli.py                 argparse entry point
  speediance/
    __init__.py
    client.py            HTTP transport, errors, login, re-auth, throttle
    routes.py            session type -> detail route
    api.py               typed endpoint wrappers, history index, library cache
    parsing.py           session/cardio/heart-rate parsing (pure)
    writes.py            template body builder, reader, verifier (pure)
  tools/
    __init__.py
    context.py           App (credentials, api, memory)
    _common.py           date/month parsing, record summaries, library resolution
    errors.py            exception -> ToolError translation
    account.py           check_connection
    sessions.py          get_calendar, get_session_detail, get_heart_rate, get_training_stats
    exercises.py         list_exercises, get_exercise, mark_exercise, list_accessories, get_exercise_history
    coaching.py          get_athlete_snapshot, get_strength_profile, compare_sessions, suggest_load
    workouts.py          list_my_workouts, get_workout, create_workout, update_workout, delete_workout
    calendar.py          schedule_workout, unschedule_workout, browse_programs
    memory_tools.py      get_preferences, set_preferences, remember_fact, forget_fact
tests/
  __init__.py, helpers.py (fake Speediance), fixtures.py (synthetic data)
  test_paths.py test_config.py test_client.py test_api.py test_parsing.py test_parsing_free.py
  test_memory.py test_tools_sessions.py test_library.py test_tools_exercises.py
  test_tools_coaching.py test_writes.py test_tools_workouts.py test_tools_calendar_memory.py
  test_server_cli.py test_readme.py
```

Execution note: work on a branch (`git checkout -b phase1`), never directly on `main`.

---

### Task 1: Project scaffold and per-OS data directory

**Files:**
- Create: `pyproject.toml`, `README.md`, `LICENSE`, `.gitignore`, `.github/workflows/ci.yml`
- Create: `src/speediance_mcp/__init__.py`, `src/speediance_mcp/paths.py`
- Create: `tests/__init__.py`, `tests/test_paths.py`

**Interfaces:**
- Produces: `speediance_mcp.__version__: str = "0.1.0"`; `paths.APP_NAME = "speediance-mcp"`; `paths.HOME_ENV = "SPEEDIANCE_MCP_HOME"`; `paths.default_dir(platform: str, env: Mapping[str, str], home: Path) -> Path`; `paths.data_dir() -> Path` (creates the directory).

- [ ] **Step 1: Create the packaging and repo files**

`pyproject.toml`:
```toml
[build-system]
requires = ["hatchling>=1.24"]
build-backend = "hatchling.build"

[project]
name = "speediance-mcp"
version = "0.1.0"
description = "Unofficial, free MCP server for Speediance Gym Monster training data (a self-hosted GM Manager alternative)"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.10"
dependencies = ["mcp>=2.2,<3"]
classifiers = [
  "License :: OSI Approved :: MIT License",
  "Programming Language :: Python :: 3",
  "Operating System :: OS Independent",
]

[project.scripts]
speediance-mcp = "speediance_mcp.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/speediance_mcp"]
```

`README.md` (expanded in Task 14):
```markdown
# speediance-mcp

A free, open-source MCP server that lets Claude read and manage your Speediance Gym Monster
training. Unofficial — not affiliated with Speediance. Setup instructions arrive with the first release.
```

`LICENSE`:
```
MIT License

Copyright (c) 2025 hbui3
Copyright (c) 2026 labatt

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
*.egg-info/
build/
dist/
credentials.json
*.db
*.db-journal
library-*.json
*.log
.env
```

`.github/workflows/ci.yml`:
```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        python: ["3.10", "3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: python -m pip install .
      - run: python -m unittest discover -s tests -t . -v
```

`src/speediance_mcp/__init__.py`:
```python
"""Unofficial MCP server for Speediance Gym Monster."""

__version__ = "0.1.0"
```

`tests/__init__.py`: empty file.

- [ ] **Step 2: Create the venv and install**

Run: `python3 -m venv .venv && .venv/bin/pip install -e .`
Expected: installs `speediance-mcp 0.1.0` and `mcp`.

- [ ] **Step 3: Write the failing test** — `tests/test_paths.py`

```python
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speediance_mcp import paths


class TestDefaultDir(unittest.TestCase):
    home = Path("/home/athlete")

    def test_windows_uses_appdata(self):
        got = paths.default_dir("win32", {"APPDATA": r"C:\Users\a\AppData\Roaming"}, self.home)
        self.assertEqual(got, Path(r"C:\Users\a\AppData\Roaming") / "speediance-mcp")

    def test_windows_without_appdata_falls_back_to_profile(self):
        got = paths.default_dir("win32", {}, self.home)
        self.assertEqual(got, self.home / "AppData" / "Roaming" / "speediance-mcp")

    def test_macos(self):
        got = paths.default_dir("darwin", {}, self.home)
        self.assertEqual(got, self.home / "Library" / "Application Support" / "speediance-mcp")

    def test_linux_honours_xdg(self):
        got = paths.default_dir("linux", {"XDG_CONFIG_HOME": "/cfg"}, self.home)
        self.assertEqual(got, Path("/cfg") / "speediance-mcp")

    def test_linux_default(self):
        got = paths.default_dir("linux", {}, self.home)
        self.assertEqual(got, self.home / ".config" / "speediance-mcp")


class TestDataDir(unittest.TestCase):
    def test_env_override_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "home"
            with mock.patch.dict(os.environ, {paths.HOME_ENV: str(target)}):
                got = paths.data_dir()
            self.assertEqual(got, target)
            self.assertTrue(target.is_dir())
```

- [ ] **Step 4: Run it to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_paths -v`
Expected: FAIL — `ImportError: cannot import name 'paths'`.

- [ ] **Step 5: Implement** — `src/speediance_mcp/paths.py`

```python
"""Per-OS data directory, standard library only."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

APP_NAME = "speediance-mcp"
HOME_ENV = "SPEEDIANCE_MCP_HOME"


def default_dir(platform: str, env: Mapping[str, str], home: Path) -> Path:
    """Where the data dir lives on each OS. Pure: no filesystem access."""
    if platform == "win32":
        base = env.get("APPDATA")
        return (Path(base) if base else home / "AppData" / "Roaming") / APP_NAME
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    base = env.get("XDG_CONFIG_HOME")
    return (Path(base) if base else home / ".config") / APP_NAME


def data_dir() -> Path:
    """The data dir, created if missing. SPEEDIANCE_MCP_HOME overrides it."""
    override = os.environ.get(HOME_ENV)
    path = Path(override) if override else default_dir(sys.platform, os.environ, Path.home())
    path.mkdir(parents=True, exist_ok=True)
    return path
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_paths -v`
Expected: 6 tests, OK.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml README.md LICENSE .gitignore .github src tests
git commit -m "feat: project scaffold and per-OS data directory"
```

---

### Task 2: Credentials store

**Files:**
- Create: `src/speediance_mcp/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `paths.data_dir()`.
- Produces: `@dataclass Credentials(email: str, token: str, user_id: str, unit: str = "kg", region: str = "Global", device_type: int = 1, password: str | None = None)`; `credentials_path(home: Path | None = None) -> Path`; `load_credentials(home=None) -> Credentials | None`; `save_credentials(creds, home=None) -> Path`; `clear_credentials(home=None) -> bool`.

- [ ] **Step 1: Write the failing test** — `tests/test_config.py`

```python
from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speediance_mcp import config
from speediance_mcp.config import Credentials


class TestCredentials(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.creds = Credentials(email="athlete@example.com", token="tok", user_id="1001", unit="lb")

    def test_round_trip(self):
        config.save_credentials(self.creds, self.home)
        self.assertEqual(config.load_credentials(self.home), self.creds)

    def test_missing_file_is_none(self):
        self.assertIsNone(config.load_credentials(self.home))

    def test_corrupt_file_is_none(self):
        config.credentials_path(self.home).write_text("{not json", encoding="utf-8")
        self.assertIsNone(config.load_credentials(self.home))

    def test_missing_required_field_is_none(self):
        config.credentials_path(self.home).write_text(json.dumps({"email": "a@b.c"}), encoding="utf-8")
        self.assertIsNone(config.load_credentials(self.home))

    def test_unknown_keys_are_ignored(self):
        data = {"email": "a@b.c", "token": "t", "user_id": "1", "future_field": 1}
        config.credentials_path(self.home).write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(config.load_credentials(self.home).email, "a@b.c")

    @unittest.skipUnless(os.name == "posix", "POSIX permissions only")
    def test_file_is_owner_only(self):
        path = config.save_credentials(self.creds, self.home)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_save_works_when_chmod_is_unavailable(self):
        # Windows: os.name is "nt" and chmod never runs; saving must still work.
        with mock.patch.object(config.os, "name", "nt"):
            config.save_credentials(self.creds, self.home)
        self.assertEqual(config.load_credentials(self.home), self.creds)

    def test_clear(self):
        config.save_credentials(self.creds, self.home)
        self.assertTrue(config.clear_credentials(self.home))
        self.assertFalse(config.clear_credentials(self.home))
        self.assertIsNone(config.load_credentials(self.home))
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_config -v`
Expected: FAIL — `ImportError: cannot import name 'config'`.

- [ ] **Step 3: Implement** — `src/speediance_mcp/config.py`

```python
"""Stored Speediance credentials (credentials.json in the data dir, owner-only)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .paths import data_dir

FILENAME = "credentials.json"


@dataclass
class Credentials:
    email: str
    token: str
    user_id: str
    unit: str = "kg"          # "kg" | "lb" — from the login response, the account's display unit
    region: str = "Global"    # "Global" | "EU"
    device_type: int = 1      # 1 = Gym Monster, 2 = Gym Pal
    password: str | None = None  # only when the user chose "remember"


def credentials_path(home: Path | None = None) -> Path:
    return (home or data_dir()) / FILENAME


def load_credentials(home: Path | None = None) -> Credentials | None:
    """The stored credentials, or None if absent or unreadable."""
    try:
        raw = json.loads(credentials_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    known = {f.name for f in fields(Credentials)}
    try:
        return Credentials(**{k: v for k, v in raw.items() if k in known})
    except TypeError:
        return None


def save_credentials(creds: Credentials, home: Path | None = None) -> Path:
    """Write credentials atomically, readable only by the owner on POSIX."""
    path = credentials_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(asdict(creds), handle, indent=2)
    os.replace(tmp, path)
    if os.name == "posix":
        os.chmod(path, 0o600)
    return path


def clear_credentials(home: Path | None = None) -> bool:
    try:
        credentials_path(home).unlink()
        return True
    except FileNotFoundError:
        return False
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_config -v`
Expected: 8 tests OK (1 skipped on Windows).

- [ ] **Step 5: Commit**

```bash
git add src/speediance_mcp/config.py tests/test_config.py
git commit -m "feat: credentials store with owner-only permissions"
```

---

### Task 3: Speediance HTTP client

**Files:**
- Create: `src/speediance_mcp/speediance/__init__.py` (empty), `src/speediance_mcp/speediance/client.py`
- Create: `tests/helpers.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: `config.Credentials`.
- Produces (in `speediance.client`): `VERSION_CODE = "41000"`; `HOSTS = {"Global": "api2.speediance.com", "EU": "euapi.speediance.com"}`; exceptions `SpeedianceError`, `AuthExpired`, `LoginFailed`, `NotFound`, `WrongNamespace`, `ServerError`, `Rejected(code, message)` (all subclass `SpeedianceError`; `Rejected` has `.code` and `.api_message`); class `SpeedianceClient(creds, *, region=None, transport=None, min_interval=1.0, on_credentials=None, sleep=time.sleep, clock=time.monotonic, timeout=30.0)` with attributes `creds`, `region` and methods `request(method, path, *, params=None, json=None, auth=True) -> Any` (returns the body's `data`), `get(path, params=None)`, `post(path, json)`, `delete(path, params=None)`, `login(email, password, *, remember=False, device_type=1) -> Credentials`, `logout() -> None`, `close() -> None`.
- Produces (in `tests.helpers`): `FakeSpeediance(routes)` (callable transport handler; `.transport()`, `.requests`, `.calls(method, path)`; routes map `(METHOD, path)` to data, an `httpx2.Response`, or a callable `request -> data | Response`; unmatched → 404), `api_error(code, message, status=200) -> httpx2.Response`, `CREDS`.

- [ ] **Step 1: Write the test helpers** — `tests/helpers.py`

```python
"""Offline fake of the Speediance API for tests."""

from __future__ import annotations

import httpx2

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
```

- [ ] **Step 2: Write the failing test** — `tests/test_client.py`

```python
from __future__ import annotations

import dataclasses
import unittest

from speediance_mcp.speediance.client import (
    AuthExpired, LoginFailed, NotFound, Rejected, ServerError, SpeedianceClient, WrongNamespace,
)
from tests.helpers import CREDS, FakeSpeediance, api_error

PROFILE = "/api/app/userinfo/info"
VERIFY = "/api/app/v2/login/verifyIdentity"
BYPASS = "/api/app/v2/login/byPass"


class TestClient(unittest.TestCase):
    def make(self, routes, creds=CREDS, **kw):
        fake = FakeSpeediance(routes)
        saved = []
        client = SpeedianceClient(creds, transport=fake.transport(), min_interval=0,
                                  on_credentials=saved.append, **kw)
        self.addCleanup(client.close)
        return client, fake, saved

    def test_returns_data_and_sends_mobile_headers(self):
        client, fake, _ = self.make({("GET", PROFILE): {"appUserId": 1001}})
        self.assertEqual(client.get(PROFILE), {"appUserId": 1001})
        headers = fake.requests[0].headers
        self.assertEqual(headers["Versioncode"], "41000")
        self.assertEqual(headers["Token"], "tok-1")
        self.assertEqual(headers["App_user_id"], "1001")
        self.assertEqual(headers["User-Agent"], "Dart/3.9 (dart:io)")
        self.assertIn("Utc_offset", headers)

    def test_nonzero_code_raises_rejected(self):
        client, _, _ = self.make({("GET", PROFILE): api_error(7, "Parameter Error")})
        with self.assertRaises(Rejected) as cm:
            client.get(PROFILE)
        self.assertEqual(cm.exception.code, 7)
        self.assertIn("Parameter Error", str(cm.exception))

    def test_do_not_have_access_is_wrong_namespace(self):
        client, _, _ = self.make({("GET", PROFILE): api_error(1, "Sorry. You do not have access.")})
        with self.assertRaises(WrongNamespace):
            client.get(PROFILE)

    def test_http_404_and_500(self):
        client, _, _ = self.make({("GET", "/boom"): api_error(0, "x", status=502)})
        with self.assertRaises(NotFound):
            client.get("/missing")
        with self.assertRaises(ServerError):
            client.get("/boom")

    def test_expired_token_without_password(self):
        client, fake, _ = self.make({("GET", PROFILE): api_error(91, "Login expired")})
        with self.assertRaises(AuthExpired):
            client.get(PROFILE)
        self.assertEqual(len(fake.requests), 1)

    def test_silent_relogin_with_remembered_password(self):
        calls = {"n": 0}

        def profile(request):
            calls["n"] += 1
            return api_error(91, "Login expired") if calls["n"] == 1 else {"appUserId": 1001}

        creds = dataclasses.replace(CREDS, password="secret")
        client, fake, saved = self.make({
            ("GET", PROFILE): profile,
            ("POST", VERIFY): {"isExist": True, "hasPwd": True},
            ("POST", BYPASS): {"token": "tok-2", "appUserId": 1001, "unit": 1},
        }, creds=creds)
        self.assertEqual(client.get(PROFILE), {"appUserId": 1001})
        self.assertEqual(saved[0].token, "tok-2")
        self.assertEqual(fake.calls("GET", PROFILE)[-1].headers["Token"], "tok-2")

    def test_login_maps_unit_and_only_keeps_password_when_remembered(self):
        routes = {("POST", VERIFY): {"isExist": True, "hasPwd": True},
                  ("POST", BYPASS): {"token": "t", "appUserId": 7, "unit": 1}}
        client, _, saved = self.make(routes, creds=None)
        creds = client.login("athlete@example.com", "pw", remember=False)
        self.assertEqual((creds.unit, creds.user_id, creds.password), ("lb", "7", None))
        self.assertEqual(saved, [creds])
        creds = client.login("athlete@example.com", "pw", remember=True)
        self.assertEqual(creds.password, "pw")

    def test_login_unit_zero_is_kg(self):
        routes = {("POST", VERIFY): {"isExist": True, "hasPwd": True},
                  ("POST", BYPASS): {"token": "t", "appUserId": 7, "unit": 0}}
        client, _, _ = self.make(routes, creds=None)
        self.assertEqual(client.login("a@b.c", "pw").unit, "kg")

    def test_login_wrong_password(self):
        routes = {("POST", VERIFY): {"isExist": True, "hasPwd": True},
                  ("POST", BYPASS): api_error(1001, "Incorrect password")}
        client, _, _ = self.make(routes, creds=None)
        with self.assertRaises(LoginFailed) as cm:
            client.login("a@b.c", "bad")
        self.assertIn("Incorrect password", str(cm.exception))

    def test_login_unknown_account(self):
        client, _, _ = self.make({("POST", VERIFY): {"isExist": False}}, creds=None)
        with self.assertRaises(LoginFailed):
            client.login("nobody@example.com", "pw")

    def test_no_credentials_raises_without_a_request(self):
        client, fake, _ = self.make({}, creds=None)
        with self.assertRaises(AuthExpired):
            client.get(PROFILE)
        self.assertEqual(fake.requests, [])

    def test_throttle_waits_between_requests(self):
        ticks = iter([0.0, 0.3, 1.0])
        sleeps = []
        fake = FakeSpeediance({("GET", PROFILE): {}})
        client = SpeedianceClient(CREDS, transport=fake.transport(), min_interval=1.0,
                                  sleep=sleeps.append, clock=lambda: next(ticks))
        self.addCleanup(client.close)
        client.get(PROFILE)
        client.get(PROFILE)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.7)
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_client -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'speediance_mcp.speediance'`.

- [ ] **Step 4: Implement** — `src/speediance_mcp/speediance/__init__.py` (empty) and `src/speediance_mcp/speediance/client.py`

```python
"""HTTP client for the private Speediance mobile-app API.

Ported from hbui3/UnofficialSpeedianceWorkoutManager (MIT). Unofficial: the API can change
without notice.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

import httpx2

from ..config import Credentials

# The API version-gates content on the app version this client declares. 41000 = v4.10.0.
# Do NOT raise this or change it rapidly: the server answers implausibly-high or rapidly
# changing codes with an anti-abuse "Invalid nonce string" throttle.
VERSION_CODE = "41000"
HOSTS = {"Global": "api2.speediance.com", "EU": "euapi.speediance.com"}
MOBILE_DEVICES = ('{"brand":"google","device":"emulator64_x86_64_arm64","deviceType":'
                  '"sdk_gphone64_x86_64","os":"","os_version":"31","manufacturer":"Google"}')
USER_AGENT = "Dart/3.9 (dart:io)"
AUTH_CODES = {91}


class SpeedianceError(Exception):
    """Base for Speediance failures."""


class AuthExpired(SpeedianceError):
    """Not logged in, or the token was invalidated (e.g. by a phone sign-in)."""


class LoginFailed(SpeedianceError):
    """Email/password login was refused."""


class NotFound(SpeedianceError):
    """The route does not exist (HTTP 404)."""


class WrongNamespace(SpeedianceError):
    """'Sorry. You do not have access.' — the id belongs to a different session namespace."""


class ServerError(SpeedianceError):
    """HTTP 5xx from Speediance."""


class Rejected(SpeedianceError):
    """The API answered with a non-zero body code."""

    def __init__(self, code: Any, message: str):
        super().__init__(f"Speediance rejected the request (code {code}): {message}")
        self.code = code
        self.api_message = message


def _tz_headers() -> dict[str, str]:
    now = datetime.now().astimezone()
    offset = now.utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    tz_name = os.environ.get("TZ") or (time.tzname[0] if time.tzname else "GMT")
    return {"Timezone": tz_name, "Utc_offset": f"{sign}{minutes // 60:02d}{minutes % 60:02d}"}


class SpeedianceClient:
    def __init__(self, creds: Credentials | None, *, region: str | None = None, transport=None,
                 min_interval: float = 1.0, on_credentials: Callable[[Credentials], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 timeout: float = 30.0):
        self.creds = creds
        self.region = region or (creds.region if creds else "Global")
        if self.region not in HOSTS:
            raise ValueError(f"region must be one of {sorted(HOSTS)}")
        self._http = httpx2.Client(base_url=f"https://{HOSTS[self.region]}", transport=transport,
                                   timeout=timeout)
        self._min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self._throttle_lock = threading.Lock()
        self._auth_lock = threading.Lock()
        self._on_credentials = on_credentials

    def _throttle(self) -> None:
        with self._throttle_lock:
            now = self._clock()
            if self._last is not None:
                wait = self._min_interval - (now - self._last)
                if wait > 0:
                    self._sleep(wait)
                    now = self._clock()
            self._last = now

    def _headers(self, auth: bool) -> dict[str, str]:
        headers = {
            "Timestamp": str(int(time.time() * 1000)),
            "Versioncode": VERSION_CODE,
            "Mobiledevices": MOBILE_DEVICES,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "App_type": "SOFTWARE",
            "Accept-Language": "en",
            **_tz_headers(),
        }
        if auth and self.creds:
            headers["App_user_id"] = self.creds.user_id
            headers["Token"] = self.creds.token
        return headers

    def request(self, method: str, path: str, *, params=None, json=None, auth: bool = True,
                _retry: bool = True) -> Any:
        if auth and not self.creds:
            raise AuthExpired("Not logged in to Speediance.")
        self._throttle()
        try:
            resp = self._http.request(method, path, params=params, json=json, headers=self._headers(auth))
        except httpx2.TransportError as exc:
            raise SpeedianceError(f"Could not reach Speediance: {exc}") from exc
        if resp.status_code == 404:
            raise NotFound(f"Speediance has no route {method} {path}")
        if resp.status_code >= 500:
            raise ServerError(f"Speediance server error (HTTP {resp.status_code})")
        try:
            body = resp.json()
        except ValueError as exc:
            raise SpeedianceError(f"Unexpected non-JSON response (HTTP {resp.status_code})") from exc
        is_dict = isinstance(body, dict)
        code = body.get("code") if is_dict else None
        message = (body.get("message") or body.get("msg") or "") if is_dict else ""
        if resp.status_code == 401 or code in AUTH_CODES:
            if auth and _retry and self._relogin():
                return self.request(method, path, params=params, json=json, auth=auth, _retry=False)
            raise AuthExpired(message or "Speediance login expired.")
        if code not in (None, 0):
            if "do not have access" in message.lower():
                raise WrongNamespace(message)
            raise Rejected(code, message)
        return body.get("data") if is_dict else body

    def get(self, path: str, params=None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: Any) -> Any:
        return self.request("POST", path, json=json)

    def delete(self, path: str, params=None) -> Any:
        return self.request("DELETE", path, params=params)

    def _relogin(self) -> bool:
        with self._auth_lock:
            if not (self.creds and self.creds.password):
                return False
            try:
                self.login(self.creds.email, self.creds.password, remember=True,
                           device_type=self.creds.device_type)
            except SpeedianceError:
                return False
            return True

    def login(self, email: str, password: str, *, remember: bool = False, device_type: int = 1) -> Credentials:
        try:
            verify = self.request("POST", "/api/app/v2/login/verifyIdentity",
                                  json={"type": 2, "userIdentity": email}, auth=False)
            if isinstance(verify, dict):
                if verify.get("isExist") is False:
                    raise LoginFailed("No Speediance account uses that email. Register in the Speediance app first.")
                if verify.get("hasPwd") is False:
                    raise LoginFailed("That account has no password yet. Set one in the Speediance app first.")
            data = self.request("POST", "/api/app/v2/login/byPass",
                                json={"userIdentity": email, "password": password, "type": 2}, auth=False)
        except Rejected as exc:
            raise LoginFailed(exc.api_message or "Login was rejected.") from exc
        except AuthExpired as exc:
            raise LoginFailed(str(exc)) from exc
        if not isinstance(data, dict) or not data.get("token") or not data.get("appUserId"):
            raise LoginFailed("Speediance's login response had no token.")
        self.creds = Credentials(
            email=email, token=data["token"], user_id=str(data["appUserId"]),
            unit="lb" if data.get("unit") == 1 else "kg", region=self.region,
            device_type=device_type, password=password if remember else None,
        )
        if self._on_credentials:
            self._on_credentials(self.creds)
        return self.creds

    def logout(self) -> None:
        try:
            self.request("POST", "/api/app/login/logout", json={}, _retry=False)
        except SpeedianceError:
            pass

    def close(self) -> None:
        self._http.close()
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_client -v`
Expected: 12 tests OK.

- [ ] **Step 6: Commit**

```bash
git add src/speediance_mcp/speediance tests/helpers.py tests/test_client.py
git commit -m "feat: Speediance HTTP client with re-auth, throttle and error model"
```

---

### Task 4: Session routing, API layer and synthetic fixtures

**Files:**
- Create: `src/speediance_mcp/speediance/routes.py`, `src/speediance_mcp/speediance/api.py`
- Create: `tests/fixtures.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `SpeedianceClient`, its exceptions.
- Produces (`speediance.routes`): `FREE_ROUTE = "freeTraining"`, `FREE_INTERVALS_ROUTE = "freeTrainingDetail"`, `DETAIL_ROUTES: dict[int, str]`, `ALL_DETAIL_ROUTES: tuple[str, ...]`, `SUMMARY_ROUTES: dict[str, str]`, `routes_to_try(session_type) -> tuple[str, ...]`, `detail_path(route, training_id) -> str`.
- Produces (`speediance.api`): `HISTORY_START = "2020-01-01"`, `SessionNotFound(SpeedianceError)`, `SpeedianceAPI(client, cache_dir, *, today=None, clock=time.time)` with properties `unit`, `device_type`, and methods `today() -> date`, `profile() -> dict`, `history(start, end) -> list[dict]`, `history_index(refresh=False) -> dict[int, dict]`, `find_session(training_id) -> dict`, `session_payload(record) -> tuple[str, Any]`, `session_summary(route, training_id) -> dict`, `free_intervals(training_id) -> list`, `heart_rate(uuid) -> Any`, `range_stats(start, end) -> dict`, `calendar(month) -> list`, `exercise_stats(group_id, max_days=50) -> list[dict]`, `templates() -> list[dict]`, `template(code) -> dict | None`, `save_template(body) -> Any`, `delete_template(template_id) -> None`, `reserve(date, code, status) -> Any`, `accessories() -> list[dict]` (memoized), `exercise(group_id) -> dict`, `exercise_batch(ids) -> list[dict]`, `library(force=False) -> list[dict]` (raw items, 24 h disk cache), `programs() -> list[dict]`, `program(program_id) -> dict`.
- Produces (`tests.fixtures`): `TODAY`, all synthetic payload constants below, `HISTORY_PATH` etc., and `standard_routes() -> dict`.

- [ ] **Step 1: Write the synthetic fixtures** — `tests/fixtures.py`

```python
"""Synthetic Speediance data for tests. Shapes mirror the live API; values are invented."""

from __future__ import annotations

import copy
import datetime as dt

TODAY = dt.date(2026, 8, 31)

HISTORY_PATH = "/api/mobile/v2/report/userTrainingDataRecord"
STATS_PATH = "/api/app/actionLibraryGroup/userActionStatPage"
TEMPLATES_PATH = "/api/app/v4/customTrainingTemplate/appPage"
TEMPLATE_DETAIL_PATH = "/api/app/v3/customTrainingTemplate/detailByCode"
SAVE_TEMPLATE_PATH = "/api/app/v2/customTrainingTemplate"
DELETE_TEMPLATE_PATH = "/api/app/customTrainingTemplate"
RESERVE_PATH = "/api/app/templateReservation"
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
    {"trainingId": None, "title": "Walk", "belongUserHealth": 1, "calorie": 30,
     "startTime": "2026-08-20 15:19:05", "trainingTime": 632},
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
                       "completionRate": 29.0, "rpe": 6, "existBoatingSkiDataGraph": True}

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
              "weekList": [{"name": "Week 1"}, {"name": "Week 2"}]}


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
        ("GET", DETAIL + "courseTrainingInfoDetail/7001"): [],
        ("GET", DETAIL + "courseTrainingInfo/7001"): ROWING_SUMMARY_7001,
        ("GET", DETAIL + "freeTraining/7002"): AEROBIC_7002,
        ("GET", DETAIL + "freeTrainingDetail/7002"): AEROBIC_INTERVALS,
        ("GET", "/api/app/watchMsg/getHeartRateGraph"): HEART_RATE,
        ("GET", "/api/mobile/v2/report/userTrainingDataStat"): RANGE_STAT,
        ("GET", "/api/app/v5/trainingCalendar/monthNew"): CALENDAR_2026_08,
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
    }
    for item in LIBRARY:
        routes[("GET", f"/api/app/actionLibraryGroup/{item['id']}")] = item
    return copy.deepcopy(routes)
```

- [ ] **Step 2: Write the failing test** — `tests/test_api.py`

```python
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
        self.assertEqual(len(api.exercise_stats(321, max_days=100)), 56)
        self.assertEqual(len(fake.calls("GET", fx.STATS_PATH)), 2)
        self.assertEqual(len(api.exercise_stats(321, max_days=10)), 10)

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
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_api -v`
Expected: FAIL — `ImportError: cannot import name 'routes'`.

- [ ] **Step 4: Implement** — `src/speediance_mcp/speediance/routes.py`

```python
"""Session type -> detail route.

The history feed and the calendar number session types differently (history: 1, 2, 5, 7, 9;
calendar: 3, 4, 6 for the same categories). This map is the union and must only be applied to a
type read from the feed it came from. Source: pookey/speediance-cli docs (MIT), verified live.
"""

from __future__ import annotations

FREE_ROUTE = "freeTraining"
FREE_INTERVALS_ROUTE = "freeTrainingDetail"

DETAIL_ROUTES = {
    1: FREE_ROUTE, 6: FREE_ROUTE, 7: FREE_ROUTE,          # Free Lift, guided/quick cardio
    2: "courseTrainingInfoDetail",                        # course / program
    3: "cttTrainingInfoDetail", 5: "cttTrainingInfoDetail",  # custom template
    4: "aiCourseTrainingInfoDetail", 9: "aiCourseTrainingInfoDetail",  # AI / Goal-Focused
}
ALL_DETAIL_ROUTES = ("cttTrainingInfoDetail", "courseTrainingInfoDetail", FREE_ROUTE,
                     "aiCourseTrainingInfoDetail")
SUMMARY_ROUTES = {"cttTrainingInfoDetail": "cttTrainingInfo",
                  "courseTrainingInfoDetail": "courseTrainingInfo"}


def routes_to_try(session_type) -> tuple[str, ...]:
    try:
        route = DETAIL_ROUTES.get(int(session_type))
    except (TypeError, ValueError):
        route = None
    return (route,) if route else ALL_DETAIL_ROUTES


def detail_path(route: str, training_id) -> str:
    return f"/api/app/trainingInfo/{route}/{int(training_id)}"
```

`src/speediance_mcp/speediance/api.py`:

```python
"""Typed wrappers over the Speediance endpoints this server uses."""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .client import NotFound, Rejected, SpeedianceClient, SpeedianceError, WrongNamespace
from .routes import FREE_INTERVALS_ROUTE, SUMMARY_ROUTES, detail_path, routes_to_try

HISTORY_START = "2020-01-01"
HISTORY_TTL = 600.0
LIBRARY_TTL = 24 * 3600.0
LIBRARY_BATCH = 50
STATS_PAGE = 50


class SessionNotFound(SpeedianceError):
    """The training id is not in this account's history."""


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

    def range_stats(self, start: str, end: str) -> dict:
        return self.client.get("/api/mobile/v2/report/userTrainingDataStat",
                               params={"startDate": start, "endDate": end}) or {}

    def calendar(self, month: str) -> list:
        return self.client.get("/api/app/v5/trainingCalendar/monthNew",
                               params={"date": month, "selectedDeviceType": self.device_type}) or []

    def exercise_stats(self, group_id, max_days: int = 50) -> list[dict]:
        out: list[dict] = []
        page = 1
        while len(out) < max_days:
            rows = self.client.get("/api/app/actionLibraryGroup/userActionStatPage",
                                   params={"id": int(group_id), "pageNo": page, "pageSize": STATS_PAGE}) or []
            out.extend(r for r in rows if isinstance(r, dict))
            if len(rows) < STATS_PAGE:
                break
            page += 1
        return out[:max_days]

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
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if self._clock() - float(cached["fetched_at"]) < LIBRARY_TTL:
                    return cached["items"]
            except (OSError, ValueError, KeyError, TypeError):
                pass
        items = self._fetch_library()
        try:
            path.write_text(json.dumps({"fetched_at": self._clock(), "items": items}), encoding="utf-8")
        except OSError:
            pass
        return items

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
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_api -v`
Expected: 15 tests OK.

- [ ] **Step 6: Commit**

```bash
git add src/speediance_mcp/speediance/routes.py src/speediance_mcp/speediance/api.py tests/fixtures.py tests/test_api.py
git commit -m "feat: session routing, typed API layer and library cache"
```

---

### Task 5: Parsing — exercise-list sessions

**Files:**
- Create: `src/speediance_mcp/speediance/parsing.py`
- Test: `tests/test_parsing.py`

**Interfaces:**
- Consumes: `routes.FREE_ROUTE`.
- Produces: `nums(value) -> list[float]`; `kind_of(item: dict) -> "reps" | "timed" | "level"`; `set_load(detail: dict, side: int | None) -> float | None`; `parse_list_exercises(payload) -> list[dict]`. Each exercise dict: `name, groupId, kind, sets, skippedSets, reps: list, weights: list, setLog: list, topWeight, volume, avgLoad, avgLoadEstimated`. Each `setLog` entry: `setIndex, reps, targetReps, seconds, weight, level, side, maxHeartRate`, plus `distance` / `pace` / `strokeRate` when non-zero.

- [ ] **Step 1: Write the failing test** — `tests/test_parsing.py`

```python
from __future__ import annotations

import unittest

from speediance_mcp.speediance import parsing
from tests import fixtures as fx


class TestHelpers(unittest.TestCase):
    def test_nums_accepts_lists_and_csv(self):
        self.assertEqual(parsing.nums("1,2.5,x,"), [1.0, 2.5])
        self.assertEqual(parsing.nums([3, None, "4"]), [3.0, 4.0])
        self.assertEqual(parsing.nums(None), [])

    def test_kind_of(self):
        self.assertEqual(parsing.kind_of({"completionMethod": 1}), "reps")
        self.assertEqual(parsing.kind_of({"completionMethod": 0}), "timed")
        self.assertEqual(parsing.kind_of({"completionMethod": 2}), "timed")
        self.assertEqual(parsing.kind_of({"completionMethod": 5}), "level")
        self.assertEqual(parsing.kind_of({"completionMethod": 1, "dataStatType": 6}), "level")

    def test_side_arrays_beat_derived_force_weights(self):
        detail = {"weights": [48.5, 47.0], "leftWeights": [12.0], "rightWeights": [12.5]}
        self.assertEqual(parsing.set_load(detail, None), 12.5)
        self.assertEqual(parsing.set_load(detail, 1), 12.0)
        self.assertEqual(parsing.set_load({"weights": [30.0, 30.0]}, None), 30.0)
        self.assertIsNone(parsing.set_load({}, None))


class TestListExercises(unittest.TestCase):
    def test_worked_sets_skipped_sets_and_loads(self):
        row, fly = parsing.parse_list_exercises(fx.CTT_5001)
        self.assertEqual(row["name"], "Barbell Bent Over Row")
        self.assertEqual(row["groupId"], 321)
        self.assertEqual((row["sets"], row["skippedSets"]), (2, 1))
        self.assertEqual(row["reps"], [12, 8])
        self.assertEqual(row["weights"], [30.0, 50.0])
        self.assertEqual(row["topWeight"], 50.0)
        self.assertEqual(row["volume"], 760.0)
        self.assertEqual(row["avgLoad"], 40.0)
        self.assertEqual([s["setIndex"] for s in row["setLog"]], [1, 2])
        self.assertEqual(fly["weights"], [12.5])

    def test_zero_heart_rate_means_no_watch(self):
        row = parsing.parse_list_exercises(fx.CTT_5001)[0]
        self.assertEqual([s["maxHeartRate"] for s in row["setLog"]], [142.0, None])

    def test_timed_sets_skip_on_zero_seconds(self):
        plank = {"actionLibraryName": "Plank", "completionMethod": 2, "finishedReps": [
            {"finishedCount": 0, "targetCount": 0, "time": 45, "trainingInfoDetail": {}},
            {"finishedCount": 0, "targetCount": 0, "time": 0, "trainingInfoDetail": {}}]}
        ex = parsing.parse_list_exercises([plank])[0]
        self.assertEqual((ex["kind"], ex["sets"], ex["skippedSets"]), ("timed", 1, 1))
        self.assertEqual(ex["setLog"][0]["seconds"], 45)
        self.assertIsNone(ex["setLog"][0]["weight"])

    def test_vita_level(self):
        vita = {"actionLibraryName": "Vita Pull", "completionMethod": 5, "finishedReps": [
            {"finishedCount": 13, "targetCount": 20, "time": 20, "level": "12", "trainingInfoDetail": {}}]}
        entry = parsing.parse_list_exercises([vita])[0]["setLog"][0]
        self.assertEqual((entry["level"], entry["reps"], entry["weight"]), (12, 13, None))

    def test_cardio_fields_are_kept_when_present(self):
        entry = parsing.parse_list_exercises(fx.AEROBIC_INTERVALS)[0]["setLog"][0]
        self.assertEqual((entry["distance"], entry["pace"], entry["strokeRate"]), (1000.0, 150.0, 24.0))

    def test_unnamed_exercise_and_junk_rows(self):
        out = parsing.parse_list_exercises([None, {"completionMethod": 1, "finishedReps": []}])
        self.assertEqual(out[0]["name"], "Exercise (not picked in app)")
        self.assertEqual(out[0]["sets"], 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_parsing -v`
Expected: FAIL — `ImportError: cannot import name 'parsing'`.

- [ ] **Step 3: Implement** — `src/speediance_mcp/speediance/parsing.py`

```python
"""Pure parsing of Speediance session payloads. No I/O.

Rules (spec §8): weights stay in the account's display unit; on dual-cable movements `weights`
is derived force telemetry, so the side arrays win; telemetry arrays are ragged, so each series
is read on its own; `maxHeartRate: 0` means "no watch".
"""

from __future__ import annotations

import math
from typing import Any

from .routes import FREE_ROUTE


def _num(value) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def nums(value) -> list[float]:
    """A telemetry series as floats; accepts a list or a CSV string. Non-numbers are dropped."""
    if value is None:
        return []
    parts = value.split(",") if isinstance(value, str) else value
    out = []
    for part in parts:
        number = _num(part)
        if number is not None:
            out.append(number)
    return out


def kind_of(item: dict) -> str:
    """'reps', 'timed', or 'level' (Vita) — decides what a set's numbers mean."""
    method = item.get("completionMethod")
    if method == 5 or item.get("dataStatType") == 6:
        return "level"
    if method in (0, 2):
        return "timed"
    return "reps"


def _side(value) -> int | None:
    try:
        side = int(value)
    except (TypeError, ValueError):
        return None
    return side if side in (1, 2) else None


def _heart(value) -> float | None:
    number = _num(value)
    return number if number and number > 0 else None


def set_load(detail: dict, side: int | None) -> float | None:
    """The resistance of one set: side arrays first, `weights` only as a fallback."""
    left = nums(detail.get("leftWeights"))
    right = nums(detail.get("rightWeights"))
    if side == 1 and left:
        return max(left)
    if side == 2 and right:
        return max(right)
    if left and right:
        return max(max(left), max(right))
    if left or right:
        return max(left or right)
    weights = nums(detail.get("weights"))
    return max(weights) if weights else None


def _list_set(kind: str, raw: dict, index: int) -> dict:
    detail = raw.get("trainingInfoDetail") or {}
    done = int(_num(raw.get("finishedCount")) or 0)
    target = int(_num(raw.get("targetCount")) or 0)
    seconds = int(_num(raw.get("time")) or 0)
    side = _side(raw.get("leftRight", detail.get("leftRight")))
    entry = {
        "setIndex": index,
        "reps": done if kind != "timed" else None,
        "targetReps": target if kind == "reps" else None,
        "seconds": seconds,
        "weight": None,
        "level": None,
        "side": side,
        "maxHeartRate": _heart(raw.get("maxHeartRate")),
        "skipped": (done == 0) if kind == "reps" else (seconds == 0),
    }
    if kind == "reps":
        load = set_load(detail, side)
        entry["weight"] = round(load, 1) if load is not None else None
    if kind == "level":
        levels = nums(raw.get("level")) or nums(detail.get("level"))
        entry["level"] = int(max(levels)) if levels else None
    for source, target_key in (("distance", "distance"), ("pace", "pace"), ("spm", "strokeRate")):
        value = _num(raw.get(source))
        if value:
            entry[target_key] = value
    return entry


def _exercise(name, group_id, kind: str, entries: list[dict]) -> dict:
    worked = [e for e in entries if not e["skipped"]]
    for number, entry in enumerate(worked, 1):
        entry["setIndex"] = number
        entry.pop("skipped")
    weights = [e["weight"] for e in worked if e["weight"] is not None]
    volume = sum((e["reps"] or 0) * (e["weight"] or 0) for e in worked) if kind == "reps" else 0.0
    return {
        "name": name or "Exercise (not picked in app)",
        "groupId": group_id,
        "kind": kind,
        "sets": len(worked),
        "skippedSets": len(entries) - len(worked),
        "reps": [e["reps"] for e in worked],
        "weights": weights,
        "setLog": worked,
        "topWeight": max(weights) if weights else None,
        "volume": round(volume, 1),
        "avgLoad": round(sum(weights) / len(weights), 1) if weights else None,
        "avgLoadEstimated": False,
    }


def parse_list_exercises(payload) -> list[dict]:
    """Custom-template, course and AI routes: a list of exercises with `finishedReps`."""
    out = []
    for raw in payload or []:
        if not isinstance(raw, dict):
            continue
        kind = kind_of(raw)
        entries = [_list_set(kind, s, i) for i, s in enumerate(raw.get("finishedReps") or [], 1)
                   if isinstance(s, dict)]
        out.append(_exercise(raw.get("actionLibraryName"), raw.get("actionLibraryGroupId"), kind, entries))
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_parsing -v`
Expected: 9 tests OK.

- [ ] **Step 5: Commit**

```bash
git add src/speediance_mcp/speediance/parsing.py tests/test_parsing.py
git commit -m "feat: parse exercise-list sessions (sets, loads, kinds, heart rate)"
```

---

### Task 6: Parsing — Free Lift, cardio, intervals, heart rate, strength math

**Files:**
- Modify: `src/speediance_mcp/speediance/parsing.py` (append)
- Test: `tests/test_parsing_free.py`

**Interfaces:**
- Consumes: Task 5's `_num`, `_side`, `_heart`, `kind_of`, `_exercise`, `parse_list_exercises`.
- Produces: `KG_LB_SCALE = 2.2`; `free_scale(payload) -> tuple[float, str | None]`; `parse_free_exercises(payload) -> tuple[list[dict], list[str]]`; `normalize_session(route, payload) -> {"exercises": list, "warnings": list}`; `find_uuid(route, payload) -> str | None`; `is_cardio(data: dict) -> bool`; `derive_cardio_stats(summary: dict) -> dict` (keys `durationSec, distanceM, pace500, speedMs, calorie, calPerMin, energyKJ, avgWatts, completion, rpe`); `intervals_from(payload) -> list[dict]` (keys `interval, seconds, distanceM, pace500, strokeRate, maxHeartRate`); `heart_rate_values(data) -> list[float]`; `downsample(values, limit) -> list[float]`; `epley(weight, reps) -> float`; `round_half(value) -> float`.

- [ ] **Step 1: Write the failing test** — `tests/test_parsing_free.py`

```python
from __future__ import annotations

import unittest

from speediance_mcp.speediance import parsing
from tests import fixtures as fx


class TestFreeLift(unittest.TestCase):
    def test_lb_account_sets_are_unscaled(self):
        self.assertEqual(parsing.free_scale(fx.FREE_6001), (1.0, None))
        exercises, warnings = parsing.parse_free_exercises(fx.FREE_6001)
        self.assertEqual(warnings, [])
        self.assertEqual(exercises[0]["groupId"], 424)
        self.assertEqual(exercises[0]["weights"], [100.0, 100.0])
        self.assertEqual(exercises[0]["volume"], 2000.0)

    def test_kg_account_sets_are_divided_by_2_2(self):
        self.assertEqual(parsing.free_scale(fx.FREE_KG_SCALED)[0], 2.2)
        exercises, _ = parsing.parse_free_exercises(fx.FREE_KG_SCALED)
        self.assertEqual(exercises[0]["weights"], [45.5, 45.5])

    def test_unreconciled_sessions_keep_raw_values_and_warn(self):
        exercises, warnings = parsing.parse_free_exercises(fx.FREE_MISMATCH)
        self.assertEqual(exercises[0]["weights"], [100.0, 100.0])
        self.assertEqual(len(warnings), 1)
        self.assertIn("didn't reconcile", warnings[0])

    def test_normalize_dispatches_on_route(self):
        self.assertEqual(parsing.normalize_session("freeTraining", fx.FREE_6001)["exercises"][0]["name"],
                         "Seated Barbell Row")
        self.assertEqual(len(parsing.normalize_session("cttTrainingInfoDetail", fx.CTT_5001)["exercises"]), 2)
        self.assertEqual(parsing.normalize_session("courseTrainingInfoDetail", None),
                         {"exercises": [], "warnings": []})

    def test_find_uuid(self):
        self.assertEqual(parsing.find_uuid("cttTrainingInfoDetail", fx.CTT_5001), "hr-uuid-5001")
        self.assertIsNone(parsing.find_uuid("freeTraining", fx.FREE_6001))
        self.assertEqual(parsing.find_uuid("freeTraining", {"uuid": "u", "showHeartGraph": 1}), "u")


class TestCardio(unittest.TestCase):
    def test_rowing_oracle(self):
        stats = parsing.derive_cardio_stats(fx.ROWING_SUMMARY_7001)
        self.assertEqual(stats["durationSec"], 530)
        self.assertEqual(stats["distanceM"], 892.71)
        self.assertEqual(stats["pace500"], 296.8)
        self.assertEqual(stats["speedMs"], 1.68)
        self.assertEqual(stats["calPerMin"], 18.2)
        self.assertEqual(stats["energyKJ"], 29.6)
        self.assertEqual(stats["avgWatts"], 56)
        self.assertEqual((stats["completion"], stats["rpe"]), (29.0, 6.0))

    def test_missing_inputs_give_none_never_nan(self):
        stats = parsing.derive_cardio_stats({"trainingTime": 0})
        self.assertIsNone(stats["pace500"])
        self.assertIsNone(stats["avgWatts"])
        self.assertIsNone(stats["calPerMin"])

    def test_is_cardio(self):
        self.assertTrue(parsing.is_cardio(fx.ROWING_SUMMARY_7001))
        self.assertTrue(parsing.is_cardio({"courseType": 2}))
        self.assertFalse(parsing.is_cardio(fx.SUMMARY_5001))

    def test_intervals(self):
        rows = parsing.intervals_from(fx.AEROBIC_INTERVALS)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"interval": 1, "seconds": 300, "distanceM": 1000.0, "pace500": 150.0,
                                   "strokeRate": 24.0, "maxHeartRate": None})


class TestHeartRateAndStrength(unittest.TestCase):
    def test_heart_rate_shapes(self):
        self.assertEqual(parsing.heart_rate_values(fx.HEART_RATE), [120.0, 150.0])
        self.assertEqual(parsing.heart_rate_values([100, 0, 110]), [100.0, 110.0])
        self.assertEqual(parsing.heart_rate_values({"list": [{"bpm": 90}]}), [90.0])
        self.assertEqual(parsing.heart_rate_values(None), [])

    def test_downsample(self):
        self.assertEqual(len(parsing.downsample(list(range(1000)), 600)), 500)
        self.assertEqual(parsing.downsample([1, 2, 3], 600), [1, 2, 3])

    def test_epley_and_round_half(self):
        self.assertAlmostEqual(parsing.epley(50, 8), 63.333, places=2)
        self.assertEqual(parsing.epley(80, 1), 80)
        self.assertEqual(parsing.epley(0, 10), 0.0)
        self.assertEqual(parsing.round_half(47.49), 47.5)
        self.assertEqual(parsing.round_half(47.2), 47.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_parsing_free -v`
Expected: FAIL — `AttributeError: module 'speediance_mcp.speediance.parsing' has no attribute 'free_scale'`.

- [ ] **Step 3: Implement** — append to `src/speediance_mcp/speediance/parsing.py`

```python
# --- Free Lift -----------------------------------------------------------------

SCALE_TOLERANCE = 0.01
KG_LB_SCALE = 2.2


def free_scale(payload: dict) -> tuple[float, str | None]:
    """Free Lift set figures are x2.2 on kg accounts and unscaled on lb accounts.

    Reconcile the sets' capacity sum against the session's own total instead of guessing.
    """
    total = _num(payload.get("totalCapacity")) or 0.0
    raw = sum(_num((st.get("summary") or {}).get("totalCapacity")) or 0.0
              for action in payload.get("actionList") or [] for st in action.get("setList") or [])
    if total <= 0 or raw <= 0:
        return 1.0, None
    if abs(raw - total) <= SCALE_TOLERANCE * total:
        return 1.0, None
    if abs(raw / KG_LB_SCALE - total) <= SCALE_TOLERANCE * total:
        return KG_LB_SCALE, None
    return 1.0, (f"Free Lift loads didn't reconcile with the session total (sets sum to {raw:.1f}, "
                 f"session reports {total:.1f}); showing the raw values.")


def parse_free_exercises(payload) -> tuple[list[dict], list[str]]:
    """`freeTraining`: one object with actionList[].setList[] (summary + rawRepList)."""
    if not isinstance(payload, dict):
        return [], []
    scale, warning = free_scale(payload)
    out = []
    for action in payload.get("actionList") or []:
        kind = kind_of(action)
        entries = []
        for index, st in enumerate(action.get("setList") or [], 1):
            summary = st.get("summary") or {}
            done = int(_num(summary.get("finishedCount")) or 0)
            seconds = int(_num(summary.get("time")) or 0)
            weight = _num(summary.get("weight")) if kind == "reps" else None
            entries.append({
                "setIndex": index,
                "reps": done if kind != "timed" else None,
                "targetReps": None,
                "seconds": seconds,
                "weight": round(weight / scale, 1) if weight is not None else None,
                "level": None,
                "side": _side(summary.get("leftRight")),
                "maxHeartRate": _heart(summary.get("maxHeartRate")),
                "skipped": (done == 0) if kind == "reps" else (seconds == 0),
            })
        out.append(_exercise(action.get("actionLibraryName"), action.get("groupId"), kind, entries))
    return out, ([warning] if warning else [])


def normalize_session(route: str, payload) -> dict:
    if route == FREE_ROUTE:
        exercises, warnings = parse_free_exercises(payload)
    else:
        exercises, warnings = parse_list_exercises(payload if isinstance(payload, list) else []), []
    return {"exercises": exercises, "warnings": warnings}


def find_uuid(route: str, payload) -> str | None:
    """The watch recording id used by the heart-rate graph endpoint, if any."""
    if isinstance(payload, dict):
        if payload.get("showHeartGraph") in (0, False):
            return None
        return payload.get("uuid") or None
    for raw in payload or []:
        if not isinstance(raw, dict):
            continue
        for st in raw.get("finishedReps") or []:
            uuid = (st.get("trainingInfoDetail") or {}).get("uuid") if isinstance(st, dict) else None
            if uuid:
                return uuid
    return None


# --- cardio --------------------------------------------------------------------

def _round(value: float, digits: int) -> float:
    """Round half up."""
    factor = 10 ** digits
    return math.floor(value * factor + 0.5) / factor


def is_cardio(data: dict) -> bool:
    return ((_num(data.get("totalDistance")) or 0) > 0
            or data.get("existBoatingSkiDataGraph") is True
            or data.get("courseType") == 2)


def derive_cardio_stats(summary: dict) -> dict:
    """Session totals -> rowing/ski metrics. Missing or zero inputs give None, never NaN."""
    duration = _num(summary.get("trainingTime"))
    distance = _num(summary.get("totalDistance"))
    calories = _num(summary.get("calorie"))
    energy = _num(summary.get("totalEnergy"))
    has_duration = duration is not None and duration > 0
    has_distance = distance is not None and distance > 0
    return {
        "durationSec": int(duration) if duration is not None else None,
        "distanceM": _round(distance, 2) if distance is not None else None,
        "pace500": _round(duration / (distance / 500.0), 1) if has_duration and has_distance else None,
        "speedMs": _round(distance / duration, 2) if has_duration and has_distance else None,
        "calorie": calories,
        "calPerMin": _round(calories / (duration / 60.0), 1) if has_duration and calories is not None else None,
        "energyKJ": _round(energy / 1000.0, 1) if energy is not None else None,
        "avgWatts": int(_round(energy / duration, 0)) if has_duration and energy and energy > 0 else None,
        "completion": _num(summary.get("completionRate")),
        "rpe": _num(summary.get("rpe")),
    }


def intervals_from(payload) -> list[dict]:
    """Per-interval rows for guided cardio (the `freeTrainingDetail` route)."""
    out = []
    for raw in payload or []:
        if not isinstance(raw, dict):
            continue
        for st in raw.get("finishedReps") or []:
            seconds = _num(st.get("time")) or 0
            distance = _num(st.get("distance")) or 0
            if seconds <= 0:
                continue
            out.append({
                "interval": len(out) + 1,
                "seconds": int(seconds),
                "distanceM": _round(distance, 1) if distance else None,
                "pace500": _round(seconds / (distance / 500.0), 1) if distance > 0 else None,
                "strokeRate": _num(st.get("spm")) or None,
                "maxHeartRate": _heart(st.get("maxHeartRate")),
            })
    return out


# --- heart rate and strength math ------------------------------------------------

_HR_KEYS = ("heart", "hr", "bpm", "value", "rate")


def heart_rate_values(data: Any) -> list[float]:
    """Pull positive heart-rate samples out of the graph payload, whatever its exact shape."""
    items = data
    if isinstance(data, dict):
        items = next((v for v in data.values() if isinstance(v, list)), [])
    out = []
    for item in items or []:
        value = None
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            value = float(item)
        elif isinstance(item, dict):
            for key, raw in item.items():
                if isinstance(raw, (int, float)) and not isinstance(raw, bool) \
                        and any(k in key.lower() for k in _HR_KEYS):
                    value = float(raw)
                    break
        if value is not None and value > 0:
            out.append(value)
    return out


def downsample(values: list, limit: int) -> list:
    if len(values) <= limit:
        return list(values)
    step = math.ceil(len(values) / limit)
    return list(values[::step])


def epley(weight, reps) -> float:
    """Estimated one-rep max: weight x (1 + reps/30)."""
    w = _num(weight) or 0.0
    r = int(reps or 0)
    if w <= 0 or r <= 0:
        return 0.0
    return w if r == 1 else w * (1 + r / 30.0)


def round_half(value: float) -> float:
    """Round to the nearest 0.5 (the machine's load increment)."""
    return math.floor(value * 2 + 0.5) / 2
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_parsing tests.test_parsing_free -v`
Expected: 22 tests OK.

- [ ] **Step 5: Commit**

```bash
git add src/speediance_mcp/speediance/parsing.py tests/test_parsing_free.py
git commit -m "feat: Free Lift scale reconciliation, cardio, intervals, heart rate, 1RM math"
```

---

### Task 7: Coaching memory (SQLite)

**Files:**
- Create: `src/speediance_mcp/memory.py`
- Test: `tests/test_memory.py`

**Interfaces:**
- Produces: `PREF_DEFAULTS`; `class Memory(path: Path, *, now: Callable[[], datetime] | None = None)` with `remember_fact(fact: str, expires_days: int = 0, category: str = "note") -> dict` (`id, fact, category, createdAt, expiresAt`; unknown categories become `note`), `facts() -> list[dict]` (active only; prunes expired), `forget_fact(fact_id: int) -> bool`, `preferences() -> dict` (keys `goal, training_days, session_minutes, load_anchors, owned_equipment`), `set_preferences(**fields) -> dict` (raises `ValueError` on bad input), `mark_exercise(group_id: int, mark: str, name: str = "") -> dict` (`mark` is `preferred` / `avoided` / `none`), `marks() -> dict[int, dict]` (`{"mark", "name"}`), `close()`. Safe to use from several threads.

- [ ] **Step 1: Write the failing test** — `tests/test_memory.py`

```python
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

    def test_durable_and_expiring_facts(self):
        durable = self.mem.remember_fact("Left shoulder impingement — no overhead pressing")
        temp = self.mem.remember_fact("Travelling next week", expires_days=7, category="schedule")
        self.assertIsNone(durable["expiresAt"])
        self.assertEqual((durable["category"], temp["category"]), ("note", "schedule"))
        self.assertEqual(self.mem.remember_fact("x", category="bogus")["category"], "note")
        self.assertEqual(temp["expiresAt"], "2026-09-08T12:00:00Z")
        self.assertEqual(len(self.mem.facts()), 3)
        self.clock["now"] += dt.timedelta(days=8)
        self.assertEqual([f["fact"] for f in self.mem.facts()], [durable["fact"], "x"])

    def test_forget(self):
        fact = self.mem.remember_fact("Prefers mornings")
        self.assertTrue(self.mem.forget_fact(fact["id"]))
        self.assertFalse(self.mem.forget_fact(fact["id"]))

    def test_fact_validation(self):
        with self.assertRaises(ValueError):
            self.mem.remember_fact("   ")
        with self.assertRaises(ValueError):
            self.mem.remember_fact("x", expires_days=-1)

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

    def test_marks(self):
        self.mem.mark_exercise(321, "preferred", "Barbell Bent Over Row")
        self.mem.mark_exercise(600, "avoided", "Single Arm Cable Row")
        self.assertEqual(self.mem.marks()[321], {"mark": "preferred", "name": "Barbell Bent Over Row"})
        self.mem.mark_exercise(600, "none")
        self.assertNotIn(600, self.mem.marks())
        with self.assertRaises(ValueError):
            self.mem.mark_exercise(1, "love")

    def test_survives_reopen(self):
        self.mem.remember_fact("Durable")
        self.mem.close()
        again = Memory(self.path, now=lambda: self.clock["now"])
        self.addCleanup(again.close)
        self.assertEqual(again.facts()[0]["fact"], "Durable")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_memory -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'speediance_mcp.memory'`.

- [ ] **Step 3: Implement** — `src/speediance_mcp/memory.py`

```python
"""Coaching memory: facts, preferences and exercise marks in a local SQLite file."""

from __future__ import annotations

import datetime as dt
import json
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
  updated_at TEXT NOT NULL
);
"""

FACT_CATEGORIES = ("goal", "schedule", "injury", "preference", "dislike", "equipment", "body", "note")
PREF_DEFAULTS = {"goal": None, "training_days": [], "session_minutes": None,
                 "load_anchors": {}, "owned_equipment": []}
MARKS = ("preferred", "avoided")


def _iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Memory:
    def __init__(self, path: Path, *, now: Callable[[], dt.datetime] | None = None):
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._lock = threading.Lock()
        # Tools run in worker threads, so the connection is shared under a lock.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- facts -------------------------------------------------------------------
    def remember_fact(self, fact: str, expires_days: int = 0, category: str = "note") -> dict:
        fact = (fact or "").strip()
        category = (category or "note").strip().lower()
        if category not in FACT_CATEGORIES:
            category = "note"
        if not fact:
            raise ValueError("fact can't be empty")
        if int(expires_days) < 0:
            raise ValueError("expires_days can't be negative (use 0 for a durable fact)")
        now = self._now()
        expires = _iso(now + dt.timedelta(days=int(expires_days))) if int(expires_days) > 0 else None
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO facts (fact, category, created_at, expires_at) VALUES (?, ?, ?, ?)",
                                   (fact, category, _iso(now), expires))
            fact_id = cur.lastrowid
        return {"id": fact_id, "fact": fact, "category": category, "createdAt": _iso(now), "expiresAt": expires}

    def facts(self) -> list[dict]:
        now = _iso(self._now())
        with self._lock, self._db:
            self._db.execute("DELETE FROM facts WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,))
            rows = self._db.execute("SELECT id, fact, category, created_at, expires_at FROM facts ORDER BY id").fetchall()
        return [{"id": r["id"], "fact": r["fact"], "category": r["category"], "createdAt": r["created_at"],
                 "expiresAt": r["expires_at"]} for r in rows]

    def forget_fact(self, fact_id: int) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM facts WHERE id = ?", (int(fact_id),)).rowcount > 0

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
            for key, value in clean.items():
                self._db.execute("INSERT INTO preferences (key, value_json) VALUES (?, ?) "
                                 "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                                 (key, json.dumps(value)))
        return self.preferences()

    # --- exercise marks ------------------------------------------------------------
    def mark_exercise(self, group_id: int, mark: str, name: str = "") -> dict:
        mark = (mark or "").strip().lower()
        if mark not in MARKS + ("none",):
            raise ValueError("mark must be 'preferred', 'avoided' or 'none'")
        with self._lock, self._db:
            if mark == "none":
                self._db.execute("DELETE FROM exercise_marks WHERE group_id = ?", (int(group_id),))
            else:
                self._db.execute(
                    "INSERT INTO exercise_marks (group_id, mark, name, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET mark = excluded.mark, name = excluded.name, "
                    "updated_at = excluded.updated_at",
                    (int(group_id), mark, name or "", _iso(self._now())))
        return {"groupId": int(group_id), "mark": mark, "name": name or ""}

    def marks(self) -> dict[int, dict]:
        with self._lock:
            rows = self._db.execute("SELECT group_id, mark, name FROM exercise_marks").fetchall()
        return {r["group_id"]: {"mark": r["mark"], "name": r["name"]} for r in rows}


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
            return {str(int(k)): float(v) for k, v in value.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError("load_anchors must map a numeric group_id to a numeric weight") from exc
    if key == "owned_equipment":
        if not isinstance(value, list):
            raise ValueError("owned_equipment must be a list of accessory names")
        return [str(n).strip() for n in value if str(n).strip()]
    raise ValueError(f"unknown preference: {key}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_memory -v`
Expected: 7 tests OK.

- [ ] **Step 5: Commit**

```bash
git add src/speediance_mcp/memory.py tests/test_memory.py
git commit -m "feat: SQLite coaching memory (facts, preferences, exercise marks)"
```

---

### Task 8: Tool context and session tools

**Files:**
- Create: `src/speediance_mcp/tools/__init__.py` (empty), `src/speediance_mcp/tools/context.py`, `src/speediance_mcp/tools/_common.py`, `src/speediance_mcp/tools/account.py`, `src/speediance_mcp/tools/sessions.py`
- Modify: `tests/helpers.py` (append `make_app`)
- Test: `tests/test_tools_sessions.py`

**Interfaces:**
- Consumes: `load_credentials`, `save_credentials`, `data_dir`, `SpeedianceClient`, `SpeedianceAPI`, `SessionNotFound`, `Memory`, parsing functions, `routes.FREE_ROUTE`.
- Produces (`tools.context`): `LOGIN_HINTS`; `class App(home=None, *, mode="local", transport=None, min_interval=1.0, today=None)` with `.home`, `.mode`, `.login_hint`, `credentials()`, `.api` (raises `ToolError(login_hint)` without credentials), `.memory`, `close()`.
- Produces (`tools._common`): `parse_date(value, field="date") -> str`, `parse_month(value) -> tuple[str, str, str]` (month, first day, last day), `is_health_import(record) -> bool`, `record_summary(record) -> dict` (`trainingId, type, title, date, minutes, calorie, volume`).
- Produces tools: `account.check_connection(app)`; `sessions.get_calendar(app, month)`, `sessions.get_session_detail(app, training_id, type=0)`, `sessions.get_heart_rate(app, training_id)`, `sessions.get_training_stats(app, start, end)`; `sessions.ROWING_GAP_NOTE`.
- Produces (`tests.helpers`): `make_app(test, routes=None, *, creds=CREDS, mode="local") -> tuple[App, FakeSpeediance]`.

- [ ] **Step 1: Append to `tests/helpers.py`**

```python
import shutil
import tempfile
from pathlib import Path


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
```

- [ ] **Step 2: Write the failing test** — `tests/test_tools_sessions.py`

```python
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
        self.assertNotIn("2026-08-20", days)  # phone-health walks are not machine sessions
        self.assertEqual(list(days), sorted(days))

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

    def test_course_rowing_has_cardio_and_the_gap_note(self):
        app, _ = make_app(self)
        got = sessions.get_session_detail(app, 7001)
        self.assertEqual(got["cardio"]["pace500"], 296.8)
        self.assertEqual(got["note"], sessions.ROWING_GAP_NOTE)

    def test_guided_rowing_has_intervals(self):
        app, _ = make_app(self)
        got = sessions.get_session_detail(app, 7002)
        self.assertEqual(len(got["intervals"]), 2)
        self.assertNotIn("note", got)

    def test_free_lift(self):
        app, _ = make_app(self)
        got = sessions.get_session_detail(app, 6001)
        self.assertEqual(got["exercises"][0]["weights"], [100.0, 100.0])
        self.assertFalse(got["heartRateAvailable"])


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
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_tools_sessions -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'speediance_mcp.tools'`.

- [ ] **Step 4: Implement**

`src/speediance_mcp/tools/__init__.py`: empty file.

`src/speediance_mcp/tools/context.py`:
```python
"""What every tool gets: stored credentials, the Speediance API and the coaching memory."""

from __future__ import annotations

import datetime as dt
import threading
from pathlib import Path
from typing import Callable

from mcp.server.mcpserver.exceptions import ToolError

from ..config import Credentials, load_credentials, save_credentials
from ..memory import Memory
from ..paths import data_dir
from ..speediance.api import SpeedianceAPI
from ..speediance.client import SpeedianceClient

LOGIN_HINTS = {
    "local": "Not signed in to Speediance, or the session expired. Run `speediance-mcp login` "
             "in a terminal, then try again.",
    "remote": "The Speediance session expired. Reconnect the Speediance connector in Claude, then try again.",
}


class App:
    def __init__(self, home: Path | None = None, *, mode: str = "local", transport=None,
                 min_interval: float = 1.0, today: Callable[[], dt.date] | None = None):
        self.home = Path(home) if home else data_dir()
        self.mode = mode
        self._transport = transport
        self._min_interval = min_interval
        self._today = today
        self._api: SpeedianceAPI | None = None
        self._memory: Memory | None = None
        self._lock = threading.Lock()

    @property
    def login_hint(self) -> str:
        return LOGIN_HINTS[self.mode]

    def credentials(self) -> Credentials | None:
        return load_credentials(self.home)

    @property
    def api(self) -> SpeedianceAPI:
        with self._lock:
            if self._api is None:
                creds = load_credentials(self.home)
                if creds is None:
                    raise ToolError(self.login_hint)
                client = SpeedianceClient(creds, transport=self._transport, min_interval=self._min_interval,
                                          on_credentials=lambda c: save_credentials(c, self.home))
                self._api = SpeedianceAPI(client, self.home, today=self._today)
            return self._api

    @property
    def memory(self) -> Memory:
        with self._lock:
            if self._memory is None:
                self._memory = Memory(self.home / "speediance-mcp.db")
            return self._memory

    def close(self) -> None:
        with self._lock:
            if self._memory is not None:
                self._memory.close()
                self._memory = None
            if self._api is not None:
                self._api.client.close()
                self._api = None
```

`src/speediance_mcp/tools/_common.py`:
```python
"""Helpers shared by the tool modules."""

from __future__ import annotations

import datetime as dt

from mcp.server.mcpserver.exceptions import ToolError


def parse_date(value, field: str = "date") -> str:
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise ToolError(f"{field} must be a real date in YYYY-MM-DD form, got {value!r}.") from None


def parse_month(value) -> tuple[str, str, str]:
    try:
        if not isinstance(value, str) or len(value) != 7:
            raise ValueError
        year, month = value.split("-")
        first = dt.date(int(year), int(month), 1)
    except ValueError:
        raise ToolError(f"month must be YYYY-MM, got {value!r}.") from None
    following = dt.date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return value, first.isoformat(), (following - dt.timedelta(days=1)).isoformat()


def is_health_import(record: dict) -> bool:
    """Walks and rides synced from a phone health app — not machine sessions."""
    return not record.get("trainingId") or bool(record.get("belongUserHealth"))


def record_summary(record: dict | None) -> dict | None:
    if not record:
        return None
    return {"trainingId": record.get("trainingId"), "type": record.get("type"), "title": record.get("title"),
            "date": str(record.get("startTime", ""))[:10],
            "minutes": round((record.get("trainingTime") or 0) / 60),
            "calorie": record.get("calorie"), "volume": record.get("totalCapacity")}
```

`src/speediance_mcp/tools/account.py`:
```python
from __future__ import annotations

from ..speediance.client import AuthExpired, LoginFailed


def check_connection(app) -> dict:
    """Verify the Speediance login is live. Call this FIRST in a session, before any other tool,
    and stop if it reports connected:false — relay its message (the user must sign in again).
    A merely expired token is renewed silently when the password was remembered."""
    creds = app.credentials()
    if creds is None:
        return {"connected": False, "action": "login", "message": app.login_hint}
    try:
        profile = app.api.profile()
    except (AuthExpired, LoginFailed):
        return {"connected": False, "action": "login", "account": creds.email, "message": app.login_hint}
    creds = app.credentials() or creds  # a silent re-login may have refreshed it
    return {"connected": True, "account": creds.email,
            "spUserId": str(profile.get("appUserId") or creds.user_id),
            "displayUnit": creds.unit, "region": creds.region,
            "message": f"Connected to Speediance as {creds.email} — session is valid."}
```

`src/speediance_mcp/tools/sessions.py`:
```python
from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from ..speediance.api import SessionNotFound
from ..speediance.parsing import (
    derive_cardio_stats, downsample, find_uuid, heart_rate_values, intervals_from, is_cardio, normalize_session,
)
from ..speediance.routes import FREE_ROUTE
from ._common import is_health_import, parse_date, parse_month

ROWING_GAP_NOTE = ("Per-interval rowing data isn't available for rowing done as a course or custom workout — "
                   "Speediance's API only exposes session totals for those. Guided cardio sessions "
                   "(e.g. 'Aerobic Rowing') do include intervals.")
HR_POINTS = 600


def get_calendar(app, month: str) -> dict:
    """What's scheduled and trained in a month (`month` = 'YYYY-MM'). Each day carries its
    trainingPlanList. The raw calendar feed hides completed custom-template sessions, so completed
    sessions from the history feed are merged in with source:"history"; pass their trainingId to
    get_session_detail. Reservations (isReservation:true) are scheduled templates."""
    month, first, last = parse_month(month)
    days = [d for d in app.api.calendar(month) if isinstance(d, dict)]
    by_date = {d.get("date"): d for d in days}
    for record in app.api.history(first, last):
        if is_health_import(record):
            continue
        date = str(record.get("startTime", ""))[:10]
        day = by_date.setdefault(date, {"date": date, "trainingPlanList": []})
        plans = day.setdefault("trainingPlanList", [])
        if any(p.get("trainingId") == record.get("trainingId") for p in plans):
            continue
        plans.append({"trainingId": record.get("trainingId"), "type": record.get("type"),
                      "title": record.get("title"), "isFinish": 1, "source": "history",
                      "trainingTime": record.get("trainingTime"), "totalCapacity": record.get("totalCapacity"),
                      "calorie": record.get("calorie")})
    return {"month": month, "displayUnit": app.api.unit, "days": [by_date[k] for k in sorted(by_date) if k]}


def _heart_rate_present(exercises: list[dict]) -> bool:
    return any(entry.get("maxHeartRate") for ex in exercises for entry in ex["setLog"])


def get_session_detail(app, training_id: int, type: int = 0) -> dict:
    """The actual per-exercise log — sets, reps and weight per movement — for ONE completed session.
    `type` is optional and advisory: the session type is always resolved from this account's own
    history, so only sessions you own can be read. resolvedType:false means the id isn't in your
    history. Rowing/ski sessions add `cardio` (pace per 500m, speed, watts, calories/min); guided
    cardio adds per-interval rows. Weights are already in displayUnit — never convert."""
    try:
        record = app.api.find_session(training_id)
    except SessionNotFound:
        return {"trainingId": int(training_id), "resolvedType": False, "displayUnit": app.api.unit,
                "exercises": [],
                "message": f"Session {training_id} isn't in this account's training history, so no detail was "
                           "fetched. Use a trainingId from get_calendar or get_athlete_snapshot."}
    route, payload = app.api.session_payload(record)
    parsed = normalize_session(route, payload)
    if route == FREE_ROUTE and isinstance(payload, dict):
        summary = payload
    else:
        summary = app.api.session_summary(route, record["trainingId"])
    out = {"trainingId": int(record["trainingId"]), "title": record.get("title"),
           "date": str(record.get("startTime", ""))[:10], "detailType": route, "resolvedType": True,
           "sessionType": record.get("type"), "displayUnit": app.api.unit,
           "exercises": parsed["exercises"], "warnings": parsed["warnings"],
           "heartRateAvailable": bool(find_uuid(route, payload)) and _heart_rate_present(parsed["exercises"])}
    if isinstance(payload, dict) and payload.get("showHeartGraph") and find_uuid(route, payload):
        out["heartRateAvailable"] = True
    if is_cardio(summary) or is_cardio(record):
        out["cardio"] = derive_cardio_stats({**record, **summary})
        if record.get("type") == 7:
            out["intervals"] = intervals_from(app.api.free_intervals(record["trainingId"]))
        if not out.get("intervals"):
            out["note"] = ROWING_GAP_NOTE
    return out


def get_heart_rate(app, training_id: int) -> dict:
    """Second-by-second heart rate for a watch-paired session: average, max, min and a series
    (downsampled to at most 600 points). available:false when no watch recorded it."""
    record = app.api.find_session(training_id)
    route, payload = app.api.session_payload(record)
    uuid = find_uuid(route, payload)
    if not uuid:
        return {"trainingId": int(training_id), "available": False,
                "reason": "No heart-rate recording for this session (no paired watch)."}
    values = heart_rate_values(app.api.heart_rate(uuid))
    if not values:
        return {"trainingId": int(training_id), "available": False,
                "reason": "Speediance returned no heart-rate samples for this session."}
    return {"trainingId": int(training_id), "available": True, "samples": len(values),
            "avg": round(sum(values) / len(values), 1), "max": max(values), "min": min(values),
            "series": downsample(values, HR_POINTS)}


def get_training_stats(app, start: str, end: str) -> dict:
    """Totals between two dates (YYYY-MM-DD, inclusive): gym sessions, strength sessions, training
    minutes, calories, volume and energy — for questions like "how was my week?"."""
    start = parse_date(start, "start")
    end = parse_date(end, "end")
    if start > end:
        raise ToolError("start must be on or before end.")
    stats = app.api.range_stats(start, end)
    rows = [r for r in app.api.history(start, end) if not is_health_import(r)]
    return {"start": start, "end": end, "displayUnit": app.api.unit,
            "sessions": len(rows),
            "strengthSessions": sum(1 for r in rows if (r.get("totalCapacity") or 0) > 0),
            "trainingMinutes": round((stats.get("trainingTime") or 0) / 60),
            "calories": stats.get("calorie") or 0,
            "volume": stats.get("totalCapacity") or 0,
            "energyKJ": round((stats.get("totalEnergy") or 0) / 1000, 1),
            "note": "Minutes, calories, volume and energy are Speediance's range totals, which include activity "
                    "imported from a connected phone health app. The session counts are gym sessions only."}
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_tools_sessions -v`
Expected: 17 tests OK.

- [ ] **Step 6: Commit**

```bash
git add src/speediance_mcp/tools tests/helpers.py tests/test_tools_sessions.py
git commit -m "feat: tool context, check_connection and session tools"
```

---

### Task 9: Exercise library and exercise tools

**Files:**
- Create: `src/speediance_mcp/library.py`, `src/speediance_mcp/tools/exercises.py`
- Modify: `src/speediance_mcp/tools/_common.py` (append)
- Test: `tests/test_library.py`, `tests/test_tools_exercises.py`

**Interfaces:**
- Consumes: `parsing.kind_of`, `App`.
- Produces (`library`): `accessory_names(catalog) -> dict[int, str]`; `summarize_exercise(raw, names=None) -> dict` (`groupId, name, category, bodyParts, muscles, secondaryMuscles, equipmentIds, equipment, unilateral, kind, recommendedWeightKg`); `variant_id(raw) -> int | None`; `resolve_exercise(items, name) -> tuple[dict | None, list[dict]]`; `filter_exercises(items, *, query="", muscle="", category="", equipment="", kind="", owned=None, owned_only=False, marks=None, include_avoided=False, limit=60) -> list[dict]` (adds `mark`).
- Produces (`tools._common`): `class AmbiguousExercise(ToolError)` with `.matches: list[dict]`; `library_items(app) -> list[dict]`; `resolve_group(app, exercise="", group_id=0) -> dict` (a summarized item; raises `AmbiguousExercise` or `ToolError`).
- Produces tools (`tools.exercises`): `list_exercises`, `get_exercise`, `mark_exercise`, `list_accessories`, `get_exercise_history`.

- [ ] **Step 1: Write the failing tests**

`tests/test_library.py`:
```python
from __future__ import annotations

import unittest

from speediance_mcp import library
from tests import fixtures as fx

NAMES = library.accessory_names(fx.ACCESSORIES)
ITEMS = [library.summarize_exercise(raw, NAMES) for raw in fx.LIBRARY]


class TestLibrary(unittest.TestCase):
    def test_summarize(self):
        pulldown = next(i for i in ITEMS if i["groupId"] == 416)
        self.assertEqual(pulldown["name"], "Seated Barbell Lat Pulldown")
        self.assertEqual(pulldown["equipment"], ["Barbell", "Flat Bench"])
        self.assertEqual((pulldown["bodyParts"], pulldown["muscles"]), (["Back"], ["Lats"]))
        vita = next(i for i in ITEMS if i["groupId"] == 700)
        self.assertEqual(vita["kind"], "level")
        self.assertTrue(next(i for i in ITEMS if i["groupId"] == 600)["unilateral"])
        self.assertEqual(library.variant_id(fx.LIBRARY[0]), 3210)

    def test_unknown_accessory_id_is_named_generically(self):
        raw = dict(fx.LIBRARY[0], accessories="4,99")
        self.assertEqual(library.summarize_exercise(raw, NAMES)["equipment"], ["Barbell", "accessory 99"])

    def test_resolve(self):
        self.assertEqual(library.resolve_exercise(ITEMS, "vita row")[0]["groupId"], 700)       # exact
        self.assertEqual(library.resolve_exercise(ITEMS, "standing")[0]["groupId"], 294)       # unique prefix
        self.assertEqual(library.resolve_exercise(ITEMS, "bent over row")[0]["groupId"], 321)  # all words
        item, candidates = library.resolve_exercise(ITEMS, "row")
        self.assertIsNone(item)
        self.assertEqual(len(candidates), 5)
        self.assertEqual(library.resolve_exercise(ITEMS, "squat"), (None, []))

    def test_filters(self):
        marks = {600: {"mark": "avoided"}, 294: {"mark": "preferred"}}
        rows = library.filter_exercises(ITEMS, query="row", marks=marks)
        self.assertNotIn(600, [r["groupId"] for r in rows])
        self.assertIn(600, [r["groupId"] for r in library.filter_exercises(ITEMS, query="row", marks=marks,
                                                                           include_avoided=True)])
        self.assertEqual([r["groupId"] for r in library.filter_exercises(ITEMS, muscle="biceps")], [294])
        owned = library.filter_exercises(ITEMS, owned=["barbell"], owned_only=True)
        self.assertEqual(sorted(r["groupId"] for r in owned), [294, 321, 424])
        self.assertEqual(library.filter_exercises(ITEMS, marks=marks)[0]["groupId"], 294)  # preferred first
        self.assertEqual(len(library.filter_exercises(ITEMS, limit=2)), 2)
        self.assertEqual([r["groupId"] for r in library.filter_exercises(ITEMS, kind="level")], [700])
```

`tests/test_tools_exercises.py`:
```python
from __future__ import annotations

import unittest

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp.tools import exercises
from tests.helpers import make_app


class TestExerciseTools(unittest.TestCase):
    def test_list_exercises_and_library_cache(self):
        app, fake = make_app(self)
        got = exercises.list_exercises(app, query="row")
        self.assertEqual(got["count"], 5)
        exercises.list_exercises(app, muscle="biceps")
        self.assertEqual(len(fake.calls("GET", "/api/app/actionLibraryTab/list")), 1)

    def test_owned_only_needs_owned_equipment(self):
        app, _ = make_app(self)
        with self.assertRaises(ToolError):
            exercises.list_exercises(app, owned_only=True)
        app.memory.set_preferences(owned_equipment=["Barbell"])
        got = exercises.list_exercises(app, owned_only=True)
        self.assertEqual(sorted(e["groupId"] for e in got["exercises"]), [294, 321, 424])

    def test_avoided_hidden_by_default(self):
        app, _ = make_app(self)
        exercises.mark_exercise(app, "avoided", group_id=600)
        names = [e["name"] for e in exercises.list_exercises(app, query="row")["exercises"]]
        self.assertNotIn("Single Arm Cable Row", names)

    def test_get_exercise(self):
        app, _ = make_app(self)
        got = exercises.get_exercise(app, 321)
        self.assertEqual((got["name"], got["equipment"], got["unilateral"]), ("Barbell Bent Over Row", ["Barbell"], False))
        self.assertEqual(got["video"], "https://example.test/321.mp4")
        with self.assertRaises(ToolError):
            exercises.get_exercise(app, 123456)

    def test_mark_exercise(self):
        app, _ = make_app(self)
        got = exercises.mark_exercise(app, "preferred", group_id=321)
        self.assertEqual((got["mark"], got["name"]), ("preferred", "Barbell Bent Over Row"))
        self.assertEqual(exercises.mark_exercise(app, "avoided", name="vita row")["groupId"], 700)
        with self.assertRaises(ToolError):
            exercises.mark_exercise(app, "love", group_id=321)
        with self.assertRaises(ToolError):
            exercises.mark_exercise(app, "avoided", name="row")  # ambiguous: nothing marked
        self.assertNotIn(600, app.memory.marks())

    def test_list_accessories_dedupes_by_name(self):
        app, _ = make_app(self)
        app.memory.set_preferences(owned_equipment=["handles"])
        rows = {a["name"]: a for a in exercises.list_accessories(app)["accessories"]}
        self.assertEqual(len(rows["Handles"]["ids"]), 2)
        self.assertTrue(rows["Handles"]["owned"])
        self.assertEqual(rows["Flat Bench"]["type"], "furniture")

    def test_exercise_history(self):
        app, _ = make_app(self)
        got = exercises.get_exercise_history(app, exercise="bent over row")
        self.assertEqual([s["date"] for s in got["sessions"]], ["2026-08-22", "2026-08-29"])
        self.assertEqual(got["summary"]["bestWeight"], {"value": 50.0, "date": "2026-08-29"})
        self.assertEqual(got["sessions"][0]["minWeight"], 30.0)

    def test_exercise_history_ambiguous_and_empty(self):
        app, _ = make_app(self)
        got = exercises.get_exercise_history(app, exercise="row")
        self.assertTrue(got["needsPick"])
        self.assertEqual(len(got["matches"]), 5)
        empty = exercises.get_exercise_history(app, groupId=424)
        self.assertEqual((empty["sessions"], empty["summary"]["sessions"]), ([], 0))
        with self.assertRaises(ToolError):
            exercises.get_exercise_history(app, exercise="squat")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_library tests.test_tools_exercises -v`
Expected: FAIL — `ImportError: cannot import name 'library'`.

- [ ] **Step 3: Implement**

`src/speediance_mcp/library.py`:
```python
"""The exercise catalog: summarize raw library items, filter them, resolve names. Pure."""

from __future__ import annotations

from .speediance.parsing import kind_of


def accessory_names(catalog: list[dict]) -> dict[int, str]:
    """accessory id -> name. Exercises reference the small ids (1 Flat Bench, 4 Barbell, 5 Handles...)."""
    out = {}
    for item in catalog or []:
        try:
            out[int(item["id"])] = str(item.get("name", "")).strip()
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _ids(csv) -> list[int]:
    return [int(x) for x in str(csv or "").split(",") if x.strip().isdigit()]


def summarize_exercise(raw: dict, names: dict[int, str] | None = None) -> dict:
    names = names or {}
    main = raw.get("mainMuscleGroupList") or []
    aux = raw.get("auxiliaryMuscleGroupList") or []
    equipment_ids = _ids(raw.get("accessories"))
    return {
        "groupId": raw.get("id"),
        "name": raw.get("title") or "",
        "category": raw.get("tabName") or "",
        "bodyParts": sorted({m.get("categoryName") for m in main if m.get("categoryName")}),
        "muscles": [m.get("muscleGroupName") for m in main if m.get("muscleGroupName")],
        "secondaryMuscles": [m.get("muscleGroupName") for m in aux if m.get("muscleGroupName")],
        "equipmentIds": equipment_ids,
        "equipment": [names.get(i, f"accessory {i}") for i in equipment_ids],
        "unilateral": raw.get("isLeftRight") == 1,
        "kind": kind_of(raw),
        "recommendedWeightKg": raw.get("recommendedWeight"),  # the library's recommendation is in kg
    }


def variant_id(raw: dict) -> int | None:
    variants = raw.get("actionLibraryList") or []
    return variants[0].get("id") if variants else None


def _norm(text) -> str:
    return " ".join(str(text or "").lower().split())


def resolve_exercise(items: list[dict], name: str) -> tuple[dict | None, list[dict]]:
    """Exact name, then a unique prefix, then a unique all-words match. Ambiguity returns candidates."""
    query = _norm(name)
    if not query:
        return None, []
    exact = [i for i in items if _norm(i["name"]) == query]
    if exact:
        return exact[0], []
    words = query.split()
    for group in ([i for i in items if _norm(i["name"]).startswith(query)],
                  [i for i in items if all(w in _norm(i["name"]) for w in words)]):
        if len(group) == 1:
            return group[0], []
        if group:
            return None, group[:10]
    return None, []


def filter_exercises(items: list[dict], *, query: str = "", muscle: str = "", category: str = "",
                     equipment: str = "", kind: str = "", owned: list[str] | None = None, owned_only: bool = False,
                     marks: dict | None = None, include_avoided: bool = False, limit: int = 60) -> list[dict]:
    marks = marks or {}
    owned_set = {_norm(o) for o in (owned or [])}
    words = _norm(query).split()
    out = []
    for item in items:
        mark = (marks.get(item["groupId"]) or {}).get("mark")
        if mark == "avoided" and not include_avoided:
            continue
        if words and not all(w in _norm(item["name"]) for w in words):
            continue
        if muscle and not any(_norm(muscle) in _norm(x) for x in item["bodyParts"] + item["muscles"]):
            continue
        if kind and _norm(kind) != item["kind"]:
            continue
        if category and _norm(category) not in _norm(item["category"]):
            continue
        if equipment and not any(_norm(equipment) in _norm(e) for e in item["equipment"]):
            continue
        if owned_only and not all(_norm(e) in owned_set for e in item["equipment"]):
            continue
        out.append({**item, "mark": mark})
    out.sort(key=lambda i: (i["mark"] != "preferred", i["name"].lower()))
    return out[:limit]
```

Append to `src/speediance_mcp/tools/_common.py`:
```python
from ..library import accessory_names, resolve_exercise, summarize_exercise


class AmbiguousExercise(ToolError):
    def __init__(self, name: str, matches: list[dict]):
        self.matches = [{"groupId": m["groupId"], "name": m["name"],
                         "muscle": (m["muscles"] or [None])[0], "equipment": m["equipment"]} for m in matches]
        listing = "; ".join(f"{m['name']} (group_id {m['groupId']})" for m in self.matches)
        super().__init__(f"'{name}' matches several exercises: {listing}. Call again with the group_id you mean.")


def library_items(app) -> list[dict]:
    names = accessory_names(app.api.accessories())
    return [summarize_exercise(raw, names) for raw in app.api.library()]


def resolve_group(app, exercise: str = "", group_id: int = 0) -> dict:
    items = library_items(app)
    if group_id:
        for item in items:
            if item["groupId"] == int(group_id):
                return item
        raise ToolError(f"No exercise with group_id {group_id} in the library.")
    if not str(exercise or "").strip():
        raise ToolError("Give an exercise name or a group_id.")
    item, candidates = resolve_exercise(items, exercise)
    if item:
        return item
    if candidates:
        raise AmbiguousExercise(exercise, candidates)
    raise ToolError(f"No exercise matches '{exercise}'. Try list_exercises with a shorter query.")
```

`src/speediance_mcp/tools/exercises.py`:
```python
from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from ..library import accessory_names, filter_exercises, summarize_exercise
from ..speediance.client import NotFound
from ._common import AmbiguousExercise, library_items, resolve_group

LIST_FIELDS = ("groupId", "name", "category", "bodyParts", "muscles", "equipment", "unilateral", "kind", "mark")


def list_exercises(app, query: str = "", muscle: str = "", category: str = "", equipment: str = "",
                   kind: str = "", owned_only: bool = False, include_avoided: bool = False, limit: int = 60) -> dict:
    """Search the Speediance exercise library. Filters: name words (`query`), body part or muscle
    (`muscle`, e.g. "chest", "biceps"), library tab (`category`), equipment name, `kind`
    ("reps", "timed" or "level" for Vita), and
    `owned_only` (only moves whose equipment you own — set it with set_preferences(owned_equipment)).
    ⊘avoided movements are hidden unless include_avoided=true; ★preferred ones sort first.
    The first call downloads the library (~30 s); later calls use a 24-hour cache."""
    items = library_items(app)
    marks = app.memory.marks()
    owned = app.memory.preferences()["owned_equipment"]
    if owned_only and not owned:
        raise ToolError("No owned equipment is saved yet. Call list_accessories, then "
                        "set_preferences(owned_equipment=[names]).")
    rows = filter_exercises(items, query=query, muscle=muscle, category=category, equipment=equipment,
                            kind=kind, owned=owned, owned_only=owned_only, marks=marks, include_avoided=include_avoided,
                            limit=max(1, min(int(limit), 200)))
    return {"count": len(rows), "displayUnit": app.api.unit,
            "exercises": [{k: row[k] for k in LIST_FIELDS} for row in rows]}


def get_exercise(app, group_id: int) -> dict:
    """One movement's details: muscles, equipment, whether it's unilateral (one side at a time —
    matters when building workouts), form description and cues, image and video."""
    try:
        raw = app.api.exercise(group_id)
    except NotFound:
        raw = None
    if not raw:
        raise ToolError(f"No exercise with group_id {group_id}.")
    item = summarize_exercise(raw, accessory_names(app.api.accessories()))
    variants = raw.get("actionLibraryList") or []
    item.pop("equipmentIds")
    return {**item,
            "description": raw.get("context") or raw.get("showDetails") or "",
            "cues": {k: raw[k] for k in ("motionFeeling", "breathingRate", "errorCorrection") if raw.get(k)},
            "image": raw.get("img"),
            "video": variants[0].get("videoPath") if variants else None,
            "mark": (app.memory.marks().get(int(group_id)) or {}).get("mark")}


def mark_exercise(app, mark: str, group_id: int = 0, name: str = "") -> dict:
    """Mark a movement ★preferred, ⊘avoided, or clear it ("none"). Identify it by group_id, or by
    `name` when you have no id (an ambiguous name marks nothing and lists candidates). Use when the
    user makes a LASTING per-exercise preference clear (it always hurts / they love it). Avoided moves
    are never programmed unless the user asks for them by name."""
    if str(mark).strip().lower() not in ("preferred", "avoided", "none"):
        raise ToolError("mark must be 'preferred', 'avoided' or 'none'.")
    item = resolve_group(app, name, group_id)
    return app.memory.mark_exercise(item["groupId"], mark, item["name"])


def list_accessories(app) -> dict:
    """Speediance's accessory catalog (bars, handles, rope, benches, AeroRow...), deduplicated by name,
    each flagged `owned`. Save what the user owns with set_preferences(owned_equipment=[names])."""
    owned = {o.lower() for o in app.memory.preferences()["owned_equipment"]}
    by_name: dict[str, dict] = {}
    for item in app.api.accessories():
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        entry = by_name.setdefault(name.lower(), {"name": name, "ids": [],
                                                  "type": "furniture" if item.get("type") == 1 else "attachment"})
        entry["ids"].append(item.get("id"))
    rows = [{**e, "owned": e["name"].lower() in owned} for e in sorted(by_name.values(), key=lambda e: e["name"].lower())]
    return {"accessories": rows,
            "hint": "Save owned items with set_preferences(owned_equipment=[names]); "
                    "list_exercises(owned_only=true) then hides moves needing anything else."}


def get_exercise_history(app, exercise: str = "", groupId: int = 0, limit: int = 50) -> dict:
    """EVERY time the user has done ONE movement, oldest -> newest: the "how is my bench press going?"
    tool. Give the name as the user says it, or a groupId. If several exercises match, nothing is
    fetched and the reply is {needsPick:true, matches:[...]} — ask which one. One entry per training
    DAY (same-day sessions combined): topWeight, volume, and minWeight when the day had a range."""
    try:
        item = resolve_group(app, exercise, groupId)
    except AmbiguousExercise as exc:
        return {"needsPick": True, "matches": exc.matches}
    rows = sorted(app.api.exercise_stats(item["groupId"], max_days=max(1, int(limit))),
                  key=lambda r: str(r.get("dayStr", "")))
    sessions = []
    for row in rows:
        entry = {"date": row.get("dayStr"), "topWeight": row.get("maxWeight"), "volume": row.get("totalCapacity")}
        if row.get("minWeight") not in (None, row.get("maxWeight")):
            entry["minWeight"] = row.get("minWeight")
        sessions.append(entry)
    head = {"exercise": {"groupId": item["groupId"], "name": item["name"],
                         "muscle": (item["muscles"] or [None])[0], "equipment": item["equipment"],
                         "kind": item["kind"]},
            "displayUnit": app.api.unit, "source": "userActionStatPage"}
    if not sessions:
        return {**head, "sessions": [], "summary": {"sessions": 0}, "note": "No logged history for this movement yet."}
    best = max(sessions, key=lambda s: s["topWeight"] or 0)
    best_volume = max(sessions, key=lambda s: s["volume"] or 0)
    return {**head, "sessions": sessions,
            "summary": {"sessions": len(sessions), "firstSeen": sessions[0]["date"], "lastSeen": sessions[-1]["date"],
                        "latestTopWeight": sessions[-1]["topWeight"],
                        "bestWeight": {"value": best["topWeight"], "date": best["date"]},
                        "bestVolume": {"value": best_volume["volume"], "date": best_volume["date"]}},
            "note": "One entry per training day; several sessions on the same day are combined."}
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_library tests.test_tools_exercises -v`
Expected: 13 tests OK.

- [ ] **Step 5: Commit**

```bash
git add src/speediance_mcp/library.py src/speediance_mcp/tools tests/test_library.py tests/test_tools_exercises.py
git commit -m "feat: exercise library search, marks, accessories and exercise history"
```

---

### Task 10: Coaching tools

**Files:**
- Create: `src/speediance_mcp/tools/coaching.py`
- Test: `tests/test_tools_coaching.py`

**Interfaces:**
- Consumes: `normalize_session`, `epley`, `round_half`, `resolve_group`, `record_summary`, `is_health_import`, `App`.
- Produces: `get_athlete_snapshot(app, days=30)`, `get_strength_profile(app, limit=15, days=90)`, `compare_sessions(app, training_id, previous_training_id=0)`, `suggest_load(app, reps, exercise="", groupId=0, rir=2)`.

- [ ] **Step 1: Write the failing test** — `tests/test_tools_coaching.py`

```python
from __future__ import annotations

import unittest

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp.tools import coaching
from tests import fixtures as fx
from tests.helpers import make_app


def empty_account_routes():
    routes = fx.standard_routes()
    routes[("GET", fx.HISTORY_PATH)] = []
    routes[("GET", fx.STATS_PATH)] = []
    routes[("GET", fx.TEMPLATES_PATH)] = []
    return routes


class TestSnapshot(unittest.TestCase):
    def test_snapshot(self):
        app, _ = make_app(self)
        app.memory.remember_fact("No overhead pressing")
        app.memory.mark_exercise(321, "preferred", "Barbell Bent Over Row")
        self.assertEqual([h["trainingId"] for h in coaching.get_athlete_snapshot(app)["history"]], [5001, 5000])
        got = coaching.get_athlete_snapshot(app, days=30)
        self.assertEqual([h["trainingId"] for h in got["history"]], [5001, 5000, 7002, 7001])
        self.assertEqual(got["profile"]["age"], 36)
        self.assertEqual(got["memory"]["facts"][0]["fact"], "No overhead pressing")
        self.assertEqual(got["memory"]["preferredExercises"], [{"groupId": 321, "name": "Barbell Bent Over Row"}])
        self.assertEqual(got["displayUnit"], "lb")


class TestStrength(unittest.TestCase):
    def test_strength_profile(self):
        app, _ = make_app(self)
        got = coaching.get_strength_profile(app)
        self.assertEqual([m["groupId"] for m in got["movements"]], [424, 321, 500])
        row = got["movements"][1]
        self.assertEqual(row["estimated1RM"], 63.3)
        self.assertEqual(row["bestSet"], {"date": "2026-08-29", "weight": 50.0, "reps": 8})
        self.assertEqual(row["topWeightChange"], 5.0)
        self.assertIsNone(got["movements"][0]["topWeightChange"])

    def test_compare_finds_previous_session(self):
        app, _ = make_app(self)
        got = coaching.compare_sessions(app, 5001)
        self.assertEqual(got["previous"]["trainingId"], 5000)
        row = next(m for m in got["movements"] if m["name"] == "Barbell Bent Over Row")
        self.assertEqual(row["change"], {"topWeight": 5.0, "reps": 0, "volume": 40.0})
        fly = next(m for m in got["movements"] if m["name"] == "Cable Fly")
        self.assertIsNone(fly["previous"])

    def test_compare_explicit_previous(self):
        app, _ = make_app(self)
        got = coaching.compare_sessions(app, 5001, previous_training_id=5000)
        self.assertEqual(got["previous"]["trainingId"], 5000)

    def test_suggest_load(self):
        app, _ = make_app(self)
        got = coaching.suggest_load(app, reps=8, exercise="bent over row")
        self.assertEqual(got["suggestedWeight"], 47.5)
        self.assertEqual(got["basis"]["date"], "2026-08-29")
        self.assertEqual((got["basis"]["weight"], got["basis"]["reps"]), (50.0, 8))

    def test_suggest_load_timed_and_ambiguous_and_validation(self):
        app, _ = make_app(self)
        self.assertIsNone(coaching.suggest_load(app, reps=10, groupId=900)["suggestedWeight"])
        with self.assertRaises(ToolError):
            coaching.suggest_load(app, reps=8, exercise="row")
        with self.assertRaises(ToolError):
            coaching.suggest_load(app, reps=0, groupId=321)


class TestEmptyAccount(unittest.TestCase):
    def test_everything_degrades_gracefully(self):
        app, _ = make_app(self, empty_account_routes())
        self.assertEqual(coaching.get_athlete_snapshot(app)["history"], [])
        profile = coaching.get_strength_profile(app)
        self.assertEqual(profile["movements"], [])
        self.assertIn("No weighted strength sessions", profile["note"])
        got = coaching.suggest_load(app, reps=8, groupId=321)
        self.assertIsNone(got["suggestedWeight"])
        self.assertIn("No history", got["note"])
        app.memory.set_preferences(load_anchors={"321": 40})
        self.assertEqual(coaching.suggest_load(app, reps=8, groupId=321)["suggestedWeight"], 40.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_tools_coaching -v`
Expected: FAIL — `ImportError: cannot import name 'coaching'`.

- [ ] **Step 3: Implement** — `src/speediance_mcp/tools/coaching.py`

```python
from __future__ import annotations

import datetime as dt

from mcp.server.mcpserver.exceptions import ToolError

from ..speediance.parsing import epley, normalize_session, round_half
from ._common import is_health_import, record_summary, resolve_group

MAX_SESSIONS_SCANNED = 10


def _age(birthday, today: dt.date) -> int | None:
    try:
        born = dt.date.fromisoformat(str(birthday)[:10])
    except ValueError:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _recent(app, days: int) -> list[dict]:
    today = app.api.today()
    start = (today - dt.timedelta(days=days)).isoformat()
    rows = [r for r in app.api.history(start, today.isoformat()) if not is_health_import(r)]
    return sorted(rows, key=lambda r: str(r.get("startTime", "")), reverse=True)


def _exercises(app, record: dict) -> list[dict]:
    route, payload = app.api.session_payload(record)
    return normalize_session(route, payload)["exercises"]


def _best_set(exercise: dict) -> dict | None:
    sets = [s for s in exercise["setLog"] if s.get("weight") and s.get("reps")]
    return max(sets, key=lambda s: epley(s["weight"], s["reps"])) if sets else None


def get_athlete_snapshot(app, days: int = 14) -> dict:
    """Everything needed before planning: profile (bodyweight, age, watch), the coaching memory
    (facts, preferences, ★preferred / ⊘avoided exercises, owned equipment) and recent sessions.
    Check the memory before any recommendation and respect it."""
    days = max(1, min(int(days), 365))
    profile = app.api.profile()
    memory = app.memory
    marks = memory.marks()
    return {
        "displayUnit": app.api.unit,
        "windowDays": days,
        "profile": {
            "sex": profile.get("sex"),
            "bodyweight": profile.get("weight"),  # as Speediance stores it; userinfo.weightUnit is unreliable
            "height": profile.get("height"),
            "age": _age(profile.get("birthday"), app.api.today()),
            "watchPaired": bool(profile.get("isWatch")),
            "trainingDays": profile.get("trainingDays"),
            "lifetime": {"minutes": round((profile.get("totalTrainingTime") or 0) / 60),
                         "volume": profile.get("totalCapacity"), "calories": profile.get("totalCalorie")},
        },
        "memory": {
            "facts": memory.facts(),
            "preferences": memory.preferences(),
            "preferredExercises": [{"groupId": g, "name": m["name"]} for g, m in marks.items() if m["mark"] == "preferred"],
            "avoidedExercises": [{"groupId": g, "name": m["name"]} for g, m in marks.items() if m["mark"] == "avoided"],
        },
        "history": [record_summary(r) for r in _recent(app, days)],
    }


def get_strength_profile(app, limit: int = 15, days: int = 90) -> dict:
    """Per movement trained recently: estimated 1RM (Epley) from its best set, that set, and how the
    top weight moved across the window. Scans up to the 10 most recent weighted sessions."""
    rows = [r for r in _recent(app, max(1, int(days))) if (r.get("totalCapacity") or 0) > 0][:MAX_SESSIONS_SCANNED]
    movements: dict = {}
    for record in rows:  # newest first
        date = str(record.get("startTime", ""))[:10]
        for exercise in _exercises(app, record):
            best = _best_set(exercise) if exercise["kind"] == "reps" else None
            if not best:
                continue
            estimate = epley(best["weight"], best["reps"])
            key = exercise["groupId"] or exercise["name"]
            entry = movements.setdefault(key, {"groupId": exercise["groupId"], "name": exercise["name"],
                                               "estimated1RM": 0.0, "bestSet": None, "sessions": 0,
                                               "_latest": None, "_oldest": None})
            entry["sessions"] += 1
            if entry["_latest"] is None:
                entry["_latest"] = exercise["topWeight"]
            entry["_oldest"] = exercise["topWeight"]
            if estimate > entry["estimated1RM"]:
                entry["estimated1RM"] = round(estimate, 1)
                entry["bestSet"] = {"date": date, "weight": best["weight"], "reps": best["reps"]}
    ranked = sorted(movements.values(), key=lambda m: m["estimated1RM"], reverse=True)[:max(1, int(limit))]
    for entry in ranked:
        latest, oldest = entry.pop("_latest"), entry.pop("_oldest")
        entry["topWeightChange"] = (round(latest - oldest, 1)
                                    if entry["sessions"] > 1 and latest is not None and oldest is not None else None)
    out = {"displayUnit": app.api.unit, "windowDays": int(days), "sessionsScanned": len(rows), "movements": ranked,
           "method": "Estimated 1RM uses the Epley formula, weight x (1 + reps/30), on each movement's best set."}
    if not ranked:
        out["note"] = "No weighted strength sessions in this window."
    return out


def _movements(app, record: dict) -> dict:
    out = {}
    for exercise in _exercises(app, record):
        out[exercise["groupId"] or exercise["name"]] = {
            "name": exercise["name"], "topWeight": exercise["topWeight"],
            "reps": sum(r or 0 for r in exercise["reps"]), "volume": exercise["volume"],
            "sets": exercise["sets"], "skippedSets": exercise["skippedSets"]}
    return out


def _delta(current, previous):
    return round(current - previous, 1) if current is not None and previous is not None else None


def compare_sessions(app, training_id: int, previous_training_id: int = 0) -> dict:
    """Compare a session with an earlier one, per movement: top weight, total reps and volume deltas.
    previous_training_id=0 picks the most recent earlier session that shares a movement."""
    current = app.api.find_session(training_id)
    current_moves = _movements(app, current)
    previous, previous_moves = None, {}
    if previous_training_id:
        previous = app.api.find_session(previous_training_id)
        previous_moves = _movements(app, previous)
    else:
        earlier = sorted((r for r in app.api.history_index().values()
                          if str(r.get("startTime", "")) < str(current.get("startTime", ""))),
                         key=lambda r: str(r.get("startTime", "")), reverse=True)[:MAX_SESSIONS_SCANNED]
        for record in earlier:
            moves = _movements(app, record)
            if set(moves) & set(current_moves):
                previous, previous_moves = record, moves
                break
    rows = []
    for key, now in current_moves.items():
        before = previous_moves.get(key)
        row = {"name": now["name"], "current": now, "previous": before}
        if before:
            row["change"] = {"topWeight": _delta(now["topWeight"], before["topWeight"]),
                             "reps": now["reps"] - before["reps"],
                             "volume": round(now["volume"] - before["volume"], 1)}
        rows.append(row)
    out = {"displayUnit": app.api.unit, "current": record_summary(current), "previous": record_summary(previous),
           "movements": rows}
    if previous is None:
        out["note"] = "No earlier session shares a movement with this one."
    return out


def _latest_top_set(app, group_id: int) -> dict | None:
    days = sorted(app.api.exercise_stats(group_id, max_days=10), key=lambda r: str(r.get("dayStr", "")), reverse=True)
    index = app.api.history_index()
    for day in days:
        if not day.get("maxWeight"):
            continue
        for record in (r for r in index.values() if str(r.get("startTime", ""))[:10] == day.get("dayStr")):
            for exercise in _exercises(app, record):
                best = _best_set(exercise) if exercise["groupId"] == group_id else None
                if best:
                    return {"date": day["dayStr"], "weight": best["weight"], "reps": best["reps"],
                            "trainingId": int(record["trainingId"])}
    return None


def suggest_load(app, reps: int, exercise: str = "", groupId: int = 0, rir: int = 2) -> dict:
    """Suggest a working weight for `reps` reps leaving `rir` reps in reserve, from the movement's most
    recent best set (Epley estimate), falling back to the user's saved load anchor. Reports its basis."""
    if not 1 <= int(reps) <= 30:
        raise ToolError("reps must be between 1 and 30.")
    if not 0 <= int(rir) <= 5:
        raise ToolError("rir (reps in reserve) must be between 0 and 5.")
    item = resolve_group(app, exercise, groupId)
    gid = item["groupId"]
    anchor = app.memory.preferences()["load_anchors"].get(str(gid))
    out = {"exercise": {"groupId": gid, "name": item["name"]}, "displayUnit": app.api.unit,
           "reps": int(reps), "rir": int(rir), "anchor": anchor}
    if (app.memory.marks().get(gid) or {}).get("mark") == "avoided":
        out["warning"] = "This movement is marked ⊘ avoided."
    if item["kind"] != "reps":
        return {**out, "suggestedWeight": None, "note": "This movement is timed or level-based, so there's no weight to suggest."}
    basis = _latest_top_set(app, gid)
    if basis:
        estimate = epley(basis["weight"], basis["reps"])
        target = round_half(estimate / (1 + (int(reps) + int(rir)) / 30.0))
        return {**out, "suggestedWeight": target, "basis": {**basis, "estimated1RM": round(estimate, 1)},
                "method": "Epley 1RM from the most recent best set, solved for reps + rir."}
    if anchor is not None:
        return {**out, "suggestedWeight": anchor, "basis": None, "note": "No logged sets found; using the saved load anchor."}
    return {**out, "suggestedWeight": None, "basis": None,
            "note": "No history or load anchor for this movement yet. Start light, or save an anchor with "
                    "set_preferences(load_anchors={group_id: weight})."}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_tools_coaching -v`
Expected: 8 tests OK.

- [ ] **Step 5: Commit**

```bash
git add src/speediance_mcp/tools/coaching.py tests/test_tools_coaching.py
git commit -m "feat: athlete snapshot, strength profile, session comparison and load suggestions"
```

---

### Task 11: Template writes and workout tools

**Files:**
- Create: `src/speediance_mcp/speediance/writes.py`, `src/speediance_mcp/tools/workouts.py`
- Test: `tests/test_writes.py`, `tests/test_tools_workouts.py`

**Interfaces:**
- Consumes: `library.summarize_exercise`, `library.variant_id`, `library.accessory_names`, `tools._common.resolve_group`, `library_items`, `App`.
- Produces (`speediance.writes`): `validate_sets(kind, sets, default_rest=60) -> list[dict]` (raises `ValueError`); `build_template(name, specs, *, unit, device_type, template_id=None) -> dict`; `read_template(detail) -> dict`; `sets_for_kind(kind, stored_sets, default_rest=60) -> list[dict]`; `verify(body, stored) -> list[str]`. A *spec* is `{"groupId", "variantId", "name", "kind", "unilateral", "sets"}`; a validated set is `{"reps", "weight", "side", "rest"}` (reps kind) or `{"seconds", "level", "side", "rest"}`.
- Produces tools (`tools.workouts`): `list_my_workouts(app)`, `get_workout(app, code)`, `create_workout(app, name, exercises)`, `update_workout(app, template_id, name=None, exercises=None)`, `delete_workout(app, template_id)` (`template_id` = code or numeric id).

- [ ] **Step 1: Write the failing tests**

`tests/test_writes.py`:
```python
from __future__ import annotations

import unittest

from speediance_mcp.speediance import writes

ROW = {"groupId": 321, "variantId": 3210, "name": "Row", "kind": "reps", "unilateral": False,
       "sets": [{"reps": 8, "weight": 50.0, "side": None, "rest": 60}] * 2}
ONE_ARM = {"groupId": 600, "variantId": 6000, "name": "One Arm", "kind": "reps", "unilateral": True,
           "sets": [{"reps": 10, "weight": 20.0, "side": None, "rest": 45}] * 3}
VITA = {"groupId": 700, "variantId": 7000, "name": "Vita", "kind": "level", "unilateral": False,
        "sets": [{"seconds": 30, "level": 12, "side": None, "rest": 60}]}


class TestBuild(unittest.TestCase):
    def test_lb_account(self):
        body = writes.build_template("Pull", [ROW], unit="lb", device_type=1)
        action = body["actionLibraryList"][0]
        self.assertEqual((action["groupId"], action["actionLibraryId"], action["templatePresetId"]), (321, 3210, -1))
        self.assertEqual((action["setsAndReps"], action["weights"]), ("8,8", "50.0,50.0"))
        self.assertEqual((action["counterweight2"], action["capacity"]), ("", 800.0))
        self.assertEqual(body["totalCapacity"], 800.0)
        self.assertNotIn("id", body)

    def test_kg_account_uses_preset_1_and_scales_total(self):
        body = writes.build_template("Pull", [ROW], unit="kg", device_type=1, template_id=9001)
        self.assertEqual(body["actionLibraryList"][0]["templatePresetId"], 1)
        self.assertEqual(body["totalCapacity"], 1760.0)
        self.assertEqual(body["id"], 9001)

    def test_unilateral_sides_alternate_but_explicit_sides_win(self):
        action = writes.build_template("x", [ONE_ARM], unit="lb", device_type=1)["actionLibraryList"][0]
        self.assertEqual(action["leftRight"], "1,2,1")
        pinned = dict(ONE_ARM, sets=[{"reps": 10, "weight": 20.0, "side": 2, "rest": 45}])
        self.assertEqual(writes.build_template("x", [pinned], unit="lb", device_type=1)["actionLibraryList"][0]["leftRight"], "2")

    def test_vita(self):
        action = writes.build_template("x", [VITA], unit="lb", device_type=1)["actionLibraryList"][0]
        self.assertEqual((action["setsAndReps"], action["level"], action["weights"], action["completionMethod"]),
                         ("30", "12", "0", "2"))


class TestValidate(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(writes.validate_sets("reps", [{"reps": 8, "weight": 50}]),
                         [{"reps": 8, "weight": 50.0, "side": None, "rest": 60}])
        self.assertEqual(writes.validate_sets("timed", [{"seconds": 45, "rest_seconds": 30}]),
                         [{"seconds": 45, "level": None, "side": None, "rest": 30}])

    def test_invalid(self):
        for kind, sets in (("reps", []), ("reps", [{"reps": 0, "weight": 5}]), ("reps", [{"reps": 5, "weight": -1}]),
                           ("timed", [{"reps": 5}]), ("level", [{"seconds": 30}]), ("reps", [{"reps": 5, "side": 3}])):
            with self.assertRaises(ValueError):
                writes.validate_sets(kind, sets)


class TestReadAndVerify(unittest.TestCase):
    def stored(self, body, weight_divisor=1.0):
        return {"actionLibraryList": [{
            "sort": i + 1, "title": f"ex {i + 1}", "actionLibraryId": a["actionLibraryId"],
            "templatePresetId": a["templatePresetId"], "setsAndReps": a["setsAndReps"],
            "weights": ",".join(f"{float(w) / weight_divisor:.1f}" for w in a["weights"].split(",")),
            "level": a["level"], "leftRight": a["leftRight"], "breakTime2": a["breakTime2"]}
            for i, a in enumerate(body["actionLibraryList"])]}

    def test_verified_round_trip(self):
        body = writes.build_template("x", [ROW, ONE_ARM, VITA], unit="lb", device_type=1)
        self.assertEqual(writes.verify(body, self.stored(body)), [])

    def test_shrunken_weights_are_caught(self):
        body = writes.build_template("x", [ROW], unit="lb", device_type=1)
        problems = writes.verify(body, self.stored(body, weight_divisor=2.2))
        self.assertEqual(len(problems), 1)
        self.assertIn("weights", problems[0])

    def test_count_mismatch(self):
        body = writes.build_template("x", [ROW], unit="lb", device_type=1)
        self.assertIn("stored 0", writes.verify(body, {"actionLibraryList": []})[0])

    def test_read_template_and_sets_for_kind(self):
        detail = {"id": 1, "code": "c", "name": "n", "durationMinute": 20, "actionLibraryList": [
            {"sort": 1, "title": "Row", "actionLibraryId": 3210, "templatePresetId": -1, "setsAndReps": "12,10",
             "weights": "30.0,40.0", "level": "0,0", "leftRight": "0,0", "breakTime2": "60,90"}]}
        exercise = writes.read_template(detail)["exercises"][0]
        self.assertEqual(exercise["sets"][1], {"count": 10, "weight": 40.0, "level": 0, "side": None, "rest": 90})
        self.assertEqual(writes.sets_for_kind("reps", exercise["sets"])[0],
                         {"reps": 12, "weight": 30.0, "side": None, "rest": 60})
```

`tests/test_tools_workouts.py`:
```python
from __future__ import annotations

import copy
import json
import unittest

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp.speediance.client import Rejected
from speediance_mcp.tools import workouts
from tests import fixtures as fx
from tests.helpers import api_error, make_app


class TemplateStore:
    """Stateful fake of the template endpoints. `weight_divisor` simulates a server that shrinks loads."""

    def __init__(self, weight_divisor=1.0):
        self.rows = copy.deepcopy(fx.TEMPLATES)
        self.details = {fx.TEMPLATES[0]["code"]: copy.deepcopy(fx.TEMPLATE_9001)}
        self.next_id = 9002
        self.weight_divisor = weight_divisor
        self.posts = []

    def save(self, request):
        body = json.loads(request.content)
        self.posts.append(body)
        if "id" in body:
            row = next(r for r in self.rows if r["id"] == body["id"])
            row["name"] = body["name"]
        else:
            row = {"id": self.next_id, "code": f"{self.next_id:024d}", "name": body["name"],
                   "actionNum": len(body["actionLibraryList"]), "durationMinute": 20}
            self.rows.append(row)
            self.next_id += 1
        self.details[row["code"]] = {"id": row["id"], "code": row["code"], "name": body["name"], "actionLibraryList": [
            {"sort": i + 1, "title": f"ex {i + 1}", "actionLibraryId": a["actionLibraryId"],
             "templatePresetId": a["templatePresetId"], "setsAndReps": a["setsAndReps"],
             "weights": ",".join(f"{float(w) / self.weight_divisor:.1f}" for w in a["weights"].split(",")),
             "level": a["level"], "leftRight": a["leftRight"], "breakTime2": a["breakTime2"]}
            for i, a in enumerate(body["actionLibraryList"])]}
        return True

    def delete(self, request):
        target = int(request.url.params["ids"])
        self.rows = [r for r in self.rows if r["id"] != target]
        return True

    def routes(self):
        table = fx.standard_routes()
        table[("GET", fx.TEMPLATES_PATH)] = lambda req: self.rows
        table[("GET", fx.TEMPLATE_DETAIL_PATH)] = lambda req: self.details.get(req.url.params.get("code"))
        table[("POST", fx.SAVE_TEMPLATE_PATH)] = self.save
        table[("DELETE", fx.DELETE_TEMPLATE_PATH)] = self.delete
        return table


PUSH = [{"name": "bent over row", "sets": [{"reps": 8, "weight": 50}, {"reps": 8, "weight": 50}]},
        {"group_id": 600, "sets": [{"reps": 10, "weight": 20}, {"reps": 10, "weight": 20}]},
        {"name": "vita row", "sets": [{"seconds": 30, "level": 12}]}]


class TestWorkoutTools(unittest.TestCase):
    def test_list_and_get(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        listed = workouts.list_my_workouts(app)
        self.assertEqual(listed["slots"], {"used": 1, "limit": None, "left": None})
        got = workouts.get_workout(app, "a" * 24)
        self.assertEqual(got["exercises"][0]["kind"], "reps")
        self.assertEqual(got["exercises"][0]["sets"][0], {"reps": 12, "weight": 30.0, "side": None, "rest": 60})
        with self.assertRaises(ToolError):
            workouts.get_workout(app, "nope")

    def test_create_verified(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        got = workouts.create_workout(app, "Push", PUSH)
        self.assertTrue(got["verified"])
        self.assertEqual((got["id"], got["exercises"]), (9002, 3))
        actions = store.posts[0]["actionLibraryList"]
        self.assertEqual((actions[0]["actionLibraryId"], actions[0]["weights"]), (3210, "50.0,50.0"))
        self.assertEqual(actions[1]["leftRight"], "1,2")
        self.assertEqual(actions[2]["level"], "12")

    def test_create_flags_shrunken_weights(self):
        store = TemplateStore(weight_divisor=2.2)
        app, _ = make_app(self, store.routes())
        got = workouts.create_workout(app, "Push", PUSH[:1])
        self.assertFalse(got["verified"])
        self.assertIn("weights", got["mismatches"][0])
        self.assertIn("warning", got)

    def test_ambiguous_name_writes_nothing(self):
        store = TemplateStore()
        app, fake = make_app(self, store.routes())
        with self.assertRaises(ToolError):
            workouts.create_workout(app, "Push", [{"name": "row", "sets": [{"reps": 8, "weight": 50}]}])
        self.assertEqual(fake.calls("POST", fx.SAVE_TEMPLATE_PATH), [])

    def test_bad_sets_write_nothing(self):
        store = TemplateStore()
        app, fake = make_app(self, store.routes())
        with self.assertRaises(ToolError):
            workouts.create_workout(app, "Push", [{"name": "vita row", "sets": [{"seconds": 30}]}])
        self.assertEqual(fake.calls("POST", fx.SAVE_TEMPLATE_PATH), [])

    def test_server_rejection_passes_through(self):
        store = TemplateStore()
        routes = store.routes()
        routes[("POST", fx.SAVE_TEMPLATE_PATH)] = api_error(5001, "Template limit reached")
        app, _ = make_app(self, routes)
        with self.assertRaises(Rejected) as cm:
            workouts.create_workout(app, "Push", PUSH[:1])
        self.assertIn("Template limit reached", str(cm.exception))

    def test_rename_rebuilds_from_stored_detail(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        got = workouts.update_workout(app, "a" * 24, name="Pull Day v2")
        self.assertTrue(got["verified"])
        body = store.posts[0]
        self.assertEqual((body["id"], body["name"]), (9001, "Pull Day v2"))
        self.assertEqual(body["actionLibraryList"][0]["setsAndReps"], "12,10")

    def test_update_with_new_exercises(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        workouts.update_workout(app, "a" * 24, exercises=PUSH[:1])
        self.assertEqual(store.posts[0]["name"], "Pull Day")
        self.assertEqual(store.posts[0]["actionLibraryList"][0]["setsAndReps"], "8,8")

    def test_delete(self):
        store = TemplateStore()
        app, fake = make_app(self, store.routes())
        self.assertEqual(workouts.delete_workout(app, "a" * 24), {"deleted": True, "code": "a" * 24, "name": "Pull Day"})
        self.assertEqual(fake.calls("DELETE", fx.DELETE_TEMPLATE_PATH)[0].url.params["ids"], "9001")
        with self.assertRaises(ToolError):
            workouts.delete_workout(app, "a" * 24)

    def test_numeric_id_handle(self):
        store = TemplateStore()
        app, _ = make_app(self, store.routes())
        self.assertEqual(workouts.delete_workout(app, 9001)["code"], "a" * 24)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_writes tests.test_tools_workouts -v`
Expected: FAIL — `ImportError: cannot import name 'writes'`.

- [ ] **Step 3: Implement**

`src/speediance_mcp/speediance/writes.py`:
```python
"""Build, read back and verify custom-template bodies (spec §8.3). Pure.

Write-fault rules: totalCapacity is never null; templatePresetId and totalCapacity depend on the
account unit (lb: -1 and the raw sum — verified live; kg: 1 and x2.2 — per pookey/speediance-cli);
unilateral movements auto-alternate sides; counterweight2 is always empty; every write is verified
by reading the template back.
"""

from __future__ import annotations

KG_LB_SCALE = 2.2


def _int(value, default=0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _csv(value) -> list[str]:
    text = str(value or "").strip()
    return [part.strip() for part in text.split(",")] if text else []


def validate_sets(kind: str, sets, default_rest: int = 60) -> list[dict]:
    if not isinstance(sets, list) or not sets:
        raise ValueError("needs at least one set")
    if len(sets) > 20:
        raise ValueError("at most 20 sets per exercise")
    out = []
    for number, raw in enumerate(sets, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"set {number} must be an object")
        side = raw.get("side")
        if side not in (None, 0, 1, 2):
            raise ValueError(f"set {number}: side must be 1 (left) or 2 (right)")
        rest = _int(raw.get("rest", raw.get("rest_seconds", default_rest)), default_rest)
        if not 0 <= rest <= 600:
            raise ValueError(f"set {number}: rest must be 0-600 seconds")
        if kind == "reps":
            reps = _int(raw.get("reps"))
            weight = _float(raw.get("weight")) or 0.0
            if not 1 <= reps <= 100:
                raise ValueError(f"set {number}: reps must be 1-100")
            if weight < 0:
                raise ValueError(f"set {number}: weight can't be negative")
            out.append({"reps": reps, "weight": weight, "side": side or None, "rest": rest})
        else:
            seconds = _int(raw.get("seconds"))
            if not 1 <= seconds <= 3600:
                raise ValueError(f"set {number}: this movement is timed, so give seconds (1-3600)")
            level = None
            if kind == "level":
                level = _int(raw.get("level"))
                if level < 1:
                    raise ValueError(f"set {number}: Vita movements need a level of 1 or more")
            out.append({"seconds": seconds, "level": level, "side": side or None, "rest": rest})
    return out


def _side(spec: dict, raw: dict, index: int) -> str:
    if raw.get("side"):
        return str(raw["side"])
    if spec["unilateral"]:
        return "1" if index % 2 == 0 else "2"
    return "0"


def build_template(name: str, specs: list[dict], *, unit: str, device_type: int, template_id=None) -> dict:
    preset = -1 if unit == "lb" else 1
    actions, total = [], 0.0
    for spec in specs:
        sets, kind = spec["sets"], spec["kind"]
        timed = kind != "reps"
        capacity = 0.0 if timed else sum(s["reps"] * s["weight"] for s in sets)
        total += capacity
        rests = ",".join(str(s["rest"]) for s in sets)
        actions.append({
            "groupId": int(spec["groupId"]),
            "actionLibraryId": int(spec["variantId"]),
            "templatePresetId": preset,
            "setsAndReps": ",".join(str(s["seconds"] if timed else s["reps"]) for s in sets),
            "breakTime": rests,
            "breakTime2": rests,
            "sportMode": ",".join("1" for _ in sets),
            "leftRight": ",".join(_side(spec, s, i) for i, s in enumerate(sets)),
            "selectCompletionMethod": ",".join("1" for _ in sets),
            "completionMethod": ",".join(("2" if timed else "1") for _ in sets),
            "countType": ",".join(("2" if timed else "1") for _ in sets),
            "weights": ",".join(("0" if timed else f"{s['weight']:.1f}") for s in sets),
            "counterweight2": "",
            "counterweight": "",
            "level": ",".join((str(s["level"]) if kind == "level" else "0") for s in sets),
            "capacity": round(capacity, 1),
        })
    body = {"name": name, "actionLibraryList": actions,
            "totalCapacity": round(total * (KG_LB_SCALE if unit == "kg" else 1.0), 1),
            "deviceType": int(device_type), "bgColor": 0}
    if template_id is not None:
        body["id"] = int(template_id)
    return body


def read_template(detail: dict) -> dict:
    actions = sorted((detail or {}).get("actionLibraryList") or [], key=lambda a: a.get("sort") or 0)
    exercises = []
    for action in actions:
        counts, weights = _csv(action.get("setsAndReps")), _csv(action.get("weights"))
        levels, sides = _csv(action.get("level")), _csv(action.get("leftRight"))
        rests = _csv(action.get("breakTime2") or action.get("breakTime"))
        sets = []
        for i, count in enumerate(counts):
            side = _int(sides[i]) if i < len(sides) else 0
            sets.append({"count": _int(count),
                         "weight": _float(weights[i]) if i < len(weights) else None,
                         "level": _int(levels[i]) if i < len(levels) else 0,
                         "side": side if side in (1, 2) else None,
                         "rest": _int(rests[i], None) if i < len(rests) else None})
        exercises.append({"name": action.get("title") or "", "actionLibraryId": action.get("actionLibraryId"),
                          "presetId": action.get("templatePresetId"), "sets": sets})
    return {"id": detail.get("id"), "code": detail.get("code"), "name": detail.get("name"),
            "durationMinute": detail.get("durationMinute"), "exercises": exercises}


def sets_for_kind(kind: str, stored_sets: list[dict], default_rest: int = 60) -> list[dict]:
    out = []
    for s in stored_sets:
        rest = s["rest"] if s["rest"] is not None else default_rest
        if kind == "reps":
            out.append({"reps": s["count"], "weight": s["weight"] or 0.0, "side": s["side"], "rest": rest})
        else:
            out.append({"seconds": s["count"], "level": (s["level"] or None) if kind == "level" else None,
                        "side": s["side"], "rest": rest})
    return out


def verify(body: dict, stored: dict | None) -> list[str]:
    """Compare what was sent with what Speediance stored. An empty list means verified."""
    sent = body["actionLibraryList"]
    got = sorted((stored or {}).get("actionLibraryList") or [], key=lambda a: a.get("sort") or 0)
    if len(sent) != len(got):
        return [f"sent {len(sent)} exercises, Speediance stored {len(got)}"]
    problems = []
    for number, (s, g) in enumerate(zip(sent, got), 1):
        label = g.get("title") or f"exercise {number}"
        if _csv(s["setsAndReps"]) != _csv(g.get("setsAndReps")):
            problems.append(f"{label}: reps/seconds sent {s['setsAndReps']} but stored {g.get('setsAndReps')}")
        sent_w = [_float(x) or 0.0 for x in _csv(s["weights"])]
        got_w = [_float(x) or 0.0 for x in _csv(g.get("weights"))]
        if got_w or any(sent_w):
            if len(sent_w) != len(got_w) or any(abs(a - b) > 0.05 for a, b in zip(sent_w, got_w)):
                problems.append(f"{label}: weights sent {s['weights']} but stored {g.get('weights')}")
        if _csv(g.get("leftRight")) and _csv(s["leftRight"]) != _csv(g.get("leftRight")):
            problems.append(f"{label}: sides sent {s['leftRight']} but stored {g.get('leftRight')}")
        if _csv(g.get("level")) and [_int(x) for x in _csv(s["level"])] != [_int(x) for x in _csv(g.get("level"))]:
            problems.append(f"{label}: levels sent {s['level']} but stored {g.get('level')}")
    return problems
```

`src/speediance_mcp/tools/workouts.py`:
```python
from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from ..library import accessory_names, summarize_exercise, variant_id
from ..speediance.writes import build_template, read_template, sets_for_kind, validate_sets, verify
from ._common import resolve_group

MAX_NAME = 60


def _row_by_code(app, handle) -> dict:
    """A template by its `code` (preferred — it survives edits) or its current numeric id."""
    key = str(handle if handle is not None else "").strip()
    for row in app.api.templates():
        if row.get("code") == key or str(row.get("id")) == key:
            return row
    raise ToolError(f"No workout template {key!r}. Call list_my_workouts for current codes.")


def _by_variant(app) -> dict:
    out = {}
    for raw in app.api.library():
        for variant in raw.get("actionLibraryList") or []:
            out[variant.get("id")] = raw
    return out


def list_my_workouts(app) -> dict:
    """The user's saved custom workout templates. Use a template's `code` with get_workout,
    update_workout, delete_workout or schedule_workout."""
    rows = app.api.templates()
    return {"workouts": [{"id": r.get("id"), "code": r.get("code"), "name": r.get("name"),
                          "exercises": r.get("actionNum"), "durationMinute": r.get("durationMinute")} for r in rows],
            "slots": {"used": len(rows), "limit": None, "left": None},
            "note": "Speediance caps how many custom workouts an account holds, but no known endpoint reports the "
                    "cap. If a create is rejected for being over it, update or delete an existing template — "
                    "never delete one without asking the user."}


def get_workout(app, code: str) -> dict:
    """A template's full prescription: exercises in order, each set's reps (or seconds), weight,
    Vita level, side and rest. Weights are in displayUnit."""
    row = _row_by_code(app, code)
    detail = app.api.template(row["code"])
    if not detail:
        raise ToolError(f"Speediance returned no detail for template {code!r}.")
    workout = read_template(detail)
    variants = _by_variant(app)
    for exercise in workout["exercises"]:
        raw = variants.get(exercise["actionLibraryId"])
        if raw:
            exercise["kind"] = summarize_exercise(raw)["kind"]
            exercise["sets"] = sets_for_kind(exercise["kind"], exercise["sets"])
    return {**workout, "displayUnit": app.api.unit}


def _resolve_specs(app, exercises) -> list[dict]:
    if not isinstance(exercises, list) or not exercises:
        raise ToolError("exercises must be a non-empty list.")
    raw_by_id = {raw.get("id"): raw for raw in app.api.library()}
    specs = []
    for number, exercise in enumerate(exercises, 1):
        if not isinstance(exercise, dict):
            raise ToolError(f"exercises[{number}] must be an object.")
        item = resolve_group(app, str(exercise.get("name") or ""), int(exercise.get("group_id") or exercise.get("groupId") or 0))
        vid = variant_id(raw_by_id.get(item["groupId"]) or {})
        if vid is None:
            raise ToolError(f"{item['name']} has no playable variant in the library.")
        try:
            sets = validate_sets(item["kind"], exercise.get("sets"), int(exercise.get("rest_seconds") or 60))
        except ValueError as exc:
            raise ToolError(f"{item['name']}: {exc}.") from None
        specs.append({"groupId": item["groupId"], "variantId": vid, "name": item["name"], "kind": item["kind"],
                      "unilateral": item["unilateral"], "sets": sets})
    return specs


def _specs_from_stored(app, detail: dict) -> list[dict]:
    variants = _by_variant(app)
    names = accessory_names(app.api.accessories())
    specs = []
    for exercise in read_template(detail)["exercises"]:
        raw = variants.get(exercise["actionLibraryId"])
        if raw is None:
            raise ToolError(f"Can't rebuild '{exercise['name']}' — it's no longer in the library. "
                            "Pass the full exercises list instead.")
        item = summarize_exercise(raw, names)
        specs.append({"groupId": item["groupId"], "variantId": exercise["actionLibraryId"], "name": item["name"],
                      "kind": item["kind"], "unilateral": item["unilateral"],
                      "sets": sets_for_kind(item["kind"], exercise["sets"])})
    return specs


def _verified(app, body: dict, row: dict) -> dict:
    mismatches = verify(body, app.api.template(row["code"]))
    out = {"id": row.get("id"), "code": row.get("code"), "name": row.get("name"),
           "verified": not mismatches, "exercises": len(body["actionLibraryList"])}
    if mismatches:
        out["mismatches"] = mismatches
        out["warning"] = "The saved template doesn't match what was sent — check it in the Speediance app before training."
    return out


def _clean_name(name) -> str:
    name = str(name or "").strip()
    if not name or len(name) > MAX_NAME:
        raise ToolError(f"name must be 1-{MAX_NAME} characters.")
    return name


def create_workout(app, name: str, exercises: list[dict]) -> dict:
    """Create a custom workout template. `exercises` is an ordered list of
    {"name": "..." or "group_id": N, "sets": [...], "rest_seconds": 60}. Sets by movement kind:
    reps -> {"reps": 10, "weight": 50}; timed -> {"seconds": 45}; Vita (level) -> {"seconds": 30, "level": 12}.
    Optional per set: "side" 1=left / 2=right (unilateral moves alternate automatically), "rest".
    Weights are in displayUnit. The template is read back after saving; verified:false means Speediance
    stored something different — tell the user. Never program a ⊘avoided movement unless asked by name."""
    name = _clean_name(name)
    specs = _resolve_specs(app, exercises)
    before = {r.get("id") for r in app.api.templates()}
    body = build_template(name, specs, unit=app.api.unit, device_type=app.api.device_type)
    app.api.save_template(body)
    created = [r for r in app.api.templates() if r.get("id") not in before and r.get("name") == name]
    if not created:
        raise ToolError("Speediance accepted the workout but it isn't in your list yet — check list_my_workouts "
                        "before retrying, to avoid a duplicate.")
    return _verified(app, body, max(created, key=lambda r: r.get("id") or 0))


def update_workout(app, template_id: str | int, name: str | None = None, exercises: list[dict] | None = None) -> dict:
    """Edit a template in place (an edit never uses a new slot). `template_id` is its `code` (preferred)
    or numeric id. Omitted fields keep their current value; `exercises`, when given, replaces the whole
    list (same format as create_workout) — start from get_workout. Verified by read-back."""
    row = _row_by_code(app, template_id)
    if exercises is None:
        detail = app.api.template(row["code"])
        if not detail:
            raise ToolError(f"Speediance returned no detail for template {template_id!r}.")
        specs = _specs_from_stored(app, detail)
    else:
        specs = _resolve_specs(app, exercises)
    new_name = _clean_name(name if name is not None else row.get("name"))
    body = build_template(new_name, specs, unit=app.api.unit, device_type=app.api.device_type, template_id=row["id"])
    app.api.save_template(body)
    refreshed = next((r for r in app.api.templates() if r.get("id") == row["id"]), {**row, "name": new_name})
    return _verified(app, body, refreshed)


def delete_workout(app, template_id: str | int) -> dict:
    """Permanently delete a custom template, by `code` (preferred) or numeric id. Not reversible —
    only when the user asked for it."""
    row = _row_by_code(app, template_id)
    app.api.delete_template(row["id"])
    return {"deleted": True, "code": row["code"], "name": row.get("name")}
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_writes tests.test_tools_workouts -v`
Expected: 19 tests OK.

- [ ] **Step 5: Commit**

```bash
git add src/speediance_mcp/speediance/writes.py src/speediance_mcp/tools/workouts.py tests/test_writes.py tests/test_tools_workouts.py
git commit -m "feat: template writes with read-back verification and workout tools"
```

---

### Task 12: Calendar, program and memory tools

**Files:**
- Create: `src/speediance_mcp/tools/calendar.py`, `src/speediance_mcp/tools/memory_tools.py`
- Test: `tests/test_tools_calendar_memory.py`

**Interfaces:**
- Consumes: `parse_date`, `workouts._row_by_code`, `App.memory`.
- Produces: `calendar.schedule_workout(app, date, code, add=True)`, `calendar.unschedule_workout(app, date, code)`, `calendar.browse_programs(app, query="", program_id=0)`; `memory_tools.get_preferences(app)`, `memory_tools.set_preferences(app, goal=None, training_days=None, session_minutes=None, load_anchors=None, owned_equipment=None)`, `memory_tools.remember_fact(app, text, category="note", expires_days=None)`, `memory_tools.forget_fact(app, memory_id)`.

- [ ] **Step 1: Write the failing test** — `tests/test_tools_calendar_memory.py`

```python
from __future__ import annotations

import json
import unittest

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp.tools import calendar, memory_tools
from tests import fixtures as fx
from tests.helpers import make_app


class TestCalendarTools(unittest.TestCase):
    def test_schedule_and_unschedule(self):
        app, fake = make_app(self)
        got = calendar.schedule_workout(app, "2026-09-01", "a" * 24)
        self.assertEqual((got["scheduled"], got["name"]), (True, "Pull Day"))
        calendar.unschedule_workout(app, "2026-09-01", "a" * 24)
        self.assertTrue(calendar.schedule_workout(app, "2026-09-02", "a" * 24, add=False)["unscheduled"])
        bodies = [json.loads(r.content) for r in fake.calls("POST", fx.RESERVE_PATH)]
        self.assertEqual([b["status"] for b in bodies], [1, 0, 0])
        self.assertEqual(bodies[0]["thatDay"], "2026-09-01")

    def test_validation_writes_nothing(self):
        app, fake = make_app(self)
        with self.assertRaises(ToolError):
            calendar.schedule_workout(app, "2026-02-30", "a" * 24)
        with self.assertRaises(ToolError):
            calendar.schedule_workout(app, "2026-09-01", "unknown")
        self.assertEqual(fake.calls("POST", fx.RESERVE_PATH), [])

    def test_programs(self):
        app, _ = make_app(self)
        self.assertEqual(calendar.browse_programs(app, query="strength")["count"], 1)
        self.assertEqual(calendar.browse_programs(app, query="yoga")["count"], 0)
        detail = calendar.browse_programs(app, program_id=77)
        self.assertEqual(detail["program"]["name"], "Strength Foundations")
        self.assertEqual(detail["structure"]["weekList"], ["Week 1", "Week 2"])


class TestMemoryTools(unittest.TestCase):
    def test_round_trip(self):
        app, _ = make_app(self)
        memory_tools.set_preferences(app, goal="Strength", owned_equipment=["Barbell"], load_anchors={"321": 45})
        fact = memory_tools.remember_fact(app, "Knee pain on lunges", category="injury", expires_days=14)
        got = memory_tools.get_preferences(app)
        self.assertEqual((got["goal"], got["owned_equipment"], got["load_anchors"]), ("Strength", ["Barbell"], {"321": 45.0}))
        self.assertEqual((got["facts"][0]["fact"], got["facts"][0]["category"]), ("Knee pain on lunges", "injury"))
        self.assertEqual(memory_tools.forget_fact(app, fact["id"]), {"forgotten": True, "id": fact["id"]})

    def test_errors_are_tool_errors(self):
        app, _ = make_app(self)
        with self.assertRaises(ToolError):
            memory_tools.set_preferences(app, session_minutes=0)
        with self.assertRaises(ToolError):
            memory_tools.remember_fact(app, "  ")
        with self.assertRaises(ToolError):
            memory_tools.forget_fact(app, 404)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_tools_calendar_memory -v`
Expected: FAIL — `ImportError: cannot import name 'calendar' from 'speediance_mcp.tools'`.

- [ ] **Step 3: Implement**

`src/speediance_mcp/tools/calendar.py`:
```python
from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from ._common import parse_date
from .workouts import _row_by_code


def _reserve(app, date: str, code: str, status: int) -> dict:
    date = parse_date(date)
    row = _row_by_code(app, code)
    app.api.reserve(date, row["code"], status)
    return {"date": date, "code": row["code"], "name": row.get("name")}


def schedule_workout(app, date: str, code: str, add: bool = True) -> dict:
    """Put a saved template (by `code`, from list_my_workouts) on a day (YYYY-MM-DD), or take it off
    with add=false."""
    if not add:
        return unschedule_workout(app, date, code)
    return {"scheduled": True, **_reserve(app, date, code, 1)}


def unschedule_workout(app, date: str, code: str) -> dict:
    """Take a scheduled template off a day (YYYY-MM-DD)."""
    return {"unscheduled": True, **_reserve(app, date, code, 0)}


def _scalars(record: dict) -> dict:
    return {k: v for k, v in record.items() if not isinstance(v, (dict, list)) and v not in (None, "")}


def _names(items: list) -> list:
    return [i.get("name") or i.get("title") or i.get("id") for i in items[:50] if isinstance(i, dict)]


def browse_programs(app, query: str = "", program_id: int = 0) -> dict:
    """Speediance's official multi-week programs. Without program_id, lists programs (filtered by
    `query`); with program_id, returns that program's details and structure."""
    if program_id:
        program = app.api.program(program_id)
        if not program:
            raise ToolError(f"No program with id {program_id}.")
        return {"program": _scalars(program),
                "structure": {k: _names(v) for k, v in program.items() if isinstance(v, list)}}
    needle = str(query or "").lower().strip()
    rows = []
    for p in app.api.programs():
        name, description = str(p.get("name") or ""), str(p.get("description") or "")
        if needle and needle not in name.lower() and needle not in description.lower():
            continue
        rows.append({"id": p.get("id"), "name": name, "description": description[:240],
                     "weeks": p.get("weekCount"), "sessionsPerWeek": p.get("weekTrainingFrequency"),
                     "difficulty": p.get("difficultyId"), "available": p.get("isPermission")})
    return {"count": len(rows), "programs": rows[:50]}
```

`src/speediance_mcp/tools/memory_tools.py`:
```python
from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError


def get_preferences(app) -> dict:
    """The coaching memory: goal, training days, session length, load anchors, owned equipment,
    active facts, and ★preferred / ⊘avoided exercises. Respect it when planning."""
    memory = app.memory
    marks = memory.marks()
    return {**memory.preferences(), "facts": memory.facts(),
            "preferredExercises": [{"groupId": g, "name": m["name"]} for g, m in marks.items() if m["mark"] == "preferred"],
            "avoidedExercises": [{"groupId": g, "name": m["name"]} for g, m in marks.items() if m["mark"] == "avoided"]}


def set_preferences(app, goal: str | None = None, training_days: list[str] | None = None,
                    session_minutes: int | None = None, load_anchors: dict[str, float] | None = None,
                    owned_equipment: list[str] | None = None) -> dict:
    """Update structured preferences (only the fields given change). load_anchors maps group_id -> a
    known working weight in displayUnit; owned_equipment is a list of accessory names from
    list_accessories. Free-text facts go in remember_fact instead."""
    fields = {"goal": goal, "training_days": training_days, "session_minutes": session_minutes,
              "load_anchors": load_anchors, "owned_equipment": owned_equipment}
    try:
        return {"preferences": app.memory.set_preferences(**{k: v for k, v in fields.items() if v is not None})}
    except ValueError as exc:
        raise ToolError(str(exc)) from None


def remember_fact(app, text: str, category: str = "note", expires_days: int | None = None) -> dict:
    """Save ONE training fact the user states — and tell them you saved it. category: goal | schedule |
    injury | preference | dislike | equipment | body | note. Leave expires_days empty for durable facts;
    set it only for temporary things ("travelling next week" ~7, a tweak ~10-14) so they clear
    themselves. Don't store bodyweight/unit (Speediance knows) or load numbers (use set_preferences)."""
    try:
        return {"saved": True, **app.memory.remember_fact(text, int(expires_days or 0), category)}
    except ValueError as exc:
        raise ToolError(str(exc)) from None


def forget_fact(app, memory_id: int) -> dict:
    """Remove a saved fact that no longer applies (ids come from get_preferences `facts`)."""
    if not app.memory.forget_fact(int(memory_id)):
        raise ToolError(f"No saved fact with id {memory_id}.")
    return {"forgotten": True, "id": int(memory_id)}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_tools_calendar_memory -v`
Expected: 5 tests OK.

- [ ] **Step 5: Commit**

```bash
git add src/speediance_mcp/tools/calendar.py src/speediance_mcp/tools/memory_tools.py tests/test_tools_calendar_memory.py
git commit -m "feat: scheduling, program browsing and coaching-memory tools"
```

---

### Task 13: MCP server and CLI

**Files:**
- Create: `src/speediance_mcp/tools/errors.py`, `src/speediance_mcp/server.py`, `src/speediance_mcp/cli.py`, `src/speediance_mcp/__main__.py`
- Modify: `.github/workflows/ci.yml` (add smoke steps)
- Test: `tests/test_server_cli.py`

**Interfaces:**
- Consumes: every tool function; `App`; `SpeedianceClient`; `SpeedianceAPI`; credentials functions.
- Produces (`tools.errors`): `translate(exc, app) -> ToolError`.
- Produces (`server`): `INSTRUCTIONS: str`; `TOOLS: tuple` (the 26 functions); `bind(fn, app) -> Callable`; `build_server(app) -> MCPServer`.
- Produces (`cli`): `main(argv=None) -> int`; `_make_client(creds, region) -> SpeedianceClient` (patch point for tests).

- [ ] **Step 1: Write the failing test** — `tests/test_server_cli.py`

```python
from __future__ import annotations

import asyncio
import contextlib
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp.server.mcpserver.exceptions import ToolError

from speediance_mcp import cli
from speediance_mcp.config import load_credentials, save_credentials
from speediance_mcp.server import build_server
from speediance_mcp.speediance.client import SpeedianceClient
from tests import fixtures as fx
from tests.helpers import CREDS, FakeSpeediance, api_error, make_app

EXPECTED_TOOLS = {
    "check_connection", "get_calendar", "get_session_detail", "get_exercise_history", "get_athlete_snapshot",
    "get_strength_profile", "list_exercises", "mark_exercise", "list_my_workouts", "get_workout",
    "create_workout", "update_workout", "delete_workout", "schedule_workout", "suggest_load",
    "get_preferences", "set_preferences", "remember_fact", "forget_fact",
    "unschedule_workout", "get_training_stats", "compare_sessions", "browse_programs", "get_exercise",
    "list_accessories", "get_heart_rate",
}


class TestServer(unittest.TestCase):
    def test_exactly_the_26_tools_with_descriptions(self):
        app, _ = make_app(self)
        tools = asyncio.run(build_server(app).list_tools())
        self.assertEqual({t.name for t in tools}, EXPECTED_TOOLS)
        self.assertTrue(all(t.description for t in tools))
        detail = next(t for t in tools if t.name == "get_session_detail")
        schema = detail.input_schema if hasattr(detail, "input_schema") else detail.inputSchema
        self.assertEqual(set(schema["properties"]), {"training_id", "type"})

    def test_calls_route_through_the_app(self):
        app, _ = make_app(self)
        result = asyncio.run(build_server(app).call_tool("get_training_stats", {"start": "2026-08-01", "end": "2026-08-31"}))
        self.assertIn('"sessions": 4', result.content[0].text)

    def test_expired_session_mid_conversation_says_how_to_fix(self):
        routes = fx.standard_routes()
        routes[("GET", fx.HISTORY_PATH)] = api_error(91, "Login expired")
        app, _ = make_app(self, routes)
        with self.assertRaises(ToolError) as cm:
            asyncio.run(build_server(app).call_tool("get_calendar", {"month": "2026-08"}))
        self.assertIn("speediance-mcp login", str(cm.exception))

    def test_remote_mode_hint(self):
        routes = fx.standard_routes()
        routes[("GET", fx.HISTORY_PATH)] = api_error(91, "Login expired")
        app, _ = make_app(self, routes, mode="remote")
        with self.assertRaises(ToolError) as cm:
            asyncio.run(build_server(app).call_tool("get_calendar", {"month": "2026-08"}))
        self.assertIn("Reconnect", str(cm.exception))


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        patcher = mock.patch.dict(os.environ, {"SPEEDIANCE_MCP_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def fake_client_factory(self, routes):
        fake = FakeSpeediance(routes)

        def factory(creds, region):
            return SpeedianceClient(creds, region=region, transport=fake.transport(), min_interval=0)
        return factory, fake

    def test_status_logged_out_and_in(self):
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 1)
        self.assertIn("Not logged in", out)
        save_credentials(CREDS, self.home)
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("athlete@example.com", out)
        self.assertIn("lb", out)
        self.assertNotIn("tok-1", out)

    def test_login_saves_credentials_without_password_and_warms_library(self):
        routes = fx.standard_routes()
        routes[("POST", "/api/app/v2/login/verifyIdentity")] = {"isExist": True, "hasPwd": True}
        routes[("POST", "/api/app/v2/login/byPass")] = {"token": "new-token", "appUserId": 1001, "unit": 1}
        factory, fake = self.fake_client_factory(routes)
        with mock.patch.object(cli, "_make_client", factory), \
                mock.patch("getpass.getpass", return_value="pw"):
            code, out, _ = self.run_cli("login", "--email", "athlete@example.com", "--no-remember")
        self.assertEqual(code, 0)
        creds = load_credentials(self.home)
        self.assertEqual((creds.token, creds.unit, creds.password), ("new-token", "lb", None))
        self.assertIn("Cached 7 exercises", out)
        self.assertNotIn("new-token", out)

    def test_login_failure(self):
        routes = {("POST", "/api/app/v2/login/verifyIdentity"): {"isExist": False}}
        factory, _ = self.fake_client_factory(routes)
        with mock.patch.object(cli, "_make_client", factory), mock.patch("getpass.getpass", return_value="pw"):
            code, _, err = self.run_cli("login", "--email", "nobody@example.com", "--no-remember")
        self.assertEqual(code, 1)
        self.assertIn("Login failed", err)
        self.assertIsNone(load_credentials(self.home))

    def test_logout(self):
        save_credentials(CREDS, self.home)
        factory, fake = self.fake_client_factory({("POST", "/api/app/login/logout"): True})
        with mock.patch.object(cli, "_make_client", factory):
            code, out, _ = self.run_cli("logout")
        self.assertEqual(code, 0)
        self.assertIsNone(load_credentials(self.home))
        self.assertEqual(len(fake.calls("POST", "/api/app/login/logout")), 1)

    def test_http_mode_not_yet_available(self):
        code, _, err = self.run_cli("serve", "--http")
        self.assertEqual(code, 2)
        self.assertIn("isn't available yet", err)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_server_cli -v`
Expected: FAIL — `ImportError: cannot import name 'cli'`.

- [ ] **Step 3: Implement**

`src/speediance_mcp/tools/errors.py`:
```python
"""Turn any failure into a friendly MCP tool error (never a stack trace)."""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from ..speediance.client import AuthExpired, LoginFailed, SpeedianceError


def translate(exc: BaseException, app) -> ToolError:
    if isinstance(exc, ToolError):
        return exc
    if isinstance(exc, (AuthExpired, LoginFailed)):
        return ToolError(app.login_hint)
    if isinstance(exc, SpeedianceError):
        return ToolError(str(exc) or "The Speediance request failed.")
    if isinstance(exc, ValueError):
        return ToolError(str(exc))
    return ToolError(f"Unexpected error ({type(exc).__name__}): {exc}")
```

`src/speediance_mcp/server.py`:
```python
"""The MCP server: 26 tools over one Speediance account."""

from __future__ import annotations

import functools
import inspect

from mcp.server.mcpserver import MCPServer

from . import __version__
from .tools import account, calendar, coaching, exercises, memory_tools, sessions, workouts
from .tools.errors import translate

INSTRUCTIONS = """\
Speediance MCP (unofficial) manages the user's Speediance Gym Monster training from their real data.

Start every session with check_connection; if it reports connected:false, relay its message and stop.

COACHING MEMORY — facts about the user's training (injuries, dislikes, schedule, goals) that you read and write:
- Before planning, creating or advising on ANY workout, check it and respect it. It rides along in
  get_athlete_snapshot, so you usually don't need get_preferences separately.
- When the user states a durable fact (goal, schedule constraint, injury, dislike, equipment), save it with
  remember_fact and tell them. Use expires_days only for temporary things. Don't store what Speediance
  already knows (bodyweight, unit) or load numbers (those go in set_preferences(load_anchors)).
- Remove facts that no longer apply with forget_fact.

EXERCISE MARKS — ★preferred and ⊘avoided movements:
- Lean toward preferred ones. NEVER put an avoided movement in a workout unless the user asks for it by name.
  list_exercises already hides avoided ones.
- When the user makes a lasting per-exercise preference clear, record it with mark_exercise.

TEMPLATES — accounts hold a limited number of custom workouts. Prefer update_workout over creating new
ones, and never delete a template to make room without asking. After create/update, check `verified`.

UNITS — every weight is already in the account's displayUnit. Never convert.
"""

TOOLS = (
    account.check_connection,
    sessions.get_calendar, sessions.get_session_detail, sessions.get_heart_rate, sessions.get_training_stats,
    coaching.get_athlete_snapshot, coaching.get_strength_profile, coaching.compare_sessions, coaching.suggest_load,
    exercises.list_exercises, exercises.get_exercise, exercises.mark_exercise, exercises.list_accessories,
    exercises.get_exercise_history,
    workouts.list_my_workouts, workouts.get_workout, workouts.create_workout, workouts.update_workout,
    workouts.delete_workout,
    calendar.schedule_workout, calendar.unschedule_workout, calendar.browse_programs,
    memory_tools.get_preferences, memory_tools.set_preferences, memory_tools.remember_fact, memory_tools.forget_fact,
)


def bind(fn, app):
    """Expose `fn(app, **kwargs)` as a tool without its `app` parameter; translate errors."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(**kwargs):
        try:
            return fn(app, **kwargs)
        except Exception as exc:
            raise translate(exc, app) from exc

    wrapper.__signature__ = signature.replace(parameters=list(signature.parameters.values())[1:])
    wrapper.__annotations__ = {k: v for k, v in fn.__annotations__.items() if k != "app"}
    del wrapper.__wrapped__
    return wrapper


def build_server(app) -> MCPServer:
    server = MCPServer(name="speediance", instructions=INSTRUCTIONS, version=__version__)
    for fn in TOOLS:
        server.tool()(bind(fn, app))
    return server
```

`src/speediance_mcp/cli.py`:
```python
"""`speediance-mcp` command line."""

from __future__ import annotations

import argparse
import getpass
import sys

from . import __version__
from .config import clear_credentials, load_credentials, save_credentials
from .paths import data_dir
from .speediance.api import SpeedianceAPI
from .speediance.client import SpeedianceClient, SpeedianceError


def _make_client(creds, region):
    return SpeedianceClient(creds, region=region)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="speediance-mcp",
        description="Unofficial MCP server for Speediance Gym Monster. With no command it runs the MCP server "
                    "over stdio — what Claude Desktop and Claude Code launch.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run the MCP server (the default).")
    serve.add_argument("--http", action="store_true", help="Remote mode with OAuth (coming in a later release).")
    login = sub.add_parser("login", help="Sign in to Speediance and store credentials locally.")
    login.add_argument("--email")
    login.add_argument("--region", choices=["Global", "EU"], default="Global")
    login.add_argument("--device-type", type=int, choices=[1, 2], default=1, help="1 = Gym Monster (default), 2 = Gym Pal")
    remember = login.add_mutually_exclusive_group()
    remember.add_argument("--remember", dest="remember", action="store_true", default=None,
                          help="Store the password so expired sessions renew silently.")
    remember.add_argument("--no-remember", dest="remember", action="store_false")
    sub.add_parser("logout", help="Sign out and delete stored credentials.")
    sub.add_parser("status", help="Show the signed-in account and data directory.")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "serve"
    if command == "serve":
        return _serve(getattr(args, "http", False))
    if command == "login":
        return _login(args)
    if command == "logout":
        return _logout()
    return _status()


def _serve(http: bool) -> int:
    if http:
        print("Remote mode (--http) isn't available yet; it arrives in the next release. "
              "Run without --http for local mode.", file=sys.stderr)
        return 2
    from .server import build_server
    from .tools.context import App
    build_server(App(mode="local")).run("stdio")
    return 0


def _login(args) -> int:
    email = (args.email or input("Speediance email: ")).strip()
    password = getpass.getpass("Password: ")
    remember = args.remember
    if remember is None:
        answer = input("Remember the password so expired sessions renew automatically? [y/N]: ")
        remember = answer.strip().lower() in ("y", "yes")
    client = _make_client(None, args.region)
    try:
        creds = client.login(email, password, remember=remember, device_type=args.device_type)
    except SpeedianceError as exc:
        client.close()
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1
    path = save_credentials(creds)
    print(f"Signed in as {creds.email} (display unit: {creds.unit}, region: {creds.region}). "
          f"Credentials saved to {path}.")
    if not remember:
        print("Password not stored: signing in on the phone app ends this session; run `speediance-mcp login` again then.")
    print("Downloading the exercise library (first time only; about 30 seconds)...")
    try:
        count = len(SpeedianceAPI(client, path.parent).library(force=True))
    except SpeedianceError as exc:
        print(f"Couldn't cache the exercise library now ({exc}); it will download on first use.")
    else:
        print(f"Cached {count} exercises.")
    finally:
        client.close()
    return 0


def _logout() -> int:
    creds = load_credentials()
    if creds is None:
        print("Not logged in.")
        return 0
    client = _make_client(creds, creds.region)
    client.logout()
    client.close()
    clear_credentials()
    print(f"Signed out {creds.email} and deleted the stored credentials.")
    return 0


def _status() -> int:
    creds = load_credentials()
    home = data_dir()
    if creds is None:
        print(f"Not logged in. Run `speediance-mcp login`.\nData directory: {home}")
        return 1
    print("\n".join([f"Account: {creds.email}", f"Region: {creds.region}", f"Display unit: {creds.unit}",
                     f"Device type: {creds.device_type}",
                     f"Password remembered: {'yes' if creds.password else 'no'}", f"Data directory: {home}"]))
    return 0
```

`src/speediance_mcp/__main__.py`:
```python
from .cli import main

raise SystemExit(main())
```

Append to `.github/workflows/ci.yml` under the job's `steps`:
```yaml
      - run: speediance-mcp --help
      - run: python -m speediance_mcp --version
```

- [ ] **Step 4: Run to verify it passes, then the whole suite**

Run: `.venv/bin/python -m unittest tests.test_server_cli -v`
Expected: 9 tests OK.
Run: `.venv/bin/python -m unittest discover -s tests -t . -v`
Expected: all tests OK.
Run: `.venv/bin/speediance-mcp --help`
Expected: usage text listing `serve`, `login`, `logout`, `status`.

- [ ] **Step 5: Commit**

```bash
git add src/speediance_mcp/tools/errors.py src/speediance_mcp/server.py src/speediance_mcp/cli.py src/speediance_mcp/__main__.py .github/workflows/ci.yml tests/test_server_cli.py
git commit -m "feat: MCP server with 26 tools and the speediance-mcp CLI"
```

---

### Task 14: README, documentation guard and release check

**Files:**
- Modify: `README.md` (full content)
- Test: `tests/test_readme.py`

**Interfaces:**
- Consumes: `server.TOOLS`.

- [ ] **Step 1: Write the failing test** — `tests/test_readme.py`

```python
from __future__ import annotations

import unittest
from pathlib import Path

from speediance_mcp.server import TOOLS

README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")


class TestReadme(unittest.TestCase):
    def test_every_tool_is_documented(self):
        missing = [fn.__name__ for fn in TOOLS if f"`{fn.__name__}`" not in README]
        self.assertEqual(missing, [])

    def test_required_sections(self):
        for heading in ("## Install", "## Connect Claude", "## Tools", "## Troubleshooting",
                        "## Your data", "## Acknowledgments", "Unofficial"):
            self.assertIn(heading, README)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_readme -v`
Expected: FAIL — tools missing from README.

- [ ] **Step 3: Write `README.md`**

````markdown
# speediance-mcp

A free, open-source [MCP](https://modelcontextprotocol.io) server that lets Claude read and manage your
**Speediance Gym Monster** training: your session history, per-set logs, exercise progress, rowing stats,
heart rate, custom workouts and schedule — plus a coaching memory of your goals, injuries and preferences.

It's a self-hosted alternative to GM Manager. It runs on your own computer, talks directly to Speediance,
and keeps everything it stores on your machine.

> **Unofficial.** Not affiliated with or endorsed by Speediance. It uses the private API behind the
> Speediance mobile app, which can change without notice.

## Install

You need **Python 3.10 or newer**. Check with `python3 --version` (Windows: `py --version`).
Get Python from [python.org](https://www.python.org/downloads/) if needed.

The easiest way is **pipx**, which installs the command in its own isolated environment:

| OS | Install pipx | Install speediance-mcp |
|---|---|---|
| macOS | `brew install pipx && pipx ensurepath` | `pipx install git+https://github.com/labatt/speediance-mcp` |
| Windows | `py -m pip install --user pipx && py -m pipx ensurepath` | `pipx install git+https://github.com/labatt/speediance-mcp` |
| Linux | `python3 -m pip install --user pipx && python3 -m pipx ensurepath` | `pipx install git+https://github.com/labatt/speediance-mcp` |

Open a new terminal after `ensurepath`. Alternatives: `uvx --from git+https://github.com/labatt/speediance-mcp speediance-mcp`,
or `pip install git+https://github.com/labatt/speediance-mcp` inside a virtual environment.

### Sign in

```
speediance-mcp login
```

Enter your Speediance email and password. You'll be asked whether to **remember the password**:

- **Yes:** when your session expires — for example because you signed in on the phone app, which ends
  other sessions — it renews silently.
- **No:** only the session token is stored; run `speediance-mcp login` again when it expires.

Use `--region EU` if your account is on Speediance's EU servers, and `--device-type 2` for a Gym Pal.
Login also downloads the exercise library (about 30 seconds, once a day at most).

`speediance-mcp status` shows who you're signed in as; `speediance-mcp logout` signs out and deletes the
stored credentials.

## Connect Claude

### Claude Desktop

Open the config file (Claude Desktop → Settings → Developer → Edit Config), or create it:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

Add:

```json
{
  "mcpServers": {
    "speediance": { "command": "speediance-mcp" }
  }
}
```

If Claude can't find the command, use the full path from `which speediance-mcp` (Windows:
`where speediance-mcp`). Restart Claude Desktop.

### Claude Code

```
claude mcp add speediance -- speediance-mcp
```

Then ask Claude something like *"How did my last pull workout compare to the one before?"*

## Tools

All weights are in your account's display unit (kg or lb) — nothing is converted.

| Tool | What it does |
|---|---|
| `check_connection` | Verify the Speediance login is live |
| `get_calendar` | A month's scheduled and completed sessions |
| `get_session_detail` | One session's per-exercise log — sets, reps, weights; rowing pace/power; guided-cardio intervals |
| `get_heart_rate` | A watch-paired session's heart-rate curve and summary |
| `get_training_stats` | Totals between two dates |
| `get_athlete_snapshot` | Profile, coaching memory and recent sessions in one call |
| `get_strength_profile` | Estimated 1RM per recently trained movement |
| `compare_sessions` | A session versus the previous one, per movement |
| `suggest_load` | A working weight for a rep target, with its reasoning |
| `list_exercises` | Search the exercise library (body part, equipment, what you own) |
| `get_exercise` | One movement's muscles, equipment, form cues and media |
| `mark_exercise` | Mark a movement ★ preferred or ⊘ avoided |
| `list_accessories` | Speediance's accessories, flagged with what you own |
| `get_exercise_history` | Every session of one movement, oldest to newest |
| `list_my_workouts` | Your saved custom workouts |
| `get_workout` | One workout's full prescription |
| `create_workout` | Create a workout (verified by reading it back) |
| `update_workout` | Edit a workout in place |
| `delete_workout` | Delete a workout |
| `schedule_workout` | Put a workout on a day |
| `unschedule_workout` | Take a workout off a day |
| `browse_programs` | Speediance's official programs |
| `get_preferences` | The coaching memory |
| `set_preferences` | Goal, training days, session length, load anchors, owned equipment |
| `remember_fact` | Save a fact about your training (optionally temporary) |
| `forget_fact` | Remove a saved fact |

The first 19 use GM Manager's tool names and, for most, its parameter names (`template_id`, `memory_id`,
`groupId`, `add`...), so prompts written for GM Manager keep working. `set_preferences`, `suggest_load` and
`create_workout` take simpler inputs: typed preference fields instead of one JSON blob, and no Dynamic
Weight modes or RM presets yet.

**Rowing:** every rowing session gets distance, pace per 500m, speed, average power and calories per minute.
Guided cardio sessions (like "Aerobic Rowing") also get per-interval rows. Rowing done as a course or custom
workout only has session totals — Speediance's API doesn't expose more.

## Troubleshooting

- **"Not signed in to Speediance, or the session expired."** Run `speediance-mcp login`. Signing in on the
  phone app ends other sessions; choose "remember password" to have this renew automatically.
- **Claude doesn't list the tools.** Check the config JSON is valid, use the command's full path, and restart
  Claude.
- **The first exercise search is slow.** The library downloads once (about 30 seconds) and is then cached
  for a day.
- **A workout comes back `verified: false`.** Speediance stored something different from what was sent.
  Check the workout in the Speediance app before training and please open an issue with your unit (kg or lb).

## Your data

Everything lives in one folder (run `speediance-mcp status` to see it):

- **Windows:** `%APPDATA%\speediance-mcp`
- **macOS:** `~/Library/Application Support/speediance-mcp`
- **Linux:** `~/.config/speediance-mcp`

It holds `credentials.json` (your token, and your password only if you chose "remember"), `speediance-mcp.db`
(the coaching memory) and the exercise-library cache. On macOS and Linux the credentials file is readable only
by you; on Windows it relies on your user profile folder's permissions. Delete the folder to erase everything.
Set `SPEEDIANCE_MCP_HOME` to use a different folder.

Nothing is sent anywhere except Speediance's own servers. Requests are paced at one per second.

## Development

```
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -t . -v
```

Tests are offline and use synthetic data only. Maintainers can run this manual check against a real account
before a release: sign in; run `get_athlete_snapshot`; create, verify and delete a throwaway workout; and call
`get_heart_rate` on a watch-paired session.

## Acknowledgments

- The Speediance client is ported from [hbui3/UnofficialSpeedianceWorkoutManager](https://github.com/hbui3/UnofficialSpeedianceWorkoutManager) (MIT).
- API findings — the session-type routes, Free Lift scaling and template write rules — come from the notes of
  [pookey/speediance-cli](https://github.com/pookey/speediance-cli) and
  [stozo04/speediance-cli](https://github.com/stozo04/speediance-cli) (both MIT).

## License

MIT — see [LICENSE](LICENSE).
````

- [ ] **Step 4: Run the README test and the full suite**

Run: `.venv/bin/python -m unittest tests.test_readme -v`
Expected: 2 tests OK.
Run: `.venv/bin/python -m unittest discover -s tests -t . -v`
Expected: all tests OK.

- [ ] **Step 5: Smoke-test the stdio server starts**

Run: `.venv/bin/python -c "from speediance_mcp.server import build_server; from speediance_mcp.tools.context import App; import tempfile, asyncio; s = build_server(App(tempfile.mkdtemp())); print(len(asyncio.run(s.list_tools())))"`
Expected: `26`

- [ ] **Step 6: Commit**

```bash
git add README.md tests/test_readme.py
git commit -m "docs: README with install, Claude setup, tools, troubleshooting and data locations"
```
