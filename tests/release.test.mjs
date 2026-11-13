import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { qualify, disposition, sha256, retryPlan, galleryDisposition } from '../scripts/release.mjs';

test('release requires matching stable versions and production identity', () => {
  const extension = {name:'pysx',publisher:'jmsapps',version:'0.1.0'};
  assert.equal(qualify('v0.1.0','0.1.0',extension),'0.1.0');
  for (const tag of ['0.1.0','v0.2.0','v0.1.0-rc1']) assert.throws(() => qualify(tag,'0.1.0',extension));
  assert.throws(() => qualify('v0.1.0','0.1.0',{...extension,version:'0.2.0'}));
  assert.throws(() => qualify('v0.1.0','0.1.0',{...extension,publisher:'pysx-local'}));
});
test('retry plans cover both partial outcomes, complete success and conflicts', () => {
  const artifacts = {wheel:'a',sdist:'b'};
  assert.deepEqual(retryPlan(artifacts,{}),{wheel:'upload',sdist:'upload'});
  assert.deepEqual(retryPlan(artifacts,{wheel:'a'}),{wheel:'present',sdist:'upload'});
  assert.deepEqual(retryPlan(artifacts,{sdist:'b'}),{wheel:'upload',sdist:'present'});
  assert.deepEqual(retryPlan(artifacts,artifacts),{wheel:'present',sdist:'present'});
  assert.throws(() => retryPlan(artifacts,{wheel:'conflict'}));
});
test('retry accepts identical artifacts and refuses conflicting content', () => {
  const hash = sha256(Buffer.from('candidate'));
  assert.equal(disposition(hash,null),'upload');
  assert.equal(disposition(hash,hash),'present');
  assert.throws(() => disposition(hash,sha256(Buffer.from('other'))));
});

test('marketplace rerun reports presence without claiming content equivalence', () => {
  assert.equal(galleryDisposition(404),'upload');
  assert.equal(galleryDisposition(200),'present-unverified');
  for (const status of [401,403,500,503]) assert.throws(() => galleryDisposition(status));
  const source = readFileSync('scripts/release.mjs','utf8');
  const gallery = source.split('galleryDisposition(response.status)')[1];
  assert.doesNotMatch(gallery,/sha256|arrayBuffer|disposition\(/,
    'the gallery branch must not hash re-served bytes');
});

test('marketplace publishes the qualified candidate by exact name', () => {
  const workflow = readFileSync('.github/workflows/release.yml','utf8');
  assert.match(workflow,/--packagePath "\.\.\/dist\/\$\{\{ steps\.retry\.outputs\.vsix \}\}"/);
  assert.doesNotMatch(workflow,/packagePath \S*\*/);
  assert.doesNotMatch(workflow,/npx --yes/);
});

test('pypi retry keeps its digest comparison alongside skip-existing', () => {
  const workflow = readFileSync('.github/workflows/release.yml','utf8');
  const job = workflow.split('\n  pypi:\n')[1].split(/\n  [a-z]+:\n/)[0];
  assert.match(job,/skip-existing: true/);
  assert.match(job,/release\.mjs pypi/);
});

test('qualification retains failure evidence', () => {
  const job = readFileSync('.github/workflows/release.yml','utf8').split('\n  qualify:\n')[1]
    .split(/\n  [a-z]+:\n/)[0];
  assert.match(job,/PYSX_DEPLOYMENT_LOGS/);
  assert.match(job,/if: always\(\)/);
  assert.match(job,/actions\/cache@v4/);
});

test('invalid release version fails before artifact or registry operations', () => {
  const result = spawnSync(process.execPath, ['scripts/release.mjs','pypi','v999.0.0','/does-not-exist'], {encoding:'utf8'});
  assert.notEqual(result.status,0);
  assert.match(result.stderr,/version must agree/);
  assert.doesNotMatch(result.stderr,/ENOENT|Registry request/);
});

test('both publishing jobs require successful qualification and exact tag checkout', () => {
  const workflow = readFileSync('.github/workflows/release.yml','utf8');
  assert.match(workflow,/types: \[published\]/);
  assert.doesNotMatch(workflow,/workflow_dispatch|pull_request|continue-on-error/);
  for (const name of ['pypi','marketplace']) {
    const job = workflow.split(`\n  ${name}:\n`)[1].split(/\n  [a-z]+:\n/)[0];
    assert.match(job,/needs: qualify/);
    assert.match(job,/ref: \$\{\{ github.event.release.tag_name \}\}/);
    assert.match(job,/name: release-candidate/);
    assert.match(job,/id-token: write/);
    assert.match(job,/steps.retry.outputs.present != 'true'/);
  }
});
