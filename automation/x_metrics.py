#!/usr/bin/env python3
"""Read visible own-profile metrics; unavailable values stay null, never zero."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from statistics import median
import browser
import settings
import research
from x_autopublish import write_json

ROOT = settings.ROOT


def count(label, name):
    match = re.search(r'([\d,.]+[KMB]?)\s+' + name, label or '', re.I)
    if not match:
        return None
    value = match[1].replace(',', '')
    scale = {'K': 1000, 'M': 1000000, 'B': 1000000000}.get(value[-1].upper(), 1)
    return round(float(value[:-1] if scale != 1 else value) * scale)


def main():
    handle = settings.load()['handle']
    rows = json.loads(browser.evaluate('https://x.com/' + handle, research.X_JS, settle=8) or '[]')
    if not rows:
        raise RuntimeError('Own profile returned no posts; metrics unavailable')
    known = {}
    for path in (ROOT / 'drafts').glob('*.json'):
        plan = json.loads(path.read_text())
        for slot in plan.get('slots', []):
            if slot.get('tweet_url'):
                known[slot['tweet_url'].split('?')[0]] = slot
    now = datetime.now(timezone.utc)
    records = []
    for row in rows:
        url = (row.get('url') or '').split('?')[0]
        if url not in known:
            continue  # exclude reposted/other accounts' posts
        slot = known[url]
        label = row.get('metrics') or ''
        records.append({'url': url, 'created_at': row.get('created'),
                        'bucket': slot.get('bucket'), 'topic': slot.get('topic'),
                        'strategy': slot.get('strategy'), 'raw_metrics': label,
                        'views': count(label, r'views?'), 'likes': count(label, r'likes?'),
                        'reposts': count(label, r'reposts?'), 'replies': count(label, r'repl(?:y|ies)')})
    folder = ROOT / 'metrics'
    folder.mkdir(exist_ok=True)
    write_json(folder / (now.strftime('%Y%m%dT%H%M%S%fZ') + '.json'),
               {'observed_at': now.isoformat(), 'posts': records,
                'scope': 'Visible own-profile posts only; missing counters are null. No profile visits or follower attribution.'})
    eligible = {}
    for path in sorted(folder.glob('*.json')):
        if path.name == 'summary.json':
            continue
        snapshot = json.loads(path.read_text())
        for record in snapshot.get('posts', []):
            if record.get('strategy') not in ('modern-life-v2', 'human-conversation-v3') or not record.get('created_at'):
                continue
            age = (datetime.fromisoformat(snapshot['observed_at']) - datetime.fromisoformat(record['created_at'].replace('Z', '+00:00'))).total_seconds() / 3600
            if 24 <= age <= 72:
                eligible[record['url']] = record  # one observation per post
    groups = {}
    for bucket in ('relatable', 'practical', 'conversation', 'timely', 'experiment'):
        sample = [r for r in eligible.values() if r['bucket'] == bucket]
        groups[bucket] = {'posts_observed_24_to_72h': len(sample)}
        for metric in ('views', 'likes', 'replies', 'reposts'):
            values = [r[metric] for r in sample if r[metric] is not None]
            groups[bucket]['median_' + metric] = median(values) if values else None
    write_json(folder / 'summary.json', {'updated_at': now.isoformat(), 'buckets': groups,
               'limitations': 'Partial visible-profile coverage; ages vary 24-72h. Descriptive engagement, not causal growth or follows. No automatic winner selection.'})
    print('Metrics snapshot saved: ' + str(len(records)) + ' known visible posts')


if __name__ == '__main__':
    main()
