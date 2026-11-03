'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { transform } = require('../userscript_transform');

function execute(source) {
  const states = new WeakMap();
  const state = object => {
    if (object == null) return object;
    if (!states.has(object)) states.set(object, Object.create(null));
    return states.get(object);
  };
  const context = vm.createContext({ privacy: {state,
    init: (object, values) => { Object.assign(state(object), values); return object; },
    storage: { getItem: () => 'private-setting' } } });
  context.window = context;
  vm.runInContext(transform(source).code, context);
  return context;
}

test('root state and game-object flags stay in closure storage', () => {
  const page = execute(`const G = window; G.__mineBotRunning = true;
    G.module = {}; G.module.__botHooked = true;
    G.result = [G.__mineBotRunning, G.module.__botHooked];`);
  assert.deepEqual(Array.from(page.result), [true, true]);
  assert.equal(page.__mineBotRunning, undefined);
  assert.deepEqual(Object.keys(page.module), []);
});

test('supports optional members, bracket members and private object initializers', () => {
  const page = execute(`const G = window; G['__mineBotRunning'] = true;
    const x = {__isBotFallback: true, api() {return 7}};
    G.result = [x.__isBotFallback, x.api(), null?.__isBotFallback, G.__mineBotRunning]; G.exposed = x;`);
  assert.deepEqual(Array.from(page.result), [true, 7, undefined, true]);
  assert.deepEqual(Object.keys(page.exposed), ['api']);
});

test('leaves strings, comments and normal game state intact', () => {
  const page = execute(`const G = window; G.__mineBotRunning = true;
    // window.__mineBotStop must remain a comment
    G.text = 'window.__mineBotRunning'; G.state = {hp: 12};
    G.read = [localStorage.getItem('lg_bot_min'), window.localStorage.getItem('lg_bot_min')];`);
  assert.equal(page.text, 'window.__mineBotRunning');
  assert.equal(page.state.hp, 12);
  assert.deepEqual(Array.from(page.read), ['private-setting', 'private-setting']);
});

test('adaptation parses the complete checked-in bot', {
  skip: !fs.existsSync(path.join(__dirname, '..', 'mine-bot.user.js')),
}, () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'mine-bot.user.js'), 'utf8');
  const result = transform(source);
  assert.ok(result.stats.globals > 300);
  assert.ok(result.stats.objectFlags > 15);
});
