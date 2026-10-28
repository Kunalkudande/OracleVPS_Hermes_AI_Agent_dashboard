"""One bounded API request per forced SSH command; no shell, files or destinations in input."""
import http.client
import json
from pathlib import Path
import re
import signal
import sys
import os

GET = re.compile(r"/api/(?:health|session|status|workspace|reports/[A-Za-z0-9_-]{1,100}/[A-Za-z0-9_.-]{1,150}\.md)")
POST = re.compile(r"/api/(?:login|logout|knowledge/(?:search|save)|jobs/[A-Za-z0-9_-]{1,100}/(?:pause|resume))")


def validate(value):
    if not isinstance(value, dict):
        raise ValueError("Expected request")
    method, path = value.get("method"), value.get("path")
    if not isinstance(path, str) or ".." in path or not ((method == "GET" and GET.fullmatch(path)) or (method == "POST" and POST.fullmatch(path))):
        raise ValueError("Unsupported route")
    body = value.get("body", "")
    if not isinstance(body, str) or len(body.encode()) > 4096:
        raise ValueError("Request too large")
    if method == "POST" and not isinstance(json.loads(body), dict):
        raise ValueError("Expected JSON object")
    for name, limit in [("cookie", 4096), ("csrf", 200), ("rate_key", 64)]:
        text = value.get(name, "")
        if not isinstance(text, str) or len(text) > limit or "\r" in text or "\n" in text:
            raise ValueError("Invalid header")
    if not re.fullmatch(r"[a-f0-9]{64}", value.get("rate_key", "")):
        raise ValueError("Missing rate limit key")
    return value


def main():
    if os.environ.get('SSH_ORIGINAL_COMMAND') == 'hermes-webui-api':
        # -I intentionally excludes this directory; add only our fixed code path.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from webui_bridge import run_ssh
        signal.alarm(15)
        raw = sys.stdin.buffer.read(5_400_001)
        if len(raw) > 5_400_000:
            raise ValueError('Request too large')
        return run_ssh(json.loads(raw))
    signal.alarm(30)
    value = validate(json.loads(sys.stdin.buffer.read(16001)))
    auth = json.loads((Path.home() / ".local/state/hermes-control/auth.json").read_text())
    headers = {"Host": "127.0.0.1:9119", "Origin": "http://127.0.0.1:9119",
               "Content-Type": "application/json", "Cookie": value.get("cookie", ""),
               "X-CSRF-Token": value.get("csrf", ""), "X-Hermes-Rate-Key": value["rate_key"],
               "X-Hermes-Bridge-Token": auth["bridge_token"]}
    conn = http.client.HTTPConnection("127.0.0.1", 9119, timeout=24)
    conn.request(value["method"], value["path"], body=value.get("body", "").encode(), headers=headers)
    response = conn.getresponse()
    body = response.read(900001)
    if len(body) > 900000:
        raise ValueError("Response too large")
    result = {"status": response.status, "body": body.decode("utf-8"),
              "cookies": [val for key, val in response.getheaders() if key.lower() == "set-cookie"]}
    conn.close()
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print('Dashboard bridge request rejected', file=sys.stderr)
        sys.exit(1)
