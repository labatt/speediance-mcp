from __future__ import annotations

from ..speediance.client import AuthExpired, LoginFailed


def check_connection(app) -> dict:
    """Verify the Speediance login is live. Call this FIRST in a session, before any other tool,
    and stop if it reports connected:false — relay its message (the user must sign in again).
    A merely expired token is renewed silently when the password was remembered."""
    creds = app.credentials()
    if creds is None:
        return {"connected": False, "action": "login", "message": app.login_hint}
    try:
        profile = app.api.profile()
    except (AuthExpired, LoginFailed):
        return {"connected": False, "action": "login", "account": creds.email, "message": app.login_hint}
    creds = app.credentials() or creds  # a silent re-login may have refreshed it
    return {"connected": True, "account": creds.email,
            "spUserId": str(profile.get("appUserId") or creds.user_id),
            "displayUnit": creds.unit, "region": creds.region,
            "message": f"Connected to Speediance as {creds.email} — session is valid."}
