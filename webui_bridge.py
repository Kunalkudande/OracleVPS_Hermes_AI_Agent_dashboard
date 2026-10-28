"""Authenticated, fixed-destination streaming bridge for Hermes WebUI.

Runs only under bridge.py's forced SSH command, or the loopback control server.
The dashboard session remains the authority; browser-supplied identity headers
are never forwarded. Upstream WebUI retains its own CSRF checks.
"""
import base64
import http.client
import json
import re
import time
from urllib.parse import unquote, urlsplit

MAX_BODY = 4_000_000
METHODS = {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'}


def validate(value):
    if not isinstance(value, dict) or value.get('method') not in METHODS:
        raise ValueError('Invalid method')
    path = value.get('path')
    if not isinstance(path, str) or len(path) > 16000:
        raise ValueError('Invalid path')
    decoded = unquote(path)
    parsed = urlsplit(path)
    if (not path.startswith('/') or path.startswith('//') or parsed.scheme or
            parsed.netloc or parsed.fragment or '\\' in decoded or
            any(ord(c) < 32 or ord(c) == 127 for c in decoded) or
            '..' in unquote(parsed.path).split('/')):
        raise ValueError('Invalid path')
    for key, limit in [('cookie', 8192), ('content_type', 250), ('csrf', 512), ('last_event_id', 512)]:
        item = value.get(key, '')
        if not isinstance(item, str) or len(item) > limit or any(ord(c) < 32 for c in item):
            raise ValueError('Invalid header')
    body = base64.b64decode(value.get('body', ''), validate=True)
    if len(body) > MAX_BODY:
        raise ValueError('Request too large')
    return body


def session_valid(cookie):
    conn = http.client.HTTPConnection('127.0.0.1', 9119, timeout=8)
    try:
        conn.request('GET', '/api/session', headers={'Host': '127.0.0.1:9119', 'Cookie': cookie})
        response = conn.getresponse()
        response.read(4096)
        return response.status == 200
    finally:
        conn.close()


def response_headers(headers):
    allowed = {'content-type', 'content-disposition', 'content-security-policy',
               'content-security-policy-report-only', 'permissions-policy'}
    result = []
    if any(name.lower() == 'content-type' and value.startswith('text/html') for name, value in headers):
        # Remove only the old mount-scoped cookie from the initial integration;
        # the dashboard's same-named Path=/ cookie must remain intact.
        result.append(['set-cookie', 'hermes_session=; Path=/hermes/; HttpOnly; SameSite=Strict; Max-Age=0'])
    for name, value in headers:
        name = name.lower()
        if name in allowed:
            if name.startswith('content-security-policy'):
                value = value.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
            result.append([name, value])
        elif name == 'location':
            # Never redirect the browser outside this private mount.
            if value.startswith('/') and not value.startswith('//'):
                result.append(['location', '/hermes' + value])
        elif name == 'set-cookie':
            # Upstream cookies belong exclusively to the embedded interface.
            value = re.sub(r'(?i);\s*Path=[^;]*', '', value)
            value = re.sub(r'(?i);\s*Domain=[^;]*', '', value)
            result.append(['set-cookie', value + '; Path=/hermes/'])
    result.extend([['cache-control', 'no-store'], ['x-frame-options', 'SAMEORIGIN'],
                   ['x-content-type-options', 'nosniff'], ['referrer-policy', 'same-origin']])
    return result


def forward(value, start, write):
    body = validate(value)
    if not session_valid(value.get('cookie', '')):
        start(401, [['content-type', 'text/html; charset=utf-8'], ['cache-control', 'no-store']])
        write(b'<!doctype html><title>Sign in required</title><p>Your control-room session expired. <a href="/" target="_top">Sign in again</a>.</p>')
        return
    headers = {'Host': '127.0.0.1:8787', 'Origin': 'http://127.0.0.1:8787',
               'Referer': 'http://127.0.0.1:8787/', 'X-Hermes-Control-User': 'control-room-owner',
               'Cookie': value.get('cookie', ''), 'Accept-Encoding': 'identity',
               'Content-Type': value.get('content_type', 'application/json'),
               'X-Hermes-CSRF-Token': value.get('csrf', ''),
               'Last-Event-ID': value.get('last_event_id', '')}
    conn = http.client.HTTPConnection('127.0.0.1', 8787, timeout=285)
    try:
        conn.request(value['method'], value['path'], body=body, headers=headers)
        response = conn.getresponse()
        start(response.status, response_headers(response.getheaders()))
        until = time.monotonic() + 280
        # read1 forwards SSE immediately instead of waiting to fill a buffer.
        while time.monotonic() < until:
            chunk = response.read1(65536)
            if not chunk:
                break
            write(chunk)
    finally:
        conn.close()


def run_ssh(value):
    import signal
    import sys
    signal.alarm(290)
    def start(status, headers):
        sys.stdout.buffer.write(json.dumps({'status': status, 'headers': headers}).encode() + b'\n')
        sys.stdout.buffer.flush()
    def write(chunk):
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    forward(value, start, write)
