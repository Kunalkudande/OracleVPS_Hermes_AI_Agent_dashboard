"""Private employer watchlist, explainable matching, application tracker and daily digest."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import uuid

from job_sources import canonical_url, collect_all

IST = timezone(timedelta(hours=5, minutes=30))
STATES = ('new', 'saved', 'applied', 'interview', 'offer', 'rejected', 'dismissed')
SKILLS = {
    'TypeScript': r'\btypescript\b', 'JavaScript': r'\bjavascript\b',
    'React': r'\breact(?:\.js|js)?\b', 'Next.js': r'\bnext\.?js\b', 'Angular': r'\bangular(?:js)?\b',
    'Node.js': r'\bnode(?:\.?js)?\b', 'NestJS': r'\bnest\.?js\b', 'Express': r'\bexpress(?:\.js)?\b',
    'PostgreSQL': r'\bpostgres(?:ql)?\b', 'SQL': r'\bsql\b', 'MongoDB': r'\bmongo(?:db)?\b',
    'Prisma': r'\bprisma\b', 'Redis': r'\bredis\b', 'RabbitMQ': r'\brabbitmq\b',
    'REST APIs': r'\brest(?:ful)?\b', 'Microservices': r'\bmicroservices?\b', 'Docker': r'\bdocker\b',
    'Python': r'\bpython\b', 'AWS': r'\baws\b|amazon web services', 'Azure': r'\bazure\b',
    'Tailwind': r'\btailwind\b', 'LLM APIs': r'\bllms?\b|generative ai|agentic|gemini api|openai api',
    'Java': r'\bjava\b', 'Spring Boot': r'\bspring(?: boot)?\b', '.NET': r'\.net\b|\bc#\b',
    'Go': r'\bgolang\b|\bgo (?:programming|language)\b', 'Kubernetes': r'\bkubernetes\b',
    'GraphQL': r'\bgraphql\b', 'CI/CD': r'ci/cd|github actions|continuous integration',
    'Testing': r'\b(?:jest|vitest|playwright|cypress|unit testing|automated tests?)\b',
    'GCP': r'\bgcp\b|google cloud', 'LangChain': r'\blangchain\b',
    'Django': r'\bdjango\b', 'FastAPI': r'\bfastapi\b', 'React Native': r'\breact native\b',
    'Flutter': r'\bflutter\b', 'Kafka': r'\bkafka\b', 'Terraform': r'\bterraform\b',
}


def stamp(now=None):
    return (now or datetime.now(timezone.utc)).isoformat()


def normalize(text):
    return re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()


def experience(text):
    """Extract required experience, excluding company age and nice-to-have sections."""
    text = re.split(r'(?im)^\s*(?:nice[- ]to[- ]have|good to have|preferred qualifications|bonus)\b', text)[0]
    expressions = []
    text = re.sub(r'\bup to two years\b', 'up to 2 years', text, flags=re.I)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        up_to = re.search(r'(?:freshers? welcome[^.\n]{0,35}|up to\s+)(\d{1,2})\s*years?', line, re.I)
        if up_to:
            expressions.append((0, int(up_to[1]), line.strip()[:220]))
            continue
        for m in re.finditer(r'\b(\d{1,2})(?:\s*(?:[-–—]|to)\s*(\d{1,2}))?\s*(\+|plus)?\s*(?:years?|yrs?)\b', line, re.I):
            around = line[max(0, m.start()-40):m.end()+120]
            header = len(line) < 90 and bool(m[2] or m[3]) and ('years' in line.lower())
            if not header and not re.search(r'experience|professional|hands.on|building|develop', around + (' ' + lines[i-1] if i else ''), re.I):
                continue
            lo, hi = int(m[1]), int(m[2]) if m[2] else None
            expressions.append((lo, hi, line.strip()[:220]))
    if not expressions:
        return None, None, None
    # A specific 5-year mandatory skill overrides a generic 1-3-year header.
    return max(expressions, key=lambda item: item[0])


def assess(job, profile):
    title, text = job['title'], job['description']
    role = normalize(title)
    primary = bool(re.search(r'full ?stack|mern|\bsoftware (?:engineer|developer)|\bsde\b|software development engineer', role))
    secondary = bool(re.search(r'backend|back end|node ?js|nestjs', role))
    reasons, gaps, excluded = [], [], []
    if not primary and not secondary:
        excluded.append('Outside selected role families')
    if re.search(r'\b(?:senior|sr|staff|principal|lead|architect|manager|intern|internship|sdet|qa|test engineer|engineer iii|engineer iv)\b', role):
        excluded.append('Seniority or role outside current search')
    if re.search(r'\b(?:frontend|front end|mobile|android|ios|react native|embedded|firmware|data engineer|devops)\b', role) and not re.search(r'full ?stack|mern', role):
        excluded.append('Specialist role outside selected full-stack/backend focus')
    if re.search(r'\b(?:internship|contract|parttime|part.time)\b', job.get('employment', ''), re.I):
        excluded.append('Not a regular full-time opening')
    location = job['location'].lower()
    aliases = {'Pune': r'\bpune\b', 'Bengaluru': r'\bbengaluru\b|\bbangalore\b', 'Hyderabad': r'\bhyderabad\b', 'Mumbai': r'\bmumbai\b|\bbombay\b'}
    cities = [city for city in profile['locations'] if city in aliases and re.search(aliases[city], location)]
    remote = 'remote' in location or job.get('workplace', '').lower() == 'remote'
    india = bool(re.search(r'\bindia\b|\(ind?\)|\bin\b', location)) or bool(cities)
    # Worldwide is accepted only when explicitly stated; generic "remote" is unknown.
    worldwide = bool(re.search(r'worldwide|anywhere in the world|global remote', location))
    if not cities and not (profile.get('remote_india') and remote and (india or worldwide)):
        excluded.append('Location or India remote eligibility unconfirmed')
    else:
        reasons.append('Location matches: ' + job['location'])
    lo, hi, exp_evidence = experience(text)
    if lo is not None and lo > 3:
        excluded.append('Requires more than three years')
    if hi is not None and hi < 1:
        excluded.append('Experience band below current experience')
    if lo is None:
        gaps.append('Experience requirement not stated; confirm seniority')
    elif lo >= 2:
        gaps.append(f'Requires {lo}+ years; about one year full-time evidenced (internships separate)')
    else:
        reasons.append('Experience band matches early-career profile')
    known = set(profile['skills'])
    listed = set(profile.get('listed_only_skills', []))
    skills = [name for name, pattern in SKILLS.items() if re.search(pattern, text, re.I)]
    matches = [s for s in skills if s in known]
    uncertain = [s for s in skills if s in listed]
    missing = [s for s in skills if s not in known | listed]
    reasons.append('Resume evidence: ' + ', '.join(matches[:10]) if matches else 'Limited overlap with demonstrated stack')
    if uncertain:
        gaps.append('Listed in resume; depth to confirm: ' + ', '.join(uncertain))
    if missing:
        gaps.append('Posting mentions skills not evidenced: ' + ', '.join(missing[:7]) + ' (may include optional skills)')
    if re.search(r'\b(?:immediate(?:ly)?|(?:max(?:imum)?|within)\s*15\s*days)\b', text, re.I):
        gaps.insert(0, 'Immediate/short joining window; confirm acceptance of one-month negotiable notice')
    else:
        gaps.append('One-month negotiable notice: employer acceptance unconfirmed')
    foreign_stack = sum(s in skills for s in ('Java', 'Spring Boot', '.NET', 'Go', 'Django', 'FastAPI'))
    if foreign_stack >= 2 and 'Node.js' not in skills and 'NestJS' not in skills:
        excluded.append('Core backend stack not evidenced in resume')
    if len(matches) < 2:
        excluded.append('Insufficient demonstrated skill overlap')
    score = (35 if primary else 22) + min(35, len(matches)*5) + (15 if lo is not None and lo <= 1 else 5) + (10 if cities or india else 0)
    score -= min(20, len(missing)*2) + (12 if lo is not None and lo >= 2 else 0)
    # Pay is deliberately not a hard filter; only employer-published values are shown.
    fit = 'stretch' if lo is not None and lo >= 2 else 'possible' if lo is None or score < 70 else 'strong'
    salary = job.get('salary')
    if not salary:
        m = re.search(r'₹\s*\d+(?:\.\d+)?\s*(?:L|lakh|lakhs)?\s*(?:-|–|to)\s*₹?\s*\d+(?:\.\d+)?\s*(?:LPA|L|lakhs?)\b', text, re.I)
        salary = m[0] if m else None
    return dict(eligible=not excluded, priority='primary' if primary else 'secondary', score=max(0, score), fit=fit,
                reasons=reasons, gaps=gaps, excluded=excluded, skills=matches, experience_min=lo,
                experience_max=hi, experience_evidence=exp_evidence, salary=salary)


@contextmanager
def process_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class JobSearch:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'jobs.sqlite3'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                    reserved_at TEXT, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memberships (
                    source_id TEXT NOT NULL, job_id TEXT NOT NULL, present INTEGER NOT NULL,
                    checked_at TEXT NOT NULL, PRIMARY KEY(source_id,job_id));
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, company TEXT NOT NULL, ok INTEGER NOT NULL,
                    checked_at TEXT NOT NULL, count INTEGER NOT NULL, error TEXT);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, at TEXT NOT NULL, report TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS digests (day TEXT PRIMARY KEY, at TEXT NOT NULL, text TEXT NOT NULL,
                    ids TEXT NOT NULL, outcome TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS actions (at TEXT NOT NULL, job_id TEXT NOT NULL, old_status TEXT, new_status TEXT);
            ''')
        if os.name != 'nt':
            os.chmod(self.root, 0o700)
            os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def profile(self):
        path = self.root / 'profile.json'
        if not path.exists():
            raise ValueError('Job-search profile has not been configured')
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict) or not isinstance(data.get('skills'), list) or not isinstance(data.get('locations'), list):
            raise ValueError('Invalid search profile')
        return data

    def sync(self, sources, now=None, collector=collect_all):
        now = now or datetime.now(timezone.utc)
        at, profile = stamp(now), self.profile()
        if not isinstance(sources, list) or not 1 <= len(sources) <= 40 or len({s['id'] for s in sources}) != len(sources):
            raise ValueError('Expected 1-40 unique employer sources')
        results = collector(sources)
        total, eligible = 0, 0
        with self.db() as db:
            for source in results:
                sid = source['id']
                db.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?)',
                           (sid, source['company'], int(source['ok']), at, len(source['jobs']), source.get('error')))
                if not source['ok']:
                    continue
                db.execute('UPDATE memberships SET present=0, checked_at=? WHERE source_id=?', (at, sid))
                for job in source['jobs']:
                    total += 1
                    assessment = assess(job, profile)
                    identity = canonical_url(job['url'])
                    # One employer URL may contain several roles; retain distinct sections.
                    identity += '#' + normalize(job['company'] + ' ' + job['title'] + ' ' + job['location'])
                    jid = hashlib.sha256(identity.encode()).hexdigest()[:20]
                    existing = db.execute('SELECT id FROM jobs WHERE identity=?', (identity,)).fetchone()
                    if not assessment['eligible'] and not existing:
                        continue
                    eligible += int(assessment['eligible'])
                    payload = dict(job, assessment=assessment)
                    db.execute('''INSERT INTO jobs(id,identity,payload,first_seen,last_seen,updated_at) VALUES(?,?,?,?,?,?)
                                  ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,last_seen=excluded.last_seen,updated_at=excluded.updated_at''',
                               (jid, identity, json.dumps(payload, ensure_ascii=False), at, at, at))
                    db.execute('INSERT OR REPLACE INTO memberships VALUES(?,?,1,?)', (sid, jid, at))
            report = {'at': at, 'sources_ok': sum(s['ok'] for s in results), 'sources_total': len(sources),
                      'openings_scanned': total, 'matching_observations': eligible,
                      'failures': [{'company': s['company'], 'error': s.get('error')} for s in results if not s['ok']]}
            db.execute('INSERT INTO runs VALUES(?,?,?)', (uuid.uuid4().hex, at, json.dumps(report)))
            db.execute('DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY at DESC LIMIT 60)')
        return report

    def listing(self, now=None):
        now = now or datetime.now(timezone.utc)
        with self.db() as db:
            records = db.execute('SELECT * FROM jobs ORDER BY last_seen DESC LIMIT 1000').fetchall()
            source_rows = [dict(r) for r in db.execute('SELECT * FROM sources ORDER BY company')]
            members = db.execute('SELECT * FROM memberships').fetchall()
            latest = db.execute('SELECT report FROM runs ORDER BY at DESC LIMIT 1').fetchone()
            digest = db.execute('SELECT day,at,outcome FROM digests ORDER BY day DESC LIMIT 1').fetchone()
        by_source = {s['id']: s for s in source_rows}
        by_job = {}
        for m in members:
            by_job.setdefault(m['job_id'], []).append(m)
        rows = []
        for record in records:
            job = json.loads(record['payload'])
            links = by_job.get(record['id'], [])
            fresh = [m for m in links if m['present'] and by_source.get(m['source_id'], {}).get('ok') and
                     now - datetime.fromisoformat(m['checked_at']) < timedelta(hours=36)]
            # Absence is meaningful only after a successful source refresh.
            state = 'active' if fresh else 'closed' if links and all(not m['present'] for m in links) else 'unverified'
            job.update(id=record['id'], status=record['status'], first_seen=record['first_seen'],
                       last_seen=record['last_seen'], reserved_at=record['reserved_at'], listing_state=state)
            job.pop('description', None)
            rows.append(job)
        rows.sort(key=lambda j: (j['listing_state'] == 'active', j['assessment']['eligible'], j['assessment']['priority'] == 'primary', j['assessment']['score']), reverse=True)
        try:
            profile = self.profile()
        except ValueError:
            profile = None
        return {'jobs': rows[:200], 'total': len(rows), 'truncated': len(rows) > 200, 'sources': source_rows,
                'last_run': json.loads(latest['report']) if latest else None, 'last_digest': dict(digest) if digest else None,
                'profile': profile, 'states': STATES,
                'coverage': 'Curated employer watchlist only. Listed means observed on an employer feed/page, not a recruiter confirmation. Missing source data stays unverified.'}

    def detail(self, jid):
        with self.db() as db:
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (jid,)).fetchone()
        if not row:
            raise ValueError('Unknown job')
        return json.loads(row['payload'])

    def set_status(self, jid, status):
        if not isinstance(jid, str) or not re.fullmatch(r'[a-f0-9]{20}', jid) or status not in STATES:
            raise ValueError('Invalid job or status')
        with self.db() as db:
            old = db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()
            if not old:
                raise ValueError('Unknown job')
            db.execute('UPDATE jobs SET status=?,updated_at=? WHERE id=?', (status, stamp(), jid))
            db.execute('INSERT INTO actions VALUES(?,?,?,?)', (stamp(), jid, old['status'], status))
        return {'ok': True, 'id': jid, 'status': status}

    def digest(self, now=None, reserve=False):
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(IST).date().isoformat()
        data = self.listing(now)
        report = data['last_run']
        if not report or now - datetime.fromisoformat(report['at']) > timedelta(hours=2):
            raise ValueError('Refresh sources before preparing a digest')
        candidates = [j for j in data['jobs'] if j['listing_state'] == 'active' and j['assessment']['eligible'] and
                      j['status'] in ('new', 'saved') and not j['reserved_at']]
        # Avoid a single busy company consuming the whole daily shortlist.
        rows, company_counts = [], {}
        for j in candidates:
            company = j['company'].lower()
            if company_counts.get(company, 0) >= 2:
                continue
            rows.append(j)
            company_counts[company] = company_counts.get(company, 0) + 1
            if len(rows) == 5:
                break
        lines = [f'Hermes job shortlist | {day} | Prepared {now.astimezone(IST):%H:%M} IST',
                 f"Employer watchlist: {report['sources_ok']}/{report['sources_total']} sources checked; {report['openings_scanned']} openings scanned.", '']
        for i, job in enumerate(rows, 1):
            a = job['assessment']
            lines += [f"{i}. {job['company']} - {job['title']}",
                      f"{job['location']} | {a['fit'].title()} fit | Pay: {a['salary'] or 'undisclosed'}",
                      'Matches: ' + ', '.join(a['skills'][:6]),
                      'Check: ' + '; '.join(a['gaps'][:2])[:270], job['url'], '']
        if not rows:
            lines.append('No new matching openings in the successfully checked sources today. Previously shortlisted jobs remain in Jobs.')
        if report['failures']:
            lines.append('Coverage gaps: ' + ', '.join(x['company'] for x in report['failures'])[:250])
        lines.append('One-month negotiable notice; employer acceptance must be confirmed. Use Hermes or the Jobs dashboard to save and track applications.')
        message = '\n'.join(lines)
        # Native Hermes handles Telegram delivery/chunking; this ledger records handoff only.
        if reserve:
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM digests WHERE day=?', (day,)).fetchone():
                    return ''
                db.execute('INSERT INTO digests VALUES(?,?,?,?,?)', (day, stamp(now), message, json.dumps([j['id'] for j in rows]), 'prepared_for_native_delivery'))
                db.executemany('UPDATE jobs SET reserved_at=? WHERE id=?', [(stamp(now), j['id']) for j in rows])
        return message


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['sync', 'list', 'detail', 'status', 'preview', 'daily'])
    parser.add_argument('id', nargs='?')
    parser.add_argument('status', nargs='?')
    parser.add_argument('--state', default=str(Path.home() / '.local/state/hermes-control/jobsearch'))
    args = parser.parse_args()
    store = JobSearch(args.state)
    if args.command in ('sync', 'daily'):
        with process_lock(store.root / 'sync.lock'):
            sources = json.loads((store.root / 'sources.json').read_text(encoding='utf-8-sig'))
            report = store.sync(sources)
            if args.command == 'sync':
                print(json.dumps(report, indent=2))
            elif not report['sources_ok']:
                raise RuntimeError('All employer sources failed; no shortlist was claimed')
            else:
                text = store.digest(reserve=True)
                if text:
                    print(text)
    elif args.command == 'list':
        print(json.dumps(store.listing(), indent=2, ensure_ascii=False))
    elif args.command == 'detail':
        print(json.dumps(store.detail(args.id), indent=2, ensure_ascii=False))
    elif args.command == 'status':
        print(json.dumps(store.set_status(args.id, args.status)))
    else:
        print(store.digest())


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Job search failed: ' + type(error).__name__ + '. Inspect the Jobs source-health view; no applications were submitted.', file=sys.stderr)
        sys.exit(1)
