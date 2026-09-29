from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route
from starlette.testclient import TestClient

from speediance_mcp.oauth import SpeedianceOAuthProvider
from speediance_mcp.oauth_store import OAuthStore
from speediance_mcp.signin import LoginGate, _client_ip, sign_in_handlers
from speediance_mcp.speediance.client import LoginFailed, NotSignedIn, ServerError

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
        # Finding 3: Speediance is called with the *configured* account email, never the
        # caller-supplied string — Unicode case-folding variants must never reach Speediance.
        self.assertEqual(self.verify.calls, [(ACCOUNT, "pw")])

    def test_verify_always_gets_the_configured_email_not_the_typed_one(self):
        self.assertIsNone(self.gate.attempt("1.2.3.4", "ATHLETE@EXAMPLE.COM", "pw"))
        self.assertEqual(self.verify.calls, [(ACCOUNT, "pw")])

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

    def test_server_not_signed_in_is_not_a_wrong_password_and_not_counted(self):
        self.verify.error = NotSignedIn("no credentials.json")
        error = self.gate.attempt("1.2.3.4", ACCOUNT, "pw")
        self.assertEqual(error, "This server isn't signed in to Speediance. Its owner needs to run "
                                "`speediance-mcp login` on the server.")
        self.assertEqual(self.store.failures(since=0), [])
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

    def test_one_ips_block_does_not_block_a_different_ip_below_the_global_limit(self):
        self.verify.error = LoginFailed("nope")
        for _ in range(5):
            self.gate.attempt("1.1.1.1", ACCOUNT, "bad")
        self.assertGreater(self.gate.wait_seconds("1.1.1.1"), 0)
        self.assertEqual(self.gate.wait_seconds("2.2.2.2"), 0)
        self.verify.error = None
        self.assertIsNone(self.gate.attempt("2.2.2.2", ACCOUNT, "pw"))


class SleepyLoginFailedVerifier:
    """Simulates a slow real Speediance login that always rejects the password."""

    def __init__(self, delay: float = 0.2):
        self.calls = []
        self.delay = delay
        self._lock = threading.Lock()

    def __call__(self, email, password):
        with self._lock:
            self.calls.append((email, password))
        time.sleep(self.delay)
        raise LoginFailed("nope")


class TestLoginGateConcurrency(unittest.TestCase):
    """Finding 1: attempt() must serialize check -> verify -> record so concurrent requests
    can't all pass the rate-limit check before any of them is recorded as a failure."""

    def test_concurrent_attempts_for_the_same_ip_are_serialized(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = OAuthStore(Path(tmp.name) / "oauth.db")  # real clock: threads need real time to elapse
        self.addCleanup(store.close)
        verify = SleepyLoginFailedVerifier()
        gate = LoginGate(store, verify, ACCOUNT)

        results: list[str | None] = [None] * 6

        def call(i):
            results[i] = gate.attempt("7.7.7.7", ACCOUNT, "pw")

        threads = [threading.Thread(target=call, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(verify.calls), 1)  # only the serialized winner reached Speediance
        rejected = [r for r in results if r and "didn't accept" in r]
        blocked = [r for r in results if r and "Too many sign-in attempts" in r]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(len(blocked), 5)


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
        # Finding 2: the page shows where the code will go, taken from the pending redirect_uri.
        self.assertIn("claude.ai", resp.text)
        self.assertIn("returned to", resp.text)
        self.assertEqual(resp.headers["cache-control"], "no-store")
        self.assertEqual(resp.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", resp.headers["content-security-policy"])
        self.assertIn("base-uri 'none'", resp.headers["content-security-policy"])
        # form-action is deliberately absent: browsers apply it to the post-sign-in redirect.
        self.assertNotIn("form-action", resp.headers["content-security-policy"])

    def test_page_warns_to_continue_only_after_clicking_connect(self):
        resp = self.http.get("/login", params={"request": self.request_id})
        self.assertIn("Only continue if you just clicked Connect in Claude yourself.", resp.text)

    def test_unnamed_client_is_shown_by_its_redirect_host_not_claude(self):
        other = OAuthClientInformationFull(client_id="c2", redirect_uris=[REDIRECT],
                                           token_endpoint_auth_method="none")  # no client_name
        asyncio.run(self.provider.register_client(other))
        params = AuthorizationParams(state="st2", scopes=None, code_challenge="c", redirect_uri=REDIRECT,
                                     redirect_uri_provided_explicitly=True)
        url = asyncio.run(self.provider.authorize(other, params))
        request_id = url.split("request=")[1]
        resp = self.http.get("/login", params={"request": request_id})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("claude.ai", resp.text)
        self.assertNotIn("Connect Claude to Speediance", resp.text)

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

    def test_non_ascii_csrf_is_refused_not_a_500(self):
        # Finding 4: secrets.compare_digest raises TypeError on a str/str comparison where one
        # side round-trips through non-ASCII bytes; compare as bytes instead.
        resp = self.post(csrf="é")
        self.assertEqual(resp.status_code, 400)
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

    def test_password_never_appears_in_a_failure_response(self):
        secret = "Sup3rSecretPassword!"
        resp = self.post(email="stranger@example.com", password=secret)
        self.assertEqual(resp.status_code, 401)
        self.assertNotIn(secret, resp.text)

        self.clock.now += 2  # clear the per-IP backoff from the attempt above
        self.verify.error = LoginFailed("nope")
        resp = self.post(password=secret)
        self.assertEqual(resp.status_code, 401)
        self.assertNotIn(secret, resp.text)


def _request(peer_host: str, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "client": (peer_host, 12345),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


class TestClientIp(unittest.TestCase):
    """Finding 5: _client_ip must trust X-Real-IP only when the peer is the reverse proxy
    running on this machine (loopback) — never a remote client's own claim."""

    def test_trusts_x_real_ip_only_from_a_loopback_peer(self):
        self.assertEqual(_client_ip(_request("127.0.0.1", {"x-real-ip": "203.0.113.5"})), "203.0.113.5")
        self.assertEqual(_client_ip(_request("::1", {"x-real-ip": "203.0.113.5"})), "203.0.113.5")

    def test_ignores_x_real_ip_from_a_remote_peer(self):
        self.assertEqual(_client_ip(_request("203.0.113.9", {"x-real-ip": "203.0.113.5"})), "203.0.113.9")

    def test_loopback_peer_without_the_header_falls_back_to_the_peer(self):
        self.assertEqual(_client_ip(_request("127.0.0.1")), "127.0.0.1")

    def test_ipv6_callers_are_keyed_by_their_64(self):
        # One IPv6 subscriber usually holds a whole /64; keying by address would let them rotate
        # through it to dodge the per-IP limit.
        self.assertEqual(_client_ip(_request("2001:db8:1:2:aaaa::1")), "2001:db8:1:2::/64")
        self.assertEqual(_client_ip(_request("2001:db8:1:2:bbbb::9")), "2001:db8:1:2::/64")
        self.assertEqual(_client_ip(_request("127.0.0.1", {"x-real-ip": "2001:DB8:1:2::77"})), "2001:db8:1:2::/64")

    def test_ipv4_and_ipv4_mapped_addresses_are_kept_as_they_are(self):
        self.assertEqual(_client_ip(_request("203.0.113.9")), "203.0.113.9")
        self.assertEqual(_client_ip(_request("::ffff:203.0.113.9")), "::ffff:203.0.113.9")
