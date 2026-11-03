'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const template = fs.readFileSync(path.join(__dirname, '..', 'userscript_runtime.js'), 'utf8');

function page(options = {}) {
  const events = {}, timers = [];
  const target = new EventTarget();
  const settings = { matches: ['^https://game\\.example/'], includes: [], excludes: [],
    noframes: false, runAt: 'document-start', digest: 'fixture', guard: 'fixture-private-guard', ...options.settings };
  const context = vm.createContext({
    location: { protocol: 'https:', href: 'https://game.example/play', ...options.location },
    document: { readyState: options.readyState || 'loading',
      addEventListener: (name, handler) => { events[name] = handler; } },
    addEventListener: target.addEventListener.bind(target),
    dispatchEvent: target.dispatchEvent.bind(target), Event,
    setTimeout: handler => timers.push(handler), console, executions: 0,
  });
  context.window = context;
  context.top = options.iframe ? {} : context;
  const code = template.replace('/*__SETTINGS__*/', JSON.stringify(settings))
    .replace('/*__SOURCE__*/', 'window.executions++; window.thisIsWindow = this === window;');
  return { context, events, flush: () => timers.splice(0).forEach(fn => fn()),
    run: () => vm.runInContext(code, context) };
}

test('script executes at document start once and uses the page window', () => {
  const p = page();
  p.run(); p.run();
  assert.equal(p.context.executions, 1);
  assert.equal(p.context.thisIsWindow, true);
  assert.equal(Object.getOwnPropertySymbols(p.context).length, 0);
});

test('URL rules, includes, excludes, internal pages and noframes are respected', () => {
  for (const options of [
    { location: { href: 'https://other.example/play' } },
    { location: { protocol: 'chrome:', href: 'chrome://settings' } },
    { iframe: true, settings: { noframes: true } },
    { settings: { excludes: ['play$'] } },
  ]) {
    const p = page(options); p.run(); assert.equal(p.context.executions, 0);
  }
  const included = page({ location: { href: 'https://other.example/play' }, settings: { includes: ['other\\.example'] } });
  included.run(); assert.equal(included.context.executions, 1);
});

test('document-end waits for DOMContentLoaded, or runs on an already ready document', () => {
  const p = page({ settings: { runAt: 'document-end' } });
  p.run(); assert.equal(p.context.executions, 0);
  p.events.DOMContentLoaded(); assert.equal(p.context.executions, 1);
  const ready = page({ readyState: 'interactive', settings: { runAt: 'document-end' } });
  ready.run(); assert.equal(ready.context.executions, 1);
});

test('document-idle waits for DOM readiness and schedules after page handlers', () => {
  const p = page({ settings: { runAt: 'document-idle' } });
  p.run(); assert.equal(p.context.executions, 0);
  p.events.DOMContentLoaded(); assert.equal(p.context.executions, 0);
  p.flush(); assert.equal(p.context.executions, 1);
  const ready = page({ readyState: 'interactive', settings: { runAt: 'document-idle' } });
  ready.run(); ready.flush(); assert.equal(ready.context.executions, 1);
});
