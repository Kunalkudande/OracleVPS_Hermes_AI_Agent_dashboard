# Hermes job-search assistant

The Jobs view shows an employer watchlist matched against a private resume profile. It explains fit, flags missing evidence and joining-window conflicts, links to employer listings, and tracks saved/applied/interview/offer/rejected/dismissed states. Recording an application never submits one.

The first release checks 11 public sources across 10 employers, including two complete Ashby boards and selected employer career pages. It is not a whole-web, LinkedIn or Naukri search. Page sources have explicit section boundaries and required evidence; a changed page or failed request stays unverified. Active means recently observed on the employer source, not confirmed by a recruiter. Posting ages are often unavailable; “new” means new to this watchlist.

Primary roles are software engineer/developer, full stack, MERN and AI full stack. Backend, Node.js and NestJS are secondary. Pune, Bengaluru, Hyderabad, Mumbai and explicitly India-eligible remote roles qualify. Salary is shown only when published; undisclosed pay is included. Experience and notice acceptance are explained rather than invented. Two-to-three-year requirements are labeled stretch; senior titles and requirements above three years are excluded.

## Operation

On the VPS, the code lives in `~/hermes-control/`. Private state lives in `~/.local/state/hermes-control/jobsearch/`: `profile.json`, `sources.json`, and `jobs.sqlite3`. Profile and database permissions are restricted. Do not publish the private state or resume contact details. `job_sources.json` is the public source template; changes must also be copied to the private `sources.json` to take effect.

```sh
python3 ~/hermes-control/jobsearch.py sync
python3 ~/hermes-control/jobsearch.py list
python3 ~/hermes-control/jobsearch.py detail JOB_ID
python3 ~/hermes-control/jobsearch.py preview
python3 ~/hermes-control/jobsearch.py status JOB_ID saved
```

Sync and preview never send messages. The installed `hermes-job-search` skill teaches Hermes these operations. Ask “Show my best full-stack matches”, “Explain the gaps for this job”, or “Save this job”. Update the private profile when skills, location or notice change; matching currently assumes about one year of full-time experience, with internships separate.

The existing native Hermes job `75d433e98fda` is reused for **09:00 IST daily** (`30 3 * * *` on the UTC scheduler). It runs `~/.hermes/scripts/job_shortlist.py` with `/usr/bin/python3` in no-agent mode and sends stdout through the existing owner Telegram destination. The routine scan has no LLM call. It selects up to five previously unreserved matches, at most two per employer. It reports no new matches when nothing new qualifies and reports source coverage gaps.

The database reserves the shortlist before native delivery, once per IST date. `prepared_for_native_delivery` is a handoff record, not a delivery receipt. Check `hermes cron runs 75d433e98fda --limit 5` for actual delivery. An uncertain/failed handoff is not automatically resent; inspect the native run before deliberately repairing a reservation, to avoid duplicates. Application statuses survive refreshes and closed/reopened listings. Stale observations become unverified after 36 hours.

## Deployment and rollback

Deploy `jobsearch.py`, `job_sources.py`, `server.py`, `bridge.py` and the three static files together. Install the script and skill in their Hermes directories, configure private state, then restart `hermes-control`. This expires existing dashboard sessions. No extra public port or credential is required. Deploy the frontend and `lib/transport.mjs` through the existing Vercel project. Both new API routes require the existing dashboard session; status changes also require CSRF protection.

Before enabling the schedule, run the Python tests, Node tests, frontend build and a source sync/preview on the VPS. The initial deployment backup is `~/hermes-control/backups/pre-jobsearch-20261124T164727Z/`, including the previous scheduler entry. To roll back, pause `75d433e98fda`, restore the backed-up server/bridge/static files and restart `hermes-control`; revert the job-search frontend commit in Vercel. Preserve private state for recovery. Restore the old schedule through Hermes CLI using the backed-up entry if desired; do not replace the entire live scheduler file, which includes other workflows.

Validation: 53 backend tests and 6 Node tests passed locally; 37 relevant tests and an 11/11-source scan passed in VPS staging. The initial scan observed 69 openings and 16 eligible matches. These counts change as employer listings change.
