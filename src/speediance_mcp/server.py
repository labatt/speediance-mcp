"""The MCP server: 28 tools over one Speediance account."""

from __future__ import annotations

import functools
import inspect
import logging
import typing

from mcp.server.mcpserver import MCPServer

from . import __version__
from .tools import account, calendar, coaching, exercises, memory_tools, sessions, workouts
from .tools.errors import translate

INSTRUCTIONS = """\
Speediance MCP (unofficial) manages the user's Speediance Gym Monster training from their real data.

Start every session with check_connection; if it reports connected:false, relay its message and stop.

COACHING MEMORY — curated facts about the user's training that you read and write. Each has a kind:
constraint (hard = never violate, soft = avoid if possible), preference, goal or observation.
- Reads: get_athlete_snapshot (memory.facts) and get_preferences (facts) return ACTIVE facts only, grouped as
  constraints{hard, soft}, preferences, goals, observations (10 most recent) and conflicts.
- Check facts.constraints.hard before building any workout, and never violate one. Honour soft constraints,
  preferences and goals unless the user says otherwise.
- `conflicts` lists pairs of active facts that seem to disagree (a different number, or one negated). Resolve
  each out loud with the user — never pick one silently — then save the winner with supersedes=[the loser's id].
  A fact's scope (e.g. location:tampa-hotel) limits where it applies: facts with different scopes both hold,
  each in its own context, so never supersede a fact with one from another scope.
- When the user states a durable fact, save it with remember_fact (one fact, max 600 characters — split longer
  ones) and tell them. source="user" when they said it directly. Use expires_days only for temporary things.
  Don't store what Speediance already knows (bodyweight, unit) or load numbers (set_preferences(load_anchors)).
- When a fact changes or is corrected, save the new one with supersedes=[old id] instead of adding a second
  version. If remember_fact answers saved=false with a nearMatch, it didn't save: read its `differences` (a
  kind or severity upgrade, a changed number, a negation) and, if the new fact updates the old one, call again
  with supersedes=[that id] — don't drop it. forget_fact archives a fact that no longer applies.
- legacyUnreviewed (counted in legacyToReview) lists facts from the old free-form store that haven't been
  curated. They may still bind: treat injury ones as hard constraints until curated. Curate them promptly with
  the user (list_facts(include_archived=true) shows them with legacy=true): re-save the ones that still hold
  with the right kind/category via import_facts (dry_run first) or remember_fact with supersedes=[legacy id],
  and forget_fact the rest. A legacy fact over 600 characters can be split into several pieces that each list
  supersedes=[legacy id], in ONE import_facts call.
- create_workout/update_workout replies repeat hardConstraints and legacyUnreviewed: re-check the workout
  against them before telling the user it's done.

EXERCISE MARKS — ★preferred and ⊘avoided movements:
- Lean toward preferred ones. NEVER put an avoided movement in a workout unless the user asks for it by name.
  list_exercises already hides avoided ones.
- When the user makes a lasting per-exercise preference clear, record it with mark_exercise. An avoided mark
  can carry a short `reason` (e.g. "left shoulder") — it's shared with the user's companion web app.
  create_workout/update_workout flag any avoided exercise you did include in their reply — tell the user.

TEMPLATES — accounts hold a limited number of custom workouts. Prefer update_workout over creating new
ones, and never delete a template to make room without asking. After create/update, check `verified`.

UNITS — every weight is already in the account's displayUnit. Never convert.
"""

TOOLS = (
    account.check_connection,
    sessions.get_calendar, sessions.get_session_detail, sessions.get_heart_rate, sessions.get_training_stats,
    coaching.get_athlete_snapshot, coaching.get_strength_profile, coaching.compare_sessions, coaching.suggest_load,
    exercises.list_exercises, exercises.get_exercise, exercises.mark_exercise, exercises.list_accessories,
    exercises.get_exercise_history,
    workouts.list_my_workouts, workouts.get_workout, workouts.create_workout, workouts.update_workout,
    workouts.delete_workout,
    calendar.schedule_workout, calendar.unschedule_workout, calendar.browse_programs,
    memory_tools.get_preferences, memory_tools.set_preferences, memory_tools.remember_fact, memory_tools.forget_fact,
    memory_tools.list_facts, memory_tools.import_facts,
)


def bind(fn, app):
    """Expose `fn(app, **kwargs)` as a tool without its `app` parameter; translate errors."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(**kwargs):
        try:
            return fn(app, **kwargs)
        except Exception as exc:
            raise translate(exc, app) from exc

    # Resolve string annotations (PEP 563) in the tool module's namespace, so aliases such as
    # Annotated[..., Field(strict=True)] survive being re-homed on this wrapper.
    hints = typing.get_type_hints(fn, include_extras=True)
    params = [p.replace(annotation=hints.get(p.name, p.annotation)) for p in list(signature.parameters.values())[1:]]
    wrapper.__signature__ = signature.replace(parameters=params,
                                              return_annotation=hints.get("return", signature.return_annotation))
    wrapper.__annotations__ = {k: v for k, v in hints.items() if k != "app"}
    del wrapper.__wrapped__
    return wrapper


def build_server(app, **mcp_kwargs) -> MCPServer:
    # Keep per-request URLs (and their query strings) out of the MCP client's logs.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    server = MCPServer(name="speediance", instructions=INSTRUCTIONS, version=__version__, **mcp_kwargs)
    for fn in TOOLS:
        server.tool()(bind(fn, app))
    return server
