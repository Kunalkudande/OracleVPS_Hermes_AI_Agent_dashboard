"""Security boundaries and scheduler compatibility; no live Hermes mutations."""
from datetime import datetime, timezone, timedelta
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from server import App, Hermes, scrub, publication_status


class DashboardTests(unittest.TestCase):
    def test_personal_routes_require_auth_csrf_and_save_recall(self):
        self.assertEqual(self.request('/api/workspace')[0], 401)
        self.login()
        self.assertEqual(self.request('/api/knowledge/save', {'title': 'Memory', 'text': 'Source-backed agent memory'}, csrf=False)[0], 403)
        code, data, _ = self.request('/api/knowledge/save', {'title': 'Memory', 'text': 'Source-backed agent memory'})
        self.assertEqual(code, 200)
        code, data, _ = self.request('/api/knowledge/search', {'question': 'agent memory'})
        self.assertEqual(code, 200)
        self.assertEqual(data['results'][0]['title'], 'Memory')
        self.assertEqual(self.request('/api/knowledge/save', {'title': [], 'text': 'bad'})[0], 400)
        code, data, _ = self.request('/api/workspace')
        self.assertEqual(code, 200)
        self.assertEqual(data['knowledge']['total'], 1)

    def test_retired_and_uncertain_slots_are_not_published(self):
        self.assertEqual(publication_status({'posted':True,'skipped':'overdue'},{}),'skipped')
        self.assertEqual(publication_status({'posted':True},{'status':'uncertain'}),'uncertain')
        self.assertEqual(publication_status({'text':'draft'},{'status':'failed'}),'failed')
        self.assertEqual(publication_status({'posted':True},{}),'unknown')
        self.assertEqual(publication_status({}, {'status':'verified','url':'https://x.com/account/status/1'}),'published')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / '.hermes'
        self.jobsfile = self.home / 'cron/jobs.json'
        self.jobsfile.parent.mkdir(parents=True)
        self.jobsfile.write_text(json.dumps({'jobs': [{'id': 'safe-job', 'name': 'Example', 'enabled': False, 'last_status': 'error', 'last_error': 'password=secret123', 'prompt': 'DO NOT EXPOSE THIS', 'next_run_at': '2000-01-01T00:00:00Z', 'schedule': {'display': 'every 5m'}}]}))
        out = self.home / 'cron/output/safe-job'
        out.mkdir(parents=True)
        (out / '2026-10-19_00-00-00.md').write_text('## Prompt\nDO NOT EXPOSE THIS\n## Output\n[CRON_FAILURE]\naccess_token=abc123\nFailed to deliver')
        self.backend = Hermes(self.home, self.root / 'x', str(self.root / 'hermes'), self.root / 'audit.jsonl')
        salt = bytes.fromhex('a1' * 16)
        auth = {'salt': salt.hex(), 'hash': hashlib.scrypt(b'test-password', salt=salt, n=16384, r=8, p=1).hex()}
        self.app = App(('127.0.0.1', 0), self.backend, auth)
        self.port = self.app.server_port
        self.thread = threading.Thread(target=self.app.serve_forever, daemon=True)
        self.thread.start()
        self.cookie, self.csrf = '', ''

    def tearDown(self):
        self.app.shutdown()
        self.app.server_close()
        self.temp.cleanup()

    def request(self, path, body=None, origin=True, csrf=True, host=None, extra_headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port)
        headers = {'Host': host or f'127.0.0.1:{self.port}', 'Cookie': self.cookie}
        if origin:
            headers['Origin'] = f'http://127.0.0.1:{self.port}'
        if csrf:
            headers['X-CSRF-Token'] = self.csrf
        if body is not None:
            headers['Content-Type'] = 'application/json'
        headers.update(extra_headers or {})
        conn.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, headers)
        response = conn.getresponse()
        raw = response.read()
        result = json.loads(raw) if 'application/json' in response.getheader('Content-Type', '') else raw
        cookie = response.getheader('Set-Cookie')
        status = response.status
        conn.close()
        return status, result, cookie

    def login(self):
        status, data, cookie = self.request('/api/login', {'password': 'test-password'})
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.cookie, self.csrf = cookie.split(';')[0], data['csrf']

    def test_authentication_and_logout(self):
        self.assertEqual(self.request('/api/status')[0], 401)
        self.assertEqual(self.request('/api/login', {'password': 'wrong'})[0], 401)
        self.login()
        self.assertEqual(self.request('/api/session')[0], 200)
        self.assertEqual(self.request('/api/logout', {})[0], 200)
        self.assertEqual(self.request('/api/session')[0], 401)

    def test_cross_origin_and_csrf_rejected(self):
        self.assertEqual(self.request('/api/login', {'password': 'test-password'}, origin=False)[0], 403)
        self.assertEqual(self.request('/api/health', host='evil.example')[0], 403)
        self.login()
        self.assertEqual(self.request('/api/jobs/safe-job/resume', {}, csrf=False)[0], 403)
        self.assertEqual(self.request('/api/jobs/safe-job/run', {})[0], 404)

    def test_rate_limit(self):
        for _ in range(8):
            self.assertEqual(self.request('/api/login', {'password': 'wrong'})[0], 401)
        self.assertEqual(self.request('/api/login', {'password': 'wrong'})[0], 429)

    def test_report_traversal_and_prompt_redaction(self):
        self.login()
        status, report, _ = self.request('/api/reports/safe-job/2026-10-19_00-00-00.md')
        self.assertEqual(status, 200)
        self.assertNotIn('DO NOT EXPOSE', report['text'])
        self.assertNotIn('abc123', report['text'])
        with self.assertRaises(ValueError):
            self.backend.output('safe-job', '../jobs.json')
        with self.assertRaises(ValueError):
            self.backend.output('../safe-job', 'file.md')

    def test_projection_unknowns_and_paused_schedule(self):
        with patch.object(self.backend, 'command', return_value=None):
            data = self.backend.snapshot()
        self.assertNotIn('DO NOT EXPOSE', json.dumps(data))
        self.assertNotIn('secret123', json.dumps(data))
        self.assertEqual(data['jobs'][0]['status'], 'paused')
        self.assertIsNone(data['jobs'][0]['next_run_at'])
        self.assertFalse(data['jobs'][0]['stale'])
        self.assertEqual(data['services'][0]['status'], 'unavailable')
        self.assertEqual(data['backup']['status'], 'unverified')
        self.assertEqual(data['history'][0]['status'], 'failed')

    def test_scheduler_mutation_must_be_verified(self):
        class Result:
            returncode = 0
        with patch.object(self.backend, 'command', return_value=Result()) as command:
            with self.assertRaises(RuntimeError):
                self.backend.action('safe-job', 'resume')
            command.assert_called_once_with([str(self.root / 'hermes'), 'cron', 'resume', 'safe-job'], timeout=20)
        def update(argv, timeout):
            data = json.loads(self.jobsfile.read_text())
            data['jobs'][0]['enabled'] = True
            self.jobsfile.write_text(json.dumps(data))
            return Result()
        with patch.object(self.backend, 'command', side_effect=update):
            self.assertEqual(self.backend.action('safe-job', 'resume')['message'], 'Schedule resumed')
        with self.assertRaises(ValueError):
            self.backend.action('safe-job;whoami', 'pause')
        with self.assertRaises(ValueError):
            self.backend.action('safe-job', 'run')

    def test_session_expiry(self):
        self.login()
        token = self.cookie.split('=', 1)[1]
        self.app.sessions[token]['expires'] = 0
        self.assertEqual(self.request('/api/status')[0], 401)

    def test_secret_scrubbing(self):
        result = scrub('Bearer abcd.efgh access_token=secret refresh_token: another password="hidden"')
        for secret in ['abcd.efgh', 'secret', 'another', 'hidden']:
            self.assertNotIn(secret, result)

    def test_verified_bridge_has_separate_client_rate_limits(self):
        self.app.auth['bridge_token'] = 'test-bridge-token'
        headers = {'X-Hermes-Bridge-Token': 'test-bridge-token', 'X-Hermes-Rate-Key': 'ab' * 32}
        for _ in range(8):
            self.assertEqual(self.request('/api/login', {'password': 'wrong'}, extra_headers=headers)[0], 401)
        self.assertEqual(self.request('/api/login', {'password': 'wrong'}, extra_headers=headers)[0], 429)
        headers['X-Hermes-Rate-Key'] = 'cd' * 32
        self.assertEqual(self.request('/api/login', {'password': 'test-password'}, extra_headers=headers)[0], 200)

    def test_spoofed_rate_key_cannot_bypass_limits(self):
        self.app.auth['bridge_token'] = 'test-bridge-token'
        for _ in range(8):
            self.request('/api/login', {'password': 'wrong'})
        headers = {'X-Hermes-Bridge-Token': 'incorrect-token', 'X-Hermes-Rate-Key': 'cd' * 32}
        self.assertEqual(self.request('/api/login', {'password': 'test-password'}, extra_headers=headers)[0], 429)


if __name__ == '__main__':
    unittest.main()
