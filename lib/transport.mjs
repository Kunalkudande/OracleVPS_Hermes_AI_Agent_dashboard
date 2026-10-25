import ssh2 from 'ssh2';
import { createHash, timingSafeEqual } from 'node:crypto';
const { Client } = ssh2;

const getRoutes = /^\/api\/(?:health|session|status|workspace|reports\/[A-Za-z0-9_-]{1,100}\/[A-Za-z0-9_.-]{1,150}\.md)$/;
const postRoutes = /^\/api\/(?:login|logout|knowledge\/(?:search|save)|jobs\/[A-Za-z0-9_-]{1,100}\/(?:pause|resume))$/;

export function allowedRoute(method, route) {
  return typeof route === 'string' && !route.includes('..') &&
    ((method === 'GET' && getRoutes.test(route)) || (method === 'POST' && postRoutes.test(route)));
}

export function configuration(env = process.env) {
  for (const name of ['HERMES_SSH_HOST', 'HERMES_SSH_USER', 'HERMES_SSH_PRIVATE_KEY', 'HERMES_SSH_HOST_SHA256', 'HERMES_PUBLIC_ORIGIN']) {
    if (!env[name]?.trim()) throw new Error('Dashboard connection not configured');
  }
  const origin = new URL(env.HERMES_PUBLIC_ORIGIN);
  if (origin.protocol !== 'https:' || origin.origin !== env.HERMES_PUBLIC_ORIGIN || origin.username || origin.password) {
    throw new Error('Invalid production origin');
  }
  if (!/^[a-f0-9]{64}$/i.test(env.HERMES_SSH_HOST_SHA256)) throw new Error('Invalid host-key fingerprint');
  return {
    host: env.HERMES_SSH_HOST,
    username: env.HERMES_SSH_USER,
    privateKey: env.HERMES_SSH_PRIVATE_KEY.replace(/\\n/g, '\n').trim() + '\n',
    fingerprint: env.HERMES_SSH_HOST_SHA256.toLowerCase(),
    origin: origin.origin
  };
}

export function matchesHost(actual, expected) {
  return typeof actual === 'string' && /^[a-f0-9]{64}$/i.test(actual) &&
    timingSafeEqual(Buffer.from(actual, 'hex'), Buffer.from(expected, 'hex'));
}

export function requestEnvelope(req, route) {
  const ip = String(req.headers['x-vercel-forwarded-for'] || req.headers['x-forwarded-for'] || req.socket?.remoteAddress || 'unknown').split(',')[0].trim();
  return {
    method: req.method, path: route,
    body: req.method === 'POST' ? JSON.stringify(req.body || {}) : '',
    cookie: String(req.headers.cookie || '').slice(0, 4096),
    csrf: String(req.headers['x-csrf-token'] || '').slice(0, 200),
    rate_key: createHash('sha256').update(ip).digest('hex')
  };
}

export function sendToHermes(config, envelope) {
  return new Promise((resolve, reject) => {
    const client = new Client();
    let done = false;
    const timer = setTimeout(() => finish(new Error('Hermes request timed out')), 27000);
    function finish(error, value) {
      if (done) return;
      done = true;
      clearTimeout(timer);
      client.end();
      error ? reject(error) : resolve(value);
    }
    client.on('error', () => finish(new Error('Hermes SSH connection failed')));
    client.on('close', () => { if (!done) finish(new Error('Hermes SSH connection closed')); });
    client.on('ready', () => {
      // authorized_keys forces bridge.py regardless of the requested command.
      client.exec('hermes-dashboard-api', { pty: false }, (error, stream) => {
        if (error) return finish(new Error('Dashboard bridge unavailable'));
        let output = '', length = 0;
        stream.on('data', chunk => {
          length += chunk.length;
          if (length > 1_000_000) return finish(new Error('Dashboard response too large'));
          output += chunk.toString('utf8');
        });
        stream.on('error', () => finish(new Error('Dashboard bridge failed')));
        stream.stderr.resume(); // No remote output or credential material in public errors/logs.
        stream.on('close', code => {
          if (code !== 0) return finish(new Error('Dashboard bridge rejected request'));
          try {
            const value = JSON.parse(output);
            if (!Number.isInteger(value.status) || value.status < 200 || value.status > 599 || typeof value.body !== 'string' || !Array.isArray(value.cookies)) throw new Error();
            finish(null, value);
          } catch { finish(new Error('Invalid dashboard bridge response')); }
        });
        stream.end(JSON.stringify(envelope));
      });
    });
    try {
      client.connect({ host: config.host, port: 22, username: config.username,
        privateKey: config.privateKey, hostHash: 'sha256',
        hostVerifier: hash => matchesHost(hash, config.fingerprint),
        algorithms: { serverHostKey: ['ssh-ed25519'] },
        readyTimeout: 10000, keepaliveInterval: 5000, keepaliveCountMax: 2 });
    } catch { finish(new Error('Invalid dashboard connection configuration')); }
  });
}
