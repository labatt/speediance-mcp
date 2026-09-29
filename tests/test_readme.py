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
