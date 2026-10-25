"""Data provenance, deduplication, source recall and conservative recommendations."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from personal import Personal


class PersonalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.personal = Personal(self.root / 'state')

    def tearDown(self):
        self.temp.cleanup()

    def test_sources_deduplicate_and_search_keeps_provenance(self):
        url = 'https://example.org/agents'
        first = self.personal.save('Agent memory', 'Agents can recall saved notes. Memory needs source provenance.', url)
        second = self.personal.save('Agent memory updated', 'Agents retain source references for memory.', url)
        self.assertEqual(first, second)
        self.personal.save('Unrelated', 'Bananas and oranges.')
        result = self.personal.search('What have I saved about agent memory?')
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['results'][0]['url'], url)
        self.assertIn('source references', result['results'][0]['excerpt'])
        self.assertEqual(self.personal.search("' OR 1=1 --")['results'], [])
        self.assertEqual(self.personal.search('unfindable')['results'], [])

    def test_import_only_explicit_research_and_idempotent(self):
        evidence = self.root / 'x/source-evidence'
        evidence.mkdir(parents=True)
        (evidence / 'good.json').write_text(json.dumps({'url': 'https://example.org/source', 'excerpt': 'Research source body', 'published_at': '2026-10-25T00:00:00Z'}))
        (evidence / 'broken.json').write_text('{')
        (self.root / 'x/config.json').write_text(json.dumps({'secret': 'never-import-this'}))
        self.personal.sync(self.root / 'x')
        self.personal.sync(self.root / 'x')
        self.assertEqual(self.personal.search()['total'], 1)
        self.assertFalse(self.personal.search('never-import-this')['results'])

    def test_unsafe_links_and_invalid_inputs_rejected(self):
        for url in ['javascript:alert(1)', 'https://user:password@example.org', 'file:///etc/passwd']:
            with self.assertRaises(ValueError):
                self.personal.save('Title', 'Body', url)
        with self.assertRaises(ValueError):
            self.personal.save('', 'Body')
        with self.assertRaises(ValueError):
            self.personal.search(['not a string'])

    def test_small_samples_missing_and_stale_metrics(self):
        now = datetime.now(timezone.utc)
        metrics = {'updated_at': now.isoformat(), 'buckets': {'ai_news': {'posts_observed_24_to_72h': 1, 'median_views': 13, 'median_replies': None}}}
        result = self.personal.analyst(metrics)
        self.assertIn('five sampled', result['experiments'][0])
        self.assertIsNone(result['observations'][0]['median_replies'])
        metrics['buckets']['ai_agents'] = {'posts_observed_24_to_72h': 6, 'median_views': 14}
        metrics['buckets']['ai_news']['posts_observed_24_to_72h'] = 6
        self.assertIn('ai_agents', self.personal.analyst(metrics)['experiments'][0])
        metrics['updated_at'] = (now - timedelta(days=8)).isoformat()
        self.assertIn('older than seven days', self.personal.analyst(metrics)['experiments'][0])

    def test_briefing_ist_date_and_recent_evidence_only(self):
        now = datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc)
        self.personal.save('Recent', 'Body', 'https://example.org/new', 'research', now.isoformat())
        self.personal.save('Old', 'Body', 'https://example.org/old', 'research', (now-timedelta(days=4)).isoformat())
        snapshot = {'jobs': [], 'issues': [], 'warnings': ['Missing plan'], 'posts': [{'date': '2026-10-25'}]}
        report = self.personal.briefing(snapshot, now)
        self.assertEqual(report['date'], '2026-10-25')
        self.assertEqual([r['title'] for r in report['research']], ['Recent'])
        self.assertEqual(len(report['posts']), 1)
        self.assertEqual(self.personal.report('briefing'), report)
