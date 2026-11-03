'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('child_process');
const { once } = require('events');
const fs = require('fs');
const os = require('os');
const path = require('path');

test('closing the last manager tab stops the server; reload and other tabs keep it alive', async t => {
  const root = path.resolve(__dirname, '..');
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'legion-manager-lifecycle-'));
  // Exercise the real server without opening a window in the user's Chrome profile.
  const script = `const { EventEmitter } = require('events');
    require('child_process').spawn = () => Object.assign(new EventEmitter(), { unref() {} });
    process.argv.push('--open'); require('./server.js');`;
  const child = spawn(process.execPath, ['-e', script], {
    cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, LOCALAPPDATA: temporary, LEGION_MANAGER_PORT: '0' },
  });
  t.after(async () => {
    if (child.exitCode === null) { child.kill(); await once(child, 'exit'); }
    fs.rmSync(temporary, { recursive: true, force: true });
  });
  const entry = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('Manager did not start')), 5000);
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.stdout.on('data', data => {
      output += data.toString();
      const match = output.match(/http:\/\/127\.0\.0\.1:\d+\/#[-_a-zA-Z0-9]+/);
      if (match) { clearTimeout(timer); resolve(new URL(match[0])); }
    });
  });
  const headers = { 'X-Manager-Token': entry.hash.slice(1) };
  const connect = async () => {
    const response = await fetch(`${entry.origin}/api/ui-session`, { headers });
    assert.equal(response.status, 200);
    const reader = response.body.getReader();
    await reader.read();
    return reader;
  };
  const first = await connect();
  await first.cancel();
  // A reload opens a replacement connection before the shutdown grace period ends.
  const replacement = await connect();
  const second = await connect();
  await replacement.cancel();
  await new Promise(resolve => setTimeout(resolve, 2300));
  assert.equal(child.exitCode, null, 'Another open tab should keep the manager running');
  const exited = once(child, 'exit');
  await second.cancel();
  const timeout = setTimeout(() => child.kill(), 7000);
  const [code] = await exited;
  clearTimeout(timeout);
  assert.equal(code, 0);
  await assert.rejects(fetch(`${entry.origin}/api/status`, { headers }));
});
