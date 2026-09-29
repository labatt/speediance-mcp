"""`speediance-mcp` command line."""

from __future__ import annotations

import argparse
import getpass
import sys

from . import __version__
from .config import clear_credentials, load_credentials, save_credentials
from .paths import data_dir
from .speediance.api import SpeedianceAPI
from .speediance.client import CLIENT_TYPES, DEFAULT_CLIENT_TYPE, SpeedianceClient, SpeedianceError


def _make_client(creds, region):
    return SpeedianceClient(creds, region=region)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="speediance-mcp",
        description="Unofficial MCP server for Speediance Gym Monster. With no command it runs the MCP server "
                    "over stdio — what Claude Desktop and Claude Code launch.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run the MCP server (the default).")
    serve.add_argument("--http", action="store_true",
                       help="Remote mode: streamable HTTP at /mcp with OAuth, for claude.ai. Put it behind HTTPS.")
    serve.add_argument("--public-url", help="The https:// address clients reach this server at (required with --http).")
    serve.add_argument("--host", default="127.0.0.1", help="Address to listen on (default 127.0.0.1).")
    serve.add_argument("--port", type=int, default=8765, help="Port to listen on (default 8765).")
    serve.add_argument("--allow-redirect-host", action="append", dest="allow_redirect_host", metavar="HOST",
                       help="Also allow OAuth clients that redirect to HOST (claude.ai, claude.com, localhost "
                            "and 127.0.0.1 are always allowed).")
    login = sub.add_parser("login", help="Sign in to Speediance and store credentials locally.")
    login.add_argument("--email")
    login.add_argument("--region", choices=["Global", "EU"], default="Global")
    login.add_argument("--device-type", type=int, choices=[1, 2], default=1, help="1 = Gym Monster (default), 2 = Gym Pal")
    login.add_argument("--unit", choices=["lb", "kg"],
                       help="Your account's weight unit, as the Speediance app shows it. Needed the first time you "
                            "sign in with a client type other than phone, which don't report it.")
    login.add_argument("--client-type", choices=list(CLIENT_TYPES), default=DEFAULT_CLIENT_TYPE,
                       help="Which Speediance session slot to sign in with (default: bike). Speediance allows one "
                            "session per slot, so pick one no device of yours uses — see the README.")
    remember = login.add_mutually_exclusive_group()
    remember.add_argument("--remember", dest="remember", action="store_true", default=True,
                          help="Store the password so expired sessions renew silently (the default).")
    remember.add_argument("--no-remember", dest="remember", action="store_false",
                          help="Store only the session token; run `speediance-mcp login` again when it expires.")
    sub.add_parser("logout", help="Sign out and delete stored credentials.")
    sub.add_parser("status", help="Show the signed-in account and data directory.")
    sub.add_parser("revoke", help="Remote mode: disconnect claude.ai and every other remote client.")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "serve"
    if command == "serve":
        return _serve(args)
    if command == "login":
        return _login(args)
    if command == "logout":
        return _logout()
    if command == "revoke":
        return _revoke()
    return _status()


def _serve(args) -> int:
    from .tools.context import App
    if not getattr(args, "http", False):
        from .server import build_server
        build_server(App(mode="local")).run("stdio")
        return 0
    if not args.public_url:
        print("--public-url is required with --http: the https:// address clients use.", file=sys.stderr)
        return 2
    from .remote import build_remote_app
    app = App(mode="remote")
    try:
        asgi = build_remote_app(app, args.public_url, allowed_redirect_hosts=args.allow_redirect_host)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    import uvicorn
    # Only the local reverse proxy's X-Real-IP (trusted by signin._client_ip) should identify a
    # caller for the sign-in rate limit; uvicorn must not also honour X-Forwarded-For from the
    # network, or a remote attacker could forge it to dodge the lockout.
    uvicorn.run(asgi, host=args.host, port=args.port, log_level="warning", proxy_headers=False,
               forwarded_allow_ips="")
    return 0


def _login(args) -> int:
    try:
        email = (args.email or input("Speediance email: ")).strip()
        password = getpass.getpass("Password: ")
    except EOFError:
        print("speediance-mcp login needs an interactive terminal to ask for your email and password. "
              "Run it in a terminal (e.g. over SSH).", file=sys.stderr)
        return 2
    known = load_credentials()
    remember = args.remember
    client = _make_client(None, args.region)
    unit = args.unit or (known.unit if known and known.email.lower() == email.lower() else None)
    try:
        creds = client.login(email, password, remember=remember, device_type=args.device_type,
                             client_type=args.client_type, unit=unit)
    except SpeedianceError as exc:
        client.close()
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1
    path = save_credentials(creds)
    print(f"Signed in as {creds.email} (display unit: {creds.unit}, region: {creds.region}, "
          f"client type: {creds.client_type}). "
          f"Credentials saved to {path}.")
    if remember:
        print("Password stored so expired sessions renew automatically (use --no-remember to store only the token).")
    else:
        print("Password not stored: signing in on the phone app ends this session; run `speediance-mcp login` again then.")
    print("Downloading the exercise library (first time only; about 30 seconds)...")
    try:
        count = len(SpeedianceAPI(client, path.parent).library(force=True))
    except SpeedianceError as exc:
        print(f"Couldn't cache the exercise library now ({exc}); it will download on first use.")
    else:
        print(f"Cached {count} exercises.")
    finally:
        client.close()
    return 0


def _logout() -> int:
    creds = load_credentials()
    if creds is None:
        print("Not logged in.")
        return 0
    client = _make_client(creds, creds.region)
    client.logout()
    client.close()
    clear_credentials()
    print(f"Signed out {creds.email} and deleted the stored credentials.")
    return 0


def _revoke() -> int:
    path = data_dir() / "oauth.db"
    if not path.exists():
        print("No remote connections.")
        return 0
    from .oauth_store import OAuthStore
    store = OAuthStore(path)
    try:
        store.revoke_all()
    finally:
        store.close()
    print("Disconnected every remote client (claude.ai and others). They'll need to sign in again.")
    return 0


def _status() -> int:
    creds = load_credentials()
    home = data_dir()
    if creds is None:
        print(f"Not logged in. Run `speediance-mcp login`.\nData directory: {home}")
        return 1
    print("\n".join([f"Account: {creds.email}", f"Region: {creds.region}", f"Display unit: {creds.unit}",
                     f"Device type: {creds.device_type}", f"Client type: {creds.client_type}",
                     f"Password remembered: {'yes' if creds.password else 'no'}", f"Data directory: {home}"]))
    return 0
