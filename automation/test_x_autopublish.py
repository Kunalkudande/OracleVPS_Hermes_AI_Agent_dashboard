import unittest
from unittest.mock import patch
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
import json
import tempfile
from zoneinfo import ZoneInfo
import x_autopublish as publisher
from x_autopublish import validate


class PublishingGuards(unittest.TestCase):
    def setUp(self):
        self.config = {'max_chars': 280, 'max_chars_verified': True,
                       'autopost': {'enabled': True, 'approval_required': False}}
        self.text = 'A small useful project is a good place to start learning something new.'

    def test_permission_is_required(self):
        self.config['autopost']['enabled'] = False
        with self.assertRaises(ValueError):
            validate(self.text, self.config, [], {'kind': 'opinion'})

    def test_free_account_length_limit(self):
        with self.assertRaises(ValueError):
            validate('word ' * 57, self.config, [], {'kind': 'opinion'})

    def test_duplicate_is_rejected(self):
        with self.assertRaises(ValueError):
            validate(self.text, self.config, [{'text': self.text.upper()}], {'kind': 'opinion'})

    def test_news_requires_evidence(self):
        with self.assertRaises(ValueError):
            validate(self.text, self.config, [], {'kind': 'news'})

    def test_original_opinion_allowed(self):
        validate(self.text, self.config, [], {'kind': 'opinion'})

    def test_uncertain_send_is_not_submitted_twice(self):
        now = datetime(2026, 10, 17, 9, 30, tzinfo=ZoneInfo('Asia/Kolkata'))
        draft = {'date': '2026-10-17', 'slot': '09:00', 'text': self.text}
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'slots': [{'slot': '09:00', 'evidence': {'kind': 'opinion'}}]}))
            stack.enter_context(patch.object(publisher, 'ROOT', root))
            stack.enter_context(patch.object(publisher, 'JOURNAL', root / 'journal.json'))
            stack.enter_context(patch.object(publisher.sys if hasattr(publisher, 'sys') else __import__('sys'), 'argv', ['publisher']))
            stack.enter_context(patch.object(publisher.due, 'due', return_value=draft))
            stack.enter_context(patch.object(publisher.due, 'plan_path', return_value=plan))
            stack.enter_context(patch.object(publisher.due, 'now_tz', return_value=now))
            stack.enter_context(patch.object(publisher.settings, 'load', return_value=self.config))
            stack.enter_context(patch.object(publisher.post, 'load_state', return_value={'posts': []}))
            stack.enter_context(patch.object(publisher.post, 'require_session', return_value={}))
            calls = []

            async def uncertain(*args, **kwargs):
                claim = json.loads((root / 'journal.json').read_text())['2026-10-17/09:00']
                self.assertEqual(claim['status'], 'uncertain')
                calls.append('submit')
                return {'verified': False}

            async def absent(*args, **kwargs):
                return []

            stack.enter_context(patch.object(publisher.post, 'do_post', side_effect=uncertain))
            stack.enter_context(patch.object(publisher.post, '_read_profile', side_effect=absent))
            with self.assertRaises(RuntimeError):
                publisher.main()
            publisher.main()
            publisher.main()
            self.assertEqual(calls, ['submit'])


if __name__ == '__main__':
    unittest.main()
