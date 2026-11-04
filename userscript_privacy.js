(config => {
  if (!config) return Promise.reject(new Error('Private userscript storage is unavailable'));
  const parse = JSON.parse, stringify = JSON.stringify;
  const create = Object.create, assign = Object.assign, own = Object.hasOwn;
  const entries = Object.entries, objectKeys = Object.keys, freeze = Object.freeze;
  const states = new WeakMap();
  const stateGet = WeakMap.prototype.get.bind(states);
  const stateHas = WeakMap.prototype.has.bind(states);
  const stateSet = WeakMap.prototype.set.bind(states);
  const nativeCreate = document.createElement.bind(document);
  const nativeQuery = document.querySelector.bind(document);
  const nativeQueryAll = document.querySelectorAll.bind(document);
  const nativeGetId = document.getElementById.bind(document);
  const nativeAppend = Function.prototype.call.bind(Node.prototype.appendChild);
  const nativeContains = Function.prototype.call.bind(Node.prototype.contains);
  const nativeAttach = Function.prototype.call.bind(Element.prototype.attachShadow);
  const shadowQuery = Function.prototype.call.bind(DocumentFragment.prototype.querySelector);
  const shadowQueryAll = Function.prototype.call.bind(DocumentFragment.prototype.querySelectorAll);
  const cssEscape = CSS.escape;
  const originalTitle = document.title;
  const uiRoots = new Set(['mine-bot-v3', 'mine-bot-v2', 'b-auc-tooltip',
    'lg-mine-floating-container', 'native-portal-hud']);
  const ghostClass = 'g' + crypto.getRandomValues(new Uint32Array(3)).join('');
  let host = null, shadow = null;
  const privateRoot = () => {
    if (!shadow) {
      host = nativeCreate('div');
      host.style.cssText = 'all:initial;display:contents';
      shadow = nativeAttach(host, {mode: 'closed'});
    }
    if (!host.isConnected) nativeAppend(document.body || document.documentElement, host);
    return shadow;
  };
  const dispatch = EventTarget.prototype.dispatchEvent.bind(window);
  const listen = EventTarget.prototype.addEventListener.bind(window);
  const unlisten = EventTarget.prototype.removeEventListener.bind(window);
  const EventClass = CustomEvent;
  let mineControl = null;
  const domMineControl = action => {
    const btn = (shadow && shadowQuery(shadow, '#b-btn')) || nativeQuery('#b-btn') || (document.querySelector && document.querySelector('#b-btn'));
    if (!btn) throw new Error('unavailable');
    const st = (shadow && shadowQuery(shadow, '#b-st')) || nativeQuery('#b-st') || (document.querySelector && document.querySelector('#b-st'));
    const isPaused = st ? (st.textContent.includes('пауз') || !st.textContent.includes('работ'))
                        : (btn.textContent.includes('Старт') || !btn.textContent.includes('Пауз'));
    const wantsPause = action === 'pause';
    const previous = isPaused;
    if (isPaused !== wantsPause) {
      btn.click();
    }
    const currentPaused = st ? (st.textContent.includes('пауз') || !st.textContent.includes('работ'))
                             : (btn.textContent.includes('Старт') || !btn.textContent.includes('Пауз'));
    return { state: currentPaused ? 'paused' : 'running', changed: previous !== currentPaused };
  };
  if (config.mineCommand && config.mineReply) listen(config.mineCommand, event => {
    if (typeof event.detail !== 'string' || event.detail.length > 1024) return;
    let packet;
    try { packet = parse(event.detail); } catch (_) { return; }
    if (!packet || typeof packet.id !== 'string' || !['start', 'pause'].includes(packet.action)) return;
    let result;
    try {
      if (typeof mineControl === 'function') {
        try { result = {ok: true, ...mineControl(packet.action)}; }
        catch (_) { result = {ok: true, ...domMineControl(packet.action)}; }
      } else {
        result = {ok: true, ...domMineControl(packet.action)};
      }
    }
    catch (error) { result = {ok: false, error: error.message === 'not_confirmed' ? 'not_confirmed' : 'unavailable'}; }
    dispatch(new EventClass(config.mineReply, {detail: stringify({id: packet.id, ...result})}));
  });
  const later = setTimeout.bind(window), cancel = clearTimeout.bind(window);
  const nativeStorage = window.localStorage;
  const get = Storage.prototype.getItem.bind(nativeStorage);
  const set = Storage.prototype.setItem.bind(nativeStorage);
  const remove = Storage.prototype.removeItem.bind(nativeStorage);
  const keyAt = Storage.prototype.key.bind(nativeStorage);
  const lengthOf = Object.getOwnPropertyDescriptor(Storage.prototype, 'length').get.bind(nativeStorage);
  const exact = new Set(['lg_funded_drills_keys', 'lg_my_funded_drills_count',
    'lg_my_funded_drills_coins', 'lg_hero_role', 'lg_mining_cap_day', 'lg_last_friendcoin_date']);
  const isPrivate = key => exact.has(key) || /^lg_(?:bot|quest|unlocked_sk)_[A-Za-z0-9_-]+$/.test(key);
  const requests = new Map();
  let sequence = 0;
  listen(config.reply, event => {
    if (typeof event.detail !== 'string') return;
    let packet;
    try { packet = parse(event.detail); } catch (_) { return; }
    if (packet.client !== config.client) return;
    const pending = requests.get(packet.id);
    if (!pending) return;
    requests.delete(packet.id);
    cancel(pending.timer);
    packet.ok ? pending.resolve(packet.result) : pending.reject(new Error(packet.error || 'Private storage failed'));
  });
  function request(payload) {
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      let sent = false;
      const send = () => {
        if (sent) return;
        const handled = !dispatch(new EventClass(config.request,
          { detail: stringify({ ...payload, id, client: config.client }), cancelable: true }));
        if (handled) { sent = true; unlisten(config.ready, send); }
      };
      const timer = later(() => {
        requests.delete(id);
        unlisten(config.ready, send);
        reject(new Error('Private storage did not respond; bot execution is blocked'));
      }, 10000);
      requests.set(id, {resolve, reject, timer});
      listen(config.ready, send);
      send();
    });
  }
  const legacy = create(null);
  for (let i = 0; i < lengthOf(); i++) {
    const key = keyAt(i);
    if (key && isPrivate(key)) legacy[key] = get(key);
  }
  return request({action: 'load', legacy}).then(result => {
    const values = assign(create(null), result.values);
    // Delete only acknowledged copies; leave unrelated game data untouched.
    for (const [key, value] of entries(legacy)) {
      if (own(values, key) && get(key) === value) remove(key);
    }
    const save = payload => request(payload).catch(error => console.error(error));
    const keys = () => {
      const all = new Set(objectKeys(values));
      for (let i = 0; i < lengthOf(); i++) {
        const key = keyAt(i);
        if (key && !isPrivate(key)) all.add(key);
      }
      return Array.from(all);
    };
    const storage = freeze({
      getItem(key) {
        key = String(key);
        return isPrivate(key) ? (own(values, key) ? values[key] : null) : get(key);
      },
      setItem(key, value) {
        key = String(key); value = String(value);
        if (!isPrivate(key)) { set(key, value); return; }
        if (key.length > 128 || value.length > 65536) throw new Error('Private setting exceeds the limit');
        values[key] = value;
        save({action: 'set', key, value});
      },
      removeItem(key) {
        key = String(key);
        if (!isPrivate(key)) { remove(key); return; }
        delete values[key];
        save({action: 'remove', key});
      },
      clear() { for (const key of keys()) this.removeItem(key); },
      key(index) { return keys()[Number(index) >>> 0] ?? null; },
      get length() { return keys().length; },
    });
    const state = object => {
      if (object == null) return object;
      if (!stateHas(object)) stateSet(object, create(null));
      return stateGet(object);
    };
    return {storage, state, ghostClass, title: originalTitle,
      registerMineControl(handler) { mineControl = handler; },
      get activeElement() { return shadow?.activeElement || document.activeElement; },
      init(object, privateValues) { assign(state(object), privateValues); return object; },
      query(method, selector) {
        if (method === 'getElementById') return (shadow && shadowQuery(shadow, '#' + cssEscape(String(selector)))) || nativeGetId(selector);
        if (method === 'querySelector') return (shadow && shadowQuery(shadow, selector)) || nativeQuery(selector);
        const nodes = [...nativeQueryAll(selector), ...(shadow ? shadowQueryAll(shadow, selector) : [])];
        nodes.item = index => nodes[index] || null;
        return nodes;
      },
      contains(container, node) {
        return nativeContains(container, node) || !!(shadow && nativeContains(shadow, node));
      },
      append(container, node) {
        if (uiRoots.has(node.id)) return nativeAppend(privateRoot(), node);
        if (node.tagName === 'STYLE' && ['lg-mine-fixes-style', 'lg-ghost-mode-style'].includes(node.id)) {
          // Keep bot CSS inside its root. Only the original game-rendering fixes
          // need a stylesheet in the document; it has no bot element identifier.
          const ghost = node.id === 'lg-ghost-mode-style';
          node.textContent = node.textContent.replaceAll('lg-ghost-mode', ghostClass);
          const sheet = new CSSStyleSheet();
          sheet.replaceSync(node.textContent);
          const gameCss = Array.from(sheet.cssRules).filter(rule => ghost ||
            /^\.mn-(fig|h)\b/.test(rule.selectorText || '')).map(rule => rule.cssText).join('\n');
          if (gameCss) {
            const style = nativeCreate('style');
            style.textContent = gameCss;
            nativeAppend(container, style);
          }
          return nativeAppend(privateRoot(), node);
        }
        return nativeAppend(container, node);
      }};
  });
})
