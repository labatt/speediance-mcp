from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import secrets
import shutil
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from speediance_mcp.config import load_credentials, save_credentials
from speediance_mcp.remote import build_remote_app, speediance_verifier
from speediance_mcp.speediance.client import LoginFailed, NotSignedIn
from speediance_mcp.tools.context import App
from tests import fixtures as fx
from tests.helpers import CREDS, FakeSpeediance, api_error

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

    def test_full_connector_flow_lists_the_32_tools(self):
        token = self.connect()
        self.assertEqual(self.verified, [CREDS.email])
        init = self.mcp(token["access_token"], "initialize",
                        {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "claude-ai", "version": "1"}})
        self.assertEqual(init.status_code, 200, init.text)
        session = init.headers.get("mcp-session-id")
        self.mcp(token["access_token"], "notifications/initialized", session=session, id_=None)
        tools = sse_json(self.mcp(token["access_token"], "tools/list", session=session, id_=2))
        self.assertEqual(len(tools["result"]["tools"]), 33)

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


class TestPublicUrlHostNormalisation(unittest.TestCase):
    def test_uppercase_host_with_default_port_still_accepts_the_real_host(self):
        from speediance_mcp.oauth_store import OAuthStore
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, True)
        save_credentials(CREDS, home)
        app = App(home, mode="remote", transport=FakeSpeediance(fx.standard_routes()).transport(),
                  min_interval=0, today=lambda: fx.TODAY)
        self.addCleanup(app.close)
        asgi = build_remote_app(app, "https://MCP.Example.com:443", verify=lambda e, p: None)
        store = OAuthStore(home / "oauth.db")
        self.addCleanup(store.close)
        token = store.issue_token("access", "c1", "fam", [], None, ttl=3600)
        with TestClient(asgi, base_url=PUBLIC) as http:
            body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "x", "version": "1"}}}
            self.assertEqual(http.post("/mcp", json=body, headers=ACCEPT).status_code, 401)
            resp = http.post("/mcp", json=body, headers=dict(ACCEPT, authorization=f"Bearer {token}",
                                                             host="mcp.example.com"))
            self.assertEqual(resp.status_code, 200, resp.text)


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

    def test_refuses_a_public_url_with_a_path(self):
        save_credentials(CREDS, self.home)
        app = App(self.home, mode="remote")
        self.addCleanup(app.close)
        with self.assertRaises(ValueError):
            build_remote_app(app, "https://mcp.example.com/sub", verify=lambda e, p: None)

    def test_refuses_a_public_url_with_a_query_or_fragment(self):
        save_credentials(CREDS, self.home)
        app = App(self.home, mode="remote")
        self.addCleanup(app.close)
        with self.assertRaises(ValueError):
            build_remote_app(app, "https://mcp.example.com/?x=1", verify=lambda e, p: None)
        with self.assertRaises(ValueError):
            build_remote_app(app, "https://mcp.example.com/#frag", verify=lambda e, p: None)

    def test_accepts_a_bare_root_or_trailing_slash_public_url(self):
        save_credentials(CREDS, self.home)
        app = App(self.home, mode="remote")
        self.addCleanup(app.close)
        build_remote_app(app, "https://mcp.example.com", verify=lambda e, p: None)
        build_remote_app(app, "https://mcp.example.com/", verify=lambda e, p: None)


class TestAllowedOrigins(RemoteBase):
    """Controller ruling: allowed_origins includes https://claude.com alongside claude.ai. The
    transport-security Origin check only runs once a bearer token has cleared the OAuth layer
    (see test_forged_host_header_is_refused for the same shape with the Host header)."""

    def test_claude_com_origin_is_allowed(self):
        token = self.connect()
        resp = self.http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                              headers=dict(ACCEPT, authorization=f"Bearer {token['access_token']}",
                                           origin="https://claude.com"))
        self.assertNotEqual(resp.status_code, 403)

    def test_an_unlisted_origin_is_refused(self):
        token = self.connect()
        resp = self.http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                              headers=dict(ACCEPT, authorization=f"Bearer {token['access_token']}",
                                           origin="https://evil.example"))
        self.assertEqual(resp.status_code, 403)


class TestSpeedianceVerifier(unittest.TestCase):
    """speediance_verifier(app) is what the sign-in page actually calls; it was untested."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)

    def make_app(self, routes, creds, **app_kwargs):
        save_credentials(creds, self.home)
        fake = FakeSpeediance(routes)
        app = App(self.home, mode="remote", transport=fake.transport(), min_interval=0, **app_kwargs)
        self.addCleanup(app.close)
        return app, fake

    def test_logs_in_with_the_stored_client_type_and_saves_the_new_token(self):
        routes = fx.standard_routes()
        routes[("POST", "/api/app/v2/login/verifyIdentity")] = {"isExist": True, "hasPwd": True}
        routes[("POST", "/api/app/v2/login/byPass")] = {"token": "tok-2", "appUserId": 1001, "unit": 1}
        creds = dataclasses.replace(CREDS, client_type="nano", password="secret")
        app, fake = self.make_app(routes, creds)

        speediance_verifier(app)(creds.email, "secret")

        calls = fake.calls("POST", "/api/app/v2/login/byPass")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].headers["App_type"], "NANO")
        saved = load_credentials(self.home)
        self.assertEqual(saved.token, "tok-2")
        self.assertEqual((saved.client_type, saved.email, saved.password), ("nano", creds.email, "secret"))

    def test_a_no_remember_deployment_stays_password_less_after_verify(self):
        routes = fx.standard_routes()
        routes[("POST", "/api/app/v2/login/verifyIdentity")] = {"isExist": True, "hasPwd": True}
        routes[("POST", "/api/app/v2/login/byPass")] = {"token": "tok-2", "appUserId": 1001, "unit": 1}
        creds = dataclasses.replace(CREDS, password=None)
        app, _ = self.make_app(routes, creds)

        speediance_verifier(app)(creds.email, "whatever-the-user-typed")

        self.assertIsNone(load_credentials(self.home).password)

    def test_raises_not_signed_in_if_credentials_were_deleted(self):
        app = App(self.home, mode="remote")
        self.addCleanup(app.close)
        with self.assertRaises(NotSignedIn):
            speediance_verifier(app)("athlete@example.com", "pw")

    def test_a_fresh_token_the_verifier_wrote_repairs_a_running_phone_slot_client(self):
        # Regression for item 1: displacement (code 90) on the phone/gym-monster slot must still
        # pick up a token a remote sign-in just wrote to disk, without touching byPass again.
        routes = fx.standard_routes()
        routes[("POST", "/api/app/v2/login/verifyIdentity")] = {"isExist": True, "hasPwd": True}
        current = {"token": CREDS.token}

        def bypass(request):
            current["token"] = "tok-2"
            return {"token": "tok-2", "appUserId": 1001, "unit": 1}
        routes[("POST", "/api/app/v2/login/byPass")] = bypass

        def profile(request):
            return fx.PROFILE if request.headers["Token"] == current["token"] else api_error(90, "offline")
        routes[("GET", "/api/app/userinfo/info")] = profile

        creds = dataclasses.replace(CREDS, client_type="phone", password="secret")
        app, fake = self.make_app(routes, creds)

        self.assertTrue(app.api.profile())  # warms the long-lived client with the old token

        speediance_verifier(app)(creds.email, "secret")  # a remote sign-in refreshes the token on disk

        self.assertTrue(app.api.profile())  # the old client's token now gets code 90; must self-heal
        self.assertEqual(len(fake.calls("POST", "/api/app/v2/login/byPass")), 1)


class TestAllowedRedirectHosts(unittest.TestCase):
    """Controller ruling: build_remote_app(..., allowed_redirect_hosts=...) extends the default set."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        save_credentials(CREDS, self.home)
        self.app = App(self.home, mode="remote")
        self.addCleanup(self.app.close)

    def test_evil_redirect_host_is_rejected_by_default(self):
        asgi = build_remote_app(self.app, PUBLIC, verify=lambda e, p: None)
        with TestClient(asgi, base_url=PUBLIC) as http:
            resp = http.post("/register", json={"redirect_uris": ["https://evil.example/cb"],
                                                 "token_endpoint_auth_method": "none",
                                                 "grant_types": ["authorization_code", "refresh_token"],
                                                 "response_types": ["code"], "client_name": "Evil"})
            self.assertEqual(resp.status_code, 400)

    def test_extra_allowed_host_is_accepted(self):
        asgi = build_remote_app(self.app, PUBLIC, verify=lambda e, p: None,
                                allowed_redirect_hosts=["client.example"])
        with TestClient(asgi, base_url=PUBLIC) as http:
            resp = http.post("/register", json={"redirect_uris": ["https://client.example/cb"],
                                                 "token_endpoint_auth_method": "none",
                                                 "grant_types": ["authorization_code", "refresh_token"],
                                                 "response_types": ["code"], "client_name": "Custom"})
            self.assertEqual(resp.status_code, 201, resp.text)
