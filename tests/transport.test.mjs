import { test } from 'node:test';
import assert from 'node:assert/strict';
import { allowedRoute, configuration, matchesHost, requestEnvelope } from '../lib/transport.mjs';
import handler from '../api/bridge.mjs';

const env = { HERMES_SSH_HOST: 'example.invalid', HERMES_SSH_USER: 'ubuntu',
  HERMES_SSH_PRIVATE_KEY: 'test-key', HERMES_SSH_HOST_SHA256: 'ab'.repeat(32), HERMES_PUBLIC_ORIGIN: 'https://hermes.example.com' };

test('only specific dashboard API operations are forwarded', () => {
  for (const route of ['/api/status', '/api/session', '/api/reports/job-id/2026-10-19_00-00-00.md']) assert.ok(allowedRoute('GET', route));
  assert.ok(allowedRoute('POST', '/api/jobs/job-id/pause'));
  for (const route of ['/api/jobs/job-id/run', '/api/reports/job-id/../../auth.json', '/api/status?target=localhost', '/api/config']) {
    assert.equal(allowedRoute('POST', route), false); assert.equal(allowedRoute('GET', route), false);
  }
  assert.equal(allowedRoute('DELETE', '/api/status'), false);
});
test('configuration requires an explicit HTTPS origin and pinned SSH host key', () => {
  assert.equal(configuration(env).origin, 'https://hermes.example.com');
  assert.throws(() => configuration({ ...env, HERMES_PUBLIC_ORIGIN: 'http://hermes.example.com' }));
  assert.throws(() => configuration({ ...env, HERMES_PUBLIC_ORIGIN: 'https://hermes.example.com/path' }));
  assert.throws(() => configuration({ ...env, HERMES_SSH_HOST_SHA256: '' }));
  assert.ok(matchesHost('ab'.repeat(32), 'ab'.repeat(32)));
  assert.equal(matchesHost('cd'.repeat(32), 'ab'.repeat(32)), false);
});
test('only dashboard headers cross the bridge, IP address is hashed', () => {
  const result = requestEnvelope({ method: 'POST', body: { password: 'test' }, headers: { cookie: 'session=test', 'x-csrf-token': 'csrf', 'x-vercel-forwarded-for': '192.0.2.1', authorization: 'do-not-forward' } }, '/api/login');
  assert.equal(result.path, '/api/login'); assert.equal(result.cookie, 'session=test');
  assert.match(result.rate_key, /^[a-f0-9]{64}$/); assert.ok(!JSON.stringify(result).includes('192.0.2.1'));
  assert.ok(!JSON.stringify(result).includes('do-not-forward'));
});
function response() { return { headers: {}, setHeader(k,v) { this.headers[k]=v; }, status(code) { this.statusCode=code; return this; }, json(value) { this.body=value; return this; } }; }
test('public handler fails closed before connecting on wrong host or origin', async () => {
  const previous = Object.fromEntries(Object.keys(env).map(key => [key,process.env[key]]));
  Object.assign(process.env,env);
  try {
    let res=response();await handler({ method:'POST',query:{route:'login'},headers:{host:'hermes.example.com',origin:'https://evil.example','content-type':'application/json'},body:{} },res);
    assert.equal(res.statusCode,403);
    res=response();await handler({method:'GET',query:{route:'status'},headers:{host:'preview.example.com'}},res);assert.equal(res.statusCode,403);
    res=response();await handler({method:'POST',query:{route:'jobs/id/run'},headers:{}},res);assert.equal(res.statusCode,404);
    delete process.env.HERMES_SSH_PRIVATE_KEY;
    res=response();await handler({method:'GET',query:{route:'status'},headers:{}},res);assert.equal(res.statusCode,503);
    assert.equal(res.headers['Cache-Control'],'no-store');
  } finally { for(const key of Object.keys(env)) { if(previous[key]===undefined)delete process.env[key];else process.env[key]=previous[key]; } }
});
