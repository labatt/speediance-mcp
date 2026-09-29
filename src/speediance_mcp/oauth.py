"""OAuth authorization server for remote mode, backed by OAuthStore (spec §4.2, §9).

The MCP SDK serves the endpoints (metadata, /register, /authorize, /token, /revoke) and
enforces PKCE and code expiry; this provider decides what codes and tokens mean. /authorize
sends the user to our own sign-in page (signin.py). Access tokens last 1 hour; refresh tokens
90 days and rotate on every use, and presenting an already-used refresh token revokes its
whole family. Scopes aren't used.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Callable

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, RefreshToken, RegistrationError, TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .oauth_store import OAuthStore

ACCESS_TTL = 3600
REFRESH_TTL = 90 * 24 * 3600
CODE_TTL = 300
PENDING_TTL = 600

# Registered redirect_uri hosts, exact match only (no implicit subdomains). Anyone could
# otherwise dynamically-register a client named "Claude" with a redirect_uri on their own host
# and send the real owner a genuine-looking /login link — a consent-phishing path to a stolen
# authorization code. localhost/127.0.0.1 are for local development against a remote server.
DEFAULT_REDIRECT_HOSTS = ("claude.ai", "claude.com", "localhost", "127.0.0.1")
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1")


class SpeedianceOAuthProvider:
    def __init__(self, store: OAuthStore, public_url: str, *, clock: Callable[[], float] = time.time,
                 allowed_redirect_hosts: tuple[str, ...] = DEFAULT_REDIRECT_HOSTS):
        self.store = store
        self.public_url = public_url.rstrip("/")
        self._clock = clock
        self.allowed_redirect_hosts = tuple(h.lower() for h in allowed_redirect_hosts)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        info = self.store.client_json(client_id)
        return OAuthClientInformationFull.model_validate_json(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.redirect_uris:
            raise RegistrationError(error="invalid_redirect_uri",
                                    error_description="at least one redirect_uri is required")
        for uri in client_info.redirect_uris:
            host = (uri.host or "").lower()
            if host not in self.allowed_redirect_hosts:
                raise RegistrationError(error="invalid_redirect_uri",
                                        error_description=f"redirect_uri host {host!r} is not allowed")
            allowed_schemes = ("http", "https") if host in _LOOPBACK_HOSTS else ("https",)
            if uri.scheme not in allowed_schemes:
                raise RegistrationError(error="invalid_redirect_uri",
                                        error_description=f"redirect_uri scheme {uri.scheme!r} is not allowed "
                                                          f"for host {host!r}")
        try:
            self.store.save_client(client_info.client_id, client_info.model_dump_json())
        except RuntimeError:
            raise RegistrationError(error="invalid_client_metadata",
                                    error_description="Too many registered clients; try again later.") from None

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        request_id, _csrf = self.store.add_pending(client.client_id, params.model_dump_json(), PENDING_TTL)
        return f"{self.public_url}/login?request={request_id}"

    def complete_sign_in(self, request_id: str) -> str | None:
        """Called by the sign-in page after a successful login: issue the code, return the redirect."""
        pending = self.store.pop_pending(request_id)
        if pending is None:
            return None
        params = AuthorizationParams.model_validate_json(pending["params_json"])
        data = {"scopes": params.scopes or [], "code_challenge": params.code_challenge,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "resource": params.resource}
        code = self.store.add_code(pending["client_id"], json.dumps(data), CODE_TTL)
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        row = self.store.get_code(authorization_code)
        if row is None or row["client_id"] != client.client_id:
            return None
        return AuthorizationCode(code=authorization_code, client_id=client.client_id,
                                 expires_at=row["expires_at"], **json.loads(row["data_json"]))

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        if not self.store.delete_code(authorization_code.code):
            raise TokenError(error="invalid_grant", error_description="authorization code already used")
        return self._issue_pair(client.client_id, secrets.token_hex(16), authorization_code.scopes,
                                authorization_code.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        row = self.store.token_row(refresh_token)
        if row is None or row["kind"] != "refresh" or row["client_id"] != client.client_id:
            return None
        if row["used"] or row["revoked"]:
            self.store.revoke_family(row["family"])  # a replayed refresh token: kill the whole family
            return None
        if row["expires_at"] <= self._clock():
            return None
        return RefreshToken(token=refresh_token, client_id=row["client_id"], scopes=row["scopes"],
                            expires_at=int(row["expires_at"]), resource=row["resource"])

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        row = self.store.token_row(refresh_token.token)
        if row is None or not self.store.mark_used(refresh_token.token):
            if row is not None:
                self.store.revoke_family(row["family"])  # lost a replay race: treat as replay
            raise TokenError(error="invalid_grant", error_description="refresh token already used")
        return self._issue_pair(client.client_id, row["family"], scopes or refresh_token.scopes,
                                refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self.store.token_row(token)
        if row is None or row["kind"] != "access" or row["revoked"] or row["expires_at"] <= self._clock():
            return None
        return AccessToken(token=token, client_id=row["client_id"], scopes=row["scopes"],
                           expires_at=int(row["expires_at"]), resource=row["resource"])

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        row = self.store.token_row(token.token)
        if row is not None:
            self.store.revoke_family(row["family"])

    def _issue_pair(self, client_id: str, family: str, scopes: list[str], resource: str | None) -> OAuthToken:
        access = self.store.issue_token("access", client_id, family, scopes, resource, ACCESS_TTL)
        refresh = self.store.issue_token("refresh", client_id, family, scopes, resource, REFRESH_TTL)
        return OAuthToken(access_token=access, expires_in=ACCESS_TTL, refresh_token=refresh,
                          scope=" ".join(scopes) or None)
