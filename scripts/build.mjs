import { mkdir, copyFile, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { copyWebuiAssets } from './webui-assets.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
await mkdir(path.join(root, 'dist'), { recursive: true });
for (const name of ['index.html', 'app.js', 'style.css', 'favicon.svg']) {
  await copyFile(path.join(root, 'static', name), path.join(root, 'dist', name));
}
// The same UI works privately over SSH and publicly over HTTPS.
const page = (await readFile(path.join(root, 'dist/index.html'), 'utf8'))
  .replace('Private access · Encrypted SSH connection', 'Private dashboard · Secure HTTPS access');
await (await import('node:fs/promises')).writeFile(path.join(root, 'dist/index.html'), page);
console.log('Built dashboard static assets. VPS code and secrets are excluded.');
await copyWebuiAssets(root);
console.log('Included pinned public Hermes WebUI assets.');
