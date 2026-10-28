import ssh2 from 'ssh2';
import { configuration, matchesHost } from '../lib/transport.mjs';
const { Client } = ssh2;
export const config = { api: { bodyParser: false, responseLimit: false } };

export function upstreamPath(query) {
  const route = query?.path;
  if (typeof route !== 'string' || route.startsWith('/') || route.includes('\\') || /[\r\n\0?#]/.test(route)) throw new Error('Invalid route');
  if (decodeURIComponent(route).split('/').includes('..')) throw new Error('Invalid route');
  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(query || {})) {
    if (name === 'path') continue;
    for (const item of Array.isArray(value) ? value : [value]) params.append(name, item);
  }
  return '/' + route + (params.size ? '?' + params : '');
}

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Vercel-CDN-Cache-Control', 'no-store');
  let settings, path;
  try { settings = configuration(); path = upstreamPath(req.query); }
  catch { return res.status(503).end('Hermes interface connection is unavailable.'); }
  if (req.headers.host !== new URL(settings.origin).host) return res.status(403).end('Use the production dashboard.');
  if (!['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(req.method)) return res.status(405).end();
  if (req.headers['sec-fetch-site'] === 'cross-site' ||
      (!['GET', 'HEAD'].includes(req.method) && req.headers.origin !== settings.origin)) {
    return res.status(403).end('Request origin rejected.');
  }
  if (!/(?:^|;\s*)hermes_session=/.test(req.headers.cookie || '')) return res.status(401).end('Sign into the control room first.');
  const chunks = []; let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 4_000_000) return res.status(413).end('Uploads through the control room are limited to 4 MB.');
    chunks.push(chunk);
  }
  const envelope = { method: req.method, path, cookie: req.headers.cookie || '',
    content_type: req.headers['content-type'] || 'application/json',
    csrf: req.headers['x-hermes-csrf-token'] || '', last_event_id: req.headers['last-event-id'] || '',
    body: Buffer.concat(chunks).toString('base64') };
  return new Promise(resolve => {
    const client = new Client(); let done = false, started = false, pending = Buffer.alloc(0);
    const finish = error => {
      if (done) return; done = true; clearTimeout(timer); client.end();
      if (error && !started && !res.headersSent) res.status(502).end('Hermes interface could not be reached. Reload to retry.');
      else res.end();
      resolve();
    };
    const timer = setTimeout(() => finish(new Error('timeout')), 285000);
    res.on('close', () => finish());
    client.on('error', () => finish(new Error('connection')));
    client.on('close', () => finish(started ? null : new Error('closed')));
    client.on('ready', () => client.exec('hermes-webui-api', { pty: false }, (error, stream) => {
      if (error) return finish(error);
      stream.stderr.resume();
      stream.on('error', finish);
      stream.on('data', chunk => {
        if (!started) {
          pending = Buffer.concat([pending, chunk]);
          const end = pending.indexOf(10);
          if (end < 0) { if (pending.length > 32768) finish(new Error('headers')); return; }
          try {
            const meta = JSON.parse(pending.subarray(0, end));
            if (!Number.isInteger(meta.status) || meta.status < 200 || meta.status > 599 || !Array.isArray(meta.headers)) throw new Error();
            res.status(meta.status);
            const cookies = [];
            for (const [name, value] of meta.headers) {
              if (name === 'set-cookie') cookies.push(value.replace(/;\s*Secure/gi, '') + '; Secure');
              else res.setHeader(name, value);
            }
            if (cookies.length) res.setHeader('Set-Cookie', cookies);
            res.flushHeaders(); started = true;
            chunk = pending.subarray(end + 1); pending = null;
          } catch { return finish(new Error('headers')); }
        }
        if (chunk.length && !res.write(chunk)) { stream.pause(); res.once('drain', () => stream.resume()); }
      });
      stream.on('close', code => finish(code ? new Error('bridge') : null));
      stream.end(JSON.stringify(envelope));
    }));
    client.connect({host: settings.host, username: settings.username, privateKey: settings.privateKey,
      hostHash: 'sha256', hostVerifier: hash => matchesHost(hash, settings.fingerprint),
      algorithms: { serverHostKey: ['ssh-ed25519'] }, readyTimeout: 12000,
      keepaliveInterval: 10000, keepaliveCountMax: 3});
  });
}
