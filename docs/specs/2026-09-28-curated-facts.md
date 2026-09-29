# Curated fact store (replaces the free-form fact layer)

Status: approved by the user 2026-09-28 (spec supplied by the user; implementation decisions below).

## Context (from the user)

The server currently has remember_fact, forget_fact and a facts[] array on get_preferences. Facts
are free text with a category label that nothing enforces. This shape already failed once in a
predecessor system. It accumulated 59 facts over six weeks and developed all of these problems:

- four pairs of near-identical duplicates saved seconds apart;
- a live contradiction (a clinical score of 6 of 8 vs 8 of 8, with nothing marking which won);
- three chains of corrections where every superseded version stayed active;
- a superseded goal still readable alongside its replacement;
- several facts over 1,000 characters silently truncated mid-sentence.

The store is read by an LLM at workout-planning time, so a stale contradiction is more dangerous
than a missing fact.

## Schema

```
Fact {
  id            int
  text          string, max 600 chars, REJECT over limit, never truncate
  kind          enum: constraint | preference | observation | goal
  severity      enum: hard | soft            // required when kind=constraint
  category      enum: injury | equipment | schedule | body | nutrition | note
  scope         string | null                // e.g. "location:tampa-hotel"
  supersedes    int[]                        // ids this replaces
  superseded_by int | null                   // derived, never set directly
  status        enum: active | archived      // derived from superseded_by + expires_at
  source        enum: user | inferred
  created_at    timestamp
  expires_at    timestamp | null
}
```

`kind` is the load-bearing field:

- A **constraint** with severity **hard** must be machine-checkable before a workout is built.
- A **preference** is a weight on exercise selection.
- An **observation** is a dated finding with no forward authority.
- A **goal** is a target.

## Tools

**remember_fact(text, kind, category, severity?, scope?, supersedes?, expires_days?)** returns the
stored fact. On write it:

- rejects text over 600 characters, returning the character count and a request to split it
  (never truncates);
- rejects `kind: constraint` without `severity`;
- runs a similarity check against active facts. If any match exceeds about 0.85 it does not
  write; it returns the near-match and asks whether the caller meant `supersedes`;
- if `supersedes` is given, sets `superseded_by` on each target and archives it in the same
  transaction.

**import_facts(facts[], dry_run=false)** is the bulk path. It runs every single-write rule on each
item and returns `{accepted: [...], deduped: [{input, matched_existing_id}], rejected: [{input,
reason}], superseded: [ids]}`. With `dry_run: true` it returns the identical report without writing.
It is never a shortcut past validation.

**forget_fact(id)** archives rather than deletes. Archived facts stay queryable but never appear in
a default read.

**list_facts(kind?, category?, include_archived=false)** is the audit surface.

## Read behaviour

`get_preferences` and `get_athlete_snapshot` return active facts only, grouped:

```
constraints: { hard: [...], soft: [...] }
preferences: [...]
goals: [...]
observations: [...]   // most recent 10
conflicts: [{ ids: [787, 788], reason: "..." }]
```

Conflict detection runs at read time. It flags two active facts in the same category that share
high term overlap and differ on a number or a negation, surfaces both ids, and never picks one
silently.

## Acceptance tests (from the user)

1. Writing a 1,200-character fact returns a rejection with the count, and nothing is stored.
2. Writing "the hotel stack moves in 5 lb steps to 60" twice, six seconds apart, stores one fact
   and returns the near-match on the second call.
3. Writing fact B with `supersedes: [A]` archives A in the same transaction; a default read
   returns B and not A.
4. `kind: constraint` without severity is rejected.
5. `import_facts` with `dry_run: true` writes nothing and returns a report identical in shape to
   the live run.
6. Importing a batch containing two near-identical items accepts one and reports the other as
   deduped.
7. A default read returns no archived or expired facts.
8. Two active facts in the same category that differ on a number appear in `conflicts` with both
   ids.
9. `list_facts(include_archived=true)` returns the full history, including superseded chains.

## Implementation decisions (ours)

- **Storage:** a new table `curated_facts` in the existing `speediance-mcp.db`. Columns follow the
  schema, plus `supersedes_json`, `superseded_by`, `archived_at` (set by forget), `expires_at`,
  `legacy`. `status` is derived at read time: archived if `superseded_by`, `archived_at`, or
  `expires_at <= now`.
- **Similarity:** stdlib only. Normalise the text (lowercase, collapse whitespace, strip
  punctuation). Score = max(`difflib.SequenceMatcher` ratio, Jaccard of word sets). Threshold 0.85.
  Compare against every active fact, and within an import batch against items already accepted
  earlier in the batch. A near-match whose id is listed in the new fact's `supersedes` is not a
  duplicate.
- **supersedes:** every id must exist and be active, else reject. A fact can't supersede itself.
  The write and the archiving happen in one SQLite transaction.
- **Conflicts:** consider pairs of active facts in the same category. Terms are word sets minus
  stopwords and numbers, and overlap is Jaccard ≥ 0.5. A pair conflicts when its sets of numbers
  differ or exactly one of the two contains a negation (not, no, never, cannot, can't, don't,
  without, unable). The reason names which (e.g. "same topic, different numbers: 6 vs 8").
- **Observations:** planning reads return the 10 most recent active observations.
- **Source:** defaults to `inferred` for Claude-written facts unless the caller passes
  `source="user"` (when the user stated it directly).
- **Migration of the legacy `facts` table (11 rows today):** copy each legacy row once into
  `curated_facts` as archived (`legacy=1`, kind `observation`, category mapped to the new enum,
  text kept whole even over 600), and leave the old table untouched. Reads add `legacyToReview: N`
  (the count of legacy rows not yet superseded) with a hint to curate them via
  `import_facts`/`remember_fact` using `supersedes` on the legacy id. Category mapping: injury,
  equipment, schedule, body, note → same; goal, preference, dislike → note.
- **Tools:** remember_fact and forget_fact change signature, and list_facts and import_facts are
  new, making 28 tools. The server instructions, README and tool-count tests are updated.
