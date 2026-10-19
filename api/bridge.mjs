import { allowedRoute, configuration, requestEnvelope, sendToHermes } from '../lib/transport.mjs';

export const config = { api: { bodyParser: { sizeLimit: '4kb' } } };

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Vercel-CDN-Cache-Control', 'no-store');
  const raw = req.query?.route;
  const route = typeof raw === 'string' ? '/api/' + raw : '';
  if (!allowedRoute(req.method, route)) return res.status(404).json({ error: 'Not found' });
  let settings;
  try { settings = configuration(); }
  catch { return res.status(503).json({ error: 'The dashboard VPS connection is not configured yet. Follow VERCEL_SETUP.md.' }); }
  // Only the stable production hostname can use this credential. Preview deployments
  // fail closed, even if someone accidentally gives them the production environment.
  if (req.headers.host !== new URL(settings.origin).host) return res.status(403).json({ error: 'Use the production dashboard address' });
  if (req.method === 'POST' && req.headers.origin !== settings.origin) return res.status(403).json({ error: 'Request origin rejected' });
  if (req.method === 'POST' && (!req.headers['content-type']?.startsWith('application/json') || typeof req.body !== 'object' || req.body === null || Array.isArray(req.body))) {
    return res.status(400).json({ error: 'Expected a JSON object' });
  }
  const envelope = requestEnvelope(req, route);
  if (Buffer.byteLength(envelope.body) > 4096) return res.status(413).json({ error: 'Request too large' });
  try {
    const result = await sendToHermes(settings, envelope);
    if (result.cookies.length) res.setHeader('Set-Cookie', result.cookies.map(cookie => cookie.replace(/;\s*Secure/gi, '') + '; Secure'));
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    return res.status(result.status).send(result.body);
  } catch {
    return res.status(502).json({ error: 'Hermes could not be reached. Try again shortly.' });
  }
}
