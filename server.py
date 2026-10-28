"""Hermes Control: private, loopback-only dashboard. Python 3.11+, no packages."""
import argparse
import base64
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit, unquote
from personal import Personal

ROOT = Path(__file__).resolve().parent
SAFE = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")
IST = timezone(timedelta(hours=5, minutes=30))


def scrub(text):
    text = str(text or "")
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+", r"\1[redacted]", text)
    text = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|encryption_key)[\s\"\x27:=]+)[^\s,}\"\x27]+', r'\1[redacted]', text)
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[private key redacted]", text, flags=re.S)
    return text


def read_json(path):
    # File size cap and no network/credentials/config discovery.
    if path.stat().st_size > 4_000_000:
        raise ValueError("Data file exceeds dashboard size limit")
    return json.loads(path.read_text(encoding="utf-8"))


def under(path, parent):
    return path.resolve().is_relative_to(parent.resolve())


def parse_time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None


def publication_status(slot, claim):
    if claim.get('status') == 'uncertain':
        return 'uncertain'
    if slot.get('tweet_url') or (claim.get('status') == 'verified' and claim.get('url')):
        return 'published'
    if slot.get('skipped'):
        return 'skipped'
    if claim.get('status') == 'failed':
        return 'failed'
    if slot.get('posted'):
        return 'unknown'
    return 'ready' if str(slot.get('text') or '').strip() else 'empty'


class Hermes:
    def __init__(self, home, xroot, cli, audit):
        self.home, self.xroot, self.cli, self.audit = Path(home), Path(xroot), cli, Path(audit)
        self.action_lock = threading.Lock()

    def personal(self):
        return Personal(self.audit.parent / 'personal')

    def workspace(self):
        personal = self.personal()
        return {'briefing': personal.report('briefing'), 'analyst': personal.report('analyst'),
                'knowledge': personal.search()}

    def jobs(self):
        data = read_json(self.home / "cron/jobs.json")
        rows = data.get("jobs", []) if isinstance(data, dict) else data
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Unsupported jobs file format")
        return rows

    def history(self, jobs):
        rows = []
        for job in jobs:
            jid = str(job.get("id", ""))
            if not SAFE.fullmatch(jid):
                continue
            parent = self.home / "cron/output" / jid
            # Atomic scheduler writes may briefly replace files during a read.
            try:
                files = sorted(parent.glob("*.md"), reverse=True)[:15]
                for path in files:
                    if not under(path, self.home / "cron/output"):
                        continue
                    excerpt = self.output(jid, path.name)
                    status = "failed" if re.search(r"(?im)\*\*Status:\*\*.*(?:fail|error)|^\[CRON_FAILURE\]|^Script exited with code [1-9]", excerpt) else "unknown"
                    if "**Status:** silent" in excerpt:
                        status = "silent"
                    elif status == "unknown" and re.search(r"(?im)\*\*Status:\*\*.*(?:success|ok)", excerpt):
                        status = "ok"
                    if files and path == files[0] and status == "unknown":
                        status = {"ok": "ok", "error": "failed"}.get(job.get("last_status"), "unknown")
                    stamp = path.stem
                    try:
                        at = datetime.strptime(stamp, "%Y-%m-%d_%H-%M-%S").replace(tzinfo=timezone.utc).isoformat()
                    except ValueError:
                        at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
                    rows.append({"job_id": jid, "name": job.get("name", jid), "file": path.name, "at": at, "status": status})
            except OSError:
                continue
        return sorted(rows, key=lambda row: row["at"], reverse=True)[:45]

    def output(self, jid, filename):
        if not SAFE.fullmatch(jid) or not re.fullmatch(r"[\w.-]{1,150}\.md", filename) or ".." in filename:
            raise ValueError("Invalid report identifier")
        parent = self.home / "cron/output"
        path = parent / jid / filename
        if not under(path, parent):
            raise ValueError("Report outside output directory")
        with path.open(encoding="utf-8", errors="replace") as stream:
            text = stream.read(180_000)
        # Do not display embedded task prompts/skill instructions in run reports.
        text = re.sub(r"(?ms)^## Prompt\s*\n.*?(?=^## (?:Output|Result|Response|Error|Final)|\Z)", "[Task instructions omitted]\n\n", text)
        return scrub(text)[:32_000]

    def command(self, argv, timeout=6):
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None

    def snapshot(self):
        now = datetime.now(timezone.utc)
        issues, warnings = [], []
        try:
            raw = self.jobs()
        except (OSError, ValueError) as error:
            raw = []
            warnings.append("Hermes scheduler data unavailable: " + type(error).__name__)
        jobs = []
        for row in raw:
            schedule = row.get("schedule") or {}
            enabled = bool(row.get("enabled", True))
            status = "paused" if not enabled else "failed" if row.get("last_status") in ("error", "failed") else "ok" if row.get("last_status") in ("ok", "success") else "unknown"
            error = scrub(row.get("last_error"))[:1800]
            delivery_error = scrub(row.get("last_delivery_error"))[:800]
            next_at = parse_time(row.get("next_run_at")) if enabled else None
            stale = bool(next_at and next_at < now - timedelta(minutes=15))
            entry = {"id": row.get("id"), "name": row.get("name", "Unnamed job"), "enabled": enabled,
                     "status": status, "schedule": schedule.get("display", schedule.get("expr", "Unknown")) if isinstance(schedule, dict) else str(schedule),
                     "last_run_at": row.get("last_run_at"), "next_run_at": row.get("next_run_at") if enabled else None,
                     "error": error, "delivery_error": delivery_error, "delivery": row.get("deliver") or "Not configured",
                     "stale": stale, "controllable": bool(SAFE.fullmatch(str(row.get("id", "")))) and Path(self.cli).is_file()}
            jobs.append(entry)
            if enabled and (status == "failed" or delivery_error or stale):
                issues.append({"title": entry["name"], "detail": error or delivery_error or "The scheduled run is over 15 minutes late.", "job_id": entry["id"]})
        services = []
        for name, unit, user in [("Hermes gateway", "hermes-gateway.service", True), ("Model proxy", "freellmapi.service", False), ("X browser", "twitter-browser.service", True)]:
            result = self.command(["systemctl"] + (["--user"] if user else []) + ["is-active", unit])
            state = result.stdout.strip() if result else "unavailable"
            services.append({"name": name, "status": state, "note": "Process state only; does not verify task completion."})
        posts = []
        today = now.astimezone(IST).date().isoformat()
        try:
            journal = read_json(self.xroot / "autopublish-state.json")
            if not isinstance(journal, dict):
                raise ValueError('Invalid publishing ledger')
        except (OSError, ValueError):
            journal = {}
            warnings.append("X publication receipt ledger unavailable")
        for date in [today, (now.astimezone(IST).date() + timedelta(days=1)).isoformat()]:
            try:
                plan_path = self.xroot / "drafts" / (date + ".json")
                if not under(plan_path, self.xroot / "drafts"):
                    raise ValueError("Invalid plan path")
                plan = read_json(plan_path)
                for slot in plan.get("slots", []):
                    text = str(slot.get("text") or "")
                    claim = journal.get(date + '/' + str(slot.get('slot')), {})
                    posts.append({"date": date, "slot": slot.get("slot"), "due_at": slot.get("due_at"), "text": text,
                                  "bucket": slot.get("bucket") or slot.get("kind") or "Draft", "url": slot.get("tweet_url") or claim.get('url'),
                                  "status": publication_status(slot, claim)})
                    if claim.get('status') == 'failed' and not slot.get('skipped'):
                        issues.append({'title': 'X draft failed preflight', 'detail': scrub(claim.get('error', 'Publishing checks failed'))[:800], 'job_id': 'd92bae72a243'})
            except FileNotFoundError:
                warnings.append("No X plan saved for " + date)
            except (OSError, ValueError, TypeError, AttributeError):
                warnings.append("X plan unreadable for " + date)
        for key, item in journal.items():
            if isinstance(item, dict) and item.get("status") == "uncertain":
                issues.append({"title": "X publication needs review", "detail": "Submission " + key + " has no verified receipt. It must not be resubmitted blindly.", "job_id": "d92bae72a243"})
        try:
            metrics = read_json(self.xroot / "metrics/summary.json")
        except (OSError, ValueError):
            metrics = None
        total, used, free = shutil.disk_usage(self.home if self.home.exists() else ROOT)
        system = {"disk_total": total, "disk_used": used, "disk_percent": round(used / total * 100), "memory_percent": None, "uptime_hours": None, "load": None}
        try:
            mem = dict((line.split(":")[0], int(line.split()[1])) for line in Path("/proc/meminfo").read_text().splitlines())
            system["memory_percent"] = round((1 - mem["MemAvailable"] / mem["MemTotal"]) * 100)
            system["uptime_hours"] = round(float(Path("/proc/uptime").read_text().split()[0]) / 3600)
            system["load"] = os.getloadavg()[0]
        except (OSError, ValueError, KeyError, AttributeError):
            pass
        # Read just the known legacy schedule and log, never browser profiles.
        cron = self.command(["crontab", "-l"])
        naukri_active = bool(any("naukri-bot/run_daily.sh" in line and not line.lstrip().startswith("#") for line in cron.stdout.splitlines())) if cron and cron.returncode == 0 else None
        naukri = {"configured": naukri_active, "schedule": "09:30 and 14:30 IST", "last_log_at": None, "checked_at": now.isoformat() if naukri_active is not None else None, "note": "Managed by Linux cron. A log update does not prove the resume was saved."}
        if naukri_active is None:
            # crontab uses setgid, deliberately blocked by NoNewPrivileges.
            # Use an explicitly timestamped setup observation, never imply it is live.
            try:
                legacy = read_json(self.audit.parent / "legacy-schedules.json")
                naukri.update(configured=legacy.get("naukri_configured"), checked_at=legacy.get("checked_at"), note="Schedule last checked at setup; run-log freshness is live. A log update does not prove the resume was saved.")
            except (OSError, ValueError, AttributeError):
                naukri["note"] = "Linux cron schedule cannot be read from this isolated service. A log update does not prove the resume was saved."
        try:
            naukri["last_log_at"] = datetime.fromtimestamp((self.home.parent / "naukri-bot/logs/cron.log").stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            pass
        heartbeat = self.home / "cron/ticker_heartbeat"
        try:
            age = max(0, int(now.timestamp() - heartbeat.stat().st_mtime))
            scheduler = {"age_seconds": age, "status": "fresh" if age < 180 else "stale"}
            if age >= 180:
                issues.append({"title": "Scheduler heartbeat is stale", "detail": "No scheduler heartbeat in " + str(age // 60) + " minutes.", "job_id": None})
        except OSError:
            scheduler = {"age_seconds": None, "status": "unavailable"}
        return {"observed_at": now.isoformat(), "timezone": "Asia/Kolkata", "mode": "live", "jobs": jobs,
                "issues": issues, "warnings": warnings, "services": services, "posts": posts, "metrics": metrics,
                "system": system, "naukri": naukri, "scheduler": scheduler, "history": self.history(raw),
                "backup": {"status": "unverified", "note": "No verified backup source is connected to this dashboard."}}

    def action(self, jid, action):
        if not SAFE.fullmatch(jid) or action not in ("pause", "resume"):
            raise ValueError("Only pause and resume are supported")
        with self.action_lock:
            jobs = self.jobs()
            row = next((row for row in jobs if row.get("id") == jid), None)
            if not row:
                raise ValueError("Unknown job")
            target_enabled = action == "resume"
            if bool(row.get("enabled", True)) == target_enabled:
                return {"message": "Already " + ("scheduled" if target_enabled else "paused")}
            result = self.command([self.cli, "cron", action, jid], timeout=20)
            success = result is not None and result.returncode == 0 and any(row.get("id") == jid and bool(row.get("enabled", True)) == target_enabled for row in self.jobs())
            self.audit.parent.mkdir(parents=True, exist_ok=True)
            with self.audit.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "job_id": jid, "action": action, "verified": success}) + "\n")
            if not success:
                raise RuntimeError("Scheduler did not confirm the change. Refresh before trying again.")
            return {"message": "Schedule resumed" if target_enabled else "Schedule paused"}


class App(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, backend, auth):
        self.backend, self.auth = backend, auth
        self.sessions, self.attempts = {}, defaultdict(list)
        self.lock = threading.RLock()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesControl"

    def log_message(self, format, *args):
        # Never log request payloads, credentials or report contents.
        pass

    def respond(self, status, data, kind="application/json", cookie=None):
        body = json.dumps(data).encode() if kind == "application/json" else data if isinstance(data, bytes) else data.encode()
        self.send_response(status)
        self.send_header("Content-Type", kind + ("; charset=utf-8" if kind.startswith("text/") or kind == "application/javascript" else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def webui(self):
        from webui_bridge import forward, MAX_BODY
        _, session = self.session()
        if not session:
            return self.respond(401, {'error': 'Please sign in'})
        started = False
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 0 or size > MAX_BODY:
                return self.respond(413, {'error': 'Uploads are limited to 4 MB'})
            value = {'method': self.command, 'path': self.path[len('/hermes'):],
                     'cookie': self.headers.get('Cookie', ''),
                     'content_type': self.headers.get('Content-Type', 'application/json'),
                     'csrf': self.headers.get('X-Hermes-CSRF-Token', ''),
                     'last_event_id': self.headers.get('Last-Event-ID', ''),
                     'body': base64.b64encode(self.rfile.read(size)).decode()}
            def start(status, headers):
                nonlocal started
                self.send_response(status)
                for name, value in headers:
                    self.send_header(name, value)
                self.send_header('Connection', 'close')
                self.end_headers()
                started = True
            def write(chunk):
                self.wfile.write(chunk)
                self.wfile.flush()
            forward(value, start, write)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            if not started:
                self.respond(502, {'error': 'Hermes interface is unavailable. Reload to retry.'})
        finally:
            self.close_connection = True

    def do_HEAD(self):
        if self.path.startswith('/hermes/') and self.allowed_host():
            return self.webui()
        self.send_response(404)
        self.end_headers()

    def do_PUT(self):
        if self.path.startswith('/hermes/'):
            return self.do_POST()
        return self.respond(405, {'error': 'Method not allowed'})

    do_PATCH = do_PUT
    do_DELETE = do_PUT

    def allowed_host(self):
        return self.headers.get("Host") in {f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}"}

    def session(self):
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookies["hermes_session"].value if "hermes_session" in cookies else ""
        except Exception:
            return None, None
        with self.server.lock:
            now = time.time()
            self.server.sessions = {key: value for key, value in self.server.sessions.items() if value["expires"] > now}
            return token, self.server.sessions.get(token)

    def do_GET(self):
        if not self.allowed_host():
            return self.respond(403, {"error": "Invalid host"})
        if self.path == '/hermes':
            self.send_response(302)
            self.send_header('Location', '/hermes/')
            self.end_headers()
            return
        if self.path.startswith('/hermes/'):
            return self.webui()
        path = unquote(urlsplit(self.path).path)
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "application/javascript"), "/style.css": ("style.css", "text/css"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}
        if path in assets:
            filename, kind = assets[path]
            return self.respond(200, (ROOT / "static" / filename).read_bytes(), kind)
        if path == "/api/health":
            return self.respond(200, {"status": "ok", "service": "Hermes Control"})
        _, session = self.session()
        if not session:
            return self.respond(401, {"error": "Please sign in"})
        try:
            if path == "/api/session":
                return self.respond(200, {"csrf": session["csrf"]})
            if path == "/api/status":
                return self.respond(200, self.server.backend.snapshot())
            if path == "/api/workspace":
                return self.respond(200, self.server.backend.workspace())
            match = re.fullmatch(r"/api/reports/([\w-]+)/([\w.-]+)", path)
            if match:
                return self.respond(200, {"text": self.server.backend.output(*match.groups())})
            return self.respond(404, {"error": "Not found"})
        except FileNotFoundError:
            return self.respond(404, {"error": "Report not found"})
        except ValueError:
            return self.respond(400, {"error": "Invalid data or report identifier"})
        except Exception:
            return self.respond(503, {"error": "Live data could not be read. Try refreshing."})

    def do_POST(self):
        if not self.allowed_host() or self.headers.get("Origin") != "http://" + self.headers.get("Host", ""):
            return self.respond(403, {"error": "Request origin rejected"})
        if self.path.startswith('/hermes/'):
            return self.webui()
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 4096:
                return self.respond(413, {"error": "Request too large"})
            body = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(body, dict):
                raise ValueError()
        except (ValueError, UnicodeDecodeError):
            return self.respond(400, {"error": "Invalid request"})
        path = urlsplit(self.path).path
        if path == "/api/login":
            with self.server.lock:
                now = time.time()
                peer = self.client_address[0]
                bridge_token = self.server.auth.get("bridge_token")
                rate_key = self.headers.get("X-Hermes-Rate-Key", "")
                if bridge_token and hmac.compare_digest(self.headers.get("X-Hermes-Bridge-Token", ""), bridge_token) and re.fullmatch(r"[a-f0-9]{64}", rate_key):
                    peer = "bridge:" + rate_key
                # Bound the public login bucket map. Keep active limits intact;
                # reject new peers during a flood instead of clearing their limits.
                if len(self.server.attempts) > 5000:
                    self.server.attempts = defaultdict(list, {key: [at for at in values if now - at < 300] for key, values in self.server.attempts.items() if any(now - at < 300 for at in values)})
                    if peer not in self.server.attempts and len(self.server.attempts) > 5000:
                        return self.respond(429, {"error": "Too many login requests. Try again later."})
                attempts = [at for at in self.server.attempts[peer] if now - at < 300]
                self.server.attempts[peer] = attempts
                if len(attempts) >= 8:
                    return self.respond(429, {"error": "Too many attempts. Wait five minutes."})
                attempts.append(now)
                password = body.get("password", "")
                if not isinstance(password, str):
                    return self.respond(400, {"error": "Invalid password"})
                derived = hashlib.scrypt(password.encode(), salt=bytes.fromhex(self.server.auth["salt"]), n=16384, r=8, p=1).hex()
                if not hmac.compare_digest(derived, self.server.auth["hash"]):
                    return self.respond(401, {"error": "Incorrect password"})
                self.server.attempts[peer] = []
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                self.server.sessions[token] = {"csrf": csrf, "expires": now + 12 * 3600}
                return self.respond(200, {"csrf": csrf}, cookie=f"hermes_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200")
        token, session = self.session()
        if not session:
            return self.respond(401, {"error": "Please sign in"})
        if not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"]):
            return self.respond(403, {"error": "Session token rejected. Reload the page."})
        if path == "/api/logout":
            with self.server.lock:
                self.server.sessions.pop(token, None)
            return self.respond(200, {"ok": True}, cookie="hermes_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
        if path in ('/api/knowledge/search', '/api/knowledge/save'):
            try:
                personal = self.server.backend.personal()
                if path.endswith('/search'):
                    return self.respond(200, personal.search(body.get('question', '')))
                identity = personal.save(scrub(body.get('title', '')) if isinstance(body.get('title'), str) else None,
                                         scrub(body.get('text', '')) if isinstance(body.get('text'), str) else None,
                                         body.get('url', ''))
                return self.respond(200, {'ok': True, 'id': identity})
            except ValueError as error:
                return self.respond(400, {'error': str(error)})
            except Exception:
                return self.respond(503, {'error': 'The library could not be read or saved. Try again.'})
        match = re.fullmatch(r"/api/jobs/([\w-]+)/(pause|resume)", path)
        if not match:
            return self.respond(404, {"error": "Not found"})
        try:
            return self.respond(200, self.server.backend.action(*match.groups()))
        except ValueError as error:
            return self.respond(400, {"error": str(error)})
        except Exception:
            return self.respond(503, {"error": "Schedule change was not confirmed. Refresh to check its state."})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9119)
    parser.add_argument("--home", default=os.path.expanduser("~/.hermes"))
    parser.add_argument("--xroot", default=os.path.expanduser("~/tweetytweets"))
    parser.add_argument("--cli", default=os.path.expanduser("~/.local/bin/hermes"))
    parser.add_argument("--auth", default=os.path.expanduser("~/.local/state/hermes-control/auth.json"))
    parser.add_argument("--audit", default=os.path.expanduser("~/.local/state/hermes-control/actions.jsonl"))
    args = parser.parse_args()
    auth = read_json(Path(args.auth))
    backend = Hermes(args.home, args.xroot, args.cli, args.audit)
    server = App(("127.0.0.1", args.port), backend, auth)
    print(f"Hermes Control listening on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
