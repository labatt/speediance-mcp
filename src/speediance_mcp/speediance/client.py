"""HTTP client for the private Speediance mobile-app API.

Ported from hbui3/UnofficialSpeedianceWorkoutManager (MIT). Unofficial: the API can change
without notice.
"""

from __future__ import annotations

import locale
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

import httpx2

from ..config import Credentials

# The API version-gates content on the app version this client declares. 41000 = v4.10.0.
# Do NOT raise this or change it rapidly: the server answers implausibly-high or rapidly
# changing codes with an anti-abuse "Invalid nonce string" throttle.
VERSION_CODE = "41000"
HOSTS = {"Global": "api2.speediance.com", "EU": "euapi.speediance.com"}
MOBILE_DEVICES = ('{"brand":"google","device":"emulator64_x86_64_arm64","deviceType":'
                  '"sdk_gphone64_x86_64","os":"","os_version":"31","manufacturer":"Google"}')
USER_AGENT = "Dart/3.9 (dart:io)"
AUTH_CODES = {91}          # token expired
DISPLACED_CODE = 90        # "offline": another login of the same client type took this session

# Speediance keeps ONE live session per client type (the App_type header), not per account or
# device (verified live 2026-09-27). A login takes over its type's slot and signs out whoever
# held it. So logins may use a slot the user doesn't otherwise occupy; every other request is
# sent as the phone app (SOFTWARE, VERSION_CODE), which works with a token from any slot.
#   name           App_type    login Versioncode   whose slot it is
CLIENT_TYPES = {
    "phone":       ("SOFTWARE", VERSION_CODE),  # the Speediance phone app
    "gym-monster": ("HARDWARE", "1"),           # the Gym Monster itself
    "nano":        ("NANO", "1"),               # Gym Nano
    "bike":        ("BIKE", "1"),               # Speediance bike
}
DEFAULT_CLIENT_TYPE = "bike"
# Re-logging in when displaced (code 90) is only safe on a slot no device of the user's needs;
# on the phone's or the machine's slot it would sign that device out again, back and forth.
RELOGIN_ON_DISPLACEMENT = {"nano", "bike"}


class SpeedianceError(Exception):
    """Base for Speediance failures."""


class AuthExpired(SpeedianceError):
    """Not logged in, or the token was invalidated (e.g. by a phone sign-in)."""


class LoginFailed(SpeedianceError):
    """Email/password login was refused."""


class NotSignedIn(SpeedianceError):
    """This server has no stored Speediance login (credentials.json is missing)."""


class NotFound(SpeedianceError):
    """The route does not exist (HTTP 404)."""


class WrongNamespace(SpeedianceError):
    """'Sorry. You do not have access.' — the id belongs to a different session namespace."""


class ServerError(SpeedianceError):
    """HTTP 5xx from Speediance."""


class Rejected(SpeedianceError):
    """The API answered with a non-zero body code."""

    def __init__(self, code: Any, message: str):
        super().__init__(f"Speediance rejected the request (code {code}): {message}")
        self.code = code
        self.api_message = message


# Exercise and workout names come back in the language Accept-Language names. The server only
# honours a bare code: "en-GB", a language it doesn't carry ("ja") and no header at all all
# answer in Chinese (verified live 2026-10-04). So send only codes known to work, else English.
SUPPORTED_LANGUAGES = ("en", "de", "fr", "es", "it", "ko")
DEFAULT_LANGUAGE = "en"


def resolve_language(preferred: str | None = None) -> str:
    """The language to ask Speediance for: the account's stored choice, else $SPEEDIANCE_LANGUAGE,
    else the system locale; any language Speediance doesn't carry becomes English."""
    try:
        system = locale.getlocale()[0]
    except ValueError:
        system = None
    for raw in (preferred, os.environ.get("SPEEDIANCE_LANGUAGE"), os.environ.get("LC_ALL"),
                os.environ.get("LC_MESSAGES"), os.environ.get("LANG"), system):
        code = re.split(r"[-_.@]", (raw or "").strip(), maxsplit=1)[0].lower()
        if code and code not in ("c", "posix"):
            return code if code in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    return DEFAULT_LANGUAGE


def _tz_headers() -> dict[str, str]:
    now = datetime.now().astimezone()
    offset = now.utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    tz_name = os.environ.get("TZ") or (time.tzname[0] if time.tzname else "GMT")
    # HTTP headers must be ASCII; localized Windows zone names ("Mitteleuropäische Zeit") aren't.
    # Utc_offset still carries the real offset.
    if not tz_name.isascii():
        tz_name = "GMT"
    return {"Timezone": tz_name, "Utc_offset": f"{sign}{minutes // 60:02d}{minutes % 60:02d}"}


class SpeedianceClient:
    def __init__(self, creds: Credentials | None, *, region: str | None = None, transport=None,
                 min_interval: float = 1.0, on_credentials: Callable[[Credentials], None] | None = None,
                 reload_credentials: Callable[[], Credentials | None] | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 timeout: float = 30.0):
        self.creds = creds
        self.region = region or (creds.region if creds else "Global")
        if self.region not in HOSTS:
            raise ValueError(f"region must be one of {sorted(HOSTS)}")
        self._http = httpx2.Client(base_url=f"https://{HOSTS[self.region]}", transport=transport,
                                   timeout=timeout)
        self._min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self._throttle_lock = threading.Lock()
        self._auth_lock = threading.Lock()
        self._on_credentials = on_credentials
        self._reload_credentials = reload_credentials

    @property
    def language(self) -> str:
        """The language Speediance answers this client in (see resolve_language)."""
        return resolve_language(self.creds.language if self.creds else None)

    def _throttle(self) -> None:
        with self._throttle_lock:
            now = self._clock()
            if self._last is not None:
                wait = self._min_interval - (now - self._last)
                if wait > 0:
                    self._sleep(wait)
                    now = self._clock()
            self._last = now

    def _headers(self, auth: bool, login_as: str | None = None) -> dict[str, str]:
        app_type, version_code = CLIENT_TYPES[login_as] if login_as else ("SOFTWARE", VERSION_CODE)
        headers = {
            "Timestamp": str(int(time.time() * 1000)),
            "Versioncode": version_code,
            "Mobiledevices": MOBILE_DEVICES,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "App_type": app_type,
            "Accept-Language": self.language,
            **_tz_headers(),
        }
        if auth and self.creds:
            headers["App_user_id"] = self.creds.user_id
            headers["Token"] = self.creds.token
        return headers

    def request(self, method: str, path: str, *, params=None, json=None, auth: bool = True,
                _retry: bool = True, _login_as: str | None = None) -> Any:
        if auth and not self.creds:
            raise AuthExpired("Not logged in to Speediance.")
        self._throttle()
        sent_token = self.creds.token if auth and self.creds else None
        try:
            resp = self._http.request(method, path, params=params, json=json, headers=self._headers(auth, _login_as))
        except httpx2.TransportError as exc:
            raise SpeedianceError(f"Could not reach Speediance: {exc}") from exc
        if resp.status_code == 404:
            raise NotFound(f"Speediance has no route {method} {path}")
        if resp.status_code >= 500:
            raise ServerError(f"Speediance server error (HTTP {resp.status_code})")
        try:
            body = resp.json()
        except ValueError as exc:
            raise SpeedianceError(f"Unexpected non-JSON response (HTTP {resp.status_code})") from exc
        is_dict = isinstance(body, dict)
        code = body.get("code") if is_dict else None
        message = (body.get("message") or body.get("msg") or "") if is_dict else ""
        if resp.status_code == 401 or code in AUTH_CODES:
            if auth and _retry and self._relogin(sent_token):
                return self.request(method, path, params=params, json=json, auth=auth, _retry=False)
            raise AuthExpired(message or "Speediance login expired.")
        if code == DISPLACED_CODE and auth:
            client_type = self.creds.client_type if self.creds else DEFAULT_CLIENT_TYPE
            if (_retry and self._relogin(sent_token, allow_password=client_type in RELOGIN_ON_DISPLACEMENT)):
                return self.request(method, path, params=params, json=json, auth=auth, _retry=False)
            raise AuthExpired(f"Speediance was signed in elsewhere with the same client type ({client_type}), "
                              "which ended this session.")
        if code not in (None, 0):
            if "do not have access" in message.lower():
                raise WrongNamespace(message)
            raise Rejected(code, message)
        return body.get("data") if is_dict else body

    def get(self, path: str, params=None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: Any) -> Any:
        return self.request("POST", path, json=json)

    def delete(self, path: str, params=None) -> Any:
        return self.request("DELETE", path, params=params)

    def _relogin(self, stale_token: str | None, allow_password: bool = True) -> bool:
        """Recover from an auth failure of a request sent with `stale_token`. True means retry.

        The disk reload never touches a device (it only reads a token some other process, such as
        a remote sign-in, already wrote), so it always runs. Only the password re-login can steal a
        device's slot (code 90 on the phone's or the machine's client type) — `allow_password=False`
        skips it there."""
        with self._auth_lock:
            if self.creds and self.creds.token != stale_token:
                return True  # another thread already refreshed the token
            if self._reload_credentials:
                # The user may have re-run `speediance-mcp login` while this process was running.
                disk = self._reload_credentials()
                if disk is None:
                    # credentials.json is gone: the user ran `speediance-mcp logout`. Never undo
                    # that with the password this process still holds in memory.
                    return False
                if disk.token and disk.token != stale_token:
                    self.creds = disk
                    return True
            if not allow_password:
                return False
            if not (self.creds and self.creds.password):
                return False
            try:
                self.login(self.creds.email, self.creds.password, remember=True,
                           device_type=self.creds.device_type, client_type=self.creds.client_type)
            except SpeedianceError:
                return False
            return True

    def login(self, email: str, password: str, *, remember: bool = False, device_type: int = 1,
              client_type: str | None = None, unit: str | None = None,
              language: str | None = None) -> Credentials:
        client_type = client_type or DEFAULT_CLIENT_TYPE
        if client_type not in CLIENT_TYPES:
            raise ValueError(f"client_type must be one of {', '.join(CLIENT_TYPES)}")
        try:
            verify = self.request("POST", "/api/app/v2/login/verifyIdentity",
                                  json={"type": 2, "userIdentity": email}, auth=False, _login_as=client_type)
            if isinstance(verify, dict):
                if verify.get("isExist") is False:
                    raise LoginFailed("No Speediance account uses that email. Register in the Speediance app first.")
                if verify.get("hasPwd") is False:
                    raise LoginFailed("That account has no password yet. Set one in the Speediance app first.")
            data = self.request("POST", "/api/app/v2/login/byPass",
                                json={"userIdentity": email, "password": password, "type": 2}, auth=False,
                                _login_as=client_type)
        except Rejected as exc:
            raise LoginFailed(exc.api_message or "Login was rejected.") from exc
        except AuthExpired as exc:
            raise LoginFailed(str(exc)) from exc
        if not isinstance(data, dict) or not data.get("token") or not data.get("appUserId"):
            raise LoginFailed("Speediance's login response had no token.")
        # Only the phone-type login reports the account's display unit (`unit`: 1 = lb). NANO/BIKE
        # responses carry `weightUnit` instead, which is 0 even on lb accounts (verified live
        # 2026-09-28) -- so never infer kg from a missing field: keep what we know.
        if data.get("unit") is not None:
            unit_value = "lb" if data.get("unit") == 1 else "kg"
        else:
            unit_value = unit or (self.creds.unit if self.creds else None)
        if unit_value not in ("lb", "kg"):
            raise LoginFailed("Speediance doesn't report whether this account uses lb or kg for this client type. "
                              "Sign in again with --unit lb or --unit kg (whatever the Speediance app shows).")
        self.creds = Credentials(
            email=email, token=data["token"], user_id=str(data["appUserId"]),
            unit=unit_value, region=self.region,
            device_type=device_type, password=password if remember else None, client_type=client_type,
            language=language or (self.creds.language if self.creds else None),
        )
        if self._on_credentials:
            self._on_credentials(self.creds)
        return self.creds

    def logout(self) -> None:
        try:
            self.request("POST", "/api/app/login/logout", json={}, _retry=False)
        except SpeedianceError:
            pass

    def close(self) -> None:
        self._http.close()
