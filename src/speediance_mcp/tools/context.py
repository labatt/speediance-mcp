"""What every tool gets: stored credentials, the Speediance API and the coaching memory."""

from __future__ import annotations

import datetime as dt
import threading
from pathlib import Path
from typing import Callable

from mcp.server.mcpserver.exceptions import ToolError

from ..config import Credentials, load_credentials, save_credentials
from ..memory import Memory
from ..paths import data_dir
from ..speediance.api import SpeedianceAPI
from ..speediance.client import SpeedianceClient

LOGIN_HINTS = {
    "local": "Not signed in to Speediance, or the session expired. Run `speediance-mcp login` "
             "in a terminal, then try again.",
    "remote": "The Speediance session expired. Reconnect the Speediance connector in Claude, then try again.",
}


class App:
    def __init__(self, home: Path | None = None, *, mode: str = "local", transport=None,
                 min_interval: float = 1.0, today: Callable[[], dt.date] | None = None):
        self.home = Path(home) if home else data_dir()
        self.mode = mode
        self._transport = transport
        self._min_interval = min_interval
        self._today = today
        self._api: SpeedianceAPI | None = None
        self._memory: Memory | None = None
        self._lock = threading.Lock()

    @property
    def login_hint(self) -> str:
        return LOGIN_HINTS[self.mode]

    def credentials(self) -> Credentials | None:
        return load_credentials(self.home)

    @property
    def api(self) -> SpeedianceAPI:
        with self._lock:
            if self._api is None:
                creds = load_credentials(self.home)
                if creds is None:
                    raise ToolError(self.login_hint)
                client = SpeedianceClient(creds, transport=self._transport, min_interval=self._min_interval,
                                          on_credentials=lambda c: save_credentials(c, self.home),
                                          reload_credentials=lambda: load_credentials(self.home))
                self._api = SpeedianceAPI(client, self.home, today=self._today)
            return self._api

    @property
    def memory(self) -> Memory:
        with self._lock:
            if self._memory is None:
                self._memory = Memory(self.home / "speediance-mcp.db")
            return self._memory

    def close(self) -> None:
        with self._lock:
            if self._memory is not None:
                self._memory.close()
                self._memory = None
            if self._api is not None:
                self._api.client.close()
                self._api = None
