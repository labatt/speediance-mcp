"""Turn any failure into a friendly MCP tool error (never a stack trace)."""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError

from ..speediance.client import AuthExpired, LoginFailed, SpeedianceError


def translate(exc: BaseException, app) -> ToolError:
    if isinstance(exc, ToolError):
        return exc
    if isinstance(exc, (AuthExpired, LoginFailed)):
        return ToolError(app.login_hint)
    if isinstance(exc, SpeedianceError):
        return ToolError(str(exc) or "The Speediance request failed.")
    if isinstance(exc, ValueError):
        return ToolError(str(exc))
    return ToolError(f"Unexpected error ({type(exc).__name__}): {exc}")
