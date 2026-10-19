#!/usr/bin/env python3
"""Publish an authorized due draft once; ambiguous sends are never retried blindly."""
import argparse
import asyncio
import difflib
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timedelta

import due
import post
import settings
from x_content_rules import text_weight, safe_unicode

ROOT = settings.ROOT
JOURNAL = ROOT / 'autopublish-state.json'


def write_json(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def normalized(text):
    return re.sub(r'\s+', ' ', text or '').strip().lower()


def validate(text, config, history, evidence):
    policy = config.get('autopost', {})
    if not policy.get('enabled') or policy.get('approval_required', True):
        raise ValueError('Automatic publishing is not authorized')
    if not text or text_weight(text) > min(280, settings.limit(config)['chars']):
        raise ValueError('Draft must contain 1-280 characters including links')
    safe_unicode(text)
    if len(text.split()) < 6:
        raise ValueError('Draft is too short to be a useful post')
    for item in history:
        old = normalized(item.get('text', ''))
        if old and (normalized(text) == old or difflib.SequenceMatcher(None, normalized(text), old).ratio() > .90):
            raise ValueError('Draft duplicates a previous post')
    if evidence.get('kind') not in ('opinion', 'tip', 'news', 'science', 'culture'):
        raise ValueError('Draft needs content classification')
    if evidence.get('kind') in ('news', 'science', 'culture'):
        sources = evidence.get('sources') or []
        if not sources or any(not s.get('url', '').startswith('https://') or not s.get('excerpt') or not s.get('fetched_at') for s in sources):
            raise ValueError('Factual news/science needs fetched source evidence')


def mark_done(draft, url):
    path = due.plan_path(draft['date'])
    plan = json.loads(path.read_text())
    for slot in plan['slots']:
        if slot['slot'] == draft['slot']:
            slot.update(posted=True, tweet_url=url)
    write_json(path, plan)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()
    with (ROOT / '.autopublish.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        draft = due.due()
        if not draft:
            return
        config = settings.load()
        ledger = post.load_state().get('posts', [])
        plan = json.loads(due.plan_path(draft['date']).read_text())
        entry = next(s for s in plan['slots'] if s['slot'] == draft['slot'])
        state = json.loads(JOURNAL.read_text()) if JOURNAL.exists() else {}
        key = draft['date'] + '/' + draft['slot']
        claim = state.get(key, {})
        if claim.get('status') == 'verified':
            mark_done(draft, claim['url'])
            return
        if claim.get('status') == 'uncertain':
            # A crash/timeout can happen AFTER a successful click. Reconcile once;
            # never submit the same draft again because a receipt is missing.
            if not claim.get('reconciled'):
                rows = asyncio.run(post._read_profile(limit=12))
                match = next((r for r in rows if normalized(r.get('text')) == normalized(claim['text'])), None)
                claim['reconciled'] = True
                if match and match.get('url'):
                    claim.update(status='verified', url=match['url'])
                    mark_done(draft, match['url'])
                    print('Published and reconciled: ' + match['url'])
                else:
                    print('Publishing paused for ' + key + ': previous send is uncertain; not retrying.')
                state[key] = claim
                write_json(JOURNAL, state)
            return
        if claim.get('status') == 'failed' and claim.get('attempts', 0) >= 2:
            return
        now = due.now_tz()
        if claim.get('last_attempt') and now - datetime.fromisoformat(claim['last_attempt']) < timedelta(hours=1):
            return
        try:
            validate(draft['text'], config, ledger, entry.get('evidence', {}))
            # Claims cap submissions even if verification never succeeds.
            todays = [x for x in state.values() if x.get('submitted_date') == now.date().isoformat()]
            if len(todays) >= 4:
                raise ValueError('Daily limit of four root posts reached')
            published_times = [datetime.fromisoformat(x['submitted_at']) for x in state.values() if x.get('submitted_at')]
            if published_times and now - max(published_times) < timedelta(hours=3):
                return
            if args.check:
                print(json.dumps({'eligible': True, 'date': draft['date'], 'slot': draft['slot'], 'text': draft['text']}))
                return
            post.require_session()
        except (Exception, SystemExit) as exc:
            if args.check:
                raise
            state[key] = {'status': 'failed', 'attempts': claim.get('attempts', 0) + 1,
                          'last_attempt': now.isoformat(), 'error': str(exc)[:400]}
            write_json(JOURNAL, state)
            raise RuntimeError('Draft preflight failed: ' + str(exc)[:400]) from None
        # Persist the uncertainty boundary BEFORE sending to X.
        state[key] = {'status': 'uncertain', 'text': draft['text'], 'reconciled': False,
                      'submitted_date': now.date().isoformat(), 'submitted_at': now.isoformat()}
        write_json(JOURNAL, state)
        post.save_state = lambda value: write_json(post.STATE, value)
        result = asyncio.run(post.do_post(draft['text'], kind='value'))
        if result.get('verified') and result.get('tweet_url'):
            state[key].update(status='verified', url=result['tweet_url'])
            write_json(JOURNAL, state)
            mark_done(draft, result['tweet_url'])
            print('Published @' + config['handle'] + ': ' + result['tweet_url'])
        else:
            raise RuntimeError('Publication is uncertain; automatic resubmission is blocked')


if __name__ == '__main__':
    main()
