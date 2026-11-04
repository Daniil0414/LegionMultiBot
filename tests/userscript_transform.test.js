'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { transform } = require('../userscript_transform');

function execute(source, extraPrivacy = {}) {
  const states = new WeakMap();
  const state = object => {
    if (object == null) return object;
    if (!states.has(object)) states.set(object, Object.create(null));
    return states.get(object);
  };
  const context = vm.createContext({ privacy: {state,
    init: (object, values) => { Object.assign(state(object), values); return object; },
    storage: { getItem: () => 'private-setting' }, ...extraPrivacy } });
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
  skip: !fs.existsSync(path.join(__dirname, 'fixtures', 'mine-bot-v9.1.8.user.js')),
}, () => {
  const source = fs.readFileSync(path.join(__dirname, 'fixtures', 'mine-bot-v9.1.8.user.js'), 'utf8');
  const result = transform(source);
  assert.ok(result.stats.globals > 300);
  assert.ok(result.stats.objectFlags > 15);
  assert.equal(result.stats.mineControls, 1);
});

test('mine control clicks the actual toggle only when a state change is needed', () => {
  let command;
  const page = execute(`(() => {
    window.__mineBotRunning = true;
    let isPaused = true, clicks = 0;
    const btn = {isConnected:true, click() {clicks++; this.onclick();}};
    function startBot() { isPaused = false; }
    function pauseBot() { isPaused = true; }
    btn.onclick = () => { if (isPaused) { startBot(); } else { pauseBot(); } };
    window.readFixture = () => ({isPaused, clicks});
    window.closeFixture = () => { window.__mineBotRunning = false; };
  })();`, {registerMineControl: handler => { command = handler; }});
  assert.equal(command('status').state, 'paused');
  assert.equal(page.readFixture().clicks, 0);
  assert.equal(command('start').state, 'running');
  assert.equal(command('start').changed, false);
  assert.equal(page.readFixture().clicks, 1);
  assert.equal(command('pause').state, 'paused');
  assert.equal(command('pause').changed, false);
  assert.equal(page.readFixture().clicks, 2);
  page.closeFixture();
  assert.throws(() => command('start'), /unavailable/);
  assert.equal(page.readFixture().clicks, 2);
});

test('mine control supports compound toggle conditions like isPaused && !miningStartToken', () => {
  let command;
  const fixture = `(() => {
    window.__mineBotRunning = true;
    let isPaused = true, miningStartToken = null, clicks = 0;
    const btn = {isConnected:true, click() {clicks++; this.onclick();}};
    function startBot() { isPaused = false; }
    function pauseBot() { isPaused = true; }
    btn.onclick = () => { if (isPaused && !miningStartToken) { startBot(); } else { pauseBot(); } };
    window.readFixture = () => ({isPaused, clicks});
    window.closeFixture = () => { window.__mineBotRunning = false; };
  })();`;
  const page = execute(fixture, {registerMineControl: handler => { command = handler; }});
  assert.equal(command('status').state, 'paused');
  assert.equal(page.readFixture().clicks, 0);
  assert.equal(command('start').state, 'running');
  assert.equal(command('start').changed, false);
  assert.equal(page.readFixture().clicks, 1);
  assert.equal(command('pause').state, 'paused');
  assert.equal(command('pause').changed, false);
  assert.equal(page.readFixture().clicks, 2);
});

test('mine control supports async startBot with miningStartToken and helmet equip delay', () => {
  let command;
  const fixture = `(() => {
    window.__mineBotRunning = true;
    let isPaused = true, miningStartToken = null, clicks = 0;
    const btn = {isConnected:true, textContent: 'Старт', click() {clicks++; this.onclick();}};
    const st = {textContent: 'На паузе'};
    async function startBot() {
      if (!isPaused || miningStartToken) return;
      const startToken = {};
      miningStartToken = startToken;
      btn.textContent = 'Пауза';
      st.textContent = 'Надеваем каску с фонариком…';
      await Promise.resolve();
      if (miningStartToken !== startToken || !window.__mineBotRunning) return;
      miningStartToken = null;
      isPaused = false;
      st.textContent = 'Работает';
    }
    function pauseBot() {
      miningStartToken = null;
      isPaused = true;
      btn.textContent = 'Старт';
      st.textContent = 'На паузе';
    }
    btn.onclick = () => { if (isPaused && !miningStartToken) { startBot(); } else { pauseBot(); } };
    window.readFixture = () => ({isPaused, miningStartToken: Boolean(miningStartToken), clicks, btnText: btn.textContent, stText: st.textContent});
    window.closeFixture = () => { window.__mineBotRunning = false; };
  })();`;
  const page = execute(fixture, {registerMineControl: handler => { command = handler; }});
  assert.equal(command('status').state, 'paused');
  assert.equal(page.readFixture().clicks, 0);

  const startResult = command('start');
  assert.equal(startResult.state, 'running');
  assert.equal(startResult.changed, true);
  assert.equal(page.readFixture().clicks, 1);
  assert.equal(page.readFixture().btnText, 'Пауза');
  assert.equal(page.readFixture().stText, 'Надеваем каску с фонариком…');

  const secondStart = command('start');
  assert.equal(secondStart.state, 'running');
  assert.equal(secondStart.changed, false);
  assert.equal(page.readFixture().clicks, 1);

  assert.equal(command('status').state, 'running');

  const pauseResult = command('pause');
  assert.equal(pauseResult.state, 'paused');
  assert.equal(pauseResult.changed, true);
  assert.equal(page.readFixture().clicks, 2);
  assert.equal(page.readFixture().btnText, 'Старт');
  assert.equal(page.readFixture().stText, 'На паузе');
});

test('a changed toggle format keeps the script intact and leaves remote control unavailable', () => {
  const result = transform('window.__mineBotRunning = true; const btn = {}; btn.onclick = () => startBot();');
  assert.equal(result.stats.mineControls, 0);
  assert.ok(result.code.includes('btn.onclick = () => startBot()'));
});
