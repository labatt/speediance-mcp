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


class TestCredentialReloadAndRelogin(unittest.TestCase):
    def make(self, routes, creds=CREDS, **kw):
        fake = FakeSpeediance(routes)
        client = SpeedianceClient(creds, transport=fake.transport(), min_interval=0, **kw)
        self.addCleanup(client.close)
        return client, fake

    @staticmethod
    def token_gated(good_token):
        return lambda req: ({"appUserId": 1001} if req.headers["Token"] == good_token
                            else api_error(91, "Login expired"))

    def test_adopts_newer_disk_credentials_after_auth_failure(self):
        disk = dataclasses.replace(CREDS, token="tok-disk")
        client, fake = self.make({("GET", PROFILE): self.token_gated("tok-disk")},
                                 reload_credentials=lambda: disk)
        self.assertEqual(client.get(PROFILE), {"appUserId": 1001})
        self.assertEqual([r.headers["Token"] for r in fake.calls("GET", PROFILE)], ["tok-1", "tok-disk"])
        self.assertEqual(client.creds.token, "tok-disk")
        self.assertEqual(fake.calls("POST", BYPASS), [])

    def test_deleted_credentials_file_means_logged_out_never_the_in_memory_password(self):
        # `speediance-mcp logout` deletes credentials.json; a running server must not undo that by
        # logging back in with the password it still holds in memory.
        creds = dataclasses.replace(CREDS, password="secret")
        client, fake = self.make({
            ("GET", PROFILE): api_error(91, "Login expired"),
            ("POST", VERIFY): {"isExist": True, "hasPwd": True},
            ("POST", BYPASS): {"token": "tok-2", "appUserId": 1001, "unit": 1},
        }, creds=creds, reload_credentials=lambda: None)
        with self.assertRaises(AuthExpired):
            client.get(PROFILE)
        self.assertEqual(fake.calls("POST", BYPASS), [])

    def test_same_token_on_disk_is_not_a_retry(self):
        client, fake = self.make({("GET", PROFILE): api_error(91, "Login expired")},
                                 reload_credentials=lambda: CREDS)
        with self.assertRaises(AuthExpired):
            client.get(PROFILE)
        self.assertEqual(len(fake.calls("GET", PROFILE)), 1)

    def test_relogin_skipped_when_token_already_refreshed(self):
        creds = dataclasses.replace(CREDS, password="secret")
        client, fake = self.make({
            ("POST", VERIFY): {"isExist": True, "hasPwd": True},
            ("POST", BYPASS): {"token": "tok-2", "appUserId": 1001, "unit": 1},
        }, creds=creds)
        self.assertTrue(client._relogin("tok-1"))
        self.assertTrue(client._relogin("tok-1"))   # a second failure carrying the same stale token
        self.assertEqual(len(fake.calls("POST", BYPASS)), 1)
        self.assertEqual(client.creds.token, "tok-2")

    def test_concurrent_auth_failures_log_in_once(self):
        import threading
        creds = dataclasses.replace(CREDS, password="secret")
        client, fake = self.make({
            ("GET", PROFILE): self.token_gated("tok-2"),
            ("POST", VERIFY): {"isExist": True, "hasPwd": True},
            ("POST", BYPASS): {"token": "tok-2", "appUserId": 1001, "unit": 1},
        }, creds=creds)
        barrier = threading.Barrier(2)
        original = client._relogin

        def synced(stale):
            barrier.wait(timeout=5)
            return original(stale)

        client._relogin = synced
        results = []
        threads = [threading.Thread(target=lambda: results.append(client.get(PROFILE))) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(results, [{"appUserId": 1001}] * 2)
        self.assertEqual(len(fake.calls("POST", BYPASS)), 1)


class TestLanguageHeader(unittest.TestCase):
    LOCALE_VARS = ("SPEEDIANCE_LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")

    def resolve(self, preferred=None, system=None, **env):
        from unittest import mock
        from speediance_mcp.speediance import client as client_mod
        with mock.patch.dict(client_mod.os.environ, env, clear=False) as live, \
                mock.patch.object(client_mod.locale, "getlocale", return_value=(system, None)):
            for name in self.LOCALE_VARS:
                if name not in env:
                    live.pop(name, None)
            return client_mod.resolve_language(preferred)

    def test_regional_locale_is_reduced_to_a_bare_code(self):
        # The server answers "en-GB" in Chinese; only the bare code works.
        self.assertEqual(self.resolve(LANG="en_GB.UTF-8"), "en")
        self.assertEqual(self.resolve(LANG="de_DE.UTF-8"), "de")

    def test_stored_preference_beats_env_and_locale(self):
        self.assertEqual(self.resolve("fr", SPEEDIANCE_LANGUAGE="de", LANG="it_IT.UTF-8"), "fr")

    def test_env_override_beats_locale(self):
        self.assertEqual(self.resolve(SPEEDIANCE_LANGUAGE="es", LANG="de_DE.UTF-8"), "es")

    def test_unsupported_language_falls_back_to_english_not_chinese(self):
        self.assertEqual(self.resolve(LANG="ja_JP.UTF-8"), "en")
        self.assertEqual(self.resolve("xx"), "en")

    def test_c_locale_and_nothing_set_mean_english(self):
        self.assertEqual(self.resolve(LANG="C"), "en")
        self.assertEqual(self.resolve(LANG="POSIX.UTF-8"), "en")
        self.assertEqual(self.resolve(), "en")

    def test_system_locale_used_when_env_is_empty(self):
        self.assertEqual(self.resolve(system="ko_KR"), "ko")

    def test_request_sends_the_accounts_language(self):
        import dataclasses
        fake = FakeSpeediance({("GET", PROFILE): {"appUserId": 1001}})
        client = SpeedianceClient(dataclasses.replace(CREDS, language="de"), transport=fake.transport(),
                                  min_interval=0)
        client.request("GET", PROFILE)
        self.assertEqual(fake.calls("GET", PROFILE)[0].headers["accept-language"], "de")


class TestTimezoneHeader(unittest.TestCase):
    def test_non_ascii_zone_name_falls_back_to_gmt(self):
        from unittest import mock
        from speediance_mcp.speediance import client as client_mod
        with mock.patch.dict(client_mod.os.environ, {}, clear=False) as env, \
                mock.patch.object(client_mod.time, "tzname", ("Mitteleuropäische Zeit", "Mitteleuropäische Sommerzeit")):
            env.pop("TZ", None)
            headers = client_mod._tz_headers()
        self.assertEqual(headers["Timezone"], "GMT")
        self.assertRegex(headers["Utc_offset"], r"^[+-]\d{4}$")

    def test_non_ascii_tz_env_falls_back_to_gmt(self):
        from unittest import mock
        from speediance_mcp.speediance import client as client_mod
        with mock.patch.dict(client_mod.os.environ, {"TZ": "Europe/Zürich"}):
            self.assertEqual(client_mod._tz_headers()["Timezone"], "GMT")

    def test_ascii_zone_name_is_kept(self):
        from unittest import mock
        from speediance_mcp.speediance import client as client_mod
        with mock.patch.dict(client_mod.os.environ, {"TZ": "Europe/Berlin"}):
            self.assertEqual(client_mod._tz_headers()["Timezone"], "Europe/Berlin")

    def test_request_succeeds_with_a_localized_windows_zone_name(self):
        from unittest import mock
        from speediance_mcp.speediance import client as client_mod
        fake = FakeSpeediance({("GET", PROFILE): {"appUserId": 1001}})
        client = SpeedianceClient(CREDS, transport=fake.transport(), min_interval=0)
        self.addCleanup(client.close)
        with mock.patch.dict(client_mod.os.environ, {}, clear=False) as env, \
                mock.patch.object(client_mod.time, "tzname", ("Mitteleuropäische Zeit", "x")):
            env.pop("TZ", None)
            self.assertEqual(client.get(PROFILE), {"appUserId": 1001})

class TestLoginClientType(unittest.TestCase):
    """Speediance keeps one session per client type (App_type). Logins can use a slot the user
    doesn't otherwise occupy, so they never sign out the phone app or the Gym Monster."""

    ROUTES = {("POST", VERIFY): {"isExist": True, "hasPwd": True},
              ("POST", BYPASS): {"token": "t", "appUserId": 7, "unit": 1},
              ("GET", PROFILE): {"appUserId": 7}}

    def make(self, routes, creds=None, **kw):
        fake = FakeSpeediance(routes)
        client = SpeedianceClient(creds, transport=fake.transport(), min_interval=0, **kw)
        self.addCleanup(client.close)
        return client, fake

    def login_headers(self, fake):
        return [(r.headers["App_type"], r.headers["Versioncode"])
                for r in fake.calls("POST", VERIFY) + fake.calls("POST", BYPASS)]

    def test_default_login_uses_the_bike_slot_and_other_calls_stay_normal(self):
        client, fake = self.make(self.ROUTES)
        creds = client.login("athlete@example.com", "pw")
        client.get(PROFILE)
        self.assertEqual(creds.client_type, "bike")
        self.assertEqual(self.login_headers(fake), [("BIKE", "1"), ("BIKE", "1")])
        normal = fake.calls("GET", PROFILE)[0].headers
        self.assertEqual((normal["App_type"], normal["Versioncode"]), ("SOFTWARE", "41000"))

    def test_each_client_type_maps_to_its_app_type(self):
        for client_type, expected in (("phone", ("SOFTWARE", "41000")), ("gym-monster", ("HARDWARE", "1")),
                                      ("nano", ("NANO", "1")), ("bike", ("BIKE", "1"))):
            client, fake = self.make(self.ROUTES)
            creds = client.login("athlete@example.com", "pw", client_type=client_type)
            self.assertEqual(creds.client_type, client_type)
            self.assertEqual(set(self.login_headers(fake)), {expected}, client_type)

    def test_unknown_client_type_is_rejected(self):
        client, _ = self.make(self.ROUTES)
        with self.assertRaises(ValueError):
            client.login("athlete@example.com", "pw", client_type="hardware2")

    def test_relogin_keeps_the_saved_client_type(self):
        calls = {"n": 0}

        def profile(request):
            calls["n"] += 1
            return api_error(91, "Login expired") if calls["n"] == 1 else {"appUserId": 1001}

        creds = dataclasses.replace(CREDS, password="secret", client_type="nano")
        routes = dict(self.ROUTES); routes[("GET", PROFILE)] = profile
        client, fake = self.make(routes, creds)
        client.get(PROFILE)
        self.assertEqual(set(self.login_headers(fake)), {("NANO", "1")})

    def test_code_90_relogs_in_silently_for_a_free_slot(self):
        calls = {"n": 0}

        def profile(request):
            calls["n"] += 1
            return api_error(90, "offline") if calls["n"] == 1 else {"appUserId": 1001}

        creds = dataclasses.replace(CREDS, password="secret", client_type="bike")
        routes = dict(self.ROUTES); routes[("GET", PROFILE)] = profile
        client, fake = self.make(routes, creds)
        self.assertEqual(client.get(PROFILE), {"appUserId": 1001})
        self.assertEqual(len(fake.calls("POST", BYPASS)), 1)

    def test_code_90_never_fights_the_phone_or_the_machine(self):
        # On the phone's or the Gym Monster's slot, a re-login on 90 would sign that device out
        # again (and it would sign us out back): report it instead of logging in.
        for client_type in ("phone", "gym-monster"):
            creds = dataclasses.replace(CREDS, password="secret", client_type=client_type)
            routes = dict(self.ROUTES); routes[("GET", PROFILE)] = api_error(90, "offline")
            client, fake = self.make(routes, creds)
            with self.assertRaises(AuthExpired) as cm:
                client.get(PROFILE)
            self.assertIn("signed in", str(cm.exception))
            self.assertEqual(fake.calls("POST", BYPASS), [], client_type)

    def test_code_90_on_the_phone_slot_still_reloads_a_fresher_disk_token(self):
        # The disk reload never touches a device (it only reads a token some other process, such
        # as a remote sign-in, already wrote) so it must run even on the phone/gym-monster slots,
        # where only the password re-login (which WOULD touch a device) is skipped.
        calls = {"n": 0}

        def profile(request):
            calls["n"] += 1
            return api_error(90, "offline") if calls["n"] == 1 else {"appUserId": 1001}

        creds = dataclasses.replace(CREDS, password="secret", client_type="phone", token="tok-1")
        disk = dataclasses.replace(creds, token="tok-2")
        routes = dict(self.ROUTES); routes[("GET", PROFILE)] = profile
        client, fake = self.make(routes, creds, reload_credentials=lambda: disk)
        self.assertEqual(client.get(PROFILE), {"appUserId": 1001})
        self.assertEqual(client.creds.token, "tok-2")
        self.assertEqual(fake.calls("POST", BYPASS), [])

    def test_code_90_on_the_phone_slot_with_no_newer_disk_token_still_fails(self):
        creds = dataclasses.replace(CREDS, password="secret", client_type="phone", token="tok-1")
        routes = dict(self.ROUTES); routes[("GET", PROFILE)] = api_error(90, "offline")
        client, fake = self.make(routes, creds, reload_credentials=lambda: creds)  # nothing newer
        with self.assertRaises(AuthExpired):
            client.get(PROFILE)
        self.assertEqual(fake.calls("POST", BYPASS), [])


class TestUnitWhenTheLoginResponseLacksIt(unittest.TestCase):
    """Only the phone-type login returns `unit`; NANO/BIKE responses carry an unreliable
    `weightUnit` instead (verified live 2026-09-28). Never guess kg from a missing field."""

    def make(self, bypass, creds=None):
        fake = FakeSpeediance({("POST", VERIFY): {"isExist": True, "hasPwd": True}, ("POST", BYPASS): bypass,
                               ("GET", PROFILE): {"appUserId": 7}})
        client = SpeedianceClient(creds, transport=fake.transport(), min_interval=0)
        self.addCleanup(client.close)
        return client

    def test_response_unit_wins(self):
        client = self.make({"token": "t", "appUserId": 7, "unit": 1})
        self.assertEqual(client.login("a@b.c", "pw", client_type="phone", unit="kg").unit, "lb")

    def test_missing_unit_uses_the_given_unit(self):
        client = self.make({"token": "t", "appUserId": 7, "weightUnit": 0})
        self.assertEqual(client.login("a@b.c", "pw", client_type="nano", unit="lb").unit, "lb")

    def test_missing_unit_keeps_the_known_unit_on_relogin(self):
        creds = dataclasses.replace(CREDS, unit="lb", client_type="nano", password="pw")
        client = self.make({"token": "t2", "appUserId": 1001, "weightUnit": 0}, creds)
        self.assertEqual(client.login(creds.email, "pw", client_type="nano").unit, "lb")

    def test_missing_unit_and_nothing_known_fails_clearly(self):
        client = self.make({"token": "t", "appUserId": 7, "weightUnit": 0})
        with self.assertRaises(LoginFailed) as cm:
            client.login("a@b.c", "pw", client_type="nano")
        self.assertIn("--unit", str(cm.exception))
