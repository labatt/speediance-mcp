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
    """The data dir, created owner-only (0700 on POSIX) if missing; an existing dir's permissions are
    left alone. SPEEDIANCE_MCP_HOME overrides it."""
    override = os.environ.get(HOME_ENV)
    path = Path(override) if override else default_dir(sys.platform, os.environ, Path.home())
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path
