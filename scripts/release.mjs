import { readFileSync, writeFileSync, readdirSync, mkdirSync, copyFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

export function qualify(tag, pythonVersion, extension) {
  if (!/^v\d+\.\d+\.\d+$/.test(tag) || tag.slice(1) !== pythonVersion || extension.version !== pythonVersion) {
    throw new Error('Release tag, Python version and extension version must agree');
  }
  if (extension.publisher !== 'jmsapps' || extension.name !== 'pysx') throw new Error('Unexpected extension identity');
  return pythonVersion;
}

export function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }

export function disposition(localHash, remoteHash) {
  if (remoteHash === null) return 'upload';
  if (localHash !== remoteHash) throw new Error('Published artifact conflicts with candidate');
  return 'present';
}

// The gallery publishes no digest and does not promise to serve back the bytes
// it was given, so a rerun can establish presence but never equivalence.
export function galleryDisposition(status) {
  if (status === 404) return 'upload';
  if (status >= 200 && status < 300) return 'present-unverified';
  throw new Error(`Marketplace request failed: ${status}`);
}

export function retryPlan(artifacts, published) {
  return Object.fromEntries(Object.entries(artifacts).map(([name, hash]) =>
    [name, disposition(hash, published[name] ?? null)]));
}

async function json(url) {
  const response = await fetch(url);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Registry request failed: ${response.status}`);
  return response.json();
}

export async function main(args) {
  const [mode, tag, directory = 'dist'] = args;
  const project = readFileSync('pyproject.toml', 'utf8');
  const version = project.match(/^version = "([^"]+)"/m)?.[1];
  const extension = JSON.parse(readFileSync('editor/package.json', 'utf8'));
  qualify(tag, version, extension);
  if (mode === 'version') return;
  if (mode === 'manifest') {
    const files = readdirSync(directory).filter(name => /\.(whl|tar\.gz|vsix)$/.test(name)).sort();
    if (files.length !== 3 || !files.some(x => x.endsWith('.whl')) || !files.some(x => x.endsWith('.tar.gz')) || !files.some(x => x.endsWith('.vsix'))) throw new Error('Expected wheel, sdist and VSIX');
    const artifacts = Object.fromEntries(files.map(name => [name, sha256(readFileSync(path.join(directory, name)))]));
    writeFileSync(path.join(directory, 'release.json'), JSON.stringify({tag, commit:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(), artifacts}, null, 2) + '\n');
    return;
  }
  const manifest = JSON.parse(readFileSync(path.join(directory, 'release.json'), 'utf8'));
  if (manifest.tag !== tag) throw new Error('Artifact tag mismatch');
  const commit = execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim();
  if (manifest.commit !== commit) throw new Error('Artifact commit mismatch');
  const names = Object.keys(manifest.artifacts);
  if (names.length !== 3 || names.some(name => path.basename(name) !== name) || !names.some(name => name.endsWith('.whl')) || !names.some(name => name.endsWith('.tar.gz')) || !names.some(name => name.endsWith('.vsix'))) throw new Error('Invalid artifact manifest');
  for (const [name, hash] of Object.entries(manifest.artifacts)) disposition(hash, sha256(readFileSync(path.join(directory,name))));
  if (mode === 'check') return;
  if (mode !== 'pypi' && mode !== 'marketplace') throw new Error('Unknown release operation');
  if (mode === 'pypi') {
    const published = await json(`https://pypi.org/pypi/pysx/${version}/json`);
    const artifacts = Object.fromEntries(Object.entries(manifest.artifacts).filter(([name]) => !name.endsWith('.vsix')));
    const remote = Object.fromEntries((published?.urls ?? []).map(file => [file.filename,file.digests.sha256]));
    const plan = retryPlan(artifacts, remote);
    mkdirSync('upload', {recursive:true});
    for (const [name, state] of Object.entries(plan)) {
      console.log(`${name}: ${state}`);
      if (state === 'upload') copyFileSync(path.join(directory,name),path.join('upload',name));
    }
    if (process.env.GITHUB_OUTPUT) writeFileSync(process.env.GITHUB_OUTPUT,`present=${Object.values(plan).every(state => state === 'present')}\n`,{flag:'a'});
  } else {
    const name = Object.keys(manifest.artifacts).find(name => name.endsWith('.vsix'));
    if (process.env.GITHUB_OUTPUT) writeFileSync(process.env.GITHUB_OUTPUT,`vsix=${name}\n`,{flag:'a'});
    const response = await fetch(`https://marketplace.visualstudio.com/_apis/public/gallery/publishers/jmsapps/vsextensions/pysx/${version}/vspackage`);
    const state = galleryDisposition(response.status);
    console.log(state === 'upload'
      ? `marketplace: upload (candidate ${name} ${manifest.artifacts[name]})`
      : `marketplace: version already published; content equivalence unverified (candidate ${name} ${manifest.artifacts[name]})`);
    if (state !== 'upload' && process.env.GITHUB_OUTPUT) writeFileSync(process.env.GITHUB_OUTPUT,'present=true\n',{flag:'a'});
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(new URL(import.meta.url).pathname)) await main(process.argv.slice(2));
