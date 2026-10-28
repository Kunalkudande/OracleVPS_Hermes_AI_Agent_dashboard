// Public frontend only, pinned to the installed VPS WebUI revision.
import { mkdir, readFile, writeFile, cp, unlink } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
export const revision = '516321171c74e817f2c8d84773fc1067e8792fde';
export async function copyWebuiAssets(root) {
  const cache = path.join(root, '.webui-assets');
  await mkdir(cache, { recursive: true });
  const folder = path.join(cache, `hermes-webui-${revision}`);
  try { await readFile(path.join(folder, 'static/index.html')); }
  catch {
    const response = await fetch(`https://codeload.github.com/nesquena/hermes-webui/tar.gz/${revision}`, {signal: AbortSignal.timeout(120000)});
    if (!response.ok) throw new Error('Could not download pinned Hermes WebUI assets');
    const archive = path.join(cache, `${revision}.tgz`);
    await writeFile(archive, Buffer.from(await response.arrayBuffer()));
    execFileSync('tar', ['-xzf', archive, '-C', cache, `hermes-webui-${revision}/static`]);
  }
  await mkdir(path.join(root, 'dist/hermes'), {recursive: true});
  await cp(path.join(folder, 'static'), path.join(root, 'dist/hermes/static'), {recursive: true});
  // Serve the dynamic shell with server-substituted auth tokens, not its template.
  await unlink(path.join(root, 'dist/hermes/static/index.html'));
}
