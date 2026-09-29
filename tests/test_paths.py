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

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_new_data_dir_is_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "home"
            with mock.patch.dict(os.environ, {paths.HOME_ENV: str(target)}):
                paths.data_dir()
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_existing_data_dir_permissions_are_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "home"
            target.mkdir(mode=0o755)
            os.chmod(target, 0o755)
            with mock.patch.dict(os.environ, {paths.HOME_ENV: str(target)}):
                paths.data_dir()
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
