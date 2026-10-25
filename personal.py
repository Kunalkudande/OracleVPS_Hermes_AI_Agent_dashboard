"""Private briefing, descriptive content analysis and source-backed local recall.

No network fetches or model calls. Imported source text is data, never instructions.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit

IST = timezone(timedelta(hours=5, minutes=30))
STOP = set('a an the what which how why when where is are was were do does can could should would tell me about explain my your to of in on and or for with from this that it'.split())


def stamp():
    return datetime.now(timezone.utc).isoformat()


def safe_url(value):
    try:
        u = urlsplit(value)
        return value if u.scheme == 'https' and u.hostname and not u.username and not u.password else ''
    except (ValueError, TypeError):
        return ''


class Personal:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL, saved_at TEXT NOT NULL, published_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS reports (kind TEXT PRIMARY KEY, data TEXT NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.folder / 'personal.sqlite3', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, title, body, url='', kind='note', published_at=None):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
            raise ValueError('Use a title of 1–200 characters')
        if not isinstance(body, str) or not 1 <= len(body.strip()) <= 12000:
            raise ValueError('Use note text of 1–12000 characters')
        if not isinstance(url, str) or len(url) > 2000 or (url and not safe_url(url)):
            raise ValueError('Use an HTTPS source link, or leave it blank')
        identity = hashlib.sha256((url or title.strip() + '\n' + body.strip()).encode()).hexdigest()[:24]
        with self.connect() as db:
            db.execute('INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title=excluded.title, body=excluded.body, published_at=excluded.published_at',
                       (identity, title.strip(), body.strip(), url, kind, stamp(), published_at))
        return identity

    def search(self, question=''):
        if not isinstance(question, str) or len(question) > 500:
            raise ValueError('Use a question of at most 500 characters')
        words = list(dict.fromkeys(w for w in re.findall(r'[\w-]+', question.lower()) if w not in STOP))[:16]
        with self.connect() as db:
            total = db.execute('SELECT count(*) FROM notes').fetchone()[0]
            if words:
                clauses = ['(lower(title) LIKE ? ESCAPE "!" OR lower(body) LIKE ? ESCAPE "!")' for _ in words]
                args = []
                for word in words:
                    term = '%' + word.replace('!', '!!').replace('_', '!_').replace('%', '!%') + '%'
                    args.extend([term, term])
                rows = db.execute('SELECT * FROM notes WHERE ' + ' OR '.join(clauses), args).fetchall()
                rows = sorted(rows, key=lambda row: (sum((3 if w in row['title'].lower() else 0) + (1 if w in row['body'].lower() else 0) for w in words), row['saved_at']), reverse=True)[:8]
            else:
                rows = db.execute('SELECT * FROM notes ORDER BY saved_at DESC LIMIT 8').fetchall() if not question.strip() else []
        results = []
        for row in rows:
            sentences = re.split(r'(?<=[.!?])\s+|\n+', row['body'])
            if words:
                sentences.sort(key=lambda s: sum(w in s.lower() for w in words), reverse=True)
            item = dict(row)
            item.pop('body')
            item['excerpt'] = ' '.join(sentences[:2])[:650]
            results.append(item)
        return {'total': total, 'results': results,
                'answer': 'Relevant passages from your saved library are below.' if results else 'I could not find supporting passages in your saved library. Try a more specific keyword or save a note.',
                'method': 'Source excerpts ranked by keywords; these are not a generated synthesis.'}

    def sync(self, xroot, scrub=lambda value: value):
        xroot = Path(xroot)
        imported, skipped = 0, 0
        # Explicit research roots only; never scan credentials, browser data or run prompts.
        paths = sorted((xroot / 'source-evidence').glob('*.json'))[-2000:]
        paths += sorted((xroot / 'drafts').glob('????-??-??.json'))[-90:]
        for path in paths:
            try:
                if path.is_symlink() or path.stat().st_size > 4_000_000:
                    raise ValueError('Unsupported source file')
                data = json.loads(path.read_text(encoding='utf-8'))
                sources = [data] if path.parent.name == 'source-evidence' else [s for slot in data.get('slots', []) for s in slot.get('evidence', {}).get('sources', [])]
                for source in sources:
                    url = safe_url(source.get('url'))
                    body = source.get('excerpt') or source.get('text')
                    if not url or not isinstance(body, str) or not body.strip():
                        continue
                    title = source.get('title') or urlsplit(url).path.rstrip('/').split('/')[-1].replace('-', ' ') or urlsplit(url).hostname
                    self.save(scrub(str(title))[:200], scrub(body)[:12000], url, 'research', source.get('published_at'))
                    imported += 1
            except (OSError, ValueError, TypeError, AttributeError):
                skipped += 1
        return {'sources_processed': imported, 'files_skipped': skipped, 'at': stamp()}

    def store_report(self, kind, report):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO reports VALUES (?, ?)', (kind, json.dumps(report)))
        return report

    def report(self, kind):
        with self.connect() as db:
            row = db.execute('SELECT data FROM reports WHERE kind=?', (kind,)).fetchone()
        return json.loads(row[0]) if row else None

    def analyst(self, metrics, now=None):
        now = now or datetime.now(timezone.utc)
        try:
            observed = datetime.fromisoformat(metrics['updated_at'].replace('Z', '+00:00'))
            fresh = observed.tzinfo is not None and timedelta(0) <= now - observed <= timedelta(days=7)
        except (KeyError, TypeError, ValueError):
            fresh = False
        buckets = (metrics or {}).get('buckets', {})
        observations, experiments = [], []
        comparable = []
        for name, data in buckets.items():
            count = data.get('posts_observed_24_to_72h', 0)
            views = data.get('median_views')
            observations.append({'bucket': name, 'posts': count, 'median_views': views,
                                 'median_likes': data.get('median_likes'), 'median_replies': data.get('median_replies')})
            if isinstance(count, int) and count >= 5 and isinstance(views, (int, float)):
                comparable.append((views, name, count))
        if not fresh:
            experiments.append('Metrics are missing or older than seven days. Refresh the metrics collector before making content decisions.')
        elif len(comparable) >= 2:
            top = max(comparable)
            experiments.append(f"Try one extra {top[1]} post in a future editorial plan; its observed median views were {top[0]} across {top[2]} sampled posts. Hold posting time and format as consistent as practical and review after two weeks.")
        else:
            experiments.append('Collect at least five sampled posts per bucket across two buckets before comparing reach. Meanwhile, test a concrete example in an explainer while keeping timing and format consistent.')
        report = {'generated_at': now.isoformat(), 'metrics_at': (metrics or {}).get('updated_at'),
                  'observations': observations, 'experiments': experiments,
                  'scope': 'Existing cumulative metrics, not a seven-day performance window. Visible counters at ages 24–72h; missing counters remain unknown. Descriptive comparisons do not establish causation. Suggestions never change publishing automatically.'}
        return self.store_report('analyst', report)

    def briefing(self, snapshot, now=None):
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(IST).date().isoformat()
        upcoming = [j for j in snapshot['jobs'] if j.get('enabled') and str(j.get('next_run_at') or '')[:10]]
        upcoming.sort(key=lambda j: j['next_run_at'])
        research = []
        with self.connect() as db:
            rows = db.execute("SELECT title, url, published_at FROM notes WHERE kind='research' AND published_at IS NOT NULL ORDER BY published_at DESC LIMIT 100").fetchall()
        for row in rows:
            try:
                published = datetime.fromisoformat(row['published_at'].replace('Z', '+00:00'))
                if published.tzinfo and timedelta(0) <= now - published <= timedelta(hours=48):
                    research.append(dict(row))
            except (ValueError, TypeError):
                continue
        report = {'generated_at': now.isoformat(), 'date': day, 'issues': snapshot['issues'],
                  'warnings': snapshot['warnings'], 'upcoming': upcoming[:6],
                  'posts': [p for p in snapshot['posts'] if p['date'] == day], 'research': research[:5],
                  'research_note': 'Saved research published within 48 hours. This is not a fresh web scan.'}
        return self.store_report('briefing', report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['briefing', 'analyst', 'sync', 'ask', 'save', 'reports'])
    parser.add_argument('question', nargs='?', default='')
    parser.add_argument('--state', default=str(Path.home() / '.local/state/hermes-control/personal'))
    parser.add_argument('--home', default=str(Path.home() / '.hermes'))
    parser.add_argument('--xroot', default=str(Path.home() / 'tweetytweets'))
    args = parser.parse_args()
    from server import Hermes, scrub
    personal = Personal(args.state)
    if args.command in ('sync', 'briefing'):
        synced = personal.sync(args.xroot, scrub)
    if args.command == 'sync':
        print(json.dumps(synced))
    elif args.command == 'save':
        note = json.loads(sys.stdin.read(16000))
        identity = personal.save(scrub(note.get('title', '')), scrub(note.get('text', '')), note.get('url', ''))
        print(json.dumps({'saved': identity}))
    elif args.command == 'reports':
        print(json.dumps({'briefing': personal.report('briefing'), 'analyst': personal.report('analyst')}, ensure_ascii=False, indent=2))
    elif args.command == 'ask':
        print(json.dumps(personal.search(args.question), ensure_ascii=False, indent=2))
    else:
        backend = Hermes(args.home, args.xroot, str(Path.home() / '.local/bin/hermes'), Path(args.state).parent / 'actions.jsonl')
        snapshot = backend.snapshot()
        report = personal.briefing(snapshot) if args.command == 'briefing' else personal.analyst(snapshot['metrics'])
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
