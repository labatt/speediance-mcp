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
    "get_preferences", "set_preferences", "remember_fact", "forget_fact", "list_facts", "import_facts",
    "unschedule_workout", "get_training_stats", "compare_sessions", "browse_programs", "get_exercise",
    "list_accessories", "get_heart_rate", "get_muscle_balance",
    "log_off_machine_workout", "get_off_machine_log", "delete_off_machine_day",
    "get_recovery", "get_readiness_trend", "get_body_metrics",
}


class TestServer(unittest.TestCase):
    def test_exactly_the_35_tools_with_descriptions(self):
        app, _ = make_app(self)
        tools = asyncio.run(build_server(app).list_tools())
        self.assertEqual({t.name for t in tools}, EXPECTED_TOOLS)
        self.assertTrue(all(t.description for t in tools))
        detail = next(t for t in tools if t.name == "get_session_detail")
        schema = detail.input_schema if hasattr(detail, "input_schema") else detail.inputSchema
        self.assertEqual(set(schema["properties"]), {"training_id", "type"})

    def test_instructions_cover_the_curated_facts(self):
        from speediance_mcp.server import INSTRUCTIONS
        for phrase in ("constraints.hard", "before building any workout", "conflicts", "out loud",
                       "legacyToReview", "supersedes", "import_facts", "list_facts", "600",
                       "legacyUnreviewed", "treat injury ones as hard constraints", "scope"):
            self.assertIn(phrase, INSTRUCTIONS)
        self.assertNotIn("when convenient", INSTRUCTIONS.lower())

    def test_http_client_logs_are_quiet(self):
        import logging
        app, _ = make_app(self)
        build_server(app)
        self.assertEqual(logging.getLogger("httpx2").level, logging.WARNING)

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

    def test_login_remembers_password_by_default_without_prompting(self):
        routes = fx.standard_routes()
        routes[("POST", "/api/app/v2/login/verifyIdentity")] = {"isExist": True, "hasPwd": True}
        routes[("POST", "/api/app/v2/login/byPass")] = {"token": "new-token", "appUserId": 1001, "unit": 1}
        factory, _ = self.fake_client_factory(routes)
        with mock.patch.object(cli, "_make_client", factory), \
                mock.patch("getpass.getpass", return_value="pw"), \
                mock.patch("builtins.input", side_effect=AssertionError("must not prompt")):
            code, out, _ = self.run_cli("login", "--email", "athlete@example.com")
        self.assertEqual(code, 0)
        self.assertEqual(load_credentials(self.home).password, "pw")
        self.assertIn("--no-remember", out)
        self.assertNotIn("pw\n", out)

    def test_login_client_type_flag_is_saved_and_shown(self):
        routes = fx.standard_routes()
        routes[("POST", "/api/app/v2/login/verifyIdentity")] = {"isExist": True, "hasPwd": True}
        routes[("POST", "/api/app/v2/login/byPass")] = {"token": "new-token", "appUserId": 1001, "unit": 1}
        factory, fake = self.fake_client_factory(routes)
        with mock.patch.object(cli, "_make_client", factory), mock.patch("getpass.getpass", return_value="pw"):
            code, out, _ = self.run_cli("login", "--email", "athlete@example.com", "--client-type", "nano")
        self.assertEqual(code, 0)
        self.assertEqual(load_credentials(self.home).client_type, "nano")
        self.assertEqual(fake.calls("POST", "/api/app/v2/login/byPass")[0].headers["App_type"], "NANO")
        _, out, _ = self.run_cli("status")
        self.assertIn("nano", out)

    def test_login_rejects_unknown_client_type(self):
        with self.assertRaises(SystemExit):
            self.run_cli("login", "--email", "a@b.c", "--client-type", "hardware2")

    def test_login_unit_flag_is_saved_for_a_slot_that_doesnt_report_it(self):
        routes = {("POST", "/api/app/v2/login/verifyIdentity"): {"isExist": True, "hasPwd": True},
                  ("POST", "/api/app/v2/login/byPass"): {"token": "new-token", "appUserId": 1001, "weightUnit": 0}}
        routes.update({k: v for k, v in fx.standard_routes().items() if k[0] == "GET"})
        factory, _ = self.fake_client_factory(routes)
        with mock.patch.object(cli, "_make_client", factory), mock.patch("getpass.getpass", return_value="pw"):
            code, _, err = self.run_cli("login", "--email", "athlete@example.com", "--client-type", "nano",
                                        "--unit", "lb")
        self.assertEqual(code, 0, err)
        self.assertEqual(load_credentials(self.home).unit, "lb")

    def test_login_without_a_terminal_explains_instead_of_crashing(self):
        with mock.patch("builtins.input", side_effect=EOFError):
            code, _, err = self.run_cli("login")
        self.assertEqual(code, 2)
        self.assertIn("interactive terminal", err)
        self.assertNotIn("Traceback", err)

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

    def test_revoke_disconnects_every_remote_client_but_keeps_registrations(self):
        from speediance_mcp.oauth_store import OAuthStore
        store = OAuthStore(self.home / "oauth.db")
        store.save_client("c1", "{}")
        token = store.issue_token("refresh", "c1", "fam", [], None, ttl=3600)
        code = store.add_code("c1", "{}", ttl=300)
        pending_id, _ = store.add_pending("c1", "{}", ttl=600)
        store.close()
        code_, out, _ = self.run_cli("revoke")
        self.assertEqual(code_, 0)
        self.assertIn("Disconnected every remote client (claude.ai and others). They'll need to sign in again.", out)
        store = OAuthStore(self.home / "oauth.db")
        self.addCleanup(store.close)
        self.assertIsNone(store.token_row(token))
        self.assertIsNone(store.get_code(code))
        self.assertIsNone(store.get_pending(pending_id))
        self.assertEqual(store.client_json("c1"), "{}")
        self.assertNotIn(token, out)

    def test_revoke_without_an_oauth_database(self):
        code, out, _ = self.run_cli("revoke")
        self.assertEqual(code, 0)
        self.assertIn("No remote connections.", out)
        self.assertFalse((self.home / "oauth.db").exists())

    def test_http_mode_requires_public_url(self):
        code, _, err = self.run_cli("serve", "--http")
        self.assertEqual(code, 2)
        self.assertIn("--public-url", err)

    def test_http_mode_refuses_without_login(self):
        code, _, err = self.run_cli("serve", "--http", "--public-url", "https://mcp.example.com")
        self.assertEqual(code, 1)
        self.assertIn("speediance-mcp login", err)

    def test_http_mode_never_trusts_proxy_headers_from_the_network(self):
        # Only signin._client_ip's own trust of X-Real-IP (from the local reverse proxy) should
        # identify callers for the sign-in rate limit — uvicorn itself must not also honour
        # X-Forwarded-For from arbitrary network peers, or a remote attacker could forge either
        # header and dodge the lockout.
        save_credentials(CREDS, self.home)
        with mock.patch("uvicorn.run") as run:
            code, _, _ = self.run_cli("serve", "--http", "--public-url", "https://mcp.example.com")
        self.assertEqual(code, 0)
        run.assert_called_once()
        _, kwargs = run.call_args
        self.assertEqual(kwargs["host"], "127.0.0.1")
        self.assertEqual(kwargs["port"], 8765)
        self.assertEqual(kwargs["proxy_headers"], False)
        self.assertEqual(kwargs["forwarded_allow_ips"], "")


class TestCredentialReload(unittest.TestCase):
    def test_running_server_picks_up_a_fresh_login(self):
        import dataclasses
        routes = fx.standard_routes()
        history = routes[("GET", fx.HISTORY_PATH)]
        routes[("GET", fx.HISTORY_PATH)] = lambda req: (history(req) if req.headers["Token"] == "tok-B"
                                                        else api_error(91, "Login expired"))
        app, fake = make_app(self, routes, creds=dataclasses.replace(CREDS, token="tok-A"))
        server = build_server(app)
        with self.assertRaises(ToolError):
            asyncio.run(server.call_tool("get_calendar", {"month": "2026-08"}))
        save_credentials(dataclasses.replace(CREDS, token="tok-B"), app.home)  # user re-ran `speediance-mcp login`
        asyncio.run(server.call_tool("get_calendar", {"month": "2026-08"}))
        self.assertEqual(fake.calls("GET", fx.HISTORY_PATH)[-1].headers["Token"], "tok-B")
