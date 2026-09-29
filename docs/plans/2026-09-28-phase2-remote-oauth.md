# speediance-mcp Phase 2 (Remote Mode for claude.ai) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let claude.ai (web and mobile) use the Speediance MCP server as a custom connector: streamable HTTP at `/mcp`, protected by OAuth with a sign-in page that only accepts the deployment's own Speediance account, and deploy it at `https://mcp.example.com`.

**Architecture:** The MCP SDK already implements the OAuth authorization-server endpoints (discovery metadata, dynamic client registration, `/authorize`, `/token`, `/revoke`) around a provider interface. We supply that provider (`oauth.py`), backed by a small SQLite store (`oauth_store.py`) that keeps codes and tokens only as SHA-256 hashes. `/authorize` hands off to our own sign-in page (`signin.py`, CSRF-protected and rate-limited), which checks the email is the deployment's account and verifies the password by logging in to Speediance. `remote.py` assembles the ASGI app, and `speediance-mcp serve --http` runs it under uvicorn behind nginx.

**Tech Stack:** Python ≥3.10, `mcp>=2.2,<3` (brings Starlette, uvicorn, python-multipart, httpx2 — no new dependencies), stdlib `sqlite3`/`hashlib`/`secrets`/`string`, unittest with `starlette.testclient.TestClient`.

**Spec:** `docs/specs/2026-09-27-speediance-mcp-design.md` — §4.2 (remote mode), §9 (security), §13 phase 2, and §15 amendments (client types: remote mode logs in with the stored `client_type`).

## Global Constraints

- No new runtime dependencies: only `mcp>=2.2,<3`. Server class `from mcp.server.mcpserver import MCPServer`; OAuth types from `mcp.server.auth.provider` and `mcp.shared.auth`; settings from `mcp.server.auth.settings`; `TransportSecuritySettings` from `mcp.server.transport_security`.
- Every module starts with `from __future__ import annotations`; stdlib unittest; offline tests with synthetic data only (`athlete@example.com`).
- Never log or print passwords, OAuth codes or tokens. Codes and tokens are stored as SHA-256 hex digests only.
- Local stdio mode (phase 1) behaviour must not change.
- Remote login accepts **only** the account in `credentials.json` (email compared case-insensitively) and refuses to start without it.
- PKCE S256 mandatory (the SDK enforces it); authorization codes single-use, 5-minute expiry; access tokens 1 hour; refresh tokens rotate on every use, and presenting an already-used refresh token revokes its whole family.
- Failed sign-ins are rate-limited: 5 per IP and 20 globally per 15 minutes, plus exponential backoff between one IP's attempts (1, 2, 4, 8 s).
- Sign-in page: CSRF token per pending request, `Cache-Control: no-store`, `X-Frame-Options: DENY`, CSP `frame-ancestors 'none'`, user text HTML-escaped.
- The server binds `127.0.0.1` by default; `--public-url` (https) is required and sets issuer, resource and the allowed Host header.
- Test command: `.venv/bin/python -m unittest discover -s tests -t . -v`.

## Review Focus

1. **A stranger enters their own Speediance account on the sign-in page** → rejected with "not the account this server is set up for", and Speediance is never called with their password. *(Test: Task 3.)*
2. **Someone guesses passwords** → after 5 failures from one IP the page answers 429 without calling Speediance; attempts in between are spaced by backoff. *(Test: Task 3.)*
3. **A stolen refresh token is replayed after the real client already used it** → the replay fails and the whole family is revoked, including the newest access token. *(Test: Task 2.)*
4. **The server restarts** → tokens issued before the restart still work, so claude.ai stays connected. *(Test: Task 2.)*
5. **A request arrives with a forged Host header (DNS rebinding)** → 421, never served. *(Test: Task 4.)*

## File Structure

```
src/speediance_mcp/
  oauth_store.py   NEW  SQLite: clients, pending sign-ins, codes, tokens, failed sign-ins (hashes only)
  oauth.py         NEW  SpeedianceOAuthProvider (the SDK's OAuthAuthorizationServerProvider)
  signin.py        NEW  LoginGate (account check + rate limit) and the /login page handlers
  remote.py        NEW  build_remote_app(): MCPServer + auth + /login + transport security
  server.py        MOD  build_server(app, **mcp_kwargs) passes auth settings through
  cli.py           MOD  serve --http --host --port --public-url
tests/
  test_oauth.py    NEW  store + provider
  test_signin.py   NEW  gate + page handlers
  test_remote.py   NEW  end-to-end OAuth + MCP over HTTP, host guard, CLI
README.md          MOD  "Use it on claude.ai (remote mode)" section
```

Work on the existing `phase1` branch (phase 2 builds on it; nothing is merged yet).

---

### Task 1: OAuth store

**Files:**
- Create: `src/speediance_mcp/oauth_store.py`
- Test: `tests/test_oauth.py`

**Interfaces:**
- Produces: `digest(value: str) -> str`; `class OAuthStore(path: Path, *, clock=time.time)` with `save_client(client_id, info_json)`, `client_json(client_id) -> str | None`, `add_pending(client_id, params_json, ttl) -> tuple[str, str]` (request_id, csrf), `get_pending(request_id) -> dict | None` (`client_id`, `params_json`, `csrf`), `pop_pending(request_id) -> dict | None`, `add_code(client_id, data_json, ttl) -> str`, `get_code(code) -> dict | None` (`client_id`, `data_json`, `expires_at`), `delete_code(code) -> bool`, `issue_token(kind, client_id, family, scopes, resource, ttl) -> str`, `token_row(token) -> dict | None` (`kind`, `client_id`, `family`, `scopes` list, `resource`, `expires_at`, `used` bool, `revoked` bool — returned even when expired/used/revoked), `mark_used(token)`, `revoke_family(family)`, `record_failure(ip)`, `failures(since, ip=None) -> list[float]` (ascending), `close()`.

- [ ] **Step 1: Write the failing test** — `tests/test_oauth.py`

```python
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from speediance_mcp.oauth_store import OAuthStore, digest


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class TestOAuthStore(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "oauth.db"
        self.clock = Clock()
        self.store = OAuthStore(self.path, clock=self.clock)
        self.addCleanup(self.store.close)

    def test_clients_round_trip(self):
        self.store.save_client("c1", '{"client_id": "c1"}')
        self.assertEqual(self.store.client_json("c1"), '{"client_id": "c1"}')
        self.assertIsNone(self.store.client_json("nope"))

    def test_pending_expires_and_pops_once(self):
        request_id, csrf = self.store.add_pending("c1", "{}", ttl=600)
        self.assertEqual(self.store.get_pending(request_id)["csrf"], csrf)
        self.assertEqual(self.store.pop_pending(request_id)["client_id"], "c1")
        self.assertIsNone(self.store.pop_pending(request_id))
        request_id, _ = self.store.add_pending("c1", "{}", ttl=600)
        self.clock.now += 601
        self.assertIsNone(self.store.get_pending(request_id))

    def test_codes_are_single_use_and_expire(self):
        code = self.store.add_code("c1", '{"x": 1}', ttl=300)
        self.assertEqual(self.store.get_code(code)["client_id"], "c1")
        self.assertTrue(self.store.delete_code(code))
        self.assertFalse(self.store.delete_code(code))
        self.assertIsNone(self.store.get_code(code))
        code = self.store.add_code("c1", "{}", ttl=300)
        self.clock.now += 301
        self.assertIsNone(self.store.get_code(code))

    def test_tokens_and_family_revocation(self):
        a = self.store.issue_token("access", "c1", "fam", ["x"], None, ttl=3600)
        r = self.store.issue_token("refresh", "c1", "fam", ["x"], "https://r", ttl=3600)
        row = self.store.token_row(r)
        self.assertEqual((row["kind"], row["family"], row["scopes"], row["resource"], row["used"]),
                         ("refresh", "fam", ["x"], "https://r", False))
        self.store.mark_used(r)
        self.assertTrue(self.store.token_row(r)["used"])
        self.store.revoke_family("fam")
        self.assertTrue(self.store.token_row(a)["revoked"])
        self.assertIsNone(self.store.token_row("unknown"))

    def test_secrets_are_stored_only_as_hashes(self):
        code = self.store.add_code("c1", "{}", ttl=300)
        token = self.store.issue_token("access", "c1", "fam", [], None, ttl=3600)
        self.store.close()
        raw = self.path.read_bytes()
        self.assertNotIn(code.encode(), raw)
        self.assertNotIn(token.encode(), raw)
        self.assertIn(digest(token).encode(), raw)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions only")
    def test_database_is_owner_only(self):
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_failures_window(self):
        self.store.record_failure("1.1.1.1")
        self.clock.now += 10
        self.store.record_failure("2.2.2.2")
        self.assertEqual(len(self.store.failures(since=0)), 2)
        self.assertEqual(self.store.failures(since=0, ip="1.1.1.1"), [1_000_000.0])
        self.assertEqual(self.store.failures(since=1_000_005.0), [1_000_010.0])
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_oauth -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'speediance_mcp.oauth_store'`.

- [ ] **Step 3: Implement** — `src/speediance_mcp/oauth_store.py`

```python
"""OAuth state for remote mode (SQLite): registered clients, pending sign-ins, authorization
codes, tokens and failed sign-in attempts. Codes and tokens are stored only as SHA-256 hashes."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (client_id TEXT PRIMARY KEY, info_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pending (
  request_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, params_json TEXT NOT NULL,
  csrf TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS codes (
  code_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, data_json TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
  token_hash TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
  client_id TEXT NOT NULL, family TEXT NOT NULL, scopes_json TEXT NOT NULL, resource TEXT,
  expires_at REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS tokens_by_family ON tokens (family);
CREATE TABLE IF NOT EXISTS login_failures (at REAL NOT NULL, ip TEXT NOT NULL);
"""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class OAuthStore:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(SCHEMA)
        if os.name == "posix":
            os.chmod(path, 0o600)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- registered clients --------------------------------------------------------------
    def save_client(self, client_id: str, info_json: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO clients (client_id, info_json) VALUES (?, ?)",
                             (client_id, info_json))

    def client_json(self, client_id: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT info_json FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        return row["info_json"] if row else None

    # --- pending sign-ins ----------------------------------------------------------------
    def add_pending(self, client_id: str, params_json: str, ttl: float) -> tuple[str, str]:
        request_id, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        now = self._clock()
        with self._lock, self._db:
            self._db.execute("DELETE FROM pending WHERE expires_at <= ?", (now,))
            self._db.execute("INSERT INTO pending (request_id, client_id, params_json, csrf, expires_at) "
                             "VALUES (?, ?, ?, ?, ?)", (request_id, client_id, params_json, csrf, now + ttl))
        return request_id, csrf

    def get_pending(self, request_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT client_id, params_json, csrf, expires_at FROM pending "
                                   "WHERE request_id = ?", (request_id,)).fetchone()
        if row is None or row["expires_at"] <= self._clock():
            return None
        return {"client_id": row["client_id"], "params_json": row["params_json"], "csrf": row["csrf"]}

    def pop_pending(self, request_id: str) -> dict | None:
        with self._lock, self._db:
            row = self._db.execute("SELECT client_id, params_json, csrf, expires_at FROM pending "
                                   "WHERE request_id = ?", (request_id,)).fetchone()
            self._db.execute("DELETE FROM pending WHERE request_id = ?", (request_id,))
        if row is None or row["expires_at"] <= self._clock():
            return None
        return {"client_id": row["client_id"], "params_json": row["params_json"], "csrf": row["csrf"]}

    # --- authorization codes ---------------------------------------------------------------
    def add_code(self, client_id: str, data_json: str, ttl: float) -> str:
        code = secrets.token_urlsafe(32)
        with self._lock, self._db:
            self._db.execute("INSERT INTO codes (code_hash, client_id, data_json, expires_at) VALUES (?, ?, ?, ?)",
                             (digest(code), client_id, data_json, self._clock() + ttl))
        return code

    def get_code(self, code: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT client_id, data_json, expires_at FROM codes WHERE code_hash = ?",
                                   (digest(code),)).fetchone()
        if row is None or row["expires_at"] <= self._clock():
            return None
        return {"client_id": row["client_id"], "data_json": row["data_json"], "expires_at": row["expires_at"]}

    def delete_code(self, code: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM codes WHERE code_hash = ?", (digest(code),)).rowcount > 0

    # --- tokens --------------------------------------------------------------------------------
    def issue_token(self, kind: str, client_id: str, family: str, scopes: list[str], resource: str | None,
                    ttl: float) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO tokens (token_hash, kind, client_id, family, scopes_json, resource, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (digest(token), kind, client_id, family, json.dumps(list(scopes)), resource, self._clock() + ttl))
        return token

    def token_row(self, token: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tokens WHERE token_hash = ?", (digest(token),)).fetchone()
        if row is None:
            return None
        return {"kind": row["kind"], "client_id": row["client_id"], "family": row["family"],
                "scopes": json.loads(row["scopes_json"]), "resource": row["resource"],
                "expires_at": row["expires_at"], "used": bool(row["used"]), "revoked": bool(row["revoked"])}

    def mark_used(self, token: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE tokens SET used = 1 WHERE token_hash = ?", (digest(token),))

    def revoke_family(self, family: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE tokens SET revoked = 1 WHERE family = ?", (family,))

    # --- failed sign-ins ----------------------------------------------------------------------
    def record_failure(self, ip: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO login_failures (at, ip) VALUES (?, ?)", (self._clock(), ip))

    def failures(self, since: float, ip: str | None = None) -> list[float]:
        with self._lock:
            if ip is None:
                rows = self._db.execute("SELECT at FROM login_failures WHERE at > ? ORDER BY at", (since,))
            else:
                rows = self._db.execute("SELECT at FROM login_failures WHERE at > ? AND ip = ? ORDER BY at",
                                        (since, ip))
            return [r["at"] for r in rows.fetchall()]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_oauth -v`
Expected: 7 tests OK (1 skipped on Windows).

- [ ] **Step 5: Commit**

```bash
git add src/speediance_mcp/oauth_store.py tests/test_oauth.py
git commit -m "feat: SQLite OAuth store (hashed codes and tokens, pending sign-ins, failed sign-ins)"
```

---

### Task 2: OAuth provider

**Files:**
- Create: `src/speediance_mcp/oauth.py`
- Modify: `tests/test_oauth.py` (append)

**Interfaces:**
- Consumes: `OAuthStore` (Task 1).
- Produces: constants `ACCESS_TTL = 3600`, `REFRESH_TTL = 90 * 24 * 3600`, `CODE_TTL = 300`, `PENDING_TTL = 600`; `class SpeedianceOAuthProvider(store: OAuthStore, public_url: str, *, clock=time.time)` implementing the SDK protocol (`get_client`, `register_client`, `authorize`, `load_authorization_code`, `exchange_authorization_code`, `load_refresh_token`, `exchange_refresh_token`, `load_access_token`, `revoke_token`) plus `complete_sign_in(request_id: str) -> str | None` (the redirect URL carrying `code` and `state`, or None if the request is unknown/expired).

- [ ] **Step 1: Append the failing tests** — to `tests/test_oauth.py`

```python
import asyncio

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

from speediance_mcp.oauth import SpeedianceOAuthProvider

PUBLIC = "https://mcp.example.com"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def run(coro):
    return asyncio.run(coro)


class TestProvider(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "oauth.db"
        self.clock = Clock()
        self.store = OAuthStore(self.path, clock=self.clock)
        self.addCleanup(self.store.close)
        self.provider = SpeedianceOAuthProvider(self.store, PUBLIC + "/", clock=self.clock)
        self.client = OAuthClientInformationFull(client_id="c1", redirect_uris=[REDIRECT],
                                                 token_endpoint_auth_method="none", client_name="Claude")
        run(self.provider.register_client(self.client))

    def sign_in(self):
        params = AuthorizationParams(state="st", scopes=None, code_challenge="chal", redirect_uri=REDIRECT,
                                     redirect_uri_provided_explicitly=True, resource=PUBLIC + "/mcp")
        url = run(self.provider.authorize(self.client, params))
        self.assertTrue(url.startswith(PUBLIC + "/login?request="))
        location = self.provider.complete_sign_in(url.split("request=")[1])
        self.assertTrue(location.startswith(REDIRECT + "?"))
        self.assertIn("state=st", location)
        return location.split("code=")[1].split("&")[0]

    def tokens(self):
        code = run(self.provider.load_authorization_code(self.client, self.sign_in()))
        return run(self.provider.exchange_authorization_code(self.client, code))

    def test_client_registration_round_trip(self):
        self.assertEqual(run(self.provider.get_client("c1")).client_name, "Claude")
        self.assertIsNone(run(self.provider.get_client("nope")))

    def test_code_flow_issues_working_tokens(self):
        code = run(self.provider.load_authorization_code(self.client, self.sign_in()))
        self.assertEqual((code.code_challenge, code.resource), ("chal", PUBLIC + "/mcp"))
        token = run(self.provider.exchange_authorization_code(self.client, code))
        self.assertEqual(token.expires_in, 3600)
        access = run(self.provider.load_access_token(token.access_token))
        self.assertEqual(access.client_id, "c1")
        self.assertIsNone(run(self.provider.load_authorization_code(self.client, code.code)))  # single use

    def test_sign_in_request_is_single_use(self):
        params = AuthorizationParams(state=None, scopes=None, code_challenge="c", redirect_uri=REDIRECT,
                                     redirect_uri_provided_explicitly=True)
        request_id = run(self.provider.authorize(self.client, params)).split("request=")[1]
        self.assertIsNotNone(self.provider.complete_sign_in(request_id))
        self.assertIsNone(self.provider.complete_sign_in(request_id))

    def test_code_for_another_client_is_rejected(self):
        other = OAuthClientInformationFull(client_id="c2", redirect_uris=[REDIRECT], token_endpoint_auth_method="none")
        self.assertIsNone(run(self.provider.load_authorization_code(other, self.sign_in())))

    def test_access_token_expires(self):
        token = self.tokens()
        self.clock.now += 3601
        self.assertIsNone(run(self.provider.load_access_token(token.access_token)))

    def test_refresh_rotates(self):
        first = self.tokens()
        refresh = run(self.provider.load_refresh_token(self.client, first.refresh_token))
        second = run(self.provider.exchange_refresh_token(self.client, refresh, []))
        self.assertNotEqual(second.refresh_token, first.refresh_token)
        self.assertIsNotNone(run(self.provider.load_access_token(second.access_token)))

    def test_replayed_refresh_token_revokes_the_family(self):
        first = self.tokens()
        refresh = run(self.provider.load_refresh_token(self.client, first.refresh_token))
        second = run(self.provider.exchange_refresh_token(self.client, refresh, []))
        # An attacker replays the already-used refresh token:
        self.assertIsNone(run(self.provider.load_refresh_token(self.client, first.refresh_token)))
        # ...and the legitimate newest tokens die with it.
        self.assertIsNone(run(self.provider.load_access_token(second.access_token)))
        self.assertIsNone(run(self.provider.load_refresh_token(self.client, second.refresh_token)))

    def test_revoke_kills_access_and_refresh(self):
        token = self.tokens()
        run(self.provider.revoke_token(run(self.provider.load_access_token(token.access_token))))
        self.assertIsNone(run(self.provider.load_access_token(token.access_token)))
        self.assertIsNone(run(self.provider.load_refresh_token(self.client, token.refresh_token)))

    def test_tokens_survive_a_restart(self):
        token = self.tokens()
        self.store.close()
        store = OAuthStore(self.path, clock=self.clock)
        self.addCleanup(store.close)
        provider = SpeedianceOAuthProvider(store, PUBLIC, clock=self.clock)
        self.assertIsNotNone(run(provider.load_access_token(token.access_token)))
        self.assertEqual(run(provider.get_client("c1")).client_id, "c1")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_oauth -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'speediance_mcp.oauth'`.

- [ ] **Step 3: Implement** — `src/speediance_mcp/oauth.py`

```python
"""OAuth authorization server for remote mode, backed by OAuthStore (spec §4.2, §9).

The MCP SDK serves the endpoints (metadata, /register, /authorize, /token, /revoke) and
enforces PKCE and code expiry; this provider decides what codes and tokens mean. /authorize
sends the user to our own sign-in page (signin.py). Access tokens last 1 hour; refresh tokens
90 days and rotate on every use, and presenting an already-used refresh token revokes its
whole family. Scopes aren't used.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Callable

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, RefreshToken, TokenError, construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .oauth_store import OAuthStore

ACCESS_TTL = 3600
REFRESH_TTL = 90 * 24 * 3600
CODE_TTL = 300
PENDING_TTL = 600


class SpeedianceOAuthProvider:
    def __init__(self, store: OAuthStore, public_url: str, *, clock: Callable[[], float] = time.time):
        self.store = store
        self.public_url = public_url.rstrip("/")
        self._clock = clock

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        info = self.store.client_json(client_id)
        return OAuthClientInformationFull.model_validate_json(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.store.save_client(client_info.client_id, client_info.model_dump_json())

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        request_id, _csrf = self.store.add_pending(client.client_id, params.model_dump_json(), PENDING_TTL)
        return f"{self.public_url}/login?request={request_id}"

    def complete_sign_in(self, request_id: str) -> str | None:
        """Called by the sign-in page after a successful login: issue the code, return the redirect."""
        pending = self.store.pop_pending(request_id)
        if pending is None:
            return None
        params = AuthorizationParams.model_validate_json(pending["params_json"])
        data = {"scopes": params.scopes or [], "code_challenge": params.code_challenge,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "resource": params.resource}
        code = self.store.add_code(pending["client_id"], json.dumps(data), CODE_TTL)
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        row = self.store.get_code(authorization_code)
        if row is None or row["client_id"] != client.client_id:
            return None
        return AuthorizationCode(code=authorization_code, client_id=client.client_id,
                                 expires_at=row["expires_at"], **json.loads(row["data_json"]))

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        if not self.store.delete_code(authorization_code.code):
            raise TokenError(error="invalid_grant", error_description="authorization code already used")
        return self._issue_pair(client.client_id, secrets.token_hex(16), authorization_code.scopes,
                                authorization_code.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        row = self.store.token_row(refresh_token)
        if row is None or row["kind"] != "refresh" or row["client_id"] != client.client_id:
            return None
        if row["used"] or row["revoked"]:
            self.store.revoke_family(row["family"])  # a replayed refresh token: kill the whole family
            return None
        if row["expires_at"] <= self._clock():
            return None
        return RefreshToken(token=refresh_token, client_id=row["client_id"], scopes=row["scopes"],
                            expires_at=int(row["expires_at"]), resource=row["resource"])

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        row = self.store.token_row(refresh_token.token)
        self.store.mark_used(refresh_token.token)
        return self._issue_pair(client.client_id, row["family"], scopes or refresh_token.scopes,
                                refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self.store.token_row(token)
        if row is None or row["kind"] != "access" or row["revoked"] or row["expires_at"] <= self._clock():
            return None
        return AccessToken(token=token, client_id=row["client_id"], scopes=row["scopes"],
                           expires_at=int(row["expires_at"]), resource=row["resource"])

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        row = self.store.token_row(token.token)
        if row is not None:
            self.store.revoke_family(row["family"])

    def _issue_pair(self, client_id: str, family: str, scopes: list[str], resource: str | None) -> OAuthToken:
        access = self.store.issue_token("access", client_id, family, scopes, resource, ACCESS_TTL)
        refresh = self.store.issue_token("refresh", client_id, family, scopes, resource, REFRESH_TTL)
        return OAuthToken(access_token=access, expires_in=ACCESS_TTL, refresh_token=refresh,
                          scope=" ".join(scopes) or None)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_oauth -v`
Expected: all tests OK.

- [ ] **Step 5: Commit**

```bash
git add src/speediance_mcp/oauth.py tests/test_oauth.py
git commit -m "feat: OAuth provider with single-use codes, rotating refresh tokens and replay revocation"
```

---

### Task 3: Sign-in page and login gate

**Files:**
- Create: `src/speediance_mcp/signin.py`
- Test: `tests/test_signin.py`

**Interfaces:**
- Consumes: `OAuthStore`, `SpeedianceOAuthProvider.complete_sign_in`, `speediance.client.LoginFailed` / `SpeedianceError`.
- Produces: constants `WINDOW = 900`, `MAX_PER_IP = 5`, `MAX_GLOBAL = 20`, `SECURITY_HEADERS`; `class LoginGate(store, verify: Callable[[str, str], None], account_email: str, *, clock=time.time)` with `wait_seconds(ip) -> int` and `attempt(ip, email, password) -> str | None` (None = success, else a user-facing error); `sign_in_handlers(provider, store, gate) -> tuple[show, submit]` (async Starlette handlers for GET and POST `/login`).

- [ ] **Step 1: Write the failing test** — `tests/test_signin.py`

```python
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from speediance_mcp.oauth import SpeedianceOAuthProvider
from speediance_mcp.oauth_store import OAuthStore
from speediance_mcp.signin import LoginGate, sign_in_handlers
from speediance_mcp.speediance.client import LoginFailed, ServerError

ACCOUNT = "athlete@example.com"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"


class Clock:
    def __init__(self):
        self.now = 2_000_000.0

    def __call__(self):
        return self.now


class Verifier:
    def __init__(self):
        self.calls = []
        self.error = None

    def __call__(self, email, password):
        self.calls.append((email, password))
        if self.error:
            raise self.error


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.clock = Clock()
        self.store = OAuthStore(Path(tmp.name) / "oauth.db", clock=self.clock)
        self.addCleanup(self.store.close)
        self.verify = Verifier()
        self.gate = LoginGate(self.store, self.verify, ACCOUNT, clock=self.clock)


class TestLoginGate(Base):
    def test_right_account_and_password(self):
        self.assertIsNone(self.gate.attempt("1.2.3.4", " Athlete@Example.com ", "pw"))
        self.assertEqual(self.verify.calls, [("Athlete@Example.com", "pw")])

    def test_other_account_is_refused_without_calling_speediance(self):
        error = self.gate.attempt("1.2.3.4", "stranger@example.com", "pw")
        self.assertIn("isn't the Speediance account", error)
        self.assertEqual(self.verify.calls, [])

    def test_wrong_password(self):
        self.verify.error = LoginFailed("Incorrect password")
        self.assertIn("didn't accept", self.gate.attempt("1.2.3.4", ACCOUNT, "bad"))

    def test_speediance_outage_is_not_counted_as_a_failure(self):
        self.verify.error = ServerError("down")
        self.assertIn("Couldn't reach Speediance", self.gate.attempt("1.2.3.4", ACCOUNT, "pw"))
        self.assertEqual(self.gate.wait_seconds("1.2.3.4"), 0)

    def test_backoff_then_block_after_five_failures(self):
        self.verify.error = LoginFailed("nope")
        for n in range(5):
            self.assertIn("didn't accept", self.gate.attempt("9.9.9.9", ACCOUNT, "bad"))
            if n < 4:
                self.assertEqual(self.gate.wait_seconds("9.9.9.9"), 2 ** n)
                self.clock.now += 2 ** n
        calls = len(self.verify.calls)
        error = self.gate.attempt("9.9.9.9", ACCOUNT, "pw")
        self.assertIn("Too many sign-in attempts", error)
        self.assertEqual(len(self.verify.calls), calls)  # blocked before reaching Speediance
        self.clock.now += 901
        self.assertEqual(self.gate.wait_seconds("9.9.9.9"), 0)

    def test_global_limit(self):
        self.verify.error = LoginFailed("nope")
        for n in range(20):
            self.store.record_failure(f"10.0.0.{n}")
        self.assertIn("Too many sign-in attempts", self.gate.attempt("10.1.1.1", ACCOUNT, "pw"))


class TestSignInPage(Base):
    def setUp(self):
        super().setUp()
        self.provider = SpeedianceOAuthProvider(self.store, "https://mcp.example.com", clock=self.clock)
        client = OAuthClientInformationFull(client_id="c1", redirect_uris=[REDIRECT], token_endpoint_auth_method="none",
                                            client_name="Claude <b>")
        asyncio.run(self.provider.register_client(client))
        params = AuthorizationParams(state="st", scopes=None, code_challenge="c", redirect_uri=REDIRECT,
                                     redirect_uri_provided_explicitly=True)
        url = asyncio.run(self.provider.authorize(client, params))
        self.request_id = url.split("request=")[1]
        self.csrf = self.store.get_pending(self.request_id)["csrf"]
        show, submit = sign_in_handlers(self.provider, self.store, self.gate)
        app = Starlette(routes=[Route("/login", show, methods=["GET"]), Route("/login", submit, methods=["POST"])])
        self.http = TestClient(app, base_url="https://mcp.example.com")

    def post(self, **overrides):
        form = {"request": self.request_id, "csrf": self.csrf, "email": ACCOUNT, "password": "pw"}
        form.update(overrides)
        return self.http.post("/login", data=form, follow_redirects=False)

    def test_form_is_escaped_and_locked_down(self):
        resp = self.http.get("/login", params={"request": self.request_id})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Claude &lt;b&gt;", resp.text)
        self.assertNotIn(ACCOUNT, resp.text)  # never reveal which account the server is for
        self.assertIn(self.csrf, resp.text)
        self.assertEqual(resp.headers["cache-control"], "no-store")
        self.assertEqual(resp.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", resp.headers["content-security-policy"])

    def test_unknown_or_expired_request(self):
        self.assertEqual(self.http.get("/login", params={"request": "nope"}).status_code, 400)
        self.clock.now += 601
        self.assertEqual(self.http.get("/login", params={"request": self.request_id}).status_code, 400)

    def test_success_redirects_with_code_and_state(self):
        resp = self.post()
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["location"].startswith(REDIRECT + "?code="))
        self.assertIn("state=st", resp.headers["location"])
        self.assertEqual(self.post().status_code, 400)  # the request can't be reused

    def test_bad_csrf_is_refused_without_calling_speediance(self):
        self.assertEqual(self.post(csrf="forged").status_code, 400)
        self.assertEqual(self.verify.calls, [])

    def test_wrong_account_and_rate_limit_statuses(self):
        resp = self.post(email="stranger@example.com")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("isn&#x27;t the Speediance account", resp.text)
        for _ in range(4):
            self.clock.now += 60
            self.post(email="stranger@example.com")
        self.clock.now += 60
        self.assertEqual(self.post().status_code, 429)
        self.assertEqual(self.verify.calls, [])
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_signin -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'speediance_mcp.signin'`.

- [ ] **Step 3: Implement** — `src/speediance_mcp/signin.py`

```python
"""The remote connector's sign-in page (spec §4.2, §9).

Asks for the Speediance email and password, accepts only the account this server was set up
with, verifies the password by logging in to Speediance, and then hands the pending OAuth
request back to the provider. CSRF-protected per request and rate-limited: 5 failures per IP
and 20 overall per 15 minutes, with 1/2/4/8 s backoff between one IP's attempts.
"""

from __future__ import annotations

import html
import json
import math
import secrets
import time
from string import Template
from typing import Callable

import anyio
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .oauth import SpeedianceOAuthProvider
from .oauth_store import OAuthStore
from .speediance.client import LoginFailed, SpeedianceError

WINDOW = 15 * 60
MAX_PER_IP = 5
MAX_GLOBAL = 20
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
}
WRONG_ACCOUNT = "That isn't the Speediance account this server is set up for."
WRONG_PASSWORD = "Speediance didn't accept that email and password."
UNREACHABLE = "Couldn't reach Speediance just now. Try again in a minute."
EXPIRED = "This sign-in link has expired. Start again from Claude."

PAGE = Template("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect Speediance</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:48px 16px;display:flex;justify-content:center}
main{max-width:380px;width:100%}h1{font-size:1.3rem;margin:0 0 8px}p{color:#9aa4ae;font-size:.9rem;line-height:1.4}
label{display:block;margin:16px 0 6px;font-size:.85rem}
input{width:100%;box-sizing:border-box;padding:10px;border-radius:6px;border:1px solid #30363d;background:#161b22;color:#e6edf3;font-size:1rem}
button{margin-top:20px;width:100%;padding:11px;border:0;border-radius:6px;background:#2ea043;color:#fff;font-weight:600;font-size:1rem}
.error{background:#3d1d20;border:1px solid #f85149;color:#ffa198;padding:10px;border-radius:6px;font-size:.9rem;margin-top:12px}
</style></head><body><main>
<h1>Connect $client to Speediance</h1>
<p>Sign in with the Speediance account this server was set up for. Your password goes only to Speediance.</p>
$error
$form
</main></body></html>""")

FORM = Template("""<form method="post" action="/login">
<input type="hidden" name="request" value="$request"><input type="hidden" name="csrf" value="$csrf">
<label for="email">Speediance email</label>
<input id="email" name="email" type="email" autocomplete="username" required autofocus>
<label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Sign in</button>
</form>""")


class LoginGate:
    def __init__(self, store: OAuthStore, verify: Callable[[str, str], None], account_email: str, *,
                 clock: Callable[[], float] = time.time):
        self.store = store
        self.verify = verify
        self.account_email = account_email.strip().lower()
        self._clock = clock

    def wait_seconds(self, ip: str) -> int:
        now = self._clock()
        since = now - WINDOW
        mine = self.store.failures(since, ip)
        everyone = self.store.failures(since)
        if len(mine) >= MAX_PER_IP:
            return math.ceil(mine[0] + WINDOW - now)
        if len(everyone) >= MAX_GLOBAL:
            return math.ceil(everyone[0] + WINDOW - now)
        if mine:
            return max(0, math.ceil(mine[-1] + 2 ** (len(mine) - 1) - now))
        return 0

    def attempt(self, ip: str, email: str, password: str) -> str | None:
        wait = self.wait_seconds(ip)
        if wait > 0:
            when = f"{wait} seconds" if wait < 120 else f"{math.ceil(wait / 60)} minutes"
            return f"Too many sign-in attempts. Try again in {when}."
        if email.strip().lower() != self.account_email:
            self.store.record_failure(ip)
            return WRONG_ACCOUNT
        try:
            self.verify(email.strip(), password)
        except LoginFailed:
            self.store.record_failure(ip)
            return WRONG_PASSWORD
        except SpeedianceError:
            return UNREACHABLE
        return None


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    # Behind the reverse proxy on this machine trust its X-Real-IP; never a remote client's.
    if peer in ("127.0.0.1", "::1"):
        return request.headers.get("x-real-ip") or peer
    return peer


def _client_name(store: OAuthStore, client_id: str) -> str:
    info = store.client_json(client_id)
    name = json.loads(info).get("client_name") if info else None
    return name or "Claude"


def _page(store: OAuthStore, request_id: str, pending: dict | None, error: str | None, status: int) -> HTMLResponse:
    form = "" if pending is None else FORM.substitute(request=html.escape(request_id),
                                                      csrf=html.escape(pending["csrf"]))
    client = "Claude" if pending is None else _client_name(store, pending["client_id"])
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    body = PAGE.substitute(client=html.escape(client), error=error_html, form=form)
    return HTMLResponse(body, status_code=status, headers=SECURITY_HEADERS)


def sign_in_handlers(provider: SpeedianceOAuthProvider, store: OAuthStore, gate: LoginGate):
    async def show(request: Request) -> Response:
        request_id = request.query_params.get("request", "")
        pending = store.get_pending(request_id)
        if pending is None:
            return _page(store, request_id, None, EXPIRED, 400)
        return _page(store, request_id, pending, None, 200)

    async def submit(request: Request) -> Response:
        form = await request.form()
        request_id = str(form.get("request", ""))
        pending = store.get_pending(request_id)
        if pending is None:
            return _page(store, request_id, None, EXPIRED, 400)
        if not secrets.compare_digest(str(form.get("csrf", "")), pending["csrf"]):
            return _page(store, request_id, None, EXPIRED, 400)
        error = await anyio.to_thread.run_sync(gate.attempt, _client_ip(request), str(form.get("email", "")),
                                               str(form.get("password", "")))
        if error:
            return _page(store, request_id, pending, error, 429 if error.startswith("Too many") else 401)
        location = provider.complete_sign_in(request_id)
        if location is None:
            return _page(store, request_id, None, EXPIRED, 400)
        return RedirectResponse(location, status_code=302, headers=SECURITY_HEADERS)

    return show, submit
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_signin -v`
Expected: all tests OK.

- [ ] **Step 5: Commit**

```bash
git add src/speediance_mcp/signin.py tests/test_signin.py
git commit -m "feat: remote sign-in page (own account only, CSRF, rate limit with backoff)"
```

---

### Task 4: Remote server, `serve --http`, and README

**Files:**
- Create: `src/speediance_mcp/remote.py`
- Modify: `src/speediance_mcp/server.py` (`build_server` accepts `**mcp_kwargs`), `src/speediance_mcp/cli.py` (`serve --http`), `README.md`
- Test: `tests/test_remote.py`; Modify: `tests/test_server_cli.py` (replace `test_http_mode_not_yet_available`)

**Interfaces:**
- Consumes: Tasks 1–3; `tools.context.App(home, mode="remote", transport=..., min_interval=0, today=...)`; `config.Credentials`, `save_credentials`; `SpeedianceClient.login(..., client_type=...)`.
- Produces: `server.build_server(app, **mcp_kwargs) -> MCPServer`; `remote.speediance_verifier(app) -> Callable[[str, str], None]`; `remote.build_remote_app(app, public_url, *, verify=None, clock=time.time) -> ASGI app` (raises `ValueError` for a non-https URL or a server that isn't signed in); CLI `speediance-mcp serve --http --public-url URL [--host 127.0.0.1] [--port 8765]`.

- [ ] **Step 1: Write the failing tests** — `tests/test_remote.py`

```python
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import shutil
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from speediance_mcp.config import save_credentials
from speediance_mcp.remote import build_remote_app
from speediance_mcp.speediance.client import LoginFailed
from speediance_mcp.tools.context import App
from tests import fixtures as fx
from tests.helpers import CREDS, FakeSpeediance

PUBLIC = "https://mcp.example.com"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
ACCEPT = {"accept": "application/json, text/event-stream"}


def sse_json(resp):
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return resp.json()


class RemoteBase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        save_credentials(CREDS, self.home)
        self.verified = []
        self.password_ok = True

        def verify(email, password):
            self.verified.append(email)
            if not self.password_ok:
                raise LoginFailed("Incorrect password")

        fake = FakeSpeediance(fx.standard_routes())
        self.app = App(self.home, mode="remote", transport=fake.transport(), min_interval=0, today=lambda: fx.TODAY)
        self.addCleanup(self.app.close)
        asgi = build_remote_app(self.app, PUBLIC + "/", verify=verify)
        self.http = TestClient(asgi, base_url=PUBLIC)
        self.http.__enter__()
        self.addCleanup(self.http.__exit__, None, None, None)

    def connect(self):
        """Run claude.ai's side of the flow; return the token response."""
        reg = self.http.post("/register", json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
                                                "grant_types": ["authorization_code", "refresh_token"],
                                                "response_types": ["code"], "client_name": "Claude"}).json()
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        resp = self.http.get("/authorize", params={"response_type": "code", "client_id": reg["client_id"],
                                                   "redirect_uri": REDIRECT, "code_challenge": challenge,
                                                   "code_challenge_method": "S256", "state": "xyz",
                                                   "resource": PUBLIC + "/mcp"}, follow_redirects=False)
        login_url = resp.headers["location"]
        self.assertTrue(login_url.startswith(PUBLIC + "/login?request="))
        page = self.http.get(login_url.replace(PUBLIC, ""))
        csrf = page.text.split('name="csrf" value="')[1].split('"')[0]
        request_id = login_url.split("request=")[1]
        resp = self.http.post("/login", data={"request": request_id, "csrf": csrf, "email": CREDS.email,
                                              "password": "pw"}, follow_redirects=False)
        self.assertEqual(resp.status_code, 302, resp.text)
        code = resp.headers["location"].split("code=")[1].split("&")[0]
        token = self.http.post("/token", data={"grant_type": "authorization_code", "code": code,
                                               "redirect_uri": REDIRECT, "client_id": reg["client_id"],
                                               "code_verifier": verifier}).json()
        token["client_id"] = reg["client_id"]
        return token

    def mcp(self, token, method, params=None, session=None, id_=1):
        headers = dict(ACCEPT, authorization=f"Bearer {token}")
        if session:
            headers["mcp-session-id"] = session
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if id_ is not None:
            body["id"] = id_
        return self.http.post("/mcp", json=body, headers=headers)


class TestRemote(RemoteBase):
    def test_discovery_metadata_uses_the_public_url(self):
        meta = self.http.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(meta["authorization_endpoint"], PUBLIC + "/authorize")
        self.assertIn("S256", meta["code_challenge_methods_supported"])
        resource = self.http.get("/.well-known/oauth-protected-resource/mcp").json()
        self.assertEqual(resource["resource"], PUBLIC + "/mcp")

    def test_mcp_requires_a_token(self):
        resp = self.http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                              headers=ACCEPT)
        self.assertEqual(resp.status_code, 401)
        self.assertIn("resource_metadata", resp.headers["www-authenticate"])

    def test_full_connector_flow_lists_the_26_tools(self):
        token = self.connect()
        self.assertEqual(self.verified, [CREDS.email])
        init = self.mcp(token["access_token"], "initialize",
                        {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "claude-ai", "version": "1"}})
        self.assertEqual(init.status_code, 200, init.text)
        session = init.headers.get("mcp-session-id")
        self.mcp(token["access_token"], "notifications/initialized", session=session, id_=None)
        tools = sse_json(self.mcp(token["access_token"], "tools/list", session=session, id_=2))
        self.assertEqual(len(tools["result"]["tools"]), 26)

    def test_refresh_then_replay(self):
        token = self.connect()
        refreshed = self.http.post("/token", data={"grant_type": "refresh_token", "client_id": token["client_id"],
                                                   "refresh_token": token["refresh_token"]}).json()
        self.assertIn("access_token", refreshed)
        replay = self.http.post("/token", data={"grant_type": "refresh_token", "client_id": token["client_id"],
                                                "refresh_token": token["refresh_token"]})
        self.assertEqual(replay.status_code, 400)
        resp = self.mcp(refreshed["access_token"], "initialize", {"protocolVersion": "2025-06-18",
                                                                   "capabilities": {},
                                                                   "clientInfo": {"name": "x", "version": "1"}})
        self.assertEqual(resp.status_code, 401)  # the replay revoked the whole family

    def test_forged_host_header_is_refused(self):
        token = self.connect()
        resp = self.http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                              headers=dict(ACCEPT, authorization=f"Bearer {token['access_token']}",
                                           host="evil.example.net"))
        self.assertEqual(resp.status_code, 421)


class TestBuildRemoteApp(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)

    def test_refuses_without_credentials(self):
        app = App(self.home, mode="remote")
        self.addCleanup(app.close)
        with self.assertRaises(ValueError) as cm:
            build_remote_app(app, PUBLIC, verify=lambda e, p: None)
        self.assertIn("speediance-mcp login", str(cm.exception))

    def test_refuses_plain_http(self):
        save_credentials(CREDS, self.home)
        app = App(self.home, mode="remote")
        self.addCleanup(app.close)
        with self.assertRaises(ValueError):
            build_remote_app(app, "http://mcp.example.com", verify=lambda e, p: None)
```

In `tests/test_server_cli.py`, replace `test_http_mode_not_yet_available` with:

```python
    def test_http_mode_requires_public_url(self):
        code, _, err = self.run_cli("serve", "--http")
        self.assertEqual(code, 2)
        self.assertIn("--public-url", err)

    def test_http_mode_refuses_without_login(self):
        code, _, err = self.run_cli("serve", "--http", "--public-url", "https://mcp.example.com")
        self.assertEqual(code, 1)
        self.assertIn("speediance-mcp login", err)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_remote tests.test_server_cli -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'speediance_mcp.remote'`.

- [ ] **Step 3: Implement**

`src/speediance_mcp/server.py` — let `build_server` pass settings through to `MCPServer`:

```python
def build_server(app, **mcp_kwargs) -> MCPServer:
    server = MCPServer(name="speediance", instructions=INSTRUCTIONS, version=__version__, **mcp_kwargs)
    for fn in TOOLS:
        server.tool()(bind(fn, app))
    return server
```

`src/speediance_mcp/remote.py`:

```python
"""Remote mode: the MCP server over streamable HTTP at /mcp, behind OAuth (spec §4.2).

Run it with `speediance-mcp serve --http --public-url https://...` behind a TLS reverse proxy.
The SDK serves the OAuth endpoints; our provider, sign-in page and store supply the policy.
"""

from __future__ import annotations

import time
from typing import Callable
from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings

from .config import save_credentials
from .oauth import SpeedianceOAuthProvider
from .oauth_store import OAuthStore
from .server import build_server
from .signin import LoginGate, sign_in_handlers
from .speediance.client import SpeedianceClient


def speediance_verifier(app) -> Callable[[str, str], None]:
    """Verify a sign-in by logging in to Speediance with the deployment's stored settings.

    A successful login also refreshes the stored Speediance token (spec §4.2)."""
    def verify(email: str, password: str) -> None:
        creds = app.credentials()
        client = SpeedianceClient(creds, on_credentials=lambda c: save_credentials(c, app.home))
        try:
            client.login(email, password, remember=creds.password is not None, device_type=creds.device_type,
                         client_type=creds.client_type)
        finally:
            client.close()
    return verify


def build_remote_app(app, public_url: str, *, verify: Callable[[str, str], None] | None = None,
                     clock: Callable[[], float] = time.time):
    parsed = urlparse(public_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("--public-url must be the https:// address clients use, e.g. https://mcp.example.com")
    creds = app.credentials()
    if creds is None:
        raise ValueError("This server isn't signed in to Speediance. Run `speediance-mcp login` on this machine first.")
    public_url = public_url.rstrip("/")
    store = OAuthStore(app.home / "oauth.db", clock=clock)
    provider = SpeedianceOAuthProvider(store, public_url, clock=clock)
    gate = LoginGate(store, verify or speediance_verifier(app), creds.email, clock=clock)
    server = build_server(
        app,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=public_url,
            resource_server_url=f"{public_url}/mcp",
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
            validate_token_resource=False,
        ),
    )
    show, submit = sign_in_handlers(provider, store, gate)
    server.custom_route("/login", methods=["GET"])(show)
    server.custom_route("/login", methods=["POST"])(submit)
    return server.streamable_http_app(transport_security=TransportSecuritySettings(
        allowed_hosts=[parsed.netloc], allowed_origins=[public_url, "https://claude.ai"]))
```

`src/speediance_mcp/cli.py` — replace the `serve` subparser arguments and `_serve`:

```python
    serve = sub.add_parser("serve", help="Run the MCP server (the default).")
    serve.add_argument("--http", action="store_true",
                       help="Remote mode: streamable HTTP at /mcp with OAuth, for claude.ai. Put it behind HTTPS.")
    serve.add_argument("--public-url", help="The https:// address clients reach this server at (required with --http).")
    serve.add_argument("--host", default="127.0.0.1", help="Address to listen on (default 127.0.0.1).")
    serve.add_argument("--port", type=int, default=8765, help="Port to listen on (default 8765).")
```

```python
def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "serve"
    if command == "serve":
        return _serve(args)
    if command == "login":
        return _login(args)
    if command == "logout":
        return _logout()
    return _status()

def _serve(args) -> int:
    from .tools.context import App
    if not getattr(args, "http", False):
        from .server import build_server
        build_server(App(mode="local")).run("stdio")
        return 0
    if not args.public_url:
        print("--public-url is required with --http: the https:// address clients use.", file=sys.stderr)
        return 2
    from .remote import build_remote_app
    app = App(mode="remote")
    try:
        asgi = build_remote_app(app, args.public_url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    import uvicorn
    uvicorn.run(asgi, host=args.host, port=args.port, log_level="warning")
    return 0
```

(With no subcommand, `args` has no `http` attribute — hence the `getattr`. `_login`, `_logout` and `_status` are unchanged.)

`README.md` — add after "## Connect Claude":

````markdown
## Use it on claude.ai (remote mode)

claude.ai (web and mobile apps) connects to MCP servers over the internet, so this mode needs a
machine that's always on and reachable over HTTPS.

1. On that machine, install and sign in as above (`speediance-mcp login`). The remote sign-in page
   only accepts this same Speediance account.
2. Run the server (keep it running with systemd, pm2 or similar):
   ```
   speediance-mcp serve --http --public-url https://mcp.example.com
   ```
   It listens on `127.0.0.1:8765`. Put a TLS reverse proxy in front. nginx:
   ```nginx
   server {
       listen 443 ssl;
       server_name mcp.example.com;
       # ssl_certificate / ssl_certificate_key: e.g. from `certbot --nginx -d mcp.example.com`
       location / {
           proxy_pass http://127.0.0.1:8765;
           proxy_http_version 1.1;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_buffering off;
           proxy_read_timeout 3600s;
       }
   }
   ```
   Caddy (fetches the certificate itself):
   ```
   mcp.example.com {
       reverse_proxy 127.0.0.1:8765
   }
   ```
3. In claude.ai: **Settings → Connectors → Add custom connector**, URL `https://mcp.example.com/mcp`.
   Claude opens the sign-in page; enter your Speediance email and password.

Security: only the account the server was set up with can connect; failed sign-ins are
rate-limited; tokens are stored hashed; access tokens last an hour and refresh automatically.
Pick the server's `--client-type` as described in [Choose a client type](#choose-a-client-type-so-you-dont-get-signed-out-of-your-phone-or-your-machine),
and give a local copy of the server a different free slot if you run both.
````

- [ ] **Step 4: Run to verify they pass, then the whole suite**

Run: `.venv/bin/python -m unittest tests.test_remote tests.test_server_cli tests.test_readme -v`
Expected: all OK.
Run: `.venv/bin/python -m unittest discover -s tests -t . -v`
Expected: all OK, no warnings from our code.

- [ ] **Step 5: Commit**

```bash
git add src/speediance_mcp/remote.py src/speediance_mcp/server.py src/speediance_mcp/cli.py README.md tests/test_remote.py tests/test_server_cli.py
git commit -m "feat: remote mode for claude.ai — serve --http with OAuth and the sign-in page"
```

---

### Task 5: Deploy at https://mcp.example.com (controller-run operations)

This task changes the live server, not the repo; the controller runs it after Task 4's review is
clean. DNS already exists: `mcp.example.com A <your server IP>` (DNS zone
`your-dns-zone`). Do **not** touch the `app.example.com` nginx server block or its
basic-auth gate.

- [ ] **Step 1: Server credentials.** The user signs the server in on its own free slot (the web
  app holds `bike`): `! /srv/speediance-mcp/.venv/bin/speediance-mcp login --client-type nano`.
  Then confirm the phone app and the Gym Monster are still signed in (first real `nano` login).
- [ ] **Step 2: Run it under pm2** (loopback only):
  `pm2 start /srv/speediance-mcp/.venv/bin/speediance-mcp --name speediance-mcp --interpreter none -- serve --http --public-url https://mcp.example.com`
  then `pm2 save`. Check: `curl -s http://127.0.0.1:8765/.well-known/oauth-authorization-server -H 'Host: mcp.example.com'` returns JSON.
- [ ] **Step 3: nginx + TLS.** Create `/etc/nginx/sites-available/mcp.example.com`
  with a port-80 server block (`server_name mcp.example.com;` `location / { proxy_pass http://127.0.0.1:8765; ... }` as in the README), enable it, `nginx -t && systemctl reload nginx`,
  then `certbot --nginx -d mcp.example.com` (adds the 443 block and redirect).
  Add `proxy_buffering off; proxy_read_timeout 3600s; proxy_set_header X-Real-IP $remote_addr;`.
- [ ] **Step 4: Verify from outside:** `curl -s https://mcp.example.com/.well-known/oauth-authorization-server` shows `issuer` `https://mcp.example.com`; an unauthenticated `POST /mcp` returns 401 with `resource_metadata`.
- [ ] **Step 5: The user adds the connector** in claude.ai: Settings → Connectors → Add custom connector → `https://mcp.example.com/mcp`, signs in, and asks Claude to "check my Speediance connection".
````
