'use strict';
const { spawn } = require('child_process');
const path = require('path');
const readline = require('readline');

let worker;
let nextId = 0;
let finishing = false;
const pending = new Map();

function reference(active) {
  if (!worker) return;
  for (const handle of [worker, worker.stdin, worker.stdout, worker.stderr]) {
    if (handle && typeof handle[active ? 'ref' : 'unref'] === 'function') {
      handle[active ? 'ref' : 'unref']();
    }
  }
}

function ensureWorker() {
  if (worker) return;
  const child = spawn(process.env.LEGION_PYTHON || 'python',
    ['-B', '-u', path.join(__dirname, 'secure_launcher.py'), `--parent-pid=${process.pid}`],
    { cwd: __dirname, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  worker = child;
  const lines = readline.createInterface({ input: child.stdout });
  lines.on('line', line => {
    let result;
    try { result = JSON.parse(line); } catch { return; }
    const request = pending.get(result.id);
    if (!request) return;
    pending.delete(result.id);
    clearTimeout(request.timer);
    if (result.ok) request.resolve(result);
    else request.reject(new Error(result.error || 'VPN launch was blocked'));
    if (pending.size === 0 && !finishing) reference(false);
  });
  const failed = () => {
    if (worker !== child) return;
    worker = undefined;
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.reject(new Error('Protected browser launcher stopped'));
    }
    pending.clear();
    lines.close();
  };
  child.on('error', failed);
  child.on('exit', failed);
  child.stdin.on('error', failed);
  child.stderr.resume();
}

function secureRequest(payload) {
  if (finishing) return Promise.reject(new Error('Protected browser launcher is shutting down'));
  ensureWorker();
  const id = ++nextId;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error('Timed out waiting for VPN verification'));
      if (pending.size === 0 && !finishing) reference(false);
    }, 90000);
    pending.set(id, { resolve, reject, timer });
    reference(true);
    worker.stdin.write(JSON.stringify({ ...payload, id }) + '\n');
  });
}

function launchSecure(key, slot, url, argumentsList = []) {
  return secureRequest({ key, slot, url, arguments: argumentsList });
}

function finishWorker(waitForBrowsers) {
  finishing = true;
  if (!worker) return Promise.resolve();
  const child = worker;
  reference(true);
  return new Promise(resolve => {
    // A stuck launch must not prevent the manager from exiting indefinitely.
    const timer = waitForBrowsers ? null : setTimeout(() => child.kill(), 8000);
    const done = () => { clearTimeout(timer); resolve(); };
    child.once('exit', done);
    child.once('error', done);
    child.stdin.end(JSON.stringify({ action: 'finish', wait_for_browsers: waitForBrowsers }) + '\n');
  });
}

const closeSecure = () => finishWorker(false);
const finishLaunches = () => finishWorker(true);
module.exports = { launchSecure, secureRequest, closeSecure, finishLaunches };
