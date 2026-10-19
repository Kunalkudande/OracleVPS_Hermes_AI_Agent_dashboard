// Run locally using your existing trusted administrator connection.
// The generated private key stays outside the repository and is restricted to bridge.py.
import ssh2 from 'ssh2';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';
const { utils } = ssh2;

const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name)+1] : fallback;
const host = option('--host'); const user = option('--user','ubuntu'); const key = option('--admin-key');
if (!host || !key || !/^[\w.:-]+$/.test(host) || !/^[a-z_][a-z0-9_-]*$/.test(user)) throw new Error('Usage: node scripts/prepare-vercel.mjs --host VPS_IP --user ubuntu --admin-key PATH');
const folder = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), '.local/state'), 'HermesDashboard');
mkdirSync(folder, { recursive:true, mode:0o700 });
if(process.platform==='win32') {
  const acl=spawnSync('icacls',[folder,'/inheritance:r','/grant:r',`${process.env.USERDOMAIN}\\${process.env.USERNAME}:(OI)(CI)F`],{encoding:'utf8'});
  if(acl.status!==0) throw new Error('Could not restrict secret folder permissions');
}
const bundlePath=path.join(folder,'vercel-bridge-credential.json');
let bundle;
if(existsSync(bundlePath)) bundle=JSON.parse(readFileSync(bundlePath,'utf8'));
else {
  const keys=utils.generateKeyPairSync('ed25519',{comment:'hermes-vercel-bridge'});
  if(utils.parseKey(keys.private) instanceof Error) throw new Error('Key generation failed; retry setup');
  bundle={privateKey:keys.private,publicKey:keys.public};
  writeFileSync(bundlePath,JSON.stringify(bundle),{mode:0o600});
}
const script=`import pathlib,json,os,secrets,base64,hashlib
home=pathlib.Path.home()
bridge=home/'hermes-control/bridge.py'
if not bridge.is_file(): raise RuntimeError('Deploy bridge.py before running setup')
key=${JSON.stringify(bundle.publicKey)}
line='restrict,command="/usr/bin/python3 -I '+str(bridge)+'" '+key+' hermes-vercel-bridge'
p=home/'.ssh/authorized_keys'
text=p.read_text()
lines=text.splitlines()
if not any(key.split()[1] in x for x in lines):
 p.write_text(text.rstrip()+'\\n'+line+'\\n');p.chmod(0o600)
auth=home/'.local/state/hermes-control/auth.json'
value=json.loads(auth.read_text())
if not value.get('bridge_token'):
 value['bridge_token']=secrets.token_urlsafe(32)
 tmp=auth.with_name('.auth-vercel-new.json');tmp.write_text(json.dumps(value));tmp.chmod(0o600);os.replace(tmp,auth)
pub=pathlib.Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().split()[1]
print(json.dumps({'fingerprint':hashlib.sha256(base64.b64decode(pub)).hexdigest()}))
`;
const result=spawnSync('ssh',['-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','-i',key,`${user}@${host}`,'python3 -'],{input:script,encoding:'utf8',timeout:25000});
if(result.status!==0) throw new Error('VPS setup failed: '+result.stderr);
const {fingerprint}=JSON.parse(result.stdout.trim());
bundle={...bundle,host,user,fingerprint};
writeFileSync(bundlePath,JSON.stringify(bundle),{mode:0o600});
writeFileSync(path.join(folder,'vercel-private-key.txt'),bundle.privateKey,{mode:0o600});
writeFileSync(path.join(folder,'vercel-settings.txt'),`Vercel project environment variables (Production only)\n\nHERMES_SSH_HOST=${host}\nHERMES_SSH_USER=${user}\nHERMES_SSH_HOST_SHA256=${fingerprint}\nHERMES_SSH_PRIVATE_KEY=Copy the complete contents of vercel-private-key.txt in this folder\nHERMES_PUBLIC_ORIGIN=https://YOUR_PROJECT.vercel.app\n\nUse your actual production URL, with no trailing slash. Redeploy after setting variables.\nThe SSH key is limited to the dashboard API. Never upload your administrator key.\n`,{mode:0o600});
console.log('Prepared a restricted VPS bridge. Vercel settings are in '+path.join(folder,'vercel-settings.txt'));
