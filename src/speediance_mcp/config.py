"""Stored Speediance credentials (credentials.json in the data dir, owner-only)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .paths import data_dir

FILENAME = "credentials.json"


@dataclass
class Credentials:
    email: str
    token: str
    user_id: str
    unit: str = "kg"          # "kg" | "lb" — from the login response, the account's display unit
    region: str = "Global"    # "Global" | "EU"
    device_type: int = 1      # 1 = Gym Monster, 2 = Gym Pal
    password: str | None = None  # absent when the user signed in with --no-remember
    client_type: str = "bike"   # which Speediance session slot logins use; see client.CLIENT_TYPES
    language: str | None = None  # names come back in this language; None = $SPEEDIANCE_LANGUAGE or the locale


def credentials_path(home: Path | None = None) -> Path:
    return (home or data_dir()) / FILENAME


def load_credentials(home: Path | None = None) -> Credentials | None:
    """The stored credentials, or None if absent or unreadable."""
    try:
        raw = json.loads(credentials_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    known = {f.name for f in fields(Credentials)}
    try:
        return Credentials(**{k: v for k, v in raw.items() if k in known})
    except TypeError:
        return None


def save_credentials(creds: Credentials, home: Path | None = None) -> Path:
    """Write credentials atomically, readable only by the owner on POSIX."""
    path = credentials_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(asdict(creds), handle, indent=2)
    os.replace(tmp, path)
    if os.name == "posix":
        os.chmod(path, 0o600)
    return path


def clear_credentials(home: Path | None = None) -> bool:
    try:
        credentials_path(home).unlink()
        return True
    except FileNotFoundError:
        return False
