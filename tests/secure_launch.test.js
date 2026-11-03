'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { EventEmitter } = require('events');
const { PassThrough } = require('stream');
const root = path.resolve(__dirname, '..');

function bridge(response) {
  const requests = [];
  const child = new EventEmitter();
  child.ref = child.unref = () => {};
  child.stdin = new PassThrough();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.stdin.on('data', data => {
    const request = JSON.parse(data.toString());
    requests.push(request);
    queueMicrotask(() => child.stdout.write(JSON.stringify({ id: request.id, ...response }) + '\n'));
  });
  const context = {
    module: { exports: {} }, __dirname: root, process: { env: {} },
    setTimeout, clearTimeout,
    require: name => name === 'child_process' ? { spawn: () => child } : require(name),
  };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'secure_launch.js'), 'utf8'), context);
  return { launch: context.module.exports.launchSecure, close: context.module.exports.closeSecure,
           finish: context.module.exports.finishLaunches, request: context.module.exports.secureRequest,
           requests, child };
}

test('manager shutdown closes worker input and blocks new launches', async () => {
  const b = bridge({ ok: true, pid: 123 });
  await b.launch('account1', 0, 'https://example.invalid');
  const stopped = b.close();
  assert.equal(b.requests.at(-1).action, 'finish');
  assert.equal(b.requests.at(-1).wait_for_browsers, false);
  assert.equal(b.child.stdin.writableEnded, true);
  await assert.rejects(b.request({ action: 'launch' }), /shutting down/);
  b.child.emit('exit', 0);
  await stopped;
  b.child.stdout.end();
});

test('quick launcher keeps the worker referenced until its browsers finish', async () => {
  const b = bridge({ ok: true, pid: 123 });
  await b.launch('account1', 0, 'https://example.invalid');
  let referenced = false;
  b.child.ref = () => { referenced = true; };
  const stopped = b.finish();
  assert.equal(referenced, true);
  assert.equal(b.requests.at(-1).wait_for_browsers, true);
  b.child.emit('exit', 0);
  await stopped;
  b.child.stdout.end();
});

test('bridge awaits the VPN result and forwards launch parameters', async () => {
  const b = bridge({ ok: true, pid: 123 });
  const result = await b.launch('account1', 4, 'https://example.invalid', ['--mute-audio']);
  assert.equal(result.pid, 123);
  assert.equal(b.requests[0].slot, 4);
  assert.deepEqual(b.requests[0].arguments, ['--mute-audio']);
  b.child.stdout.end();
});

test('bridge propagates VPN rejection without reporting success', async () => {
  const b = bridge({ ok: false, error: 'VPN unavailable' });
  await assert.rejects(b.launch('account1', 0, 'https://example.invalid'), /VPN unavailable/);
  b.child.stdout.end();
});

test('bridge keeps concurrent replies associated with their requests', async () => {
  const b = bridge({ ok: true, pid: 123 });
  const results = await Promise.all([
    b.launch('account1', 0, 'https://example.invalid'),
    b.launch('account2', 1, 'https://example.invalid'),
  ]);
  assert.equal(results.length, 2);
  assert.notEqual(results[0].id, results[1].id);
  b.child.stdout.end();
});

function webManager(launchSecure, secureRequest) {
  let handler;
  const context = {
    __dirname: root, process: { env: {} }, setTimeout, Buffer,
    console: { log() {}, error() {} },
    require: name => {
      if (name === './secure_launch') return { launchSecure, secureRequest };
      if (name === './manager_access') return require(path.join(root, 'manager_access'));
      if (name === 'crypto') return { randomBytes: () => Buffer.alloc(32, 1) };
      if (name === 'child_process') return { spawnSync: () => ({ stdout: 'DISPLAY;0;0;1920;1080;True' }) };
      if (name === 'http') return { createServer: callback => { handler = callback; return { listen() {}, address: () => ({ port: 54321 }) }; } };
      if (name === 'fs') return { ...fs, existsSync: () => false };
      return require(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'server.js'), 'utf8'), context);
  return async (url, data) => {
    const request = new PassThrough();
    request.method = 'POST';
    request.url = url;
    request.headers = { host: '127.0.0.1:54321', 'x-manager-token': Buffer.alloc(32, 1).toString('base64url') };
    let status, body;
    const response = { setHeader() {}, writeHead(code) { status = code; }, end(value) { body = JSON.parse(value); } };
    const result = handler(request, response);
    request.end(JSON.stringify(data));
    await result;
    return { status, body };
  };
}

test('HTTP launch API reports an error when VPN verification fails', async () => {
  let called = false;
  const api = webManager(async () => { called = true; throw new Error('VPN unavailable'); });
  const result = await api('/api/launch-slot', { key: 'account1' });
  assert.equal(called, true);
  assert.equal(result.status, 500);
  assert.match(result.body.error, /VPN/);
  assert.equal(result.body.ok, undefined);
});

test('script installation uses the shared manager without opening a browser', async () => {
  let request;
  const api = webManager(() => { throw new Error('Should not launch Chrome'); }, async payload => {
    request = payload;
    return { userscript: { installed: true, version: '2' } };
  });
  const result = await api('/api/userscript', { script_url: 'https://github.com/a/b/blob/main/bot.js' });
  assert.equal(request.action, 'userscript-install');
  assert.equal(request.url, 'https://github.com/a/b/blob/main/bot.js');
  assert.equal(result.status, 200);
  assert.equal(result.body.version, '2');
});

test('script installation returns download errors without reporting success', async () => {
  const api = webManager(() => {}, async () => { throw new Error('GitHub unavailable'); });
  const result = await api('/api/userscript', { script_url: 'https://github.com/a/b/blob/main/bot.js' });
  assert.equal(result.status, 500);
  assert.equal(result.body.error, 'GitHub unavailable');
});

test('updating a selected script forwards its id without replacing other scripts', async () => {
  let request;
  const api = webManager(() => {}, async payload => {
    request = payload;
    return { userscript: { enabled_count: 2, scripts: [{ id: 'first' }, { id: 'second' }] } };
  });
  const result = await api('/api/userscript', { script_url: 'https://github.com/a/b/blob/main/helper.js', script_id: 'second' });
  assert.equal(request.script_id, 'second');
  assert.equal(result.body.enabled_count, 2);
});

test('HTTP launch API waits before returning success', async () => {
  let finish;
  const api = webManager(() => new Promise(resolve => { finish = resolve; }));
  const result = api('/api/launch-slot', { key: 'account1' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(typeof finish, 'function');
  finish({ ok: true, pid: 123 });
  const response = await result;
  assert.equal(response.status, 200);
  assert.equal(response.body.ok, true);
});
