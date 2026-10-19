# X content planning on the VPS

Strategy `human-conversation-v3` uses four daily formats: a relatable observation, a practical tip, one specific unanswered question and a rotating light post. The generated voice uses plain language, natural rhythm, kind humour and one or two contextual emojis per day. It avoids jargon, motivational slogans, headline summaries, invented personal stories and requests for engagement.

The planner collects bounded optional research, sends at most 16,000 request bytes to the existing local proxy, reserves 1,800 output tokens and makes at most three attempts. HTTP errors, including 413, switch to a compact evergreen request with 1,400 output tokens. Retries do not replay earlier model output. Long links never cause silent deletion of an interesting sentence.

`x_content_rules.py` provides shared editorial checks and a conservative Unicode length bound. This can reject some posts that X would accept, intentionally keeping submissions within the free-account ceiling. Existing duplicate checks, account checks, locks, daily limits and uncertain-send receipts remain in the publisher.

## Deployment

These files extend the existing `tweetytweets` installation; they are not a standalone publisher and do not contain its browser profile, credentials, provider keys, original dependencies, drafts or receipt ledger.

- Deploy Python files under `~/tweetytweets/scripts/`.
- Deploy `x_voice_profile.md` as `~/tweetytweets/references/voice-profile.local.md`.
- Preserve backups and use the existing `.autopublish.lock` while replacing files.
- Keep the scheduler's existing `x_generate_plan.sh` and `x_autopublish.sh` entry points.
- `x_generate_plan.py --date YYYY-MM-DD` validates/reuses an existing fresh or editorially reviewed plan; `--force` explicitly regenerates it.
- Published slots, receipt URLs and scheduled times are preserved during refreshes. Marking a reviewed plan with `editorial_reviewed_at` protects it from routine morning regeneration after its validation succeeds.

On the VPS, run tests using the existing project environment:

```sh
cd ~/tweetytweets/scripts
../.venv/bin/python -m unittest test_x_autopublish test_x_content_rules test_x_planner -v
```

The planner test simulates an HTTP 413 and verifies compact recovery, original scheduled times, published-post preservation and reuse of reviewed drafts. Content-quality checks catch some repetitive patterns; human review is still useful for taste, originality and factual accuracy. No growth or engagement outcome is guaranteed.

## Silent-check log retention

`cleanup_publisher_logs.py` removes only exact empty/silent publisher reports older than 48 hours, using both report time and file modification time. It checks only the known publisher's output directory, rejects symlinks and unexpected content, and never accesses draft plans or publication receipts. Run it without `--apply` to preview.

The supplied `hermes-log-retention.timer` runs hourly, with up to two minutes of jitter and a persistent catch-up after downtime. Deploy the cleanup script under `~/hermes-control/`, install the two unit files under `~/.config/systemd/user/`, then enable the timer. The cleanup service can write only to this publisher's report directory. Hermes' existing count-based pruning stays in place, so reports can be removed sooner by that separate policy. Errors and publishing reports are not deleted by this age-based cleanup.

Test retention with `python -m unittest test_log_retention -v` from this directory.
