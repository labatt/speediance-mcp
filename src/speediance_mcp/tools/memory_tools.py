from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

# Strict so a client's `true` / "7" / 2.5 is rejected at the tool boundary instead of coerced to an int.
StrictDays = Annotated[int | None, Field(strict=True)]


def get_preferences(app) -> dict:
    """The coaching memory: goal, training days, session length, load anchors, owned equipment,
    ★preferred / ⊘avoided exercises, and the ACTIVE curated facts grouped as `facts`:
    constraints{hard, soft}, preferences, goals, observations (10 most recent), conflicts, legacyToReview and
    legacyUnreviewed (old free-form facts not yet curated — they may still bind: treat injury ones as hard
    constraints until curated, and curate them promptly with the user).
    Check facts.constraints.hard before building any workout; resolve any conflicts with the user."""
    memory = app.memory
    marks = memory.marks()
    return {**memory.preferences(), "facts": memory.fact_digest(),
            "preferredExercises": [{"groupId": g, "name": m["name"]} for g, m in marks.items() if m["mark"] == "preferred"],
            "avoidedExercises": [{"groupId": g, "name": m["name"], "reason": m["reason"]}
                                 for g, m in marks.items() if m["mark"] == "avoided"]}


def set_preferences(app, goal: str | None = None, training_days: list[str] | None = None,
                    session_minutes: int | None = None, load_anchors: dict[str, float | None] | None = None,
                    owned_equipment: list[str] | None = None) -> dict:
    """Update structured preferences (only the fields given change). load_anchors maps group_id -> a
    known working weight in displayUnit and is MERGED into the saved anchors (other anchors are kept);
    a weight of null or 0 removes that anchor. owned_equipment is a list of accessory names from
    list_accessories (it replaces the saved list). Free-text facts go in remember_fact instead."""
    fields = {"goal": goal, "training_days": training_days, "session_minutes": session_minutes,
              "load_anchors": load_anchors, "owned_equipment": owned_equipment}
    try:
        return {"preferences": app.memory.set_preferences(**{k: v for k, v in fields.items() if v is not None})}
    except ValueError as exc:
        raise ToolError(str(exc)) from None


def remember_fact(app, text: str, kind: str, category: str, severity: str | None = None,
                  scope: str | None = None, supersedes: list[int] | None = None, expires_days: StrictDays = None,
                  source: str = "inferred") -> dict:
    """Save ONE durable training fact (max 600 characters — split longer ones; nothing is truncated) and
    tell the user you saved it.
    kind: constraint (a rule a workout must obey — needs severity: hard = never violate, soft = avoid if
    possible) | preference (a weight on exercise choice) | goal (a target) | observation (a dated finding,
    no forward authority).
    category: injury | equipment | schedule | body | nutrition | note.
    scope: optional context such as "location:tampa-hotel". supersedes: ids this fact replaces (they are
    archived in the same write) — use it for corrections and updates instead of adding a second version.
    expires_days: only for temporary things ("travelling next week" ~7). source: "user" when the user stated
    it directly, else "inferred" (default).
    If a near-identical active fact exists nothing is saved and the near-match comes back (saved=false):
    re-send with supersedes=[its id] if the new one replaces it. Don't store bodyweight/unit (Speediance
    knows) or load numbers (use set_preferences)."""
    try:
        return app.memory.remember_fact(text, kind=kind, category=category, severity=severity, scope=scope,
                                        supersedes=supersedes, expires_days=expires_days, source=source)
    except ValueError as exc:
        raise ToolError(str(exc)) from None


def forget_fact(app, id: int) -> dict:  # noqa: A002 — the spec names the parameter `id`
    """Archive a fact that no longer applies (ids come from get_preferences `facts` or list_facts). It
    stays in list_facts(include_archived=true) but leaves every default read. For a fact that changed,
    prefer remember_fact(..., supersedes=[id]). Forgetting a legacy fact marks it reviewed."""
    try:
        fact_id = int(id)
    except (ValueError, TypeError):
        raise ToolError(f"id must be a fact id number, got {id!r}.") from None
    try:
        return {"forgotten": True, "fact": app.memory.forget_fact(fact_id)}
    except (LookupError, ValueError) as exc:
        raise ToolError(str(exc)) from None


def list_facts(app, kind: str | None = None, category: str | None = None, include_archived: bool = False) -> dict:
    """The audit surface for the curated facts, oldest first, with each fact's status, supersedes /
    supersededBy chain, source and legacy flag. Default: active facts only. include_archived=true returns
    the full history (superseded, forgotten, expired and legacy facts). Filter by kind (constraint |
    preference | observation | goal) or category (injury | equipment | schedule | body | nutrition | note)."""
    try:
        facts = app.memory.list_facts(kind=kind, category=category, include_archived=bool(include_archived))
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    return {"count": len(facts), "facts": facts}


def import_facts(app, facts: list[dict], dry_run: bool = False) -> dict:
    """Bulk-save facts (up to 200), e.g. to curate the legacy facts. Each item is an object with the same
    fields as remember_fact: text, kind, category, and optional severity, scope, supersedes, expires_days,
    source. Every single-write rule applies to every item, in order (an item is also deduped against items
    accepted earlier in the batch). Returns {accepted, deduped: [{input, matched_existing_id}],
    rejected: [{input, reason}], superseded: [ids], dryRun}. dry_run=true returns the identical report
    and writes nothing — use it first, show the user, then run it for real."""
    try:
        return app.memory.import_facts(facts, dry_run=bool(dry_run))
    except ValueError as exc:
        raise ToolError(str(exc)) from None
