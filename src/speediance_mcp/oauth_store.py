"""OAuth state for remote mode (SQLite): registered clients, pending sign-ins, authorization
codes, tokens and failed sign-in attempts. Codes and tokens are stored only as SHA-256 hashes."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (client_id TEXT PRIMARY KEY, info_json TEXT NOT NULL, created_at REAL);
CREATE TABLE IF NOT EXISTS pending (
  request_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, params_json TEXT NOT NULL,
  csrf TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS codes (
  code_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, data_json TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
  token_hash TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
  client_id TEXT NOT NULL, family TEXT NOT NULL, scopes_json TEXT NOT NULL, resource TEXT,
  expires_at REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS tokens_by_family ON tokens (family);
CREATE INDEX IF NOT EXISTS pending_by_expiry ON pending (expires_at);
CREATE INDEX IF NOT EXISTS codes_by_expiry ON codes (expires_at);
CREATE INDEX IF NOT EXISTS tokens_by_expiry ON tokens (expires_at);
CREATE TABLE IF NOT EXISTS login_failures (at REAL NOT NULL, ip TEXT NOT NULL);
"""

# /register and /authorize are unauthenticated, so their rows are capped. A pending sign-in
# lives 10 minutes; past MAX_PENDING the oldest go first (the newest request always wins, so an
# abandoned or hostile flood can't block the owner). A registered client that has never been
# issued a token is dropped after UNUSED_CLIENT_TTL; past MAX_CLIENTS registration is refused.
MAX_PENDING = 200
MAX_CLIENTS = 500
UNUSED_CLIENT_TTL = 24 * 3600


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class OAuthStore:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        # Create file with owner-only perms before sqlite3.connect to prevent default perms window
        if os.name == "posix" and not path.exists():
            fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(SCHEMA)
            columns = {r["name"] for r in self._db.execute("PRAGMA table_info(clients)")}
            if "created_at" not in columns:  # a database from before created_at existed
                self._db.execute("ALTER TABLE clients ADD COLUMN created_at REAL")
        if os.name == "posix":
            os.chmod(path, 0o600)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- registered clients --------------------------------------------------------------
    def save_client(self, client_id: str, info_json: str) -> None:
        """Register a client. Raises RuntimeError if MAX_CLIENTS are already registered."""
        now = self._clock()
        with self._lock, self._db:
            # Drop clients registered over a day ago that never got a token (NULL = pre-migration).
            self._db.execute("DELETE FROM clients WHERE (created_at IS NULL OR created_at < ?) "
                             "AND client_id NOT IN (SELECT client_id FROM tokens)", (now - UNUSED_CLIENT_TTL,))
            others = self._db.execute("SELECT COUNT(*) FROM clients WHERE client_id != ?",
                                      (client_id,)).fetchone()[0]
            if others >= MAX_CLIENTS:
                raise RuntimeError("too many registered clients")
            self._db.execute("INSERT OR REPLACE INTO clients (client_id, info_json, created_at) VALUES (?, ?, ?)",
                             (client_id, info_json, now))

    def client_json(self, client_id: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT info_json FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        return row["info_json"] if row else None

    # --- pending sign-ins ----------------------------------------------------------------
    def add_pending(self, client_id: str, params_json: str, ttl: float) -> tuple[str, str]:
        request_id, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        now = self._clock()
        with self._lock, self._db:
            self._purge_unlocked()
            excess = self._db.execute("SELECT COUNT(*) FROM pending").fetchone()[0] - (MAX_PENDING - 1)
            if excess > 0:
                self._db.execute("DELETE FROM pending WHERE request_id IN "
                                 "(SELECT request_id FROM pending ORDER BY expires_at LIMIT ?)", (excess,))
            self._db.execute("INSERT INTO pending (request_id, client_id, params_json, csrf, expires_at) "
                             "VALUES (?, ?, ?, ?, ?)", (request_id, client_id, params_json, csrf, now + ttl))
        return request_id, csrf

    def get_pending(self, request_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT client_id, params_json, csrf, expires_at FROM pending "
                                   "WHERE request_id = ?", (request_id,)).fetchone()
        if row is None or row["expires_at"] <= self._clock():
            return None
        return {"client_id": row["client_id"], "params_json": row["params_json"], "csrf": row["csrf"]}

    def pop_pending(self, request_id: str) -> dict | None:
        with self._lock, self._db:
            row = self._db.execute("SELECT client_id, params_json, csrf, expires_at FROM pending "
                                   "WHERE request_id = ?", (request_id,)).fetchone()
            self._db.execute("DELETE FROM pending WHERE request_id = ?", (request_id,))
        if row is None or row["expires_at"] <= self._clock():
            return None
        return {"client_id": row["client_id"], "params_json": row["params_json"], "csrf": row["csrf"]}

    # --- authorization codes ---------------------------------------------------------------
    def add_code(self, client_id: str, data_json: str, ttl: float) -> str:
        code = secrets.token_urlsafe(32)
        with self._lock, self._db:
            self._db.execute("INSERT INTO codes (code_hash, client_id, data_json, expires_at) VALUES (?, ?, ?, ?)",
                             (digest(code), client_id, data_json, self._clock() + ttl))
        return code

    def get_code(self, code: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT client_id, data_json, expires_at FROM codes WHERE code_hash = ?",
                                   (digest(code),)).fetchone()
        if row is None or row["expires_at"] <= self._clock():
            return None
        return {"client_id": row["client_id"], "data_json": row["data_json"], "expires_at": row["expires_at"]}

    def delete_code(self, code: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM codes WHERE code_hash = ?", (digest(code),)).rowcount > 0

    # --- tokens --------------------------------------------------------------------------------
    def issue_token(self, kind: str, client_id: str, family: str, scopes: list[str], resource: str | None,
                    ttl: float) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock, self._db:
            # Check if family is already revoked; if so, mark new token as dead on arrival
            is_revoked = bool(self._db.execute("SELECT 1 FROM tokens WHERE family = ? AND revoked = 1 LIMIT 1",
                                               (family,)).fetchone())
            self._db.execute(
                "INSERT INTO tokens (token_hash, kind, client_id, family, scopes_json, resource, expires_at, revoked) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (digest(token), kind, client_id, family, json.dumps(list(scopes)), resource, self._clock() + ttl, 1 if is_revoked else 0))
        return token

    def token_row(self, token: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tokens WHERE token_hash = ?", (digest(token),)).fetchone()
        if row is None:
            return None
        return {"kind": row["kind"], "client_id": row["client_id"], "family": row["family"],
                "scopes": json.loads(row["scopes_json"]), "resource": row["resource"],
                "expires_at": row["expires_at"], "used": bool(row["used"]), "revoked": bool(row["revoked"])}

    def mark_used(self, token: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("UPDATE tokens SET used = 1 WHERE token_hash = ? AND used = 0 AND revoked = 0",
                                   (digest(token),)).rowcount > 0

    def revoke_family(self, family: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE tokens SET revoked = 1 WHERE family = ?", (family,))

    def revoke_all(self) -> None:
        """Disconnect every remote client: delete all tokens, pending sign-ins and codes.
        Registered clients stay, so they can simply sign in again."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM tokens")
            self._db.execute("DELETE FROM pending")
            self._db.execute("DELETE FROM codes")

    def _purge_unlocked(self) -> None:
        """Delete expired rows. Assumes lock and db context already held."""
        now = self._clock()
        self._db.execute("DELETE FROM codes WHERE expires_at <= ?", (now,))
        self._db.execute("DELETE FROM pending WHERE expires_at <= ?", (now,))
        self._db.execute("DELETE FROM tokens WHERE expires_at <= ?", (now,))
        self._db.execute("DELETE FROM login_failures WHERE at < ?", (now - 86400,))

    def purge(self) -> None:
        """Delete expired rows from codes, pending, tokens, and old login failures (>24h)."""
        with self._lock, self._db:
            self._purge_unlocked()

    # --- failed sign-ins ----------------------------------------------------------------------
    def record_failure(self, ip: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO login_failures (at, ip) VALUES (?, ?)", (self._clock(), ip))

    def failures(self, since: float, ip: str | None = None) -> list[float]:
        with self._lock:
            if ip is None:
                rows = self._db.execute("SELECT at FROM login_failures WHERE at > ? ORDER BY at", (since,))
            else:
                rows = self._db.execute("SELECT at FROM login_failures WHERE at > ? AND ip = ? ORDER BY at",
                                        (since, ip))
            return [r["at"] for r in rows.fetchall()]
