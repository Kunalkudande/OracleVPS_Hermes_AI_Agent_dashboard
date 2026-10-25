# Hermes personal workflows

Installed on the existing VPS on 25 October 2026. The first briefing and content
review were generated, both native Hermes jobs were run successfully, and Hermes
lists the personal operations skill as enabled.

| Workflow | India time | Native Hermes job |
| --- | --- | --- |
| Morning briefing and research import | Daily 08:00 IST | `14b513603201` |
| AI Orbit content review | Sundays 18:00 IST | `f333cb08c877` |

Cron expressions are UTC: `30 2 * * *` and `30 12 * * 0`. Both jobs use
`--no-agent --deliver local`: reports remain on the VPS and in the private
dashboard; no Telegram or email delivery is enabled. The first morning run is
26 October at 08:00 IST. The next weekly run is 25 October at 18:00 IST.

The dashboard now includes Today, Knowledge and Content analyst. The private
SSH dashboard at `http://localhost:9119/#today` serves these changes. The existing
Windows launcher can reconnect its tunnel after a reboot. Vercel CLI has no
credentials on this machine, so the public Vercel frontend has not been redeployed.
For that deployment, use the existing Vercel project and production settings;
the updated `lib/transport.mjs` must accompany the static frontend so the new API
routes pass the SSH bridge allowlist.

## What is connected

The briefing reads existing scheduler metadata, saved X plans, publication
receipts, workflow issues and library articles published within 48 hours. It does
not perform a fresh web scan. Missing or failed feeds therefore cannot be
interpreted as evidence that no news occurred.

The analyst reads `~/tweetytweets/metrics/summary.json`. This is an existing
cumulative summary of sampled posts observed at ages 24–72 hours, not a seven-day
cohort. It reports missing counters as unknown, declines comparisons with fewer
than five sampled posts per bucket in at least two buckets, and flags metrics
older than seven days. The threshold is an editorial guard, not statistical
significance. Experiments are suggestions; publishing behavior is unchanged.

The library imports only bounded JSON files from `source-evidence` and dated
draft evidence, preserving source links. Initial import processed 85 source
occurrences into 63 unique entries. No browser sessions, config secrets or prompt
logs are indexed. Imports are idempotent by source URL. Pasted notes are supported;
providing a URL does not fetch it. Search is lexical and returns source excerpts.
For a synthesized answer, ask Hermes to search the saved research; the installed
skill instructs it to cite evidence and acknowledge gaps.

## Files and operations

- Implementation: `/home/ubuntu/hermes-control/personal.py`.
- Private database: `/home/ubuntu/.local/state/hermes-control/personal/personal.sqlite3`.
- Native cron scripts: `~/.hermes/scripts/personal_briefing.py` and `personal_analyst.py`.
- Agent skill: `~/.hermes/skills/productivity/hermes-personal-operations/SKILL.md`.
- Pre-deployment backup: `~/hermes-control/backups/personal-20261025T093121Z`.

Run on the VPS:

```sh
python3 ~/hermes-control/personal.py ask 'agent memory'
python3 ~/hermes-control/personal.py sync
python3 ~/hermes-control/personal.py reports
hermes cron runs 14b513603201 --limit 3
hermes cron runs f333cb08c877 --limit 3
```

To disable future runs, use `hermes cron pause JOB_ID`. Preserve the SQLite file
to retain notes. For code rollback, pause the two jobs, restore server/bridge/static
files from the named backup, and restart `hermes-control.service`; do not delete
the library. The existing service already permits writes to this state directory,
so no systemd sandbox permissions were widened.

Validation: 19 backend tests passed on Windows and in VPS staging; four web
transport tests passed, static build and JavaScript syntax checks passed. Browser
QA used an isolated fixture to verify sign-in, all three new views and source
search. Live VPS checks confirmed 63 library entries, successful native job runs,
skill discovery, service health HTTP 200 and unauthenticated workspace HTTP 401.
