# speediance-mcp

A free, open-source [MCP](https://modelcontextprotocol.io) server that lets Claude read and manage your
**Speediance Gym Monster** training: your session history, per-set logs, exercise progress, rowing stats,
heart rate, custom workouts and schedule — plus a coaching memory of your goals, injuries and preferences.

It's a self-hosted alternative to GM Manager. It runs on your own computer, talks directly to Speediance,
and keeps everything it stores on your machine.

> **Unofficial.** Not affiliated with or endorsed by Speediance. It uses the private API behind the
> Speediance mobile app, which can change without notice.

## Install

You need **Python 3.10 or newer**. Check with `python3 --version` (Windows: `py --version`).
Get Python from [python.org](https://www.python.org/downloads/) if needed.

The easiest way is **pipx**, which installs the command in its own isolated environment:

| OS | Install pipx | Install speediance-mcp |
|---|---|---|
| macOS | `brew install pipx && pipx ensurepath` | `pipx install git+https://github.com/labatt/speediance-mcp` |
| Windows | `py -m pip install --user pipx && py -m pipx ensurepath` | `pipx install git+https://github.com/labatt/speediance-mcp` |
| Linux | `python3 -m pip install --user pipx && python3 -m pipx ensurepath` | `pipx install git+https://github.com/labatt/speediance-mcp` |

Open a new terminal after `ensurepath`. Alternatives: `uvx --from git+https://github.com/labatt/speediance-mcp speediance-mcp`,
or `pip install git+https://github.com/labatt/speediance-mcp` inside a virtual environment.

### Sign in

```
speediance-mcp login
```

Enter your Speediance email and password. By default the password is **remembered**, so an expired
session renews silently. If you'd rather not store it, use `speediance-mcp login --no-remember`: only the
session token is kept, and you run `speediance-mcp login` again when it expires.

### Choose a client type (so you don't get signed out of your phone or your machine)

Speediance allows **one signed-in session per client type**, not per account. Every Speediance app and
machine signs in as a type, and a new sign-in with a type signs out whatever was using it:

| `--client-type` | Speediance's own user of that slot | Signing in with it signs out... |
|---|---|---|
| `phone` | The Speediance phone app | your phone app (and your phone signs this server out) |
| `gym-monster` | The Gym Monster | your Gym Monster |
| `nano` | Gym Nano | a Gym Nano on your account, if you have one |
| `bike` (default) | Speediance bike | a Speediance bike on your account, if you have one |

**Pick a type that no device of yours uses:**

- **Gym Monster, no Nano or bike (most people):** keep the default, `bike`.
- **You also own a Speediance bike:** use `--client-type nano`.
- **You own both a Nano and a bike:** use `--client-type phone`, and expect your phone app and this server
  to sign each other out. Never use `gym-monster` unless you want to be signed out at the machine.
- **Running two tools on the same account** (for example this server and another Speediance tool): give
  them different free types, or they will sign each other out.

With `nano` or `bike`, if something else does take the slot, the server quietly signs back in (with a
remembered password). With `phone` or `gym-monster` it never does that automatically, since it would sign
your phone or machine out again; it tells you to run `speediance-mcp login` instead.

`speediance-mcp status` shows the client type you signed in with. These types were found by testing (they
aren't documented by Speediance), and a future Speediance update could change them.

Use `--region EU` if your account is on Speediance's EU servers, and `--device-type 2` for a Gym Pal.
Login also downloads the exercise library (about 30 seconds, once a day at most).

`speediance-mcp status` shows who you're signed in as; `speediance-mcp logout` signs out and deletes the
stored credentials.

## Connect Claude

### Claude Desktop

Open the config file (Claude Desktop → Settings → Developer → Edit Config), or create it:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

Add:

```json
{
  "mcpServers": {
    "speediance": { "command": "speediance-mcp" }
  }
}
```

Then restart Claude Desktop.

**macOS:** Claude Desktop doesn't see your shell's `PATH`, so it usually can't find commands in
`~/.local/bin` (where pipx puts them). Use the full path that `which speediance-mcp` prints:

```json
{
  "mcpServers": {
    "speediance": { "command": "/Users/you/.local/bin/speediance-mcp" }
  }
}
```

**Windows:** if Claude can't find the command, use the full path that `where speediance-mcp` prints. JSON
needs forward slashes or doubled backslashes — `"C:/Users/you/.local/bin/speediance-mcp.exe"` or
`"C:\\Users\\you\\.local\\bin\\speediance-mcp.exe"`, never single backslashes:

```json
{
  "mcpServers": {
    "speediance": { "command": "C:/Users/you/.local/bin/speediance-mcp.exe" }
  }
}
```

**With uvx instead of pipx** (sign in first with
`uvx --from git+https://github.com/labatt/speediance-mcp speediance-mcp login`):

```json
{
  "mcpServers": {
    "speediance": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/labatt/speediance-mcp", "speediance-mcp"]
    }
  }
}
```

On macOS, use the full path from `which uvx` as the `command` for the same reason as above.

### Claude Code

```
claude mcp add speediance -- speediance-mcp
```

Then ask Claude something like *"How did my last pull workout compare to the one before?"*

## Use it on claude.ai (remote mode)

claude.ai (web and mobile apps) connects to MCP servers over the internet, so this mode needs a
machine that's always on and reachable over HTTPS.

1. On that machine, install and sign in as above (`speediance-mcp login`). The remote sign-in page
   only accepts this same Speediance account.
2. Run the server (keep it running with systemd, pm2 or similar):
   ```
   speediance-mcp serve --http --public-url https://mcp.example.com
   ```
   It listens on `127.0.0.1:8765`. Put a TLS reverse proxy in front. nginx (the `limit_req` lines
   are optional but recommended: they slow down anyone hammering the sign-in and OAuth endpoints):
   ```nginx
   # at http level (e.g. in /etc/nginx/conf.d/speediance.conf, outside the server block):
   limit_req_zone $binary_remote_addr zone=speediance_oauth:10m rate=10r/m;

   server {
       listen 443 ssl;
       server_name mcp.example.com;
       # ssl_certificate / ssl_certificate_key: e.g. from `certbot --nginx -d mcp.example.com`
       location ~ ^/(register|authorize|login|token)$ {
           limit_req zone=speediance_oauth burst=20 nodelay;
           proxy_pass http://127.0.0.1:8765;
           proxy_http_version 1.1;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_buffering off;
           proxy_read_timeout 3600s;
       }
       location / {
           proxy_pass http://127.0.0.1:8765;
           proxy_http_version 1.1;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_buffering off;
           proxy_read_timeout 3600s;
       }
   }
   ```
   Caddy (fetches the certificate itself):
   ```
   mcp.example.com {
       reverse_proxy 127.0.0.1:8765 {
           header_up X-Real-IP {remote_host}
       }
   }
   ```
   The server reads `X-Real-IP` from this local proxy to rate-limit sign-in attempts by the
   caller's real address, so whichever proxy you use must set that header.
3. In claude.ai: **Settings → Connectors → Add custom connector**, URL `https://mcp.example.com/mcp`.
   Claude opens the sign-in page; enter your Speediance email and password.

By default only clients that redirect to `claude.ai`, `claude.com`, `localhost` or `127.0.0.1` can
connect. To let another MCP client connect (for example a self-hosted or third-party one), add its
redirect host with `--allow-redirect-host`, repeatable:

```
speediance-mcp serve --http --public-url https://mcp.example.com --allow-redirect-host client.example
```

To disconnect claude.ai and every other remote client, run `speediance-mcp revoke`.
`speediance-mcp logout` also stops the server using your Speediance account until you sign in again.

Security:

- Only clients that redirect to `claude.ai`, `claude.com`, `localhost` or `127.0.0.1` can connect;
  add others with `--allow-redirect-host`.
- After repeated wrong sign-ins, the sign-in page locks for up to 15 minutes.
- Someone who knows the server's address can keep new sign-ins locked out by repeatedly failing
  (20 failed attempts per 15 minutes overall lock everyone out). Existing connections keep working;
  the nginx `limit_req` lines above make this slower.
- Only the account the server was set up with can connect; tokens are stored hashed; access tokens
  last an hour and refresh automatically.

The `--client-type` you pass to `speediance-mcp login` (see
[Choose a client type](#choose-a-client-type-so-you-dont-get-signed-out-of-your-phone-or-your-machine))
is also the slot every remote sign-in through this server uses — with `phone`, a claude.ai sign-in
through it signs your phone app out, the same as it would locally. Give a local copy of the server
a different free slot if you run both.

## Tools

All weights are in your account's display unit (kg or lb) — nothing is converted.

⊘ avoided marks (with an optional reason) are shared with the companion web app when both use the same
data dir; `create_workout` and `update_workout` flag any avoided exercise they were asked to include.

| Tool | What it does |
|---|---|
| `check_connection` | Verify the Speediance login is live |
| `get_calendar` | A month's scheduled and completed sessions |
| `get_session_detail` | One session's per-exercise log — sets, reps, weights; rowing pace/power; guided-cardio intervals |
| `get_heart_rate` | A watch-paired session's heart-rate curve and summary |
| `get_training_stats` | Totals between two dates |
| `get_athlete_snapshot` | Profile, coaching memory and recent sessions in one call |
| `get_strength_profile` | Estimated 1RM per recently trained movement |
| `get_muscle_balance` | Which muscles the recent work loaded, push:pull and upper:lower, and what's been missed |
| `compare_sessions` | A session versus the previous one, per movement |
| `suggest_load` | A working weight for a rep target, with its reasoning |
| `list_exercises` | Search the exercise library (body part, equipment, what you own) |
| `get_exercise` | One movement's muscles, equipment, form cues and media |
| `mark_exercise` | Mark a movement ★ preferred or ⊘ avoided |
| `list_accessories` | Speediance's accessories, flagged with what you own |
| `get_exercise_history` | Every session of one movement, oldest to newest |
| `list_my_workouts` | Your saved custom workouts |
| `get_workout` | One workout's full prescription |
| `create_workout` | Create a workout (verified by reading it back) |
| `update_workout` | Edit a workout in place |
| `delete_workout` | Delete a workout |
| `schedule_workout` | Put a workout on a day |
| `unschedule_workout` | Take a workout off a day |
| `browse_programs` | Speediance's official programs |
| `get_preferences` | The coaching memory |
| `set_preferences` | Goal, training days, session length, load anchors, owned equipment |
| `remember_fact` | Save one curated fact: a hard/soft constraint, preference, goal or observation (max 600 characters; near-duplicates are refused; `supersedes` replaces older facts) |
| `forget_fact` | Archive a fact that no longer applies (kept in the history) |
| `list_facts` | Audit the facts, optionally with the full history of superseded, forgotten and expired ones |
| `import_facts` | Save many facts at once under the same rules, with a `dry_run` report first |

19 of these use GM Manager's tool names and, for most, its parameter names (`template_id`, `groupId`,
`add`...), so prompts written for GM Manager keep working: `check_connection`, `get_calendar`,
`get_session_detail`, `get_exercise_history`, `get_athlete_snapshot`, `get_strength_profile`, `list_exercises`,
`mark_exercise`, `list_my_workouts`, `get_workout`, `create_workout`, `update_workout`, `delete_workout`,
`schedule_workout`, `suggest_load`, `get_preferences`, `set_preferences`, `remember_fact` and `forget_fact`.
The other 10 are new: `get_heart_rate`, `get_training_stats`, `compare_sessions`, `get_exercise`,
`list_accessories`, `unschedule_workout`, `browse_programs`, `list_facts`, `import_facts` and
`get_muscle_balance`. `set_preferences`,
`suggest_load` and `create_workout` take simpler inputs: typed preference fields instead of one JSON blob, and
no Dynamic Weight modes or RM presets yet. `remember_fact` and `forget_fact` differ from GM Manager's (see
below).

**Coaching facts** are curated rather than free text. Each fact has a `kind` — a `constraint` (with `severity`
`hard` or `soft`), a `preference`, a `goal` or an `observation` — and a `category` (`injury`, `equipment`,
`schedule`, `body`, `nutrition`, `note`), an optional `scope` (e.g. `location:tampa-hotel`) and a `source`
(`user` or `inferred`). A fact is at most 600 characters: longer ones are refused with the count, never
truncated. A new fact that nearly repeats an active one isn't saved; you get the near-match back and can re-send
it with `supersedes` to replace the old one, which is archived in the same write. `forget_fact(id)` archives
rather than deletes, and expired facts archive themselves. `get_preferences` and `get_athlete_snapshot` return
only active facts, grouped as `constraints` (`hard`, `soft`), `preferences`, `goals`, the 10 most recent
`observations`, and `conflicts`: pairs of active facts in the same category that look like the same topic but
differ on a number or a negation, with both ids, so Claude asks you instead of picking one. `list_facts` shows
the full history. Facts with different scopes (e.g. `location:home` and `location:tampa-hotel`) never count as
duplicates or conflicts. When a refused near-match differs in kind, severity, scope, a number or a negation, the
reply names the difference so an upgrade isn't lost. Facts saved before this change are kept as legacy facts:
reads list them as `legacyUnreviewed` (and count them in `legacyToReview`) until each one is re-saved with
`supersedes` (via `remember_fact` or `import_facts`; a long one can be split into several pieces in one import)
or forgotten. Until then they may still bind, and Claude treats injury ones as hard constraints.
`create_workout` and `update_workout` replies repeat your `hardConstraints` and `legacyUnreviewed` facts so
Claude re-checks the workout against them.

**Rowing:** every rowing session gets distance, pace per 500m, speed, average power and calories per minute.
Guided cardio sessions (like "Aerobic Rowing") also get per-interval rows. Rowing done as a course or custom
workout gets a `rowing` block instead: the machine records a sample every few seconds, so each programmed
piece reports its stroke rate, pace, watts, and how much of it stayed inside the target stroke-rate band.

## Troubleshooting

- **"Not signed in to Speediance, or the session expired."** Run `speediance-mcp login`. A remembered
  password (the default) renews an expired session automatically; after `--no-remember`, sign in again.
- **Your phone app or Gym Monster keeps getting signed out.** You're signed in with the `phone` or
  `gym-monster` client type. Run `speediance-mcp login --client-type bike` (or `nano`) — see
  [Choose a client type](#choose-a-client-type-so-you-dont-get-signed-out-of-your-phone-or-your-machine).
- **A tool still says to sign in after you ran `speediance-mcp login`.** Restart Claude so the server starts
  fresh with the new login.
- **Claude doesn't list the tools.** Check the config JSON is valid, use the command's full path, and restart
  Claude.
- **The first exercise search is slow.** The library downloads once (about 30 seconds) and is then cached
  for a day.
- **A workout comes back `verified: false`.** Speediance stored something different from what was sent.
  Check the workout in the Speediance app before training and please open an issue with your unit (kg or lb).

## Your data

Everything lives in one folder (run `speediance-mcp status` to see it):

- **Windows:** `%APPDATA%\speediance-mcp`
- **macOS:** `~/Library/Application Support/speediance-mcp`
- **Linux:** `~/.config/speediance-mcp`

It holds `credentials.json` (your token, and your password unless you signed in with `--no-remember`), `speediance-mcp.db`
(the coaching memory) and the exercise-library cache. On macOS and Linux the credentials file is readable only
by you; on Windows it relies on your user profile folder's permissions. Delete the folder to erase everything.
Set `SPEEDIANCE_MCP_HOME` to use a different folder.

Nothing is sent anywhere except Speediance's own servers. Requests are paced at one per second.

## Security notes

- **Unofficial API.** This uses the private API behind the Speediance app. It isn't supported by Speediance
  and can change or break without notice.
- **Credentials stay on your computer.** `credentials.json` in the data folder holds your session token — and
  your password unless you used `--no-remember`. On macOS and Linux it (and a newly created data folder) is
  readable only by you.
- **One session per client type.** Speediance signs out whoever last used the same client type. With the
  default `bike` type (or `nano`), this server doesn't share a slot with your phone or your Gym Monster —
  see [Choose a client type](#choose-a-client-type-so-you-dont-get-signed-out-of-your-phone-or-your-machine).
- **Nothing is sent anywhere but Speediance.** No telemetry, no third-party services; tokens and passwords are
  never logged.

## Development

```
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -t . -v
```

Tests are offline and use synthetic data only. Maintainers can run this manual check against a real account
before a release:

- sign in and run `get_athlete_snapshot`;
- create, verify and delete a throwaway workout;
- create a workout with a Vita (level) exercise and confirm the level shows correctly in the Speediance app;
- call `get_heart_rate` on a watch-paired session.

## Acknowledgments

- The Speediance client is ported from [hbui3/UnofficialSpeedianceWorkoutManager](https://github.com/hbui3/UnofficialSpeedianceWorkoutManager) (MIT).
- API findings — the session-type routes, Free Lift scaling and template write rules — come from the notes of
  [pookey/speediance-cli](https://github.com/pookey/speediance-cli) and
  [stozo04/speediance-cli](https://github.com/stozo04/speediance-cli) (both MIT).

## License

MIT — see [LICENSE](LICENSE).
