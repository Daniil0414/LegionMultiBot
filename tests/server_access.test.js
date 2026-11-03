'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');

test('running manager rejects foreign origins and traversal, accepts its own token', async t => {
  const root = path.resolve(__dirname, '..');
  const localData = fs.mkdtempSync(path.join(os.tmpdir(), 'legion-manager-test-'));
  const child = spawn(process.execPath, [path.join(root, 'server.js')], {
    cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, LOCALAPPDATA: localData, LEGION_MANAGER_PORT: '0' },
  });
  t.after(async () => {
    child.kill();
    if (child.exitCode === null) await new Promise(resolve => child.once('exit', resolve));
    // The only temporary directory contains no user profiles or private state.
    assert.equal(path.dirname(localData), os.tmpdir());
    fs.rmSync(localData, { recursive: true, force: true });
  });
  const entry = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('Manager did not start')), 5000);
    child.once('error', reject);
    child.stdout.on('data', data => {
      output += data.toString();
      const match = output.match(/http:\/\/127\.0\.0\.1:\d+\/#[-_a-zA-Z0-9]+/);
      if (match) { clearTimeout(timer); resolve(new URL(match[0])); }
    });
  });
  const token = entry.hash.slice(1);
  const base = entry.origin;
  let response = await fetch(`${base}/api/status`);
  assert.equal(response.status, 404);
  response = await fetch(`${base}/api/status`, { headers: { 'X-Manager-Token': token } });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).running, false);
  response = await fetch(`${base}/api/status`, { headers: {
    'X-Manager-Token': token, Origin: 'https://play.multidonate.ru',
  } });
  assert.equal(response.status, 404);
  assert.equal(response.headers.get('access-control-allow-origin'), null);
  response = await fetch(`${base}/app.js`, { headers: { 'Sec-Fetch-Site': 'cross-site' } });
  assert.equal(response.status, 404);
  response = await fetch(`${base}/app.js`, { headers: { 'Sec-Fetch-Site': 'same-origin' } });
  assert.equal(response.status, 200);
  const traversal = await new Promise((resolve, reject) => {
    const request = http.get({ hostname: entry.hostname, port: entry.port,
                              path: '/../vpn_config.json' }, result => {
      result.resume(); resolve(result.statusCode);
    });
    request.on('error', reject);
  });
  assert.equal(traversal, 404);
});
