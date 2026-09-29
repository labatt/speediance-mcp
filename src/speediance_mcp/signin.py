"""The remote connector's sign-in page (spec §4.2, §9).

Asks for the Speediance email and password, accepts only the account this server was set up
with, verifies the password by logging in to Speediance, and then hands the pending OAuth
request back to the provider. CSRF-protected per request and rate-limited: 5 failures per IP
and 20 overall per 15 minutes, with 1/2/4/8 s backoff between one IP's attempts.
"""

from __future__ import annotations

import html
import ipaddress
import json
import math
import secrets
import threading
import time
from string import Template
from typing import Callable
from urllib.parse import urlparse

import anyio
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .oauth import SpeedianceOAuthProvider
from .oauth_store import OAuthStore
from .speediance.client import LoginFailed, NotSignedIn, SpeedianceError

WINDOW = 15 * 60
MAX_PER_IP = 5
MAX_GLOBAL = 20
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # No form-action: browsers apply it to the redirect after sign-in (to claude.ai), too.
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; "
                               "base-uri 'none'",
}
WRONG_ACCOUNT = "That isn't the Speediance account this server is set up for."
WRONG_PASSWORD = "Speediance didn't accept that email and password."
UNREACHABLE = "Couldn't reach Speediance just now. Try again in a minute."
EXPIRED = "This sign-in link has expired. Start again from Claude."
NOT_SIGNED_IN = ("This server isn't signed in to Speediance. Its owner needs to run "
                 "`speediance-mcp login` on the server.")
CONNECT_WARNING = "Only continue if you just clicked Connect in Claude yourself."
# Sign-ins run the blocking Speediance login in a worker thread. They get their own small
# limiter (created lazily: a CapacityLimiter needs a running event loop) so a burst of them
# can't use up the default thread pool that tool calls run on.
SIGN_IN_LIMITER: anyio.CapacityLimiter | None = None

PAGE = Template("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect Speediance</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:48px 16px;display:flex;justify-content:center}
main{max-width:380px;width:100%}h1{font-size:1.3rem;margin:0 0 8px}p{color:#9aa4ae;font-size:.9rem;line-height:1.4}
label{display:block;margin:16px 0 6px;font-size:.85rem}
input{width:100%;box-sizing:border-box;padding:10px;border-radius:6px;border:1px solid #30363d;background:#161b22;color:#e6edf3;font-size:1rem}
button{margin-top:20px;width:100%;padding:11px;border:0;border-radius:6px;background:#2ea043;color:#fff;font-weight:600;font-size:1rem}
.error{background:#3d1d20;border:1px solid #f85149;color:#ffa198;padding:10px;border-radius:6px;font-size:.9rem;margin-top:12px}
</style></head><body><main>
<h1>Connect $client to Speediance</h1>
<p>Sign in with the Speediance account this server was set up for. Your password goes only to Speediance.</p>
$notice
$error
$form
</main></body></html>""")

FORM = Template("""<form method="post" action="/login">
<input type="hidden" name="request" value="$request"><input type="hidden" name="csrf" value="$csrf">
<label for="email">Speediance email</label>
<input id="email" name="email" type="email" autocomplete="username" required autofocus>
<label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Sign in</button>
</form>""")


class LoginGate:
    def __init__(self, store: OAuthStore, verify: Callable[[str, str], None], account_email: str, *,
                 clock: Callable[[], float] = time.time):
        self.store = store
        self.verify = verify
        # The exact string Speediance is told to log in as (never the caller-typed one: Unicode
        # case-folding variants of the account email must never reach Speediance).
        self._account_email = account_email.strip()
        self.account_email = self._account_email.lower()
        self._clock = clock
        # Single-account server: serializing sign-ins is fine, and it closes a rate-limit-bypass
        # race where concurrent requests could all pass the check before any of them records a
        # failure. attempt() holds this for its whole check -> verify -> record sequence.
        self._lock = threading.Lock()

    def wait_seconds(self, ip: str) -> int:
        now = self._clock()
        since = now - WINDOW
        mine = self.store.failures(since, ip)
        everyone = self.store.failures(since)
        if len(mine) >= MAX_PER_IP:
            return math.ceil(mine[0] + WINDOW - now)
        if len(everyone) >= MAX_GLOBAL:
            return math.ceil(everyone[0] + WINDOW - now)
        if mine:
            return max(0, math.ceil(mine[-1] + 2 ** (len(mine) - 1) - now))
        return 0

    def attempt(self, ip: str, email: str, password: str) -> str | None:
        with self._lock:
            wait = self.wait_seconds(ip)
            if wait > 0:
                when = f"{wait} seconds" if wait < 120 else f"{math.ceil(wait / 60)} minutes"
                return f"Too many sign-in attempts. Try again in {when}."
            if email.strip().lower() != self.account_email:
                self.store.record_failure(ip)
                return WRONG_ACCOUNT
            try:
                self.verify(self._account_email, password)
            except NotSignedIn:
                return NOT_SIGNED_IN  # the owner's problem, not a wrong password: don't count it
            except LoginFailed:
                self.store.record_failure(ip)
                return WRONG_PASSWORD
            except SpeedianceError:
                return UNREACHABLE
            return None


def _client_ip(request: Request) -> str:
    """The key failed sign-ins are counted under."""
    peer = request.client.host if request.client else "unknown"
    # Behind the reverse proxy on this machine trust its X-Real-IP; never a remote client's.
    if peer in ("127.0.0.1", "::1"):
        return _rate_key(request.headers.get("x-real-ip") or peer)
    return _rate_key(peer)


def _rate_key(address: str) -> str:
    """IPv6 callers are keyed by their /64: one subscriber usually holds a whole /64 and could
    otherwise rotate through it to dodge the per-IP limit. IPv4 (and IPv4-mapped) stay as-is."""
    try:
        ip = ipaddress.ip_address(address.strip())
    except ValueError:
        return address
    if ip.version == 6 and ip.ipv4_mapped is None:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return address


def _redirect_host(pending: dict) -> str:
    """The host the pending request's redirect_uri points at (spec: shown on the page so the
    user knows where they'll be sent, since anyone can dynamically-register a client name)."""
    params = json.loads(pending["params_json"])
    return urlparse(params.get("redirect_uri", "")).hostname or ""


def _client_name(store: OAuthStore, client_id: str, fallback: str) -> str:
    info = store.client_json(client_id)
    name = json.loads(info).get("client_name") if info else None
    return name or fallback or "Claude"


def _page(store: OAuthStore, request_id: str, pending: dict | None, error: str | None, status: int) -> HTMLResponse:
    if pending is None:
        form, client, notice = "", "Claude", ""
    else:
        form = FORM.substitute(request=html.escape(request_id), csrf=html.escape(pending["csrf"]))
        host = _redirect_host(pending)
        client = _client_name(store, pending["client_id"], host)
        notice = (f"<p>After you sign in you'll be returned to <strong>{html.escape(host)}</strong>.</p>"
                  if host else "") + f"<p>{html.escape(CONNECT_WARNING)}</p>"
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    body = PAGE.substitute(client=html.escape(client), notice=notice, error=error_html, form=form)
    return HTMLResponse(body, status_code=status, headers=SECURITY_HEADERS)


def sign_in_handlers(provider: SpeedianceOAuthProvider, store: OAuthStore, gate: LoginGate):
    async def show(request: Request) -> Response:
        request_id = request.query_params.get("request", "")
        pending = store.get_pending(request_id)
        if pending is None:
            return _page(store, request_id, None, EXPIRED, 400)
        return _page(store, request_id, pending, None, 200)

    async def submit(request: Request) -> Response:
        form = await request.form()
        request_id = str(form.get("request", ""))
        pending = store.get_pending(request_id)
        if pending is None:
            return _page(store, request_id, None, EXPIRED, 400)
        # Compare as bytes: secrets.compare_digest raises TypeError on a str/str comparison
        # where either side has non-ASCII characters.
        if not secrets.compare_digest(str(form.get("csrf", "")).encode(), pending["csrf"].encode()):
            return _page(store, request_id, None, EXPIRED, 400)
        global SIGN_IN_LIMITER
        if SIGN_IN_LIMITER is None:
            SIGN_IN_LIMITER = anyio.CapacityLimiter(2)
        error = await anyio.to_thread.run_sync(gate.attempt, _client_ip(request), str(form.get("email", "")),
                                               str(form.get("password", "")), limiter=SIGN_IN_LIMITER)
        if error:
            return _page(store, request_id, pending, error, 429 if error.startswith("Too many") else 401)
        location = provider.complete_sign_in(request_id)
        if location is None:
            return _page(store, request_id, None, EXPIRED, 400)
        return RedirectResponse(location, status_code=302, headers=SECURITY_HEADERS)

    return show, submit
