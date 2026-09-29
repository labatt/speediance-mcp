from __future__ import annotations

import asyncio
import os
import stat
import tempfile
import unittest
from pathlib import Path

from mcp.server.auth.provider import AuthorizationParams, RegistrationError
from mcp.shared.auth import OAuthClientInformationFull

from speediance_mcp.oauth import SpeedianceOAuthProvider
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
        self.assertTrue(self.store.mark_used(r))
        self.assertTrue(self.store.token_row(r)["used"])
        self.store.revoke_family("fam")
        self.assertTrue(self.store.token_row(a)["revoked"])
        self.assertIsNone(self.store.token_row("unknown"))

    def test_mark_used_is_atomic_and_prevents_replay(self):
        token = self.store.issue_token("refresh", "c1", "fam", ["x"], None, ttl=3600)
        self.assertTrue(self.store.mark_used(token))
        self.assertFalse(self.store.mark_used(token))

    def test_mark_used_fails_on_revoked_token(self):
        token = self.store.issue_token("access", "c1", "fam", ["x"], None, ttl=3600)
        self.store.revoke_family("fam")
        self.assertFalse(self.store.mark_used(token))

    def test_purge_removes_expired_rows(self):
        code = self.store.add_code("c1", "{}", ttl=300)
        pending_id, _ = self.store.add_pending("c1", "{}", ttl=300)
        token = self.store.issue_token("access", "c1", "fam", ["x"], None, ttl=300)
        self.store.record_failure("1.1.1.1")
        # Verify they exist before purge
        self.assertIsNotNone(self.store.get_code(code))
        self.assertIsNotNone(self.store.get_pending(pending_id))
        self.assertIsNotNone(self.store.token_row(token))
        self.assertEqual(len(self.store.failures(since=0)), 1)
        # Advance clock past all expiries and purge
        self.clock.now += 86401  # Advance past 24h cutoff
        self.store.purge()
        # Verify expired rows are gone
        self.assertIsNone(self.store.get_code(code))
        self.assertIsNone(self.store.get_pending(pending_id))
        self.assertIsNone(self.store.token_row(token))
        # Verify failure older than 24h is gone
        self.assertEqual(len(self.store.failures(since=0)), 0)

    def test_purge_keeps_unexpired_rows(self):
        code1 = self.store.add_code("c1", "{}", ttl=300)
        code2 = self.store.add_code("c1", "{}", ttl=600)
        self.clock.now += 301
        self.store.purge()
        self.assertIsNone(self.store.get_code(code1))
        self.assertIsNotNone(self.store.get_code(code2))

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
        # Test with permissive umask to verify it doesn't affect the result
        old_umask = os.umask(0o022)
        try:
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            path = Path(tmp.name) / "oauth_umask.db"
            store = OAuthStore(path, clock=self.clock)
            self.addCleanup(store.close)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        finally:
            os.umask(old_umask)

    def test_failures_window(self):
        self.store.record_failure("1.1.1.1")
        self.clock.now += 10
        self.store.record_failure("2.2.2.2")
        self.assertEqual(len(self.store.failures(since=0)), 2)
        self.assertEqual(self.store.failures(since=0, ip="1.1.1.1"), [1_000_000.0])
        self.assertEqual(self.store.failures(since=1_000_005.0), [1_000_010.0])

    def test_revoked_family_makes_new_tokens_dead_on_arrival(self):
        # Issue a token in family F, revoke the family, then issue another token in F
        token1 = self.store.issue_token("access", "c1", "fam", ["x"], None, ttl=3600)
        self.assertFalse(self.store.token_row(token1)["revoked"])
        self.store.revoke_family("fam")
        self.assertTrue(self.store.token_row(token1)["revoked"])
        # New token issued into the same family is marked revoked
        token2 = self.store.issue_token("refresh", "c1", "fam", ["x"], None, ttl=3600)
        self.assertTrue(self.store.token_row(token2)["revoked"])

    def test_pending_is_capped_and_the_newest_request_wins(self):
        ids = [self.store.add_pending("c1", "{}", ttl=600)[0] for _ in range(250)]
        count = self.store._db.execute("SELECT COUNT(*) FROM pending").fetchone()[0]
        self.assertLessEqual(count, 200)
        self.assertIsNotNone(self.store.get_pending(ids[-1]))
        self.assertIsNone(self.store.get_pending(ids[0]))

    def test_unused_client_older_than_a_day_is_pruned_but_one_with_tokens_is_kept(self):
        self.store.save_client("unused", '{"client_id": "unused"}')
        self.store.save_client("used", '{"client_id": "used"}')
        self.store.issue_token("refresh", "used", "fam", [], None, ttl=90 * 86400)
        self.clock.now += 86401
        self.store.save_client("new", '{"client_id": "new"}')
        self.assertIsNone(self.store.client_json("unused"))
        self.assertIsNotNone(self.store.client_json("used"))
        self.assertIsNotNone(self.store.client_json("new"))

    def test_the_501st_client_is_refused(self):
        for i in range(500):
            self.store.save_client(f"c{i}", "{}")
        with self.assertRaises(RuntimeError):
            self.store.save_client("one-too-many", "{}")
        self.assertIsNone(self.store.client_json("one-too-many"))

    def test_an_old_clients_table_is_migrated_and_its_unused_clients_count_as_old(self):
        self.store.close()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "old.db"
        import sqlite3
        db = sqlite3.connect(str(path))
        db.execute("CREATE TABLE clients (client_id TEXT PRIMARY KEY, info_json TEXT NOT NULL)")
        db.execute("INSERT INTO clients VALUES ('legacy', '{}')")
        db.commit()
        db.close()
        store = OAuthStore(path, clock=self.clock)
        self.addCleanup(store.close)
        self.assertEqual(store.client_json("legacy"), "{}")
        store.save_client("fresh", "{}")
        self.assertIsNone(store.client_json("legacy"))  # NULL created_at = old, and never used
        self.assertEqual(store.client_json("fresh"), "{}")

    def test_expiry_indexes_exist(self):
        names = {r[0] for r in self.store._db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        self.assertTrue({"pending_by_expiry", "codes_by_expiry", "tokens_by_expiry"} <= names)

    def test_revoke_all_drops_tokens_pending_and_codes_but_keeps_clients(self):
        self.store.save_client("c1", "{}")
        token = self.store.issue_token("access", "c1", "fam", [], None, ttl=3600)
        code = self.store.add_code("c1", "{}", ttl=300)
        pending_id, _ = self.store.add_pending("c1", "{}", ttl=600)
        self.store.revoke_all()
        self.assertIsNone(self.store.token_row(token))
        self.assertIsNone(self.store.get_code(code))
        self.assertIsNone(self.store.get_pending(pending_id))
        self.assertEqual(self.store.client_json("c1"), "{}")


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

    def test_registration_is_refused_when_the_client_table_is_full(self):
        for i in range(499):  # c1 from setUp makes 500
            self.store.save_client(f"x{i}", "{}")
        extra = OAuthClientInformationFull(client_id="c-extra", redirect_uris=[REDIRECT],
                                           token_endpoint_auth_method="none")
        with self.assertRaises(RegistrationError) as cm:
            run(self.provider.register_client(extra))
        self.assertEqual(cm.exception.error, "invalid_client_metadata")
        self.assertEqual(cm.exception.error_description, "Too many registered clients; try again later.")

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

    def test_concurrent_refresh_second_exchange_fails_and_revokes(self):
        first = self.tokens()
        refresh = run(self.provider.load_refresh_token(self.client, first.refresh_token))
        # First exchange succeeds
        second = run(self.provider.exchange_refresh_token(self.client, refresh, []))
        self.assertIsNotNone(run(self.provider.load_access_token(second.access_token)))
        # Second exchange with the same loaded refresh token fails and revokes the family
        from mcp.server.auth.provider import TokenError
        with self.assertRaises(TokenError) as cm:
            run(self.provider.exchange_refresh_token(self.client, refresh, []))
        self.assertEqual(cm.exception.error, "invalid_grant")
        self.assertEqual(cm.exception.error_description, "refresh token already used")
        # The first exchange's new access token is revoked
        self.assertIsNone(run(self.provider.load_access_token(second.access_token)))
        # The new refresh token from the first exchange is also revoked
        self.assertIsNone(run(self.provider.load_refresh_token(self.client, second.refresh_token)))

    def test_tokens_issued_to_revoked_family_are_dead_on_arrival(self):
        # After replay revokes a family, tokens issued to that family are unusable
        first = self.tokens()
        refresh = run(self.provider.load_refresh_token(self.client, first.refresh_token))
        second = run(self.provider.exchange_refresh_token(self.client, refresh, []))
        # Replay revokes the family
        from mcp.server.auth.provider import TokenError
        with self.assertRaises(TokenError):
            run(self.provider.exchange_refresh_token(self.client, refresh, []))
        # The family is marked revoked: both old and new tokens are unusable
        self.assertIsNone(run(self.provider.load_access_token(second.access_token)))
        # Verify if we try to issue more tokens to this family, they're also dead
        family_row = self.store.token_row(second.refresh_token)
        self.assertIsNotNone(family_row)
        # Issue a new token in the same family directly via store
        new_token = self.store.issue_token("access", self.client.client_id, family_row["family"],
                                           family_row["scopes"], family_row["resource"], 3600)
        # This new token is marked revoked and unusable
        self.assertIsNone(run(self.provider.load_access_token(new_token)))

    def test_refresh_token_of_another_client_is_rejected(self):
        # Client c2 cannot use c1's refresh token
        first = self.tokens()
        # Register another client
        other = OAuthClientInformationFull(client_id="c2", redirect_uris=[REDIRECT],
                                          token_endpoint_auth_method="none", client_name="Other")
        run(self.provider.register_client(other))
        # c2 tries to load c1's refresh token -> returns None
        self.assertIsNone(run(self.provider.load_refresh_token(other, first.refresh_token)))
        # c1's refresh token is still loadable by c1 (c2's attempt did not revoke it)
        refresh = run(self.provider.load_refresh_token(self.client, first.refresh_token))
        self.assertIsNotNone(refresh)
        self.assertEqual(refresh.token, first.refresh_token)


class TestRedirectHostAllowlist(unittest.TestCase):
    """Fix round 1, finding 2: dynamic registration must not let anyone mint a genuine-looking
    /login link that hands their own host an authorization code (consent phishing)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.clock = Clock()
        self.store = OAuthStore(Path(tmp.name) / "oauth.db", clock=self.clock)
        self.addCleanup(self.store.close)
        self.provider = SpeedianceOAuthProvider(self.store, PUBLIC, clock=self.clock)

    def test_untrusted_host_is_rejected(self):
        client = OAuthClientInformationFull(client_id="evil", redirect_uris=["https://evil.example/cb"],
                                            token_endpoint_auth_method="none", client_name="Claude")
        with self.assertRaises(RegistrationError) as cm:
            run(self.provider.register_client(client))
        self.assertEqual(cm.exception.error, "invalid_redirect_uri")
        self.assertIsNone(self.store.client_json("evil"))  # rejected registration is never saved

    def test_claude_ai_over_https_is_accepted(self):
        client = OAuthClientInformationFull(client_id="ok", redirect_uris=[REDIRECT],
                                            token_endpoint_auth_method="none")
        run(self.provider.register_client(client))  # does not raise
        self.assertIsNotNone(run(self.provider.get_client("ok")))

    def test_localhost_over_http_is_accepted(self):
        client = OAuthClientInformationFull(client_id="local", redirect_uris=["http://localhost:1234/callback"],
                                            token_endpoint_auth_method="none")
        run(self.provider.register_client(client))
        self.assertIsNotNone(run(self.provider.get_client("local")))

    def test_claude_ai_over_http_is_rejected(self):
        client = OAuthClientInformationFull(client_id="insecure",
                                            redirect_uris=["http://claude.ai/api/mcp/auth_callback"],
                                            token_endpoint_auth_method="none")
        with self.assertRaises(RegistrationError):
            run(self.provider.register_client(client))

    def test_empty_redirect_uris_is_rejected(self):
        client = OAuthClientInformationFull(client_id="none", redirect_uris=[], token_endpoint_auth_method="none")
        with self.assertRaises(RegistrationError):
            run(self.provider.register_client(client))

    def test_subdomain_of_an_allowed_host_is_not_implicitly_allowed(self):
        client = OAuthClientInformationFull(client_id="sub", redirect_uris=["https://evil.claude.ai/cb"],
                                            token_endpoint_auth_method="none")
        with self.assertRaises(RegistrationError):
            run(self.provider.register_client(client))

    def test_host_comparison_is_case_insensitive(self):
        client = OAuthClientInformationFull(client_id="upper",
                                            redirect_uris=["https://Claude.AI/api/mcp/auth_callback"],
                                            token_endpoint_auth_method="none")
        run(self.provider.register_client(client))  # does not raise
        self.assertIsNotNone(run(self.provider.get_client("upper")))

    def test_custom_allowlist_can_be_narrower(self):
        provider = SpeedianceOAuthProvider(self.store, PUBLIC, clock=self.clock,
                                           allowed_redirect_hosts=("mcp.example.com",))
        client = OAuthClientInformationFull(client_id="narrow", redirect_uris=[REDIRECT],
                                            token_endpoint_auth_method="none")
        with self.assertRaises(RegistrationError):
            run(provider.register_client(client))
