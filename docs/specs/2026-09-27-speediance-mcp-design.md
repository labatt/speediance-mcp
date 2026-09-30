# speediance-mcp — Design

**Date:** 2026-09-27
**Status:** Approved 2026-09-27; amended the same day with live-verified API facts (§15)

## 1. Purpose

GM Manager — the hosted MCP server many Speediance Gym Monster owners use to let Claude
read and manage their training — is becoming a paid service. `speediance-mcp` is a **free,
MIT-licensed, self-hostable replacement**: an MCP server that anyone can install from GitHub
and point at their own Speediance account.

**Success looks like:**

- A Claude Desktop or Claude Code user installs it in a few commands on Linux, Windows or
  macOS, logs in once, and has the same tools GM Manager offered.
- A user who can host a small server (like the author) exposes it to claude.ai web and mobile
  through an OAuth connector.
- The tools are *correct* on the Speediance API's many quirks, not just shaped right.

**Requirements from the user:** simple install; few requirements; minimal reliance on
third-party tools; portable across Linux, Windows and macOS; README and install instructions;
open source.

**Unofficial.** Not affiliated with or endorsed by Speediance. It uses the private API behind
the Speediance mobile app, which can change without notice.

## 2. Scope

**In scope**

- GM Manager's 19 tools under the same names, plus 7 additions (26 tools; §6).
- Local mode over stdio (phase 1) and remote mode over streamable HTTP with OAuth (phase 2).
- A coaching memory: facts, preferences, load anchors, exercise marks, owned equipment.
- README, install instructions per OS, MIT license, CI on all three OSes.

**Out of scope**

- Multi-tenant hosting. One Speediance account per deployment.
- Phone-health walks, `.fit` uploads, or other data GM Manager merged in from its own
  integrations. They are not part of Speediance's API.
- Scheduling official Speediance courses (the user chose templates only).
- Changing the account's kg/lb unit (`PUT /api/app/userinfo`) — rare and risky mid-history.
- Speediance's subscription-gated AI template generator.
- Publishing to PyPI. Install from the GitHub URL; PyPI can come later.

## 3. Architecture

```
speediance-mcp/
  pyproject.toml            build backend hatchling; one runtime dependency: mcp
  README.md  LICENSE        MIT; hbui3's copyright notice kept alongside the author's
  .gitignore                credentials, *.db, logs, caches, .env
  .github/workflows/ci.yml  Linux / Windows / macOS x Python 3.10-3.13
  src/speediance_mcp/
    __init__.py  __main__.py
    cli.py                  entry point `speediance-mcp`: (default) stdio | login | logout | status | serve --http
    paths.py                per-OS data dir, stdlib only
    config.py               load/save credentials.json (owner-only perms)
    speediance/
      client.py             HTTP transport, login, silent re-auth, headers, throttle, error model
      routes.py             session-type -> detail-route map; plan routes
      parsing.py            set parsing, units, ragged arrays, Free Lift scale, heart-rate nulls
      writes.py             template payload builder enforcing the write-fault rules
    memory.py               SQLite store: facts, preferences, marks, equipment (+ OAuth tables, phase 2)
    server.py               FastMCP instance, server instructions, tool registration
    tools/                  account.py sessions.py exercises.py workouts.py calendar.py coaching.py memory.py
    oauth.py                phase 2: OAuth authorization-server provider + login page
  tests/                    unittest; offline; synthetic fixtures only
  docs/specs/ docs/plans/
```

**Runtime dependency:** the official `mcp` package only, pinned `mcp>=2.2,<3`. In 2.x the
server class is `MCPServer` (`from mcp.server.mcpserver import MCPServer`; it was `FastMCP`
in 1.x). It brings its own HTTP stack (Starlette, Uvicorn, **httpx2**, pydantic). The
Speediance client uses **httpx2**, which `mcp` already requires, so there is no separate
HTTP dependency. Everything else is standard
library: `sqlite3`, `json`, `hashlib`, `secrets`, `getpass`, `pathlib`.

**Python:** 3.10+ (the `mcp` SDK's floor).

**Units of responsibility**

| Unit | Does | Depends on |
|---|---|---|
| `speediance.client` | Authenticated requests to one region's API; login; re-auth; throttle; turns body `code` into exceptions | httpx, `config` |
| `speediance.routes` | Maps a session's history-feed `type` to its detail route | — |
| `speediance.parsing` | Turns raw payloads into uniform exercises/sets | — (pure) |
| `speediance.writes` | Builds a template body that won't trip the write faults | `parsing` (pure) |
| `memory` | Durable coaching memory | `sqlite3` |
| `tools.*` | One MCP tool each: validate args, call client + parsing + memory, shape output | the above |
| `oauth` (phase 2) | OAuth server for the remote connector | `mcp.server.auth`, `memory`, `client` (to verify login) |

`parsing` and `writes` are pure and hold most of the hard-won logic, so they carry most of the
tests.

## 4. Modes

### 4.1 Local mode (phase 1, default)

```
pipx install git+https://github.com/<owner>/speediance-mcp
speediance-mcp login          # prompts: email, password (hidden), region, "remember password?"
```

Then one config block in the client. The README gives exact snippets and file locations for
Claude Desktop (Windows, macOS, Linux) and Claude Code (`claude mcp add speediance -- speediance-mcp`).

- Runs over stdio. No listener, no OAuth: the client launches it as a child process.
- `speediance-mcp status` prints the logged-in account, region, unit and data-dir path.
- `speediance-mcp logout` calls Speediance logout and deletes the stored credentials.

`uvx` works as an alternative to `pipx` and is documented as such. Neither is required at
runtime; plain `pip install` into a venv also works.

### 4.2 Remote mode (phase 2)

```
speediance-mcp serve --http --host 127.0.0.1 --port 8765 --public-url https://mcp.example.com
```

- Streamable HTTP at `/mcp`, with the SDK's OAuth authorization-server support:
  metadata discovery, dynamic client registration, authorization code + PKCE (S256),
  refresh tokens.
- **Login page.** `/authorize` renders a small HTML form asking for the Speediance email and
  password. The server verifies them by calling Speediance's login, and accepts **only the
  account this deployment was set up for** (the email in `credentials.json`). On success it
  refreshes the stored Speediance token and issues the authorization code.
- The deployment must first be set up with `speediance-mcp login`. `serve --http` refuses to
  start without it.
- Binds to `127.0.0.1` by default. The README documents a reverse proxy for TLS: nginx and
  Caddy examples. `--public-url` is required so issued metadata carries the external HTTPS
  origin.

## 5. Speediance client

### 5.1 Hosts and headers

- Global `https://api2.speediance.com`; EU `https://euapi.speediance.com`. Chosen at login.
- Auth header `Token: <token>`, plus the mobile-app headers (timezone, UTC offset, device type).
- **`Versioncode` pinned at `41000`** (app v4.10.0). The API gates content by declared app
  version, and **throttles clients claiming implausibly high versions or changing it
  rapidly**. The value is a module constant with a comment warning against raising it.

### 5.2 Login and re-auth

- `POST /api/app/v2/login/verifyIdentity` `{"type": 2, "userIdentity": email}`, then
  `POST /api/app/v2/login/byPass` `{"userIdentity", "password", "type": 2}` → `token`, `appUserId`.
- Speediance allows one live session per account: signing in on the phone app invalidates
  this token. If the password was remembered, the client re-logs-in silently once and retries
  the request. Otherwise it raises `AuthExpired`, which tools report with the fix for the
  current mode.

### 5.3 Error model

- **Failure is in the body `code`, not the HTTP status.** An existing route rejecting input
  answers HTTP 200 with a non-zero `code`. A missing route returns 404. HTTP 5xx means the API
  fell over — some malformed write bodies do that.
- Exceptions: `SpeedianceError` (base), `AuthExpired`, `NotFound`, `Rejected(code, message)`,
  `ServerError`.
- "Sorry. You do not have access" on a detail route means the id belongs to a different
  session namespace, not a permission problem.

### 5.4 Throttle

At most **1 request per second**, globally per process, as a politeness and anti-throttling
measure. Tools that fan out (strength profile) take this into account in their limits.

### 5.5 Session-type routing

The history feed (`userTrainingDataRecord`) and the calendar number session types
**differently**: history uses {1, 2, 5, 7, 9}, the calendar {3, 4, 6, ...} for the same
categories. The map below is the union and must only be applied to a type read from the
feed it came from:

| type | category | detail route (`/api/app/trainingInfo/<route>/<id>`) |
|---|---|---|
| 1, 6 | Free Lift | `freeTraining` (one object with `actionList[]`) |
| 7 | Quick / guided cardio (e.g. "Aerobic Rowing") | `freeTraining` (+ `freeTrainingDetail` for per-interval rows) |
| 2 | Course / program | `courseTrainingInfoDetail` (+ summary `courseTrainingInfo`) |
| 3, 5 | Custom template | `cttTrainingInfoDetail` |
| 4, 9 | AI / Goal-Focused | `aiCourseTrainingInfoDetail` |

Tools that take a `training_id` **always resolve the type from the history feed** rather than
trusting a caller-supplied type. This is both correct (the numbering problem) and safe: the
detail routes don't check ownership, so resolving from the caller's own history guarantees we
only read this account's sessions. An unknown type falls back to trying each route in turn.

Plan routes (keyed by `courseId`, not `trainingId`): `v2/course/info/{id}` for courses,
`aiCourse/info/{id}` (no version prefix) for AI plans.

## 6. Tools

**Compatibility note.** GM Manager's schemas for `check_connection`, `get_calendar`,
`get_session_detail`, `list_my_workouts` and `get_exercise_history` were recovered in full and
are matched exactly: names, parameter names and defaults, return shapes. The other 14 were not
recoverable (the service was already gone). They keep GM Manager's names and documented
behavior, but their parameters are reconstructed and may differ in detail.

All weights are in the account's display unit (`displayUnit`). **The display unit comes from
the login response's `unit` field (`1` = lb, anything else = kg)** and is stored with the
credentials. `userinfo/info`'s `weightUnit` is *not* the display unit — it reads `0` on an
lb account (verified 2026-09-27). Tools never convert.

### Account

| Tool | Params | Returns | Source |
|---|---|---|---|
| `check_connection` | — | `{connected, account, spUserId, displayUnit, region, message}`; on failure `{connected:false, action, message}` | `userinfo/info` (liveness) + stored unit |

### History

| Tool | Params | Returns | Source |
|---|---|---|---|
| `get_calendar` | `month` `YYYY-MM` | Per day: `trainingPlanList[]` with completion flags, plus completed sessions merged in from the history feed (`source:"history"`), because the calendar feed is lossy | `v5/trainingCalendar/monthNew` + history |
| `get_session_detail` | `training_id`, `type=0` (advisory, ignored for routing) | `{trainingId, detailType, resolvedType, displayUnit, exercises:[{name, groupId, sets, reps[], weights[], setLog[], avgLoad, avgLoadEstimated}], cardio?, heartRateAvailable}` | §5.5 routes |
| `get_exercise_history` | `exercise=""`, `groupId=0`, `limit=50` | `{exercise, displayUnit, sessions:[{date, topWeight, volume, minWeight?}], summary}`; ambiguous name → `{needsPick:true, matches}` | `actionLibraryGroup/userActionStatPage` |
| `get_athlete_snapshot` | `days=30` | Profile (sex, bodyweight, height, age, unit, watch paired, lifetime totals), coaching memory (facts, preferences, ★/⊘ marks, owned equipment), `history[]` of recent sessions | `userinfo/info` + history + memory |
| `get_strength_profile` | `limit=15` | Per recently-trained movement: best weight, estimated 1RM (Epley), latest top set, trend | history detail + `userActionStatPage` |
| `get_training_stats` *(new)* | `start`, `end` | Totals over the range (sessions, time, volume, calories), **labeled as including phone-health imports** | `v2/report/userTrainingDataStat` |
| `compare_sessions` *(new)* | `training_id`, `previous_training_id=0` (0 = the previous session containing the same movements) | Per movement: load, reps and volume deltas; sets completed vs missed | two details |
| `get_heart_rate` *(new)* | `training_id` | Second-by-second heart-rate curve, summarized (avg/max/zones) plus the raw series, or `{available:false}` without a paired watch | `watchMsg/getHeartRateGraph?uuid=` |

`get_session_detail` returns `cardio` for rowing and ski sessions: distance, pace per 500m,
speed, average power, calories per minute, completion, RPE; plus per-interval rows when the
session is guided cardio (§8.6).

### Exercises

| Tool | Params | Returns | Source |
|---|---|---|---|
| `list_exercises` | `query=""`, `body_part=""`, `category=""`, `equipment=""`, `owned_only=false`, `include_avoided=false`, `limit=50` | Matching movements `{groupId, name, bodyPart, muscle, equipment, unilateral, mark}`. ⊘avoided hidden unless `include_avoided` | `actionLibraryTab/list`, `actionLibraryGroup/trainingPartGroup` (cached) |
| `get_exercise` *(new)* | `group_id` | Muscles, equipment, form description/media links, `unilateral`, variants | `actionLibraryGroup/{id}?isDisplay=1` |
| `mark_exercise` | `group_id`, `mark` = `preferred`/`avoided`/`none` | Updated mark | memory |
| `list_accessories` *(new)* | — | Speediance's accessory catalog, deduplicated by name (the catalog repeats items per Monster model), each flagged `owned` | `accessories/list` |

The library is cached in the data directory for 24 hours (it's ~1,000 movements and rarely
changes). Exercise fields: the name is `title`; body part and muscle come from
`mainMuscleGroupList[].categoryName` / `muscleGroupName` (there is no body-part taxonomy with
names; `trainingPartId2` is opaque); equipment is `accessories`, a comma list of the catalog's
small ids (1 Flat Bench, 2 Tricep Rope, 4 Barbell, 5 Handles, 8 AeroRow, 9 Incline Bench…).
Owned equipment is stored **by accessory name**, so it holds across Monster models.

### Templates

| Tool | Params | Returns | Source |
|---|---|---|---|
| `list_my_workouts` | — | `{workouts:[{id, code, name, exercises, durationMinute}], slots:{used, limit, left}}`. `limit`/`left` are `null`: no known endpoint reports the account's template limit | `v4/customTrainingTemplate/appPage` |
| `get_workout` | `code` | Full prescription: exercises, sets, reps/seconds/level, weights, sides, presets | `v3/customTrainingTemplate/detailByCode` |
| `create_workout` | `name`, `exercises[]` | `{code, id, name, verified}` — see §8.3 | `POST v2/customTrainingTemplate` |
| `update_workout` | `code`, `name?`, `exercises?` | Same as create. Omitted fields keep their current value; `exercises`, when given, replaces the list | same route, with `id`/`code` |
| `delete_workout` | `code` | `{deleted:true}` | `DELETE customTrainingTemplate?ids=` |

`exercises[]` items: `{group_id | name, sets:[{reps | seconds, weight | level, side?, mode?}], rest_seconds?}`.
`mode` is `standard` (default), `chain` or `eccentric`, written to the per-set `sportMode` CSV as 1/2/3
(verified: the machine's edit screen stored Standard/Chain/Eccentric as `"1,2,3"`). The chain/eccentric
overload amount is not stored in a template; the user dials it in on the machine.
A `name` is resolved against the library (exact, then prefix, then all-words); an ambiguous
name fails with the candidates rather than guessing.

No known endpoint reports the template limit, so `create_workout` doesn't pre-check it; if
Speediance rejects a create for being over its limit, that rejection is passed through with
the advice to delete or reuse a template. **RM presets (8RM etc.) are out of scope for phase
1:** exercises are prescribed with explicit weights, seconds, or Vita levels.

### Calendar

| Tool | Params | Returns | Source |
|---|---|---|---|
| `schedule_workout` | `date` `YYYY-MM-DD`, `code` | `{scheduled:true}` | `POST templateReservation` `status:1` |
| `unschedule_workout` *(new)* | `date`, `code` | `{unscheduled:true}` | same route, `status:0` |

### Coaching

| Tool | Params | Returns | Source |
|---|---|---|---|
| `suggest_load` | `exercise | group_id`, `reps`, `rir=2` | Suggested weight with its reasoning: recent top sets, estimated 1RM, the user's load anchor if set, avoided/preferred note | history + memory |
| `browse_programs` *(new)* | `query=""`, `program_id=0` | Program list, or one program's structure when `program_id` is set | `exclusivePlan/page`, `exclusivePlan/{id}` |

### Memory

| Tool | Params | Returns |
|---|---|---|
| `get_preferences` | — | Preferences, load anchors, active facts (expired ones pruned), ★/⊘ marks, owned equipment |
| `set_preferences` | any of `goal`, `training_days[]`, `session_minutes`, `load_anchors{group_id: weight}`, `owned_equipment[]` | Updated preferences |
| `remember_fact` | `fact`, `expires_days=0` (0 = durable) | `{id, fact, expiresAt}` |
| `forget_fact` | `fact_id` | `{forgotten:true}` |

### Server instructions

The MCP `instructions` field carries GM Manager's coaching rules, adapted:

- Before planning, creating or advising on any workout, check the coaching memory and respect
  it (it rides along in `get_athlete_snapshot`, so there's usually no need to call
  `get_preferences` separately).
- When the user states a durable fact (goal, schedule, injury, dislike, equipment), save it
  with `remember_fact` and say so. Use `expires_days` only for temporary things. Don't store
  what Speediance already knows (bodyweight, unit) or numeric load anchors (those go in
  `set_preferences`).
- Lean toward ★preferred movements. Never program a ⊘avoided movement unless the user asks
  for it by name. `list_exercises` already hides avoided ones.
- Mark a lasting per-exercise preference with `mark_exercise` when the conversation makes it
  clear.
- Templates are slot-limited: check `list_my_workouts().slots` before creating, and never
  delete a template to make room without asking.
- Weights are in the account's unit; never convert.

## 7. Coaching memory store

SQLite at `<data-dir>/speediance-mcp.db`, created on first use, with a `schema_version` table
for future migrations.

```
facts(id INTEGER PK, fact TEXT, created_at TEXT, expires_at TEXT NULL)
preferences(key TEXT PK, value_json TEXT)          -- goal, training_days, session_minutes, load_anchors, owned_equipment
exercise_marks(group_id INTEGER PK, mark TEXT CHECK(mark IN ('preferred','avoided')), name TEXT, updated_at TEXT)
-- phase 2:
oauth_clients(client_id TEXT PK, client_json TEXT, created_at TEXT)
oauth_codes(code_hash TEXT PK, client_id, redirect_uri, code_challenge, scopes, expires_at)
oauth_tokens(token_hash TEXT PK, kind TEXT, client_id, scopes, expires_at, revoked INTEGER)
```

The library cache is a separate JSON file, not the database (it is disposable).

## 8. Data rules (correctness)

These encode hard-won API behavior. Sources: this author's own work, `pookey/speediance-cli`
and `stozo04/speediance-cli` (both MIT). Each rule gets a test.

### 8.1 Units and loads

- Weights come back in the account's display unit on read and go out verbatim on write.
  Nothing converts. The unit comes from `userinfo/info` `weightUnit`.
- **`weights` is a trap on dual-cable exercises.** There it is derived force telemetry, not
  the resistance setting. Use `leftWeights`/`rightWeights`; `weights` is only a safe fallback
  on single-cable movements.
- **Load points.** `weights` is the load at one attachment point. The point count (1 or 2)
  is `capacity / sum(weights)`, measured to be exactly 1 or 2.
- `finishedCount` is already the user-visible rep count, never doubled for dual-handle sets.

### 8.2 Set kinds

`completionMethod` decides what numbers mean: `1` = rep target (`targetCount` is reps);
`0`, `2` = timed, no reps counted (`targetCount` is seconds); `5` = Vita: timed window with reps
counted, intensity is a **level** (the `level` field; `weights` is all zeros). Levels have no
upper bound. Skipped sets: reps sets skip on `done == 0`, timed/level sets on `seconds == 0`.

### 8.3 Template writes

Encoded in `speediance.writes`:

1. `totalCapacity` is **never null** (null → HTTP 500). Always a computed number.
2. **Customize preset, unit-dependent wire format.** Every movement is sent with
   `templatePresetId: -1`, the app's "Customize" mode, where the machine runs the stored weights.
   Positive ids are the app's presets (`templatePresetList`: 1 Gain Muscle, 3 Stamina, 5 Strength),
   which load from the user's 1RM instead. `build_template` works in the display unit (what `verify`
   compares); `wire_body` converts at save time.
   - **lb accounts:** sent as built: weights verbatim, `totalCapacity` as the raw sum.
     Live-verified (35 saved → 35 lb shown).
   - **kg accounts:** the server reads a -1 movement's `weights`/`capacity` as pounds, so they go
     out × 2.2, and `totalCapacity` × 2.2 whatever the preset. Verified live 2026-09-30: 20 kg sent
     as 44.00 stored 20, and the machine showed 20 kg in Customize mode. Loads must be whole kg
     up to 100: 22.5/20.5/12.5 stored as 22/20/12, and 9.5 stored as 9.50 but showed as 9 on
     the machine, so `create_workout`/`update_workout` refuse half kilos up front.
3. *(merged into rule 2)*
4. A unilateral movement (`isLeftRight`) with no explicit sides gets sides auto-alternated
   `1,2,1,2,…`. All-`0` sides → HTTP 500.
5. `counterweight2` is always sent empty (a non-empty value clobbers weights server-side).
6. **Verify by read-back.** After a create/update, fetch the template and compare stored
   weights and reps with what was sent. On a mismatch, return `verified:false` and the diff
   rather than claiming success. Rules 2 and 3 are unverified on lb accounts; this check makes
   a wrong assumption visible instead of silent.

### 8.4 Free Lift

- Free Lift (`freeTraining`) answers one object with `actionList[].setList[]` (`summary` +
  `rawRepList`), not the list of exercises the other routes return. Parsing must know which
  route answered, because an empty payload doesn't say.
- Dual-handle sets report each hand separately in `rawRepList` (12 reps → 24 raw entries,
  tagged by `side`).
- **Scale.** Free Lift per-set figures carry a ×2.2 scale on kg accounts (pookey). On an lb
  account the set capacities sum to exactly the session total — ratio 1.0, verified
  2026-09-27 — so there is no scale. Parsing reconciles each session's raw set-capacity sum against the session's
  own `totalCapacity`, trying scaled and unscaled. If neither reconciles within ~1%, it keeps
  the raw values and adds a warning to the tool output. It never silently divides.

### 8.5 Telemetry and misc

- Per-rep telemetry arrays are **ragged** (one set: 13 amplitudes, 12 watts). Never zip two
  channels by index; scale each series over its own length.
- `maxHeartRate: 0.0` means "no watch", reported as `null`, decided per set (a watch can
  connect mid-session).
- Range stats (`userTrainingDataStat`) include phone-health imports and are labeled so.
- The calendar feed is lossy: it hides completed custom-template sessions. Completed sessions
  come from the history feed.

### 8.6 Rowing and cardio

- Every rowing/ski session has totals: `trainingTime`, `totalDistance`, `totalEnergy`
  (joules), `calorie`, `completionRate`, `rpe`. From these: pace per 500m, speed, average
  power (`totalEnergy / trainingTime`), calories per minute. Null inputs give null outputs,
  never NaN.
- **Guided cardio** (type 7, e.g. "Aerobic Rowing") also has per-interval rows via
  `freeTraining`/`freeTrainingDetail`.
- Rowing done as a **course or custom template** carries per-point telemetry at
  `GET /api/app/boatingSkiDataGraph/{uuid}` (found 2026-09-29 in the phone app's route list and
  verified live). It is keyed on the session **uuid**, not the trainingId, which is why 12
  trainingId-based path probes all missed it. `existBoatingSkiDataGraph: true` says a recording
  exists. Samples arrive every few seconds with `spm`, `pace` (s/500m), `power` (W),
  `resistance`, and the programmed target band (`minSpm`/`maxSpm`, `minResistance`/
  `maxResistance`). A change in that band marks the boundary between programmed pieces, so the
  tool cuts blocks there and reports each block's rates plus how much of its stroking time sat
  inside the band. Samples with `spm` 0 are the flywheel spinning up, a pause or the cool-down:
  they count as rest and are excluded from every average, since their coasting `pace` would
  otherwise report a speed never actually rowed. A session flagged but with no samples still
  gets the explanatory note rather than an empty list.

## 9. Security

- **Credentials file** `<data-dir>/credentials.json`: token, user id, region, email, and the
  password only if the user chose "remember". Created with mode 0600 on POSIX. On Windows it
  sits in the user's private profile directory (`%APPDATA%`); the README states that
  file-permission hardening there relies on the profile's default ACLs.
- **Never logged:** tokens, passwords, OAuth codes or tokens. Logs go to stderr (stdout is
  the MCP channel in stdio mode).
- **Remote login (phase 2):**
  - Only the deployment's own Speediance account can authorize. A stranger can't log in, and
    can't use the server to proxy their own account.
  - Failed logins are rate-limited per IP and globally (e.g. 5 per 15 minutes), with
    exponential backoff.
  - PKCE S256 is mandatory. Redirect URIs must exactly match what the client registered.
  - Authorization codes are single-use and expire after 5 minutes.
  - Access tokens last 1 hour. Refresh tokens rotate on use, and a reused refresh token
    revokes its family.
  - Codes and tokens are stored as SHA-256 hashes, never in plain text.
  - The login form is CSRF-protected, and the page sends no-store and frame-denying headers.
- **Ownership on reads:** detail lookups resolve against the account's own history (§5.5).
- **No SSRF surface:** no tool fetches arbitrary URLs.

## 10. Errors

- Every tool returns MCP tool errors with a plain-language message and the next step. Nothing
  surfaces as a stack trace.
- `AuthExpired`: local mode → "run `speediance-mcp login`"; remote mode → "reconnect the
  connector in claude.ai".
- `Rejected` passes Speediance's `code` and message through.
- Inputs are validated before any request (dates, months, marks, reps > 0).
- Ambiguous exercise names return candidates instead of guessing.

## 11. Testing

- `unittest` (standard library); also runs under pytest. No dev dependencies required.
- **Offline:** Speediance HTTP is mocked at the transport. No test touches the network or a
  real account.
- **Synthetic fixtures only.** No real account data, emails or ids in the repo.
- Coverage priorities: `routes` (every type), `parsing` (every §8 rule), `writes` (every
  write fault), `memory` (expiry, marks), each tool's argument validation and output shape,
  and phase 2's OAuth flow (PKCE, rotation, reuse revocation, rate limiting, wrong account).
- CI: GitHub Actions on `ubuntu-latest`, `windows-latest`, `macos-latest` × Python 3.10–3.13.
  It also smoke-tests the installed console script (`speediance-mcp --help`).
- A manual acceptance checklist in the README for maintainers: real login, snapshot, one
  create/verify/delete round trip on a throwaway template, heart rate on a watch session.

## 12. Packaging, docs, licensing

- `pyproject.toml`: hatchling build backend, console script `speediance-mcp`,
  `requires-python >= 3.10`, dependency `mcp` with a tested minimum version.
- **README:**
  1. What it is (and the unofficial disclaimer)
  2. Install per OS (pipx / uvx / pip)
  3. Log in
  4. Connect Claude Desktop (config file locations per OS) and Claude Code
  5. Tool list
  6. Remote mode with nginx and Caddy examples
  7. Troubleshooting (expired token; phone logins invalidate the token)
  8. Data locations and how to wipe them
  9. Security notes
  10. Acknowledgments
- **License:** MIT. The API client is ported from hbui3/UnofficialSpeedianceWorkoutManager
  (MIT), so hbui3's copyright notice is preserved in `LICENSE`. API facts from
  pookey/speediance-cli and stozo04/speediance-cli are credited in the README. Their code is
  not copied.
- **Hygiene:** fresh repository, no history from any private repo. `.gitignore` covers
  credentials, databases, caches and logs.

## 13. Phases

1. **Core + local mode:** client, routes, parsing, writes, memory, all 26 tools, stdio server,
   CLI (`login`/`logout`/`status`), README, CI. Usable in Claude Desktop and Claude Code.
2. **Remote mode:** OAuth provider and login page, `serve --http`, reverse-proxy docs, and
   deployment on the author's server on its own subdomain and nginx block, leaving the
   existing app's basic-auth gate untouched.

## 14. Unverified assumptions (tracked, not hidden)

| Assumption | How it's handled |
|---|---|
| The ×2.2 template scaling only affects kg accounts | lb and kg paths both live-verified; read-back verification (§8.3 rule 6) exposes a wrong assumption |
| Free Lift ×2.2 per-set scale | lb verified unscaled; reconciled against session totals per session (§8.4) |
| The 14 reconstructed GM Manager tool schemas | Same names and documented behavior; parameter details may differ. Noted in the README |
| Course/AI reservation shape | Out of scope (templates only) |
| `getHeartRateGraph` response shape | Confirmed to exist by other projects; shape pinned by the first live acceptance run |

## 15. Amendments (2026-09-27, from live verification)

Found while grounding the implementation plan against the live API and the current SDK:

1. `mcp` 2.x renamed `FastMCP` → `MCPServer` and depends on `httpx2`, not `httpx` (§3).
2. The display unit comes from the login response's `unit` (1 = lb); `userinfo/info`
   `weightUnit` is not it (§6).
3. No endpoint reports the template slot limit; `limit`/`left` are `null` and the server's
   own rejection is passed through (§6).
4. Exercise name is `title`; body part/muscle come from `mainMuscleGroupList`; equipment is
   a list of the accessory catalog's small ids; owned equipment is stored by name (§6).
5. Template writes use the Customize preset (-1) with a unit-dependent wire format (lb and kg verified) (§8.3).
6. Free Lift sets are unscaled on lb accounts (verified ratio 1.0) (§8.4).
7. RM presets are out of scope for phase 1 (§6).

Recorded after the phase-1 final review (2026-09-27):

8. `get_athlete_snapshot` defaults to `days=14` (GM Manager parity), not 30 (§6).
9. §6 parameter names were aligned with GM Manager so prompts written for it keep working:
   `update_workout(template_id, name?, exercises?)` and `delete_workout(template_id)`, where
   `template_id` is the template `code` (preferred) or its numeric id; `forget_fact(memory_id)`;
   `remember_fact(text, category="note", expires_days=0)`; `schedule_workout(date, code, add=true)`
   (`add=false` unschedules); `list_exercises(query, muscle=, category, equipment, kind=,
   owned_only, include_avoided, limit=60)` (`muscle` replaces `body_part`; `kind` is
   reps/timed/level); `mark_exercise(mark, group_id=0, name=)` (a name is resolved like
   `create_workout` names); `suggest_load(reps, exercise="", groupId=0, rir=2)`;
   `get_exercise_history(exercise="", groupId=0, limit=50)` (limit capped at 500).
10. Template writes are validated and verified more strictly than §8.3 first said: reps weights
    must be finite numbers 0–1000 with at most one decimal place; reps, seconds and levels must be
    whole numbers (no bools, no truncation of 8.9) and sides 1/2; the read-back `verify` also
    compares each exercise's `actionLibraryId`, rest (`breakTime2`) and `templatePresetId`;
    `update_workout` checks the edit happened in place (same code, new name shown, no new
    template id); and a name-only update refuses to rebuild — before any write — an exercise that
    was built with a Speediance preset/RM load (`templatePresetId` other than -1/0/1) or has a
    missing/zero stored weight, asking for the full `exercises` list instead. A rejected create is
    reported with Speediance's message plus the custom-workout-limit advice (§6).
11. Credentials reload on auth failure: when a request fails with an expired session, the running
    server first re-reads `credentials.json` and, if it holds a different token (the user re-ran
    `speediance-mcp login`), adopts it and retries once; only then does it fall back to the
    remembered-password re-login. Concurrent failures on the same stale token log in once (§5.2).
12. `set_preferences(load_anchors=...)` merges into the saved anchors; a `null` or `0` weight
    removes that anchor (§6, §7).
13. An unpinned bilateral set's resistance is `max(leftWeights) + max(rightWeights)`, not
    `max(max(left), max(right))` — both cables carry the load (verified live 2026-09-27, §8.5);
    and a quick single-exercise session (type 7 via `freeTraining`) can have no `actionList` at
    all, so `get_session_detail`/coaching fall back to `freeTrainingDetail` (list-route shape)
    when `freeTraining` parses to zero exercises and the session isn't cardio (verified live
    2026-09-27, §5.5).
14. `speediance-mcp login` remembers the password by default, so expired sessions renew
    silently, and no longer prompts for it; `--no-remember` stores only the session token.
    This replaces the "remember password?" prompt in §5 and the opt-in wording in §12 (user
    decision 2026-09-27). The trade-off is documented in the README: with one session per
    account, a silent re-login signs out the phone app or any other tool using the account.
15. Login client type is configurable (`speediance-mcp login --client-type`, stored in
    credentials): `phone` = `SOFTWARE` (the phone app's slot), `gym-monster` = `HARDWARE` (the
    machine's slot), `nano` = `NANO`, `bike` = `BIKE` (default). Non-phone types log in with
    `Versioncode: 1`. Every other request stays `SOFTWARE`/`41000`. Speediance keeps one session per
    client type. Verified live 2026-09-27: a `BIKE` login kept the Gym Monster and the phone
    signed in, and the phone's sign-in kept the `BIKE` session alive. `HARDWARE` signed the Gym
    Monster out (confirmed twice). `NANO` accepts the same request pattern, but a `NANO` login has
    not been tested. Code 90 (displaced) triggers a silent re-login only for `nano`/`bike`; for
    `phone`/`gym-monster` it's reported as auth loss, so we never fight the user's own devices.
16. The free-form fact layer (§6 `remember_fact`/`forget_fact`, §7 `facts` table) is replaced by
    the curated fact store in `docs/specs/2026-09-28-curated-facts.md` (user decision 2026-09-28):
    facts have a `kind` (constraint with hard/soft severity, preference, observation, goal), an
    enforced category, optional scope and `supersedes`, a 600-character limit (rejected, never
    truncated) and near-duplicate refusal. `remember_fact(text, kind, category, severity?, scope?,
    supersedes?, expires_days?, source?)` and `forget_fact(id)` (archives, never deletes) change
    signature; `list_facts` and `import_facts` are new, making 28 tools (was 26 in §2 and §13).
    `get_preferences` and `get_athlete_snapshot` return a grouped `facts` block (constraints
    hard/soft, preferences, goals, 10 most recent observations, read-time conflicts,
    legacyToReview). Facts live in a new `curated_facts` table; the legacy `facts` table is left
    untouched and each of its rows is copied once, archived, for the user to curate.
