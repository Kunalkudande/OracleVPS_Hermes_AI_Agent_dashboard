# Oracle VPS Hermes AI Agent Dashboard

A personal control room for Hermes: live workflow status, schedules, saved run reports, X drafts, engagement summaries and server health.

The frontend deploys to **Vercel**. Hermes and its Python backend keep running on your **Oracle VPS**. A dedicated SSH key permits only a bounded dashboard API bridge. It cannot open an interactive shell, forward ports, or run arbitrary commands.

## Deploy to Vercel

1. Import this GitHub repository into Vercel.
2. Use root directory **`./`**, framework preset **Other**, and Node.js **22.x**. `vercel.json` sets the build command and `dist` output directory.
3. Add the five Production environment variables in [VERCEL_SETUP.md](VERCEL_SETUP.md).
4. Deploy, set `HERMES_PUBLIC_ORIGIN` to your actual stable production address, then redeploy.

The sign-in page deploys without secrets, but live API access returns a setup message until the variables are configured. The dashboard password stays on the VPS, never in GitHub source or frontend code.

## Features

- **Today:** a saved morning briefing at 08:00 IST with recent saved research,
  scheduled work, publishing status and attention items.
- **Content analyst:** a saved Sunday 18:00 IST review with sample sizes, missing
  counters and conservative editorial experiments. Uses cumulative metrics, not
  a weekly cohort; never changes the publishing plan automatically.
- **Knowledge:** saved notes and imported research with keyword search, source
  passages and links. Conversational synthesis is available through the installed
  `hermes-personal-operations` Hermes skill. Dashboard search displays excerpts.
- **Overview:** active/paused jobs, latest errors, next runs and scheduler heartbeat freshness.
- **Automations:** pause/resume future runs with confirmation, saved-state verification and an action audit. Running tasks continue.
- **X publishing:** today's and tomorrow's drafts, missing slots, saved post URLs, uncertain-submission warnings and existing engagement summaries. The dashboard does not submit posts.
- **Run history:** up to 15 saved reports per workflow, 45 overall; outcome filtering and a text viewer. Embedded task prompts are omitted; common credential patterns are redacted.
- **System:** process state, disk/memory usage, uptime, Naukri log freshness and timestamped legacy schedule observations. Backup verification is explicitly unconnected.

Data refreshes every 30 seconds while visible. Displayed timestamps use India time; raw cron expressions use the UTC scheduler. A healthy process or successful empty publisher tick does not prove delivery or publication.

## Architecture

```text
Laptop / phone browser
        | HTTPS, login cookie + CSRF protection
        v
Vercel static dashboard + Node API function
        | SSH, dedicated key + pinned VPS host identity
        v
Forced bridge.py command on VPS
        | fixed localhost destination, allowlisted API paths
        v
Python dashboard at 127.0.0.1:9119
        | reads status; Hermes CLI for pause/resume only
        v
Existing Hermes workflows
```

The existing Windows SSH tunnel continues to work independently. Sessions stay on the VPS rather than inside short-lived Vercel functions. The supplied user service starts the backend at boot when the user manager has lingering enabled.

## Development and checks

```sh
npm ci --omit=optional
npm test
npm run test:backend
npm run build
```

Use Node.js 22.x and Python 3.11+. Node tests cover route allowlists, origin validation, host-key pinning and fail-closed configuration. Python tests cover authentication, logout/expiry, CSRF, rate limits, report traversal/redaction, bridge validation and verification of schedule changes. Tests use temporary data, not real jobs.

`server.py` and `bridge.py` need only the Python standard library. `ssh2` is the server-side Node dependency; its version is locked.

## VPS publishing automation

The `automation/` directory versions the existing VPS planner and its publishing guards. These scripts run in the existing `tweetytweets` environment on the VPS, not in Vercel. See [automation/README.md](automation/README.md) for the current content strategy and deployment notes.

Never commit passwords, private keys, auth files, cookies, cron reports, generated credential bundles or populated environment files. `.gitignore` excludes these and build/dependency output. `.env.example` contains placeholders only.

See [PERSONAL_OPERATIONS.md](PERSONAL_OPERATIONS.md) for the personal workflows,
installed schedule identifiers, storage and recovery instructions.
