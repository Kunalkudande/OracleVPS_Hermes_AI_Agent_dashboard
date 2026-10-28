import { test } from 'node:test';
import assert from 'node:assert/strict';
import handler, { upstreamPath } from '../api/webui.mjs';

test('mount preserves query parameters without accepting absolute destinations', () => {
  assert.equal(upstreamPath({path: 'api/sessions', profile: 'default', search: 'a & b'}), '/api/sessions?profile=default&search=a+%26+b');
  assert.equal(upstreamPath({path: ''}), '/');
  for (const path of ['/evil', '../auth', '%2e%2e/auth', 'api/x?target=bad', 'a\\b', 'a\r\n']) {
    assert.throws(() => upstreamPath({path}));
  }
});

test('WebUI rejects wrong host, cross-site mutations and missing sessions before SSH', async () => {
  const env = {HERMES_SSH_HOST:'example.invalid', HERMES_SSH_USER:'ubuntu', HERMES_SSH_PRIVATE_KEY:'test',
    HERMES_SSH_HOST_SHA256:'ab'.repeat(32), HERMES_PUBLIC_ORIGIN:'https://hermes.example.com'};
  const old = Object.fromEntries(Object.keys(env).map(k=>[k,process.env[k]])); Object.assign(process.env,env);
  const response = () => ({headers:{}, setHeader(k,v){this.headers[k]=v;}, status(c){this.code=c;return this;},end(){}});
  try {
    for (const [headers, method, code] of [
      [{host:'preview.example.com'},'GET',403],
      [{host:'hermes.example.com',origin:'https://evil.example',cookie:'hermes_session=fake'},'POST',403],
      [{host:'hermes.example.com','sec-fetch-site':'cross-site',cookie:'hermes_session=fake'},'GET',403],
      [{host:'hermes.example.com'},'GET',401]]) {
      const res=response(); await handler({query:{path:'api/sessions'},headers,method},res);assert.equal(res.code,code);
    }
  } finally {for(const key of Object.keys(env)){if(old[key]===undefined)delete process.env[key];else process.env[key]=old[key];}}
});
