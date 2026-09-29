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

    def test_client_type_round_trips_and_defaults_to_bike(self):
        config.save_credentials(Credentials(email="a@b.c", token="t", user_id="1", client_type="nano"), self.home)
        self.assertEqual(config.load_credentials(self.home).client_type, "nano")
        self.assertEqual(Credentials(email="a@b.c", token="t", user_id="1").client_type, "bike")

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
