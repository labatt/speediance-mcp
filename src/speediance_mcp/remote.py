"""Remote mode: the MCP server over streamable HTTP at /mcp, behind OAuth (spec §4.2).

Run it with `speediance-mcp serve --http --public-url https://...` behind a TLS reverse proxy.
The SDK serves the OAuth endpoints; our provider, sign-in page and store supply the policy.
"""

from __future__ import annotations

import time
from typing import Callable, Iterable
from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings

from .config import save_credentials
from .oauth import DEFAULT_REDIRECT_HOSTS, SpeedianceOAuthProvider
from .oauth_store import OAuthStore
from .server import build_server
from .signin import LoginGate, sign_in_handlers
from .speediance.client import NotSignedIn, SpeedianceClient


def speediance_verifier(app, transport=None) -> Callable[[str, str], None]:
    """Verify a sign-in by logging in to Speediance with the deployment's stored settings.

    A successful login also refreshes the stored Speediance token (spec §4.2), which is also what
    lets a running server on the phone/gym-monster slot recover after a displacement (client.py's
    `_relogin` disk-reload path picks this fresh token up on its next request)."""
    def verify(email: str, password: str) -> None:
        creds = app.credentials()
        if creds is None:
            raise NotSignedIn("This server isn't signed in to Speediance any more. Run "
                              "`speediance-mcp login` on the server.")
        client = SpeedianceClient(creds, transport=transport or getattr(app, "_transport", None),
                                  min_interval=getattr(app, "_min_interval", 1.0),
                                  on_credentials=lambda c: save_credentials(c, app.home))
        try:
            client.login(email, password, remember=creds.password is not None, device_type=creds.device_type,
                         client_type=creds.client_type)
        finally:
            client.close()
    return verify


def build_remote_app(app, public_url: str, *, verify: Callable[[str, str], None] | None = None,
                     clock: Callable[[], float] = time.time,
                     allowed_redirect_hosts: Iterable[str] | None = None):
    parsed = urlparse(public_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("--public-url must be the https:// address clients use, e.g. https://mcp.example.com")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("--public-url must be the site root, e.g. https://mcp.example.com")
    creds = app.credentials()
    if creds is None:
        raise ValueError("This server isn't signed in to Speediance. Run `speediance-mcp login` on this machine first.")
    public_url = public_url.rstrip("/")
    store = OAuthStore(app.home / "oauth.db", clock=clock)
    if allowed_redirect_hosts is None:
        provider = SpeedianceOAuthProvider(store, public_url, clock=clock)
    else:
        provider = SpeedianceOAuthProvider(store, public_url, clock=clock,
                                           allowed_redirect_hosts=tuple(DEFAULT_REDIRECT_HOSTS) +
                                           tuple(allowed_redirect_hosts))
    gate = LoginGate(store, verify or speediance_verifier(app), creds.email, clock=clock)
    server = build_server(
        app,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=public_url,
            resource_server_url=f"{public_url}/mcp",
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
            validate_token_resource=False,
        ),
    )
    show, submit = sign_in_handlers(provider, store, gate)
    server.custom_route("/login", methods=["GET"])(show)
    server.custom_route("/login", methods=["POST"])(submit)
    # Host headers arrive lowercased and without the default port; match that form.
    host = parsed.netloc.lower()
    if host.endswith(":443"):
        host = host[:-len(":443")]
    return server.streamable_http_app(transport_security=TransportSecuritySettings(
        allowed_hosts=[host], allowed_origins=[public_url, "https://claude.ai", "https://claude.com"]))
