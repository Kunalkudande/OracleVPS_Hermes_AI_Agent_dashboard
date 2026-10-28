import base64
import unittest
from unittest.mock import patch
import webui_bridge as bridge


class WebUIBridgeTests(unittest.TestCase):
    def test_webui_has_distinct_cookie_and_migrates_only_old_mount_cookie(self):
        from pathlib import Path
        unit = Path(__file__).with_name('hermes-webui.service').read_text()
        self.assertIn('HERMES_WEBUI_COOKIE_NAME=hermes_webui_session', unit)
        cookies = [v for k, v in bridge.response_headers([('Content-Type', 'text/html')]) if k == 'set-cookie']
        self.assertEqual(cookies, ['hermes_session=; Path=/hermes/; HttpOnly; SameSite=Strict; Max-Age=0'])

    def test_fixed_destination_rejects_injection_and_traversal(self):
        for path in ['https://evil.example/', '//evil.example/', '/%2e%2e/auth', '/api/\r\nX: bad', '/a%5cb', '/api/status#x']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                bridge.validate({'method': 'GET', 'path': path})
        self.assertEqual(bridge.validate({'method': 'POST', 'path': '/api/chat',
                                        'body': base64.b64encode(b'{}').decode()}), b'{}')
        with self.assertRaises(ValueError):
            bridge.validate({'method': 'CONNECT', 'path': '/'})
        with self.assertRaises(ValueError):
            bridge.validate({'method': 'GET', 'path': '/', 'cookie': 'x\r\nX: injected'})

    def test_expired_dashboard_session_cannot_reach_webui(self):
        output, starts = [], []
        with patch.object(bridge, 'session_valid', return_value=False), patch.object(bridge.http.client, 'HTTPConnection') as upstream:
            bridge.forward({'method': 'GET', 'path': '/'}, lambda *args: starts.append(args), output.append)
            upstream.assert_not_called()
        self.assertEqual(starts[0][0], 401)
        self.assertIn(b'Sign in again', b''.join(output))

    def test_embedding_keeps_other_csp_rules_and_scopes_cookies(self):
        result = bridge.response_headers([
            ('Content-Security-Policy', "frame-ancestors 'none'; object-src 'none'; script-src 'self'"),
            ('X-Frame-Options', 'DENY'),
            ('Set-Cookie', 'webui=x; HttpOnly; Path=/; Domain=example.com; SameSite=Strict'),
            ('Location', '/login'), ('Server', 'private-info'), ('Location', '//evil.example')])
        headers = dict(result)
        self.assertEqual(headers['x-frame-options'], 'SAMEORIGIN')
        self.assertIn("frame-ancestors 'self'", headers['content-security-policy'])
        self.assertIn("object-src 'none'", headers['content-security-policy'])
        self.assertEqual(headers['location'], '/hermes/login')
        self.assertEqual(headers['set-cookie'], 'webui=x; HttpOnly; SameSite=Strict; Path=/hermes/')
        self.assertNotIn('server', headers)


if __name__ == '__main__':
    unittest.main()
