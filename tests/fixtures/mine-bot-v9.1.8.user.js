// ==UserScript==
// @name         Шамиль Клиент - Legion Auto-Mine, Market, Players, Pocket City & Quest Dungeon
// @namespace    https://github.com/Daniil0414/LegionAutoMiner
// @version      9.1.8
// @description  Auto-mining bot, Stealth Anti-Detection, Mine 3.0 Auto-Drill (Collect/Build), Auto-Claim Quests, Auto-Lift (+30% online), Drill Tracker (5m), Drag&Drop Ore Priorities, Auction 409/429/Raffle fixes, Auction market sniper, Player Intelligence, Pocket City Hub, Mass PM Broadcast & Quest Dungeon Module for Legion MMO (ZakvielChannel).
// @author       Daniil0414
// @match        https://play.multidonate.ru/c/*
// @icon         https://www.google.com/s2/favicons?sz=64&domain=play.multidonate.ru
// @updateURL    https://raw.githubusercontent.com/Daniil0414/LegionAutoMiner/main/mine-bot.user.js
// @downloadURL  https://raw.githubusercontent.com/Daniil0414/LegionAutoMiner/main/mine-bot.user.js
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(() => {
  'use strict';

  const G = typeof unsafeWindow !== 'undefined' ? unsafeWindow : window;

  // Очищаем старые экземпляры бота (защита от двойного запуска)
  if (G.__mineBotStop) {
    try { G.__mineBotStop(); } catch (e) {}
  }
  document.querySelectorAll('#mine-bot-v3, #mine-bot-v2, #b-auc-tooltip').forEach(el => el.remove());
  G.__mineBotRunning = true;

  // =========================================================================
  // 🛡️ КРИТИЧЕСКИЙ АНТИ-ДЕТЕКТ И СТЕЛС-РЕЖИМ (30.10: РЕПОРТ #2168 «АВТО-МАЙНЕР»)
  // =========================================================================
  // В обновлении игры 30.10 добавлена телеметрия tlPi() и bwOf(pt):
  // 1. Проверка нативности: natv(window.fetch) и natv(window.requestAnimationFrame);
  // 2. Проверка нетронутости методов шахты: LGMine.open === open, LGMine.onEvent === onEvent;
  // 3. Проверка биома: LGMineBiome.own() (paint === origPaint && off === origOff);
  // 4. Проверка дескрипторов Document.prototype: hidden, visibilityState, hasFocus (tlHp);
  // 5. Проверка реальной видимости вкладки (TL.rv);
  // 6. Проверка естественных кликов (TL.ti, ut: 1 при isTrusted === false).
  //
  // НАША 100% НЕУЯЗВИМАЯ ЗАЩИТА:
  // - Скрытый маскировочный движок: Function.prototype.toString возвращает
  //   "function ...() { [native code] }" для всех перехваченных методов;
  // - Умные геттеры LGMine.open/onEvent и LGMineBiome.paint/off: при вызове
  //   из tlPi() / tele() / own() мгновенно отдают оригинальные нативные ссылки,
  //   гарантируя tlPi() === 0 (биты 1, 2, 4 = 0);
  // - Второй эшелон обороны: в перехвате fetch для /api/mineDig мы вычищаем
  //   любые поля pi, ut, rv, hp, gm из объекта телеметрии tl и гарантируем
  //   естественный ti (1-2 клика живого человека).
  // =========================================================================

  const stealthNativeFns = new WeakSet();
  const customFnNames = new WeakMap();

  try {
    const patchToStringOn = (targetProto) => {
      if (!targetProto) return;
      const orig = targetProto.toString;
      function customToString() {
        if (stealthNativeFns.has(this)) {
          const fnName = customFnNames.get(this) || this.name || '';
          return `function ${fnName}() { [native code] }`;
        }
        return orig.apply(this, arguments);
      }
      targetProto.toString = customToString;
      stealthNativeFns.add(customToString);
      customFnNames.set(customToString, 'toString');
    };

    patchToStringOn(Function.prototype);
    if (G.Function && G.Function.prototype && G.Function.prototype !== Function.prototype) {
      patchToStringOn(G.Function.prototype);
    }
    if (window.Function && window.Function.prototype && window.Function.prototype !== Function.prototype && window.Function.prototype !== G.Function?.prototype) {
      patchToStringOn(window.Function.prototype);
    }
  } catch (e) {}

  function makeStealthNative(fn, name) {
    if (typeof fn === 'function') {
      try {
        stealthNativeFns.add(fn);
        if (name) customFnNames.set(fn, name);
      } catch (e) {}
    }
    return fn;
  }

  let origHiddenGetter = null;
  let origVisibilityGetter = null;
  let isWindowFocused = true;

  try {
    const protos = [Document.prototype];
    if (G.Document && G.Document.prototype && !protos.includes(G.Document.prototype)) protos.push(G.Document.prototype);
    if (window.Document && window.Document.prototype && !protos.includes(window.Document.prototype)) protos.push(window.Document.prototype);

    for (const proto of protos) {
      try {
        const descH = Object.getOwnPropertyDescriptor(proto, 'hidden');
        if (descH && typeof descH.get === 'function') {
          if (!origHiddenGetter) origHiddenGetter = descH.get;
          makeStealthNative(descH.get, 'get hidden');
        }
        const descV = Object.getOwnPropertyDescriptor(proto, 'visibilityState');
        if (descV && typeof descV.get === 'function') {
          if (!origVisibilityGetter) origVisibilityGetter = descV.get;
          makeStealthNative(descV.get, 'get visibilityState');
        }
        if (typeof proto.hasFocus === 'function') {
          makeStealthNative(proto.hasFocus, 'hasFocus');
        }
      } catch (e) {}
    }

    // Гарантируем чистоту document от собственных свойств (чтобы tlHp() возвращал 0)
    const docs = [document];
    if (G.document && !docs.includes(G.document)) docs.push(G.document);
    for (const doc of docs) {
      try {
        if (Object.prototype.hasOwnProperty.call(doc, 'hidden')) delete doc.hidden;
        if (Object.prototype.hasOwnProperty.call(doc, 'visibilityState')) delete doc.visibilityState;
        if (Object.prototype.hasOwnProperty.call(doc, 'hasFocus')) delete doc.hasFocus;
      } catch (e) {}
    }

    // Блокируем всплытие событий скрытия вкладки в capture-фазе (игра не гасит SSE и таймеры комнат)
    const suppressHiddenEvent = (e) => {
      if (e && (e.type === 'visibilitychange' || e.type === 'webkitvisibilitychange')) {
        e.stopImmediatePropagation();
      }
    };
    window.addEventListener('visibilitychange', suppressHiddenEvent, true);
    document.addEventListener('visibilitychange', suppressHiddenEvent, true);
    if (G.window && G.window !== window) {
      G.window.addEventListener('visibilitychange', suppressHiddenEvent, true);
    }

    window.addEventListener('focus', () => { isWindowFocused = true; }, true);
    window.addEventListener('blur', () => { isWindowFocused = false; }, true);
  } catch (e) {
  }

  // Честная проверка реальной видимости вкладки для наших внутренних фоновых оптимизаций
  function isPageActuallyHidden() {
    try {
      if (origHiddenGetter) {
        const val = origHiddenGetter.call(document);
        if (typeof val === 'boolean') return val;
      }
      if (origVisibilityGetter) {
        const v = origVisibilityGetter.call(document);
        if (v === 'hidden') return true;
      }
    } catch (e) {}
    return !isWindowFocused;
  }

  // =========================================================================
  // ⚡ ФОНОВЫЙ ПОЛИФИЛ requestAnimationFrame ДЛЯ ШАХТЫ И ИГРЫ
  // В фоновой вкладке браузер полностью останавливает native RAF (0 FPS).
  // Из-за этого игра замирает на шаге героя к новому камню (S.path не очищается,
  // S.then не вызывается, /api/mineDig не отправляется).
  // Полифил гарантирует выполнение очередей кадров в фоне через быстрый таймер!
  // =========================================================================
  // 🧹 Очистка остаточных ключей оптимизаций в браузере
  try {
    localStorage.removeItem('lg_bot_eco_optimizer');
    localStorage.removeItem('lgCityR');
    localStorage.removeItem('lgCityHide');
    localStorage.removeItem('lgCityCap');
    localStorage.removeItem('lgCityQ');
    localStorage.removeItem('lgMineHide');
    if (document.body) document.body.classList.remove('lg-eco-active');
  } catch (e) {}

  const origRAF = (G.requestAnimationFrame || window.requestAnimationFrame)?.bind(G) || null;
  const origCAF = (G.cancelAnimationFrame || window.cancelAnimationFrame)?.bind(G) || null;
  const bgRafCallbacks = new Map();
  let bgRafIdCounter = 10000;

  function safeRAF(cb) {
    if (!isPageActuallyHidden() && typeof origRAF === 'function') {
      return origRAF(cb);
    }
    const id = ++bgRafIdCounter;
    const delay = 40;
    const t = setTimeout(() => {
      bgRafCallbacks.delete(id);
      try {
        cb(performance.now());
      } catch (e) {}
    }, delay);
    bgRafCallbacks.set(id, t);
    return id;
  }

  function safeCAF(id) {
    if (bgRafCallbacks.has(id)) {
      clearTimeout(bgRafCallbacks.get(id));
      bgRafCallbacks.delete(id);
      return;
    }
    if (typeof origCAF === 'function') {
      try { origCAF(id); } catch (e) {}
    }
  }

  makeStealthNative(safeRAF, 'requestAnimationFrame');
  makeStealthNative(safeCAF, 'cancelAnimationFrame');

  try {
    G.requestAnimationFrame = safeRAF;
    G.cancelAnimationFrame = safeCAF;
    window.requestAnimationFrame = safeRAF;
    window.cancelAnimationFrame = safeCAF;
  } catch (e) {}

  // =========================================================================
  // 🎵 БЕСШУМНЫЙ WEB AUDIO KEEP-ALIVE (ОБХОД СНА ВКЛАДОК И MEMORY SAVER)
  // Активный AudioContext с нулевой громкостью (gain = 0) сигнализирует Chromium/Edge,
  // что вкладка воспроизводит аудио. Это полностью исключает:
  // 1) Замораживание вкладки (Tab Freezing / Memory Saver);
  // 2) 60-секундный троттлинг таймеров (Intensive Wake Up Throttling);
  // 3) Выгрузку вкладки из памяти (Tab Discarding).
  // При этом звук абсолютно неслышен (0 дБ) и не мешает другим вкладкам.
  // =========================================================================
  let bgAudioCtx = null;
  function initBackgroundAudioKeepAlive() {
    if (bgAudioCtx) return;
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext || G.AudioContext || G.webkitAudioContext;
      if (!AudioCtx) return;
      bgAudioCtx = new AudioCtx();
      const osc = bgAudioCtx.createOscillator();
      const gain = bgAudioCtx.createGain();
      gain.gain.value = 0; // Полная тишина (0 дБ)
      osc.connect(gain);
      gain.connect(bgAudioCtx.destination);
      osc.start();

      const resumeAudio = () => {
        if (bgAudioCtx && bgAudioCtx.state === 'suspended') {
          bgAudioCtx.resume().catch(() => {});
        }
      };
      window.addEventListener('click', resumeAudio, { passive: true, capture: true });
      window.addEventListener('touchstart', resumeAudio, { passive: true, capture: true });
      window.addEventListener('keydown', resumeAudio, { passive: true, capture: true });
      resumeAudio();
    } catch (e) {}
  }

  // =========================================================================
  // ⚙️ НЕУБИВАЕМЫЙ WEB WORKER ХАРТБИТ (ИЗОЛИРОВАННЫЙ СИСТЕМНЫЙ ПОТОК)
  // Таймеры внутри Web Worker выполняются в отдельном системном потоке OS
  // и НЕ замедляются Chromium даже после многих часов в фоне.
  // Воркер раз в 1.2с пробуждает основной поток, проверяет застрявшую ходьбу
  // и держит цикл автошахты строго по расписанию.
  // =========================================================================
  let bgWorker = null;
  let lastStepExecutionTime = Date.now();

  function initBackgroundWorker() {
    if (bgWorker) return;
    try {
      const workerCode = `
        let timer = null;
        self.onmessage = function(e) {
          if (e.data === 'start') {
            if (!timer) {
              timer = setInterval(function() {
                self.postMessage('tick');
              }, 1200);
            }
          } else if (e.data === 'stop') {
            if (timer) {
              clearInterval(timer);
              timer = null;
            }
          }
        };
      `;
      const blob = new Blob([workerCode], { type: 'application/javascript' });
      const url = URL.createObjectURL(blob);
      bgWorker = new Worker(url);
      bgWorker.onmessage = function(e) {
        if (e.data === 'tick') {
          onBackgroundHeartbeat();
        }
      };
      bgWorker.postMessage('start');
    } catch (e) {}
  }

  // =========================================================================
  // 🚶 БЕЗОПАСНЫЙ WATCHDOG ПЕРЕДВИЖЕНИЯ В ФОНЕ (В СООТВЕТСТВИИ С ФИЗИКОЙ ИГРЫ)
  // В игре скорость героя SPEED = 260 px/сек, а сервер проверяет MINE_REACH (200px).
  // Бот позволяет персонажу честно дойти до цели через safeRAF,
  // а принудительно завершает шаг ТОЛЬКО если персонаж реально завис дольше положенного времени.
  // Это исключает ошибки сервера "Не подойти" и гарантирует, что руда никогда не уйдет в ложный бан!
  // =========================================================================
  function resolveBackgroundWalking(force = false) {
    const S = G.__lgMineS;
    if (!S) return false;
    // 🛡️ Защита: во время перехода этажей (< 5 сек) не вмешиваемся в анимацию спавна/хода персонажа
    if (Date.now() - lastFloorTransitionTime < 5000) return false;
    if (S.path && S.path.length > 0 && typeof S.then === 'function') {
      const elapsed = Date.now() - targetAssignedTime;
      let requiredTime = 2500;
      try {
        const lastPt = S.path[S.path.length - 1];
        if (lastPt && S.pos) {
          const dist = Math.hypot(lastPt.x - S.pos.x, lastPt.y - S.pos.y);
          requiredTime = Math.max(1000, (dist / 260) * 1000 + 800);
        }
      } catch (e) {}

      if (force || elapsed > requiredTime) {
        const lastPt = S.path[S.path.length - 1];
        if (lastPt) {
          S.pos = { x: lastPt.x, y: lastPt.y };
        }
        S.path = null;
        if (S.raf) {
          try { safeCAF(S.raf); } catch (e) {}
          S.raf = 0;
        }
        const cb = S.then;
        S.then = null;
        try {
          if (typeof cb === 'function') cb();
        } catch (e) {}
        return true;
      }
    }
    return false;
  }

  function onBackgroundHeartbeat() {
    if (!G.__mineBotRunning) return;

    if (isPaused) return;

    // 1. Мгновенно проталкиваем отправку /api/mineDig, если персонаж завис на шаге к камню
    resolveBackgroundWalking();

    // 2. Watchdog: если Chromium усыпил основной таймер step() дольше чем на 2.8с,
    // пробуждаем цикл бота из отдельного потока воркера!
    const elapsed = Date.now() - lastStepExecutionTime;
    if (elapsed > 2800) {
      clearTimeout(timer);
      step();
    }
  }

  let mode = 'screen'; // По умолчанию 'screen', можно переключить на 'ghost' (из города)
  let isPaused = true; // При запуске скрипт находится на паузе (не начинает копать автоматически)
  let isDrillCollect = localStorage.getItem('lg_bot_drill_collect') !== 'false'; // По умолчанию true (пассивный сбор руды с буров)
  let isDrillBuild = localStorage.getItem('lg_bot_drill_build') !== 'false'; // По умолчанию true (отдельный тумблер авто-установки/взноса на текущем этаже)
  let isDrillBuildAllFloors = localStorage.getItem('lg_bot_drill_build_all_floors') === 'true'; // По умолчанию false (взнос в буры на ВСЕХ этажах 1..N через API)
  let autoClaimQuests = localStorage.getItem('lg_bot_auto_claim_quests') !== 'false'; // По умолчанию true (автосбор поручений)
  let isClaimingQuests = false;
  let lastQuestClaimTime = 0;
  let autoLiftBestFloor = localStorage.getItem('lg_bot_auto_lift') !== 'false'; // По умолчанию true (раз в 2 мин, переход при +30% людей)
  let lastLiftCheckTime = 0;
  let isCheckingLiftFloors = false;
  let isRidingLift = false;
  let pendingLiftRide = null;
  // Восстановление профинансированных буров из localStorage
  let savedFundedDrills = [];
  try {
    savedFundedDrills = JSON.parse(localStorage.getItem('lg_funded_drills_keys') || '[]');
  } catch (e) {}
  const fundedDrills = new Set(Array.isArray(savedFundedDrills) ? savedFundedDrills : []);

  // ⏳ Суточный лимит копания (10 часов / me.cap, сброс в 05:00 МСК)
  let cachedDailyMiningCapReached = false;
  let cachedDailyMiningCapDay = '';

  function getMineGamingDay(ts = Date.now()) {
    // 05:00 МСК (02:00 UTC) — граница игровых суток
    return new Date(ts - 2 * 3600 * 1000).toISOString().slice(0, 10);
  }

  let myFundedDrillsCount = parseInt(localStorage.getItem('lg_my_funded_drills_count') || '0', 10) || 0;
  let myFundedDrillsTotalCoins = parseInt(localStorage.getItem('lg_my_funded_drills_coins') || '0', 10) || 0;

  function saveFundedDrillsState() {
    try {
      localStorage.setItem('lg_funded_drills_keys', JSON.stringify(Array.from(fundedDrills)));
      localStorage.setItem('lg_my_funded_drills_count', String(myFundedDrillsCount));
      localStorage.setItem('lg_my_funded_drills_coins', String(myFundedDrillsTotalCoins));
    } catch (e) {}
  }
  let lastDrillCheckTime = 0;

  // =========================================================================
  // 🎯 НАСТРАИВАЕМЫЕ ПРИОРИТЕТЫ ДОБЫЧИ РУД И СУНДУКОВ (DRAG & DROP)
  // По умолчанию: Железо ВЫШЕ Угля!
  // =========================================================================
  const DEFAULT_ORE_PRIORITIES = [
    { id: 'chest', name: '🎁 Сундук (Клад)', color: '#ffd700', enabled: true },
    { id: 'rare', name: '💎 Редкие самоцветы', color: '#c084fc', enabled: true },
    { id: 'iron', name: '⚪ Железо', color: '#cbd5e1', enabled: true },
    { id: 'coal', name: '⚫ Уголь', color: '#94a3b8', enabled: true },
    { id: 'gold', name: '🟡 Золото', color: '#facc15', enabled: true },
    { id: 'silver', name: '🪙 Серебро', color: '#e2e8f0', enabled: true },
    { id: 'crystal', name: '🔷 Кристаллы / Сера', color: '#67e8f9', enabled: true },
    { id: 'obsidian', name: '🟣 Обсидиан / Мифрил', color: '#a855f7', enabled: true },
    { id: 'other_ore', name: '⛏️ Прочая руда', color: '#fb923c', enabled: true },
    { id: 'rock', name: '🪨 Обычный камень', color: '#9ca3af', enabled: true }
  ];

  function saveCustomPriorities(list) {
    try {
      localStorage.setItem('lg_bot_custom_priorities', JSON.stringify(list));
    } catch (e) {}
  }

  function loadCustomPriorities() {
    try {
      const raw = localStorage.getItem('lg_bot_custom_priorities');
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed) && parsed.length > 0) {
          const loadedIds = new Set(parsed.map(x => x.id));
          const result = parsed.map(item => {
            const def = DEFAULT_ORE_PRIORITIES.find(d => d.id === item.id);
            return {
              id: item.id,
              name: def ? def.name : item.name,
              color: def ? def.color : item.color,
              enabled: item.enabled !== false
            };
          });
          for (const def of DEFAULT_ORE_PRIORITIES) {
            if (!loadedIds.has(def.id)) {
              result.push({ ...def });
            }
          }
          return result;
        }
      }
    } catch (e) {}
    return DEFAULT_ORE_PRIORITIES.map(x => ({ ...x }));
  }

  let customPriorities = loadCustomPriorities();

  function getRockCategory(rockId, group) {
    if (rockId == null) return 'rock';
    const idx = typeof rockId === 'number' ? rockId : parseInt(rockId, 10);
    if (!isNaN(idx) && idx >= 0) {
      const cell = getFloorCell(idx);
      if (cell && Array.isArray(cell)) {
        const [t, lv, r] = cell;
        if (t === 'chest' || t === 'prize') return 'chest';
        const res = r ? String(r).toLowerCase() : '';
        if (res === 'iron') return 'iron';
        if (res === 'coal') return 'coal';
        if (res === 'gold') return 'gold';
        if (res === 'silver') return 'silver';
        if (['crystal', 'sulfur'].includes(res)) return 'crystal';
        if (['obsidian', 'mythril', 'adamantite'].includes(res)) return 'obsidian';
        if (t === 'rare' || ['ruby', 'emerald', 'sapphire', 'amber', 'topaz', 'garnet', 'amethyst', 'malachite', 'morion', 'aquamarine', 'runestone'].includes(res)) return 'rare';
        if (t === 'ore' || res) return 'other_ore';
        if (t === 'rock' || ['stone', 'basalt'].includes(res)) return 'rock';
      }
    }

    const name = formatRockName(rockId);
    if (name) {
      const l = name.toLowerCase();
      if (l.includes('сундук') || l.includes('клад') || l.includes('chest') || l.includes('prize')) return 'chest';
      if (l.includes('железо') || l.includes('железа') || l.includes('iron')) return 'iron';
      if (l.includes('уголь') || l.includes('угля') || l.includes('coal')) return 'coal';
      if (l.includes('золото') || l.includes('золота') || l.includes('gold')) return 'gold';
      if (l.includes('серебр') || l.includes('серебра') || l.includes('silver')) return 'silver';
      if (l.includes('кристалл') || l.includes('сера') || l.includes('серы') || l.includes('sulfur')) return 'crystal';
      if (l.includes('обсидиан') || l.includes('мифрил') || l.includes('адамантит')) return 'obsidian';
      if (l.includes('редк') || l.includes('самоцвет') || l.includes('рубин') || l.includes('изумруд') || l.includes('сапфир') || l.includes('топаз') || l.includes('янтарь')) return 'rare';
      if (l.includes('руд') || l.includes('жил')) return 'other_ore';
      if (l.includes('камен') || l.includes('базальт') || l.includes('пород')) return 'rock';
    }

    if (group) {
      const title = ((group.getAttribute && (group.getAttribute('title') || group.getAttribute('data-t') || group.getAttribute('data-obj'))) || '').toLowerCase();
      if (title.includes('сундук') || title.includes('клад') || title.includes('chest') || title.includes('prize')) return 'chest';
      if (title.includes('железо') || title.includes('железа') || title.includes('iron')) return 'iron';
      if (title.includes('уголь') || title.includes('угля') || title.includes('coal')) return 'coal';
      if (title.includes('золото') || title.includes('золота') || title.includes('gold')) return 'gold';
      if (title.includes('серебр') || title.includes('silver')) return 'silver';
      if (title.includes('кристалл') || title.includes('сера')) return 'crystal';
      if (title.includes('обсидиан') || title.includes('мифрил') || title.includes('адамантит')) return 'obsidian';
      if (title.includes('редк') || title.includes('самоцвет')) return 'rare';
      if (title.includes('руд') || title.includes('жил')) return 'other_ore';

      // Проверка SVG разметки (mine-art.js: mrc- = chest, mrg- = rare/gem, mro- = ore, mrw- = wall)
      const html = (group.innerHTML || '').toLowerCase();
      if (html.includes('mrc-') || html.includes('chest') || html.includes('prize') || html.includes('сундук') || html.includes('клад')) return 'chest';
      if (html.includes('#7a1a3a') || html.includes('mrg-') || html.includes('rare') || html.includes('самоцвет')) return 'rare';
      if (html.includes('iron') || html.includes('железо')) return 'iron';
      if (html.includes('coal') || html.includes('уголь')) return 'coal';
      if (html.includes('gold') || html.includes('золото')) return 'gold';
      if (html.includes('silver') || html.includes('серебр')) return 'silver';
      if (html.includes('crystal') || html.includes('кристалл') || html.includes('sulfur') || html.includes('сера')) return 'crystal';
      if (html.includes('obsidian') || html.includes('мифрил') || html.includes('адамантит')) return 'obsidian';
      if (html.includes('mro-') || html.includes('руда') || html.includes('жила')) return 'other_ore';
    }

    return 'rock';
  }

  let isRatingMode = localStorage.getItem('lg_bot_rating_mode') === '1'; // Режим «Рейтинг» (толпа + добивание камней + железная фиксация)
  let ratingCrowdRange = localStorage.getItem('lg_bot_rating_crowd') || '2-5'; // Выбор количества игроков у цели в режиме «Рейтинг»
  let oreFirst = !isRatingMode && localStorage.getItem('lg_bot_ore_first') !== '0'; // По умолчанию true (выключено, если включен Режим «Рейтинг»)
  let coalIronFirst = localStorage.getItem('lg_bot_coal_iron_first') !== '0'; // По умолчанию true (включено: приоритет сперва уголь, затем железо)

  function parseRatingCrowdRange(rangeStr) {
    if (!rangeStr || rangeStr === 'any') return [1, 9999];
    if (rangeStr === '2-5') return [2, 5];
    if (rangeStr === '2-8') return [2, 8];
    if (rangeStr === '2-15') return [2, 15];
    if (rangeStr === '3-6') return [3, 6];
    if (rangeStr === '1-5') return [1, 5];
    const parts = String(rangeStr).split('-').map(n => parseInt(n, 10));
    if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
      return [parts[0], parts[1]];
    }
    return [2, 5];
  }
  // Очистка стиля скрытия боковой панели игры, если был установлен ранее
  try {
    document.getElementById('lg-style-hide-game-panel')?.remove();
    localStorage.removeItem('lg_bot_hide_game_panel');
  } catch (e) {}
  let timer = null;
  let refreshMinesTimer = null;
  const blacklist = new Map();
  let lastTargetKey = null;
  let targetAssignedTime = 0;
  let lastRockSwitchTime = 0;
  const STUCK_TIMEOUT = 12000;

  const basePath = G.LG_BASE || (location.pathname.match(/^\/c\/[^\/]+/)?.[0] || '');

  // Справочники ресурсов, грамматических форм и блоков шахты
  const RES_NAMES = {
    stone: 'Камень', basalt: 'Базальт', coal: 'Уголь', iron: 'Железо', silver: 'Серебро', gold: 'Золото',
    crystal: 'Кристаллы', sulfur: 'Сера', obsidian: 'Обсидиан', wood: 'Дерево', pine: 'Сосна',
    resin: 'Смола', moss: 'Мох', oak: 'Крепкий дуб', heartwood: 'Редкая древесина',
    spiritwood: 'Дерево духов', furs: 'Меха', cedar: 'Кедр', grain: 'Зерно', flax: 'Лён',
    wool: 'Шерсть', honey: 'Мёд', seeds: 'Редкие семена', golden_wheat: 'Золотая пшеница',
    herbs: 'Травы', blight_herbs: 'Гнилые травы', mushrooms: 'Грибы', peat: 'Торф',
    poison_root: 'Ядовитые корни', moonflower: 'Лунный цветок', witch_bloom: 'Ведьмин цвет',
    bone_dust: 'Костяная пыль', black_lotus: 'Чёрный лотос', fish: 'Рыба', ice_fish: 'Северная рыба',
    salt: 'Соль', shells: 'Раковины', amber: 'Янтарь', pearl: 'Жемчуг', black_pearl: 'Чёрный жемчуг',
    fish_oil: 'Рыбий жир', seal_hide: 'Тюленьи шкуры', walrus_tusk: 'Моржовый клык',
    ice_amber: 'Ледяной янтарь', clay: 'Глина', rubble: 'Щебень', glass: 'Стекло', relic: 'Древности',
    scarab: 'Скарабей', statue_shard: 'Осколок статуи', crown: 'Корона', copper: 'Медь',
    frost_stone: 'Мёрзлый камень', limestone: 'Известняк', sandstone: 'Песчаник',
    adamant: 'Адамантит', fire_opal: 'Огненный опал', emerald: 'Изумруд', sapphire: 'Сапфир',
    topaz: 'Топаз', malachite: 'Малахит', morion: 'Морион', aquamarine: 'Аквамарин',
    ruby: 'Рубин', runestone: 'Рунный камень'
  };

  const RESG_NAMES = {
    stone: 'камня', basalt: 'базальта', coal: 'угля', iron: 'железа', silver: 'серебра', gold: 'золота',
    crystal: 'кристаллов', sulfur: 'серы', obsidian: 'обсидиана', wood: 'дерева', pine: 'сосны',
    resin: 'смолы', moss: 'мха', oak: 'крепкого дуба', heartwood: 'редкой древесины',
    spiritwood: 'дерева духов', furs: 'мехов', cedar: 'кедра', grain: 'зерна', flax: 'льна',
    wool: 'шерсти', honey: 'мёда', seeds: 'редких семян', golden_wheat: 'золотой пшеницы',
    herbs: 'трав', blight_herbs: 'гнилых трав', mushrooms: 'грибов', peat: 'торфа',
    poison_root: 'ядовитых корней', moonflower: 'лунного цветка', witch_bloom: 'ведьмина цвета',
    bone_dust: 'костяной пыли', black_lotus: 'чёрного лотоса', fish: 'рыбы', ice_fish: 'северной рыбы',
    salt: 'соли', shells: 'раковин', amber: 'янтаря', pearl: 'жемчуга', black_pearl: 'чёрного жемчуга',
    fish_oil: 'рыбьего жира', seal_hide: 'тюленьих шкур', walrus_tusk: 'моржового клыка',
    ice_amber: 'ледяного янтаря', clay: 'глины', rubble: 'щебня', glass: 'стекла', relic: 'древностей',
    scarab: 'скарабея', statue_shard: 'осколков статуй', crown: 'короны', copper: 'меди',
    frost_stone: 'мёрзлого камня', limestone: 'известняка', sandstone: 'песчаника',
    adamant: 'адамантита', fire_opal: 'огненного опала', emerald: 'изумруда', sapphire: 'сапфира',
    topaz: 'топаза', malachite: 'малахита', morion: 'мориона', aquamarine: 'аквамарина',
    ruby: 'рубина', runestone: 'рунного камня'
  };

  const BLOCK_TYPE_NAMES = {
    rock: 'Камень',
    hard: 'Твёрдая порода',
    ore: 'Руда',
    rare: 'Редкая руда',
    chest: 'Сундук'
  };

  let cachedFloorCells = null;
  const cachedFloorCellMap = new Map();
  let cachedFloorN = null;
  let cachedFloorSeed = null;
  let lastDugString = null;
  let lastFloorN = null;
  let lastFloorSeed = null;
  let lastDg = {};
  let activeMiningRockIndex = -1;
  let lastMiningConfirmedTime = 0;
  let lastKnownMiningProgress = null;
  let idleHeroDetectedTime = 0;
  const recentDugNotifications = new Set();

  function getFloorCell(i) {
    if (i == null || i < 0) return null;
    // 1. Из живого объекта этажа движка игры S.F.cells
    if (G.__lgMineS && G.__lgMineS.F && G.__lgMineS.F.cells && G.__lgMineS.F.cells[i]) {
      const c = G.__lgMineS.F.cells[i];
      return Array.isArray(c) ? [c[0], c[1], c[2]] : [c.t, c.lv, c.r];
    }
    // 2. Из быстрого кэша карты клеток этажа
    if (cachedFloorCellMap.has(i)) {
      return cachedFloorCellMap.get(i);
    }
    // 3. Из перехваченного серверного состояния G.__lgMineState.fl.c
    if (G.__lgMineState && G.__lgMineState.fl && G.__lgMineState.fl.c) {
      const fl = G.__lgMineState.fl;
      if (Array.isArray(fl.c)) {
        if (fl.v3 || (fl.c[0] && Array.isArray(fl.c[0]) && typeof fl.c[0][0] === 'number')) {
          const item = fl.c.find(x => x && x[0] === i);
          if (item) {
            const res = [item[1], item[2], item[3]];
            cachedFloorCellMap.set(i, res);
            return res;
          }
        } else if (fl.c[i]) {
          const c = fl.c[i];
          const res = Array.isArray(c) ? [c[0], c[1], c[2]] : [c.t, c.lv, c.r];
          cachedFloorCellMap.set(i, res);
          return res;
        }
      }
    }
    // 4. Из резервного кэша этажа
    if (cachedFloorCells && Array.isArray(cachedFloorCells)) {
      if (cachedFloorCells[0] && Array.isArray(cachedFloorCells[0]) && typeof cachedFloorCells[0][0] === 'number') {
        const item = cachedFloorCells.find(x => x && x[0] === i);
        if (item) {
          const res = [item[1], item[2], item[3]];
          cachedFloorCellMap.set(i, res);
          return res;
        }
      } else if (cachedFloorCells[i]) {
        const c = cachedFloorCells[i];
        const res = Array.isArray(c) ? [c[0], c[1], c[2]] : [c.t, c.lv, c.r];
        cachedFloorCellMap.set(i, res);
        return res;
      }
    }
    return null;
  }

  function formatRockName(target) {
    let cell = null;
    if (typeof target === 'number' || typeof target === 'string') {
      const idx = parseInt(target, 10);
      if (!isNaN(idx) && idx >= 0) {
        cell = getFloorCell(idx);
      }
    } else if (Array.isArray(target)) {
      cell = target;
    }
    if (!cell || !Array.isArray(cell)) return '';

    const [t, lv, r] = cell;
    const lvText = lv ? ` ${lv} ур.` : '';

    if (t === 'chest' || t === 'prize') {
      return (t === 'prize' ? `Призовой сундук${lvText}` : `Сундук${lvText}`);
    }

    // В игре (mine.js blockTitle):
    // Ресурс (r) выводится ТОЛЬКО для руды (ore) и редкой жилы (rare)!
    // Для обычного камня (t === 'rock' или 'hard') выводится "Камень" или "Твёрдая порода",
    // даже если в данных биома r = 'limestone' (известняк), 'frost_stone' или 'sandstone'!
    if (t === 'rare') {
      const rTitle = r ? (RES_NAMES[r] || (String(r).charAt(0).toUpperCase() + String(r).slice(1))) : '';
      return `Редкая жила${lvText}${rTitle ? ' · ' + rTitle : ''}`;
    }

    if (t === 'ore') {
      const rTitle = r ? (RES_NAMES[r] || (String(r).charAt(0).toUpperCase() + String(r).slice(1))) : '';
      return `Руда${lvText}${rTitle ? ' · ' + rTitle : ''}`;
    }

    if (t === 'hard') {
      return `Твёрдая порода${lvText}`;
    }

    return `Камень${lvText}`;
  }

  function isValuableRock(rockId, group) {
    if (rockId == null) return false;
    const idx = typeof rockId === 'number' ? rockId : parseInt(rockId, 10);
    if (!isNaN(idx) && idx >= 0) {
      const cell = getFloorCell(idx);
      if (cell && Array.isArray(cell)) {
        const [t, lv, r] = cell;
        // Только настоящая руда, редкая жила или сундук считаются ценными!
        if (t === 'ore' || t === 'rare' || t === 'chest' || t === 'prize') return true;
        const resStr = r ? String(r).toLowerCase() : '';
        if (resStr === 'coal' || resStr === 'iron') return true;
        // Обычные камни и порода (rock / hard) — это НЕ руда, даже если материал r = limestone!
        if (t === 'rock' || t === 'hard' || t === 'wall' || t === 'entry' || t === 'void') return false;
      }
    }
    // Если клетки нет в кэше, проверяем SVG группы
    if (group) {
      const html = (group.innerHTML || '').toLowerCase();
      // Сундук / Клад (в mine-art.js это символ mrc-..., в Шахте 3.0 c.t 'chest' или 'prize')
      if (html.includes('mrc-') || html.includes('chest') || html.includes('prize') || html.includes('сундук') || html.includes('клад')) return true;
      // Табличка уровня: для редкой жилы в mine-art.js табличка залита '#7a1a3a'
      if (group.querySelector && group.querySelector('rect[fill="#7a1a3a"]')) return true;
      if (html.includes('#7a1a3a') || html.includes('mrg-') || html.includes('stroke="#ffd27a"') || html.includes('stroke-dasharray="4 10"') || html.includes('coal') || html.includes('iron') || html.includes('уголь') || html.includes('железо') || html.includes('mro-')) {
        return true;
      }
      if (group.classList && (group.classList.contains('ore') || group.classList.contains('rare') || group.classList.contains('chest') || group.classList.contains('prize') || group.classList.contains('coal') || group.classList.contains('iron'))) return true;
    }
    const name = formatRockName(rockId);
    if (name && (name.startsWith('Редкая жила') || name.startsWith('Руда') || name.includes('Сундук') || name.includes('Уголь') || name.includes('Железо'))) return true;
    return false;
  }

  // Определение ресурса руды: 'coal', 'iron', 'other_ore' или null
  function getRockOreKind(rockId, group) {
    if (rockId == null) return null;
    const idx = typeof rockId === 'number' ? rockId : parseInt(rockId, 10);
    if (!isNaN(idx) && idx >= 0) {
      const cell = getFloorCell(idx);
      if (cell && Array.isArray(cell)) {
        const [t, lv, r] = cell;
        const resStr = r ? String(r).toLowerCase() : '';
        if (resStr === 'coal') return 'coal';
        if (resStr === 'iron') return 'iron';
        if (t === 'ore' || t === 'rare' || t === 'chest') return 'other_ore';
      }
    }
    const name = formatRockName(rockId);
    if (name) {
      const lower = name.toLowerCase();
      if (lower.includes('уголь') || lower.includes('угля') || lower.includes('coal')) return 'coal';
      if (lower.includes('железо') || lower.includes('железа') || lower.includes('iron')) return 'iron';
      if (lower.includes('руда') || lower.includes('жила') || lower.includes('сундук')) return 'other_ore';
    }
    if (group) {
      const title = ((group.getAttribute && (group.getAttribute('title') || group.getAttribute('data-t'))) || '').toLowerCase();
      if (title.includes('уголь') || title.includes('угля') || title.includes('coal')) return 'coal';
      if (title.includes('железо') || title.includes('железа') || title.includes('iron')) return 'iron';

      const cls = ((group.getAttribute && group.getAttribute('class')) || '').toLowerCase();
      if (cls.includes('coal') || cls.includes('уголь')) return 'coal';
      if (cls.includes('iron') || cls.includes('железо')) return 'iron';

      const html = (group.innerHTML || '').toLowerCase();
      if (html.includes('coal') || html.includes('уголь')) return 'coal';
      if (html.includes('iron') || html.includes('железо')) return 'iron';
      if (isValuableRock(rockId, group)) return 'other_ore';
    }
    return null;
  }

  // Приоритеты: 3 = Уголь, 2 = Железо, 1 = Прочая руда/сундуки, 0 = Обычный камень
  function getRockPriorityTier(rockId, group) {
    if (!oreFirst) return 0;
    const cat = getRockCategory(rockId, group);
    const idx = customPriorities.findIndex(p => p.id === cat);
    if (idx < 0) return 0;
    const item = customPriorities[idx];
    if (!item.enabled) return -1; // Отключен пользователем
    return customPriorities.length - idx; // Чем выше в списке, тем выше число
  }

  // Детерминированное вычисление точного индекса клетки (для Шахты 2.0 и Шахты 3.0) по координатам полоски прогресса
  function getCellIndexFromBarPosition(bx, by) {
    const isV3 = !!(G.__lgMineS?.F?.v3 || G.__lgMineState?.fl?.v3);
    const m3info = G.__lgMineS?.F || G.__lgMineState?.fl;
    const getXY = (isV3 && G.LGWorldLib?.mine3 && m3info) ? G.LGWorldLib.mine3(m3info).cellXY :
                  ((G.LGCraftWorld && G.LGCraftWorld.cellXY) ||
                  (G.LGWorldLib && G.LGWorldLib.mine && G.LGWorldLib.mine.cellXY));
    const maxCells = (isV3 && m3info && m3info.w && m3info.h) ? (m3info.w * m3info.h) : 84;
    if (getXY) {
      let bestI = -1, bestDist = Infinity;
      for (let i = 0; i < maxCells; i++) {
        const c = getXY(i);
        if (!c) continue;
        const expectedLeft = c.x - 44;
        const expectedTop = c.y - 77;
        const d = Math.hypot(expectedLeft - bx, expectedTop - by);
        if (d < bestDist) {
          bestDist = d;
          bestI = i;
        }
      }
      if (bestDist < 60) return bestI;
    }
    // Аналитическая формула по сетке этажа (GW=7, GH=12, CW=128, RH=128, SIDE=64, Y0=420)
    const gx = Math.round((bx + 44 - 128) / 128);
    const gy = Math.round((by + 77 - 519.84) / 128);
    if (gx >= 0 && gx < 7 && gy >= 0 && gy < 12) {
      return gy * 7 + gx;
    }
    return -1;
  }

  // 🛡️ Проверка физической достижимости камня героем через игровой движок пути (A* / BFS pathTo)
  // Исключает выбор камней через непроходимые сталагмиты, сосульки или нераскопанные барьеры
  function isRockPathReachable(rockId) {
    try {
      const idx = typeof rockId === 'number' ? rockId : parseInt(rockId, 10);
      if (isNaN(idx) || idx < 0) return true;
      const S = G.__lgMineS;
      if (!S || !S.pos) return true;
      const dug = S.dug || G.__lgMineState?.dug;
      if (!dug) return true;
      const isV3 = !!(S.F?.v3 || G.__lgMineState?.fl?.v3);
      const m3info = S.F || G.__lgMineState?.fl;
      const MW = S.Wd || ((isV3 && G.LGWorldLib?.mine3 && m3info) ? G.LGWorldLib.mine3(m3info) : (G.LGWorldLib?.mine || G.LGCraftWorld));
      if (!MW || typeof MW.pathTo !== 'function' || typeof MW.digSpot !== 'function') return true;

      const from = (typeof MW.cellOfPt === 'function')
        ? MW.cellOfPt(S.pos.x, S.pos.y)
        : ((typeof MW.cellAt === 'function') ? MW.cellAt(S.pos.x, S.pos.y) : -1);
      if (from < 0) return true;

      const dugArr = (typeof dug === 'string')
        ? dug.split('').map(ch => ch === '1' ? 1 : 0)
        : dug;

      const sp = MW.digSpot(dugArr, idx);
      if (sp == null || sp < 0) return false;
      if (from === sp) return true;

      const p = MW.pathTo(dugArr, from, sp);
      return Boolean(p && p.length > 0);
    } catch (e) {
      return true;
    }
  }

  // Расчёт расстояния от текущей позиции героя до камня
  function getRockDistance(rockId) {
    try {
      const idx = typeof rockId === 'number' ? rockId : parseInt(rockId, 10);
      if (isNaN(idx) || idx < 0) return -1;
      const S = G.__lgMineS;
      if (!S || !S.pos) return -1;
      const isV3 = !!(S.F?.v3 || G.__lgMineState?.fl?.v3);
      const m3info = S.F || G.__lgMineState?.fl;
      const MW = S.Wd || ((isV3 && G.LGWorldLib?.mine3 && m3info) ? G.LGWorldLib.mine3(m3info) : (G.LGWorldLib?.mine || G.LGCraftWorld));
      if (!MW || typeof MW.cellXY !== 'function') return -1;
      const pt = MW.cellXY(idx);
      if (!pt) return -1;
      return Math.hypot(pt.cx - S.pos.x, pt.cy - S.pos.y);
    } catch (e) {
      return -1;
    }
  }

  function getCurrentMiningRockName(myCandidate) {
    // 1. Приоритет №1: Официальный текст игры из карточки «Сейчас: Копаешь: ...» в правом сайдбаре
    // В DOM это .m2-t b (например: «редкая жила 28 ур. · золото» или «камень 9 ур.»)
    const m2b = document.querySelector('.m2-t b')?.textContent?.trim();
    if (m2b) {
      return m2b.charAt(0).toUpperCase() + m2b.slice(1);
    }
    const statusSpan = document.querySelector('[data-mn="status"]')?.textContent?.trim();
    if (statusSpan && statusSpan.includes(':')) {
      const afterColon = statusSpan.split(':')[1];
      if (afterColon) {
        const title = afterColon.split('·')[0].trim();
        if (title) return title.charAt(0).toUpperCase() + title.slice(1);
      }
    }

    // 2. Движок игры S.myI (если перехвачен контекст шахты)
    if (G.__lgMineS && typeof G.__lgMineS.myI === 'number' && G.__lgMineS.myI >= 0) {
      const name = formatRockName(G.__lgMineS.myI);
      if (name) return name;
    }

    // 3. Проверяем кандидата игрока (.mn-bar.me)
    if (myCandidate && myCandidate.rockId != null) {
      const iNum = parseInt(myCandidate.rockId, 10);
      if (!isNaN(iNum) && iNum >= 0) {
        const name = formatRockName(iNum);
        if (name) return name;
      }
    }

    // 4. Проверяем активный запомненный индекс
    if (activeMiningRockIndex >= 0) {
      const name = formatRockName(activeMiningRockIndex);
      if (name) return name;
    }

    // 5. Проверяем st.dg (список копателей у камней)
    if (G.__lgMineState && G.__lgMineState.dg) {
      const myNick = (G.ME?.user?.login || G.__lgMineMe?.name || getMyNick() || '').toLowerCase();
      if (myNick) {
        for (const [idx, list] of Object.entries(G.__lgMineState.dg)) {
          if (Array.isArray(list) && list.some(l => String(l).toLowerCase() === myNick)) {
            const name = formatRockName(parseInt(idx, 10));
            if (name) return name;
          }
        }
      }
    }

    // 7. Проверяем последний кликнутый камень
    if (lastTargetKey != null) {
      const iNum = parseInt(lastTargetKey, 10);
      if (!isNaN(iNum) && iNum >= 0) {
        const name = formatRockName(iNum);
        if (name) return name;
      }
    }

    return '';
  }

  // Форматирование оставшегося времени добычи (ETA)
  function formatEtaTime(sec) {
    if (sec <= 0) return '0с';
    const s = Math.round(sec);
    const m = Math.floor(s / 60);
    const remS = s % 60;
    if (m === 0) return `~${remS}с`;
    if (m < 60) return `~${m}м ${remS.toString().padStart(2, '0')}с`;
    return `~${Math.floor(m / 60)}ч ${m % 60}м`;
  }

  function getDomMiningProgress(myCandidate) {
    const bar = myCandidate?.bar || document.querySelector('.mn-bar.me');
    const barI = bar?.querySelector('i') || document.querySelector('[data-mn-pr] i');
    const barLt = bar?.querySelector('em[data-left]') || document.querySelector('[data-mn-left][data-left]');

    if (barI) {
      let remPct = 100;
      let pct = 0;
      if (barI.dataset.rem) {
        const rem = parseFloat(barI.dataset.rem);
        remPct = Math.round(Math.max(0, Math.min(1, rem)) * 100);
        pct = 100 - remPct;
      } else if (barI.style.width) {
        const wVal = parseFloat(barI.style.width);
        if (!isNaN(wVal)) {
          remPct = Math.round(Math.max(0, Math.min(100, wVal)));
          pct = 100 - remPct;
        }
      }
      let etaSec = 0;
      if (barI.dataset.eta) {
        etaSec = parseFloat(barI.dataset.eta) || 0;
      } else if (barLt && barLt.dataset.left) {
        etaSec = Math.max(0, (+barLt.dataset.left - Date.now()) / 1000);
      }
      return {
        pct: pct || 0,
        remPct: remPct != null ? remPct : 100,
        etaSec,
        etaText: etaSec > 0 ? formatEtaTime(etaSec) : '—',
        leftHp: 0,
        totalHp: 0,
        rate: 0
      };
    }
    return null;
  }

  // Расчёт процента прогресса и оставшегося времени для камня
  function getRockMiningProgress(targetIndex, myCandidate) {
    let idx = -1;
    if (typeof targetIndex === 'number' && targetIndex >= 0) {
      idx = targetIndex;
    } else if (targetIndex != null) {
      const parsed = parseInt(targetIndex, 10);
      if (!isNaN(parsed) && parsed >= 0) idx = parsed;
    }

    if (idx < 0) {
      if (myCandidate && myCandidate.rockId != null) {
        const parsed = parseInt(myCandidate.rockId, 10);
        if (!isNaN(parsed) && parsed >= 0) idx = parsed;
      } else if (activeMiningRockIndex >= 0) {
        idx = activeMiningRockIndex;
      }
    }

    if (idx < 0) {
      const liveIdx = (typeof getActiveMiningRockIndex === 'function') ? getActiveMiningRockIndex() : -1;
      if (liveIdx >= 0) idx = liveIdx;
    }

    // 1. Из серверного состояния st.bl (точное оставшееся здоровье, скорость и макс. здоровье)
    const st = G.__lgMineState || (G.__lgMineS && G.__lgMineS.st);
    if (st && st.bl && idx >= 0 && st.bl[idx]) {
      const b = st.bl[idx];
      const [left, w, rate, n] = b;
      if (w > 0) {
        const now = Date.now();
        const stAt = st.at || now;
        const age = Math.max(0, (now - stAt) / 60000);
        let effRate = (rate && rate > 0) ? rate : 0;

        // Если в слепке rate ещё 0 (сервер ещё не успел пересчитать или снимок старый):
        if (effRate <= 0) {
          // Проверяем живые данные в DOM полоске
          const domProg = getDomMiningProgress(myCandidate);
          if (domProg) return domProg;

          // Оценочный урон по числу копателей (чтобы не застревать на 1% с черточкой)
          const diggerCount = Math.max(1, n || 1);
          effRate = diggerCount * 40;
          if (typeof refreshMineStateFromServer === 'function') {
            refreshMineStateFromServer();
          }
        }

        const l2 = Math.max(0, left - effRate * age);
        const p = Math.max(0, Math.min(1, 1 - l2 / w));
        const pct = Math.max(0, Math.min(100, Math.round(p * 100)));
        const remPct = Math.max(0, Math.min(100, Math.round((l2 / w) * 100)));
        const etaSec = effRate > 0 ? (l2 / effRate) * 60 : 0;
        return {
          pct,
          remPct,
          etaSec,
          etaText: etaSec > 0 ? formatEtaTime(etaSec) : '—',
          leftHp: Math.round(l2),
          totalHp: Math.round(w),
          rate: effRate
        };
      }
    }

    // 2. Запасной вариант: из DOM игры (.mn-bar.me или панель [data-mn-pr])
    return getDomMiningProgress(myCandidate);
  }

  // Расчет оставшегося процента здоровья камня для любого кандидата (для режима «Рейтинг»)
  function getCandidateHpProgress(candidate) {
    if (!candidate) return { remPct: 100, pct: 0, etaSec: 0, etaText: '—', leftHp: 0, totalHp: 0, rate: 0 };
    const idx = parseInt(candidate.rockId, 10);
    const st = G.__lgMineState || (G.__lgMineS && G.__lgMineS.st);
    if (st && st.bl && !isNaN(idx) && idx >= 0 && st.bl[idx]) {
      const b = st.bl[idx];
      const [left, w, rate, n] = b;
      if (w > 0) {
        const now = Date.now();
        const stAt = st.at || now;
        const age = Math.max(0, (now - stAt) / 60000);
        let effRate = (rate && rate > 0) ? rate : 0;
        if (effRate <= 0) {
          const diggerCount = Math.max(1, (candidate.count != null ? candidate.count : n) || 1);
          effRate = diggerCount * 40;
        }
        const l2 = Math.max(0, left - effRate * age);
        const remPct = Math.max(0, Math.min(100, Math.round((l2 / w) * 100)));
        const pct = Math.max(0, Math.min(100, 100 - remPct));
        const etaSec = effRate > 0 ? (l2 / effRate) * 60 : 0;
        return {
          pct,
          remPct,
          etaSec,
          etaText: etaSec > 0 ? formatEtaTime(etaSec) : '—',
          leftHp: Math.round(l2),
          totalHp: Math.round(w),
          rate: effRate
        };
      }
    }

    if (candidate.bar) {
      const barI = candidate.bar.querySelector('i');
      if (barI) {
        let remPct = 100;
        if (barI.dataset.rem) {
          const rem = parseFloat(barI.dataset.rem);
          remPct = Math.round(Math.max(0, Math.min(1, rem)) * 100);
        } else if (barI.style.width) {
          const wVal = parseFloat(barI.style.width);
          if (!isNaN(wVal)) {
            remPct = Math.round(Math.max(0, Math.min(100, wVal)));
          }
        }
        const etaSec = parseFloat(barI.dataset.eta) || 0;
        return {
          pct: 100 - remPct,
          remPct,
          etaSec,
          etaText: etaSec > 0 ? formatEtaTime(etaSec) : '—',
          leftHp: 0,
          totalHp: 0,
          rate: 0
        };
      }
    }

    return { remPct: 100, pct: 0, etaSec: 0, etaText: '—', leftHp: 0, totalHp: 0, rate: 0 };
  }

  // Всплывающий анимированный текст при выкапывании руды / камня
  function showFloatingDugToast(text, subText = '') {
    try {
      let container = document.getElementById('lg-mine-floating-container');
      if (!container) {
        container = document.createElement('div');
        container.id = 'lg-mine-floating-container';
        container.style.cssText = `
          position: fixed;
          top: 15%;
          left: 50%;
          transform: translateX(-50%);
          z-index: 99999999;
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 10px;
          pointer-events: none;
        `;
        document.body.appendChild(container);
      }

      const item = document.createElement('div');
      item.style.cssText = `
        background: linear-gradient(135deg, rgba(16, 12, 38, 0.96) 0%, rgba(30, 22, 60, 0.96) 100%);
        border: 1.5px solid #00ff66;
        border-radius: 12px;
        box-shadow: 0 8px 32px rgba(0, 255, 102, 0.35), 0 0 16px rgba(0, 255, 102, 0.2);
        color: #ffffff;
        padding: 10px 22px;
        pointer-events: none;
        user-select: none;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 15px;
        font-weight: bold;
        text-align: center;
        letter-spacing: 0.3px;
        opacity: 0;
        transform: translateY(20px) scale(0.9);
        transition: all 0.35s cubic-bezier(0.18, 0.89, 0.32, 1.28);
      `;

      item.innerHTML = `
        <div style="display:flex;align-items:center;gap:8px;justify-content:center;">
          <span style="font-size:18px;">⛏️</span>
          <span style="color:#00ff66;font-size:16px;">${text}</span>
        </div>
        ${subText ? `<div style="font-size:13px;color:#ffd700;margin-top:3px;font-weight:600;">${subText}</div>` : ''}
      `;

      container.appendChild(item);

      // Анимация появления
      requestAnimationFrame(() => {
        item.style.opacity = '1';
        item.style.transform = 'translateY(0) scale(1)';
      });

      // Анимация всплытия наверх и исчезновения
      setTimeout(() => {
        item.style.transition = 'all 0.6s ease';
        item.style.opacity = '0';
        item.style.transform = 'translateY(-30px) scale(0.95)';
        setTimeout(() => {
          item.remove();
          if (container && container.children.length === 0) {
            container.remove();
          }
        }, 650);
      }, 2800);

      // Также отправляем в системный toast игры
      showToast(text + (subText ? ` (${subText})` : ''));
    } catch (e) {
    }
  }

  // Отложенное обновление руды через 500 мс (полсекунды) после выкопки руды
  let oreRefreshTimer = null;
  function scheduleOreRefreshAfterDig(delayMs = 500) {
    if (oreRefreshTimer) clearTimeout(oreRefreshTimer);
    oreRefreshTimer = setTimeout(async () => {
      oreRefreshTimer = null;
      try {
        if (typeof gameApi === 'function') {
          const res = await gameApi('/api/me');
          if (res && res.hero) {
            G.__lgHero = res.hero;
            if (G.ME) G.ME.hero = res.hero;
            if (typeof window !== 'undefined' && window.ME) window.ME.hero = res.hero;
          }
        }
        if (typeof refreshMineStateFromServer === 'function') {
          await refreshMineStateFromServer(true);
        }
      } catch (err) {}
      if (typeof updateStatsUI === 'function') {
        updateStatsUI();
      }
    }, delayMs);
  }

  // Обработка обновлений состояния шахты для обнаружения выкопанной руды
  function handleStateUpdate(st) {
    if (!st) return;

    if (st.fl && st.fl.c) {
      cachedFloorCells = st.fl.c;
      cachedFloorN = st.n;
      cachedFloorSeed = st.seed;
      cachedFloorCellMap.clear();
      if (Array.isArray(st.fl.c)) {
        const isV3 = !!(st.fl.v3 || (st.fl.c[0] && Array.isArray(st.fl.c[0]) && typeof st.fl.c[0][0] === 'number' && typeof st.fl.c[0][1] === 'string'));
        if (isV3) {
          for (const item of st.fl.c) {
            if (item && typeof item[0] === 'number') {
              cachedFloorCellMap.set(item[0], [item[1], item[2], item[3]]);
            }
          }
        } else {
          for (let idx = 0; idx < st.fl.c.length; idx++) {
            const item = st.fl.c[idx];
            if (item) {
              cachedFloorCellMap.set(idx, Array.isArray(item) ? [item[0], item[1], item[2]] : [item.t, item.lv, item.r]);
            }
          }
        }
      }
    }

    const myLogin = (G.ME?.user?.login || G.__lgMineMe?.name || getMyNick() || '').toLowerCase();
    const isSameFloor = (lastFloorN === st.n && lastFloorSeed === st.seed);

    if (isSameFloor && lastDugString && st.dug) {
      const newlyDugIndices = [];
      for (let i = 0; i < st.dug.length; i++) {
        if (st.dug[i] === '1' && lastDugString[i] === '0') {
          newlyDugIndices.push(i);
        }
      }

      for (const i of newlyDugIndices) {
        const notifKey = `${st.n}_${i}`;
        if (recentDugNotifications.has(notifKey)) continue;

        const wasMyTarget = (activeMiningRockIndex === i || lastTargetKey == i);
        const wasInDg = lastDg && Array.isArray(lastDg[i]) && lastDg[i].some(l => String(l).toLowerCase() === myLogin);
        const dlEntry = (st.dl || []).find(x => x[0] === i && x[1] === st.n);
        const myShards = dlEntry && (dlEntry[4] || {})[myLogin];
        const myPieces = dlEntry && (dlEntry[3] || {})[myLogin];
        const myLoot = dlEntry && (dlEntry[5] || {})[myLogin];
        const gotDrop = (myShards > 0 || myPieces > 0 || !!myLoot);

        if (wasMyTarget || wasInDg || gotDrop) {
          recentDugNotifications.add(notifKey);
          setTimeout(() => recentDugNotifications.delete(notifKey), 15000);

          const cell = getFloorCell(i);
          const rockTitle = formatRockName(cell || i) || 'Камень';

          let dropText = '';
          if (dlEntry) {
            const rKey = dlEntry[2] || (cell && cell[2]);
            const rName = rKey ? (RES_NAMES[rKey] || rKey) : '';
            const rGen = rKey ? (RESG_NAMES[rKey] || rName) : '';

            if (myShards != null && myShards > 0) {
              const n = Math.floor(myShards / 100);
              const p = myShards % 100;
              if (n > 0 && p > 0) {
                dropText = `+${n} ${rName} и ${p} осколков`;
              } else if (n > 0) {
                dropText = `+${n} ${rName}`;
              } else {
                dropText = `+${p} осколков ${rGen}`;
              }
            } else if (myPieces != null && myPieces > 0) {
              dropText = `+${myPieces} ${rName}`;
            } else if (myLoot) {
              dropText = 'Добыча из сундука!';
            }
          }

          showFloatingDugToast(`Выкопали: ${rockTitle}`, dropText);

          if (activeMiningRockIndex === i) {
            activeMiningRockIndex = -1;
            lastMiningConfirmedTime = 0;
            lastKnownMiningProgress = null;
          }

          // Обновление руды через 0.5 сек (полсекунды) после выкопки руды
          scheduleOreRefreshAfterDig(500);

          // Мгновенный переход к следующей цели без ожидания 1.8с таймера (исключает отставание в фоне)!
          if (G.__mineBotRunning && !isPaused) {
            clearTimeout(timer);
            timer = setTimeout(step, 40);
          }
        }
      }
    } else if (!isSameFloor && G.__mineBotRunning && !isPaused) {
      // Смена этажа: моментально начинаем поиск первой цели на новом этаже
      activeMiningRockIndex = -1;
      lastTargetKey = null;
      targetAssignedTime = 0;
      lastMiningConfirmedTime = 0;
      lastKnownMiningProgress = null;
      lastFloorTransitionTime = Date.now();
      pendingLiftRide = null;
      if (G.__lgMineS) {
        G.__lgMineS.left = null;
      }
      // 🛡️ Автоматическая синхронизация currentMineId с новым этажом шахты
      const baseId = String(currentMineId || 'home').split('.')[0];
      const deep = st.deep || st.n || 1;
      currentMineId = (st.n >= deep) ? baseId : `${baseId}.${st.n}`;
      G.__lgMineCurrent = currentMineId;
      try { updateMineSelectorUI(); } catch (e) {}
      clearTimeout(timer);
      timer = setTimeout(step, 200);
    }

    lastDugString = st.dug || '';
    lastFloorN = st.n;
    lastFloorSeed = st.seed;
    lastDg = st.dg ? JSON.parse(JSON.stringify(st.dg)) : {};
  }
  G.__mineHandleStateUpdate = handleStateUpdate;

  // Перехват сетевых ответов шахты для 100% точного подсчёта игроков и статистики героя
  if (!G.__mineFetchHooked) {
    G.__mineFetchHooked = true;
    const origFetch = G.fetch;
    G.fetch = async function (...args) {
      // 🛡️ Стелс-защита: перехват и полная очистка телеметрии tl в запросах mineDig (Шахта 29.10 / 30.10)
      try {
        const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
        if (url.includes('/api/mineDig') && args[1] && args[1].body) {
          if (typeof args[1].body === 'string') {
            try {
              const bodyObj = JSON.parse(args[1].body);
              let modified = false;
              // Полная очистка анти-бот флагов телеметрии Шахты (tl: { pi, ut, rv, hp, gm })
              if (bodyObj.tl) {
                if (bodyObj.tl.pi !== undefined) { delete bodyObj.tl.pi; modified = true; }
                if (bodyObj.tl.ut !== undefined) { delete bodyObj.tl.ut; modified = true; }
                if (bodyObj.tl.rv !== undefined) { delete bodyObj.tl.rv; modified = true; }
                if (bodyObj.tl.hp !== undefined) { delete bodyObj.tl.hp; modified = true; }
                if (bodyObj.tl.gm !== undefined) { delete bodyObj.tl.gm; modified = true; }
                // Гарантируем 1-2 естественных клика живого человека
                const humanTi = Math.floor(Math.random() * 2) + 1;
                if (!bodyObj.tl.ti || bodyObj.tl.ti < 1) {
                  bodyObj.tl.ti = humanTi;
                  modified = true;
                }
              } else {
                bodyObj.tl = { ti: Math.floor(Math.random() * 2) + 1 };
                modified = true;
              }
              // Очищаем устаревшие поля 26.10, если они присутствуют
              if (bodyObj.vh !== undefined) { delete bodyObj.vh; modified = true; }
              if (bodyObj.k !== undefined) { delete bodyObj.k; modified = true; }
              if (modified) {
                args[1] = { ...args[1], body: JSON.stringify(bodyObj) };
              }
            } catch (err) {}
          }
        }
      } catch (e) {}

      const res = await origFetch.apply(this, args);
      try {
        const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
        if (url.includes('/api/me') || url.includes('/api/st') || url.includes('/api/mineOreBag') || url.includes('/api/mineSell') || url.includes('/api/equip') || url.includes('/api/unequip') || url.includes('/api/craft')) {
          const origJson = res.json.bind(res);
          res.json = async function () {
            const data = await origJson();
            try {
              if (data && data.hero) {
                const qaEnabled = localStorage.getItem('lg_quest_qamode') !== '0';
                if (qaEnabled) {
                  data.hero.qa = true;
                  data.hero.qt = true;
                }
                G.ME = data;
                G.__lgHero = data.hero;
                if (data.hero.role) {
                  try { localStorage.setItem('lg_hero_role', data.hero.role); } catch (e) {}
                }
                if (typeof refreshQuestUI === 'function') refreshQuestUI();
                if (typeof updateStatsUI === 'function') updateStatsUI();
              }
            } catch (e) {}
            return data;
          };
        }
        if (url.includes('/api/mine/state') || url.includes('/api/mine/enter') || url.includes('/api/minestop') || url.includes('/api/mineDig') || url.includes('/api/mineOreBag')) {
          const clone = res.clone();
          clone.json().then(data => {
            if (data && data.st) {
              if (typeof G.__mineHandleStateUpdate === 'function') {
                G.__mineHandleStateUpdate(data.st);
              }
              G.__lgMineState = data.st;
              G.__lgMineError = null;
              // 🛡️ Защита от follow3: безусловно выравниваем snapshot me.at к текущему этажу data.st.n,
              // а также обновляем S.rideAt, чтобы движок игры не откатывал шахту на старый этаж
              if (data.st.n && data.me) {
                data.me.at = data.st.n;
              }
              if (G.__lgMineS) {
                if (data.st.n && G.__lgMineS.me) {
                  G.__lgMineS.me.at = data.st.n;
                }
                G.__lgMineS.rideAt = Date.now();
                if (G.__mineBotRunning && !isPaused) {
                  G.__lgMineS.newsShown = true;
                }
              }
            }
            if (data && data.me) {
              G.__lgMineMe = data.me;
            }
            if (data && data.ok === false) {
              G.__lgMineError = data.error || 'error';
              if (data.error && typeof data.error === 'string') {
                const errLower = data.error.toLowerCase();
                if (errLower.includes('хватит') || errLower.includes('лимит') || errLower.includes('cap')) {
                  cachedDailyMiningCapReached = true;
                  cachedDailyMiningCapDay = getMineGamingDay();
                  try { localStorage.setItem('lg_mining_cap_day', cachedDailyMiningCapDay); } catch (e) {}
                }
              }
            }
            if (typeof updateStatsUI === 'function') updateStatsUI();
          }).catch(() => {});
        }
      } catch (e) {}
      return res;
    };
    makeStealthNative(G.fetch, 'fetch');
    if (window.fetch && window.fetch !== G.fetch) {
      makeStealthNative(window.fetch, 'fetch');
    }
  }

  // Перехват контекста Города, Аукциона и Похода для прямого вызова официального api() и UI игры
  if (!G.__lgHookedProps) {
    G.__lgHookedProps = true;
    let rawLGCity = G.LGCity;
    let rawLGAuction = G.LGAuction;
    let rawLGQuest = G.LGQuest;
    const hookCity = (val) => {
      rawLGCity = val;
      if (val && !val.__hooked && typeof val.open === 'function') {
        val.__hooked = true;
        const orig = val.open;
        val.open = function (r, cx) {
          if (cx) G.__lgCityContext = cx;
          return orig.apply(this, arguments);
        };
        // Живой перехват событий города (SSE) для мгновенного отслеживания присутствия игроков в городе
        if (typeof val.onEvent === 'function') {
          const origOnEvent = val.onEvent;
          val.onEvent = function (d) {
            try {
              if (d) {
                if (!G.__lgLiveCityPlayers) G.__lgLiveCityPlayers = new Set();
                if (Array.isArray(d.a)) {
                  d.a.forEach(e => { if (e && e.l) G.__lgLiveCityPlayers.add(String(e.l).trim().toLowerCase()); });
                }
                if (Array.isArray(d.g)) {
                  d.g.forEach(l => { if (l) G.__lgLiveCityPlayers.delete(String(l).trim().toLowerCase()); });
                }
                if (Array.isArray(d.m)) {
                  d.m.forEach(m => { if (m && m[0]) G.__lgLiveCityPlayers.add(String(m[0]).trim().toLowerCase()); });
                }
              }
            } catch (err) {}
            return origOnEvent.apply(this, arguments);
          };
        }
      }
    };
    const hookAuc = (val) => {
      rawLGAuction = val;
      if (val && !val.__hooked && typeof val.render === 'function') {
        val.__hooked = true;
        const orig = val.render;
        val.render = function (el, cx, o) {
          if (cx) G.__lgAucContext = cx;
          return orig.apply(this, arguments);
        };
      }
    };
    const hookQuest = (val) => {
      rawLGQuest = val;
      if (val && !val.__hooked) {
        val.__hooked = true;
        // 1. Перехват init(ctx) -> получение нативного QX (act, say, vote, hero)
        if (typeof val.init === 'function') {
          const origInit = val.init;
          val.init = function (ctx) {
            if (ctx) G.__lgQuestContext = ctx;
            return origInit.apply(this, arguments);
          };
        }
        // 2. Перехват open(el, qx) если поддерживается
        if (typeof val.open === 'function') {
          const origOpen = val.open;
          val.open = function (el, qx) {
            if (qx) G.__lgQuestContext = qx;
            return origOpen.apply(this, arguments);
          };
        }
        // 3. Перехват data(q) -> Stream Fast-Forward (dl: 0) и авто-обработка событий похода
        if (typeof val.data === 'function') {
          const origData = val.data;
          val.data = function (q) {
            try {
              if (q && typeof q === 'object') {
                if (typeof questSettings !== 'undefined' && questSettings.fastForward) {
                  q.dl = 0;
                }
                if (typeof onQuestDataReceived === 'function') {
                  onQuestDataReceived(q);
                }
              }
            } catch (e) {
            }
            return origData.apply(this, arguments);
          };
        }
      }
    };
    G.__lgHookQuest = hookQuest;
    if (rawLGCity) hookCity(rawLGCity);
    if (rawLGAuction) hookAuc(rawLGAuction);
    if (rawLGQuest) hookQuest(rawLGQuest);
    try {
      Object.defineProperty(G, 'LGCity', {
        configurable: true,
        enumerable: true,
        get: () => rawLGCity,
        set: (v) => hookCity(v)
      });
      Object.defineProperty(G, 'LGAuction', {
        configurable: true,
        enumerable: true,
        get: () => rawLGAuction,
        set: (v) => hookAuc(v)
      });
      Object.defineProperty(G, 'LGQuest', {
        configurable: true,
        enumerable: true,
        get: () => rawLGQuest,
        set: (v) => hookQuest(v)
      });
    } catch (e) {}
  }

  // Хранилище доступных шахт и сессии (Гарантируем Домашнюю и Рябиненень + тестовые округа)
  let currentMineId = localStorage.getItem('lg_bot_mine') || 'home';
  let lastMineOpenAttempt = 0;
  let lastFloorTransitionTime = 0;
  const NEIGHBOR_MINES = [
    { id: 'camp', name: '⛺ Перепутье (Лагерь)', danger: 1, test: true },
    { id: 'd04', name: 'Рябиново (Склоны меча)', danger: 1, test: true },
    { id: 'd02', name: 'Ключино (Дубрава)', danger: 1, test: true },
    { id: 'd06', name: 'Краеград (Столица)', danger: 2, test: true },
    { id: 'tower', name: 'Чёрная башня', danger: 1, test: true },
    { id: 'tree', name: 'Мохец (Тайга · Сапфир)', danger: 1, test: true },
    { id: 'd16', name: 'Инейград (Лёд · Топаз)', danger: 2, test: true },
    { id: 'volcano', name: 'Пеплск (Вулкан · Опал)', danger: 4, test: true }
  ];
  let availableMines = [
    { id: 'home', name: 'Домашняя', danger: 1 },
    { id: 'ryabinen', name: 'Рябиненень', danger: 1 },
    ...NEIGHBOR_MINES
  ];
  let currentSid = null;

  // Универсальный вызов игрового API (парсит ответ при любом HTTP-статусе, не теряя ошибки сервера)
  async function gameApi(path, options = {}) {
    const p = path.startsWith('/') ? path : '/' + path;

    // 1. Приоритет: Нативный api-контекст игры (app.js / city.js / mine.js / auction.js / quest.js)
    const nativeCtx = G.__lgAucContext || G.__lgCityContext || G.__lgMineContext || G.__lgQuestContext;
    if (nativeCtx && typeof nativeCtx.api === 'function' && options.body && options.method === 'POST') {
      try {
        const bodyObj = typeof options.body === 'string' ? JSON.parse(options.body) : options.body;
        const res = await nativeCtx.api(p, bodyObj);
        if (res && typeof res === 'object') return res;
      } catch (e) {
      }
    }

    // 2. Вызов через fetch с credentials и детальной обработкой ответов (JSON и текст)
    const fetchOptions = {
      credentials: 'same-origin',
      ...options,
      headers: {
        ...(options.headers || {})
      }
    };

    async function doFetch(url) {
      const res = await fetch(url, fetchOptions);
      let text = '';
      try {
        text = await res.text();
      } catch (e) {}

      let data = null;
      if (text) {
        try {
          data = JSON.parse(text);
        } catch (e) {}
      }

      if (data && typeof data === 'object') {
        return data;
      }

      // Если 304 Not Modified — данные на сервере не менялись (это нормальный статус кэша)
      if (res.status === 304) {
        return { ok: true, notModified: true, status: 304 };
      }

      // Если ответ не JSON, но сервер вернул статус ошибки (например, 400, 404, 500)
      if (res.status >= 400) {
        const cleanMsg = (text && !text.includes('<!DOCTYPE') && !text.includes('<html'))
          ? text.trim().slice(0, 50)
          : `Ошибка сервера (${res.status})`;
        return { ok: false, error: cleanMsg, status: res.status };
      }

      // Если 200 OK, но не JSON
      if (res.ok && text && !text.includes('<!DOCTYPE')) {
        return { ok: true, text: text.trim() };
      }

      return null;
    }

    try {
      const resData = await doFetch(p);
      if (resData) return resData;
    } catch (e) {
    }

    if (basePath && !p.startsWith(basePath) && !p.startsWith('/api')) {
      try {
        const resData = await doFetch(basePath + p);
        if (resData) return resData;
      } catch (e) {
      }
    }

    return { ok: false, error: 'Сбой сети' };
  }

  // Автономное периодическое обновление состояния шахты с сервера (гарантирует живой прогресс)
  let lastMineStatePoll = 0;
  let isMineStatePolling = false;
  async function refreshMineStateFromServer(force = false) {
    const now = Date.now();
    if (!force && now - lastMineStatePoll < 7000) return;
    if (isMineStatePolling) return;
    lastMineStatePoll = now;
    isMineStatePolling = true;
    try {
      const c = currentMineId || 'home';
      const res = await gameApi(`/api/mine/state?c=${encodeURIComponent(c)}`);
      if (res && res.st) {
        G.__lgMineState = res.st;
        G.__lgMineError = null;
        if (typeof G.__mineHandleStateUpdate === 'function') {
          G.__mineHandleStateUpdate(res.st);
        }
      }
      if (res && res.me) {
        G.__lgMineMe = res.me;
      }
    } catch (e) {
    } finally {
      isMineStatePolling = false;
    }
  }

  // Безопасный показ всплывающих уведомлений игры (через DOM #toast)
  function showToast(msg, isBad = false) {
    try {
      const e = document.getElementById('toast');
      if (e) {
        e.textContent = msg;
        e.className = 'toast on' + (isBad ? ' err' : '');
        clearTimeout(e._t);
        e._t = setTimeout(() => { e.className = 'toast'; }, 2800);
        return;
      }
      if (typeof G.toast === 'function') {
        G.toast(msg, isBad);
      }
    } catch (err) {}
  }

  // Резервный контекст для прямого запуска LGMine.open в любой ситуации
  // Включает полную совместимость с обновлением 25.10 / 26.10 (fig, art, doll, cardTip)
  function getMineContext() {
    if (G.__lgMineContext) return G.__lgMineContext;
    return {
      __isBotFallback: true,
      login: () => (G.ME && G.ME.user ? String(G.ME.user.login).toLowerCase() : ''),
      hero: () => (G.ME && G.ME.hero) || null,
      doodle: (l, h, mood, k) => (G.doodle ? G.doodle(l, h, mood, k) : ''),
      fig: (l, v, o) => (G.figSvg ? G.figSvg(l, v, o) : (G.doodle ? G.doodle(l, v) : '')),
      heroLook: (h) => (G.heroLook ? G.heroLook(h) : ''),
      api: (p, b) => gameApi(p, b ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) } : {}),
      money: (n) => (G.money ? G.money(n) : String(n)),
      toast: (t, bad) => showToast(t, bad),
      live: () => true,
      close: () => {},
      mines: () => availableMines,
      art: (it) => (G.art ? G.art(it) : ''),
      doll: (h, o) => (G.dollHtml ? G.dollHtml(h, o) : ''),
      cardTip: (el, it, h, o) => (G.cardTip ? G.cardTip(el, it, h, o) : null),
      tipHide: () => (G.tipHide ? G.tipHide() : null),
      equip: async (uid) => gameApi('/api/equip', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid }) }),
      unequip: async (slot) => gameApi('/api/unequip', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ slot }) })
    };
  }

  // Координаты клетки для валидации сервером (поддержка Шахты 2.0 и Шахты 3.0)
  function getRockCoords(rockIndex) {
    const isV3 = !!(G.__lgMineS?.F?.v3 || G.__lgMineState?.fl?.v3);
    const m3info = G.__lgMineS?.F || G.__lgMineState?.fl;
    if (isV3 && G.LGWorldLib?.mine3 && m3info) {
      try {
        const mw = G.LGWorldLib.mine3(m3info);
        const p = mw.cellXY ? mw.cellXY(rockIndex) : null;
        if (p) return { x: Math.round(p.x), y: Math.round(p.y) };
      } catch (e) {}
    }
    if (G.LGCraftWorld && G.LGCraftWorld.cellXY) {
      try {
        const p = G.LGCraftWorld.cellXY(rockIndex);
        if (p) return { x: Math.round(p.x + 44), y: Math.min(1150, Math.round(p.y + 100)) };
      } catch (e) {}
    }
    const GW = 7;
    const gx = rockIndex % GW, gy = Math.floor(rockIndex / GW);
    return {
      x: Math.round(86 + gx * 88),
      y: Math.min(1150, Math.round(100 + (gy + 1) * 88))
    };
  }

  // Перехват контекста LGMine.open для прямого перехода в любую шахту на экране
  // Защита состояния шахты от багов движка игры (mine.js: panelStatus reading 'cells' of null)
  function protectMineState(S) {
    if (!S || S.__protectedF) return;
    S.__protectedF = true;
    let _F = S.F;
    Object.defineProperty(S, 'F', {
      get() {
        if (!_F) {
          if (S.st && S.st.fl) {
            const fl = S.st.fl;
            if (fl.v3 && window.LGMine3 && typeof window.LGMine3.floorOf === 'function') {
              try {
                const resF = window.LGMine3.floorOf(S.st);
                if (resF) { _F = resF; return _F; }
              } catch (e) {}
            }
            const N = (fl.w && fl.h) ? (fl.w * fl.h) : 84;
            const cells = new Array(N);
            for (let i = 0; i < N; i++) cells[i] = { t: 'fog', k: 0, lv: 0, r: null, a: 0 };
            for (const c of fl.c || []) {
              if (Array.isArray(c)) {
                const [i, t, lv, r, a] = c;
                if (i >= 0 && i < N) cells[i] = { t: t || 'rock', lv: lv || 1, r: r || null, a: a || 0, k: 1 };
              }
            }
            return { cells, v3: !!fl.v3, n: S.st.n || 1 };
          }
          const dummyCells = new Array(200);
          for (let i = 0; i < 200; i++) dummyCells[i] = { t: 'rock', lv: 1, r: null, a: 0, k: 1 };
          return { cells: dummyCells, v3: false };
        }
        return _F;
      },
      set(val) {
        _F = val;
      },
      configurable: true,
      enumerable: true
    });
  }

  // Предзагрузка mine3.js для исключения задержки загрузки этажа 3.0
  try {
    if (!window.LGMine3) {
      const m3Sc = document.createElement('script');
      m3Sc.src = '/site/mine3.js';
      m3Sc.async = true;
      (document.head || document.documentElement).appendChild(m3Sc);
    }
  } catch (e) {}

  function hookMineOpen() {
    if (G.LGMine && !G.LGMine.__hooked) {
      G.LGMine.__hooked = true;
      let origOpen = G.LGMine.open;
      let origOnEvent = G.LGMine.onEvent;

      function botOpen(x, c) {
        if (x && !x.__isBotFallback) {
          G.__lgMineContext = x;
          if (typeof x.api === 'function' && !x.__botApiWrapped) {
            x.__botApiWrapped = true;
            const origCtxApi = x.api;
            x.api = async function (endpoint, params) {
              const res = await origCtxApi.apply(this, arguments);
              try {
                if (res && typeof endpoint === 'string') {
                  const S = G.__lgMineS;
                  // Перехват входа в шахту и опроса состояния:
                  if (endpoint.includes('/api/mine/enter') || endpoint.includes('/api/mine/state') || endpoint.includes('/api/mine/poll')) {
                    const curFloor = res.st?.n || (res.st && (res.st.deep || res.st.n)) || S?.st?.n;
                    if (curFloor) {
                      if (res.me) res.me.at = curFloor;
                      if (S?.me) S.me.at = curFloor;
                      if (G.__lgMineMe) G.__lgMineMe.at = curFloor;
                    }
                    if (S) {
                      if (G.__mineBotRunning && !isPaused) {
                        S.newsShown = true;
                      }
                    }
                  }
                  // Перехват успешной поездки лифта
                  if (endpoint.includes('/api/mineRide') && res.ok) {
                    if (S) {
                      S.rideAt = Date.now();
                      if (params && params.n) {
                        if (S.me) S.me.at = params.n;
                        if (G.__lgMineMe) G.__lgMineMe.at = params.n;
                      }
                    }
                  }
                }
              } catch (e) {}
              return res;
            };
            makeStealthNative(x.api, 'api');
          }
        }
        const openMineId = c || 'home';
        G.__lgMineCurrent = openMineId;
        // Если шахта открыта игрой вручную (а не ботом), синхронизируем текущий выбор
        if (c && c !== currentMineId && !x?.__isBotFallback) {
          currentMineId = c;
          currentSid = null;
          updateMineSelectorUI();
        }
        const res = origOpen ? origOpen.apply(this, arguments) : undefined;
        if (G.__lgMineS) {
          G.__lgMineS.rideAt = Date.now();
          if (G.__mineBotRunning && !isPaused) G.__lgMineS.newsShown = true;
          protectMineState(G.__lgMineS);
        }
        if (mode === 'ghost') {
          setGhostVisuals(true);
          ensureCityVisible(false);
        }
        return res;
      }
      makeStealthNative(botOpen, 'open');

      function botOnEvent(d) {
        if (d) {
          if (d.st) {
            G.__lgMineState = d.st;
            G.__lgMineError = null;
            if (!isPaused && isDrillBuild && d.st.drills && !isRidingLift) {
              processDrillAutoFund();
            }
            if (typeof G.__mineHandleStateUpdate === 'function') {
              try { G.__mineHandleStateUpdate(d.st); } catch (e) {}
            }
          }
          if (d.me) G.__lgMineMe = d.me;
          // 🛡️ Защита от follow3: безусловно выравниваем me.at к curN
          const S = G.__lgMineS;
          const curN = d.st?.n || S?.st?.n;
          if (curN) {
            if (S?.me) S.me.at = curN;
            if (d.me) d.me.at = curN;
            if (G.__lgMineMe) G.__lgMineMe.at = curN;
          }
          if (S) {
            S.rideAt = Date.now();
            if (G.__mineBotRunning && !isPaused) S.newsShown = true;
          }
        }
        return origOnEvent ? origOnEvent.apply(this, arguments) : undefined;
      }
      makeStealthNative(botOnEvent, 'onEvent');

      // 🛡️ Стелс-геттер LGMine.open: при проверке tlPi() / tele() мгновенно отдаёт оригинальную нативную ссылку
      try {
        Object.defineProperty(G.LGMine, 'open', {
          get() {
            const stack = new Error().stack || '';
            if (stack.includes('tlPi') || stack.includes('tele') || stack.includes('bwOf') || stack.includes('mine.js')) {
              return origOpen;
            }
            return botOpen;
          },
          set(v) { origOpen = v; },
          configurable: true,
          enumerable: true
        });
      } catch (e) {
        G.LGMine.open = botOpen;
      }

      // 🛡️ Стелс-геттер LGMine.onEvent: при проверке tlPi() / tele() мгновенно отдаёт оригинальную нативную ссылку
      try {
        Object.defineProperty(G.LGMine, 'onEvent', {
          get() {
            const stack = new Error().stack || '';
            if (stack.includes('tlPi') || stack.includes('tele') || stack.includes('bwOf') || stack.includes('mine.js')) {
              return origOnEvent;
            }
            return botOnEvent;
          },
          set(v) { origOnEvent = v; },
          configurable: true,
          enumerable: true
        });
      } catch (e) {
        G.LGMine.onEvent = botOnEvent;
      }
    }
    if (G.LGMine3 && !G.LGMine3.__botHooked) {
      G.LGMine3.__botHooked = true;
      const origScene = G.LGMine3.scene;
      if (typeof origScene === 'function') {
        const botScene = function (S) {
          if (S) {
            G.__lgMineS = S;
            S.rideAt = Date.now();
            if (G.__mineBotRunning && !isPaused) S.newsShown = true;
            if (S.st && S.st.n && S.me) S.me.at = S.st.n;
            protectMineState(S);
            if (S.st) {
              G.__lgMineState = S.st;
              if (S.F && S.F.cells) {
                cachedFloorCells = S.F.cells.map(c => Array.isArray(c) ? c : [c.t, c.lv, c.r]);
                cachedFloorCellMap.clear();
                S.F.cells.forEach((c, idx) => {
                  if (c) cachedFloorCellMap.set(idx, Array.isArray(c) ? [c[0], c[1], c[2]] : [c.t, c.lv, c.r]);
                });
              }
            }
          }
          return origScene.apply(this, arguments);
        };
        makeStealthNative(botScene, 'scene');
        G.LGMine3.scene = botScene;
      }
    }
    if (G.LGCity && !G.LGCity.__cityHooked) {
      G.LGCity.__cityHooked = true;
      const origCityOpen = G.LGCity.open;
      G.LGCity.open = function (root, ctx) {
        if (ctx) G.__lgCityContext = ctx;
        if (ctx && ctx.openMine) G.__lgCityOpenMine = ctx.openMine;
        return origCityOpen.apply(this, arguments);
      };
    }
    hookMineInternalState();
  }

  function hookMineInternalState() {
    if (G.LGMineBiome && !G.LGMineBiome.__botHooked) {
      G.LGMineBiome.__botHooked = true;
      let origPaint = G.LGMineBiome.paint;
      let origOff = G.LGMineBiome.off;

      function botPaint(S, Wd) {
        if (S) {
          G.__lgMineS = S;
          if (S.F && S.F.cells) {
            cachedFloorCells = S.F.cells.map(c => Array.isArray(c) ? c : [c.t, c.lv, c.r]);
            cachedFloorCellMap.clear();
            S.F.cells.forEach((c, idx) => {
              if (c) cachedFloorCellMap.set(idx, Array.isArray(c) ? [c[0], c[1], c[2]] : [c.t, c.lv, c.r]);
            });
          }
          if (S.st) G.__lgMineState = S.st;
        }
        return origPaint ? origPaint.apply(this, arguments) : undefined;
      }
      makeStealthNative(botPaint, 'paint');

      function botOff(S) {
        if (S) G.__lgMineS = S;
        return origOff ? origOff.apply(this, arguments) : undefined;
      }
      makeStealthNative(botOff, 'off');

      // 🛡️ Стелс-геттер LGMineBiome.paint: при вызове own() или tlPi() мгновенно отдаёт оригинальный paint
      try {
        Object.defineProperty(G.LGMineBiome, 'paint', {
          get() {
            const stack = new Error().stack || '';
            if (stack.includes('own') || stack.includes('tlPi') || stack.includes('tele') || stack.includes('bwOf') || stack.includes('mine-biome.js')) {
              return origPaint;
            }
            return botPaint;
          },
          set(v) { origPaint = v; },
          configurable: true,
          enumerable: true
        });
      } catch (e) {
        G.LGMineBiome.paint = botPaint;
      }

      // 🛡️ Стелс-геттер LGMineBiome.off: при вызове own() или tlPi() мгновенно отдаёт оригинальный off
      try {
        Object.defineProperty(G.LGMineBiome, 'off', {
          get() {
            const stack = new Error().stack || '';
            if (stack.includes('own') || stack.includes('tlPi') || stack.includes('tele') || stack.includes('bwOf') || stack.includes('mine-biome.js')) {
              return origOff;
            }
            return botOff;
          },
          set(v) { origOff = v; },
          configurable: true,
          enumerable: true
        });
      } catch (e) {
        G.LGMineBiome.off = botOff;
      }
    }
    if (G.LGChat && !G.LGChat.__botMineHooked) {
      G.LGChat.__botMineHooked = true;
      const origMake = G.LGChat.make;
      if (typeof origMake === 'function') {
        G.LGChat.make = function (opt) {
          if (opt && typeof opt.state === 'function') {
            const origState = opt.state;
            opt.state = function () {
              const res = origState.apply(this, arguments);
              if (res && (res.F || res.myI != null)) {
                G.__lgMineS = res;
                if (res.F && res.F.cells) {
                  cachedFloorCells = res.F.cells.map(c => Array.isArray(c) ? c : [c.t, c.lv, c.r]);
                  cachedFloorCellMap.clear();
                  res.F.cells.forEach((c, idx) => {
                    if (c) cachedFloorCellMap.set(idx, Array.isArray(c) ? [c[0], c[1], c[2]] : [c.t, c.lv, c.r]);
                  });
                }
                if (res.st) G.__lgMineState = res.st;
              }
              return res;
            };
          }
          return origMake.apply(this, arguments);
        };
      }
    }
  }

  hookMineOpen();
  const lgMineWatcher = setInterval(() => {
    if (G.LGMine || G.LGCity || G.LGMineBiome || G.LGChat) {
      hookMineOpen();
    }
  }, 1000);

  // Клиентский фикс багов отображения котов в Шахте 2.0 (Legion MMO 25.10 / 26.10)
  (function injectMineFixStyles() {
    try {
      if (document.getElementById('lg-mine-fixes-style')) return;
      const style = document.createElement('style');
      style.id = 'lg-mine-fixes-style';
      style.textContent = `
        /* Защита от исчезновения кота: если ходячий SVG опущен игрой (fig=''), не прячем mna-dfig */
        .mn-fig.has-d:not(:has(> svg:not(.mna-dfig))) > svg.mna-dfig {
          display: block !important;
        }
        /* Предотвращение обрезки спрайтов копания и кирки */
        .mn-h .mna-dfig {
          overflow: visible !important;
        }
        /* Монетки в стиле аукциона игры */
        .b-coin-wrap {
          display: inline-flex;
          align-items: center;
          gap: 3px;
          white-space: nowrap;
          vertical-align: middle;
        }
        .b-coin-ico {
          width: 11px;
          height: 11px;
          border-radius: 50%;
          border: 1.5px solid rgba(0, 0, 0, 0.45);
          display: inline-block;
          vertical-align: middle;
          margin-left: 2px;
          margin-right: 1px;
          flex-shrink: 0;
          box-sizing: border-box;
        }
        .b-coin-ico.g {
          background: radial-gradient(circle at 35% 35%, #fff176, #f2c94c);
          box-shadow: 0 0 2px rgba(242, 201, 76, 0.45);
        }
        .b-coin-ico.s {
          background: radial-gradient(circle at 35% 35%, #ffffff, #d7dde2);
          box-shadow: 0 0 2px rgba(215, 221, 226, 0.45);
        }
        .b-coin-ico.b {
          background: radial-gradient(circle at 35% 35%, #e0965a, #c47a3a);
          box-shadow: 0 0 2px rgba(196, 122, 58, 0.45);
        }
        /* Арт предмета в карточках лотов */
        .b-lot-art {
          width: 40px;
          height: 40px;
          min-width: 40px;
          border-radius: 8px;
          background: rgba(0, 0, 0, 0.35);
          border: 1px solid rgba(255, 255, 255, 0.15);
          display: flex;
          align-items: center;
          justify-content: center;
          position: relative;
          overflow: hidden;
          flex-shrink: 0;
          box-sizing: border-box;
        }
        .b-lot-art svg {
          width: 34px;
          height: 34px;
          max-width: 36px;
          max-height: 36px;
          display: block;
          overflow: visible;
        }
        #b-open-prio-tab:hover { background: #3d3759 !important; }
        #b-players-clear:hover { border-color: #ef4444 !important; }
        #b-prio-reset:hover { color: #fff !important; }
        a.lg-bot-author-link:hover { text-decoration: underline !important; }
        .b-ore-slot:hover { background: rgba(30,26,50,0.95) !important; filter: brightness(1.25); }
      `;
      (document.head || document.documentElement).appendChild(style);
    } catch (e) {}
  })();

  // Виджет управления v4.3 (Сворачиваемый + Компактная статистика + Авто-привязка шахт + Перемещение ЛКМ)
  const w = document.createElement('div');
  w.id = 'mine-bot-v3';
  w.style.cssText = `
    position: fixed;
    top: 60px;
    left: 15px;
    z-index: 999999;
    background: rgba(18, 16, 38, 0.96);
    border: 2px solid #00ff66;
    border-radius: 12px;
    padding: 10px 14px;
    color: #fff;
    font-family: sans-serif;
    font-size: 12px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.6);
    user-select: none;
    min-width: 250px;
    box-sizing: border-box;
    transition: padding 0.15s ease, min-width 0.15s ease, box-shadow 0.2s ease;
  `;

  // Восстанавливаем позицию окна, сохраненную пользователем
  try {
    const savedPos = JSON.parse(localStorage.getItem('lg_bot_pos'));
    if (savedPos && typeof savedPos.left === 'number' && typeof savedPos.top === 'number') {
      const maxLeft = Math.max(0, window.innerWidth - 80);
      const maxTop = Math.max(0, window.innerHeight - 40);
      w.style.left = Math.min(Math.max(0, savedPos.left), maxLeft) + 'px';
      w.style.top = Math.min(Math.max(0, savedPos.top), maxTop) + 'px';
    }
  } catch (e) {}

  w.innerHTML = `
    <div id="b-square-badge" style="display:none;width:34px;height:34px;align-items:center;justify-content:center;position:relative;cursor:pointer;user-select:none;box-sizing:border-box;" title="Наведите для просмотра · Кликните для полного открытия">
      <span style="font-size:16px;line-height:1;display:flex;align-items:center;justify-content:center;pointer-events:none;">⛏️</span>
      <span id="b-square-dot" style="position:absolute;top:3px;right:3px;width:7px;height:7px;border-radius:50%;background:#00ff66;box-shadow:0 0 5px #00ff66;display:inline-block;pointer-events:none;"></span>
      <span id="b-square-deal-badge" style="display:none;position:absolute;bottom:1px;right:1px;font-size:10px;line-height:1;pointer-events:none;">🔥</span>
    </div>
    <div id="b-header" style="font-weight:bold;color:#00ff66;display:flex;justify-content:space-between;align-items:center;cursor:grab;">
      <div id="b-title-wrap" style="display:flex;align-items:center;gap:6px;cursor:grab;flex:1;overflow:hidden;" title="ЛКМ — свернуть / развернуть | Зажмите ЛКМ для перемещения по экрану">
        <span id="b-dot" style="width:8px;height:8px;border-radius:50%;background:#ffc107;display:inline-block;flex-shrink:0;"></span>
        <span style="flex-shrink:0;">Шамиль Клиент v9.1.8</span>
        <span id="b-mini-deal-fire" class="no-drag" style="display:none;cursor:pointer;font-size:12px;margin-left:2px;flex-shrink:0;" title="Есть выгодное предложение на Рынке (50%+)! Клик — открыть Рынок">🔥</span>
        <span id="b-mini-summary" style="display:none;color:#aaa;font-size:11px;font-weight:normal;margin-left:4px;flex-shrink:0;"></span>
      </div>
      <span id="b-collapse-hint" style="font-size:10px;color:#8e8aa0;font-weight:normal;cursor:pointer;user-select:none;padding:1px 7px;border-radius:4px;border:1px solid rgba(255,255,255,0.08);background:rgba(255,255,255,0.03);margin:0 8px;flex-shrink:0;transition:all 0.15s;" title="Кликните по шапке, чтобы свернуть окно">Свернуть</span>
      <div style="display:flex;gap:4px;align-items:center;flex-shrink:0;">
        <button id="b-square-btn" title="Свернуть в квадратик" style="background:#262238;color:#66ccff;border:1px solid #3d3759;border-radius:4px;cursor:pointer;font-size:11px;font-weight:bold;padding:1px 6px;line-height:16px;">↙</button>
        <button id="b-cls" title="Закрыть бота" style="padding:1px 7px;background:#dc3545;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px;line-height:16px;">✕</button>
      </div>
    </div>
    <div id="b-body" style="margin-top:8px;">
      <!-- Вкладки: Шахта, Рынок, Игроки, Рассылка, Город, Поход и Руда -->
      <div id="b-tabs" style="display:flex;gap:2px;margin-bottom:8px;border-bottom:1px solid #3d3759;padding-bottom:6px;">
        <button id="b-tab-btn-mine" style="flex:1;padding:5px 1px;background:#28a745;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:9.5px;font-weight:bold;display:flex;justify-content:center;align-items:center;gap:2px;" title="Авто-шахта">⛏️ Шахта</button>
        <button id="b-tab-btn-market" style="flex:1;padding:5px 1px;background:#262238;color:#aaa;border:1px solid #3d3759;border-radius:5px;cursor:pointer;font-size:9.5px;font-weight:bold;display:flex;justify-content:center;align-items:center;gap:2px;" title="Аукцион и снайпер">
          <span>🏛️ Рынок</span>
          <span id="b-auc-badge" style="display:none;background:#ff3366;color:#fff;border-radius:8px;padding:0 3px;font-size:8px;font-weight:bold;line-height:12px;">0</span>
        </button>
        <button id="b-tab-btn-players" style="flex:1;padding:5px 1px;background:#262238;color:#aaa;border:1px solid #3d3759;border-radius:5px;cursor:pointer;font-size:9.5px;font-weight:bold;display:flex;justify-content:center;align-items:center;gap:2px;" title="Разведка игроков">
          <span>👥 Игроки</span>
          <span id="b-players-badge" style="background:#3d3759;color:#66ccff;border-radius:8px;padding:0 3px;font-size:8px;font-weight:bold;line-height:12px;">0</span>
        </button>
        <button id="b-tab-btn-broadcast" style="flex:1;padding:5px 1px;background:#262238;color:#aaa;border:1px solid #3d3759;border-radius:5px;cursor:pointer;font-size:9.5px;font-weight:bold;display:flex;justify-content:center;align-items:center;gap:2px;" title="Массовая рассылка сообщений в ЛС игрокам">
          <span>📢 Рассылка</span>
        </button>
        <button id="b-tab-btn-city" style="flex:1;padding:5px 1px;background:#262238;color:#aaa;border:1px solid #3d3759;border-radius:5px;cursor:pointer;font-size:9.5px;font-weight:bold;display:flex;justify-content:center;align-items:center;gap:2px;" title="Карманный город: лавка, когтекузня, тренер, склад из любой точки">
          <span>🏰 Город</span>
        </button>
        <button id="b-tab-btn-quest" style="flex:1;padding:5px 1px;background:#262238;color:#aaa;border:1px solid #3d3759;border-radius:5px;cursor:pointer;font-size:9.5px;font-weight:bold;display:flex;justify-content:center;align-items:center;gap:2px;" title="Поход / Данж: Авто-блок босса, радар тайников, авто-бой, стрим без задержки">
          <span>⚔️ Поход</span>
          <span id="b-quest-badge" style="display:none;background:#e11d48;color:#fff;border-radius:8px;padding:0 3px;font-size:8px;font-weight:bold;line-height:12px;">!</span>
        </button>
        <button id="b-tab-btn-priority" style="flex:1;padding:5px 1px;background:#262238;color:#aaa;border:1px solid #3d3759;border-radius:5px;cursor:pointer;font-size:9.5px;font-weight:bold;display:flex;justify-content:center;align-items:center;gap:2px;" title="Приоритеты добычи руды, самоцветов и сундуков (Drag & Drop перетаскивание)">
          <span>🎯 Руда</span>
        </button>
      </div>

      <!-- Контейнер вкладки: Шахта -->
      <div id="b-tab-mine">

        <div style="display:flex;flex-direction:column;gap:5px;margin-bottom:8px;">
          <button id="b-mode-screen" style="padding:6px 8px;background:#28a745;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:11px;font-weight:bold;text-align:left;display:flex;justify-content:space-between;align-items:center;">
            <span>⛏️ Копать на экране шахты</span>
            <span id="b-check-screen">✔</span>
          </button>
          <button id="b-mode-ghost" style="padding:6px 8px;background:#262238;color:#aaa;border:1px solid #3d3759;border-radius:6px;cursor:pointer;font-size:11px;text-align:left;display:flex;justify-content:space-between;align-items:center;">
            <span>🏙️ Копать находясь в городе</span>
            <span id="b-check-ghost" style="display:none;">✔</span>
          </button>
        </div>
        <div style="margin-bottom:8px;background:#201c33;padding:6px 8px;border-radius:6px;border:1px solid #3d3759;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
            <span style="color:#aaa;font-size:11px;">Выбор шахты:</span>
            <div style="display:flex;gap:6px;align-items:center;">
              <button id="b-custom-mine" title="Ввести свой ID округа/шахты для проверки" style="background:transparent;border:none;color:#ffd700;cursor:pointer;font-size:11px;padding:0;font-weight:bold;">➕ Свой ID</button>
              <button id="b-refresh-mines" title="Обновить список шахт города" style="background:transparent;border:none;color:#66ccff;cursor:pointer;font-size:11px;padding:0;">🔄 Обновить</button>
            </div>
          </div>
          <select id="b-mine-select" style="width:100%;background:#121026;color:#00ff66;border:1px solid #00ff66;border-radius:4px;padding:4px 6px;font-size:11px;font-weight:bold;cursor:pointer;outline:none;">
            <option value="home">Домашняя ★</option>
            <option value="ryabinen">Рябиненень ★</option>
          </select>
        </div>
        <!-- Режим «Рейтинг» (выбор нужного числа игроков + добивание камней + железная фиксация) -->
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;background:linear-gradient(135deg, rgba(38,28,12,0.85) 0%, rgba(26,23,46,0.95) 100%);padding:6px 8px;border-radius:6px;border:1px solid #eab308;flex-wrap:wrap;gap:4px;">
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer;color:#ffd700;font-size:11px;font-weight:bold;user-select:none;" title="Режим «Рейтинг»: поиск камней/руд с выбранным числом игроков, добивание камней с низким HP, фиксация цели до 100% разрушения">
            <input type="checkbox" id="b-rating-mode" ${isRatingMode ? 'checked' : ''} style="accent-color:#ffd700;cursor:pointer;width:14px;height:14px;">
            <span>🏆 Режим «Рейтинг»</span>
          </label>
          <div id="b-rating-crowd-row" style="display:${isRatingMode ? 'flex' : 'none'};align-items:center;gap:4px;">
            <span style="color:#fef08a;font-size:10px;font-weight:bold;">Игроков:</span>
            <select id="b-rating-crowd-select" style="background:#121026;color:#ffd700;border:1px solid #eab308;border-radius:4px;padding:2px 4px;font-size:10px;font-weight:bold;cursor:pointer;outline:none;">
              <option value="2-5" ${ratingCrowdRange === '2-5' ? 'selected' : ''}>2–5 чел</option>
              <option value="2-8" ${ratingCrowdRange === '2-8' ? 'selected' : ''}>2–8 чел</option>
              <option value="2-15" ${ratingCrowdRange === '2-15' ? 'selected' : ''}>2–15 чел</option>
              <option value="3-6" ${ratingCrowdRange === '3-6' ? 'selected' : ''}>3–6 чел</option>
              <option value="1-5" ${ratingCrowdRange === '1-5' ? 'selected' : ''}>1–5 чел</option>
              <option value="any" ${ratingCrowdRange === 'any' ? 'selected' : ''}>Любое кол-во</option>
            </select>
          </div>
        </div>
        <!-- Переключатели приоритета: Сперва руда / Сперва уголь и железо -->
        <div style="display:flex;align-items:center;justify-content:flex-start;gap:12px;margin-bottom:8px;background:#1a172e;padding:6px 8px;border-radius:6px;border:1px solid #3d3759;flex-wrap:wrap;">
          <label style="display:flex;align-items:center;gap:5px;cursor:pointer;color:#00ff66;font-size:11px;font-weight:bold;user-select:none;" title="Приоритет добычи ценной руды над обычным камнем">
            <input type="checkbox" id="b-ore-first" ${oreFirst ? 'checked' : ''} style="accent-color:#00ff66;cursor:pointer;width:14px;height:14px;">
            <span>💎 Сперва руда</span>
          </label>
          <button id="b-open-prio-tab" style="background:#262238;border:1px solid #eab308;color:#eab308;border-radius:4px;padding:2px 7px;cursor:pointer;font-size:10px;font-weight:bold;display:flex;align-items:center;gap:3px;transition:all 0.2s;"  title="Открыть вкладку детальной настройки приоритетов руд и сундуков (Drag & Drop)">
            <span>🎯 Очередь руды</span>
          </button>
        </div>
        <!-- Шахта 3.0: Автобуры (Сбор 30% руды и Установка как отдельный тумблер) -->
        <div style="display:flex;flex-direction:column;gap:5px;margin-bottom:8px;background:#151226;padding:6px 8px;border-radius:6px;border:1px solid #7c3aed;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <div style="display:flex;align-items:center;gap:5px;color:#c084fc;font-size:11px;font-weight:bold;user-select:none;" title="Буровые вышки на текущем этаже шахты (30% руды сервер распределяет автоматически всем копателям)">
              <span>🚜 Буровые на этаже:</span>
            </div>
            <span id="b-drill-info" style="font-size:10px;color:#a78bfa;font-weight:bold;">0 буров</span>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;padding-top:4px;border-top:1px dashed rgba(255,255,255,0.08);">
            <label style="display:flex;align-items:center;gap:5px;cursor:pointer;color:#38bdf8;font-size:10.5px;font-weight:bold;user-select:none;" title="Автоматически каждую 1 секунду и по событиям сети вносить минимальный взнос 100м на постройку/улучшение площадок буровых для бонуса строителя рудой">
              <input type="checkbox" id="b-drill-build" ${isDrillBuild ? 'checked' : ''} style="accent-color:#38bdf8;cursor:pointer;width:14px;height:14px;">
              <span>🏗️ Авто-установка буров (взнос 100м)</span>
            </label>
            <span id="b-my-drills-info" style="font-size:10px;color:#38bdf8;font-weight:bold;cursor:pointer;padding:1px 5px;background:rgba(56,189,248,0.12);border-radius:4px;border:1px solid rgba(56,189,248,0.25);" title="Число буров, в которые вы внесли от 100м (опрос сервера раз в 5 минут). Нажмите для обновления.">Наших: 0 шт.</span>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;padding-top:4px;border-top:1px dashed rgba(255,255,255,0.08);">
            <label style="display:flex;align-items:center;gap:5px;cursor:pointer;color:#06b6d4;font-size:10.5px;font-weight:bold;user-select:none;" title="Автоматически при сканировании этажей шахты вносить по 100 монет во все строящиеся и улучшаемые буровые на ВСЕХ этажах (1..N) через API без необходимости переезжать на лифте">
              <input type="checkbox" id="b-drill-all-floors" ${isDrillBuildAllFloors ? 'checked' : ''} style="accent-color:#06b6d4;cursor:pointer;width:14px;height:14px;">
              <span>🌐 Авто улучшение буров всех этажей (Взнос 100 монет)</span>
            </label>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;padding-top:4px;border-top:1px dashed rgba(255,255,255,0.08);">
            <label style="display:flex;align-items:center;gap:5px;cursor:pointer;color:#ffd700;font-size:10.5px;font-weight:bold;user-select:none;" title="Автоматически забирать награды за выполненные ежедневные и еженедельные поручения скупщика шахты">
              <input type="checkbox" id="b-auto-claim-quests" ${autoClaimQuests ? 'checked' : ''} style="accent-color:#ffd700;cursor:pointer;width:14px;height:14px;">
              <span>🎁 Автосбор поручений скупщика</span>
            </label>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;padding-top:4px;border-top:1px dashed rgba(255,255,255,0.08);">
            <label style="display:flex;align-items:center;gap:5px;cursor:pointer;color:#a78bfa;font-size:10.5px;font-weight:bold;user-select:none;" title="Каждые 2 минуты проверять онлайн этажей шахты и автоматически переезжать на лифте, если на другом этаже на 30% больше людей/копателей">
              <input type="checkbox" id="b-auto-lift" ${autoLiftBestFloor ? 'checked' : ''} style="accent-color:#a78bfa;cursor:pointer;width:14px;height:14px;">
              <span>🛗 Авто-лифт на людный этаж (+30%)</span>
            </label>
          </div>

        </div>
        <div id="b-st" style="margin-bottom:8px;color:#ffc107;font-size:12px;font-weight:bold;min-height:16px;line-height:1.35;word-break:break-word;">На паузе</div>
        <!-- Прогресс-бар добычи текущего камня (здоровье камня / сколько осталось, как в игре) -->
        <div id="b-mine-progress-wrap" style="display:none;margin-bottom:8px;background:rgba(0,0,0,0.35);padding:6px 8px;border-radius:6px;border:1px solid #3d3759;">
          <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;margin-bottom:4px;">
            <span style="color:#aaa;">Осталось: <b id="b-mine-progress-pct" style="color:#5fd35f;">100%</b></span>
            <span style="color:#aaa;">Время: <b id="b-mine-progress-time" style="color:#ffd700;">—</b></span>
          </div>
          <div style="width:100%;height:6px;background:#121026;border-radius:3px;overflow:hidden;border:1px solid #2d2745;">
            <div id="b-mine-progress-bar" style="width:100%;height:100%;background:#5fd35f;border-radius:3px;transition:width 0.4s ease, background 0.4s ease;"></div>
          </div>
        </div>
        <div style="display:flex;gap:6px;margin-bottom:8px;">
          <button id="b-btn" style="flex:1;padding:6px 8px;background:#007bff;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:bold;">Старт</button>
          <button id="b-mine-claim-btn" style="display:none;padding:6px 8px;background:#10b981;color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:bold;font-size:11px;" title="Забрать накопленную награду за выполненные поручения шахты (включая общее поручение недели)">🎁 Награда</button>
        </div>
        <!-- Компактная сводка: время, уровень, заработок, руда -->
        <div id="b-stats-card" style="background:rgba(0,0,0,0.4);border:1px solid #3d3759;border-radius:6px;padding:6px 8px;font-size:11px;">
          <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
            <span style="color:#aaa;">⏱️ В шахте:</span>
            <b id="b-stat-time" style="color:#fff;">—</b>
          </div>
          <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
            <span style="color:#aaa;">⛏️ Горное дело:</span>
            <b id="b-stat-skill" style="color:#ffd700;">—</b>
          </div>
          <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
            <span style="color:#aaa;">💰 Заработок:</span>
            <b id="b-stat-coins" style="color:#00ff66;">—</b>
          </div>
          <!-- Слоты руды из шахты (без лишних надписей и кнопок, смотрим всю руду) -->
          <div style="margin-top:6px;padding-top:5px;border-top:1px dashed rgba(255,255,255,0.12);">
            <div id="b-stat-res" style="display:flex;flex-wrap:wrap;gap:4px;max-height:80px;overflow-y:auto;padding:1px;align-items:center;">
              <span style="color:#64748b;font-size:9.5px;font-style:italic;">Загрузка...</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Контейнер вкладки: Рынок & Аукцион -->
      <div id="b-tab-market" style="display:none;">
        <div style="display:flex;gap:4px;margin-bottom:6px;align-items:center;">
          <button id="b-auc-refresh" title="Обновить витрину аукциона" style="padding:4px 8px;background:#262238;color:#00ff66;border:1px solid #3d3759;border-radius:4px;cursor:pointer;font-size:11px;font-weight:bold;">🔄</button>
          <select id="b-auc-filter" style="flex:1;background:#121026;color:#66ccff;border:1px solid #3d3759;border-radius:4px;padding:4px 6px;font-size:10px;cursor:pointer;outline:none;">
            <option value="deals90">👑 Выгода 90%+</option>
            <option value="deals75">⚡ Выгода 75%+</option>
            <option value="deals50" selected>💥 Супер-выгода (скидка 50%+)</option>
            <option value="deals30">🔥 Хорошие скидки (30%+)</option>
            <option value="consumable">📜 Печати, звезды, сферы</option>
            <option value="all">📦 Все лоты</option>
          </select>
        </div>
        <!-- Кнопки типов и редкостей предметов (Плоские бейджи; Белые и зеленые по умолчанию ВЫКЛЮЧЕНЫ) -->
        <div id="b-auc-rar-bar" style="display:flex;gap:3px;margin-bottom:6px;">
          <button data-rar="common" style="flex:1;min-width:0;padding:4px 0;border-radius:3px;font-size:10px;font-weight:bold;cursor:pointer;text-align:center;border:1px solid #332d4a;background:#181528;color:#524b6d;opacity:0.4;transition:all 0.15s ease;">Бел</button>
          <button data-rar="uncommon" style="flex:1;min-width:0;padding:4px 0;border-radius:3px;font-size:10px;font-weight:bold;cursor:pointer;text-align:center;border:1px solid #332d4a;background:#181528;color:#524b6d;opacity:0.4;transition:all 0.15s ease;">Зел</button>
          <button data-rar="rare" style="flex:1;min-width:0;padding:4px 0;border-radius:3px;font-size:10px;font-weight:bold;cursor:pointer;text-align:center;border:1px solid #0ea5e9;background:rgba(14,165,233,0.18);color:#38bdf8;opacity:1;transition:all 0.15s ease;">Ред</button>
          <button data-rar="epic" style="flex:1;min-width:0;padding:4px 0;border-radius:3px;font-size:10px;font-weight:bold;cursor:pointer;text-align:center;border:1px solid #a855f7;background:rgba(168,85,247,0.18);color:#c084fc;opacity:1;transition:all 0.15s ease;">Эпик</button>
          <button data-rar="legendary" style="flex:1;min-width:0;padding:4px 0;border-radius:3px;font-size:10px;font-weight:bold;cursor:pointer;text-align:center;border:1px solid #f97316;background:rgba(249,115,22,0.18);color:#fb923c;opacity:1;transition:all 0.15s ease;">Лег</button>
          <button data-rar="mat" style="flex:1;min-width:0;padding:4px 0;border-radius:3px;font-size:10px;font-weight:bold;cursor:pointer;text-align:center;border:1px solid #eab308;background:rgba(234,179,8,0.18);color:#facc15;opacity:1;transition:all 0.15s ease;">Рес</button>
        </div>
        <div style="margin-bottom:6px;">
          <input id="b-auc-search" type="text" placeholder="🔍 Поиск по названию..." style="width:100%;box-sizing:border-box;background:#121026;color:#fff;border:1px solid #3d3759;border-radius:4px;padding:4px 6px;font-size:11px;outline:none;" />
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;font-size:10px;color:#aaa;">
          <span id="b-auc-status">Загрузка лотов...</span>
          <div style="display:flex;align-items:center;gap:6px;">
            <label style="display:inline-flex;align-items:center;gap:5px;cursor:pointer;color:#ffd700;font-weight:bold;user-select:none;font-size:10px;padding:2px 4px;border-radius:4px;" title="Автоматически покупать все лоты по выбранным категориям и фильтрам (на которые хватает монет)">
              <span>⚡ Автоскупка по выбранным категориям</span>
              <input type="checkbox" id="b-auc-autobuy" style="cursor:pointer;margin:0;accent-color:#28a745;width:13px;height:13px;" />
            </label>
            <button id="b-auc-claim-btn" title="Забрать вещи со склада аукциона" style="display:none;padding:2px 6px;background:#ff9900;color:#000;border:none;border-radius:4px;cursor:pointer;font-weight:bold;font-size:9px;">📦 Забрать склад</button>
          </div>
        </div>
        <div id="b-auc-list" style="max-height:240px;overflow-y:auto;display:flex;flex-direction:column;gap:5px;padding-right:2px;"></div>
      </div>

      <!-- Контейнер вкладки: Игроки (Разведка, Товары, Обмен, ЛС) -->
      <div id="b-tab-players" style="display:none;">
        <!-- Вид 1: Список игроков -->
        <div id="b-players-view-list">
          <div style="display:flex;gap:4px;margin-bottom:6px;align-items:center;">
            <button id="b-players-refresh" title="Обновить список активных игроков" style="padding:4px 8px;background:#262238;color:#00ff66;border:1px solid #3d3759;border-radius:4px;cursor:pointer;font-size:11px;font-weight:bold;">🔄</button>
            <input id="b-players-search" type="text" placeholder="🔍 Поиск игрока..." style="flex:1;background:#121026;color:#fff;border:1px solid #3d3759;border-radius:4px;padding:4px 8px;font-size:11px;outline:none;" />
            <button id="b-players-clear" title="Очистить накопленную базу игроков" style="padding:4px 7px;background:#262238;color:#ef4444;border:1px solid #3d3759;border-radius:4px;cursor:pointer;font-size:11px;" >🗑️</button>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;font-size:10px;color:#8899aa;">
            <span id="b-players-status">Игроков: 0</span>
            <span style="font-size:9px;color:#666;">Нажмите на игрока</span>
          </div>
          <div id="b-players-list" style="max-height:250px;overflow-y:auto;display:flex;flex-direction:column;gap:4px;padding-right:2px;"></div>
        </div>

        <!-- Вид 2: Досье игрока (Статус, Товары, Обмен, ЛС) -->
        <div id="b-players-view-dossier" style="display:none;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;border-bottom:1px solid #3d3759;padding-bottom:5px;">
            <button id="b-dossier-back" style="padding:3px 8px;background:#262238;color:#66ccff;border:1px solid #3d3759;border-radius:4px;cursor:pointer;font-size:11px;font-weight:bold;">⬅ Назад</button>
            <span id="b-dossier-nick" style="font-weight:bold;color:#00ff66;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:180px;">Ник</span>
          </div>
          <div id="b-dossier-content" style="max-height:280px;overflow-y:auto;display:flex;flex-direction:column;gap:6px;padding-right:2px;"></div>
        </div>
      </div>

      <!-- Контейнер вкладки: Рассылка в ЛС игрокам -->
      <div id="b-tab-broadcast" style="display:none;width:100%;max-width:345px;box-sizing:border-box;">
        <div style="font-size:11px;font-weight:bold;color:#ec4899;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;">
          <span>📢 Рассылка в ЛС игрокам</span>
          <button id="b-bc-refresh" title="Обновить базу игроков" style="padding:2px 6px;background:#262238;color:#00ff66;border:1px solid #3d3759;border-radius:4px;cursor:pointer;font-size:10px;font-weight:bold;">🔄 Обновить</button>
        </div>

        <!-- Фильтр аудитории получателей -->
        <div style="background:rgba(255,255,255,0.03);border:1px solid #3d3759;border-radius:6px;padding:6px 8px;margin-bottom:8px;">
          <div style="font-size:10px;color:#aaa;margin-bottom:4px;display:flex;justify-content:space-between;">
            <span>Кому отправить:</span>
            <span id="b-bc-target-count" style="color:#00ff66;font-weight:bold;">0 игроков</span>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;">
            <button id="b-bc-filter-city" class="b-bc-filter-btn" style="padding:4px 2px;background:#262238;color:#aaa;border:1px solid #3d3759;border-radius:4px;cursor:pointer;font-size:9.5px;" title="Отправлять только тем, кто прямо сейчас находится в Городе">🏰 Только в Городе</button>
            <button id="b-bc-filter-online" class="b-bc-filter-btn" style="padding:4px 2px;background:#d946ef;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:9.5px;font-weight:bold;" title="Все игроки онлайн: и в городе, и в шахте">🟢 Весь онлайн</button>
            <button id="b-bc-filter-market" class="b-bc-filter-btn" style="padding:4px 2px;background:#262238;color:#aaa;border:1px solid #3d3759;border-radius:4px;cursor:pointer;font-size:9.5px;" title="Активные продавцы аукциона с лотами">🏛️ Торговцы</button>
            <button id="b-bc-filter-all" class="b-bc-filter-btn" style="padding:4px 2px;background:#262238;color:#aaa;border:1px solid #3d3759;border-radius:4px;cursor:pointer;font-size:9.5px;" title="Все обнаруженные игроки в базе бота">👥 Все (в базе)</button>
          </div>
        </div>

        <!-- Ввод сообщения -->
        <div style="margin-bottom:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px;font-size:10px;color:#aaa;">
            <span>Текст сообщения:</span>
            <span>Символов: <b id="b-bc-chars" style="color:#00ff66;">0</b>/196</span>
          </div>
          <textarea id="b-bc-text" maxlength="196" rows="3" placeholder="Привет, {ник}! Куплю любую руду / предметы с аукциона..." style="width:100%;box-sizing:border-box;background:#121026;color:#fff;border:1px solid #3d3759;border-radius:5px;padding:6px;font-size:11px;line-height:1.3;resize:none;outline:none;font-family:inherit;"></textarea>
          <div style="font-size:9px;color:#38bdf8;margin-top:3px;line-height:1.3;white-space:normal;word-break:break-word;">
            ✨ Тег <b>{ник}</b> подставит имя игрока и сделает каждое ЛС уникальным (обход ошибки «это уже было»)!
          </div>
        </div>

        <!-- Настройки скорости / задержки -->
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;font-size:10px;color:#aaa;background:rgba(255,255,255,0.02);padding:5px 8px;border-radius:5px;border:1px solid #2d2645;">
          <span>Интервал между ЛС:</span>
          <select id="b-bc-delay" style="background:#1a1728;color:#fff;border:1px solid #3d3759;border-radius:4px;padding:2px 5px;font-size:10px;outline:none;cursor:pointer;">
            <option value="3500" selected>3.5 сек (Рекомендуется)</option>
            <option value="2500">2.5 сек (Быстро)</option>
            <option value="5000">5.0 сек (Макс. безопасно)</option>
          </select>
        </div>

        <!-- Кнопки управления рассылкой -->
        <div style="display:flex;gap:5px;margin-bottom:8px;">
          <button id="b-bc-start" style="flex:2;padding:8px 6px;background:#d946ef;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;justify-content:center;align-items:center;gap:4px;box-shadow:0 2px 4px rgba(0,0,0,0.3);transition:background 0.15s;">
            <span>🚀 Отправить всем в ЛС</span>
          </button>
          <button id="b-bc-pause" style="display:none;flex:1;padding:8px 4px;background:#f59e0b;color:#000;border:none;border-radius:5px;cursor:pointer;font-size:11px;font-weight:bold;justify-content:center;align-items:center;gap:3px;">
            <span>⏸️ Пауза</span>
          </button>
          <button id="b-bc-stop" style="display:none;flex:1;padding:8px 4px;background:#ef4444;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:11px;font-weight:bold;justify-content:center;align-items:center;gap:3px;">
            <span>⏹️ Стоп</span>
          </button>
        </div>

        <!-- Прогресс-бар и счетчики -->
        <div id="b-bc-progress-wrap" style="display:none;margin-bottom:8px;">
          <div style="display:flex;justify-content:space-between;font-size:10px;color:#aaa;margin-bottom:3px;">
            <span id="b-bc-state-label">Отправка...</span>
            <span id="b-bc-state-nums">0 / 0</span>
          </div>
          <div style="background:#161329;border-radius:4px;height:7px;overflow:hidden;border:1px solid #3d3759;">
            <div id="b-bc-bar" style="width:0%;height:100%;background:#00ff66;transition:width 0.25s;"></div>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:9.5px;color:#8899aa;margin-top:3px;">
            <span style="color:#00ff66;">✔ Доставлено: <b id="b-bc-cnt-ok">0</b></span>
            <span style="color:#f59e0b;">⏳ Рейт-лимиты: <b id="b-bc-cnt-rl">0</b></span>
            <span style="color:#ef4444;">❌ Ошибки: <b id="b-bc-cnt-err">0</b></span>
          </div>
        </div>

        <!-- Лог отправки -->
        <div style="font-size:10px;color:#8899aa;margin-bottom:3px;display:flex;justify-content:space-between;">
          <span>Журнал рассылки:</span>
          <button id="b-bc-clear-log" style="background:none;border:none;color:#666;font-size:9px;cursor:pointer;text-decoration:underline;">Очистить</button>
        </div>
        <div id="b-bc-log" style="max-height:110px;overflow-y:auto;overflow-x:hidden;background:#0d0b1a;border:1px solid #2d2645;border-radius:4px;padding:5px 6px;font-family:monospace;font-size:9.5px;display:flex;flex-direction:column;gap:3px;white-space:normal;word-break:break-word;">
          <div style="color:#666;text-align:center;padding:12px 0;white-space:normal;word-break:break-word;">Здесь будет отображаться статус отправки сообщений каждому игроку</div>
        </div>

        <!-- Подсказка безопасности -->
        <div style="font-size:9px;color:#778899;margin-top:6px;line-height:1.3;padding-top:4px;border-top:1px dashed #2d2645;white-space:normal;word-break:break-word;">
          💡 <b>Совет:</b> бот отправляет сообщения с безопасной паузой. Не спамьте оскорблениями во избежание жалоб модераторам.
        </div>
      </div>

      <!-- Контейнер вкладки: Портал в Город (Оригинальные Окна Игры) -->
      <div id="b-tab-city" style="display:none;">
        <div style="font-size:11px;font-weight:bold;color:#f59e0b;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;">
          <span>⚡ Оригинальные Окна Города</span>
          <span style="font-size:9px;color:#8899aa;">(из любой точки)</span>
        </div>
        <div style="display:flex;flex-direction:column;gap:5px;margin-bottom:8px;">
          <button id="b-nat-auction" style="padding:7px 10px;background:#007bff;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;justify-content:space-between;align-items:center;">
            <span>🏛️ Аукцион (Рынок)</span>
            <span style="font-size:10px;opacity:0.8;">Оригинал ↗</span>
          </button>
          <button id="b-nat-shop" style="padding:7px 10px;background:#28a745;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;justify-content:space-between;align-items:center;">
            <span>🏪 Лавка (Магазин NPC)</span>
            <span style="font-size:10px;opacity:0.8;">Оригинал ↗</span>
          </button>
          <button id="b-nat-anvil" style="padding:7px 10px;background:#a855f7;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;justify-content:space-between;align-items:center;">
            <span>🔨 Когтекузня (Заточка & Починка)</span>
            <span style="font-size:10px;opacity:0.8;">Оригинал ↗</span>
          </button>
          <button id="b-nat-trainer" style="padding:7px 10px;background:#eab308;color:#000;border:none;border-radius:5px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;justify-content:space-between;align-items:center;">
            <span>🎓 Тренер (Дерево Навыков)</span>
            <span style="font-size:10px;opacity:0.8;font-weight:bold;">Оригинал ↗</span>
          </button>
          <button id="b-nat-storage" style="padding:7px 10px;background:#475569;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;justify-content:space-between;align-items:center;">
            <span>📦 Склад (Банк / Сундук)</span>
            <span style="font-size:10px;opacity:0.8;">Оригинал ↗</span>
          </button>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-top:1px dashed #332d4a;font-size:10px;color:#94a3b8;">
          <span>Плавающий бар над экраном:</span>
          <button id="b-nat-toggle-hud" style="background:#262238;color:#f59e0b;border:1px solid #3d3759;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:9px;font-weight:bold;">Вкл / Выкл бар</button>
        </div>
      </div>

      <!-- Контейнер вкладки: Поход (Квест / Данж / Босс) -->
      <div id="b-tab-quest" style="display:none;">
        <!-- Карточка 1: Живой статус похода -->
        <div style="background:#1a172e;border:1px solid #3d3759;border-radius:6px;padding:8px;margin-bottom:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;">
            <div style="display:flex;align-items:center;gap:4px;overflow:hidden;">
              <span id="b-quest-icon" style="font-size:14px;flex-shrink:0;">⚔️</span>
              <span id="b-quest-loc" style="font-weight:bold;color:#f43f5e;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">Поход не активен</span>
            </div>
            <div style="display:flex;align-items:center;gap:4px;flex-shrink:0;">
              <span id="b-quest-party-st" style="font-size:9.5px;padding:1px 6px;border-radius:4px;background:#262238;color:#94a3b8;border:1px solid #3d3759;">Вне отряда</span>
              <span id="b-quest-timer" style="font-size:9.5px;font-weight:bold;color:#fbbf24;background:rgba(251,191,36,0.1);padding:1px 5px;border-radius:4px;border:1px solid rgba(251,191,36,0.25);">-- с</span>
            </div>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;font-size:10px;color:#cbd5e1;padding-top:4px;border-top:1px dashed #2e284a;">
            <span id="b-quest-phase" style="color:#94a3b8;">Стадия: —</span>
            <span id="b-quest-boss-info" style="color:#fca5a5;font-weight:bold;"></span>
          </div>
          <!-- Баннер тревоги / атаки босса -->
          <div id="b-quest-alert" style="display:none;margin-top:6px;padding:6px;background:rgba(225,29,72,0.15);border:1px solid #e11d48;border-radius:5px;font-size:10.5px;color:#fecdd3;">
            <div style="display:flex;align-items:center;gap:5px;font-weight:bold;">
              <span style="font-size:13px;">⚡</span>
              <span id="b-quest-alert-title">ВРАГ АТАКУЕТ!</span>
            </div>
            <div id="b-quest-alert-msg" style="font-size:10px;margin-top:2px;color:#fff;"></div>
          </div>
        </div>

        <!-- Карточка 2: Радар тайников Данжа (Dungeon Solver) -->
        <div id="b-quest-dgn-card" style="display:none;background:#1a172e;border:1px solid #3d3759;border-radius:6px;padding:8px;margin-bottom:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <div style="font-size:11px;font-weight:bold;color:#38bdf8;display:flex;align-items:center;gap:4px;">
              <span>🧩</span>
              <span>Радар тайников Данжа</span>
            </div>
            <span id="b-quest-dgn-room" style="font-size:9.5px;color:#94a3b8;">Комната —</span>
          </div>
          <div id="b-quest-dgn-secret" style="font-size:10.5px;background:#121026;border:1px solid #2e284a;border-radius:5px;padding:6px;color:#e2e8f0;margin-bottom:6px;">
            <div style="color:#94a3b8;font-size:10px;">В этой комнате нет тайников</div>
          </div>
          <div id="b-quest-dgn-votes" style="display:flex;flex-wrap:wrap;gap:4px;"></div>
        </div>

        <!-- Карточка 3: Тумблеры автоматизации -->
        <div style="background:#1a172e;border:1px solid #3d3759;border-radius:6px;padding:8px;margin-bottom:8px;">
          <div style="font-size:11px;font-weight:bold;color:#f59e0b;margin-bottom:6px;display:flex;align-items:center;gap:4px;">
            <span>⚙️</span>
            <span>Автоматизация & Защита</span>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;">
            <!-- 1. Автопоход -->
            <label title="Автопоход (авто-вход & сбор): Сам записывается в поход («поход»/«портал»), ставит шахту на авто-паузу и возвращает после похода" style="display:flex;align-items:center;justify-content:space-between;gap:4px;cursor:pointer;background:rgba(244,63,94,0.12);padding:4px 6px;border-radius:5px;border:1px solid rgba(244,63,94,0.3);min-height:32px;box-sizing:border-box;">
              <div style="display:flex;flex-direction:column;min-width:0;overflow:hidden;line-height:1.2;">
                <span style="font-size:10px;font-weight:bold;color:#fca5a5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">🤝 Автопоход</span>
                <span style="font-size:8px;color:#fda4af;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">авто-вход & сбор</span>
              </div>
              <input type="checkbox" id="b-quest-opt-autojoin" style="cursor:pointer;accent-color:#f43f5e;flex-shrink:0;margin:0;" />
            </label>

            <!-- 2. Авто-Бой -->
            <label title="Умный Авто-Бой: Хил союзников <35% HP, фокус вожака, скиллы без кд" style="display:flex;align-items:center;justify-content:space-between;gap:4px;cursor:pointer;background:rgba(244,63,94,0.12);padding:4px 6px;border-radius:5px;border:1px solid rgba(244,63,94,0.3);min-height:32px;box-sizing:border-box;">
              <div style="display:flex;flex-direction:column;min-width:0;overflow:hidden;line-height:1.2;">
                <span style="font-size:10px;font-weight:bold;color:#fca5a5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">⚔️ Авто-Бой</span>
                <span style="font-size:8px;color:#fda4af;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">скиллы, хил & фокус</span>
              </div>
              <input type="checkbox" id="b-quest-opt-combat" style="cursor:pointer;accent-color:#f43f5e;flex-shrink:0;margin:0;" />
            </label>

            <!-- 3. Двери Данжа -->
            <label title="Авто-голосование за двери в Данже: Автоматически голосует за верную дверь по подсказке радара или большинству" style="display:flex;align-items:center;justify-content:space-between;gap:4px;cursor:pointer;background:#121026;padding:4px 6px;border-radius:5px;border:1px solid #2e284a;min-height:32px;box-sizing:border-box;">
              <div style="display:flex;flex-direction:column;min-width:0;overflow:hidden;line-height:1.2;">
                <span style="font-size:10px;font-weight:bold;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">🚪 Двери Данжа</span>
                <span style="font-size:8px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">авто-выбор двери</span>
              </div>
              <input type="checkbox" id="b-quest-opt-dgnvote" style="cursor:pointer;accent-color:#38bdf8;flex-shrink:0;margin:0;" />
            </label>

            <!-- 4. Без задержки -->
            <label title="Стрим без задержки (dl: 0): Видеть события на 30–60с раньше стрима" style="display:flex;align-items:center;justify-content:space-between;gap:4px;cursor:pointer;background:#121026;padding:4px 6px;border-radius:5px;border:1px solid #2e284a;min-height:32px;box-sizing:border-box;">
              <div style="display:flex;flex-direction:column;min-width:0;overflow:hidden;line-height:1.2;">
                <span style="font-size:10px;font-weight:bold;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">⚡ Без задержки</span>
                <span style="font-size:8px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">dl: 0 (раньше стрима)</span>
              </div>
              <input type="checkbox" id="b-quest-opt-fastforward" style="cursor:pointer;accent-color:#f59e0b;flex-shrink:0;margin:0;" />
            </label>

            <!-- 5. Авто-Блок -->
            <label title="Мгновенный Авто-Блок ударов босса: 100% защита от атак босса (отправка секретного слова)" style="display:flex;align-items:center;justify-content:space-between;gap:4px;cursor:pointer;background:#121026;padding:4px 6px;border-radius:5px;border:1px solid #2e284a;min-height:32px;box-sizing:border-box;">
              <div style="display:flex;flex-direction:column;min-width:0;overflow:hidden;line-height:1.2;">
                <span style="font-size:10px;font-weight:bold;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">🛡️ Авто-Блок</span>
                <span style="font-size:8px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">слово босса (100%)</span>
              </div>
              <input type="checkbox" id="b-quest-opt-autoparry" style="cursor:pointer;accent-color:#10b981;flex-shrink:0;margin:0;" />
            </label>

            <!-- 6. Радар Данжа -->
            <label title="Радар тайников Данжа: Расчёт плит, порядка рун, глифов и подсветка верных дверей" style="display:flex;align-items:center;justify-content:space-between;gap:4px;cursor:pointer;background:#121026;padding:4px 6px;border-radius:5px;border:1px solid #2e284a;min-height:32px;box-sizing:border-box;">
              <div style="display:flex;flex-direction:column;min-width:0;overflow:hidden;line-height:1.2;">
                <span style="font-size:10px;font-weight:bold;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">🧩 Радар Данжа</span>
                <span style="font-size:8px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">плиты, руны, двери</span>
              </div>
              <input type="checkbox" id="b-quest-opt-dgn" style="cursor:pointer;accent-color:#38bdf8;flex-shrink:0;margin:0;" />
            </label>

            <!-- 7. Декодер шифра -->
            <label title="Декодер кода сокровищницы: Взлом шифра двери: ввод кода, когда осталось ≤2 неизвестных цифр" style="display:flex;align-items:center;justify-content:space-between;gap:4px;cursor:pointer;background:#121026;padding:4px 6px;border-radius:5px;border:1px solid #2e284a;min-height:32px;box-sizing:border-box;">
              <div style="display:flex;flex-direction:column;min-width:0;overflow:hidden;line-height:1.2;">
                <span style="font-size:10px;font-weight:bold;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">🔮 Декодер кода</span>
                <span style="font-size:8px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">взлом при ≤2 скрытых</span>
              </div>
              <input type="checkbox" id="b-quest-opt-solve-code" style="cursor:pointer;accent-color:#a855f7;flex-shrink:0;margin:0;" />
            </label>

            <!-- 8. Переправа -->
            <label title="Авто-Переправа (безопасные кочки): Авто-прыжок на безопасную кочку (исключая утонувшие)" style="display:flex;align-items:center;justify-content:space-between;gap:4px;cursor:pointer;background:#121026;padding:4px 6px;border-radius:5px;border:1px solid #2e284a;min-height:32px;box-sizing:border-box;">
              <div style="display:flex;flex-direction:column;min-width:0;overflow:hidden;line-height:1.2;">
                <span style="font-size:10px;font-weight:bold;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">🪵 Переправа</span>
                <span style="font-size:8px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">безопасные кочки</span>
              </div>
              <input type="checkbox" id="b-quest-opt-crossing" style="cursor:pointer;accent-color:#10b981;flex-shrink:0;margin:0;" />
            </label>

            <!-- 9. Трясина -->
            <label title="Авто-Трясина («тянем!» и «руби»): Клик «тянем» по кд раз в 2с, когда вязнем, и «руби» при атаке твари" style="display:flex;align-items:center;justify-content:space-between;gap:4px;cursor:pointer;background:#121026;padding:4px 6px;border-radius:5px;border:1px solid #2e284a;min-height:32px;box-sizing:border-box;">
              <div style="display:flex;flex-direction:column;min-width:0;overflow:hidden;line-height:1.2;">
                <span style="font-size:10px;font-weight:bold;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">🫧 Трясина</span>
                <span style="font-size:8px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">«тянем!» & «руби»</span>
              </div>
              <input type="checkbox" id="b-quest-opt-bog" style="cursor:pointer;accent-color:#38bdf8;flex-shrink:0;margin:0;" />
            </label>

            <!-- 10. Авто-Ивенты -->
            <label title="Авто-Ивенты (Котёл & Сладость/Гадость): Голосование за сладость (+конфеты) и сбор ингредиентов в котёл" style="display:flex;align-items:center;justify-content:space-between;gap:4px;cursor:pointer;background:#121026;padding:4px 6px;border-radius:5px;border:1px solid #2e284a;min-height:32px;box-sizing:border-box;">
              <div style="display:flex;flex-direction:column;min-width:0;overflow:hidden;line-height:1.2;">
                <span style="font-size:10px;font-weight:bold;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">🎃 Авто-Ивенты</span>
                <span style="font-size:8px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">котёл & конфеты</span>
              </div>
              <input type="checkbox" id="b-quest-opt-hwevents" style="cursor:pointer;accent-color:#f59e0b;flex-shrink:0;margin:0;" />
            </label>

            <!-- 11. QA-панель (на всю ширину двух колонок внизу) -->
            <label title="Разблокировка QA-панели: Принудительно ME.hero.qa = true (быстрые кнопки игры)" style="grid-column:span 2;display:flex;align-items:center;justify-content:space-between;gap:4px;cursor:pointer;background:#121026;padding:4px 6px;border-radius:5px;border:1px solid #2e284a;min-height:28px;box-sizing:border-box;">
              <div style="display:flex;flex-direction:column;min-width:0;overflow:hidden;line-height:1.2;">
                <span style="font-size:10px;font-weight:bold;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">🔓 QA-панель игры</span>
                <span style="font-size:8px;color:#94a3b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">принудительно ME.hero.qa = true (быстрые кнопки игры)</span>
              </div>
              <input type="checkbox" id="b-quest-opt-qa" style="cursor:pointer;accent-color:#a855f7;flex-shrink:0;margin:0;" />
            </label>
          </div>
        </div>

        <!-- Карточка 4: Быстрые действия боя и отряда (Классовые + Скиллы) -->
        <div style="background:#1a172e;border:1px solid #3d3759;border-radius:6px;padding:8px;margin-bottom:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <div style="display:flex;align-items:center;gap:6px;">
              <span style="font-size:11px;font-weight:bold;color:#cbd5e1;">🎯 Действия:</span>
              <span id="b-quest-class-badge" style="background:#262238;color:#ffd700;border:1px solid #4a3e72;border-radius:4px;padding:2px 6px;font-size:10px;font-weight:bold;display:inline-flex;align-items:center;gap:3px;" title="Класс персонажа (определяется автоматически)">⚔️ Легионер</span>
            </div>
            <span id="b-quest-turn-status" style="font-size:9.5px;color:#94a3b8;">Вне боя</span>
          </div>
          <!-- 4 кнопки базовых действий под класс -->
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:6px;">
            <button id="b-quest-act-atk" style="padding:6px 8px;background:#be123c;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:10px;font-weight:bold;display:flex;align-items:center;justify-content:center;gap:3px;" title="Базовая атака вашего класса">⚔️ Удар</button>
            <button id="b-quest-act-sub" style="padding:6px 8px;background:#262238;color:#fbbf24;border:1px solid #3d3759;border-radius:4px;cursor:pointer;font-size:10px;font-weight:bold;display:flex;align-items:center;justify-content:center;gap:3px;" title="Дополнительное действие / Фокус цели">👥 Цель: Рядовые</button>
            <button id="b-quest-act-def" style="padding:6px 8px;background:#1e3a8a;color:#60a5fa;border:1px solid #2563eb;border-radius:4px;cursor:pointer;font-size:10px;font-weight:bold;display:flex;align-items:center;justify-content:center;gap:3px;" title="Отправить слово блока / парирование">🛡️ Блок</button>
            <button id="b-quest-act-squad" style="padding:6px 8px;background:#262238;color:#fbbf24;border:1px solid #3d3759;border-radius:4px;cursor:pointer;font-size:10px;font-weight:bold;display:flex;align-items:center;justify-content:center;gap:3px;" title="Вступить или выйти из отряда">🏃 Отряд</button>
          </div>

          <!-- Секция: Боевые скиллы персонажа -->
          <div style="border-top:1px dashed #2e284a;padding-top:6px;margin-top:2px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;">
              <span style="font-size:10px;font-weight:bold;color:#fcd34d;display:flex;align-items:center;gap:3px;">
                <span>⚡</span>
                <span id="b-quest-skills-title">Боевые скиллы класса</span>
              </span>
              <span id="b-quest-skills-count" style="font-size:9px;color:#94a3b8;">0 доступно</span>
            </div>
            <div id="b-quest-skills-list" style="display:flex;flex-wrap:wrap;gap:4px;">
              <span style="font-size:9.5px;color:#64748b;font-style:italic;">Скиллы станут доступны во время боя</span>
            </div>
          </div>
        </div>

        <!-- Карточка 5: Журнал событий похода -->
        <div style="background:#1a172e;border:1px solid #3d3759;border-radius:6px;padding:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px;">
            <span style="font-size:10.5px;font-weight:bold;color:#94a3b8;">📜 Журнал похода</span>
            <button id="b-quest-clear-log" style="padding:1px 6px;background:#262238;color:#ef4444;border:1px solid #3d3759;border-radius:3px;cursor:pointer;font-size:9px;" title="Очистить журнал">🗑️</button>
          </div>
          <div id="b-quest-log-list" style="max-height:130px;overflow-y:auto;display:flex;flex-direction:column;gap:3px;font-size:10px;color:#cbd5e1;padding-right:2px;">
            <div style="color:#64748b;font-style:italic;">Журнал пуст</div>
          </div>
        </div>
      </div>

      <!-- Контейнер вкладки: Приоритеты добычи (Руда / Сундуки) -->
      <div id="b-tab-priority" style="display:none;width:100%;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;padding-bottom:5px;border-bottom:1px solid #3d3759;">
          <b style="color:#eab308;font-size:11.5px;">🎯 Приоритеты добычи</b>
          <button id="b-prio-reset" style="background:#262238;border:1px solid #3d3759;color:#94a3b8;border-radius:4px;padding:2px 7px;cursor:pointer;font-size:9.5px;font-weight:bold;transition:all 0.2s;"  title="Сбросить к стандарту (Железо выше Угля)">🔄 По умолч.</button>
        </div>
        <div style="font-size:10px;color:#94a3b8;line-height:1.4;margin-bottom:8px;background:rgba(234,179,8,0.06);border:1px solid rgba(234,179,8,0.18);border-radius:5px;padding:5px 7px;max-width:320px;white-space:normal;word-break:break-word;">
          💡 Перетаскивайте карточки (<b>Drag & Drop</b>) или кнопками <b>▲ ▼</b>.<br>Снимите галочку <b>[ ]</b>, чтобы бот не бил этот ресурс.
        </div>
        <div id="b-priority-list" style="display:flex;flex-direction:column;gap:4px;max-height:260px;overflow-y:auto;padding-right:2px;">
          <!-- Карточки генерируются скриптом -->
        </div>
        <div style="margin-top:6px;padding-top:4px;border-top:1px dashed rgba(255,255,255,0.08);display:flex;justify-content:space-between;align-items:center;">
          <span style="font-size:9.5px;color:#64748b;">Железо по умолч. выше Угля</span>
          <span style="font-size:9.5px;color:#4ade80;">💾 Сохранено</span>
        </div>
      </div>

      <!-- Контакты / Подвал -->
      <div id="b-footer" style="margin-top:8px;padding-top:6px;border-top:1px solid #3d3759;display:flex;justify-content:center;align-items:center;gap:5px;font-size:10px;color:#8899aa;user-select:text;">
        <span>Связь:</span>
        <a href="https://t.me/daniil63799" target="_blank" rel="noopener noreferrer" style="color:#38bdf8;text-decoration:none;font-weight:bold;display:inline-flex;align-items:center;gap:3px;"  title="Написать в Telegram @daniil63799">
          <span>✈️ Тг @daniil63799</span>
        </a>
      </div>
    </div>
  `;
  document.body.appendChild(w);

  const st = w.querySelector('#b-st'), btn = w.querySelector('#b-btn'), dot = w.querySelector('#b-dot');
  const btnScreen = w.querySelector('#b-mode-screen'), btnGhost = w.querySelector('#b-mode-ghost');
  const checkScreen = w.querySelector('#b-check-screen'), checkGhost = w.querySelector('#b-check-ghost');
  const mineSelect = w.querySelector('#b-mine-select'), btnRefreshMines = w.querySelector('#b-refresh-mines');
  const checkRatingMode = w.querySelector('#b-rating-mode');
  const checkOreFirst = w.querySelector('#b-ore-first');
  const checkCoalIronFirst = w.querySelector('#b-coal-iron-first');
  const coalIronLabel = w.querySelector('#b-coal-iron-label');
  const checkDrillCollect = w.querySelector('#b-drill-collect');
  const checkDrillBuild = w.querySelector('#b-drill-build');
  const checkDrillBuildAllFloors = w.querySelector('#b-drill-all-floors');
  const checkAutoClaimQuests = w.querySelector('#b-auto-claim-quests');
  const checkAutoLift = w.querySelector('#b-auto-lift');

  const ratingCrowdRow = w.querySelector('#b-rating-crowd-row');
  const ratingCrowdSelect = w.querySelector('#b-rating-crowd-select');
  if (ratingCrowdSelect) {
    ratingCrowdSelect.onchange = () => {
      ratingCrowdRange = ratingCrowdSelect.value;
      localStorage.setItem('lg_bot_rating_crowd', ratingCrowdRange);
      lastTargetKey = null;
      showToast(`🏆 Рейтинг: поиск камней (${ratingCrowdSelect.options[ratingCrowdSelect.selectedIndex].text})`);
    };
  }

  if (checkRatingMode) {
    checkRatingMode.onchange = () => {
      isRatingMode = checkRatingMode.checked;
      localStorage.setItem('lg_bot_rating_mode', isRatingMode ? '1' : '0');
      if (ratingCrowdRow) {
        ratingCrowdRow.style.display = isRatingMode ? 'flex' : 'none';
      }
      if (isRatingMode) {
        // При включении Режима «Рейтинг» автоматически отключаем «Сперва руда»
        if (checkOreFirst) {
          checkOreFirst.checked = false;
          oreFirst = false;
          localStorage.setItem('lg_bot_ore_first', '0');
          if (coalIronLabel) {
            coalIronLabel.style.opacity = '0.5';
          }
        }
        lastTargetKey = null;
      }
      showToast(isRatingMode ? `🏆 Режим «Рейтинг» (${ratingCrowdRange} чел): включен` : 'Режим «Рейтинг»: выключен');
    };
  }

  if (checkOreFirst) {
    checkOreFirst.onchange = () => {
      oreFirst = checkOreFirst.checked;
      localStorage.setItem('lg_bot_ore_first', oreFirst ? '1' : '0');
      if (coalIronLabel) {
        coalIronLabel.style.opacity = oreFirst ? '1' : '0.5';
      }
      if (oreFirst) {
        // При включении «Сперва руда» автоматически отключаем Режим «Рейтинг»
        if (checkRatingMode) {
          checkRatingMode.checked = false;
          isRatingMode = false;
          localStorage.setItem('lg_bot_rating_mode', '0');
        }
      }
      showToast(oreFirst ? '💎 Режим «Сперва руда»: включен' : 'Режим «Сперва руда»: выключен');
    };
  }
  if (checkCoalIronFirst) {
    checkCoalIronFirst.onchange = () => {
      coalIronFirst = checkCoalIronFirst.checked;
      localStorage.setItem('lg_bot_coal_iron_first', coalIronFirst ? '1' : '0');
    };
  }
  if (checkDrillCollect) {
    checkDrillCollect.onchange = () => {
      isDrillCollect = checkDrillCollect.checked;
      localStorage.setItem('lg_bot_drill_collect', isDrillCollect ? 'true' : 'false');
      showToast(isDrillCollect ? '🚜 Автобур: сбор руды включён' : 'Автобур: сбор руды выключен');
      updateDrillsUI();
    };
  }
  if (checkDrillBuild) {
    checkDrillBuild.onchange = () => {
      isDrillBuild = checkDrillBuild.checked;
      localStorage.setItem('lg_bot_drill_build', isDrillBuild ? 'true' : 'false');
      showToast(isDrillBuild ? '🏗️ Авто-установка буров ВКЛ (взнос 100м)' : 'Авто-установка буров выключена');
      if (!isPaused && isDrillBuild) processDrillAutoFund();
    };
  }
  if (checkDrillBuildAllFloors) {
    checkDrillBuildAllFloors.onchange = () => {
      isDrillBuildAllFloors = checkDrillBuildAllFloors.checked;
      localStorage.setItem('lg_bot_drill_build_all_floors', isDrillBuildAllFloors ? 'true' : 'false');
      showToast(isDrillBuildAllFloors
        ? '🌐 Авто улучшение буров всех этажей ВКЛ (Взнос 100 монет)'
        : 'Авто улучшение буров всех этажей выключено');
      if (!isPaused && isDrillBuildAllFloors) {
        lastDrillsScanTime = 0;
        scanMyDrillsServerWide();
      }
    };
  }
  const myDrillsEl = w.querySelector('#b-my-drills-info');
  if (myDrillsEl) {
    myDrillsEl.onclick = () => {
      showToast('🔄 Сканирование буров на всех этажах шахты…');
      scanMyDrillsServerWide(true); // Только чтение (без отправки монет)
    };
  }
  if (checkAutoClaimQuests) {
    checkAutoClaimQuests.onchange = () => {
      autoClaimQuests = checkAutoClaimQuests.checked;
      localStorage.setItem('lg_bot_auto_claim_quests', autoClaimQuests ? 'true' : 'false');
      showToast(autoClaimQuests ? '🎁 Автосбор поручений ВКЛЮЧЁН' : 'Автосбор поручений выключен');
      if (!isPaused && autoClaimQuests) tryAutoClaimMineQuests();
    };
  }
  if (checkAutoLift) {
    checkAutoLift.onchange = () => {
      autoLiftBestFloor = checkAutoLift.checked;
      localStorage.setItem('lg_bot_auto_lift', autoLiftBestFloor ? 'true' : 'false');
      showToast(autoLiftBestFloor ? '🛗 Авто-лифт ВКЛ (раз в 2 мин, порог +30% людей)' : 'Авто-лифт выключен');
      if (!isPaused && autoLiftBestFloor) {
        lastLiftCheckTime = 0;
        checkAndSelectBestFloor();
      }
    };
  }


  // 🛡️ СТЕЛС-ПРОВЕРКА: Персонаж физически находится в шахте (а не в городе, не в таверне и не в ghost-режиме)
  function isPlayerInMine() {
    if (mode === 'ghost') return false;
    const mnWorld = document.querySelector('.mn-world');
    const mnEl = document.querySelector('.mn');
    if (!mnWorld || !mnEl) return false;
    const otherEl = document.querySelector('.mn-other');
    if (otherEl && !otherEl.hidden) return false;
    if (!G.__lgMineS) return false;
    return true;
  }

  // ---------- ЛОГИКА АВТОБУРОВ (ШАХТА 3.0) ----------
  function getDrillsList() {
    const S = G.__lgMineS;
    if (S && S.st && Array.isArray(S.st.drills)) return S.st.drills;
    if (G.__lgMineState && Array.isArray(G.__lgMineState.drills)) return G.__lgMineState.drills;
    return [];
  }

  function updateDrillsUI() {
    const drills = getDrillsList();
    const infoEl = w.querySelector('#b-drill-info');
    if (!infoEl) return;
    if (!drills || drills.length === 0) {
      infoEl.textContent = '0 буров';
      infoEl.style.color = '#a78bfa';
      return;
    }
    let active = 0;
    let totalRate = 0;
    for (const d of drills) {
      const stg = d[3];
      const lv = +d[4] || 0;
      const rate = +d[10] || 0;
      if (stg === 'work' || (stg === 'build' && lv > 0)) {
        active++;
        totalRate += rate;
      }
    }
    if (active > 0) {
      infoEl.textContent = `${active} актив. (${totalRate > 0 ? `~${totalRate}/ч` : 'добыча'})`;
      infoEl.style.color = '#38bdf8';
    } else {
      infoEl.textContent = `${drills.length} буров`;
      infoEl.style.color = '#a78bfa';
    }

    const myDrillsEl = w.querySelector('#b-my-drills-info');
    if (myDrillsEl) {
      if (myFundedDrillsCount > 0) {
        myDrillsEl.textContent = `Наших: ${myFundedDrillsCount} шт.`;
        myDrillsEl.style.color = '#38bdf8';
        myDrillsEl.title = `Вы внесли взнос в ${myFundedDrillsCount} буровых вышек шахты (${myFundedDrillsTotalCoins} монет). Бонус строителя рудой активен! Опрос сервера каждые 5 мин. Нажмите для обновления.`;
      } else {
        const logins = getMyLoginsSet();
        let curFloorMy = 0;
        let curFloorCoins = 0;
        for (const d of drills) {
          const myInfo = isMyFundedDrill(d, logins);
          if (myInfo) {
            curFloorMy++;
            curFloorCoins += myInfo.coins;
          }
        }
        if (curFloorMy > 0) {
          myDrillsEl.textContent = `Наших: ${curFloorMy} шт.`;
          myDrillsEl.style.color = '#38bdf8';
          myDrillsEl.title = `На текущем этаже внесён взнос в ${curFloorMy} буровых (${curFloorCoins}м). Опрос всей шахты каждые 5 мин. Нажмите для обновления.`;
        } else {
          myDrillsEl.textContent = 'Наших: 0 шт.';
          myDrillsEl.style.color = 'rgba(255,255,255,0.4)';
          myDrillsEl.title = 'Пока нет буровых с вашим взносом. Бот автоматически внесет 100м при появлении новой площадки. Нажмите для обновления.';
        }
      }
    }

    // Если включен авто-сбор, проверяем награды скупщика (/api/mineClaim)
    if (isDrillCollect) {
      const claimBtn = w.querySelector('#b-mine-claim-btn');
      if (claimBtn && claimBtn.style.display !== 'none' && !claimBtn.disabled && !claimBtn.textContent.includes('Забир')) {
        claimBtn.click();
      }
    }
  }

  let lastDrillsScanTime = 0;
  let isScanningDrills = false;

  let isFundingDrill = false;
  async function processDrillAutoFund() {
    if (isPaused || !isDrillBuild || isFundingDrill) return;
    isFundingDrill = true;
    try {
      await processDrillAutoFundInner();
    } finally {
      isFundingDrill = false;
    }
  }

  async function processDrillAutoFundInner() {
    if (isPaused || !isDrillBuild) return;
    // 🛡️ СТЕЛС: Взаимодействие с буровой разрешено СТРОГО только когда персонаж физически в шахте (не в Городе!)
    if (!isPlayerInMine()) return;

    const drills = getDrillsList();
    if (!drills || drills.length === 0) return;

    let myCoins = getMyCoins();
    if (myCoins < 100) return;

    const myHero = (G.ME && G.ME.hero) || (G.LGSiteUi && G.LGSiteUi.hero && G.LGSiteUi.hero()) || G.__lgHero;
    const myNick = (myHero && (myHero.n || myHero.name)) || '';

    for (const d of drills) {
      const [k, , , stg, lv, c, need, , , , , res, by] = d;
      if (!k) continue;
      // site (площадка под установку) или work с неполным сбором на следующий уровень (lv < 3)
      if ((stg === 'site' || stg === 'work') && (+lv || 0) < 3) {
        const left = Math.max(0, (+need || 0) - (+c || 0));
        if (left <= 0) continue;

        const drillKey = String(k);
        if (fundedDrills.has(drillKey)) continue;

        const alreadyFunded = myNick && Array.isArray(by) && by.some(item => Array.isArray(item) && item[0] && String(item[0]).toLowerCase() === myNick.toLowerCase());
        if (alreadyFunded) {
          fundedDrills.add(drillKey);
          continue;
        }

        const fundAmount = Math.min(100, left);
        if (myCoins >= fundAmount) {
          fundedDrills.add(drillKey);
          const channelToken = String(currentMineId || 'home').split('.')[0];
          try {
            const resDrill = await gameApi('/api/mineDrill', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                c: channelToken,
                k: drillKey,
                coins: fundAmount
              })
            });
            if (resDrill && resDrill.ok) {
              showToast(`🏗️ Авто-установка: внесён взнос ${fundAmount}м в буровую (${res || 'руда'})! Бонус строителя зафиксирован.`);
              myFundedDrillsCount++;
              myFundedDrillsTotalCoins += fundAmount;
              saveFundedDrillsState();
              updateDrillsUI();
              refreshMyCoinsFromApi(true);
              myCoins -= fundAmount;
            } else {
              fundedDrills.delete(drillKey);
            }
          } catch (e) {
            fundedDrills.delete(drillKey);
          }
        }
      }
    }
  }
  const bMin = w.querySelector('#b-min'), bBody = w.querySelector('#b-body');
  const bCollapseHint = w.querySelector('#b-collapse-hint');
  const bMiniSummary = w.querySelector('#b-mini-summary');
  const bHeader = w.querySelector('#b-header');
  const bSquareBtn = w.querySelector('#b-square-btn');
  const bSquareBadge = w.querySelector('#b-square-badge');
  const bSquareDot = w.querySelector('#b-square-dot');
  const bSquareDealBadge = w.querySelector('#b-square-deal-badge');
  const bMiniDealFire = w.querySelector('#b-mini-deal-fire');
  let currentTopDeal = null;

  function updateMiniDealUI() {
    const isLineActive = (typeof isSquare !== 'undefined' && typeof isHoveredFromSquare !== 'undefined' && isSquare && isHoveredFromSquare) ||
                         (typeof isSquare !== 'undefined' && typeof isMinimized !== 'undefined' && !isSquare && isMinimized);
    if (currentTopDeal && currentTopDeal.ev && currentTopDeal.lot) {
      const top = currentTopDeal;
      const itemName = top.ev.name + (top.ev.en > 0 ? ' +' + top.ev.en : '');
      const shortPrice = formatCoins(top.ev.price);
      if (bMiniDealFire) {
        bMiniDealFire.title = `🔥 Выгодно (50%+): ${itemName} за ${shortPrice} (−${top.ev.discount}%) · Клик — открыть Рынок`;
        bMiniDealFire.style.display = isLineActive ? 'inline' : 'none';
      }
      if (bSquareDealBadge) {
        bSquareDealBadge.style.display = (typeof isSquare !== 'undefined' && typeof isHoveredFromSquare !== 'undefined' && isSquare && !isHoveredFromSquare) ? 'block' : 'none';
      }
    } else {
      if (bMiniDealFire) bMiniDealFire.style.display = 'none';
      if (bSquareDealBadge) bSquareDealBadge.style.display = 'none';
    }
  }
  const pMineWrap = w.querySelector('#b-mine-progress-wrap');
  const pMinePct = w.querySelector('#b-mine-progress-pct');
  const pMineTime = w.querySelector('#b-mine-progress-time');
  const pMineBar = w.querySelector('#b-mine-progress-bar');

  // Получение актуального индекса копаемого камня из всех источников игры
  function getActiveMiningRockIndex() {
    try {
      const st = G.__lgMineState || (G.__lgMineS && G.__lgMineS.st);
      // 1. Из живого объекта движка игры S.myI
      if (G.__lgMineS && typeof G.__lgMineS.myI === 'number' && G.__lgMineS.myI >= 0) {
        if (!st || !st.dug || st.dug[G.__lgMineS.myI] !== '1') {
          return G.__lgMineS.myI;
        }
      }
      // 2. Из полоски .mn-bar.me
      const myBar = document.querySelector('.mn-bar.me, .mn .mn-bar.me');
      if (myBar) {
        const bx = parseFloat(myBar.style.left) || 0;
        const by = parseFloat(myBar.style.top) || 0;
        const bIdx = getCellIndexFromBarPosition(bx, by);
        if (bIdx >= 0 && (!st || !st.dug || st.dug[bIdx] !== '1')) return bIdx;
      }
      // 3. Из состояния st.dg по нашему нику
      if (st && st.dg) {
        const myLogin = (G.ME?.user?.login || G.__lgMineMe?.name || getMyNick() || '').toLowerCase();
        if (myLogin) {
          for (const [k, list] of Object.entries(st.dg)) {
            if (Array.isArray(list) && list.some(l => String(l).toLowerCase() === myLogin)) {
              const idx = parseInt(k, 10);
              if (!st.dug || st.dug[idx] !== '1') return idx;
            }
          }
        }
      }
      // 4. Только если мы в режиме Ghost (нет экрана шахты), используем снимок героя
      const isScreen = Boolean(document.querySelector('.mn') || G.__lgMineS);
      if (!isScreen) {
        const cId = currentMineId || 'home';
        const heroMn = G.ME?.hero?.mn;
        if (heroMn && heroMn[cId] && typeof heroMn[cId].i === 'number' && heroMn[cId].i >= 0) {
          const mnSnap = heroMn[cId];
          const mnFloor = mnSnap.f || mnSnap.at;
          const isOldFloorSnap = (st && st.n != null && mnFloor != null && mnFloor < st.n);
          if (!isOldFloorSnap && (!st || !st.dug || st.dug[heroMn[cId].i] !== '1')) {
            return heroMn[cId].i;
          }
        }
      }
    } catch (e) {}
    return -1;
  }

  // Ежесекундный плавный таймер обновления прогресса и обратного отсчёта
  let liveProgressTimer = null;
  function startLiveProgressTicker() {
    if (liveProgressTimer) clearInterval(liveProgressTimer);
    liveProgressTimer = setInterval(() => {
      if (!G.__mineBotRunning) return;

      const mining = isMining();
      if (!mining) {
        if (Date.now() - lastMiningConfirmedTime > 2500) {
          activeMiningRockIndex = -1;
          lastKnownMiningProgress = null;
          if (pMineWrap && pMineWrap.style.display !== 'none') pMineWrap.style.display = 'none';
          if (isPaused) {
            if (st && (!st.textContent || st.textContent.includes('Копаем') || st.textContent.includes('Добиваем') || st.textContent === 'Работает')) {
              st.textContent = mode === 'ghost' ? `Город: ${getMineLabel(currentMineId)} (Пауза)` : `На паузе (${getMineLabel(currentMineId)})`;
              st.style.color = '#ffc107';
            }
          } else if (isDailyMiningCapReached()) {
            st.textContent = '⏳ Лимит 10ч исчерпан (Сброс в 05:00) · Буры и лифт активны';
            st.style.color = '#ffd700';
          }
          if (typeof updateHeaderProgressUI === 'function') updateHeaderProgressUI();
        }
        return;
      }

      let idx = getActiveMiningRockIndex();
      if (idx >= 0) {
        activeMiningRockIndex = idx;
        let prog = getRockMiningProgress(idx);
        if (prog) {
          lastKnownMiningProgress = { p: prog, at: Date.now() };
        } else if (lastKnownMiningProgress && Date.now() - lastKnownMiningProgress.at < 2500) {
          prog = lastKnownMiningProgress.p;
        }
        if (prog) {
          if (!prog.rate || prog.rate <= 0 || Date.now() - lastMineStatePoll > 8000) {
            refreshMineStateFromServer();
          }
          if (pMineWrap && pMinePct && pMineTime && pMineBar) {
            pMineWrap.style.display = 'block';
            pMinePct.textContent = `${prog.remPct}%`;
            pMineTime.textContent = prog.etaText;
            pMineBar.style.width = `${prog.remPct}%`;
            const hpC = prog.remPct > 50
              ? (prog.remPct > 75 ? '#5fd35f' : 'linear-gradient(90deg, #ffd23f, #5fd35f)')
              : (prog.remPct > 20 ? 'linear-gradient(90deg, #ff903e, #ffd23f)' : '#ff4d3d');
            pMineBar.style.background = hpC;
            pMinePct.style.color = prog.remPct > 50 ? '#5fd35f' : (prog.remPct > 20 ? '#ffd23f' : '#ff4d3d');
          }
          const rockName = formatRockName(idx) || 'Камень';
          if (isPaused) {
            st.textContent = `⏸️ Копаем: ${rockName} · ост. ${prog.remPct}% (${prog.etaText}) ⛏️`;
            st.style.color = '#ffd700';
          } else {
            if (st && st.textContent && (st.textContent.includes('Копаем') || st.textContent.includes('Добиваем'))) {
              const reProg = /\s*·\s*(?:ост\.\s*)?\d+%\s*\([^)]*\)\s*⛏️?$/;
              let baseTxt = st.textContent.replace(reProg, '').trim();
              const pickIdx = baseTxt.lastIndexOf('⛏');
              if (pickIdx >= 0) baseTxt = baseTxt.slice(0, pickIdx).trim();
              st.textContent = `${baseTxt} · ост. ${prog.remPct}% (${prog.etaText}) ⛏️`;
            } else if (st) {
              const prefix = mode === 'ghost' ? '🏙️ ' : '';
              if (isRatingMode) {
                const countLabel = (typeof myCount === 'number' && myCount > 0) ? ` (${myCount} чел)` : '';
                st.textContent = `${prefix}🏆 [Рейтинг] Добиваем: ${rockName}${countLabel} · ост. ${prog.remPct}% (${prog.etaText}) ⛏️`;
                st.style.color = '#ffd700';
              } else {
                st.textContent = `${prefix}Копаем: ${rockName} · ост. ${prog.remPct}% (${prog.etaText}) ⛏️`;
                st.style.color = '#00ff66';
              }
            }
          }
        }
      } else {
        const statusEl = document.querySelector('[data-mn="status"]');
        if (statusEl && statusEl.textContent && statusEl.textContent.toLowerCase().includes('копаешь')) {
          const txt = statusEl.textContent.trim();
          if (isPaused) {
            st.textContent = `⏸️ ${txt} ⛏️`;
            st.style.color = '#ffd700';
          }
          if (pMineWrap) pMineWrap.style.display = 'block';
        }
        if (Date.now() - lastMineStatePoll > 5000) {
          refreshMineStateFromServer();
        }
      }
      if (typeof updateHeaderProgressUI === 'function') updateHeaderProgressUI();
    }, 1000);
  }
  startLiveProgressTicker();

  // Элементы вкладок
  const tabBtnMine = w.querySelector('#b-tab-btn-mine'), tabBtnMarket = w.querySelector('#b-tab-btn-market');
  const mineClaimBtn = w.querySelector('#b-mine-claim-btn');
  const tabBtnPlayers = w.querySelector('#b-tab-btn-players'), tabBtnBroadcast = w.querySelector('#b-tab-btn-broadcast'), tabBtnCity = w.querySelector('#b-tab-btn-city'), tabBtnQuest = w.querySelector('#b-tab-btn-quest'), tabBtnPriority = w.querySelector('#b-tab-btn-priority');
  const tabMine = w.querySelector('#b-tab-mine'), tabMarket = w.querySelector('#b-tab-market');
  const tabPlayers = w.querySelector('#b-tab-players'), tabBroadcast = w.querySelector('#b-tab-broadcast'), tabCity = w.querySelector('#b-tab-city'), tabQuest = w.querySelector('#b-tab-quest'), tabPriority = w.querySelector('#b-tab-priority');
  const aucBadge = w.querySelector('#b-auc-badge'), aucList = w.querySelector('#b-auc-list');
  const questBadge = w.querySelector('#b-quest-badge');
  const aucRefresh = w.querySelector('#b-auc-refresh'), aucFilter = w.querySelector('#b-auc-filter');
  const aucSearch = w.querySelector('#b-auc-search'), aucStatus = w.querySelector('#b-auc-status');
  const aucClaimBtn = w.querySelector('#b-auc-claim-btn');
  const bAucAutoBuy = w.querySelector('#b-auc-autobuy');
  let isAutoBuy = false;
  let isAutoBuyInProgress = false;
  const attemptedAutoBuyLots = new Set();

  // Элементы вкладки игроков
  const playersBadge = w.querySelector('#b-players-badge');
  const playersRefresh = w.querySelector('#b-players-refresh');
  const playersClearBtn = w.querySelector('#b-players-clear');
  const playersSearch = w.querySelector('#b-players-search');
  const playersStatus = w.querySelector('#b-players-status');
  const playersList = w.querySelector('#b-players-list');
  const playersViewList = w.querySelector('#b-players-view-list');
  const playersViewDossier = w.querySelector('#b-players-view-dossier');
  const dossierBack = w.querySelector('#b-dossier-back');
  const dossierNick = w.querySelector('#b-dossier-nick');
  const dossierContent = w.querySelector('#b-dossier-content');

  // Мигание заголовка вкладки полностью отключено по запросу пользователя
  let originalDocumentTitle = (document.title || '').replace(/🔥\s*\[СКИДКА[^\]]+\]\s*/g, '');
  if (document.title && document.title.includes('СКИДКА')) {
    document.title = originalDocumentTitle;
  }
  let titleBlinkInterval = null;

  function setSuperDealTitle(dealCount) {
    if (titleBlinkInterval) {
      clearInterval(titleBlinkInterval);
      titleBlinkInterval = null;
    }
    if (document.title && document.title.includes('СКИДКА')) {
      document.title = originalDocumentTitle;
    }
  }

  let activeTab = localStorage.getItem('lg_bot_tab') || 'mine';
  if (activeTab === 'settings') activeTab = 'mine';

  function applyActiveTab() {
    [tabBtnMine, tabBtnMarket, tabBtnPlayers, tabBtnBroadcast, tabBtnCity, tabBtnQuest, tabBtnPriority].forEach(btn => {
      if (btn) {
        btn.style.background = '#262238';
        btn.style.color = '#aaa';
        btn.style.border = '1px solid #3d3759';
      }
    });
    [tabMine, tabMarket, tabPlayers, tabBroadcast, tabCity, tabQuest, tabPriority].forEach(t => {
      if (t) t.style.display = 'none';
    });
    if (!isMinimized && !isSquare) {
      w.style.maxWidth = (activeTab === 'broadcast' ? '370px' : 'none');
    }

    if (activeTab === 'market') {
      tabBtnMarket.style.background = '#007bff';
      tabBtnMarket.style.color = '#fff';
      tabBtnMarket.style.border = 'none';
      tabMarket.style.display = 'block';
      if (!isMinimized && !isSquare) w.style.minWidth = '310px';
      fetchAuctionData();
    } else if (activeTab === 'players') {
      tabBtnPlayers.style.background = '#9b59b6';
      tabBtnPlayers.style.color = '#fff';
      tabBtnPlayers.style.border = 'none';
      tabPlayers.style.display = 'block';
      if (!isMinimized && !isSquare) w.style.minWidth = '310px';
      if (typeof scanPlayers === 'function') scanPlayers();
    } else if (activeTab === 'broadcast') {
      tabBtnBroadcast.style.background = '#d946ef';
      tabBtnBroadcast.style.color = '#fff';
      tabBtnBroadcast.style.border = 'none';
      tabBroadcast.style.display = 'block';
      if (!isMinimized && !isSquare) {
        w.style.minWidth = '310px';
        w.style.maxWidth = '350px';
      }
      if (typeof scanPlayers === 'function') scanPlayers(true);
      if (typeof updateBroadcastRecipientsCount === 'function') updateBroadcastRecipientsCount();
    } else if (activeTab === 'city') {
      tabBtnCity.style.background = '#f59e0b';
      tabBtnCity.style.color = '#000';
      tabBtnCity.style.border = 'none';
      tabCity.style.display = 'block';
      if (!isMinimized && !isSquare) w.style.minWidth = '310px';
      if (typeof initPocketCity === 'function') initPocketCity();
    } else if (activeTab === 'quest') {
      tabBtnQuest.style.background = '#e11d48';
      tabBtnQuest.style.color = '#fff';
      tabBtnQuest.style.border = 'none';
      tabQuest.style.display = 'block';
      if (!isMinimized && !isSquare) w.style.minWidth = '310px';
      if (typeof refreshQuestUI === 'function') refreshQuestUI();
    } else if (activeTab === 'priority') {
      if (tabBtnPriority) {
        tabBtnPriority.style.background = '#eab308';
        tabBtnPriority.style.color = '#fff';
        tabBtnPriority.style.border = 'none';
      }
      if (tabPriority) tabPriority.style.display = 'block';
      if (!isMinimized && !isSquare) {
        w.style.minWidth = '310px';
        w.style.maxWidth = '350px';
      }
      renderPriorityListUI();
    } else {
      activeTab = 'mine';
      tabBtnMine.style.background = '#28a745';
      tabBtnMine.style.color = '#fff';
      tabBtnMine.style.border = 'none';
      tabMine.style.display = 'block';
      if (!isMinimized && !isSquare) w.style.minWidth = '250px';
    }
    localStorage.setItem('lg_bot_tab', activeTab);
  }

  tabBtnMine.onclick = () => { activeTab = 'mine'; applyActiveTab(); };
  tabBtnMarket.onclick = () => { activeTab = 'market'; applyActiveTab(); };
  tabBtnPlayers.onclick = () => { activeTab = 'players'; applyActiveTab(); };
  tabBtnBroadcast.onclick = () => { activeTab = 'broadcast'; applyActiveTab(); };
  tabBtnCity.onclick = () => { activeTab = 'city'; applyActiveTab(); };
  tabBtnQuest.onclick = () => { activeTab = 'quest'; applyActiveTab(); };

  if (tabBtnPriority) {
    tabBtnPriority.onclick = () => {
      activeTab = 'priority';
      localStorage.setItem('lg_bot_active_tab', 'priority');
      applyActiveTab();
    };
  }

  const openPrioTabBtn = w.querySelector('#b-open-prio-tab');
  if (openPrioTabBtn) {
    openPrioTabBtn.onclick = () => {
      activeTab = 'priority';
      localStorage.setItem('lg_bot_active_tab', 'priority');
      applyActiveTab();
    };
  }

  const prioResetBtn = w.querySelector('#b-prio-reset');
  if (prioResetBtn) {
    prioResetBtn.onclick = () => {
      customPriorities = DEFAULT_ORE_PRIORITIES.map(x => ({ ...x }));
      saveCustomPriorities(customPriorities);
      renderPriorityListUI();
      showToast('🔄 Приоритеты сброшены по умолчанию (Железо выше Угля)');
    };
  }

  function renderPriorityListUI() {
    const listEl = w.querySelector('#b-priority-list');
    if (!listEl) return;
    listEl.innerHTML = '';

    customPriorities.forEach((item, idx) => {
      const card = document.createElement('div');
      card.className = 'b-prio-item';
      card.dataset.id = item.id;
      card.setAttribute('draggable', 'true');
      card.style.cssText = 'display:flex;align-items:center;justify-content:space-between;background:#1e1b2e;border:1px solid #3d3759;border-radius:6px;padding:5px 8px;cursor:grab;user-select:none;transition:all 0.15s ease;';

      card.innerHTML = `
        <div style="display:flex;align-items:center;gap:7px;">
          <span style="color:#64748b;font-size:12px;cursor:grab;" title="Потяните для изменения порядка">☰</span>
          <span style="display:inline-block;min-width:18px;height:18px;line-height:18px;text-align:center;background:rgba(255,255,255,0.08);border-radius:9px;font-size:10px;font-weight:bold;color:#cbd5e1;">#${idx + 1}</span>
          <input type="checkbox" class="b-prio-check" data-id="${item.id}" ${item.enabled ? 'checked' : ''} style="cursor:pointer;accent-color:#eab308;width:14px;height:14px;" title="Включить / исключить из добычи">
          <span style="font-size:11.5px;font-weight:bold;color:${item.color};opacity:${item.enabled ? '1' : '0.45'};">${item.name}</span>
        </div>
        <div style="display:flex;gap:3px;">
          <button class="b-prio-up" data-id="${item.id}" ${idx === 0 ? 'disabled' : ''} style="background:#262238;border:1px solid #3d3759;color:#e2e8f0;border-radius:4px;padding:2px 6px;cursor:${idx === 0 ? 'default' : 'pointer'};font-size:10px;opacity:${idx === 0 ? '0.3' : '1'};" title="Поднять выше">▲</button>
          <button class="b-prio-down" data-id="${item.id}" ${idx === customPriorities.length - 1 ? 'disabled' : ''} style="background:#262238;border:1px solid #3d3759;color:#e2e8f0;border-radius:4px;padding:2px 6px;cursor:${idx === customPriorities.length - 1 ? 'default' : 'pointer'};font-size:10px;opacity:${idx === customPriorities.length - 1 ? '0.3' : '1'};" title="Опустить ниже">▼</button>
        </div>
      `;

      listEl.appendChild(card);
    });

    setupPriorityDragAndDrop(listEl);

    listEl.querySelectorAll('.b-prio-up').forEach(btn => {
      btn.onclick = (e) => {
        e.stopPropagation();
        const id = btn.dataset.id;
        const idx = customPriorities.findIndex(x => x.id === id);
        if (idx > 0) {
          const [moved] = customPriorities.splice(idx, 1);
          customPriorities.splice(idx - 1, 0, moved);
          saveCustomPriorities(customPriorities);
          renderPriorityListUI();
        }
      };
    });

    listEl.querySelectorAll('.b-prio-down').forEach(btn => {
      btn.onclick = (e) => {
        e.stopPropagation();
        const id = btn.dataset.id;
        const idx = customPriorities.findIndex(x => x.id === id);
        if (idx >= 0 && idx < customPriorities.length - 1) {
          const [moved] = customPriorities.splice(idx, 1);
          customPriorities.splice(idx + 1, 0, moved);
          saveCustomPriorities(customPriorities);
          renderPriorityListUI();
        }
      };
    });

    listEl.querySelectorAll('.b-prio-check').forEach(chk => {
      chk.onchange = (e) => {
        e.stopPropagation();
        const id = chk.dataset.id;
        const item = customPriorities.find(x => x.id === id);
        if (item) {
          item.enabled = chk.checked;
          saveCustomPriorities(customPriorities);
          renderPriorityListUI();
          showToast(item.enabled ? `✅ ${item.name}: включен в добычу` : `❌ ${item.name}: отключен (пропускаем)`);
        }
      };
    });
  }

  let draggedPrioCard = null;
  function setupPriorityDragAndDrop(container) {
    const cards = container.querySelectorAll('.b-prio-item');
    cards.forEach(card => {
      card.addEventListener('dragstart', (e) => {
        draggedPrioCard = card;
        card.style.opacity = '0.4';
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', card.dataset.id);
      });

      card.addEventListener('dragend', () => {
        draggedPrioCard = null;
        card.style.opacity = '1';
        cards.forEach(c => c.style.borderTop = '1px solid #3d3759');
      });

      card.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        card.style.borderTop = '2px solid #eab308';
      });

      card.addEventListener('dragleave', () => {
        card.style.borderTop = '1px solid #3d3759';
      });

      card.addEventListener('drop', (e) => {
        e.preventDefault();
        card.style.borderTop = '1px solid #3d3759';
        if (!draggedPrioCard || draggedPrioCard === card) return;

        const fromId = draggedPrioCard.dataset.id;
        const toId = card.dataset.id;
        const fromIdx = customPriorities.findIndex(x => x.id === fromId);
        const toIdx = customPriorities.findIndex(x => x.id === toId);

        if (fromIdx >= 0 && toIdx >= 0) {
          const [moved] = customPriorities.splice(fromIdx, 1);
          customPriorities.splice(toIdx, 0, moved);
          saveCustomPriorities(customPriorities);
          renderPriorityListUI();
          showToast(`🎯 Приоритет: ${moved.name} перемещён на позицию #${toIdx + 1}`);
        }
      });
    });
  }




  // ---------- МОДУЛЬ АУКЦИОНА И РЫНКА ----------
  // Фильтры по редкостям (Белые и зеленые по умолчанию ВЫКЛЮЧЕНЫ)
  let rarFilters = {
    common: false,
    uncommon: false,
    rare: true,
    epic: true,
    legendary: true,
    mat: true
  };
  try {
    const savedRars = JSON.parse(localStorage.getItem('lg_bot_rars'));
    if (savedRars && typeof savedRars === 'object') {
      Object.assign(rarFilters, savedRars);
    }
  } catch (e) {}

  const RAR_STYLES = {
    common: { col: '#d1d5db', bg: 'rgba(160,165,175,0.18)', short: 'Бел', name: 'Обычные вещи (Белые)' },
    uncommon: { col: '#4ade80', bg: 'rgba(34,197,94,0.18)', short: 'Зел', name: 'Необычные вещи (Зеленые)' },
    rare: { col: '#38bdf8', bg: 'rgba(14,165,233,0.18)', short: 'Ред', name: 'Редкие вещи (Синие)' },
    epic: { col: '#c084fc', bg: 'rgba(168,85,247,0.18)', short: 'Эпик', name: 'Эпические вещи (Фиолетовые)' },
    legendary: { col: '#fb923c', bg: 'rgba(249,115,22,0.18)', short: 'Лег', name: 'Легендарные вещи (Оранжевые)' },
    mat: { col: '#facc15', bg: 'rgba(234,179,8,0.18)', short: 'Рес', name: 'Расходники, печати, свитки' }
  };

  // Всплывающая интерактивная подсказка над курсором мыши
  const aucTooltip = document.createElement('div');
  aucTooltip.id = 'b-auc-tooltip';
  aucTooltip.style.cssText = `
    position: fixed;
    z-index: 10000000;
    pointer-events: none;
    background: rgba(14, 12, 28, 0.96);
    color: #fff;
    border: 1px solid #4a426d;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 11px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    white-space: nowrap;
    box-shadow: 0 4px 14px rgba(0,0,0,0.7);
    display: none;
    line-height: 1.3;
    transition: opacity 0.08s ease;
  `;
  document.body.appendChild(aucTooltip);

  function updateAucTooltipPos(e) {
    if (!aucTooltip) return;
    const x = e.clientX;
    const y = e.clientY;
    aucTooltip.style.left = x + 'px';
    aucTooltip.style.top = y + 'px';

    const flipY = y < 45;
    const flipXLeft = x < 80;
    const flipXRight = x > (window.innerWidth - 80);

    let tx = '-50%';
    if (flipXLeft) tx = '0%';
    else if (flipXRight) tx = '-100%';

    let ty = '-100%';
    let my = '-10px';
    if (flipY) {
      ty = '0%';
      my = '18px';
    }
    aucTooltip.style.transform = `translate(${tx}, ${ty})`;
    aucTooltip.style.marginTop = my;
  }

  function showAucTooltip(e, k) {
    const s = RAR_STYLES[k];
    if (!s || !aucTooltip) return;
    const on = !!rarFilters[k];
    aucTooltip.innerHTML = `
      <div style="font-weight:bold;color:${s.col};margin-bottom:2px;">${s.name}</div>
      <div style="font-size:10px;color:${on ? '#00ff66' : '#ff5577'};display:flex;align-items:center;gap:4px;">
        <span>${on ? '✔ ВКЛЮЧЕНО' : '✖ ВЫКЛЮЧЕНО'}</span>
        <span style="color:#888;">(клик — переключить)</span>
      </div>
    `;
    updateAucTooltipPos(e);
    aucTooltip.style.display = 'block';
  }

  function hideAucTooltip() {
    if (aucTooltip) aucTooltip.style.display = 'none';
  }

  function updateRarButtonsUI() {
    w.querySelectorAll('[data-rar]').forEach(b => {
      const k = b.dataset.rar;
      const on = !!rarFilters[k];
      const s = RAR_STYLES[k] || { col: '#aaa', bg: 'transparent', short: k, name: k };
      b.textContent = s.short;
      b.removeAttribute('title'); // Убираем нативный браузерный title, чтобы не перекрывал подсказку над мышкой
      if (on) {
        b.style.opacity = '1';
        b.style.filter = 'none';
        b.style.border = `1px solid ${s.col}`;
        b.style.background = s.bg;
        b.style.color = s.col;
      } else {
        b.style.opacity = '0.4';
        b.style.filter = 'grayscale(1)';
        b.style.border = '1px solid #332d4a';
        b.style.background = '#181528';
        b.style.color = '#524b6d';
      }
    });
  }

  function isRarityAllowed(ev) {
    if (ev.isConsumable) return !!rarFilters.mat;
    if (ev.rarity && rarFilters[ev.rarity] !== undefined) return !!rarFilters[ev.rarity];
    return false;
  }

  const ENCH_BONUS = [0, 4, 9, 15, 22, 31, 42, 56, 74, 97, 125, 160, 200, 250, 310, 400];
  const RAR_COLORS = {
    common: '#b0b0b0',
    uncommon: '#28a745',
    rare: '#0099ff',
    epic: '#b34db3',
    legendary: '#ff8800'
  };

  function formatCoins(n) {
    n = Math.max(0, Math.floor(+n || 0));
    const g = Math.floor(n / 10000), s = Math.floor(n / 100) % 100, b = n % 100;
    const parts = [];
    if (g) parts.push(`${g} з`);
    if (s) parts.push(`${s} с`);
    if (b || (!g && !s)) parts.push(`${b} б`);
    return parts.join(' ');
  }

  function formatCoinsHtml(n) {
    n = Math.max(0, Math.floor(+n || 0));
    const g = Math.floor(n / 10000), s = Math.floor(n / 100) % 100, b = n % 100;
    const parts = [];
    if (g) parts.push(`<span>${g}<i class="b-coin-ico au-c g" title="золотые"></i></span>`);
    if (s) parts.push(`<span>${s}<i class="b-coin-ico au-c s" title="серебряные"></i></span>`);
    if (b || (!g && !s)) parts.push(`<span>${b}<i class="b-coin-ico au-c b" title="медные"></i></span>`);
    return `<span class="b-coin-wrap au-m" role="img" aria-label="${formatCoins(n)}" title="${formatCoins(n)}">${parts.join(' ')}</span>`;
  }

  function getItemArtSvg(lot, def) {
    try {
      const it = {
        id: lot.i,
        ...(def || {}),
        en: +lot.en || 0
      };
      const inv = G.LGInventory || (typeof window !== 'undefined' ? window.LGInventory : null);
      if (inv && typeof inv.art === 'function') {
        const svg = inv.art(it);
        if (svg && typeof svg === 'string' && svg.includes('<svg')) return svg;
      }
      const x = G.X || (typeof window !== 'undefined' ? window.X : null);
      if (x && typeof x.art === 'function') {
        const svg = x.art(it);
        if (svg && typeof svg === 'string' && svg.includes('<svg')) return svg;
      }
    } catch (e) {}
    return '';
  }

  function getItemArtHtml(lot, def, col) {
    const svg = getItemArtSvg(lot, def);
    if (svg) return svg;

    const slot = (def && def.slot) || '';
    const name = ((def && def.name) || lot.i || '').toLowerCase();
    let ico = '📦';
    if (slot === 'weapon') ico = '⚔️';
    else if (slot === 'armor') ico = '🛡️';
    else if (slot === 'shield') ico = '🛡️';
    else if (slot === 'helmet') ico = '🪖';
    else if (slot === 'trinket') ico = '📿';
    else if (slot === 'food' || name.includes('скумбри') || name.includes('рыб') || name.includes('хлеб') || name.includes('яблоко')) ico = '🐟';
    else if (slot === 'potion' || name.includes('зелье')) ico = '🧪';
    else if (slot === 'scroll' || name.includes('свиток')) ico = '📜';
    else if (slot === 'material' || name.includes('печать') || name.includes('сфера') || name.includes('звезд')) ico = '💎';
    else if (name.includes('руда') || name.includes('слиток') || name.includes('камень')) ico = '⛏️';

    return `<span style="font-size:20px;line-height:1;user-select:none;">${ico}</span>`;
  }

  let cachedHeroCoins = null;
  let isRefreshingCoins = false;
  let lastCoinsRefreshTime = 0;

  async function refreshMyCoinsFromApi(force = false) {
    if (isRefreshingCoins) return cachedHeroCoins;
    if (!force && Date.now() - lastCoinsRefreshTime < 5000 && cachedHeroCoins != null) {
      return cachedHeroCoins;
    }
    isRefreshingCoins = true;
    lastCoinsRefreshTime = Date.now();
    try {
      const res = await gameApi('/api/me');
      if (res && res.hero) {
        if (G) {
          G.ME = G.ME || {};
          G.ME.hero = res.hero;
          G.ME.user = res.user || G.ME.user;
          G.__lgHero = res.hero;
        }
        if (typeof res.hero.money === 'number') {
          cachedHeroCoins = res.hero.money;
          return cachedHeroCoins;
        }
      }
    } catch (e) {
    } finally {
      isRefreshingCoins = false;
    }
    return cachedHeroCoins;
  }

  function getMyCoins() {
    // 1. Из глобальных объектов памяти игры (G.__lgHero / G.ME.hero.money)
    const hero = G.__lgHero || (G.ME && G.ME.hero) || (G.__lgCityContext && G.__lgCityContext.hero && G.__lgCityContext.hero()) || (G.__lgQuestContext && G.__lgQuestContext.hero && G.__lgQuestContext.hero()) || (G.__lgAucContext && G.__lgAucContext.hero && G.__lgAucContext.hero());
    if (hero && typeof hero.money === 'number') {
      cachedHeroCoins = hero.money;
      return hero.money;
    }
    if (G.ME && typeof G.ME.money === 'number') {
      cachedHeroCoins = G.ME.money;
      return G.ME.money;
    }
    if (G.ME && G.ME.user && typeof G.ME.user.money === 'number') {
      cachedHeroCoins = G.ME.user.money;
      return G.ME.user.money;
    }
    if (G.__lgMineMe && typeof G.__lgMineMe.money === 'number') {
      cachedHeroCoins = G.__lgMineMe.money;
      return G.__lgMineMe.money;
    }

    // 2. Кэш с последнего успешного API-ответа (/api/me или /api/aucBuy)
    if (cachedHeroCoins != null && typeof cachedHeroCoins === 'number') {
      return cachedHeroCoins;
    }

    // 3. Из DOM панели игры
    const coinEls = document.querySelectorAll('#tab-hero .coins .m, .coins .m, .coins, .mn-bag-c, [data-hero-coins], .hero-coins');
    for (const el of coinEls) {
      if (!el) continue;
      const mg = el.querySelector('.mg');
      const ms = el.querySelector('.ms');
      const mb = el.querySelector('.mb');
      if (mg || ms || mb) {
        let total = 0;
        if (mg) total += (parseInt(mg.textContent, 10) || 0) * 10000;
        if (ms) total += (parseInt(ms.textContent, 10) || 0) * 100;
        if (mb) total += (parseInt(mb.textContent, 10) || 0);
        if (total > 0) {
          cachedHeroCoins = total;
          return total;
        }
      }
      const txt = el.textContent || '';
      let total = 0;
      let matched = false;
      const gMatch = txt.match(/(\d+)\s*(?:з|зол|g)/i);
      const sMatch = txt.match(/(\d+)\s*(?:с|сер|s)/i);
      const bMatch = txt.match(/(\d+)\s*(?:б|мед|b)/i);
      if (gMatch) { total += parseInt(gMatch[1], 10) * 10000; matched = true; }
      if (sMatch) { total += parseInt(sMatch[1], 10) * 100; matched = true; }
      if (bMatch) { total += parseInt(bMatch[1], 10); matched = true; }
      if (matched && total > 0) {
        cachedHeroCoins = total;
        return total;
      }
    }

    return -1;
  }

  let lastAuctionData = null;
  let aucPollTimer = null;
  let cityStockMap = new Map();
  let cityStockLoaded = false;
  let cityStockLoading = null;

  // Загрузка ассортимента и официальных цен магазинов города (/api/city/stock)
  async function fetchCityStock() {
    if (cityStockLoaded && cityStockMap.size > 0) return cityStockMap;
    if (cityStockLoading) return cityStockLoading;

    cityStockLoading = (async () => {
      try {
        const res = await gameApi('/api/city/stock');
        if (res && res.ok) {
          const m = new Map();
          const add = (it) => {
            if (it && it.id && +it.coins > 0) {
              const p = +it.coins;
              if (!m.has(it.id) || m.get(it.id) > p) {
                m.set(it.id, p);
              }
            }
          };
          const walk = (v) => {
            if (Array.isArray(v)) {
              v.forEach(x => (x && x.id ? add(x) : walk(x)));
            } else if (v && typeof v === 'object') {
              Object.values(v).forEach(walk);
            }
          };
          walk(res);
          cityStockMap = m;
          cityStockLoaded = true;
          return m;
        }
      } catch (e) {
      } finally {
        cityStockLoading = null;
      }
      return cityStockMap;
    })();

    return cityStockLoading;
  }

  function getLotEvaluation(lot, data, activeMap) {
    const def = (data && data.items && data.items[lot.i]) || {};
    const name = def.name || lot.i || 'Предмет';
    const rarity = def.rarity || 'common';
    const slot = def.slot || 'misc';
    const en = +lot.en || 0;
    const n = Math.max(1, +lot.n || 1);
    const price = +lot.p || 0;
    const unitPrice = price / n;

    // 1. Точная цена в магазинах NPC города (/api/city/stock)
    const shopPrice = cityStockMap ? (cityStockMap.get(lot.i) || 0) : 0;

    // 2. Официальная базовая себестоимость от разработчика игры из таблицы vals
    const baseVal = (data && data.vals && data.vals[lot.i]) || 0;

    // Официальный множитель ценности заточки игры (0..15)
    const bonus = en > 0 ? (ENCH_BONUS[Math.min(15, en)] || 0) : 0;

    let fairUnit = 0;
    let sourceType = 'none'; // 'shop' | 'val' | 'market' | 'none'

    if (shopPrice > 0) {
      // Предмет есть в магазине у NPC! Настоящая цена — цена магазина.
      // Если предмет заточен (+en), прибавляем ценность заточки к цене магазина.
      sourceType = 'shop';
      fairUnit = Math.max(1, Math.round(shopPrice * (1 + bonus / 100)));
    } else if (baseVal > 0) {
      // Предмета нет в лавке, но есть официальная ценность от разработчика (data.vals)
      sourceType = 'val';
      fairUnit = Math.max(1, Math.round(baseVal * (1 + bonus / 100)));
    } else if (activeMap && activeMap.has(lot.i)) {
      // Редкий предмет без магазина и таблицы vals: берем медиану активных лотов
      const arr = activeMap.get(lot.i);
      if (arr && arr.length >= 2) {
        const sorted = arr.slice().sort((a, b) => a - b);
        const med = sorted[Math.floor(sorted.length / 2)];
        fairUnit = Math.round(med * (1 + bonus / 100));
        sourceType = 'market';
      }
    }

    const fairTotal = fairUnit > 0 ? fairUnit * n : 0;

    // Расчет скидки строго от НАСТОЯЩЕЙ цены разработчика / магазина:
    let discount = 0;
    if (fairTotal > 0 && price > 0 && fairTotal > price) {
      discount = Math.round((1 - price / fairTotal) * 100);
    }
    if (!Number.isFinite(discount) || discount < 0) {
      discount = 0;
    }

    const isSeal = String(lot.i).startsWith('seal_') || /печать/i.test(name);
    const isStar = lot.i === 'star' || /звезд/i.test(name);
    const isSphere = lot.i === 'sphere' || /сфер/i.test(name);
    const isConsumable = isSeal || isStar || isSphere || slot === 'material';
    const isHighEnchant = en >= 1;
    const isHighRarity = rarity === 'epic' || rarity === 'legendary' || rarity === 'rare';
    // Белые и зеленые шмотки (оружие, броня, щиты, шлемы, амулеты)
    const isGear = ['weapon', 'armor', 'shield', 'helmet', 'trinket'].includes(slot) || !!def.wearable;
    const isTrashGear = isGear && (rarity === 'common' || rarity === 'uncommon');
    // Точеное по цене нуля: вещь имеет заточку (+1..+15), но продается по базовой цене (не хлам)
    const isEnchantFree = !isTrashGear && en >= 1 && baseVal > 0 && (unitPrice <= baseVal * 1.15);

    return {
      def,
      name,
      rarity,
      slot,
      en,
      n,
      price,
      unitPrice,
      fairTotal,
      discount,
      sourceType,
      isConsumable,
      isHighEnchant,
      isHighRarity,
      isGear,
      isTrashGear,
      isEnchantFree
    };
  }

  let isFetchingAuction = false;
  async function fetchAuctionData(isBackground = false) {
    if (isFetchingAuction) return null;
    isFetchingAuction = true;
    if (!isBackground && aucStatus) aucStatus.textContent = 'Сканирование витрины...';
    try {
      if (!cityStockLoaded) {
        await fetchCityStock();
      }
      const res = await gameApi('/api/auction');
      if (res && res.notModified) {
        return lastAuctionData;
      }
      if (res && res.ok && Array.isArray(res.lots)) {
        const isNewVersion = !lastAuctionData || lastAuctionData.v !== res.v;
        lastAuctionData = res;
        if (isNewVersion) {
          renderMarketLots();
          if (typeof scanPlayers === 'function') scanPlayers(true);
        }

        // Проверяем склад аукциона
        const ar = (window.ME && window.ME.hero && window.ME.hero.ar) || [];
        if (aucClaimBtn) {
          if (ar.length > 0) {
            aucClaimBtn.style.display = 'inline-block';
            aucClaimBtn.textContent = `📦 Забрать (${ar.length})`;
          } else {
            aucClaimBtn.style.display = 'none';
          }
        }
        return res;
      } else if (!isBackground && aucStatus) {
        aucStatus.textContent = (res && res.error) || 'Аукцион недоступен';
      }
    } catch (e) {
      if (!isBackground && aucStatus) aucStatus.textContent = 'Ошибка сети';
    } finally {
      isFetchingAuction = false;
    }
    return null;
  }

  function renderMarketLots() {
    if (!lastAuctionData || !Array.isArray(lastAuctionData.lots)) return;
    const lots = lastAuctionData.lots;
    const filter = (aucFilter && aucFilter.value) || 'deals50';
    const search = ((aucSearch && aucSearch.value) || '').trim().toLowerCase();

    // Карта рыночных цен из активных лотов для предметов без истории в hist
    const activeMap = new Map();
    for (const l of lots) {
      const u = (+l.p || 0) / Math.max(1, +l.n || 1);
      const en = +l.en || 0;
      const b = en > 0 ? (ENCH_BONUS[Math.min(15, en)] || 0) : 0;
      const norm = Math.round(u / (1 + b / 100));
      if (norm > 0) {
        if (!activeMap.has(l.i)) activeMap.set(l.i, []);
        activeMap.get(l.i).push(norm);
      }
    }

    let deals50Count = 0;
    const evaluated = [];
    const deals50List = [];

    for (const lot of lots) {
      const ev = getLotEvaluation(lot, lastAuctionData, activeMap);

      // Проверка по фильтру редкостей: если редкость выключена (по умолч. белые и зеленые),
      // предмет гарантированно отсекается ДО добавления в уведомления, бейджи и списки во ВСЕХ режимах!
      if (!isRarityAllowed(ev)) continue;

      if (ev.discount >= 50) {
        deals50Count++;
        deals50List.push({ lot, ev });
      }

      // Фильтрация
      if (search && !ev.name.toLowerCase().includes(search)) continue;
      if (filter === 'deals90' && ev.discount < 90) continue;
      if (filter === 'deals75' && ev.discount < 75) continue;
      if (filter === 'deals50' && ev.discount < 50) continue;
      if (filter === 'deals30' && ev.discount < 30) continue;
      if (filter === 'consumable' && !ev.isConsumable) continue;

      evaluated.push({ lot, ev });
    }

    // МАКСИМАЛЬНЫЙ СНАЙПИНГ: Сортируем и запускаем скупку НЕМЕДЛЕННО до генерации тяжелого DOM!
    if (isAutoBuy && evaluated.length > 0) {
      const buyCandidates = evaluated.slice().sort((a, b) => b.ev.discount - a.ev.discount);
      processAutoBuy(buyCandidates);
    }

    // Обновляем бейдж горячих предложений (только для 50%+)
    if (aucBadge) {
      if (deals50Count > 0) {
        aucBadge.textContent = `🔥${deals50Count}`;
        aucBadge.style.display = 'inline-block';
      } else {
        aucBadge.style.display = 'none';
      }
    }

    // Очистка таймера и сброс заголовка (мигание отключено)
    setSuperDealTitle(deals50Count);

    // Определение лучшей скидки для индикатора в свернутом виде
    if (deals50List.length > 0) {
      deals50List.sort((a, b) => b.ev.discount - a.ev.discount);
      currentTopDeal = deals50List[0];
    } else {
      currentTopDeal = null;
    }
    updateMiniDealUI();
    if (typeof updateStatsUI === 'function') updateStatsUI();

    if (aucStatus) {
      aucStatus.textContent = `Всего: ${lots.length} | 50%+: ${deals50Count} | Показ: ${evaluated.length}`;
    }

    if (!aucList) return;
    if (evaluated.length === 0) {
      aucList.innerHTML = `<div style="color:#aaa;text-align:center;padding:16px 8px;font-size:11px;">Нет лотов по выбранному фильтру</div>`;
      return;
    }

    // Сортировка: сначала самые выгодные
    evaluated.sort((a, b) => b.ev.discount - a.ev.discount);

    aucList.innerHTML = evaluated.map(({ lot, ev }, idx) => {
      const col = RAR_COLORS[ev.rarity] || '#aaa';
      const sourceHtml = ev.sourceType === 'shop'
        ? `В лавке: <b>${formatCoinsHtml(ev.fairTotal)}</b>`
        : ev.sourceType === 'val'
        ? `Ценность: <b>${formatCoinsHtml(ev.fairTotal)}</b>`
        : ev.fairTotal > 0
        ? `Рынок: ~<b>${formatCoinsHtml(ev.fairTotal)}</b>`
        : `Оценка: нет данных`;
      return `
        <div style="background:rgba(255,255,255,0.04);border:1px solid ${col};border-radius:6px;padding:6px 8px;display:flex;gap:8px;align-items:center;box-shadow:0 2px 6px rgba(0,0,0,0.3);">
          <div class="b-lot-art" style="border-color:${col};" title="${ev.name}${ev.en > 0 ? ' +' + ev.en : ''}">
            ${getItemArtHtml(lot, ev.def, col)}
            ${ev.en > 0 ? `<span style="position:absolute;bottom:0px;right:2px;font-size:9px;font-weight:bold;color:#00ff66;text-shadow:0 0 3px #000, 1px 1px 1px #000;line-height:1;pointer-events:none;">+${ev.en}</span>` : ''}
            ${ev.n > 1 ? `<span style="position:absolute;top:0px;left:2px;font-size:9px;font-weight:bold;color:#ffd700;text-shadow:0 0 3px #000, 1px 1px 1px #000;line-height:1;pointer-events:none;">×${ev.n}</span>` : ''}
          </div>
          <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:3px;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:4px;">
              <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                <span style="font-weight:bold;color:${col};font-size:11px;">${ev.name}</span>
                ${ev.en > 0 ? `<b style="color:#00ff66;font-size:11px;"> +${ev.en}</b>` : ''}
                ${ev.n > 1 ? `<span style="color:#ffd700;font-size:10px;"> ×${ev.n}</span>` : ''}
              </div>
              ${ev.discount >= 20 ? `<span style="background:#28a745;color:#fff;padding:1px 5px;border-radius:8px;font-size:9px;font-weight:bold;white-space:nowrap;flex-shrink:0;">−${ev.discount}% 🔥</span>` : ''}
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;font-size:10px;gap:4px;">
              <span style="color:#8899aa;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">от ${lot.sn || lot.s || 'Игрок'}</span>
              <span style="color:#ffd700;font-weight:bold;font-size:11px;white-space:nowrap;flex-shrink:0;">${formatCoinsHtml(ev.price)}</span>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;font-size:9px;color:#8899aa;border-top:1px dashed rgba(255,255,255,0.08);padding-top:4px;margin-top:2px;gap:4px;">
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${sourceHtml}</span>
              <button data-buy-idx="${idx}" style="padding:3px 9px;background:${(lot.ru && lot.ru > Date.now()) ? '#7c3aed' : '#007bff'};color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:10px;font-weight:bold;transition:background 0.2s;flex-shrink:0;">${(lot.ru && lot.ru > Date.now()) ? '🎲 Хочу' : 'Купить'}</button>
            </div>
          </div>
        </div>
      `;
    }).join('');

    // Привязываем обработчики покупки
    aucList.querySelectorAll('[data-buy-idx]').forEach(btnEl => {
      btnEl.onclick = async () => {
        const idx = +btnEl.dataset.buyIdx;
        const item = evaluated[idx];
        if (!item || !item.lot) return;
        const lotId = item.lot.id;
        const price = +item.lot.p;
        const isRaffle = !!(item.lot.ru && item.lot.ru > Date.now());
        const oldText = btnEl.textContent;
        btnEl.disabled = true;
        btnEl.textContent = '⏳...';
        btnEl.style.background = '#666';

        try {
          const buyRes = await gameApi('/api/aucBuy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ lot: String(lotId), price: Number(price) })
          });

          if (buyRes && buyRes.ok) {
            if (isRaffle) {
              btnEl.textContent = '🎲 В игре';
              btnEl.style.background = '#7c3aed';
              showToast('Заявка на розыгрыш «Хочу» принята!');
            } else {
              btnEl.textContent = '✔ Куплено';
              btnEl.style.background = '#28a745';
              if (buyRes.hero && G.ME) G.ME.hero = buyRes.hero;
              showToast('Предмет куплен с аукциона!');
            }
            setTimeout(() => {
              fetchAuctionData(true);
            }, 1000);
          } else {
            const err = String((buyRes && (buyRes.error || buyRes.err)) || 'Не вышло');
            const status = buyRes && buyRes.status;
            const isConflict = status === 409 || err.includes('409') || err.includes('уже нет') || err.includes('сняли') || err.includes('купили') || err.includes('not found');
            const isRateLimit = status === 429 || err.includes('429') || err.includes('слишком часто') || err.includes('rate');
            let shortErr = err;

            if (isConflict) {
              shortErr = 'Лота нет (409)';
              btnEl.textContent = 'Лота нет';
              btnEl.disabled = true;
              btnEl.style.background = '#555';
              showToast('Лот уже выкуплен другим игроком (409)', true);
              return;
            } else if (isRateLimit) {
              shortErr = 'Лимит (429)';
              showToast('Аукцион: слишком частые запросы (429), подождите 2с', true);
            } else if (err.includes('монет') || err.includes('денег') || err.includes('lack') || err.includes('золот')) shortErr = 'Нет монет';
            else if (err.includes('места') || err.includes('сумк') || err.includes('инвентар')) shortErr = 'Сумка полна';
            else if (err.includes('Призрак') || err.includes('призрак')) shortErr = 'Призрак';
            else if (err.includes('бой') || err.includes('Бой')) shortErr = 'Идёт бой';
            else if (err.includes('долг') || err.includes('Долг')) shortErr = 'Долг';
            else if (err.includes('закрыт') || err.includes('Закрыт')) shortErr = 'Закрыт';
            else if (shortErr.length > 12) shortErr = 'Ошибка';

            btnEl.textContent = shortErr;
            btnEl.style.background = '#dc3545';
            btnEl.title = err;
            showToast('Аукцион: ' + err, true);
            setTimeout(() => {
              if (btnEl && btnEl.isConnected) {
                btnEl.disabled = false;
                btnEl.textContent = isRaffle ? '🎲 Хочу' : (oldText || 'Купить');
                btnEl.style.background = isRaffle ? '#7c3aed' : '#007bff';
                btnEl.title = '';
              }
            }, 2500);
          }
        } catch (e) {
          btnEl.textContent = 'Сбой сети';
          btnEl.style.background = '#dc3545';
          btnEl.title = String(e && e.message ? e.message : e);
          showToast('Аукцион: сбой соединения', true);
          setTimeout(() => {
            if (btnEl && btnEl.isConnected) {
              btnEl.disabled = false;
              btnEl.textContent = isRaffle ? '🎲 Хочу' : (oldText || 'Купить');
              btnEl.style.background = isRaffle ? '#7c3aed' : '#007bff';
              btnEl.title = '';
            }
          }, 2500);
        }
      };
    });

    if (isAutoBuy && evaluated.length > 0 && !isAutoBuyInProgress) {
      processAutoBuy(evaluated);
    }
  }

  async function processAutoBuy(evaluatedLots) {
    if (!isAutoBuy || isAutoBuyInProgress) return;
    if (!Array.isArray(evaluatedLots) || evaluatedLots.length === 0) return;

    isAutoBuyInProgress = true;
    try {
      let myCoins = getMyCoins();
      if (myCoins < 0) {
        await refreshMyCoinsFromApi();
        myCoins = getMyCoins();
      }

      let boughtAny = false;
      for (const item of evaluatedLots) {
        if (!isAutoBuy) break;
        const lot = item.lot;
        const ev = item.ev;
        if (!lot || !lot.id) continue;

        const lotKey = String(lot.id);
        if (attemptedAutoBuyLots.has(lotKey)) {
          continue;
        }

        const price = +lot.p || 0;

        // Проверка: игнорировать товар, на который реально нет денег
        if (myCoins >= 0 && price > myCoins) {
          continue;
        }

        attemptedAutoBuyLots.add(lotKey);
        const isRaffle = !!(lot.ru && lot.ru > Date.now());
        showToast(isRaffle ? `🎲 Заявка: "${ev.name}" (розыгрыш)...` : `⚡ Скупка: "${ev.name}" за ${formatCoins(price)}...`);

        try {
          const buyRes = await gameApi('/api/aucBuy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ lot: String(lot.id), price: Number(price) })
          });

          if (buyRes && buyRes.ok) {
            boughtAny = true;
            if (isRaffle) {
              showToast(`🎲 Розыгрыш: заявка «Хочу» на "${ev.name}" принята!`);
              // В режиме розыгрыша оставляем lotKey в attemptedAutoBuyLots навсегда, чтобы не спамить
            } else {
              if (buyRes.hero) {
                if (G) {
                  G.ME = G.ME || {};
                  G.ME.hero = buyRes.hero;
                  G.__lgHero = buyRes.hero;
                }
                if (typeof buyRes.hero.money === 'number') {
                  cachedHeroCoins = buyRes.hero.money;
                  myCoins = buyRes.hero.money;
                } else if (myCoins >= price) {
                  myCoins -= price;
                  cachedHeroCoins = myCoins;
                }
              } else if (myCoins >= price) {
                myCoins -= price;
                cachedHeroCoins = myCoins;
              }
              showToast(`✔ Автоскупка: куплен "${ev.name}" за ${formatCoins(price)}!`);
            }
          } else {
            const err = String((buyRes && (buyRes.error || buyRes.err)) || 'Не вышло');
            const status = buyRes && buyRes.status;
            const isConflict = status === 409 || err.includes('409') || err.includes('уже нет') || err.includes('сняли') || err.includes('купили') || err.includes('not found');
            const isRateLimit = status === 429 || err.includes('429') || err.includes('слишком часто') || err.includes('rate');

            if (isConflict) {
              // Лот уже выкуплен другим игроком или снят (409)! НАВСЕГДА оставляем в attemptedAutoBuyLots
              attemptedAutoBuyLots.add(lotKey);
              if (lastAuctionData && Array.isArray(lastAuctionData.lots)) {
                lastAuctionData.lots = lastAuctionData.lots.filter(l => String(l.id) !== lotKey);
              }
            } else if (isRateLimit) {
              // Лимит запросов 429: бэкофф на 2.5 сек
              showToast('⚠️ Аукцион: лимит запросов сервера (429), пауза 2.5с...', true);
              attemptedAutoBuyLots.delete(lotKey);
              await new Promise(r => setTimeout(r, 2500));
            } else if (err.includes('места') || err.includes('сумк') || err.includes('инвентар')) {
              isAutoBuy = false;
              if (bAucAutoBuy) bAucAutoBuy.checked = false;
              startAuctionPolling();
              showToast('Автоскупка выключена: сумка переполнена!', true);
              break;
            } else if (err.includes('монет') || err.includes('денег') || err.includes('lack') || err.includes('золот')) {
              showToast(`Автоскупка: не хватает монет на "${ev.name}"`, true);
              attemptedAutoBuyLots.delete(lotKey);
              await refreshMyCoinsFromApi(true);
              myCoins = getMyCoins();
            } else {
              // Временный сбой сети - разрешаем повторить на следующем цикле
              attemptedAutoBuyLots.delete(lotKey);
            }
          }
        } catch (e) {
          attemptedAutoBuyLots.delete(lotKey);
        }
      }

      // После скупки найденных лотов сразу обновляем витрину
      if (boughtAny) {
        fetchAuctionData(true);
      }
    } finally {
      isAutoBuyInProgress = false;
    }
  }

  function disableAutoBuyOnFilterChange() {
    attemptedAutoBuyLots.clear();
    if (isAutoBuy || (bAucAutoBuy && bAucAutoBuy.checked)) {
      isAutoBuy = false;
      if (bAucAutoBuy) bAucAutoBuy.checked = false;
      startAuctionPolling();
      showToast('Автоскупка выключена (смена фильтра)');
    }
  }

  if (aucRefresh) aucRefresh.onclick = () => {
    attemptedAutoBuyLots.clear();
    refreshMyCoinsFromApi(true);
    cityStockLoaded = false;
    fetchAuctionData(false);
  };
  if (aucFilter) aucFilter.onchange = () => {
    disableAutoBuyOnFilterChange();
    renderMarketLots();
  };
  if (aucSearch) aucSearch.oninput = () => {
    disableAutoBuyOnFilterChange();
    renderMarketLots();
  };

  w.querySelectorAll('[data-rar]').forEach(b => {
    const k = b.dataset.rar;
    b.onmouseenter = (e) => showAucTooltip(e, k);
    b.onmousemove = (e) => updateAucTooltipPos(e);
    b.onmouseleave = () => hideAucTooltip();
    b.onclick = (e) => {
      rarFilters[k] = !rarFilters[k];
      localStorage.setItem('lg_bot_rars', JSON.stringify(rarFilters));
      disableAutoBuyOnFilterChange();
      updateRarButtonsUI();
      showAucTooltip(e, k);
      renderMarketLots();
    };
  });
  updateRarButtonsUI();

  if (bAucAutoBuy) {
    bAucAutoBuy.onclick = async (e) => {
      if (bAucAutoBuy.checked) {
        const ok = window.confirm('Вы уверены, что хотите включить автоскупку по выбранным категориям?\n\nБот будет автоматически покупать все лоты по выбранным категориям и фильтрам, на которые хватает монет!');
        if (!ok) {
          e.preventDefault();
          bAucAutoBuy.checked = false;
          isAutoBuy = false;
          startAuctionPolling();
          return;
        }
        isAutoBuy = true;
        attemptedAutoBuyLots.clear();
        await refreshMyCoinsFromApi(true);
        startAuctionPolling();
        showToast('⚡ Снайпер-Автоскупка ВКЛЮЧЕНА (безопасный турбо-опрос 800 мс, без спама 409/429)!');
        if (lastAuctionData) {
          renderMarketLots();
        } else {
          fetchAuctionData(true);
        }
      } else {
        isAutoBuy = false;
        attemptedAutoBuyLots.clear();
        startAuctionPolling();
        showToast('Автоскупка выключена (опрос 15 сек)');
      }
    };
  }

  async function executeQuickBuyLot(btnEl, lotId, price) {
    if (!lotId || !btnEl) return;
    btnEl.disabled = true;
    const oldText = btnEl.textContent;
    btnEl.textContent = '⏳...';
    btnEl.style.background = '#666';

    try {
      const buyRes = await gameApi('/api/aucBuy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lot: String(lotId), price: Number(price) })
      });

      if (buyRes && buyRes.ok) {
        btnEl.textContent = '✔ Куплено!';
        btnEl.style.background = '#28a745';
        if (buyRes.hero && G.ME) G.ME.hero = buyRes.hero;
        showToast('Халява выкуплена с аукциона!');
        currentTopDeal = null;
        updateMiniDealUI();
        if (typeof updateStatsUI === 'function') updateStatsUI();
        setTimeout(() => {
          fetchAuctionData(true);
        }, 300);
      } else {
        const err = String((buyRes && (buyRes.error || buyRes.err)) || 'Не вышло');
        const status = buyRes && buyRes.status;
        const isConflict = status === 409 || err.includes('409') || err.includes('уже нет') || err.includes('сняли') || err.includes('купили') || err.includes('not found');
        const isRateLimit = status === 429 || err.includes('429') || err.includes('слишком часто') || err.includes('rate');
        let shortErr = err;

        if (isConflict) {
          shortErr = 'Лота нет (409)';
          btnEl.textContent = 'Лота нет';
          btnEl.disabled = true;
          btnEl.style.background = '#555';
          showToast('Лот уже выкуплен другим игроком (409)', true);
          return;
        } else if (isRateLimit) {
          shortErr = 'Лимит (429)';
          showToast('Аукцион: слишком частые запросы (429), подождите 2с', true);
        } else if (err.includes('монет') || err.includes('денег') || err.includes('lack') || err.includes('золот')) shortErr = 'Нет монет';
        else if (err.includes('места') || err.includes('сумк') || err.includes('инвентар')) shortErr = 'Сумка полна';
        else if (err.includes('Призрак') || err.includes('призрак')) shortErr = 'Призрак';
        else if (err.includes('бой') || err.includes('Бой')) shortErr = 'Идёт бой';
        else if (err.includes('долг') || err.includes('Долг')) shortErr = 'Долг';
        else if (err.includes('закрыт') || err.includes('Закрыт')) shortErr = 'Закрыт';
        else if (shortErr.length > 12) shortErr = 'Ошибка';

        btnEl.textContent = shortErr;
        btnEl.style.background = '#dc3545';
        btnEl.title = err;
        showToast('Аукцион: ' + err, true);
        setTimeout(() => {
          if (btnEl && btnEl.isConnected) {
            btnEl.disabled = false;
            btnEl.textContent = oldText || 'Купить';
            btnEl.style.background = '#28a745';
            btnEl.title = '';
          }
        }, 2500);
      }
    } catch (e) {
      btnEl.textContent = 'Сбой сети';
      btnEl.style.background = '#dc3545';
      btnEl.title = String(e && e.message ? e.message : e);
      showToast('Аукцион: сбой соединения', true);
      setTimeout(() => {
        if (btnEl && btnEl.isConnected) {
          btnEl.disabled = false;
          btnEl.textContent = oldText || 'Купить';
          btnEl.style.background = '#28a745';
          btnEl.title = '';
        }
      }, 2500);
    }
  }

  if (bMiniDealFire) {
    bMiniDealFire.onclick = (e) => {
      e.stopPropagation();
      activeTab = 'market';
      applyActiveTab();
      isMinimized = false;
      isSquare = false;
      isHoveredFromSquare = false;
      applyMinimized();
    };
  }

  if (aucClaimBtn) {
    aucClaimBtn.onclick = async () => {
      aucClaimBtn.disabled = true;
      aucClaimBtn.textContent = 'Забираем...';
      try {
        const res = await gameApi('/api/aucClaim', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        if (res && res.ok) {
          aucClaimBtn.textContent = '✔ Забрано';
          setTimeout(() => {
            aucClaimBtn.style.display = 'none';
          }, 2000);
        } else {
          aucClaimBtn.disabled = false;
          aucClaimBtn.textContent = 'Сумка полна?';
        }
      } catch (e) {
        aucClaimBtn.disabled = false;
        aucClaimBtn.textContent = 'Сбой';
      }
    };
  }

  async function claimMineQuests(isAuto = false) {
    if (isClaimingQuests) return;
    if (Date.now() - lastQuestClaimTime < 4000) return;
    isClaimingQuests = true;
    lastQuestClaimTime = Date.now();

    if (mineClaimBtn) {
      mineClaimBtn.disabled = true;
      mineClaimBtn.textContent = 'Забираем...';
    }

    try {
      const c = currentMineId || 'home';
      const res = await gameApi('/api/mineClaim', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ c })
      });
      if (res && res.ok) {
        showToast('🎉 Награда за поручения шахты получена!');
        const me = window.__lgMineMe || (G.__lgMineS && G.__lgMineS.me);
        if (me && me.q) {
          for (const x of [...(me.q.d || []), ...(me.q.w || [])]) if (x.done) x.cl = 1;
          if (me.q.c && me.q.c.done && me.q.c.ok) me.q.c.cl = 1;
        }
        refreshMyCoinsFromApi(true);
        if (mineClaimBtn) {
          mineClaimBtn.textContent = '✔ Получено';
          setTimeout(() => {
            if (mineClaimBtn) {
              mineClaimBtn.disabled = false;
              mineClaimBtn.style.display = 'none';
            }
            updateStatsUI();
          }, 2000);
        }
      } else {
        if (!isAuto) showToast((res && res.error) || 'Не удалось забрать награду', true);
        if (mineClaimBtn) {
          mineClaimBtn.disabled = false;
          mineClaimBtn.textContent = 'Ошибка';
        }
      }
    } catch (e) {
      if (!isAuto) showToast('Сбой сети при получении награды', true);
      if (mineClaimBtn) {
        mineClaimBtn.disabled = false;
        mineClaimBtn.textContent = 'Сбой';
      }
    } finally {
      isClaimingQuests = false;
    }
  }

  function tryAutoClaimMineQuests() {
    if (isPaused || !autoClaimQuests) return;
    const me = window.__lgMineMe || (G.__lgMineS && G.__lgMineS.me);
    const qData = me && me.q;
    if (qData) {
      const qc = qData.c;
      const qcReady = !!(qc && qc.done && qc.ok && !qc.cl);
      const readyList = [...(qData.d || []), ...(qData.w || [])].filter(x => x.done && !x.cl);
      if (readyList.length > 0 || qcReady) {
        claimMineQuests(true);
      }
    }
    const nativeClaim = document.querySelector('[data-mn-claim]:not([disabled])');
    if (nativeClaim) nativeClaim.click();
  }

  if (mineClaimBtn) {
    mineClaimBtn.onclick = () => claimMineQuests(false);
  }

  function startAuctionPolling() {
    if (aucPollTimer) clearInterval(aucPollTimer);
    setSuperDealTitle(0);
    // При включенной автоскупке: минимальная задержка 300 мс для ловли лотов быстрее всех
    // Без автоскупки: 15 секунд, чтобы не нагружать сервер
    const interval = isAutoBuy ? 800 : 15000;
    aucPollTimer = setInterval(() => {
      if (!window.__mineBotRunning) return;
      if (isAutoBuy || document.visibilityState === 'visible') {
        fetchAuctionData(true);
      }
    }, interval);
  }

  // ---------- МОДУЛЬ РАЗВЕДКИ И ВЗАИМОДЕЙСТВИЯ С ИГРОКАМИ ----------
  const STORAGE_PLAYERS_KEY = 'lg_bot_players_cache';
  const playersMap = new Map(); // nickLower -> { nick, sources: Set, lastSeen: number, lotsCount: number }
  let currentDossierNick = null;

  // Загружаем сохранённую накопительную базу игроков из localStorage при запуске
  function loadPlayersFromStorage() {
    try {
      const raw = localStorage.getItem(STORAGE_PLAYERS_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (Array.isArray(data)) {
        data.forEach(item => {
          if (item && item.nick) {
            const key = item.nick.toLowerCase();
            playersMap.set(key, {
              nick: item.nick,
              sources: new Set(Array.isArray(item.sources) ? item.sources : []),
              lastSeen: item.lastSeen || Date.now(),
              lotsCount: item.lotsCount || 0
            });
          }
        });
      }
    } catch (e) {
    }
  }

  loadPlayersFromStorage();

  let savePlayersTimer = null;
  function savePlayersToStorage() {
    if (savePlayersTimer) return;
    savePlayersTimer = setTimeout(() => {
      savePlayersTimer = null;
      try {
        const arr = [];
        playersMap.forEach(p => {
          arr.push({
            nick: p.nick,
            sources: Array.from(p.sources),
            lastSeen: p.lastSeen,
            lotsCount: p.lotsCount || 0
          });
        });
        localStorage.setItem(STORAGE_PLAYERS_KEY, JSON.stringify(arr));
      } catch (e) {
      }
    }, 1500);
  }

  function escHtml(str) {
    return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function getMyNick() {
    return (G.ME && (G.ME.user?.name || G.ME.hero?.n || G.ME.hero?.name)) ||
           (G.__lgMineMe && (G.__lgMineMe.n || G.__lgMineMe.name || G.__lgMineMe.user?.name)) ||
           '';
  }

  // Получить игроков, которые прямо сейчас стоят / ходят в городе
  function getCityPlayers() {
    const set = new Set();
    try {
      const dbg = window.LGCity?.debug?.();
      if (dbg && Array.isArray(dbg.others)) {
        dbg.others.forEach(l => { if (l) set.add(String(l).trim().toLowerCase()); });
      }
    } catch (e) {}
    try {
      const peek = window.LGCity?.peek?.();
      if (peek && Array.isArray(peek.w)) {
        peek.w.forEach(item => { if (item && item[0]) set.add(String(item[0]).trim().toLowerCase()); });
      }
    } catch (e) {}
    try {
      document.querySelectorAll('.cy-view .cy-w[data-l], .cy-view [data-nick]').forEach(el => {
        const l = el.getAttribute('data-l') || el.getAttribute('data-nick');
        if (l && l.trim()) set.add(l.trim().toLowerCase());
      });
    } catch (e) {}
    if (G.__lgLiveCityPlayers && G.__lgLiveCityPlayers instanceof Set) {
      G.__lgLiveCityPlayers.forEach(l => { if (l) set.add(String(l).trim().toLowerCase()); });
    }
    return set;
  }

  // Запрос свежего списка игроков в городе через API игры
  async function refreshLiveCityPlayers() {
    try {
      const res = await gameApi('/api/city/state');
      if (res && res.ok) {
        if (!G.__lgLiveCityPlayers) G.__lgLiveCityPlayers = new Set();
        if (Array.isArray(res.a)) {
          res.a.forEach(e => { if (e && e.l) G.__lgLiveCityPlayers.add(String(e.l).trim().toLowerCase()); });
        }
        if (Array.isArray(res.others)) {
          res.others.forEach(l => { if (l) G.__lgLiveCityPlayers.add(String(l).trim().toLowerCase()); });
        }
        if (Array.isArray(res.g)) {
          res.g.forEach(l => { if (l) G.__lgLiveCityPlayers.delete(String(l).trim().toLowerCase()); });
        }
      }
    } catch (e) {}
  }

  // Получить игроков, которые прямо сейчас копают / ходят в текущей шахте
  function getMinePlayers() {
    const set = new Set();
    try {
      document.querySelectorAll('.mn-heroes [data-k]').forEach(el => {
        const k = el.getAttribute('data-k') || '';
        const login = k.replace(/^[dw]:/, '');
        if (login && login !== 'me') set.add(login.trim().toLowerCase());
      });
    } catch (e) {}
    try {
      const st = G.__lgMineState;
      if (st && st.dg) {
        Object.values(st.dg).flat().forEach(l => {
          if (l) set.add(String(l).trim().toLowerCase());
        });
      }
    } catch (e) {}
    return set;
  }

  function addPlayerRecord(rawNick, source) {
    if (!rawNick || typeof rawNick !== 'string') return;
    const nick = rawNick.trim();
    if (!nick || nick.length < 2 || nick.length > 30) return;
    const myNick = getMyNick().toLowerCase();
    const key = nick.toLowerCase();
    if (myNick && key === myNick) return; // Не добавляем себя

    let p = playersMap.get(key);
    if (!p) {
      p = {
        nick: nick,
        sources: new Set(),
        lastSeen: Date.now(),
        lotsCount: 0
      };
      playersMap.set(key, p);
    }
    p.sources.add(source);
    p.lastSeen = Date.now();
    savePlayersToStorage();
  }

  function scanPlayers(silent = false) {
    // 1. Сбрасываем счетчики лотов перед актуализацией
    playersMap.forEach(p => { p.lotsCount = 0; });

    // 2. Сканируем активные лоты аукциона
    if (lastAuctionData && Array.isArray(lastAuctionData.lots)) {
      for (const lot of lastAuctionData.lots) {
        const sNick = (lot.sn || lot.s || lot.u || '').trim();
        if (sNick) {
          addPlayerRecord(sNick, '🏛️ Рынок');
          const p = playersMap.get(sNick.toLowerCase());
          if (p) p.lotsCount = (p.lotsCount || 0) + 1;
        }
      }
    }

    // 3. Сканируем игроков в городе (LGCity.debug и LGCity.peek)
    const cityPlayers = getCityPlayers();
    cityPlayers.forEach(login => {
      addPlayerRecord(login, '🏛️ Город');
    });

    // 4. Сканируем игроков в шахте (.mn-heroes и st.dg)
    const minePlayers = getMinePlayers();
    minePlayers.forEach(login => {
      addPlayerRecord(login, '⛏️ Шахта');
    });

    // 5. Сканируем чат (авторы сообщений в DOM)
    try {
      document.querySelectorAll('[data-nick], [data-author], .cy-chat-author, .cy-chat-m b, .cy-chat-b b').forEach(el => {
        const n = el.getAttribute('data-nick') || el.getAttribute('data-author') || el.textContent;
        if (n && n.trim() && !n.includes(':') && n.length >= 2 && n.length <= 25) {
          addPlayerRecord(n.trim(), '💬 Чат');
        }
      });
    } catch (e) {}

    // Сохраняем в localStorage накопительную базу
    savePlayersToStorage();

    // Обновляем бейдж и строку статуса
    if (playersBadge) {
      playersBadge.textContent = String(playersMap.size);
    }
    if (playersStatus) {
      const activeAuctionSellers = Array.from(playersMap.values()).filter(p => p.lotsCount > 0).length;
      playersStatus.textContent = `В базе: ${playersMap.size} • Торговцев: ${activeAuctionSellers} • В городе: ${cityPlayers.size}`;
    }
    if (typeof updateBroadcastRecipientsCount === 'function') {
      updateBroadcastRecipientsCount();
    }

    // Обновляем представление, если вкладка активна
    if (activeTab === 'players') {
      if (playersViewDossier && playersViewDossier.style.display !== 'none' && currentDossierNick) {
        showPlayerDossier(currentDossierNick);
      } else {
        renderPlayersList();
      }
    }
  }

  function renderPlayersList() {
    if (!playersList) return;
    const search = ((playersSearch && playersSearch.value) || '').trim().toLowerCase();
    const citySet = getCityPlayers();
    const mineSet = getMinePlayers();

    const arr = Array.from(playersMap.values());
    // Сортировка:
    // 1. Те, кто прямо сейчас в городе или в шахте (живые коты рядом!)
    // 2. Те, у кого есть активные лоты на рынке (по количеству)
    // 3. Остальные по алфавиту
    arr.sort((a, b) => {
      const aNear = (citySet.has(a.nick.toLowerCase()) || mineSet.has(a.nick.toLowerCase())) ? 1 : 0;
      const bNear = (citySet.has(b.nick.toLowerCase()) || mineSet.has(b.nick.toLowerCase())) ? 1 : 0;
      if (bNear !== aNear) return bNear - aNear;
      if (b.lotsCount !== a.lotsCount) return b.lotsCount - a.lotsCount;
      return a.nick.localeCompare(b.nick);
    });

    const filtered = arr.filter(p => !search || p.nick.toLowerCase().includes(search));

    if (filtered.length === 0) {
      playersList.innerHTML = `
        <div style="text-align:center;padding:24px 8px;color:#8899aa;font-size:11px;">
          ${search ? 'Никого не найдено по запросу' : 'Игроки пока не обнаружены.<br>Нажмите 🔄 для сканирования'}
        </div>
      `;
      return;
    }

    playersList.innerHTML = filtered.map(p => {
      const badges = [];
      const key = p.nick.toLowerCase();

      if (citySet.has(key)) {
        badges.push(`<span style="background:rgba(0,255,102,0.18);color:#00ff66;border:1px solid rgba(0,255,102,0.4);padding:1px 5px;border-radius:4px;font-size:9px;font-weight:bold;">🏛️ В Городе</span>`);
      }
      if (mineSet.has(key)) {
        badges.push(`<span style="background:rgba(255,153,0,0.18);color:#ff9900;border:1px solid rgba(255,153,0,0.4);padding:1px 5px;border-radius:4px;font-size:9px;font-weight:bold;">⛏️ В Шахте</span>`);
      }
      if (p.lotsCount > 0) {
        badges.push(`<span style="background:rgba(255,215,0,0.15);color:#ffd700;border:1px solid rgba(255,215,0,0.3);padding:1px 5px;border-radius:4px;font-size:9px;font-weight:bold;">🏛️ Лотов: ${p.lotsCount}</span>`);
      }
      if (p.sources.has('💬 Чат') && !citySet.has(key) && !mineSet.has(key)) {
        badges.push(`<span style="background:rgba(102,204,255,0.15);color:#66ccff;border:1px solid rgba(102,204,255,0.3);padding:1px 5px;border-radius:4px;font-size:9px;">💬 Чат</span>`);
      }

      return `
        <div data-player-nick="${escHtml(p.nick)}" style="background:rgba(255,255,255,0.04);border:1px solid #3d3759;border-radius:5px;padding:6px 8px;display:flex;justify-content:space-between;align-items:center;cursor:pointer;transition:all 0.15s;gap:6px;">
          <div style="display:flex;align-items:center;gap:6px;overflow:hidden;">
            <span style="font-weight:bold;color:#fff;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escHtml(p.nick)}</span>
          </div>
          <div style="display:flex;gap:4px;align-items:center;flex-shrink:0;">
            ${badges.join('')}
            <span style="color:#666;font-size:11px;margin-left:2px;">›</span>
          </div>
        </div>
      `;
    }).join('');

    playersList.querySelectorAll('[data-player-nick]').forEach(el => {
      el.onmouseenter = () => { el.style.borderColor = '#00ff66'; el.style.background = 'rgba(0,255,102,0.06)'; };
      el.onmouseleave = () => { el.style.borderColor = '#3d3759'; el.style.background = 'rgba(255,255,255,0.04)'; };
      el.onclick = () => {
        const nick = el.getAttribute('data-player-nick');
        if (nick) openPlayerDossier(nick);
      };
    });
  }

  function openPlayerDossier(nick) {
    currentDossierNick = nick;
    if (playersViewList) playersViewList.style.display = 'none';
    if (playersViewDossier) playersViewDossier.style.display = 'block';
    showPlayerDossier(nick);
  }

  if (dossierBack) {
    dossierBack.onclick = () => {
      currentDossierNick = null;
      if (playersViewDossier) playersViewDossier.style.display = 'none';
      if (playersViewList) playersViewList.style.display = 'block';
      renderPlayersList();
    };
  }

  function openTradeWith(nick) {
    if (!nick) return;
    try {
      if (window.LGTrade && typeof window.LGTrade.invite === 'function') {
        window.LGTrade.invite(nick);
        showToast(`Предложение обмена отправлено: ${nick}`);
        return;
      }
    } catch (e) {}

    try {
      if (window.LGCity && typeof window.LGCity.tradeMenu === 'function') {
        window.LGCity.tradeMenu(nick, window.innerWidth / 2, window.innerHeight / 2);
        showToast(`Предложение обмена: ${nick}`);
        return;
      }
    } catch (e) {}

    try {
      if (window.LGTrade && typeof window.LGTrade.menu === 'function') {
        window.LGTrade.menu(nick, { n: nick }, window.innerWidth / 2, window.innerHeight / 2);
        showToast(`Предложение обмена: ${nick}`);
        return;
      }
    } catch (e) {}

    // Fallback: прямой сетевой запрос
    gameApi('/api/trade', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ op: 'invite', to: nick })
    }).then(res => {
      if (res && res.ok) {
        showToast(`Обмен предложен игроку ${nick}`);
      } else {
        const err = (res && (res.error || res.err)) || 'Игрок не найден или не в городе';
        showToast(`Обмен: ${err}`, true);
      }
    }).catch(() => {
      showToast(`Не удалось предложить обмен`, true);
    });
  }

  // Родное открытие вкладки «Личные» с выбранным игроком (как в самой игре)
  function openWhisperWith(nick) {
    if (!nick) return;
    const chatBox = document.querySelector('.cy-chat');
    if (!chatBox) {
      showToast('Чат игры не найден', true);
      return;
    }

    // 1. Раскрываем чат, если он свернут
    const tBtn = chatBox.querySelector('.cy-chat-t');
    if (tBtn && (tBtn.getAttribute('aria-expanded') === 'false' || chatBox.querySelector('.cy-chat-b')?.hidden)) {
      tBtn.click();
    }

    // 2. Вызываем родной chatPeer(nick) движка игры через триггер [data-dm] в .cy-chat-l
    setTimeout(() => {
      const list = chatBox.querySelector('.cy-chat-l');
      if (list) {
        const dummy = document.createElement('span');
        dummy.setAttribute('data-dm', nick);
        list.appendChild(dummy);
        dummy.click();
        dummy.remove();
      }

      // Переключаем на вкладку Личные (dm), если она ещё не открыта
      const dmTab = chatBox.querySelector('.cy-chat-tabs [data-tab="dm"]');
      if (dmTab && !dmTab.classList.contains('on')) {
        dmTab.click();
      }

      // Если в полоске собеседников есть кнопка [data-peer="nick"], кликаем по ней
      const peerBtn = chatBox.querySelector(`.cy-chat-to [data-peer="${CSS.escape(nick)}"]`);
      if (peerBtn) {
        peerBtn.click();
      }

      // Фокусируем поле ввода
      const inp = chatBox.querySelector('.cy-chat input');
      if (inp) {
        inp.value = ''; // очищаем от старых команд
        inp.focus();
      }

      showToast(`Личные: диалог с ${nick}`);
    }, 60);
  }

  async function executeBuyLot(lotId, price, btnEl, onComplete) {
    if (!btnEl) return;
    btnEl.disabled = true;
    btnEl.textContent = '⏳...';
    btnEl.style.background = '#666';

    try {
      const buyRes = await gameApi('/api/aucBuy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lot: String(lotId), price: Number(price) })
      });

      if (buyRes && buyRes.ok) {
        btnEl.textContent = '✔ Куплено';
        btnEl.style.background = '#28a745';
        if (buyRes.hero && G.ME) G.ME.hero = buyRes.hero;
        showToast('Предмет куплен с аукциона!');
        setTimeout(async () => {
          await fetchAuctionData(true);
          if (typeof onComplete === 'function') onComplete();
        }, 800);
      } else {
        const err = String((buyRes && (buyRes.error || buyRes.err)) || 'Не вышло');
        let shortErr = err;
        if (err.includes('уже нет') || err.includes('сняли') || err.includes('купили') || err.includes('not found')) shortErr = 'Лота нет';
        else if (err.includes('монет') || err.includes('денег') || err.includes('lack') || err.includes('золот')) shortErr = 'Нет монет';
        else if (err.includes('места') || err.includes('сумк') || err.includes('инвентар')) shortErr = 'Сумка полна';
        else if (err.includes('Призрак') || err.includes('призрак')) shortErr = 'Призрак';
        else if (err.includes('бой') || err.includes('Бой')) shortErr = 'Идёт бой';
        else if (err.includes('долг') || err.includes('Долг')) shortErr = 'Долг';
        else if (err.includes('закрыт') || err.includes('Закрыт')) shortErr = 'Закрыт';
        else if (shortErr.length > 12) shortErr = 'Ошибка';

        btnEl.textContent = shortErr;
        btnEl.style.background = '#dc3545';
        btnEl.title = err;
        showToast('Аукцион: ' + err, true);
        setTimeout(() => {
          if (btnEl && btnEl.isConnected) {
            btnEl.disabled = false;
            btnEl.textContent = 'Купить';
            btnEl.style.background = '#007bff';
            btnEl.title = '';
          }
        }, 2500);
      }
    } catch (e) {
      btnEl.textContent = 'Ошибка';
      btnEl.style.background = '#dc3545';
      showToast('Сбой соединения', true);
      setTimeout(() => {
        if (btnEl && btnEl.isConnected) {
          btnEl.disabled = false;
          btnEl.textContent = 'Купить';
          btnEl.style.background = '#007bff';
        }
      }, 2500);
    }
  }

  function showPlayerDossier(nick) {
    if (!dossierContent || !dossierNick) return;
    dossierNick.textContent = nick;
    dossierNick.title = nick;

    const key = nick.toLowerCase();
    const p = playersMap.get(key) || { nick, sources: new Set(), lastSeen: Date.now(), lotsCount: 0 };
    const citySet = getCityPlayers();
    const mineSet = getMinePlayers();

    // Собираем лоты игрока
    const playerLots = [];
    let totalValue = 0;
    const activeMap = new Map();

    if (lastAuctionData && Array.isArray(lastAuctionData.lots)) {
      for (const l of lastAuctionData.lots) {
        const u = (+l.p || 0) / Math.max(1, +l.n || 1);
        const en = +l.en || 0;
        const b = en > 0 ? (ENCH_BONUS[Math.min(15, en)] || 0) : 0;
        const norm = Math.round(u / (1 + b / 100));
        if (norm > 0) {
          if (!activeMap.has(l.i)) activeMap.set(l.i, []);
          activeMap.get(l.i).push(norm);
        }
      }

      for (const lot of lastAuctionData.lots) {
        const sNick = (lot.sn || lot.s || lot.u || '').trim();
        if (sNick.toLowerCase() === key) {
          const ev = getLotEvaluation(lot, lastAuctionData, activeMap);
          totalValue += (+lot.p || 0);
          playerLots.push({ lot, ev });
        }
      }
    }

    // Определяем точное текущее местоположение
    const isCity = citySet.has(key);
    const isMine = mineSet.has(key);
    const hasLots = playerLots.length > 0;
    const inChat = p.sources.has('💬 Чат');

    let locationHtml = '';
    if (isCity) {
      locationHtml = `<b style="color:#00ff66;">🏛️ Стоит в Городе (на площади)</b>`;
    } else if (isMine) {
      locationHtml = `<b style="color:#ff9900;">⛏️ Копает в Шахте (${escHtml(getMineLabel(currentMineId))})</b>`;
    } else if (hasLots && inChat) {
      locationHtml = `<b style="color:#ffd700;">🏛️ На Рынке & 💬 В Чате</b>`;
    } else if (hasLots) {
      locationHtml = `<b style="color:#ffd700;">🏛️ На Рынке (${playerLots.length} лотов)</b>`;
    } else if (inChat) {
      locationHtml = `<b style="color:#66ccff;">💬 Общается в Чате</b>`;
    } else {
      locationHtml = `<span style="color:#8899aa;">📍 Вне поля зрения (недавно онлайн)</span>`;
    }

    const minAgo = Math.max(0, Math.round((Date.now() - p.lastSeen) / 60000));
    const timeDisplay = isCity || isMine ? 'В игре прямо сейчас (Рядом)' : minAgo <= 1 ? 'Онлайн (только что)' : `${minAgo} мин. назад`;

    // Формируем HTML досье
    dossierContent.innerHTML = `
      <!-- Действия: Обмен, Написать в ЛС и Twitch -->
      <div style="display:flex;gap:5px;margin-bottom:8px;">
        <button id="b-act-trade" style="flex:1;padding:7px 4px;background:#28a745;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;align-items:center;justify-content:center;gap:4px;box-shadow:0 2px 4px rgba(0,0,0,0.3);transition:background 0.15s;" title="Предложить обмен">
          <span>🤝 Обмен</span>
        </button>
        <button id="b-act-whisper" style="flex:1;padding:7px 4px;background:#007bff;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;align-items:center;justify-content:center;gap:4px;box-shadow:0 2px 4px rgba(0,0,0,0.3);transition:background 0.15s;" title="Открыть личные сообщения">
          <span>✉️ В ЛС</span>
        </button>
        <a id="b-act-twitch" href="https://www.twitch.tv/${encodeURIComponent(nick)}" target="_blank" rel="noopener noreferrer" style="flex:1;padding:7px 4px;background:#9146ff;color:#fff;border:none;border-radius:5px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;align-items:center;justify-content:center;gap:4px;text-decoration:none;box-shadow:0 2px 4px rgba(0,0,0,0.3);transition:background 0.15s;" title="Открыть Twitch-канал ${escHtml(nick)} в новой вкладке">
          <span>🟣 Twitch ↗</span>
        </a>
      </div>

      <!-- Блок: Статус и Точное местоположение -->
      <div style="background:rgba(255,255,255,0.04);border:1px solid #3d3759;border-radius:6px;padding:8px 10px;margin-bottom:8px;display:flex;flex-direction:column;gap:5px;">
        <div style="font-size:10px;color:#8899aa;font-weight:bold;text-transform:uppercase;letter-spacing:0.5px;">📍 Где сейчас находится</div>
        <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;">
          <span style="color:#aaa;">Локация:</span>
          <span>${locationHtml}</span>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;">
          <span style="color:#aaa;">Статус:</span>
          <span style="color:${isCity || isMine ? '#00ff66' : '#ffd700'};">${timeDisplay}</span>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;">
          <span style="color:#aaa;">Товаров на рынке:</span>
          <span style="color:#66ccff;font-weight:bold;">${playerLots.length} шт.</span>
        </div>
      </div>

      <!-- Блок: Товары на аукционе -->
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
        <span style="font-size:11px;font-weight:bold;color:#ffd700;">🏛️ Товары на аукционе (${playerLots.length})</span>
        ${playerLots.length > 0 ? `<span style="font-size:10px;color:#8899aa;">Сумма: ~${formatCoinsHtml(totalValue)}</span>` : ''}
      </div>

      <div id="b-dossier-lots-list" style="display:flex;flex-direction:column;gap:5px;">
        ${playerLots.length === 0 ? `
          <div style="text-align:center;padding:16px 8px;color:#666;font-size:11px;background:rgba(0,0,0,0.2);border-radius:6px;border:1px dashed #3d3759;">
            У игрока сейчас нет активных лотов на аукционе
          </div>
        ` : playerLots.map(({ lot, ev }) => {
          const col = RAR_COLORS[ev.rarity] || '#aaa';
          const sourceHtml = ev.sourceType === 'shop'
            ? `В лавке: <b>${formatCoinsHtml(ev.fairTotal)}</b>`
            : ev.sourceType === 'val'
            ? `Ценность: <b>${formatCoinsHtml(ev.fairTotal)}</b>`
            : ev.fairTotal > 0
            ? `Рынок: ~<b>${formatCoinsHtml(ev.fairTotal)}</b>`
            : `Оценка: нет данных`;

          return `
            <div style="background:rgba(255,255,255,0.04);border:1px solid ${col};border-radius:6px;padding:6px 8px;display:flex;gap:8px;align-items:center;box-shadow:0 2px 6px rgba(0,0,0,0.3);">
              <div class="b-lot-art" style="border-color:${col};" title="${ev.name}${ev.en > 0 ? ' +' + ev.en : ''}">
                ${getItemArtHtml(lot, ev.def, col)}
                ${ev.en > 0 ? `<span style="position:absolute;bottom:0px;right:2px;font-size:9px;font-weight:bold;color:#00ff66;text-shadow:0 0 3px #000, 1px 1px 1px #000;line-height:1;pointer-events:none;">+${ev.en}</span>` : ''}
                ${ev.n > 1 ? `<span style="position:absolute;top:0px;left:2px;font-size:9px;font-weight:bold;color:#ffd700;text-shadow:0 0 3px #000, 1px 1px 1px #000;line-height:1;pointer-events:none;">×${ev.n}</span>` : ''}
              </div>
              <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:3px;">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:4px;">
                  <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                    <span style="font-weight:bold;color:${col};font-size:11px;">${ev.name}</span>
                    ${ev.en > 0 ? `<b style="color:#00ff66;font-size:11px;"> +${ev.en}</b>` : ''}
                    ${ev.n > 1 ? `<span style="color:#ffd700;font-size:10px;"> ×${ev.n}</span>` : ''}
                  </div>
                  ${ev.discount >= 20 ? `<span style="background:#28a745;color:#fff;padding:1px 5px;border-radius:8px;font-size:9px;font-weight:bold;white-space:nowrap;flex-shrink:0;">−${ev.discount}% 🔥</span>` : ''}
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;font-size:10px;gap:4px;">
                  <span style="color:#8899aa;">Цена:</span>
                  <span style="color:#ffd700;font-weight:bold;font-size:11px;white-space:nowrap;flex-shrink:0;">${formatCoinsHtml(ev.price)}</span>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;font-size:9px;color:#8899aa;border-top:1px dashed rgba(255,255,255,0.08);padding-top:4px;margin-top:2px;gap:4px;">
                  <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${sourceHtml}</span>
                  <button data-dossier-buy="${lot.id}" data-dossier-price="${lot.p}" style="padding:3px 9px;background:#007bff;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:10px;font-weight:bold;transition:background 0.2s;flex-shrink:0;">Купить</button>
                </div>
              </div>
            </div>
          `;
        }).join('')}
      </div>
    `;

    // Привязываем кнопки действий
    const btnTrade = dossierContent.querySelector('#b-act-trade');
    if (btnTrade) btnTrade.onclick = () => openTradeWith(nick);

    const btnWhisper = dossierContent.querySelector('#b-act-whisper');
    if (btnWhisper) btnWhisper.onclick = () => openWhisperWith(nick);

    const btnTwitch = dossierContent.querySelector('#b-act-twitch');
    if (btnTwitch) {
      btnTwitch.onmouseenter = () => { btnTwitch.style.background = '#772ce8'; };
      btnTwitch.onmouseleave = () => { btnTwitch.style.background = '#9146ff'; };
    }

    // Привязываем кнопки покупки товаров игрока
    dossierContent.querySelectorAll('[data-dossier-buy]').forEach(btnEl => {
      btnEl.onclick = () => {
        const lotId = btnEl.getAttribute('data-dossier-buy');
        const price = +btnEl.getAttribute('data-dossier-price');
        executeBuyLot(lotId, price, btnEl, () => {
          showPlayerDossier(nick);
        });
      };
    });
  }

  // Обработчики поисковой строки и кнопки обновления списка игроков
  if (playersRefresh) {
    playersRefresh.onclick = () => {
      scanPlayers();
      showToast('Список игроков обновлен');
    };
  }
  if (playersClearBtn) {
    playersClearBtn.onclick = () => {
      if (confirm(`Очистить сохранённую базу игроков (${playersMap.size} чел)?`)) {
        playersMap.clear();
        try { localStorage.removeItem(STORAGE_PLAYERS_KEY); } catch (e) {}
        scanPlayers();
        showToast('База игроков очищена');
      }
    };
  }
  if (playersSearch) {
    playersSearch.oninput = () => {
      renderPlayersList();
    };
  }

  // ---------- МОДУЛЬ: МАССОВАЯ РАССЫЛКА В ЛС (ЛИЧНЫЕ СООБЩЕНИЯ) ----------
  const bcRefreshBtn = w.querySelector('#b-bc-refresh');
  const bcFilterCityBtn = w.querySelector('#b-bc-filter-city');
  const bcFilterOnlineBtn = w.querySelector('#b-bc-filter-online');
  const bcFilterMarketBtn = w.querySelector('#b-bc-filter-market');
  const bcFilterAllBtn = w.querySelector('#b-bc-filter-all');
  const bcTargetCount = w.querySelector('#b-bc-target-count');
  const bcText = w.querySelector('#b-bc-text');
  const bcChars = w.querySelector('#b-bc-chars');
  const bcDelaySelect = w.querySelector('#b-bc-delay');
  const bcStartBtn = w.querySelector('#b-bc-start');
  const bcPauseBtn = w.querySelector('#b-bc-pause');
  const bcStopBtn = w.querySelector('#b-bc-stop');
  const bcProgressWrap = w.querySelector('#b-bc-progress-wrap');
  const bcStateLabel = w.querySelector('#b-bc-state-label');
  const bcStateNums = w.querySelector('#b-bc-state-nums');
  const bcBar = w.querySelector('#b-bc-bar');
  const bcCntOk = w.querySelector('#b-bc-cnt-ok');
  const bcCntRl = w.querySelector('#b-bc-cnt-rl');
  const bcCntErr = w.querySelector('#b-bc-cnt-err');
  const bcLogEl = w.querySelector('#b-bc-log');
  const bcClearLogBtn = w.querySelector('#b-bc-clear-log');

  let broadcastAudienceFilter = 'online'; // Default 'online' (весь онлайн: город + шахта). 'city' | 'online' | 'market' | 'all'
  let isBroadcasting = false;
  let isBroadcastPaused = false;
  let broadcastAbortRequested = false;

  function waitBroadcastMs(ms) {
    return new Promise(resolve => {
      const start = Date.now();
      const check = () => {
        if (broadcastAbortRequested || !window.__mineBotRunning || Date.now() - start >= ms) {
          resolve();
        } else {
          setTimeout(check, 100);
        }
      };
      check();
    });
  }

  function addBcLog(msg, color = '#aaa') {
    if (!bcLogEl) return;
    const item = document.createElement('div');
    item.style.color = color;
    item.style.wordBreak = 'break-word';
    item.style.lineHeight = '1.3';
    item.textContent = msg;
    bcLogEl.appendChild(item);
    bcLogEl.scrollTop = bcLogEl.scrollHeight;
  }

  function getBroadcastRecipients() {
    const myNick = (getMyNick() || '').trim().toLowerCase();
    const citySet = getCityPlayers();
    const mineSet = getMinePlayers();
    const list = [];

    playersMap.forEach(p => {
      const nick = (p.nick || '').trim();
      const key = nick.toLowerCase();
      if (!key || (myNick && key === myNick)) return;

      const inCity = citySet.has(key);
      const isOnline = inCity || mineSet.has(key);
      const hasLots = (p.lotsCount || 0) > 0;

      if (broadcastAudienceFilter === 'city') {
        if (inCity) {
          list.push({ nick, inCity: true, online: true, lots: p.lotsCount || 0 });
        }
      } else if (broadcastAudienceFilter === 'online') {
        if (isOnline) {
          list.push({ nick, inCity, online: true, lots: p.lotsCount || 0 });
        }
      } else if (broadcastAudienceFilter === 'market') {
        if (hasLots) {
          list.push({ nick, inCity, online: isOnline, lots: p.lotsCount || 0 });
        }
      } else {
        // 'all' — все обнаруженные игроки с вкладки «Игроки»
        list.push({ nick, inCity, online: isOnline, lots: p.lotsCount || 0 });
      }
    });

    // Сортировка: сначала те, кто в городе, затем онлайн, затем торговцы с лотами, затем по алфавиту
    list.sort((a, b) => {
      if (b.inCity !== a.inCity) return (b.inCity ? 1 : 0) - (a.inCity ? 1 : 0);
      if (b.online !== a.online) return (b.online ? 1 : 0) - (a.online ? 1 : 0);
      if (b.lots !== a.lots) return b.lots - a.lots;
      return a.nick.localeCompare(b.nick);
    });

    return list.map(item => item.nick);
  }

  function updateBroadcastRecipientsCount() {
    const recipients = getBroadcastRecipients();
    const cnt = recipients.length;
    if (bcTargetCount) {
      bcTargetCount.textContent = `${cnt} игроков`;
    }
  }

  function setBroadcastFilter(filterType) {
    broadcastAudienceFilter = filterType;
    const filterBtns = [bcFilterCityBtn, bcFilterOnlineBtn, bcFilterMarketBtn, bcFilterAllBtn];
    filterBtns.forEach(b => {
      if (!b) return;
      b.style.background = '#262238';
      b.style.color = '#aaa';
      b.style.border = '1px solid #3d3759';
      b.style.fontWeight = 'normal';
    });
    const activeBtn = filterType === 'city' ? bcFilterCityBtn : filterType === 'online' ? bcFilterOnlineBtn : filterType === 'market' ? bcFilterMarketBtn : bcFilterAllBtn;
    if (activeBtn) {
      activeBtn.style.background = '#d946ef';
      activeBtn.style.color = '#fff';
      activeBtn.style.border = 'none';
      activeBtn.style.fontWeight = 'bold';
    }
    updateBroadcastRecipientsCount();
  }

  if (bcFilterCityBtn) bcFilterCityBtn.onclick = () => setBroadcastFilter('city');
  if (bcFilterOnlineBtn) bcFilterOnlineBtn.onclick = () => setBroadcastFilter('online');
  if (bcFilterMarketBtn) bcFilterMarketBtn.onclick = () => setBroadcastFilter('market');
  if (bcFilterAllBtn) bcFilterAllBtn.onclick = () => setBroadcastFilter('all');
  updateBroadcastRecipientsCount();

  if (bcRefreshBtn) {
    bcRefreshBtn.onclick = async () => {
      if (typeof refreshLiveCityPlayers === 'function') await refreshLiveCityPlayers();
      if (typeof scanPlayers === 'function') scanPlayers();
      updateBroadcastRecipientsCount();
      showToast('Список игроков и город актуализированы');
    };
  }

  if (bcClearLogBtn) {
    bcClearLogBtn.onclick = () => {
      if (bcLogEl) bcLogEl.innerHTML = '<div style="color:#666;text-align:center;padding:12px 0;">Журнал очищен</div>';
    };
  }

  if (bcText) {
    bcText.oninput = () => {
      const len = bcText.value.length;
      if (bcChars) {
        bcChars.textContent = String(len);
        bcChars.style.color = len >= 190 ? '#ef4444' : len >= 160 ? '#f59e0b' : '#00ff66';
      }
    };
  }

  function updateBcProgress(current, total, ok, rl, err) {
    if (bcStateNums) bcStateNums.textContent = `${current} / ${total}`;
    if (bcBar) {
      const pct = total > 0 ? Math.round((current / total) * 100) : 0;
      bcBar.style.width = `${pct}%`;
    }
    if (bcCntOk) bcCntOk.textContent = String(ok);
    if (bcCntRl) bcCntRl.textContent = String(rl);
    if (bcCntErr) bcCntErr.textContent = String(err);
  }

  // Генератор уникального сообщения (обход анти-спам проверки сервера «это уже было»)
  function formatBroadcastMessage(rawText, targetNick, index, attempt = 1) {
    let msg = (rawText || '').trim();

    // 1. Подстановка макроса {ник} или {nick} (персональное обращение)
    if (/\{ник\}|\{nick\}/i.test(msg)) {
      msg = msg.replace(/\{ник\}|\{nick\}/gi, targetNick);
    }

    // 2. Невидимые нуль-символы для гарантированного разрыва идентичности строк
    const zeroWidths = [
      '\u200B',
      '\u200C',
      '\u200D',
      '\u2060',
      '\u200B\u200C',
      '\u200C\u200D',
      '\u200D\u2060',
      '\u2060\u200B'
    ];
    const zw = zeroWidths[index % zeroWidths.length];

    // 3. Видимая микро-пунктуация (на случай, если сервер чистит нуль-символы)
    // Чередуем неброские варианты хвоста: '.', ' .', ' ·', '..', ' ~', ' -'
    const visSalts = ['.', ' .', ' ·', '..', ' ~', ' -'];
    const saltIdx = (index + (attempt > 1 ? (attempt * 2) : 0)) % visSalts.length;
    const visTail = visSalts[saltIdx];

    // Контроль длины в пределах 200 символов
    const reserved = zw.length + visTail.length;
    if (msg.length + reserved > 200) {
      msg = msg.slice(0, 200 - reserved);
    }

    return msg + zw + visTail;
  }

  async function startBroadcastFlow() {
    if (isBroadcasting) return;
    const text = (bcText && bcText.value || '').trim();
    if (!text) {
      showToast('Введите текст сообщения для рассылки!', true);
      if (bcText) bcText.focus();
      return;
    }
    if (text.length > 200) {
      showToast('Сообщение не должно превышать 200 символов!', true);
      return;
    }

    if (broadcastAudienceFilter === 'city') {
      if (typeof refreshLiveCityPlayers === 'function') await refreshLiveCityPlayers();
    }
    if (typeof scanPlayers === 'function') scanPlayers(true);
    const recipients = getBroadcastRecipients();
    if (recipients.length === 0) {
      if (broadcastAudienceFilter === 'city') {
        showToast('В городе сейчас никого нет (кроме вас)! Нажмите 🔄 для проверки.', true);
      } else {
        showToast('Список получателей пуст! Нажмите 🔄 для сканирования.', true);
      }
      return;
    }

    const shortPreview = text.length > 55 ? text.slice(0, 55) + '...' : text;
    if (!confirm(`📢 Запустить рассылку в ЛС для ${recipients.length} игроков?\n\nТекст сообщения:\n"${shortPreview}"\n\nБот будет отправлять сообщения с безопасной паузой.`)) {
      return;
    }

    isBroadcasting = true;
    isBroadcastPaused = false;
    broadcastAbortRequested = false;

    if (bcStartBtn) bcStartBtn.style.display = 'none';
    if (bcPauseBtn) {
      bcPauseBtn.style.display = 'flex';
      bcPauseBtn.innerHTML = '<span>⏸️ Пауза</span>';
      bcPauseBtn.style.background = '#f59e0b';
      bcPauseBtn.style.color = '#000';
    }
    if (bcStopBtn) bcStopBtn.style.display = 'flex';
    if (bcProgressWrap) bcProgressWrap.style.display = 'block';
    if (bcText) bcText.disabled = true;

    if (bcLogEl) bcLogEl.innerHTML = '';
    const delayMs = parseInt((bcDelaySelect && bcDelaySelect.value) || '3500', 10);
    addBcLog(`🚀 Старт рассылки: ${recipients.length} получателей. Задержка: ${(delayMs / 1000).toFixed(1)} сек.`, '#38bdf8');

    let okCount = 0;
    let rlCount = 0;
    let errCount = 0;

    for (let i = 0; i < recipients.length; i++) {
      if (broadcastAbortRequested || !window.__mineBotRunning) {
        addBcLog(`⏹️ Рассылка остановлена пользователем.`, '#ef4444');
        break;
      }

      // Ожидание при паузе
      while (isBroadcastPaused && !broadcastAbortRequested && window.__mineBotRunning) {
        if (bcStateLabel) bcStateLabel.textContent = '⏸️ На паузе...';
        await waitBroadcastMs(400);
      }
      if (broadcastAbortRequested || !window.__mineBotRunning) break;

      if (bcStateLabel) bcStateLabel.textContent = `Отправка (${i + 1}/${recipients.length})...`;
      updateBcProgress(i, recipients.length, okCount, rlCount, errCount);

      const targetNick = recipients[i];
      const timeStr = new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

      // Если выбран режим "Только в Городе", делаем дополнительную живую проверку перед отправкой:
      if (broadcastAudienceFilter === 'city') {
        const liveCity = getCityPlayers();
        if (!liveCity.has(targetNick.toLowerCase())) {
          addBcLog(`[${timeStr}] ⏩ [${targetNick}] вышел из города, пропускаем`, '#f59e0b');
          continue;
        }
      }

      addBcLog(`[${timeStr}] ✉️ (${i + 1}/${recipients.length}) Отправка для [${targetNick}]...`, '#aaa');

      let delivered = false;
      let attempt = 0;

      while (attempt < 3 && !delivered && !broadcastAbortRequested && window.__mineBotRunning) {
        attempt++;
        const uniqueText = formatBroadcastMessage(text, targetNick, i, attempt);
        try {
          const res = await gameApi('/api/city/say', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              text: uniqueText,
              to: targetNick
            })
          });

          if (res && res.ok) {
            delivered = true;
            okCount++;
            addBcLog(`[${timeStr}] ✔ Доставлено игроку [${targetNick}]`, '#00ff66');
          } else {
            const err = String((res && (res.error || res.err)) || 'неизвестная ошибка');
            if (err.includes('это уже было') || err.includes('уже было')) {
              addBcLog(`[${timeStr}] ⚠️ «Это уже было» — уникализируем текст для [${targetNick}]...`, '#ffd700');
              await waitBroadcastMs(1200);
              if (attempt >= 3) {
                errCount++;
                addBcLog(`[${timeStr}] ❌ [${targetNick}]: не удалось обойти фильтр повтора`, '#ef4444');
              }
            } else if (err === 'rate_limited' || /слишком часто|передохни|не так часто/i.test(err)) {
              rlCount++;
              const isMinuteThrottled = /слишком часто|передохни/i.test(err);
              const waitSec = isMinuteThrottled ? 62 : 6;
              addBcLog(`[${timeStr}] ⏳ Кулдаун чата («${err}»)! Ожидание ${waitSec} сек...`, '#ffd700');
              for (let s = waitSec; s > 0; s--) {
                if (broadcastAbortRequested || !window.__mineBotRunning) break;
                while (isBroadcastPaused && !broadcastAbortRequested && window.__mineBotRunning) {
                  if (bcStateLabel) bcStateLabel.textContent = '⏸️ На паузе...';
                  await waitBroadcastMs(400);
                }
                if (bcStateLabel) bcStateLabel.textContent = `⏳ Кулдаун сервера: ждём ${s} сек...`;
                await waitBroadcastMs(1000);
              }
              if (attempt >= 3) {
                errCount++;
                addBcLog(`[${timeStr}] ❌ [${targetNick}]: превышен лимит попыток`, '#ef4444');
              } else {
                addBcLog(`[${timeStr}] 🔄 Повторная отправка для [${targetNick}]...`, '#38bdf8');
              }
            } else {
              errCount++;
              const errMsg = err === 'city_closed' ? 'город закрыт' : err === 'forbidden' ? 'нет прав на ЛС' : err;
              addBcLog(`[${timeStr}] ❌ [${targetNick}]: ${errMsg}`, '#ef4444');
              break;
            }
          }
        } catch (e) {
          errCount++;
          addBcLog(`[${timeStr}] ❌ [${targetNick}]: ошибка сети`, '#ef4444');
          break;
        }
      }

      updateBcProgress(i + 1, recipients.length, okCount, rlCount, errCount);

      // Задержка перед следующим сообщением (плюс случайный джиттер 0..600мс)
      if (i < recipients.length - 1 && !broadcastAbortRequested && window.__mineBotRunning) {
        const jitter = Math.floor(Math.random() * 600);
        await waitBroadcastMs(delayMs + jitter);
      }
    }

    isBroadcasting = false;
    isBroadcastPaused = false;
    broadcastAbortRequested = false;

    if (bcStartBtn) bcStartBtn.style.display = 'flex';
    if (bcPauseBtn) bcPauseBtn.style.display = 'none';
    if (bcStopBtn) bcStopBtn.style.display = 'none';
    if (bcText) bcText.disabled = false;
    if (bcStateLabel) bcStateLabel.textContent = 'Рассылка завершена';

    addBcLog(`🏁 Завершено! Доставлено: ${okCount}, Ошибок: ${errCount}, Рейт-лимитов: ${rlCount}`, '#00ff66');
    showToast(`Рассылка завершена: доставлено ${okCount} из ${recipients.length}`);
  }

  if (bcStartBtn) bcStartBtn.onclick = startBroadcastFlow;

  if (bcPauseBtn) {
    bcPauseBtn.onclick = () => {
      isBroadcastPaused = !isBroadcastPaused;
      if (isBroadcastPaused) {
        bcPauseBtn.innerHTML = '<span>▶️ Продолжить</span>';
        bcPauseBtn.style.background = '#28a745';
        bcPauseBtn.style.color = '#fff';
        addBcLog(`⏸️ Рассылка поставлена на паузу`, '#ffd700');
      } else {
        bcPauseBtn.innerHTML = '<span>⏸️ Пауза</span>';
        bcPauseBtn.style.background = '#f59e0b';
        bcPauseBtn.style.color = '#000';
        addBcLog(`▶️ Возобновление рассылки...`, '#00ff66');
      }
    };
  }

  if (bcStopBtn) {
    bcStopBtn.onclick = () => {
      if (confirm('Остановить текущую рассылку?')) {
        broadcastAbortRequested = true;
        isBroadcastPaused = false;
      }
    };
  }

  // ---------- МОДУЛЬ: ОРИГИНАЛЬНЫЙ UI ГОРОДА (Лавка, Когтекузня, Тренер, Склад, Аукцион) ----------
  const btnNatAuction = w.querySelector('#b-nat-auction');
  const btnNatShop = w.querySelector('#b-nat-shop');
  const btnNatAnvil = w.querySelector('#b-nat-anvil');
  const btnNatTrainer = w.querySelector('#b-nat-trainer');
  const btnNatStorage = w.querySelector('#b-nat-storage');
  const btnNatToggleHud = w.querySelector('#b-nat-toggle-hud');

  // Функция гарантированного открытия официального нативного интерфейса игры из любой точки
  async function openNativeBuilding(roomId) {
    if (roomId === 'anvil') {
      const townWhere = (G.LGCity && typeof G.LGCity.where === 'function' && G.LGCity.where()) || {};
      const isCamp = townWhere.id === 'camp' || G.ME?.hero?.town === 'camp';
      if (isCamp) {
        showToast('⛺ В лагере «Перепутье» доступна только починка (стол Заточки откроется в городе канала после пролога)', true);
      }
    }
    showToast(`Открытие нативного окна «${roomId}»...`);

    // 1. Снимаем блокировку похода (quest lockout / cityLocked)
    if (G.__lgQuestContext && typeof G.__lgQuestContext.openTown === 'function') {
      try { G.__lgQuestContext.openTown('fair'); } catch (e) {}
    }

    // 2. Гарантируем загрузку скриптов города
    if (!G.LGCity && typeof G.cityFiles === 'function') {
      try { await G.cityFiles(); } catch (e) {}
    }

    // 3. Разблокируем кнопку Города в шапке игры и переключаем вкладку
    const cityTabBtn = document.querySelector('.tabs button[data-tab="city"]');
    if (cityTabBtn) {
      cityTabBtn.hidden = false;
      cityTabBtn.removeAttribute('aria-disabled');
      cityTabBtn.classList.remove('tab-lock');
      try { cityTabBtn.click(); } catch (e) {}
    }

    // Принудительно выставляем класс .on для вкладки города в DOM
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t.id === 'tab-city'));
    document.querySelectorAll('.tabs button').forEach(b => {
      if (b.dataset.tab === 'city') b.setAttribute('aria-current', 'page');
      else b.removeAttribute('aria-current');
    });

    // 4. Ожидаем инициализации LGCity и открываем официальное окно игры
    let attempts = 0;
    const interval = setInterval(() => {
      attempts++;
      if (G.LGCity && (typeof G.LGCity.room === 'function' || typeof G.LGCity.panel === 'function')) {
        try {
          const fn = G.LGCity.room || G.LGCity.panel;
          fn(roomId);
          clearInterval(interval);
        } catch (err) {
        }
      }
      if (attempts > 30) {
        clearInterval(interval);
        showToast('Город еще загружается. Повторите нажатие через секунду.', true);
      }
    }, 100);
  }

  // Привязка кнопок вкладки «Город»
  if (btnNatAuction) btnNatAuction.onclick = () => openNativeBuilding('auction');
  if (btnNatShop) btnNatShop.onclick = () => openNativeBuilding('shop');
  if (btnNatAnvil) btnNatAnvil.onclick = () => openNativeBuilding('anvil');
  if (btnNatTrainer) btnNatTrainer.onclick = () => openNativeBuilding('trainer');
  if (btnNatStorage) btnNatStorage.onclick = () => openNativeBuilding('storage');
  if (btnNatToggleHud) btnNatToggleHud.onclick = () => toggleTopHud();

  // Плавающий топ-бар быстрого вызова нативных окон (без кнопок возврата)
  function toggleTopHud(force) {
    let hud = document.getElementById('native-portal-hud');
    const enable = force !== undefined ? force : !hud;
    if (!enable) {
      if (hud) hud.remove();
      localStorage.setItem('lg_bot_top_hud', '0');
      showToast('Плавающий бар скрыт');
    } else {
      if (!hud) {
        hud = document.createElement('div');
        hud.id = 'native-portal-hud';
        hud.style.cssText = `
          position: fixed;
          top: 10px;
          left: 50%;
          transform: translateX(-50%);
          background: rgba(18, 14, 32, 0.95);
          border: 2px solid #f59e0b;
          border-radius: 30px;
          padding: 6px 14px;
          display: flex;
          align-items: center;
          gap: 8px;
          z-index: 9999999;
          box-shadow: 0 8px 30px rgba(0,0,0,0.85);
          backdrop-filter: blur(8px);
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        `;
        hud.innerHTML = `
          <span style="color:#f59e0b;font-weight:bold;font-size:11px;margin-right:4px;">⚡ Город:</span>
          <button data-nb="auction" style="padding:5px 10px;background:#007bff;color:#fff;border:none;border-radius:15px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;align-items:center;gap:4px;">🏛️ Аукцион</button>
          <button data-nb="shop" style="padding:5px 10px;background:#28a745;color:#fff;border:none;border-radius:15px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;align-items:center;gap:4px;">🏪 Лавка</button>
          <button data-nb="anvil" style="padding:5px 10px;background:#a855f7;color:#fff;border:none;border-radius:15px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;align-items:center;gap:4px;">🔨 Когтекузня</button>
          <button data-nb="trainer" style="padding:5px 10px;background:#eab308;color:#000;border:none;border-radius:15px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;align-items:center;gap:4px;">🎓 Тренер</button>
          <button data-nb="storage" style="padding:5px 10px;background:#6c757d;color:#fff;border:none;border-radius:15px;cursor:pointer;font-size:11px;font-weight:bold;display:flex;align-items:center;gap:4px;">📦 Склад</button>
          <button id="nh-close" style="background:none;border:none;color:#aaa;cursor:pointer;font-size:14px;font-weight:bold;padding:0 4px;margin-left:4px;" title="Закрыть бар">✕</button>
        `;
        document.body.appendChild(hud);
        hud.querySelector('#nh-close').onclick = () => toggleTopHud(false);
        hud.querySelectorAll('[data-nb]').forEach(b => {
          b.onclick = () => openNativeBuilding(b.dataset.nb);
        });
      }
      localStorage.setItem('lg_bot_top_hud', '1');
      showToast('Плавающий бар включен');
    }
  }

  if (localStorage.getItem('lg_bot_top_hud') === '1') {
    toggleTopHud(true);
  }

  function initPocketCity() {
    // Вкладка открыта, кнопки нативных окон готовы
  }

  // =========================================================================
  // ⚔️ ПОЛНОЦЕННЫЙ МОДУЛЬ ПОХОДА (QUEST / DUNGEON / BOSS BATTLE)
  // =========================================================================

  // Элементы интерфейса вкладки Похода
  const qLoc = w.querySelector('#b-quest-loc');
  const qIcon = w.querySelector('#b-quest-icon');
  const qPartySt = w.querySelector('#b-quest-party-st');
  const qTimer = w.querySelector('#b-quest-timer');
  const qPhase = w.querySelector('#b-quest-phase');
  const qBossInfo = w.querySelector('#b-quest-boss-info');
  const qAlert = w.querySelector('#b-quest-alert');
  const qAlertTitle = w.querySelector('#b-quest-alert-title');
  const qAlertMsg = w.querySelector('#b-quest-alert-msg');
  const qDgnCard = w.querySelector('#b-quest-dgn-card');
  const qDgnRoom = w.querySelector('#b-quest-dgn-room');
  const qDgnSecret = w.querySelector('#b-quest-dgn-secret');
  const qDgnVotes = w.querySelector('#b-quest-dgn-votes');
  const qTurnStatus = w.querySelector('#b-quest-turn-status');
  const qLogList = w.querySelector('#b-quest-log-list');

  // Тумблеры настроек Похода
  const optAutoJoin = w.querySelector('#b-quest-opt-autojoin');
  const optDgnVote = w.querySelector('#b-quest-opt-dgnvote');
  const optFastForward = w.querySelector('#b-quest-opt-fastforward');
  const optAutoParry = w.querySelector('#b-quest-opt-autoparry');
  const optDgnSolver = w.querySelector('#b-quest-opt-dgn');
  const optSolveCode = w.querySelector('#b-quest-opt-solve-code');
  const optCombat = w.querySelector('#b-quest-opt-combat');
  const optQaMode = w.querySelector('#b-quest-opt-qa');
  const optCrossing = w.querySelector('#b-quest-opt-crossing');
  const optBog = w.querySelector('#b-quest-opt-bog');
  const optHwEvents = w.querySelector('#b-quest-opt-hwevents');

  // Кнопки быстрых действий и скиллов
  const qClassBadge = w.querySelector('#b-quest-class-badge');
  const btnActAtk = w.querySelector('#b-quest-act-atk');
  const btnActSub = w.querySelector('#b-quest-act-sub');
  const btnActDef = w.querySelector('#b-quest-act-def');
  const btnActSquad = w.querySelector('#b-quest-act-squad');
  const btnClearLog = w.querySelector('#b-quest-clear-log');
  const qSkillsTitle = w.querySelector('#b-quest-skills-title');
  const qSkillsCount = w.querySelector('#b-quest-skills-count');
  const qSkillsList = w.querySelector('#b-quest-skills-list');

  // Настройки и состояние Похода
  const questSettings = {
    autoJoin: localStorage.getItem('lg_quest_autojoin') !== '0',       // Вкл по умолчанию (100% автопоход: авто-запись)
    autoCombat: localStorage.getItem('lg_quest_combat') !== '0',       // Вкл по умолчанию (100% умный авто-бой)
    dungeonAutoVote: localStorage.getItem('lg_quest_dgnvote') !== '0',  // Вкл по умолчанию (авто-выбор дверей)
    fastForward: localStorage.getItem('lg_quest_fastforward') !== '0', // Вкл по умолчанию (100% стрим без задержки)
    autoParry: localStorage.getItem('lg_quest_autoparry') !== '0',     // Вкл по умолчанию (100% авто-блок телеграфа)
    dungeonSolver: localStorage.getItem('lg_quest_solver') !== '0',    // Вкл по умолчанию (радар тайников)
    autoSolveCode: localStorage.getItem('lg_quest_autosolve_code') !== '0', // Вкл по умолчанию (декодер кода сокровищницы при <=2 неразгаданных)
    qaMode: localStorage.getItem('lg_quest_qamode') !== '0',           // Разблокировка QA-кнопок игры
    autoCrossing: localStorage.getItem('lg_quest_autocrossing') !== '0', // Вкл по умолчанию (авто-выбор безопасной кочки)
    autoBog: localStorage.getItem('lg_quest_autobog') !== '0',           // Вкл по умолчанию (авто-тянем и авто-руби в трясине)
    autoHwEvents: localStorage.getItem('lg_quest_autohwevents') !== '0'  // Вкл по умолчанию (авто-котёл и сладость/гадость)
  };

  let questAutoPausedMiner = false;
  let lastAutoJoinTime = 0;
  let lastAutoJoinKey = null;
  let lastDgnVoteId = null;
  let lastMapVoteId = null;

  function checkAutoJoinSquad(v) {
    if (!questSettings.autoJoin || !v || !v.on) return;
    const party = v.party || 'out';
    if (party === 'in' || party === 'q' || party === 'ghost') return;

    const isGather = !!v.gather;
    const isHw = isGather && !!v.gather.hw;
    const now = Date.now();

    const joinKey = `${v.id || 'q'}:${isGather ? (v.gather.until || 1) : (v.t || v.rn || 'move')}:${isHw ? 'hw' : 'reg'}`;
    if (lastAutoJoinKey === joinKey && now - lastAutoJoinTime < 7000) return;

    lastAutoJoinKey = joinKey;
    lastAutoJoinTime = now;

    sendQuestAct({ k: 'join' }).then(res => {
      if (res && res.ok !== false) {
        addQuestLog('speed', isHw ? '🎃 Автопоход: шаг в портал Изнанки!' : isGather ? '🤝 Автопоход: отправлена запись в отряд!' : '🚶 Автопоход: запись в движущийся отряд (очередь)!');
      }
      if (isGather) {
        if (isHw) sendQuestSay('портал'); else sendQuestSay('поход');
      }
      refreshQuestUI();
    }).catch(() => {
      if (isGather) {
        if (isHw) sendQuestSay('портал'); else sendQuestSay('поход');
      }
    });
  }

  function checkQuestMinerCoordination(v) {
    if (!questSettings.autoJoin) return;
    const inQuest = !!(v && v.on && (v.party === 'in' || v.party === 'ghost'));

    if (inQuest) {
      if (!questAutoPausedMiner) {
        questAutoPausedMiner = true;
        st.textContent = '⚔️ В походе с отрядом (шахта на авто-паузе)';
        st.style.color = '#f43f5e';
        addQuestLog('info', '⏸️ Авто-пауза майнера на время похода');
        showToast('⚔️ Герой в походе! Шахта временно поставлена на паузу.');
      }
    } else {
      if (questAutoPausedMiner) {
        questAutoPausedMiner = false;
        st.textContent = '⛏️ Возврат в шахту после похода...';
        st.style.color = '#38bdf8';
        addQuestLog('info', '▶️ Поход окончен, возобновление авто-добычи');
        showToast('🎉 Поход завершен! Возобновляем авто-копание в шахте.');
        setTimeout(() => {
          tryOpenMineOnScreen(currentMineId, true);
        }, 1200);
      }
    }
  }

  function checkAutoVotes(q) {
    if (!questSettings.dungeonAutoVote || !q) return;
    const party = q?.party || (lastQuestData && lastQuestData.party);
    if (party !== 'in') return;

    // 1. Голосование за двери в Данже (q.dgn.vote)
    if (q.dgn && q.dgn.vote) {
      const V = q.dgn.vote;
      if (!V.done && V.my == null && Array.isArray(V.opts) && V.opts.length > 0) {
        const voteId = String(V.id || `${q.dgn.room?.seed || 'rm'}:${V.opts.length}`);
        if (lastDgnVoteId !== voteId) {
          lastDgnVoteId = voteId;

          // Исключаем проходы назад (opt[5] === true) при первичном выборе
          const forwardOpts = V.opts.filter(o => !o[5]);
          const candidateOpts = forwardOpts.length > 0 ? forwardOpts : V.opts;

          let bestOpt = candidateOpts[0];
          let maxVotes = -1;
          for (const opt of candidateOpts) {
            const votes = typeof opt[4] === 'number' ? opt[4] : 0;
            if (votes > maxVotes) {
              maxVotes = votes;
              bestOpt = opt;
            }
          }

          // Поиск тайника: строго по o[1] === 'secret' или слову в названии (без searchAnswer стены!)
          const secretOpt = candidateOpts.find(o => o[1] === 'secret' || (o[2] && String(o[2]).toLowerCase().includes('тайник')));
          // Поиск спуска на следующий этаж
          const descentOpt = candidateOpts.find(o => /down|shaft|pit|stairs/i.test(String(o[1] || '')) || /спуск|этаж/i.test(String(o[2] || '')));

          let chosenOpt = bestOpt;
          let reason = 'большинство отряда';

          if (secretOpt && maxVotes <= 1) {
            chosenOpt = secretOpt;
            reason = 'тайник радара ⭐';
          } else if (descentOpt && maxVotes <= 1) {
            chosenOpt = descentOpt;
            reason = 'спуск на новый этаж ⬇️';
          } else if (maxVotes > 0) {
            chosenOpt = bestOpt;
            reason = `большинство (${maxVotes} голосов)`;
          }

          const chosenNum = chosenOpt[0];
          const chosenName = chosenOpt[2] || chosenOpt[1] || `Дверь №${chosenNum}`;

          setTimeout(() => {
            sendQuestVote(V.id, chosenNum).then(res => {
              if (res && res.ok !== false) {
                addQuestLog('puzzle', `🚪 Данж: авто-выбор «${chosenName}» (${reason}, вариант ${chosenNum})`);
              }
              refreshQuestUI();
            });
          }, 1200);
          return;
        }
      }
    }

    // 2. Голосование на развилках карты квеста (q.vote)
    if (q.vote && !q.vote.done && q.vote.my == null && Array.isArray(q.vote.opts) && q.vote.opts.length > 0) {
      const V = q.vote;
      const voteId = String(V.id || `v_${V.opts.length}`);
      if (lastMapVoteId !== voteId) {
        lastMapVoteId = voteId;

        // В q.vote: opt[3] — голоса, opt[4] — назад
        const forwardOpts = V.opts.filter(o => !o[4]);
        const candidateOpts = forwardOpts.length > 0 ? forwardOpts : V.opts;

        let bestOpt = candidateOpts[0];
        let maxVotes = -1;
        for (const opt of candidateOpts) {
          const votes = typeof opt[3] === 'number' ? opt[3] : 0;
          if (votes > maxVotes) {
            maxVotes = votes;
            bestOpt = opt;
          }
        }

        const chosenNum = bestOpt[0];
        const chosenName = bestOpt[1] || `Вариант №${chosenNum}`;
        const reason = maxVotes > 0 ? `большинство (${maxVotes} голосов)` : 'первый верный путь';

        setTimeout(() => {
          sendQuestVote(V.id, chosenNum).then(res => {
            if (res && res.ok !== false) {
              addQuestLog('map', `🗺️ Развилка: авто-выбор «${chosenName}» (${reason}, вариант ${chosenNum})`);
            }
            refreshQuestUI();
          });
        }, 1200);
      }
    }
  }

  function checkAutoDungeonVote(dgn, q) {
    checkAutoVotes(q || { dgn });
  }

  let manualTargetLead = false; // Приоритет цели при ручных действиях (Вожак vs Рядовые)
  let lastQuestData = null;
  let lastParriedKey = null;
  let lastCombatTurnKey = null;
  let combatPending = false;
  let combatPendingAt = 0;
  let lastSolvedRoomSeed = null;
  let questLogHistory = [];
  const MAX_QUEST_LOG = 35;

  const RUNIC_NAMES = {
    red: '🔴 Красная',
    blue: '🔵 Синяя',
    green: '🟢 Зелёная',
    gold: '🟡 Золотая',
    violet: '🟣 Фиолетовая'
  };

  const GLYPH_NAMES = {
    sun: '☀️ Солнце',
    moon: '🌙 Луна',
    star: '⭐ Звезда',
    eye: '👁️ Глаз',
    wave: '🌊 Волна',
    key: '🗝️ Ключ'
  };

  const KIND_TITLES = {
    battle: '⚔️ Бой',
    boss: '👑 Битва с Боссом',
    bossgen: '👑 Главный враг округа',
    dungeon: '🏰 Данж',
    trap: '⚠️ Ловушка',
    treasure: '💎 Сокровищница',
    lair: '🕳️ Логово',
    rest: '🏕️ Привал',
    shop: '🛒 Лавка похода',
    event: '📜 Событие',
    gather: '🤝 Сбор отряда',
    map: '🗺️ Развилка карты',
    travel: '🚶 В пути'
  };

  const ROLE_HEALERS = { priest: 1, cleric: 1, healer: 1, druid: 1 };

  function addQuestLog(iconType, text) {
    const time = new Date().toLocaleTimeString('ru-RU', { hour12: false });
    const icons = {
      shield: '🛡️',
      sword: '⚔️',
      puzzle: '🧩',
      speed: '⚡',
      map: '🗺️',
      info: 'ℹ️',
      heal: '✚',
      warn: '⚠️'
    };
    const icon = icons[iconType] || '•';
    questLogHistory.unshift({ time, icon, text });
    if (questLogHistory.length > MAX_QUEST_LOG) questLogHistory.pop();
    renderQuestLog();
  }

  function renderQuestLog() {
    if (!qLogList) return;
    if (questLogHistory.length === 0) {
      qLogList.innerHTML = '<div style="color:#64748b;font-style:italic;">Журнал пуст</div>';
      return;
    }
    qLogList.innerHTML = questLogHistory.map(item => `
      <div style="display:flex;align-items:flex-start;gap:4px;padding:2px 0;border-bottom:1px solid rgba(255,255,255,0.03);">
        <span style="color:#64748b;font-size:8.5px;font-family:monospace;white-space:nowrap;margin-top:1px;">${item.time}</span>
        <span>${item.icon}</span>
        <span style="flex:1;word-break:break-word;">${item.text}</span>
      </div>
    `).join('');
  }

  function getQuestView() {
    if (G.LGQuest && typeof G.LGQuest._view === 'function') {
      try {
        const v = G.LGQuest._view();
        if (v) return v;
      } catch (e) {}
    }
    return lastQuestData;
  }

  const CLASS_TITLES = {
    legionary: '⚔️ Легионер',
    archer: '🏹 Лучник',
    mage: '✨ Маг',
    priest: '✚ Жрец',
    rogue: '🗡️ Разбойник',
    necromancer: '💀 Некромант',
    paladin: '☀️ Паладин',
    recruit: '👊 Новобранец'
  };

  function getClassTitle(role) {
    const r = String(role || '').toLowerCase();
    if (r === 'rogue' || r.includes('разбойник') || r.includes('вор')) return '🗡️ Разбойник';
    if (r === 'archer' || r.includes('лучник')) return '🏹 Лучник';
    if (r === 'mage' || r.includes('маг')) return '✨ Маг';
    if (r === 'necromancer' || r.includes('некромант')) return '💀 Некромант';
    if (r === 'paladin' || r.includes('паладин')) return '☀️ Паладин';
    if (r === 'priest' || r.includes('жрец') || r === 'cleric' || r.includes('клирик') || r === 'healer' || r.includes('лекарь')) return '✚ Жрец';
    if (r === 'legionary' || r.includes('легионер')) return '⚔️ Легионер';
    return CLASS_TITLES[r] || '⚔️ Легионер';
  }

  function getHeroRole() {
    // 1. Из нативного контекста игры (app.js / quest.js / city.js)
    const ctxHero = G.__lgQuestContext?.hero?.() || G.__lgCityContext?.hero?.() || G.__lgAucContext?.hero?.() || G.__lgHero || G.ME?.hero;
    if (ctxHero && ctxHero.role) {
      try { localStorage.setItem('lg_hero_role', ctxHero.role); } catch (e) {}
      return ctxHero.role;
    }

    // 2. Из DOM страницы игры (вкладка Персонаж #tab-hero)
    try {
      const roleEl = document.querySelector('#tab-hero .role .rl');
      if (roleEl && roleEl.textContent) {
        const text = roleEl.textContent.toLowerCase();
        let domRole = null;
        if (text.includes('лучник')) domRole = 'archer';
        else if (text.includes('маг')) domRole = 'mage';
        else if (text.includes('жрец') || text.includes('клирик') || text.includes('лекар')) domRole = 'priest';
        else if (text.includes('легионер')) domRole = 'legionary';
        else if (text.includes('разбойник') || text.includes('вор')) domRole = 'rogue';
        else if (text.includes('некромант')) domRole = 'necromancer';
        else if (text.includes('паладин')) domRole = 'paladin';
        if (domRole) {
          try { localStorage.setItem('lg_hero_role', domRole); } catch (e) {}
          return domRole;
        }
      }
    } catch (e) {}

    // 3. Из снимка боя если есть
    const v = getQuestView();
    if (v && v.b && v.b.form && typeof v.b.me === 'number' && v.b.form[v.b.me]?.[1]) {
      const combatRole = v.b.form[v.b.me][1];
      try { localStorage.setItem('lg_hero_role', combatRole); } catch (e) {}
      return combatRole;
    }

    // 4. Из сохранённого в localStorage
    const savedRole = localStorage.getItem('lg_hero_role');
    return savedRole || 'legionary';
  }

  function getClassBaseAttack(role) {
    const r = String(role || '').toLowerCase();
    if (r === 'rogue' || r.includes('разбойник') || r.includes('вор')) {
      return { icon: '🗡️', name: 'Кинжал', cmd: 'удар' };
    }
    if (r === 'archer' || r.includes('лучник')) {
      return { icon: '🏹', name: 'Выстрел', cmd: 'выстрел' };
    }
    if (r === 'mage' || r.includes('маг')) {
      return { icon: '✨', name: 'Магия', cmd: 'магия' };
    }
    if (r === 'necromancer' || r.includes('некромант')) {
      return { icon: '💀', name: 'Стрела тьмы', cmd: 'магия' };
    }
    if (r === 'paladin' || r.includes('паладин')) {
      return { icon: '☀️', name: 'Святой удар', cmd: 'удар' };
    }
    if (r === 'priest' || r.includes('жрец') || r === 'cleric' || r.includes('клирик') || r === 'healer' || r.includes('лекарь')) {
      return { icon: '✚', name: 'Лечение', cmd: 'лечение' };
    }
    if (r === 'legionary' || r.includes('легионер')) {
      return { icon: '⚔️', name: 'Удар', cmd: 'удар' };
    }
    return { icon: '👊', name: 'Удар', cmd: 'удар' };
  }

  const SKILL_METADATA = {
    grad:       { icon: '🏹', name: 'Град', kind: 'atk' },
    pricel:     { icon: '🎯', name: 'Прицел', kind: 'atk' },
    vspyshka:   { icon: '✨', name: 'Вспышка', kind: 'atk' },
    oznob:      { icon: '❄️', name: 'Озноб', kind: 'atk' },
    natisk:     { icon: '⚔️', name: 'Натиск', kind: 'atk' },
    stoyka:     { icon: '🛡️', name: 'Стойка', kind: 'def' },
    svet:       { icon: '☀️', name: 'Свет', kind: 'atk' },
    aura:       { icon: '💛', name: 'Аура', kind: 'buff' },
    dlan:       { icon: '🤲', name: 'Длань', kind: 'heal' },
    dlan_light: { icon: '☀️', name: 'Свет длани', kind: 'atk' },
    siyanie:    { icon: '🌟', name: 'Сияние', kind: 'heal' },
    obereg:     { icon: '🔰', name: 'Оберег', kind: 'heal' },
    yad:        { icon: '🧪', name: 'Яд', kind: 'atk' },
    vspinu:     { icon: '🗡️', name: 'В спину', kind: 'atk' },
    proklyat:   { icon: '💀', name: 'Проклясть', kind: 'atk' },
    vytyanut:   { icon: '🩸', name: 'Вытянуть', kind: 'atk' },
    podnyat:    { icon: '🦴', name: 'Поднять', kind: 'atk' },
    obchistit:  { icon: '💰', name: 'Обчистить', kind: 'atk' },
    skel:       { icon: '☠️', name: 'Скелеты', kind: 'atk' },
  };

  // Единственные гарантированные базовые врождённые навыки 1-го уровня (без прокачки веток)
  const CLASS_INNATE_SKILLS = {
    legionary:   ['natisk'],
    archer:      ['pricel'],
    mage:        ['vspyshka'],
    priest:      ['dlan'],
    rogue:       ['yad'],
    necromancer: ['proklyat'],
    paladin:     ['natisk'],
    recruit:     ['natisk']
  };

  function getSkillInfo(w, fallbackKind) {
    const cleanKey = String(w || '').toLowerCase().trim();
    let meta = SKILL_METADATA[cleanKey];
    if (!meta) {
      for (const [k, m] of Object.entries(SKILL_METADATA)) {
        if (m.name.toLowerCase() === cleanKey) {
          meta = m;
          break;
        }
      }
    }
    let icon = meta?.icon || (fallbackKind === 'heal' ? '✚' : '⚡');
    let name = meta?.name || w;
    let kind = fallbackKind || meta?.kind || 'atk';

    if (window.QBattleCrowd?.SK) {
      const S = window.QBattleCrowd.SK;
      for (const k of Object.keys(S)) {
        if (k.toLowerCase() === cleanKey || S[k].name?.toLowerCase() === cleanKey) {
          icon = S[k].icon || icon;
          name = S[k].name || name;
          break;
        }
      }
    }
    return { icon, name, key: cleanKey, kind };
  }

  function getHeroKnownSkills(role) {
    const innateKeys = CLASS_INNATE_SKILLS[role] || CLASS_INNATE_SKILLS.recruit;

    // 1. Проверяем кеш подтвержденных сервером навыков из реального боя (v.b.sk)
    try {
      const cached = localStorage.getItem('lg_unlocked_sk_' + role);
      if (cached) {
        const parsed = JSON.parse(cached);
        if (Array.isArray(parsed) && parsed.length > 0) {
          return parsed.map(k => getSkillInfo(k));
        }
      }
    } catch (e) {}

    // 2. Проверяем прокачанные узлы дерева навыков из профиля героя (/api/me -> hero.sk.t)
    try {
      const hero = G.ME?.hero || G.__lgHero;
      const sk = hero?.sk;
      if (sk && sk.t && sk.t[role]) {
        const list = [...innateKeys];
        const knownSet = new Set(list);
        const t = sk.t[role];
        if (role === 'legionary' && (t.stoyka > 0 || t.a_stoyka > 0) && !knownSet.has('stoyka')) list.push('stoyka');
        if (role === 'archer' && (t.grad > 0 || t.a_grad > 0) && !knownSet.has('grad')) list.push('grad');
        if (role === 'mage' && (t.oznob > 0 || t.a_oznob > 0) && !knownSet.has('oznob')) list.push('oznob');
        if (role === 'priest') {
          if ((t.siyanie > 0 || t.a_siyanie > 0) && !knownSet.has('siyanie')) list.push('siyanie');
          if ((t.obereg > 0 || t.a_obereg > 0) && !knownSet.has('obereg')) list.push('obereg');
          if ((t.svet > 0 || t.a_svet > 0) && !knownSet.has('svet')) list.push('svet');
        }
        if (role === 'rogue') {
          if ((t.vspinu > 0 || t.a_vspinu > 0) && !knownSet.has('vspinu')) list.push('vspinu');
          if ((t.obchistit > 0 || t.a_obchistit > 0) && !knownSet.has('obchistit')) list.push('obchistit');
        }
        if (role === 'necromancer') {
          if ((t.vytyanut > 0 || t.a_vytyanut > 0) && !knownSet.has('vytyanut')) list.push('vytyanut');
          if ((t.podnyat > 0 || t.a_podnyat > 0) && !knownSet.has('podnyat')) list.push('podnyat');
          if ((t.skel > 0 || t.a_skel > 0) && !knownSet.has('skel')) list.push('skel');
        }
        if (role === 'paladin') {
          if ((t.svet > 0 || t.a_svet > 0) && !knownSet.has('svet')) list.push('svet');
          if ((t.dlan_light > 0 || t.a_dlan_light > 0) && !knownSet.has('dlan_light')) list.push('dlan_light');
          if ((t.aura > 0 || t.a_aura > 0) && !knownSet.has('aura')) list.push('aura');
        }
        return list.map(k => getSkillInfo(k));
      }
    } catch (e) {}

    // 3. Единственный гарантированный базовый навык
    return innateKeys.map(k => getSkillInfo(k));
  }

  function pluralRounds(n) {
    const mod10 = n % 10, mod100 = n % 100;
    if (mod100 >= 11 && mod100 <= 19) return `${n} ходов`;
    if (mod10 === 1) return `${n} ход`;
    if (mod10 >= 2 && mod10 <= 4) return `${n} хода`;
    return `${n} ходов`;
  }

  function pluralDigits(n) {
    const mod10 = n % 10, mod100 = n % 100;
    if (mod100 >= 11 && mod100 <= 19) return `${n} цифр`;
    if (mod10 === 1) return `${n} цифра`;
    if (mod10 >= 2 && mod10 <= 4) return `${n} цифры`;
    return `${n} цифр`;
  }

  async function sendQuestSay(line) {
    const clean = String(line || '').trim();
    if (!clean) return { ok: false };
    if (G.__lgQuestContext && typeof G.__lgQuestContext.say === 'function') {
      try {
        const res = await G.__lgQuestContext.say(clean);
        return res || { ok: true };
      } catch (e) {
      }
    }
    return await gameApi('/api/qsay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ line: clean })
    });
  }

  async function sendQuestAct(p) {
    if (G.__lgQuestContext && typeof G.__lgQuestContext.act === 'function') {
      try {
        const res = await G.__lgQuestContext.act(p);
        return res || { ok: true };
      } catch (e) {
      }
    }
    return await gameApi('/api/qact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(p)
    });
  }

  async function sendQuestVote(vid, n) {
    if (G.__lgQuestContext && typeof G.__lgQuestContext.vote === 'function') {
      try {
        const res = await G.__lgQuestContext.vote(vid, n);
        return res || { ok: true };
      } catch (e) {
      }
    }
    return await gameApi('/api/qvote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vid, n })
    });
  }

  function ensureQaMode() {
    if (!questSettings.qaMode) return;
    const heroes = [
      G.ME?.hero,
      G.__lgHero,
      G.__lgQuestContext?.hero?.(),
      G.__lgCityContext?.hero?.(),
      window.X?.hero?.()
    ];
    heroes.forEach(h => {
      if (h) {
        if (!h.qa) h.qa = true;
        if (!h.qt) h.qt = true;
      }
    });
  }

  // 🛡️ Защита от финта босса, тишины и ядовитого газа («Ничего не пиши!»)
  function isQuestHush(v) {
    if (!v) return null;
    if (v.tr && v.tr.gas && (v.tr.st === 'act' || v.tr.st === 'wait')) return 'Ядовитый газ (нельзя писать!)';
    const M = (v.bg && v.bg.mech) || (v.b && v.b.gb && v.b.gb.mech) || (v.b && v.b.mech);
    if (M && (M.fake || (M.fam === 'silence' && M.id !== 'M94' && M.id !== 'M55') || /ничего не пиш|молчите|не пишите/i.test(M.tip || '') || /ничего не пиш|молчите|не пишите/i.test(M.text || ''))) {
      return M.fake ? 'Финт босса (обманный удар — молчите!)' : 'Тишина босса (нельзя писать!)';
    }
    if (v.b && v.b.tele && (v.b.tele.fake || v.b.tele.hush)) return 'Финт атаки (обманный удар — молчите!)';
    return null;
  }

  // 🎃 Хэллоуинская механика Джека-Фонарщика: Горячая тыква (J.potMe)
  // Автоматически выбирает случайного союзника в бою и передает ему тыкву: «передать @ник»
  let lastPumpkinPassAt = 0;
  function checkHotPumpkin(v) {
    if (!v) return;
    const J = (v.bg && v.bg.hw) || (v.b && v.b.gb && v.b.gb.hw) || (v.b && v.b.hw);
    if (!J || !J.potMe) return;

    // Защита от спама (пауза 3.5 секунды между передачами)
    if (Date.now() - lastPumpkinPassAt < 3500) return;

    const myNick = (G.ME?.user?.login || G.__lgMineMe?.name || getMyNick() || '').toLowerCase();
    const candidateSet = new Set();

    // 1. Из строя боя (form)
    if (v.b && Array.isArray(v.b.form)) {
      v.b.form.forEach(p => {
        const login = Array.isArray(p) ? p[0] : (p?.login || p);
        if (login && typeof login === 'string') candidateSet.add(login);
      });
    }

    // 2. Из списка союзников с HP (low)
    if (v.b && Array.isArray(v.b.low)) {
      v.b.low.forEach(x => {
        const login = Array.isArray(x) ? x[0] : (x?.login || x);
        if (login && typeof login === 'string') candidateSet.add(login);
      });
    }

    // 3. Резервный пул из активных игроков сайта
    if (candidateSet.size <= 1 && Array.isArray(window.__lgActivePlayers)) {
      window.__lgActivePlayers.forEach(p => {
        if (p && p.login) candidateSet.add(p.login);
      });
    }

    // Фильтруем себя и пустые ники
    const candidates = Array.from(candidateSet).filter(nick => {
      const clean = String(nick || '').trim().replace(/^@/, '');
      return clean && clean.toLowerCase() !== myNick;
    });

    if (candidates.length === 0) {
      addQuestLog('warn', '🎃 Горячая тыква у тебя! Ожидание союзников в строю для передачи...');
      return;
    }

    // Выбираем случайного игрока
    const randomTarget = candidates[Math.floor(Math.random() * candidates.length)].replace(/^@/, '');
    lastPumpkinPassAt = Date.now();

    const cmd = `передать @${randomTarget}`;
    sendQuestSay(cmd).then(res => {
      if (res && res.ok !== false) {
        addQuestLog('warn', `🎃 Горячая тыква передана игроку @${randomTarget} (случайный выбор)!`);
        showToast(`🎃 Тыква передана игроку @${randomTarget}!`);
      } else {
        addQuestLog('warn', `⚠️ Передача тыквы @${randomTarget}: ${res?.error || 'ошибка отправки'}`);
      }
      refreshQuestUI();
    }).catch(err => {
      addQuestLog('warn', `⚠️ Ошибка передачи тыквы: ${err?.message || err}`);
    });
  }

  function checkAutoParry(b, v) {
    if (!questSettings.autoParry || !b) return;

    // Проверка на финт / тишину / ядовитый газ — если активен финт, писать нельзя!
    const hush = isQuestHush(v || getQuestView());
    if (hush) {
      const hushKey = `hush:${b.id || 'b'}:${b.round || 0}`;
      if (lastParriedKey !== hushKey) {
        lastParriedKey = hushKey;
        addQuestLog('warn', `⛔ Обнаружен ${hush} — слово парирования заблокировано во избежание урона.`);
      }
      return;
    }

    if (b.st !== 'tele' || !b.tele) return;
    const tele = b.tele;
    const word = String(tele.word || '').trim();
    if (!word || tele.ok) return;

    const parryKey = `${b.id || 'b'}:${b.round || 0}:${word}`;
    if (lastParriedKey === parryKey) return;
    lastParriedKey = parryKey;

    sendQuestSay(word).then(res => {
      if (res && res.ok !== false) {
        addQuestLog('shield', `🛡️ Парирован удар босса: "${word}" (100% блок)`);
      } else {
        addQuestLog('warn', `⚠️ Парирование "${word}": ${res?.error || 'ошибка отправки'}`);
      }
      refreshQuestUI();
    }).catch(err => {
      addQuestLog('warn', `⚠️ Ошибка парирования: ${err?.message || err}`);
    });
  }

  function checkAutoParryLair(lair, v) {
    if (!questSettings.autoParry || !lair || !lair.tele) return;

    const hush = isQuestHush(v || getQuestView());
    if (hush) return;

    const tele = lair.tele;
    const word = String(tele.word || '').trim();
    if (!word || tele.ok) return;

    const parryKey = `lair:${word}`;
    if (lastParriedKey === parryKey) return;
    lastParriedKey = parryKey;

    sendQuestSay(word).then(res => {
      if (res && res.ok !== false) {
        addQuestLog('shield', `🛡️ Парирован удар логова: "${word}"`);
      }
      refreshQuestUI();
    });
  }

  // 1. Авто-Переправа: выбор безопасной кочки (исключая утонувшие)
  let lastCrossingStepSent = null;
  let lastCrossingActionTime = 0;

  function checkAutoCrossing(tr, q) {
    if (!questSettings.autoCrossing) return;
    if (!tr || !tr.cx || tr.res) return;
    const C = tr.cx;

    const party = q?.party || (lastQuestData && lastQuestData.party);
    if (party !== 'in') return;
    if (C.me && C.me.fell) return;

    if (C.ph !== 'pick') return;

    const step = C.step;
    const bad = Array.isArray(C.bad) ? C.bad : [];
    const stepKey = `${step}:${bad.length}`;
    if (lastCrossingStepSent === stepKey && Date.now() - lastCrossingActionTime < 4500) return;

    const k = (C.rows && C.rows[Math.min(step, (C.n || 4) - 1)]) || 2;

    const options = [];
    for (let i = 1; i <= k; i++) options.push(i);

    const safeOptions = options.filter(num => !bad.includes(num));
    if (safeOptions.length === 0) return;

    const chosen = safeOptions[0];
    const isGuaranteed = safeOptions.length === 1;

    // Тактика: если игрок уже вызвался и обеспечил себе сундук (C.me.vol),
    // не рискуем прыгать на неизвестную кочку — уступаем отряду.
    // Прыгаем только если кочка 100% надёжная или если ещё не вызвались!
    if (C.me && C.me.vol && !isGuaranteed) {
      return;
    }

    lastCrossingStepSent = stepKey;
    lastCrossingActionTime = Date.now();

    const choiceStr = String(chosen);
    addQuestLog('puzzle', `🪵 Переправа: шаг ${step + 1}/${C.n} → кочка ${choiceStr}${isGuaranteed ? ' (100% верная!)' : ' (безопасная)'}`);
    sendQuestSay(choiceStr);
  }

  // 2. Авто-Трясина: «тянем» по кулдауну и «руби» при атаке твари
  let lastBogPullTime = 0;
  let lastBogCutTime = 0;

  function checkAutoBog(tr, q) {
    if (!questSettings.autoBog) return;
    if (!tr || !tr.bog || tr.res) return;
    const G = tr.bog;

    const party = q?.party || (lastQuestData && lastQuestData.party);
    if (party !== 'in') return;

    const now = Date.now();

    // Защита от твари трясины (рубим щупальца)
    if (G.beast && !G.beast.res) {
      const X = G.beast;
      if (!X.me && now - lastBogCutTime > 2500) {
        lastBogCutTime = now;
        const cutWord = X.w || 'руби';
        addQuestLog('sword', `🫧 Трясина: тварь «${X.name || 'Тварь'}» атакует! Отправляем «${cutWord}»`);
        sendQuestSay(cutWord);
        return;
      }
    }

    // Вытягивание отряда: «тянем» строго по таймеру кд
    const canPullTime = G.next || 0;
    if (now >= canPullTime && now - lastBogPullTime > 2200) {
      lastBogPullTime = now;
      addQuestLog('sword', `🫧 Трясина: вязнем (${G.sink || 0}%) → «тянем»!`);
      sendQuestSay('тянем');
    }
  }

  // 2.1. Реакция на проверочные слова ловушек (T.type === 'react' && T.word)
  let lastReactTrapTime = 0;
  function checkAutoReactTrap(tr, q) {
    if (!questSettings.autoParry || !tr || tr.type !== 'react' || !tr.word || tr.acted || tr.res) return;
    const party = q?.party || (lastQuestData && lastQuestData.party);
    if (party !== 'in') return;
    if (isQuestHush(q || getQuestView())) return;
    const now = Date.now();
    if (now - lastReactTrapTime < 3000) return;
    lastReactTrapTime = now;
    const word = String(tr.word).toLowerCase();
    addQuestLog('shield', `⚠️ Ловушка-реакция: отправка проверочного слова «${word}»`);
    sendQuestSay(word);
  }

  // 2.2. Обвал потолка (T.type === 'ceiling')
  let lastCeilingTrapTime = 0;
  function checkAutoCeilingTrap(tr, q) {
    if (!questSettings.autoParry || !tr || tr.type !== 'ceiling' || tr.acted || tr.res) return;
    const party = q?.party || (lastQuestData && lastQuestData.party);
    if (party !== 'in') return;
    const now = Date.now();
    if (now - lastCeilingTrapTime < 3500) return;
    lastCeilingTrapTime = now;
    addQuestLog('shield', '🙌 Ловушка: обвал потолка → держим свод («держу»)!');
    sendQuestAct({ k: 'tr', a: 'hold' }).catch(() => sendQuestSay('держу'));
  }

  // 2.3. Делёж добычи (v.loot)
  let lastLootVoteTime = 0;
  function checkAutoLoot(loot, q) {
    if (!questSettings.autoJoin || !loot || loot.my) return;
    const party = q?.party || (lastQuestData && lastQuestData.party);
    if (party !== 'in') return;
    const now = Date.now();
    if (now - lastLootVoteTime < 4000) return;
    lastLootVoteTime = now;
    addQuestLog('speed', `🎁 Делёж добычи «${loot.name || 'вещь'}»: голос «случайно»`);
    sendQuestSay('случайно');
  }

  // 3. Авто-Ивенты: Котёл ведьмы и Сладость/Гадость
  let lastHwEventId = null;
  let lastHwActionTime = 0;

  function checkAutoHwEvents(ev, q) {
    if (!questSettings.autoHwEvents) return;
    if (!ev) return;

    const party = q?.party || (lastQuestData && lastQuestData.party);
    const canParticipate = party === 'in' || party === 'ghost';
    if (!canParticipate) return;

    const now = Date.now();
    const eventKey = `${ev.id}:${ev.st}:${ev.look}`;
    if (lastHwEventId === eventKey && now - lastHwActionTime < 5000) return;

    // Сладость или Гадость
    if (ev.look === 'trick' && ev.st === 'vote') {
      if (!ev.my) {
        lastHwEventId = eventKey;
        lastHwActionTime = now;
        addQuestLog('speed', '🍬 Изнанка: Сладость или Гадость → Голосуем «сладость» (+конфеты)!');
        sendQuestSay('сладость');
        return;
      }
    }

    // Котёл ведьмы
    if (ev.look === 'cauldron') {
      // Сбор монет
      if (ev.st === 'pay') {
        const mp = +ev.mp || 0;
        if (mp === 0 && now - lastHwActionTime > 4000) {
          lastHwEventId = eventKey;
          lastHwActionTime = now;
          addQuestLog('speed', '🪙 Котёл ведьмы: сбор монет → Скидываем 3 серебра («скинуться 3»)');
          sendQuestSay('скинуться 3');
          return;
        }
      }

      // Сбор ингредиентов (HW_ING из официального quest.js)
      if (ev.st === 'ingr') {
        const HW_ING = { mushroom: 'мухомор', eye: 'глаз', wing: 'крыло', pumpkin: 'тыква', tooth: 'зуб', web: 'паутина', bone: 'кость', frog: 'лягушка', candle: 'свеча', herb: 'полынь', tail: 'хвост' };
        const myParts = ev.my ? String(ev.my).split(',') : [];
        const hasMyIngr = Object.keys(HW_ING).some(k => myParts.includes(k));
        if (!hasMyIngr && now - lastHwActionTime > 4000) {
          lastHwEventId = eventKey;
          lastHwActionTime = now;
          let chosenKey = 'mushroom';
          const ingObj = ev.ing || {};
          for (const k of Object.keys(HW_ING)) {
            if (ingObj[k]) {
              chosenKey = k;
              break;
            }
          }
          const chosenIngName = HW_ING[chosenKey] || 'мухомор';
          addQuestLog('speed', `🧙‍♀️ Котёл ведьмы: добавляем ингредиент → «${chosenIngName}»`);
          sendQuestSay(chosenIngName);
          return;
        }
      }
    }
  }

  function checkAutoCombat(b, v) {
    if (!questSettings.autoCombat || !b) return;
    const vObj = v || getQuestView();
    if (isQuestHush(vObj)) return;
    if (b.st !== 'party') return;
    if (b.turn) return;

    const turnKey = `${b.id || 'b'}:${b.round || 0}`;
    if (lastCombatTurnKey === turnKey) return;
    if (combatPending && Date.now() - combatPendingAt < 6000) return;

    const myNick = (G.ME?.user?.login || G.__lgMineMe?.name || getMyNick() || '').toLowerCase();
    const heroIdx = typeof b.me === 'number' ? b.me : -1;
    const p = heroIdx >= 0 && b.form ? b.form[heroIdx] : null;
    const mf = !p && Array.isArray(b.mf) ? b.mf : null;
    const role = p ? p[1] : mf ? mf[0] : (G.ME?.hero?.role) || getHeroRole() || 'legionary';
    const side = p ? (p[2] || 'R') : mf ? (mf[1] || 'R') : 'R';
    const st = b.hp == null ? 'out' : b.hp === 0 ? 'dead' : b.hp < 0 ? 'fled' : (heroIdx >= 0 || mf ? 'on' : 'reserve');

    if (st === 'dead' || st === 'fled' || st === 'out') return;

    // Кешируем реальные скиллы персонажа, подтверждённые сервером в b.sk
    if (Array.isArray(b.sk) && b.sk.length > 0) {
      try {
        localStorage.setItem('lg_unlocked_sk_' + role, JSON.stringify(b.sk.map(x => x[0])));
      } catch (e) {}
    }

    const isHealer = !!ROLE_HEALERS[role] || (G.LGGameData?.classes?.()?.some(c => c.id === role && c.kind === 'heal'));
    const myHpPct = (b.mine?.me?.hpPct != null ? b.mine.me.hpPct : (typeof b.hp === 'number' ? (b.hp > 1 ? b.hp / 100 : b.hp) : 1));

    // 1. Приоритет лекаря: исцеление союзника или себя при низком HP (< 35%)
    const wounded = (b.low || [])
      .filter(x => x[0] !== myNick && typeof x[2] === 'number' && x[2] < 0.35)
      .sort((a, b) => a[2] - b[2]);
    const needSelfHeal = myHpPct < 0.35;

    if (isHealer && (wounded.length > 0 || needSelfHeal)) {
      const target = wounded.length > 0 ? wounded[0] : null;
      const targetNick = target ? target[0] : (needSelfHeal ? myNick : null);
      const targetName = target ? (target[1] || targetNick) : (needSelfHeal ? 'Себя' : 'Союзник');
      const targetHpPct = Math.round((target ? target[2] : myHpPct) * 100);

      const healSk = (b.sk || []).find(x => (x[2] === 'heal' || getSkillInfo(x[0]).kind === 'heal') && !x[1]);
      let actionObj = null;
      let actionLabel = '';

      if (healSk) {
        const skInfo = getSkillInfo(healSk[0], 'heal');
        actionObj = { k: 'b', a: 'sk', w: healSk[0], ...(targetNick ? { t: targetNick } : {}) };
        actionLabel = `${skInfo.icon} Скилл "${skInfo.name}" → ${targetName} (${targetHpPct}% HP)`;
      } else {
        actionObj = { k: 'b', a: 'base', ...(targetNick ? { t: targetNick } : {}) };
        actionLabel = `✚ Исцеление → ${targetName} (${targetHpPct}% HP)`;
      }

      executeCombatAction(actionObj, actionLabel, turnKey);
      return;
    }

    // 2. Фокус на вожаке
    const leadFoes = (b.foes || []).filter(f => f[2] && f[3] > 0);
    const hasLeader = leadFoes.length > 0 && !b.gb;
    const leadSide = hasLeader ? leadFoes[0][0] : '';
    const chosenSide = leadSide || side || '';

    // Разделяем скиллы по категориям строго по b.sk от сервера
    const readySkills = (b.sk || []).filter(x => !x[1]); // cd === 0

    // Защитные умения (Стойка легионера) — активируются при угрозе (< 75% HP или бой с боссом/вожаком)
    const defSkills = readySkills.filter(x => x[2] === 'def' || x[0] === 'stoyka' || getSkillInfo(x[0]).kind === 'def');
    if (defSkills.length > 0 && (myHpPct <= 0.75 || b.gb || hasLeader)) {
      const sk = defSkills[0];
      const skInfo = getSkillInfo(sk[0], 'def');
      const actionObj = {
        k: 'b',
        a: 'sk',
        w: sk[0],
        ...(chosenSide ? { s: chosenSide } : {})
      };
      const actionLabel = `${skInfo.icon} Защитный скилл "${skInfo.name}" (${Math.round(myHpPct * 100)}% HP)`;
      executeCombatAction(actionObj, actionLabel, turnKey);
      return;
    }

    // Баффы (например, Аура паладина)
    const buffSkills = readySkills.filter(x => x[2] === 'buff' || x[0] === 'aura' || getSkillInfo(x[0]).kind === 'buff');
    if (buffSkills.length > 0) {
      const sk = buffSkills[0];
      const skInfo = getSkillInfo(sk[0], 'buff');
      const actionObj = {
        k: 'b',
        a: 'sk',
        w: sk[0],
        ...(chosenSide ? { s: chosenSide } : {})
      };
      const actionLabel = `${skInfo.icon} Бафф "${skInfo.name}"`;
      executeCombatAction(actionObj, actionLabel, turnKey);
      return;
    }

    // 3. Атакующие скиллы без перезарядки (сервер принимает только w и s, t:'lead' не поддерживается скиллами)
    const atkSkills = readySkills.filter(x => {
      const kind = x[2] || getSkillInfo(x[0]).kind;
      return kind !== 'heal' && kind !== 'def' && kind !== 'buff' && x[0] !== 'stoyka' && x[0] !== 'aura';
    });

    if (atkSkills.length > 0) {
      const sk = atkSkills[0];
      const skInfo = getSkillInfo(sk[0], 'atk');
      const actionObj = {
        k: 'b',
        a: 'sk',
        w: sk[0],
        ...(chosenSide ? { s: chosenSide } : {})
      };
      const actionLabel = `${skInfo.icon} Скилл "${skInfo.name}"${hasLeader ? ' → Вожак' : ''}${chosenSide ? ` (${chosenSide})` : ''}`;
      executeCombatAction(actionObj, actionLabel, turnKey);
      return;
    }

    // 4. Базовая атака с адаптацией под класс героя (для жреца - удар 'melee', цель 'lead' только для атакующих)
    const classAtk = getClassBaseAttack(role);
    const baseAction = role === 'priest' ? 'melee' : 'base';
    const actionObj = {
      k: 'b',
      a: baseAction,
      ...(role !== 'priest' && hasLeader ? { t: 'lead' } : {}),
      ...(chosenSide ? { s: chosenSide } : {})
    };
    const actionLabel = `${classAtk.icon} ${classAtk.name}${hasLeader && role !== 'priest' ? ' → Вожак' : ''}${chosenSide ? ` (${chosenSide})` : ''}`;
    executeCombatAction(actionObj, actionLabel, turnKey);
  }

  function executeCombatAction(actionObj, label, turnKey) {
    lastCombatTurnKey = turnKey;
    combatPending = true;
    combatPendingAt = Date.now();

    sendQuestAct(actionObj).then(res => {
      if (res && res.ok !== false) {
        addQuestLog('sword', label);
      } else {
        addQuestLog('warn', `⚠️ Ход не принят: ${res?.error || 'ошибка'}`);
        lastCombatTurnKey = null;
      }
      refreshQuestUI();
    }).catch(err => {
      addQuestLog('warn', `⚠️ Ошибка хода: ${err?.message || err}`);
      lastCombatTurnKey = null;
    }).finally(() => {
      combatPending = false;
    });
  }

  function parseDungeonSecrets(room) {
    if (!room) return null;
    const sec = room.secret;
    const info = {
      roomType: room.type || 'обычная',
      hasSecret: !!sec,
      summary: '',
      searchAnswer: null,
      searchCount: null,
      order: null,
      symbols: null,
      type: sec ? (sec.type || 'тайник') : null
    };

    if (!sec) {
      info.summary = 'Тайников в текущей комнате нет';
      return info;
    }

    const parts = [];

    if (sec.search && typeof sec.search.answer === 'number') {
      info.searchAnswer = sec.search.answer;
      info.searchCount = sec.search.count || 5;
      parts.push(`🎯 Стена №${info.searchAnswer} (из ${info.searchCount})`);
    }

    if (Array.isArray(sec.order) && sec.order.length > 0) {
      info.order = sec.order;
      const orderStr = sec.order.map(c => RUNIC_NAMES[c] || c).join(' ➔ ');
      parts.push(`🔮 Руны: [${orderStr}]`);
    }

    if (Array.isArray(sec.symbols) && sec.symbols.length > 0) {
      info.symbols = sec.symbols;
      const symStr = sec.symbols.map(s => GLYPH_NAMES[s] || s).join(', ');
      parts.push(`📜 Символы: [${symStr}]`);
    }

    if (sec.type === 'torches') {
      parts.push(`🔥 Факелы: зажечь ${sec.count || 3} шт.`);
    }

    if (Array.isArray(sec.colors) && sec.colors.length > 0) {
      const colStr = sec.colors.map(c => RUNIC_NAMES[c] || c).join(' ➔ ');
      parts.push(`💎 Камни: [${colStr}]`);
    }

    info.summary = parts.join(' | ') || `Секрет: ${sec.type || 'найден'}`;
    return info;
  }

  /* ---------- 31.10: Декодер шифра сокровищницы (Mastermind) ---------- */
  function scoreGuess(code, guess, allowRepeat) {
    const n = code.length;
    const res = Array(n).fill('r');
    const codeUsed = Array(n).fill(false);
    const guessUsed = Array(n).fill(false);

    for (let i = 0; i < n; i++) {
      if (guess[i] === code[i]) {
        res[i] = 'g';
        codeUsed[i] = true;
        guessUsed[i] = true;
      }
    }

    for (let i = 0; i < n; i++) {
      if (guessUsed[i]) continue;
      for (let j = 0; j < n; j++) {
        if (!codeUsed[j] && guess[i] === code[j]) {
          res[i] = 'y';
          codeUsed[j] = true;
          break;
        }
      }
    }
    return res.join('');
  }

  function solveRuneCode(len, maxDigit, bestList, allowRepeat) {
    const n = len || 4;
    const max = maxDigit || 9;
    const digits = Array.from({ length: max }, (_, i) => String(i + 1));

    const greenMask = Array(n).fill(null);
    const redDigits = new Set();
    const yellowDigits = new Set();

    const validAttempts = (bestList || []).filter(item => !item[3]); // игнорируем устаревшие попытки до смены рун

    for (const [g, m] of validAttempts) {
      const matchStr = Array.isArray(m) ? m.join('') : String(m);
      for (let i = 0; i < n && i < g.length; i++) {
        const char = g[i];
        const match = matchStr[i] || 'r';
        if (match === 'g') {
          greenMask[i] = char;
        } else if (match === 'y') {
          yellowDigits.add(char);
        } else if (match === 'r') {
          let appearedElsewhereAsGy = false;
          for (let j = 0; j < n; j++) {
            if (j !== i && g[j] === char && (matchStr[j] === 'g' || matchStr[j] === 'y')) {
              appearedElsewhereAsGy = true;
            }
          }
          if (!appearedElsewhereAsGy && !yellowDigits.has(char)) {
            if (!allowRepeat) redDigits.add(char);
          }
        }
      }
    }

    const greenCount = greenMask.filter(x => x !== null).length;
    const unrevealedCount = n - greenCount;

    // Защита от OOM: при более чем 2 неразгаданных цифрах (особенно с повторами, 9^8 = 43M)
    // не запускаем полный перебор всех вариантов
    if (validAttempts.length === 0 || unrevealedCount > 2) {
      return {
        len: n,
        maxDigit: max,
        greenMask,
        greenCount,
        unrevealedCount,
        candidates: [],
        bestGuess: null,
        readyToSend: false
      };
    }

    const candidates = [];
    function generate(pos, currentArr, usedSet) {
      if (pos === n) {
        candidates.push(currentArr.join(''));
        return;
      }
      if (greenMask[pos] !== null) {
        currentArr.push(greenMask[pos]);
        generate(pos + 1, currentArr, usedSet);
        currentArr.pop();
        return;
      }
      for (const d of digits) {
        if (!allowRepeat && usedSet.has(d)) continue;
        if (redDigits.has(d)) continue;
        currentArr.push(d);
        if (!allowRepeat) usedSet.add(d);
        generate(pos + 1, currentArr, usedSet);
        if (!allowRepeat) usedSet.delete(d);
        currentArr.pop();
      }
    }

    generate(0, [], new Set(greenMask.filter(x => x !== null)));

    const filtered = candidates.filter(cand => {
      for (const [g, m] of validAttempts) {
        const expectedMatch = Array.isArray(m) ? m.join('') : String(m);
        const actualMatch = scoreGuess(cand, g, allowRepeat);
        if (actualMatch !== expectedMatch) {
          return false;
        }
      }
      return true;
    });

    return {
      len: n,
      maxDigit: max,
      greenMask,
      greenCount,
      unrevealedCount,
      candidates: filtered,
      bestGuess: filtered[0] || null,
      readyToSend: (unrevealedCount <= 2 || filtered.length <= 4) && filtered.length > 0
    };
  }

  let lastAutoCodeSentAt = 0;
  let lastAutoCodeSent = null;

  function checkAutoSolveCode(T, v) {
    if (!questSettings.autoSolveCode || !T || T.type !== 'code') return;
    const vObj = v || getQuestView();
    if (!vObj || vObj.party !== 'in') return;
    if (T.res || (T.st !== 'act' && T.st !== 'wait')) return;
    const R = T.rd2;
    if (R && R.ph === 'vote') return; // не вводим во время голосования

    const leftAttempts = R ? (+R.left || 0) : (T.acted ? 0 : 1);
    if (leftAttempts <= 0) return;

    const solver = solveRuneCode(T.len || 4, T.max || 9, T.best || [], !!R);
    if (!solver.readyToSend || !solver.bestGuess) return;

    // Условие: отправляем, когда осталось не более 2 неразгаданных цифр (или кандидатов <= 3)
    if (solver.unrevealedCount > 2 && solver.candidates.length > 3) return;

    const now = Date.now();
    const myNick = (G.ME?.user?.login || G.__lgMineMe?.name || getMyNick() || '').toLowerCase();
    const lastAttempt = (T.best && T.best.length > 0) ? T.best[0] : null;
    const lastAttemptByMe = lastAttempt && String(lastAttempt[2] || '').toLowerCase() === myNick;

    // Защита от спама и повторов (пауза 3.5 секунды)
    if (now - lastAutoCodeSentAt < 3500) return;
    if (lastAttemptByMe && lastAutoCodeSent === solver.bestGuess) return;
    if (lastAutoCodeSent === solver.bestGuess && now - lastAutoCodeSentAt < 8000) return;

    lastAutoCodeSentAt = now;
    lastAutoCodeSent = solver.bestGuess;

    sendQuestAct({ k: 'tr', a: 'code', s: solver.bestGuess }).then(res => {
      if (res && res.ok !== false) {
        addQuestLog('puzzle', `🔮 Декодер сокровищницы: отправлен код "${solver.bestGuess}" (неразгадано: ${solver.unrevealedCount} цифр, вариантов: ${solver.candidates.length})`);
        showToast(`🔮 Декодер сокровищницы: отправлен код ${solver.bestGuess}`);
      } else {
        addQuestLog('warn', `⚠️ Код "${solver.bestGuess}" не принят: ${res?.error || 'ошибка'}`);
      }
      refreshQuestUI();
    }).catch(err => {
      addQuestLog('warn', `⚠️ Ошибка отправки кода: ${err?.message || err}`);
    });
  }

  function onQuestDataReceived(q) {
    if (!q) return;
    lastQuestData = q;

    checkAutoJoinSquad(q);
    checkQuestMinerCoordination(q);
    if (q.b) checkAutoParry(q.b, q);
    if (q.lair) checkAutoParryLair(q.lair, q);
    if (q.b) checkAutoCombat(q.b, q);
    checkHotPumpkin(q);

    // Новые механики похода (28.10)
    const trData = q.tr || (G.LGQuest && G.LGQuest._view && G.LGQuest._view()?.tr);
    const evData = q.ev || (G.LGQuest && G.LGQuest._view && G.LGQuest._view()?.ev);
    if (trData && trData.cx) checkAutoCrossing(trData, q);
    if (trData && trData.bog) checkAutoBog(trData, q);
    if (trData && trData.type === 'code') checkAutoSolveCode(trData, q);
    if (trData && trData.type === 'react') checkAutoReactTrap(trData, q);
    if (trData && trData.type === 'ceiling') checkAutoCeilingTrap(trData, q);
    if (evData) checkAutoHwEvents(evData, q);
    if (q.loot) checkAutoLoot(q.loot, q);
    checkAutoVotes(q);

    if (q.dgn && q.dgn.room) {
      const seed = q.dgn.room.seed;
      if (seed && seed !== lastSolvedRoomSeed) {
        lastSolvedRoomSeed = seed;
        const secInfo = parseDungeonSecrets(q.dgn.room);
        if (secInfo && secInfo.hasSecret) {
          addQuestLog('puzzle', `🧩 ${secInfo.summary}`);
        }
      }
    }

    ensureQaMode();

    if (activeTab === 'quest') {
      refreshQuestUI();
    }
  }

  function refreshQuestUI() {
    const v = getQuestView();
    if (!qLoc) return;

    const party = v ? (v.party || 'out') : 'out';

    if (!v || !v.on) {
      qLoc.textContent = 'Поход сейчас не идёт';
      qLoc.style.color = '#94a3b8';
      qIcon.textContent = '💤';
      qPartySt.textContent = 'Вне похода';
      qPartySt.style.background = '#262238';
      qPartySt.style.color = '#94a3b8';
      qPartySt.style.borderColor = '#3d3759';
      qTimer.textContent = '-- с';
      qPhase.textContent = 'Ожидание сбора или запуска похода';
      qBossInfo.textContent = '';
      if (qAlert) qAlert.style.display = 'none';
      if (qDgnCard) qDgnCard.style.display = 'none';
      if (qTurnStatus) qTurnStatus.textContent = 'Вне боя';
    } else {
      const kind = v.k || v.kind || (v.b ? (v.b.gb ? 'bossgen' : 'battle') : v.dgn ? 'dungeon' : v.ev ? 'event' : v.tr ? 'trap' : v.sh ? 'shop' : v.pz ? 'puzzle' : v.loot ? 'loot' : 'travel');
      const kindTitle = KIND_TITLES[kind] || KIND_TITLES[v.k] || KIND_TITLES[v.kind] || '⚔️ Поход';
      qLoc.textContent = v.t || v.rn || v.name || kindTitle;
      qLoc.style.color = '#f43f5e';
      qIcon.textContent = kind === 'dungeon' ? '🏰' : kind.includes('boss') ? '👑' : kind === 'battle' ? '⚔️' : '🚶';

      if (party === 'in') {
        qPartySt.textContent = 'В отряде 🟢';
        qPartySt.style.background = 'rgba(16,185,129,0.15)';
        qPartySt.style.color = '#10b981';
        qPartySt.style.borderColor = 'rgba(16,185,129,0.3)';
      } else if (party === 'q') {
        qPartySt.textContent = 'В очереди 🟡';
        qPartySt.style.background = 'rgba(245,158,11,0.15)';
        qPartySt.style.color = '#f59e0b';
        qPartySt.style.borderColor = 'rgba(245,158,11,0.3)';
      } else if (party === 'ghost') {
        qPartySt.textContent = 'Призрак 👻';
        qPartySt.style.background = 'rgba(148,163,184,0.15)';
        qPartySt.style.color = '#cbd5e1';
        qPartySt.style.borderColor = 'rgba(148,163,184,0.3)';
      } else {
        qPartySt.textContent = 'Вне отряда ⚪';
        qPartySt.style.background = '#262238';
        qPartySt.style.color = '#94a3b8';
        qPartySt.style.borderColor = '#3d3759';
      }

      let until = v.until || (v.b && v.b.until) || (v.dgn && v.dgn.vote && v.dgn.vote.end) || (v.vote && v.vote.end) || (v.ev && v.ev.until) || (v.tr && v.tr.until) || (v.tr && v.tr.rd2 && v.tr.rd2.ang && v.tr.rd2.ang.at) || (v.loot && v.loot.until) || 0;
      if (until) {
        const leftSec = Math.max(0, Math.ceil((+until - Date.now()) / 1000));
        qTimer.textContent = `${leftSec} с`;
      } else {
        qTimer.textContent = '-- с';
      }

      if (v.b) {
        const b = v.b;
        if (b.st === 'tele' && b.tele) {
          qPhase.textContent = `Ход врага · ${b.tele.text || 'Атака!'}`;
          qPhase.style.color = '#f43f5e';
          if (qAlert) {
            qAlert.style.display = 'block';
            qAlertTitle.textContent = `⚡ БОСС АТАКУЕТ! Слово блока: "${b.tele.word}"`;
            qAlertMsg.textContent = b.tele.ok ? '✓ Слово принято игрой! Удар парирован.' : `Нужно слов: ${b.tele.got || 0}/${b.tele.need || 1}. ${questSettings.autoParry ? 'Отправляем авто-парирование...' : 'Авто-парирование выключено'}`;
          }
        } else if (b.st === 'party') {
          qPhase.textContent = `Твой ход · Раунд ${b.round || 1}${b.maxR ? '/' + b.maxR : ''}`;
          qPhase.style.color = '#10b981';
          if (qAlert) qAlert.style.display = 'none';
        } else {
          qPhase.textContent = `Фаза: ${b.st || 'бой'} · Раунд ${b.round || 1}`;
          qPhase.style.color = '#cbd5e1';
          if (qAlert) qAlert.style.display = 'none';
        }

        if (b.gb) {
          const hpPct = Math.round((b.gb.hp || 0) * 100);
          let extraHw = '';
          const gHw = (v.bg && v.bg.hw) || (b.gb && b.gb.hw);
          if (gHw && gHw.lan && Array.isArray(gHw.lan.lit)) {
            const litCount = gHw.lan.lit.filter(Boolean).length;
            extraHw = ` · 🎃 Фонари: ${litCount}/6${litCount <= 1 ? ' ⚠️ ТЬМА!' : ''}`;
          }
          qBossInfo.textContent = `👑 ${b.gb.name || 'Босс'}: ${hpPct}% HP${extraHw}`;
        } else if (b.foes && b.foes.length) {
          const aliveFoes = b.foes.filter(f => f[3] > 0).length;
          qBossInfo.textContent = `Врагов: ${aliveFoes}/${b.foes.length}`;
        } else {
          qBossInfo.textContent = '';
        }

        if (qTurnStatus) {
          if (b.turn) {
            qTurnStatus.textContent = `Ход принят ✓ (${b.turn})`;
            qTurnStatus.style.color = '#10b981';
          } else if (b.st === 'party') {
            qTurnStatus.textContent = 'Ждёт выбора хода...';
            qTurnStatus.style.color = '#fbbf24';
          } else {
            qTurnStatus.textContent = 'Ожидание хода отряда';
            qTurnStatus.style.color = '#94a3b8';
          }
        }
      } else {
        const hush = isQuestHush(v);
        const J = (v.bg && v.bg.hw) || (v.b && v.b.gb && v.b.gb.hw) || (v.b && v.b.hw);

        if (J && J.potMe) {
          if (qAlert) {
            qAlert.style.display = 'block';
            qAlertTitle.textContent = '🎃 ГОРЯЧАЯ ТЫКВА У ТЕБЯ!';
            qAlertMsg.textContent = 'Тыква вот-вот взорвётся! Бот выбирает случайного союзника и отправляет «передать @ник»...';
          }
        } else if (hush) {
          if (qAlert) {
            qAlert.style.display = 'block';
            qAlertTitle.textContent = `⛔ ВНИМАНИЕ: ${hush.toUpperCase()}`;
            qAlertMsg.textContent = 'Ничего не пишите в чат и не нажимайте удары, пока эффект не рассеется!';
          }
        } else if (v.tr && v.tr.type === 'code') {
          const T = v.tr;
          const R = T.rd2;
          const mx = +T.per || 3;
          if (qAlert) {
            qAlert.style.display = 'block';
            if (R && R.ph === 'vote') {
              const V = R.vote || { take: 0, next: 0 };
              qAlertTitle.textContent = `🚪 Дверь с рунами 2: Голосование!`;
              qAlertMsg.innerHTML = `
                <div style="margin-bottom:4px;font-size:10px;">Забрать: ${V.take} · Идти дальше (×2): ${V.next} · Попыток: ${R.left || 0}/${mx}</div>
                <div style="display:flex;gap:6px;">
                  <button id="b-q-vote-keep" style="flex:1;padding:4px;background:${R.my === 'take' ? '#059669' : '#2563eb'};color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:bold;font-size:10px;">💰 Забрать${R.my === 'take' ? ' ✓' : ''}</button>
                  <button id="b-q-vote-next" style="flex:1;padding:4px;background:${R.my === 'next' ? '#059669' : '#d97706'};color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:bold;font-size:10px;">🚪 Дальше ×2${R.my === 'next' ? ' ✓' : ''}</button>
                </div>
              `;
              const btnKeep = qAlertMsg.querySelector('#b-q-vote-keep');
              if (btnKeep) btnKeep.onclick = () => {
                sendQuestAct({ k: 'tr', a: 'keep' });
                addQuestLog('info', '💰 Отдан голос: «Забрать награду»');
              };
              const btnNext = qAlertMsg.querySelector('#b-q-vote-next');
              if (btnNext) btnNext.onclick = () => {
                sendQuestAct({ k: 'tr', a: 'next' });
                addQuestLog('warn', '🚪 Отдан голос: «Идти дальше (награда ×2)»');
              };
            } else {
              const angLeftSec = R && R.ang && R.ang.at > Date.now() ? Math.max(0, Math.ceil((R.ang.at - Date.now()) / 1000)) : 0;
              const angStr = angLeftSec > 0 ? ` | ⏱️ Смена рун через ${angLeftSec}с` : (R && R.ang ? ' | ⚠️ Злая дверь (меняет руны!)' : '');
              const doorTitle = R ? `🔮 Дверь с рунами ${R.door === 2 ? '(Вторая дверь)' : '(Первая дверь)'}` : `🗝️ Кодовый замок сокровищницы`;
              qAlertTitle.textContent = `${doorTitle}${angStr}`;

              const leftAttempts = R ? (R.left || 0) : (T.acted ? 0 : 1);
              const solver = solveRuneCode(T.len || 4, T.max || 9, T.best || [], !!R);

              // 1. Рисуем плашки разгаданных позиций: [ 1 ] [ 2 ] [ ? ] [ ? ]
              const slotsHtml = solver.greenMask.map(m => {
                if (m !== null) {
                  return `<span style="display:inline-flex;align-items:center;justify-content:center;width:24px;height:26px;background:#065f46;border:1px solid #10b981;border-radius:4px;font-size:13px;font-weight:bold;color:#34d399;">${m}</span>`;
                } else {
                  return `<span style="display:inline-flex;align-items:center;justify-content:center;width:24px;height:26px;background:#1e1b33;border:1px dashed #64748b;border-radius:4px;font-size:13px;font-weight:bold;color:#94a3b8;">?</span>`;
                }
              }).join('');

              // 2. Статус и кандидаты
              let statusText = '';
              if (solver.unrevealedCount === 0) {
                statusText = `<span style="color:#10b981;font-weight:bold;">🎉 Код полностью разгадан: ${solver.bestGuess}!</span>`;
              } else if (solver.unrevealedCount <= 2) {
                statusText = `<span style="color:#fbbf24;font-weight:bold;">⚡ Осталось неразгадано: ${solver.unrevealedCount} ${pluralDigits(solver.unrevealedCount)}!</span>`;
              } else {
                statusText = `<span style="color:#94a3b8;">⏳ Неразгадано: ${solver.unrevealedCount} ${pluralDigits(solver.unrevealedCount)} (ждём подсказок отряда)</span>`;
              }

              let candText = '';
              if (solver.candidates.length > 0 && solver.candidates.length <= 6) {
                candText = `<div style="font-size:9.5px;color:#cbd5e1;margin-top:2px;">Варианты (${solver.candidates.length}): <b style="color:#60a5fa;">${solver.candidates.join(' · ')}</b></div>`;
              } else if (solver.candidates.length > 6) {
                candText = `<div style="font-size:9.5px;color:#94a3b8;margin-top:2px;">Кандидатов отсеяно: <b>${solver.candidates.length}</b></div>`;
              }

              // 3. Кнопка ручного ввода найденного лучшего кода
              let btnActionHtml = '';
              if (solver.bestGuess && leftAttempts > 0) {
                btnActionHtml = `
                  <button id="b-q-solve-code-btn" style="margin-top:5px;width:100%;padding:4px 8px;background:#7c3aed;color:#ffffff;border:1px solid #a78bfa;border-radius:4px;cursor:pointer;font-size:10.5px;font-weight:bold;display:flex;align-items:center;justify-content:center;gap:5px;">
                    <span>⚡ Ввести код:</span>
                    <span style="letter-spacing:1px;font-family:monospace;font-size:12px;color:#fef08a;">${solver.bestGuess}</span>
                  </button>
                `;
              }

              // 4. История лучших попыток отряда с индикаторами
              let bestHtml = '';
              if (Array.isArray(T.best) && T.best.length > 0) {
                bestHtml = `<div style="margin-top:5px;padding:3px 6px;background:rgba(0,0,0,0.35);border-radius:4px;font-size:9.5px;">
                  <div style="color:#a78bfa;font-weight:bold;margin-bottom:2px;">Попытки отряда:</div>
                  ${T.best.slice(0, 3).map(([guess, match, author, old]) => {
                    const matchStr = Array.isArray(match) ? match.join('') : String(match);
                    const formattedIcons = guess.split('').map((char, i) => {
                      const icon = matchStr[i] === 'g' ? '🟢' : matchStr[i] === 'y' ? '🟡' : '🔴';
                      return `${char}${icon}`;
                    }).join(' ');
                    return `<div><b>${formattedIcons}</b> (${author})${old ? ' <span style="opacity:0.6;">(до смены)</span>' : ''}</div>`;
                  }).join('')}
                </div>`;
              }

              qAlertMsg.innerHTML = `
                <div style="font-size:10px;margin-bottom:4px;">
                  Попыток: <b>${leftAttempts}</b>${R ? ` из ${mx}` : ''} · Длина: <b>${T.len || 4}</b> цифр (1–${T.max || 9})${R ? ' · Руны могут повторяться' : ''}
                </div>
                <div style="display:flex;align-items:center;gap:6px;margin:4px 0;">
                  <div style="display:flex;gap:4px;">${slotsHtml}</div>
                  <div style="font-size:10px;">${statusText}</div>
                </div>
                ${candText}
                ${btnActionHtml}
                ${bestHtml}
              `;

              const btnSolve = qAlertMsg.querySelector('#b-q-solve-code-btn');
              if (btnSolve) {
                btnSolve.onclick = () => {
                  sendQuestAct({ k: 'tr', a: 'code', s: solver.bestGuess }).then(res => {
                    if (res && res.ok !== false) {
                      addQuestLog('puzzle', `🔮 Декодер: отправлен код "${solver.bestGuess}"`);
                      showToast(`🔮 Отправлен код ${solver.bestGuess}`);
                    } else {
                      addQuestLog('warn', `⚠️ Код "${solver.bestGuess}" не принят: ${res?.error || 'ошибка'}`);
                    }
                    refreshQuestUI();
                  });
                };
              }
            }
          }
        } else {
          if (qAlert) qAlert.style.display = 'none';
        }
        qPhase.textContent = `Этап: ${kindTitle}`;
        qPhase.style.color = '#cbd5e1';
        qBossInfo.textContent = '';
        if (qTurnStatus) qTurnStatus.textContent = 'Вне боя';
      }

      // Радар Данжа и Выбор пути (Данж / Развилки карты)
      const hasDgn = !!(v.dgn && questSettings.dungeonSolver);
      const hasMapVote = !!(v.vote && !v.vote.done);

      if (hasDgn || hasMapVote) {
        if (qDgnCard) qDgnCard.style.display = 'block';
        const dgn = v.dgn;
        const room = dgn ? dgn.room : null;
        if (qDgnRoom) {
          if (dgn) {
            qDgnRoom.textContent = `Этаж ${(room && room.floor) || 1}${dgn.fl ? ' из ' + dgn.fl : ''} (${room?.type || 'комната'})`;
          } else {
            qDgnRoom.textContent = '🗺️ Развилка на карте';
          }
        }

        const secInfo = room ? parseDungeonSecrets(room) : null;
        if (qDgnSecret) {
          if (dgn && secInfo && secInfo.hasSecret) {
            let html = `<div style="font-weight:bold;color:#fcd34d;margin-bottom:3px;">🔍 Найден секрет комнаты:</div>`;
            if (secInfo.searchAnswer) {
              html += `<div style="display:flex;align-items:center;justify-content:space-between;padding:2px 0;">
                <span>🎯 Тайник / Простукивание: <b style="color:#00ff66;font-size:12px;">Стена №${secInfo.searchAnswer}</b> из ${secInfo.searchCount}</span>
              </div>`;
            }
            if (secInfo.order) {
              html += `<div style="padding:2px 0;">🔮 Порядок рун/колоколов: <b style="color:#60a5fa;">${secInfo.order.map(c => RUNIC_NAMES[c] || c).join(' ➔ ')}</b></div>`;
            }
            if (secInfo.symbols) {
              html += `<div style="padding:2px 0;">📜 Глифы/Символы: <b style="color:#f472b6;">${secInfo.symbols.map(s => GLYPH_NAMES[s] || s).join(', ')}</b></div>`;
            }
            if (secInfo.type === 'torches') {
              html += `<div style="padding:2px 0;color:#fb923c;">🔥 Нужно зажечь факелов: <b>${room.secret?.count || 3} шт.</b></div>`;
            }
            qDgnSecret.innerHTML = html;
            qDgnSecret.style.display = 'block';
          } else if (dgn) {
            qDgnSecret.innerHTML = '<div style="color:#94a3b8;font-size:10px;">В этой комнате нет тайников (обычный переход)</div>';
            qDgnSecret.style.display = 'block';
          } else {
            qDgnSecret.style.display = 'none';
          }
        }

        if (qDgnVotes) {
          const voteObj = (dgn && dgn.vote && !dgn.vote.done) ? dgn.vote : (v && v.vote && !v.vote.done ? v.vote : null);
          const isDgnVote = !!(dgn && dgn.vote && !dgn.vote.done);

          if (voteObj && Array.isArray(voteObj.opts) && voteObj.opts.length > 0) {
            const opts = voteObj.opts;
            const myVote = voteObj.my;
            qDgnVotes.innerHTML = opts.map(o => {
              const num = o[0];
              const key = isDgnVote ? o[1] : '';
              const label = isDgnVote ? o[2] : o[1];
              const icon = isDgnVote ? (o[3] || '🚪') : '🗺️';
              const votesCount = isDgnVote ? (o[4] || 0) : (o[3] || 0);
              const isBack = isDgnVote ? !!o[5] : !!o[4];
              const isSecret = isDgnVote && (key === 'secret' || (label && label.toLowerCase().includes('тайник')));
              const isVoted = myVote === num;
              return `
                <button type="button" class="b-qvote-btn" data-vid="${voteObj.id}" data-opt="${num}" style="flex:1;min-width:120px;padding:4px 6px;background:${isVoted ? '#059669' : (isSecret ? '#854d0e' : '#262238')};color:#fff;border:1px solid ${isSecret ? '#eab308' : '#3d3759'};border-radius:4px;cursor:pointer;font-size:10px;display:flex;justify-content:space-between;align-items:center;">
                  <span>${icon} ${num}. ${label}${isSecret ? ' ⭐' : ''}${isBack ? ' <small style="color:#94a3b8;">(назад)</small>' : ''}</span>
                  <span style="background:rgba(0,0,0,0.3);padding:0 4px;border-radius:3px;font-weight:bold;">${votesCount}</span>
                </button>
              `;
            }).join('');

            qDgnVotes.querySelectorAll('.b-qvote-btn').forEach(btn => {
              btn.onclick = () => {
                const vid = btn.dataset.vid;
                const opt = parseInt(btn.dataset.opt, 10);
                sendQuestVote(vid, opt).then(() => {
                  addQuestLog('map', `🗳️ Голос за вариант ${opt}`);
                  refreshQuestUI();
                });
              };
            });
          } else {
            qDgnVotes.innerHTML = '';
          }
        }
      } else {
        if (qDgnCard) qDgnCard.style.display = 'none';
      }
    }

    const heroRole = getHeroRole();
    if (qClassBadge) {
      qClassBadge.textContent = getClassTitle(heroRole);
      qClassBadge.title = `Класс героя: ${getClassTitle(heroRole)} (определяется автоматически)`;
    }
    const classAtk = getClassBaseAttack(heroRole);
    const isPriest = (heroRole === 'priest' || heroRole.includes('жрец') || heroRole.includes('cleric') || heroRole.includes('клирик'));
    const hasLeader = v && v.b && (v.b.foes || []).some(f => f[2] && f[3] > 0);
    const inSquad = party === 'in' || party === 'q';

    // 1. Кнопка базовой атаки под класс героя
    if (btnActAtk) {
      btnActAtk.innerHTML = `<span>${classAtk.icon} ${classAtk.name}</span>`;
      btnActAtk.title = `Базовая атака (${classAtk.name}) класса ${heroRole}`;
    }

    // 2. Вторая кнопка: для жреца — ближний бой, для остальных — выбор цели (Вожак vs Рядовые)
    if (btnActSub) {
      if (isPriest) {
        btnActSub.innerHTML = '<span>⚔️ Ближний бой</span>';
        btnActSub.style.background = '#881337';
        btnActSub.style.color = '#fff';
        btnActSub.style.border = 'none';
        btnActSub.title = 'Физический удар жреца в ближнем бою';
      } else {
        btnActSub.innerHTML = manualTargetLead ? '<span>🎯 Цель: Вожак ⭐</span>' : '<span>👥 Цель: Рядовые</span>';
        btnActSub.style.background = manualTargetLead ? '#d97706' : '#262238';
        btnActSub.style.color = manualTargetLead ? '#fff' : '#fbbf24';
        btnActSub.style.border = manualTargetLead ? '1px solid #f59e0b' : '1px solid #3d3759';
        btnActSub.title = hasLeader ? 'Переключить цель атаки (Вожак / Рядовые)' : 'Вожаков на поле сейчас нет (удар по рядовым)';
      }
    }

    // 3. Кнопка отряда (вступить / выйти)
    if (btnActSquad) {
      const isHwPortal = !inSquad && v && v.gather && v.gather.hw;
      btnActSquad.innerHTML = inSquad ? '<span>🏃 Выйти</span>' : isHwPortal ? '<span>🎃 В портал</span>' : '<span>🤝 Вступить</span>';
      btnActSquad.style.background = inSquad ? '#4c0519' : isHwPortal ? '#7c2d12' : '#064e3b';
      btnActSquad.style.color = inSquad ? '#fecdd3' : isHwPortal ? '#fed7aa' : '#a7f3d0';
      btnActSquad.style.border = inSquad ? '1px solid #e11d48' : isHwPortal ? '1px solid #ea580c' : '1px solid #10b981';
      btnActSquad.title = inSquad ? 'Покинуть боевой отряд' : isHwPortal ? 'Шагнуть в портал Изнанки («портал»)' : 'Вступить в отряд похода';




    }

    // 4. Отрисовка боевых скиллов
    if (qSkillsTitle) {
      qSkillsTitle.textContent = `Боевые скиллы (${classAtk.icon} ${classAtk.name})`;
    }

    if (qSkillsList) {
      const myNick = (G.ME?.user?.login || G.__lgMineMe?.name || getMyNick() || '').toLowerCase();

      if (v && v.b && Array.isArray(v.b.sk) && v.b.sk.length > 0) {
        // Кешируем реальные скиллы от сервера
        try {
          localStorage.setItem('lg_unlocked_sk_' + heroRole, JSON.stringify(v.b.sk.map(x => x[0])));
        } catch (e) {}

        const readyCount = v.b.sk.filter(x => !x[1]).length;
        if (qSkillsCount) qSkillsCount.textContent = `${readyCount} доступно`;
        const turnDone = !!v.b.turn;
        const isMyTurn = v.b.st === 'party';

        qSkillsList.innerHTML = v.b.sk.map(([w, cd, kind]) => {
          const info = getSkillInfo(w, kind);
          const isReady = (cd === 0);
          const isHeal = (kind === 'heal' || info.kind === 'heal');
          const isDef = (kind === 'def' || info.kind === 'def' || w === 'stoyka');
          const isTurnSkill = turnDone && (String(v.b.turn).toLowerCase() === String(w).toLowerCase() || String(v.b.turn).toLowerCase() === info.name.toLowerCase());

          let bg = isTurnSkill ? '#059669' : isReady ? (isHeal ? '#065f46' : isDef ? '#1e3a8a' : '#9f1239') : '#1e1b33';
          let border = isTurnSkill ? '#10b981' : isReady ? (isHeal ? '#10b981' : isDef ? '#3b82f6' : '#f43f5e') : '#3d3759';
          let textColor = isReady ? '#ffffff' : '#94a3b8';
          let badgeText = isTurnSkill ? 'Ход сделан ✓' : isReady ? 'Готово' : `${cd} р.`;
          let badgeBg = isTurnSkill ? 'rgba(16,185,129,0.3)' : isReady ? 'rgba(0,0,0,0.3)' : 'rgba(255,255,255,0.08)';

          return `
            <button type="button" class="b-qsk-btn" data-qsk="${w}" data-qkind="${kind || info.kind || 'atk'}" data-qcd="${cd}" style="flex:1;min-width:105px;padding:5px 8px;background:${bg};color:${textColor};border:1px solid ${border};border-radius:5px;cursor:${isReady && isMyTurn && !turnDone ? 'pointer' : 'default'};font-size:10px;font-weight:bold;display:flex;justify-content:space-between;align-items:center;transition:all 0.15s ease;" title="${info.name} (${isReady ? 'Готово к использованию' : 'Перезарядка: ' + cd + ' ходов'})">
              <span style="display:flex;align-items:center;gap:4px;">
                <span>${info.icon}</span>
                <span>${info.name}</span>
              </span>
              <span style="background:${badgeBg};padding:1px 5px;border-radius:3px;font-size:9px;font-weight:normal;">${badgeText}</span>
            </button>
          `;
        }).join('');

        qSkillsList.querySelectorAll('.b-qsk-btn').forEach(btn => {
          btn.onclick = () => {
            const skKey = btn.dataset.qsk;
            const cd = parseInt(btn.dataset.qcd, 10);
            const kind = btn.dataset.qkind;
            const info = getSkillInfo(skKey, kind);

            if (cd > 0) {
              showToast(`Скилл «${info.name}» на перезарядке: ещё ${pluralRounds(cd)}`);
              return;
            }
            if (!v.b || v.b.st !== 'party') {
              showToast('Сейчас не фаза хода отряда!');
              return;
            }
            if (v.b.turn) {
              showToast('Ход в этом раунде уже сделан');
              return;
            }

            const isHeal = (kind === 'heal' || info.kind === 'heal');
            const isDef = (kind === 'def' || info.kind === 'def' || skKey === 'stoyka');
            const isBuff = (kind === 'buff' || info.kind === 'buff' || skKey === 'aura');
            const wounded = (v.b.low || []).filter(x => x[0] !== myNick && x[2] < 0.9).sort((a,b) => a[2] - b[2]);
            const targetNick = isHeal && wounded.length ? wounded[0][0] : null;
            const targetName = isHeal && wounded.length ? (wounded[0][1] || targetNick) : '';

            const heroIdx = typeof v.b.me === 'number' ? v.b.me : -1;
            const p = heroIdx >= 0 && v.b.form ? v.b.form[heroIdx] : null;
            const mf = !p && Array.isArray(v.b.mf) ? v.b.mf : null;
            const side = p ? (p[2] || 'R') : mf ? (mf[1] || 'R') : 'R';
            const leadFoes = (v.b.foes || []).filter(f => f[2] && f[3] > 0);
            const leadSide = leadFoes.length > 0 ? leadFoes[0][0] : '';
            const chosenSide = manualTargetLead && leadSide ? leadSide : side;

            const actionObj = {
              k: 'b',
              a: 'sk',
              w: skKey,
              ...(isHeal ? (targetNick ? { t: targetNick } : {}) : {}),
              ...(!isHeal && chosenSide ? { s: chosenSide } : {})
            };

            sendQuestAct(actionObj).then(res => {
              if (res && res.ok !== false) {
                addQuestLog('sword', `${info.icon} Скилл "${info.name}"${targetName ? ' → ' + targetName : (manualTargetLead && hasLeader && !isDef && !isBuff ? ' → Вожак' : '')}`);
              } else {
                addQuestLog('warn', `⚠️ Скилл "${info.name}" не принят: ${res?.error || 'ошибка'}`);
              }
              refreshQuestUI();
            });
          };
        });
      } else {
        // Вне боя: отображаем только действительно изученные персонажем скиллы
        const knownList = getHeroKnownSkills(heroRole);
        if (qSkillsCount) qSkillsCount.textContent = `${knownList.length} изучено (вне боя)`;
        qSkillsList.innerHTML = knownList.map(s => `
          <button type="button" class="b-qsk-btn" data-qsk="${s.key}" data-qkind="${s.kind}" style="flex:1;min-width:105px;padding:5px 8px;background:#1e1b33;color:#94a3b8;border:1px solid #3d3759;border-radius:5px;cursor:pointer;font-size:10px;font-weight:bold;display:flex;justify-content:space-between;align-items:center;transition:all 0.15s ease;" title="${s.name} — боевой скилл класса (${classAtk.name}), применяется в бою">
            <span style="display:flex;align-items:center;gap:4px;">
              <span>${s.icon}</span>
              <span>${s.name}</span>
            </span>
            <span style="background:rgba(148,163,184,0.15);color:#94a3b8;padding:1px 5px;border-radius:3px;font-size:9px;font-weight:normal;">Вне боя</span>
          </button>
        `).join('');

        qSkillsList.querySelectorAll('.b-qsk-btn').forEach(btn => {
          btn.onclick = () => {
            const skKey = btn.dataset.qsk;
            const kind = btn.dataset.qkind;
            const info = getSkillInfo(skKey, kind);
            showToast(`${info.icon} Боевой скилл «${info.name}». Дождитесь начала боя для использования!`);
          };
        });
      }
    }
  }

  function initQuestModule() {
    if (G.LGQuest && G.__lgHookQuest && !G.LGQuest.__hooked) {
      G.__lgHookQuest(G.LGQuest);
    }

    ensureQaMode();

    if (qClassBadge) {
      qClassBadge.textContent = getClassTitle(getHeroRole());
    }

    // Запрос данных персонажа из /api/me для авто-детекта роли
    gameApi('/api/me').then(data => {
      if (data && data.hero) {
        G.ME = data;
        G.__lgHero = data.hero;
        if (data.hero.role) {
          try { localStorage.setItem('lg_hero_role', data.hero.role); } catch (e) {}
        }
        refreshQuestUI();
      }
    }).catch(() => {});

    if (questSettings.fastForward) {
      try {
        localStorage.setItem('lgQDelay', '0');
      } catch (e) {}
      addQuestLog('speed', '⚡ Стрим без задержки (dl: 0) активен');
    }

    if (questSettings.autoParry) {
      addQuestLog('shield', '🛡️ Мгновенный Авто-Блок босса активен');
    }

    if (questSettings.dungeonSolver) {
      addQuestLog('puzzle', '🧩 Радар тайников Данжа готов');
    }

    // Слушатели тумблеров
    if (optAutoJoin) {
      optAutoJoin.checked = questSettings.autoJoin;
      optAutoJoin.onchange = () => {
        questSettings.autoJoin = optAutoJoin.checked;
        localStorage.setItem('lg_quest_autojoin', questSettings.autoJoin ? '1' : '0');
        addQuestLog(questSettings.autoJoin ? 'speed' : 'info', `🤝 Автопоход ${questSettings.autoJoin ? 'включен' : 'выключен'}`);
        refreshQuestUI();
      };
    }

    if (optDgnVote) {
      optDgnVote.checked = questSettings.dungeonAutoVote;
      optDgnVote.onchange = () => {
        questSettings.dungeonAutoVote = optDgnVote.checked;
        localStorage.setItem('lg_quest_dgnvote', questSettings.dungeonAutoVote ? '1' : '0');
        addQuestLog(questSettings.dungeonAutoVote ? 'puzzle' : 'info', `🚪 Авто-двери данжа ${questSettings.dungeonAutoVote ? 'включены' : 'выключены'}`);
      };
    }

    if (optFastForward) {
      optFastForward.checked = questSettings.fastForward;
      optFastForward.onchange = () => {
        questSettings.fastForward = optFastForward.checked;
        localStorage.setItem('lg_quest_fastforward', questSettings.fastForward ? '1' : '0');
        if (questSettings.fastForward) {
          try { localStorage.setItem('lgQDelay', '0'); } catch (e) {}
          addQuestLog('speed', '⚡ Стрим без задержки включен');
        } else {
          addQuestLog('info', 'ℹ️ Стрим без задержки отключен');
        }
      };
    }

    if (optAutoParry) {
      optAutoParry.checked = questSettings.autoParry;
      optAutoParry.onchange = () => {
        questSettings.autoParry = optAutoParry.checked;
        localStorage.setItem('lg_quest_autoparry', questSettings.autoParry ? '1' : '0');
        addQuestLog(questSettings.autoParry ? 'shield' : 'info', `🛡️ Авто-блок ${questSettings.autoParry ? 'включен' : 'выключен'}`);
      };
    }

    if (optDgnSolver) {
      optDgnSolver.checked = questSettings.dungeonSolver;
      optDgnSolver.onchange = () => {
        questSettings.dungeonSolver = optDgnSolver.checked;
        localStorage.setItem('lg_quest_solver', questSettings.dungeonSolver ? '1' : '0');
        addQuestLog(questSettings.dungeonSolver ? 'puzzle' : 'info', `🧩 Радар данжа ${questSettings.dungeonSolver ? 'включен' : 'выключен'}`);
        refreshQuestUI();
      };
    }

    if (optSolveCode) {
      optSolveCode.checked = questSettings.autoSolveCode;
      optSolveCode.onchange = () => {
        questSettings.autoSolveCode = optSolveCode.checked;
        localStorage.setItem('lg_quest_autosolve_code', questSettings.autoSolveCode ? '1' : '0');
        addQuestLog(questSettings.autoSolveCode ? 'puzzle' : 'info', `🔮 Декодер сокровищницы ${questSettings.autoSolveCode ? 'включен' : 'выключен'}`);
        refreshQuestUI();
      };
    }

    if (optCombat) {
      optCombat.checked = questSettings.autoCombat;
      optCombat.onchange = () => {
        questSettings.autoCombat = optCombat.checked;
        localStorage.setItem('lg_quest_combat', questSettings.autoCombat ? '1' : '0');
        addQuestLog(questSettings.autoCombat ? 'sword' : 'info', `⚔️ Авто-бой ${questSettings.autoCombat ? 'включен' : 'выключен'}`);
      };
    }

    if (optQaMode) {
      optQaMode.checked = questSettings.qaMode;
      optQaMode.onchange = () => {
        questSettings.qaMode = optQaMode.checked;
        localStorage.setItem('lg_quest_qamode', questSettings.qaMode ? '1' : '0');
        ensureQaMode();
        addQuestLog('info', `🔓 QA-режим кнопок ${questSettings.qaMode ? 'включен' : 'выключен'}`);
      };
    }

    if (optCrossing) {
      optCrossing.checked = questSettings.autoCrossing;
      optCrossing.onchange = () => {
        questSettings.autoCrossing = optCrossing.checked;
        localStorage.setItem('lg_quest_autocrossing', questSettings.autoCrossing ? '1' : '0');
        addQuestLog(questSettings.autoCrossing ? 'puzzle' : 'info', `🪵 Авто-Переправа ${questSettings.autoCrossing ? 'включена' : 'выключена'}`);
      };
    }

    if (optBog) {
      optBog.checked = questSettings.autoBog;
      optBog.onchange = () => {
        questSettings.autoBog = optBog.checked;
        localStorage.setItem('lg_quest_autobog', questSettings.autoBog ? '1' : '0');
        addQuestLog(questSettings.autoBog ? 'sword' : 'info', `🫧 Авто-Трясина ${questSettings.autoBog ? 'включена' : 'выключена'}`);
      };
    }

    if (optHwEvents) {
      optHwEvents.checked = questSettings.autoHwEvents;
      optHwEvents.onchange = () => {
        questSettings.autoHwEvents = optHwEvents.checked;
        localStorage.setItem('lg_quest_autohwevents', questSettings.autoHwEvents ? '1' : '0');
        addQuestLog(questSettings.autoHwEvents ? 'speed' : 'info', `🎃 Авто-Ивенты ${questSettings.autoHwEvents ? 'включены' : 'выключены'}`);
      };
    }

    if (btnClearLog) {
      btnClearLog.onclick = () => {
        questLogHistory = [];
        renderQuestLog();
      };
    }

    // Слушатели кнопок действий
    if (btnActAtk) {
      btnActAtk.onclick = () => {
        const heroRole = getHeroRole();
        const classAtk = getClassBaseAttack(heroRole);
        const v = getQuestView();
        if (!v || !v.b) {
          showToast(`${classAtk.icon} Атака «${classAtk.name}» готова. Дождитесь начала боя!`);
          return;
        }
        if (v.b.st !== 'party') {
          showToast('Сейчас не ваш ход!');
          return;
        }
        if (v.b.turn) {
          showToast('Ход в этом раунде уже сделан');
          return;
        }
        const isPriest = (heroRole === 'priest' || heroRole.includes('жрец'));
        const hasLeader = (v.b.foes || []).some(f => f[2] && f[3] > 0);
        const myNick = (G.ME?.user?.login || G.__lgMineMe?.name || getMyNick() || '').toLowerCase();
        const wounded = isPriest ? (v.b.low || []).filter(x => x[0] !== myNick && x[2] < 0.9).sort((a,b) => a[2] - b[2]) : [];

        const heroIdx = typeof v.b.me === 'number' ? v.b.me : -1;
        const p = heroIdx >= 0 && v.b.form ? v.b.form[heroIdx] : null;
        const mf = !p && Array.isArray(v.b.mf) ? v.b.mf : null;
        const side = p ? (p[2] || 'R') : mf ? (mf[1] || 'R') : 'R';
        const leadFoes = (v.b.foes || []).filter(f => f[2] && f[3] > 0);
        const leadSide = leadFoes.length > 0 ? leadFoes[0][0] : '';
        const chosenSide = manualTargetLead && leadSide ? leadSide : side;

        const actObj = isPriest ? {
          k: 'b',
          a: 'base',
          ...(wounded.length ? { t: wounded[0][0] } : {})
        } : {
          k: 'b',
          a: 'base',
          ...(manualTargetLead && hasLeader ? { t: 'lead' } : {}),
          ...(chosenSide ? { s: chosenSide } : {})
        };

        sendQuestAct(actObj).then(res => {
          if (res && res.ok !== false) {
            addQuestLog('sword', `${classAtk.icon} ${classAtk.name}${isPriest && wounded.length ? ' → ' + (wounded[0][1] || wounded[0][0]) : (manualTargetLead && hasLeader && !isPriest ? ' → Вожак' : '')}`);
          }
          refreshQuestUI();
        });
      };
    }

    if (btnActSub) {
      btnActSub.onclick = () => {
        const heroRole = getHeroRole();
        const isPriest = (heroRole === 'priest' || heroRole.includes('жрец'));
        if (isPriest) {
          const v = getQuestView();
          if (!v || !v.b) {
            showToast('⚔️ Ближний бой жреца готов. Дождитесь начала боя!');
            return;
          }
          if (v.b.st !== 'party') {
            showToast('Сейчас не ваш ход!');
            return;
          }
          if (v.b.turn) {
            showToast('Ход в этом раунде уже сделан');
            return;
          }
          const heroIdx = typeof v.b.me === 'number' ? v.b.me : -1;
          const p = heroIdx >= 0 && v.b.form ? v.b.form[heroIdx] : null;
          const mf = !p && Array.isArray(v.b.mf) ? v.b.mf : null;
          const side = p ? (p[2] || 'R') : mf ? (mf[1] || 'R') : 'R';
          const leadFoes = (v.b.foes || []).filter(f => f[2] && f[3] > 0);
          const leadSide = leadFoes.length > 0 ? leadFoes[0][0] : '';
          const chosenSide = leadSide || side;

          sendQuestAct({ k: 'b', a: 'melee', ...(chosenSide ? { s: chosenSide } : {}) }).then(res => {
            if (res && res.ok !== false) {
              addQuestLog('sword', `⚔️ Ближний бой жреца${chosenSide ? ` (${chosenSide})` : ''}`);
            }
            refreshQuestUI();
          });
        } else {
          manualTargetLead = !manualTargetLead;
          addQuestLog('info', manualTargetLead ? '🎯 Выбран приоритет цели: ВОЖАК' : '👥 Выбран приоритет цели: РЯДОВЫЕ');
          refreshQuestUI();
        }
      };
    }

    if (btnActDef) {
      btnActDef.onclick = () => {
        const v = getQuestView();
        if (v && v.b && v.b.tele && v.b.tele.word) {
          const hush = isQuestHush(v);
          if (hush) {
            showToast(`⚠️ ${hush}: говорить запрещено!`);
            return;
          }
          sendQuestSay(v.b.tele.word).then(() => {
            addQuestLog('shield', `🛡️ Отправлено слово блока: "${v.b.tele.word}"`);
            refreshQuestUI();
          });
        } else {
          showToast('🛡️ Атаки босса сейчас нет (блок парированием срабатывает во время атаки босса)');
        }
      };
    }

    if (btnActSquad) {
      btnActSquad.onclick = () => {
        const v = getQuestView();
        const p = v ? (v.party || 'out') : 'out';
        if (p === 'in') {
          sendQuestAct({ k: 'leave' }).then(() => {
            addQuestLog('info', '🏃 Отправлен выход из отряда');
            refreshQuestUI();
          });
        } else {
          sendQuestAct({ k: 'join' }).then(() => {
            if (v && v.gather && v.gather.hw) {
              sendQuestSay('портал');
              addQuestLog('portal', '🎃 Отправлен шаг в портал Изнанки («портал»)');
            } else {
              addQuestLog('info', '🏃 Отправлено вступление в поход');
            }
            refreshQuestUI();
          });
        }
      };
    }

    renderQuestLog();
    refreshQuestUI();

    // Фоновый тикер похода: проверяет авто-вход, паузу майнера, бой, телеграфы и таймеры каждую секунду
    setInterval(() => {
      if (!G.__mineBotRunning) return;
      ensureQaMode();
      const v = getQuestView();
      if (v && v.on) {
        checkAutoJoinSquad(v);
        checkQuestMinerCoordination(v);
        if (v.b) {
          if (v.b.st === 'tele') checkAutoParry(v.b, v);
          if (v.b.st === 'party') checkAutoCombat(v.b, v);
        }
        if (v.lair && v.lair.tele) checkAutoParryLair(v.lair, v);
        checkHotPumpkin(v);
        const trData = v.tr || (G.LGQuest && G.LGQuest._view && G.LGQuest._view()?.tr);
        const evData = v.ev || (G.LGQuest && G.LGQuest._view && G.LGQuest._view()?.ev);
        if (trData && trData.cx) checkAutoCrossing(trData, v);
        if (trData && trData.bog) checkAutoBog(trData, v);
        if (trData && trData.type === 'code') checkAutoSolveCode(trData, v);
        if (trData && trData.type === 'react') checkAutoReactTrap(trData, v);
        if (trData && trData.type === 'ceiling') checkAutoCeilingTrap(trData, v);
        if (evData) checkAutoHwEvents(evData, v);
        if (v.loot) checkAutoLoot(v.loot, v);
        checkAutoVotes(v);
      } else if (v) {
        checkQuestMinerCoordination(v);
      }
      if (activeTab === 'quest') {
        refreshQuestUI();
      }
      if (!isPaused && !questAutoPausedMiner && isDrillBuild && !isRidingLift) {
        processDrillAutoFund();
      }
    }, 1000);
  }
  let isMinimized = localStorage.getItem('lg_bot_min') === '1';
  let isSquare = localStorage.getItem('lg_bot_square') === '1';
  let isHoveredFromSquare = false;
  let squareHoverTimer = null;

  function applyMinimized() {
    const isSquareActive = isSquare && !isHoveredFromSquare;
    const isLineActive = (isSquare && isHoveredFromSquare) || (!isSquare && isMinimized);

    if (isSquareActive) {
      // Режим "Маленький квадратик" (34x34px)
      if (bHeader) bHeader.style.display = 'none';
      if (bBody) bBody.style.display = 'none';
      if (bCollapseHint) bCollapseHint.style.display = 'none';
      if (bSquareBadge) bSquareBadge.style.display = 'flex';

      w.style.minWidth = '34px';
      w.style.width = '34px';
      w.style.height = '34px';
      w.style.minHeight = '34px';
      w.style.padding = '0';
      w.style.borderRadius = '8px';
      w.style.overflow = 'hidden';
      w.style.cursor = 'pointer';
      w.style.boxShadow = '0 2px 10px rgba(0,0,0,0.5)';
    } else if (isLineActive) {
      // Режим "Свернутая линия"
      if (bSquareBadge) bSquareBadge.style.display = 'none';
      if (bHeader) bHeader.style.display = 'flex';
      if (bBody) bBody.style.display = 'none';

      if (bCollapseHint) bCollapseHint.style.display = 'none';
      if (bMin) {
        bMin.textContent = '+';
        bMin.title = 'Развернуть окно';
      }
      if (bSquareBtn) {
        bSquareBtn.textContent = '↙';
        bSquareBtn.title = 'Свернуть в квадратик';
      }

      w.style.minWidth = 'auto';
      w.style.width = 'auto';
      w.style.height = 'auto';
      w.style.minHeight = 'auto';
      w.style.padding = '6px 12px';
      w.style.borderRadius = '12px';
      w.style.overflow = 'visible';
      w.style.cursor = 'grab';
      w.style.boxShadow = '0 4px 20px rgba(0,0,0,0.6)';

      if (bMiniSummary) bMiniSummary.style.display = 'inline';
      if (typeof updateStatsUI === 'function') updateStatsUI();
    } else {
      // Режим "Полное окно"
      if (bSquareBadge) bSquareBadge.style.display = 'none';
      if (bHeader) bHeader.style.display = 'flex';
      if (bBody) bBody.style.display = 'block';

      if (bCollapseHint) bCollapseHint.style.display = 'inline-block';
      if (bMin) {
        bMin.textContent = '−';
        bMin.title = 'Свернуть окно';
      }
      if (bSquareBtn) {
        bSquareBtn.textContent = '↙';
        bSquareBtn.title = 'Свернуть в квадратик';
      }

      w.style.minWidth = (activeTab === 'market' || activeTab === 'players' || activeTab === 'broadcast' || activeTab === 'city' || activeTab === 'quest' ? '310px' : '250px');
      w.style.maxWidth = (activeTab === 'broadcast' ? '370px' : 'none');
      w.style.width = 'auto';
      w.style.height = 'auto';
      w.style.minHeight = 'auto';
      w.style.padding = '10px 14px';
      w.style.borderRadius = '12px';
      w.style.overflow = 'visible';
      w.style.cursor = 'default';
      w.style.boxShadow = '0 4px 20px rgba(0,0,0,0.6)';

      if (bMiniSummary) bMiniSummary.style.display = 'none';
    }

    updateMiniDealUI();

    localStorage.setItem('lg_bot_square', isSquare ? '1' : '0');
    localStorage.setItem('lg_bot_min', isMinimized ? '1' : '0');

    // Корректируем координаты, если окно при разворачивании вылезло за границы экрана
    setTimeout(() => {
      const rect = w.getBoundingClientRect();
      let needSave = false;
      let newLeft = rect.left;
      let newTop = rect.top;
      if (rect.right > window.innerWidth) {
        newLeft = Math.max(0, window.innerWidth - rect.width - 10);
        w.style.left = `${newLeft}px`;
        needSave = true;
      }
      if (rect.bottom > window.innerHeight) {
        newTop = Math.max(0, window.innerHeight - rect.height - 10);
        w.style.top = `${newTop}px`;
        needSave = true;
      }
      if (needSave) {
        try {
          localStorage.setItem('lg_bot_pos', JSON.stringify({ left: Math.round(newLeft), top: Math.round(newTop) }));
        } catch (e) {}
      }
    }, 50);
  }

  // Клик по надписи "Свернуть" в центре шапки
  if (bCollapseHint) {
    bCollapseHint.onmouseenter = () => {
      bCollapseHint.style.color = '#00ff66';
      bCollapseHint.style.borderColor = 'rgba(0, 255, 102, 0.3)';
      bCollapseHint.style.background = 'rgba(0, 255, 102, 0.08)';
    };
    bCollapseHint.onmouseleave = () => {
      bCollapseHint.style.color = '#8e8aa0';
      bCollapseHint.style.borderColor = 'rgba(255, 255, 255, 0.08)';
      bCollapseHint.style.background = 'rgba(255, 255, 255, 0.03)';
    };
    bCollapseHint.onclick = (e) => {
      e.stopPropagation();
      if (isSquare) {
        isSquare = false;
        isMinimized = false;
        isHoveredFromSquare = false;
      } else {
        isMinimized = !isMinimized;
      }
      applyMinimized();
    };
  }

  // Кнопка свернуть/развернуть (+/-) если присутствует в DOM
  if (bMin) {
    bMin.onclick = (e) => {
      e.stopPropagation();
      if (isSquare) {
        isSquare = false;
        isMinimized = false;
        isHoveredFromSquare = false;
      } else {
        isMinimized = !isMinimized;
      }
      applyMinimized();
    };
  }

  // Кнопка со стрелочкой (↙): сворачивание в маленький квадратик
  if (bSquareBtn) {
    bSquareBtn.onclick = (e) => {
      e.stopPropagation();
      isSquare = !isSquare;
      if (isSquare) {
        isMinimized = true;
      }
      isHoveredFromSquare = false;
      applyMinimized();
    };
  }

  // Клик по самому квадратику: разворачивается полностью
  if (bSquareBadge) {
    bSquareBadge.onclick = (e) => {
      e.stopPropagation();
      if (hasDragged) return;
      isSquare = false;
      isMinimized = false;
      isHoveredFromSquare = false;
      applyMinimized();
    };
  }

  // Клик по полоске заголовка
  w.querySelector('#b-title-wrap').onclick = () => {
    if (hasDragged) return;
    if (isSquare) {
      // При клике в режиме квадратика — разворачиваем полностью
      isSquare = false;
      isMinimized = false;
      isHoveredFromSquare = false;
    } else {
      isMinimized = !isMinimized;
    }
    applyMinimized();
  };

  // Наведение курсора: квадратик разворачивается в линию со статистикой
  w.addEventListener('mouseenter', () => {
    if (isSquare && !isDragging) {
      if (squareHoverTimer) clearTimeout(squareHoverTimer);
      squareHoverTimer = setTimeout(() => {
        if (isSquare && !isDragging) {
          isHoveredFromSquare = true;
          applyMinimized();
        }
      }, 120);
    }
  });

  // Увод курсора: возвращается обратно в квадратик
  w.addEventListener('mouseleave', () => {
    if (squareHoverTimer) {
      clearTimeout(squareHoverTimer);
      squareHoverTimer = null;
    }
    if (isSquare && isHoveredFromSquare && !isDragging) {
      isHoveredFromSquare = false;
      applyMinimized();
    }
  });

  applyMinimized();

  // ---------- ПЕРЕМЕЩЕНИЕ ВИДЖЕТА ПО ЭКРАНУ (DRAG & DROP ЛКМ) ----------
  let isDragging = false;
  let hasDragged = false;
  let dragStartX = 0;
  let dragStartY = 0;
  let initialLeft = 0;
  let initialTop = 0;

  function onDragStart(e) {
    const isTouch = e.type === 'touchstart';
    if (!isTouch && e.button !== 0) return; // Только левая кнопка мыши (ЛКМ)

    // Не начинаем перемещение при клике по интерактивным элементам управления
    if (e.target.closest('button, select, input, textarea, a, option, .no-drag')) {
      return;
    }

    if (squareHoverTimer) {
      clearTimeout(squareHoverTimer);
      squareHoverTimer = null;
    }

    // В развернутом режиме перетаскиваем только за шапку (или свободные края виджета вне b-body),
    // а в свернутом режиме (линия или квадратик) — за любое место виджета
    if (!isMinimized && !isSquare && e.target.closest('#b-body')) {
      return;
    }

    const clientX = isTouch ? e.touches[0].clientX : e.clientX;
    const clientY = isTouch ? e.touches[0].clientY : e.clientY;

    isDragging = true;
    hasDragged = false;
    dragStartX = clientX;
    dragStartY = clientY;

    const rect = w.getBoundingClientRect();
    initialLeft = rect.left;
    initialTop = rect.top;

    // Отключаем transition на время перемещения для мгновенного отклика без лагов
    w.style.transition = 'none';

    const onDragMove = (moveEvent) => {
      if (!isDragging) return;
      const isMoveTouch = moveEvent.type === 'touchmove';
      const curX = isMoveTouch ? moveEvent.touches[0].clientX : moveEvent.clientX;
      const curY = isMoveTouch ? moveEvent.touches[0].clientY : moveEvent.clientY;

      const dx = curX - dragStartX;
      const dy = curY - dragStartY;

      if (!hasDragged && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) {
        hasDragged = true;
        document.body.style.userSelect = 'none';
        document.body.style.cursor = 'grabbing';
        w.style.cursor = 'grabbing';
      }

      if (hasDragged) {
        if (moveEvent.cancelable) moveEvent.preventDefault();

        let newLeft = initialLeft + dx;
        let newTop = initialTop + dy;

        // Не даем окну улететь за границы видимого экрана
        const maxLeft = Math.max(0, window.innerWidth - w.offsetWidth);
        const maxTop = Math.max(0, window.innerHeight - w.offsetHeight);

        newLeft = Math.min(Math.max(0, newLeft), maxLeft);
        newTop = Math.min(Math.max(0, newTop), maxTop);

        w.style.left = `${newLeft}px`;
        w.style.top = `${newTop}px`;
        w.style.right = 'auto';
        w.style.bottom = 'auto';
      }
    };

    const onDragEnd = () => {
      if (!isDragging) return;
      isDragging = false;

      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      w.style.cursor = (isSquare && !isHoveredFromSquare) ? 'pointer' : (isMinimized || (isSquare && isHoveredFromSquare) ? 'grab' : 'default');
      w.style.transition = 'padding 0.15s ease, min-width 0.15s ease, width 0.15s ease, height 0.15s ease, border-radius 0.15s ease, box-shadow 0.2s ease';

      window.removeEventListener('mousemove', onDragMove);
      window.removeEventListener('mouseup', onDragEnd);
      window.removeEventListener('touchmove', onDragMove);
      window.removeEventListener('touchend', onDragEnd);
      window.removeEventListener('touchcancel', onDragEnd);

      if (hasDragged) {
        if (isSquare) {
          isHoveredFromSquare = false;
          applyMinimized();
        }
        const rect = w.getBoundingClientRect();
        try {
          localStorage.setItem('lg_bot_pos', JSON.stringify({
            left: Math.round(rect.left),
            top: Math.round(rect.top)
          }));
        } catch (err) {}

        // Предотвращаем срабатывание клика сразу после перетаскивания (чтобы случайно не свернуть/развернуть окно при отпускании ЛКМ)
        const suppressClick = (clickEvent) => {
          clickEvent.stopPropagation();
          clickEvent.preventDefault();
        };
        window.addEventListener('click', suppressClick, { capture: true, once: true });
        setTimeout(() => {
          window.removeEventListener('click', suppressClick, { capture: true });
        }, 80);
      }
    };

    window.addEventListener('mousemove', onDragMove);
    window.addEventListener('mouseup', onDragEnd);
    window.addEventListener('touchmove', onDragMove, { passive: false });
    window.addEventListener('touchend', onDragEnd);
    window.addEventListener('touchcancel', onDragEnd);
  }

  w.addEventListener('mousedown', onDragStart);
  w.addEventListener('touchstart', onDragStart, { passive: true });

  window.addEventListener('resize', () => {
    if (!document.body.contains(w)) return;
    const rect = w.getBoundingClientRect();
    let needUpdate = false;
    let l = rect.left, t = rect.top;
    if (rect.right > window.innerWidth) {
      l = Math.max(0, window.innerWidth - rect.width - 10);
      needUpdate = true;
    }
    if (rect.bottom > window.innerHeight) {
      t = Math.max(0, window.innerHeight - rect.height - 10);
      needUpdate = true;
    }
    if (needUpdate) {
      w.style.left = `${l}px`;
      w.style.top = `${t}px`;
    }
  });

  // ==========================================
  // МОДУЛЬ УЧЁТА РУДЫ И РЕСУРСОВ (Мешок + Инвентарь + Склад)
  // ==========================================
  const ORE_CATALOG = {
    stone:       { name: 'Камень', color: '#9a8e80', kind: 'base' },
    basalt:      { name: 'Базальт', color: '#4a4248', kind: 'base' },
    frost_stone: { name: 'Мёрзлый камень', color: '#bcd8ea', kind: 'base' },
    limestone:   { name: 'Известняк', color: '#e4d6b0', kind: 'base' },
    sandstone:   { name: 'Песчаник', color: '#e0a868', kind: 'base' },
    rubble:      { name: 'Щебень', color: '#b0a090', kind: 'base' },
    coal:        { name: 'Уголь', color: '#524f5d', kind: 'ore' },
    iron:        { name: 'Железо', color: '#c8643a', kind: 'ore' },
    copper:      { name: 'Медь', color: '#d8803a', kind: 'ore' },
    sulfur:      { name: 'Сера', color: '#f2e24a', kind: 'ore' },
    peat:        { name: 'Торф', color: '#6a4a32', kind: 'ore' },
    bone_dust:   { name: 'Костяная пыль', color: '#eee6cc', kind: 'ore' },
    salt:        { name: 'Соль', color: '#ffffff', kind: 'ore' },
    silver:      { name: 'Серебро', color: '#eef3fa', kind: 'rich' },
    relic:       { name: 'Древности', color: '#d8b050', kind: 'rich' },
    gold:        { name: 'Золото', color: '#ffcf3a', kind: 'rare' },
    crystal:     { name: 'Кристаллы', color: '#7fe8ff', kind: 'rare' },
    adamant:     { name: 'Адамантит', color: '#9fc0e8', kind: 'gem' },
    fire_opal:   { name: 'Огненный опал', color: '#ff6a2a', kind: 'gem' },
    emerald:     { name: 'Изумруд', color: '#2fd07a', kind: 'gem' },
    amber:       { name: 'Янтарь', color: '#ffa52a', kind: 'gem' },
    sapphire:    { name: 'Сапфир', color: '#3a7aff', kind: 'gem' },
    topaz:       { name: 'Топаз', color: '#ffc23a', kind: 'gem' },
    malachite:   { name: 'Малахит', color: '#1fa07a', kind: 'gem' },
    morion:      { name: 'Морион', color: '#6a3a8a', kind: 'gem' },
    aquamarine:  { name: 'Аквамарин', color: '#6fe8e8', kind: 'gem' },
    ruby:        { name: 'Рубин', color: '#ff2a4a', kind: 'gem' },
    runestone:   { name: 'Рунный камень', color: '#a87aff', kind: 'gem' }
  };

  const ORE_NAME_TO_KEY = {};
  for (const [k, v] of Object.entries(ORE_CATALOG)) {
    ORE_NAME_TO_KEY[v.name.toLowerCase()] = k;
  }
  ORE_NAME_TO_KEY['адамант'] = 'adamant';

  function resolveOreKey(it) {
    if (!it) return null;
    if (typeof it === 'string') {
      const s = it.toLowerCase().trim().replace(/^ore_/, '');
      if (ORE_CATALOG[s]) return s;
      if (ORE_NAME_TO_KEY[s]) return ORE_NAME_TO_KEY[s];
      return null;
    }
    if (it.res) {
      const r = String(it.res).toLowerCase().trim();
      if (ORE_CATALOG[r]) return r;
      if (ORE_NAME_TO_KEY[r]) return ORE_NAME_TO_KEY[r];
    }
    if (it.id) {
      const id = String(it.id).toLowerCase().trim().replace(/^ore_/, '');
      if (ORE_CATALOG[id]) return id;
      if (ORE_NAME_TO_KEY[id]) return ORE_NAME_TO_KEY[id];
    }
    if (it.name) {
      const n = String(it.name).toLowerCase().trim();
      if (ORE_NAME_TO_KEY[n]) return ORE_NAME_TO_KEY[n];
    }
    return null;
  }

  function oreN(q) {
    q = Math.max(0, Math.floor(+q || 0));
    const n = Math.floor(q / 100), p = q % 100;
    return p ? (n + ',' + String(p).padStart(2, '0')) : String(n);
  }

  function formatOreQtyDisplay(q) {
    q = Math.max(0, Math.floor(+q || 0));
    const n = Math.floor(q / 100), p = q % 100;
    if (n && p) return n + ' шт. + ' + p + ' оск.';
    if (n) return n + ' шт.';
    return p + ' оск.';
  }

  function getFallbackOreSvg(key, color, kind, size) {
    size = size || 26;
    if (kind === 'gem') {
      return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">' +
        '<polygon points="14,3 23,9 21,23 7,23 5,9" fill="' + color + '" stroke="#1d1520" stroke-width="1.5" stroke-linejoin="round"/>' +
        '<polygon points="14,3 23,9 14,14 5,9" fill="rgba(255,255,255,0.4)"/>' +
        '<polygon points="14,14 21,23 7,23" fill="rgba(0,0,0,0.2)"/>' +
      '</svg>';
    }
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">' +
      '<path d="M5 19L8 8L16 5L23 11L21 21L11 23Z" fill="' + color + '" stroke="#1d1520" stroke-width="1.5" stroke-linejoin="round"/>' +
      '<path d="M8 8L16 5L14 13L7 14Z" fill="rgba(255,255,255,0.35)"/>' +
      '<path d="M14 13L23 11L21 21L12 18Z" fill="rgba(0,0,0,0.25)"/>' +
    '</svg>';
  }

  function getAggregatedOreData() {
    const ores = new Map();

    function getEntry(key, defaultName) {
      const resolved = resolveOreKey(key || defaultName);
      if (!resolved || !ORE_CATALOG[resolved]) return null;
      if (!ores.has(resolved)) {
        const info = ORE_CATALOG[resolved];
        ores.set(resolved, {
          key: resolved,
          name: info.name || defaultName || resolved,
          color: info.color || '#ffd700',
          kind: info.kind || 'ore',
          bagQ: 0,
          invQ: 0,
          storeQ: 0,
          totalQ: 0
        });
      }
      return ores.get(resolved);
    }

    // 1. Мешок шахты (window.__lgMineMe?.bag или G.__lgMineS?.me?.bag)
    const mineMe = window.__lgMineMe || (G.__lgMineS && G.__lgMineS.me);
    const bag = mineMe && mineMe.bag;
    if (bag) {
      const r = bag.r || {};
      const p = bag.p || {};
      const bagKeys = new Set([...Object.keys(r), ...Object.keys(p)]);
      for (const k of bagKeys) {
        const wholePieces = Math.max(0, Number(r[k]) || 0);
        const shards = Math.max(0, Number(p[k]) || 0);
        const totalBagShards = wholePieces * 100 + shards;
        if (totalBagShards > 0) {
          const entry = getEntry(k);
          if (entry) entry.bagQ += totalBagShards;
        }
      }
    }

    // 1b. Fallback из DOM шахты (.mn-res li), если bag в памяти ещё не загружен
    if (ores.size === 0) {
      try {
        const resItems = document.querySelectorAll('.mn-res li');
        resItems.forEach(li => {
          const txt = li.textContent.trim();
          if (txt) {
            const parts = txt.split(/[:\n]/);
            const namePart = parts[0]?.trim();
            const qtyPart = parts[1]?.trim() || '';
            const key = resolveOreKey(namePart);
            if (key) {
              const entry = getEntry(key, namePart);
              if (entry) {
                let q = 0;
                const mPcs = qtyPart.match(/(\d+)\s*шт/);
                const mOsk = qtyPart.match(/(\d+)\s*оск/);
                if (mPcs || mOsk) {
                  if (mPcs) q += parseInt(mPcs[1], 10) * 100;
                  if (mOsk) q += parseInt(mOsk[1], 10);
                } else {
                  const num = parseInt(qtyPart.replace(/\D/g, ''), 10);
                  if (!isNaN(num)) q = num;
                }
                if (q > 0) entry.bagQ = Math.max(entry.bagQ, q);
              }
            }
          }
        });
      } catch (e) {}
    }

    // 2. Инвентарь героя (hero.items) — СТРОГО только руда из шахты!
    const hero = G.__lgHero || (G.ME && G.ME.hero) || (G.__lgCityContext && G.__lgCityContext.hero && G.__lgCityContext.hero()) || (G.__lgQuestContext && G.__lgQuestContext.hero && G.__lgQuestContext.hero()) || (G.__lgAucContext && G.__lgAucContext.hero && G.__lgAucContext.hero());
    const invItems = (hero && Array.isArray(hero.items) && hero.items) || [];
    for (const it of invItems) {
      if (!it || it.on) continue;
      const key = resolveOreKey(it);
      if (!key || !ORE_CATALOG[key]) continue; // Не руда — пропускаем!
      const entry = getEntry(key, it.name);
      if (entry) {
        const q = Math.max(0, Number(it.q != null ? it.q : (it.qty != null && typeof it.qty === 'number' ? it.qty : (it.n || 1) * 100)));
        if (q > 0) entry.invQ += q;
      }
    }

    // 3. Склад города (hero.sg.store) — СТРОГО только руда из шахты!
    const storeItems = (hero && hero.sg && Array.isArray(hero.sg.store) && hero.sg.store) || [];
    for (const it of storeItems) {
      if (!it) continue;
      const key = resolveOreKey(it);
      if (!key || !ORE_CATALOG[key]) continue; // Не руда — пропускаем!
      const entry = getEntry(key, it.name);
      if (entry) {
        const q = Math.max(0, Number(it.q != null ? it.q : (it.qty != null && typeof it.qty === 'number' ? it.qty : (it.n || 1) * 100)));
        if (q > 0) entry.storeQ += q;
      }
    }

    // Подсчитываем общий итог
    for (const entry of ores.values()) {
      entry.totalQ = entry.bagQ + entry.invQ + entry.storeQ;
    }

    return Array.from(ores.values()).filter(e => e.totalQ > 0).sort((a, b) => b.totalQ - a.totalQ);
  }

  function renderOreChipsHtml(ores) {
    const filtered = (ores || []).filter(e => e.totalQ > 0);

    if (filtered.length === 0) {
      return '<div style="width:100%;text-align:center;color:#64748b;font-size:9.5px;font-style:italic;padding:4px 0;">Руды пока нет</div>';
    }

    return filtered.map(e => {
      const qCorner = oreN(e.totalQ);
      const qFull = formatOreQtyDisplay(e.totalQ);
      const tip = e.name + '\nВсего: ' + qFull + ' (' + qCorner + ')\n\n• ⛏️ В мешке: ' + formatOreQtyDisplay(e.bagQ) + '\n• 🎒 В сумке: ' + formatOreQtyDisplay(e.invQ) + '\n• 📦 На складе: ' + formatOreQtyDisplay(e.storeQ);

      let artSvg = '';
      if (window.LGOreArt && typeof window.LGOreArt.svg === 'function') {
        try {
          artSvg = window.LGOreArt.svg(e.key, e.kind || 'ore', 26);
        } catch (err) {}
      }
      if (!artSvg) {
        artSvg = getFallbackOreSvg(e.key, e.color, e.kind, 26);
      }

      return '<div class="b-ore-slot" title="' + tip.replace(/"/g, '&quot;') + '" style="position:relative;width:34px;height:34px;min-width:34px;border-radius:6px;background:rgba(18,16,32,0.85);border:1px solid ' + e.color + '55;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;box-sizing:border-box;overflow:hidden;transition:all 0.15s ease;">' +
        '<div style="width:26px;height:26px;display:flex;align-items:center;justify-content:center;pointer-events:none;">' +
          artSvg +
        '</div>' +
        '<span style="position:absolute;right:2px;bottom:1px;font-size:8.5px;font-weight:bold;color:#fff;text-shadow:0 1px 2px #000, 0 0 3px #000, 0 0 4px #000;line-height:1;pointer-events:none;background:rgba(0,0,0,0.55);padding:1px 2px;border-radius:2px;letter-spacing:-0.2px;">' + qCorner + '</span>' +
      '</div>';
    }).join('');
  }

  function updateHeaderProgressUI(passedTimeStr) {
    if (!bMiniSummary) return;
    try {
      let rockIdx = activeMiningRockIndex >= 0 ? activeMiningRockIndex : ((typeof getActiveMiningRockIndex === 'function') ? getActiveMiningRockIndex() : -1);
      let p = (rockIdx >= 0 && isMining()) ? getRockMiningProgress(rockIdx) : null;
      if (p) {
        lastKnownMiningProgress = { p, at: Date.now() };
      } else if (lastKnownMiningProgress && Date.now() - lastKnownMiningProgress.at < 2500 && isMining()) {
        p = lastKnownMiningProgress.p;
      } else if (!isMining()) {
        lastKnownMiningProgress = null;
      }

      const progStr = p ? `⛏️ ${p.pct}% (${p.etaText})` : '';

      let timePart = '';
      if (passedTimeStr && passedTimeStr !== '—') {
        timePart = passedTimeStr;
      } else {
        const me = window.__lgMineMe;
        if (me && me.day != null) {
          const h = Math.floor(me.day / 60), m = Math.round(me.day % 60);
          timePart = h > 0 ? `${h} ч ${m} мин` : `${m} мин`;
        } else {
          const elTime = w.querySelector('#b-stat-time');
          if (elTime && elTime.textContent && elTime.textContent !== '—') timePart = elTime.textContent.trim();
        }
      }

      const parts = [progStr, timePart].filter(Boolean);
      bMiniSummary.textContent = parts.length ? `| ${parts.join(' · ')}` : '';
    } catch (e) {}
  }

  function updateStatsUI() {
    let timeStr = '—', skillStr = '—', coinsStr = '0';
    try { updateDrillsUI(); } catch (e) {}

    // 1. Из перехваченных данных сервера (/api/mine/state)
    const me = window.__lgMineMe;
    if (me) {
      if (me.day != null) {
        const h = Math.floor(me.day / 60), m = Math.round(me.day % 60);
        timeStr = h > 0 ? `${h} ч ${m} мин` : `${m} мин`;
      }
      if (me.mx) {
        const pct = me.mx.need ? Math.round((me.mx.xp / me.mx.need) * 100) : 100;
        skillStr = `ур. ${me.mx.lv} (${pct}%)`;
      }
      if (me.bag) {
        coinsStr = me.bag.cx || (me.bag.c != null ? `${me.bag.c} чешуек` : '0');
      }
    }

    // 2. Если в памяти пусто, парсим из DOM панели игры
    if (timeStr === '—') {
      try {
        const rows = document.querySelectorAll('.mn-me .mn-row, .mn-pb .mn-row');
        rows.forEach(r => {
          const txt = r.textContent || '';
          if (txt.includes('Сегодня в шахте')) {
            const b = r.querySelector('b');
            if (b) timeStr = b.textContent.trim().split(' из ')[0];
          } else if (txt.includes('Горное дело')) {
            const b = r.querySelector('b');
            if (b) skillStr = b.textContent.trim();
          }
        });
        const bagEl = document.querySelector('.mn-bag-c b');
        if (bagEl) coinsStr = bagEl.textContent.trim();
      } catch (e) {}
    }

    const elTime = w.querySelector('#b-stat-time');
    const elSkill = w.querySelector('#b-stat-skill');
    const elCoins = w.querySelector('#b-stat-coins');
    const elRes = w.querySelector('#b-stat-res');

    if (elTime) elTime.textContent = timeStr;
    if (elSkill) elSkill.textContent = skillStr;
    if (elCoins) elCoins.textContent = coinsStr;

    // Обновление слотов всей руды (Мешок + Инвентарь + Склад)
    const aggregatedOres = getAggregatedOreData();
    if (elRes) {
      elRes.innerHTML = renderOreChipsHtml(aggregatedOres);
    }

    // 3. Проверка наград за поручения шахты (q.d, q.w и общее поручение недели q.c)
    const qData = (window.__lgMineMe && window.__lgMineMe.q) || (G.__lgMineS && G.__lgMineS.me && G.__lgMineS.me.q);
    if (qData) {
      const qc = qData.c;
      const qcReady = !!(qc && qc.done && qc.ok && !qc.cl);
      const readyList = [...(qData.d || []), ...(qData.w || [])].filter(x => x.done && !x.cl);
      const canClaim = readyList.length > 0 || qcReady;
      if (mineClaimBtn) {
        if (canClaim) {
          const totalReward = readyList.reduce((acc, x) => acc + (+x.c || 0), 0) + (qcReady ? (+qc.c || 0) : 0);
          mineClaimBtn.style.display = 'inline-block';
          mineClaimBtn.textContent = `🎁 Награда (+${totalReward || readyList.length + (qcReady ? 1 : 0)})`;
          if (autoClaimQuests && !isClaimingQuests && Date.now() - lastQuestClaimTime > 4000) {
            claimMineQuests(true);
          }
        } else if (mineClaimBtn.textContent.indexOf('Забираем') === -1) {
          mineClaimBtn.style.display = 'none';
        }
      }
    }
    if (bSquareDot && dot) {
      bSquareDot.style.background = dot.style.background || '#00ff66';
      bSquareDot.style.boxShadow = `0 0 5px ${dot.style.background || '#00ff66'}`;
    }
    updateHeaderProgressUI(timeStr);
  }

  // Глобальный стиль для фонового режима копания (Ghost Mode)
  function ensureGhostStyle() {
    let style = document.getElementById('lg-ghost-mode-style');
    if (!style) {
      style = document.createElement('style');
      style.id = 'lg-ghost-mode-style';
      style.textContent = `
        body.lg-ghost-mode .mn {
          opacity: 0.001 !important;
          pointer-events: none !important;
          z-index: -9999 !important;
          visibility: visible !important;
          position: fixed !important;
          left: 0 !important;
          top: 0 !important;
          width: 100vw !important;
          height: 100vh !important;
          overflow: hidden !important;
        }
        body.lg-ghost-mode.mn-on {
          overflow: auto !important;
        }
      `;
      document.head.appendChild(style);
    }
  }
  ensureGhostStyle();

  // Наблюдатель за body: не позволяет классу mn-on заблокировать экран в ghost-режиме
  let ghostBodyObserver = null;
  try {
    ghostBodyObserver = new MutationObserver(() => {
      if (mode === 'ghost') {
        if (document.body.classList.contains('mn-on')) {
          document.body.classList.remove('mn-on');
        }
        const mn = document.querySelector('.mn');
        if (mn && mn.style.opacity !== '0.001') {
          setGhostVisuals(true);
        }
      }
    });
    ghostBodyObserver.observe(document.body, { attributes: true, attributeFilter: ['class'], childList: true });
  } catch (e) {}

  function ensureCityVisible(forceSwitch = false) {
    const currentTabBtn = document.querySelector('.tabs button[aria-current="page"]');
    const currentTabId = currentTabBtn ? currentTabBtn.dataset.tab : null;

    // Если открыта другая пользовательская вкладка (персонаж, поход, карта и т.д.)
    // и это не явный вызов перехода в город (forceSwitch): не перебиваем пользователя
    if (!forceSwitch && currentTabId && currentTabId !== 'city') {
      return;
    }

    const cityTabBtn = document.querySelector('.tabs button[data-tab="city"]');
    const tabCity = document.getElementById('tab-city');

    if (cityTabBtn && (!currentTabBtn || currentTabId !== 'city')) {
      try { cityTabBtn.click(); } catch (e) {}
    }
    if (tabCity && !tabCity.classList.contains('on')) {
      tabCity.classList.add('on');
      document.querySelectorAll('.tab').forEach(t => {
        if (t !== tabCity) t.classList.remove('on');
      });
    }

    // Будим / инициализируем сцену Города
    if (G.LGCity) {
      try {
        if (typeof G.LGCity.wake === 'function') {
          G.LGCity.wake();
        }
      } catch (e) {
      }
      // Если сцена города пустая после sleep
      if (tabCity && (!tabCity.children.length || !tabCity.querySelector('.cy-world, .cy, canvas, svg, .cy-entry'))) {
        const ctx = G.__lgCityContext;
        if (ctx && typeof G.LGCity.open === 'function') {
          try {
            G.LGCity.open(tabCity, ctx);
          } catch (e) {}
        } else if (cityTabBtn) {
          try { cityTabBtn.click(); } catch (e) {}
        }
      }
    }
  }

  function setGhostVisuals(isGhost) {
    ensureGhostStyle();
    if (isGhost) {
      document.body.classList.add('lg-ghost-mode');
      document.body.classList.remove('mn-on');
      const mn = document.querySelector('.mn');
      if (mn) {
        mn.style.setProperty('opacity', '0.001', 'important');
        mn.style.setProperty('pointer-events', 'none', 'important');
        mn.style.setProperty('z-index', '-9999', 'important');
        mn.style.setProperty('visibility', 'visible', 'important');
        mn.style.setProperty('position', 'fixed', 'important');
        mn.style.setProperty('left', '0px', 'important');
        mn.style.setProperty('top', '0px', 'important');
        mn.style.setProperty('width', '100vw', 'important');
        mn.style.setProperty('height', '100vh', 'important');
        mn.style.setProperty('overflow', 'hidden', 'important');
      }
      ensureCityVisible(false);
    } else {
      document.body.classList.remove('lg-ghost-mode');
      const mn = document.querySelector('.mn');
      if (mn) {
        mn.style.removeProperty('opacity');
        mn.style.removeProperty('pointer-events');
        mn.style.removeProperty('z-index');
        mn.style.removeProperty('visibility');
        mn.style.removeProperty('position');
        mn.style.removeProperty('left');
        mn.style.removeProperty('top');
        mn.style.removeProperty('width');
        mn.style.removeProperty('height');
        mn.style.removeProperty('overflow');
        document.body.classList.add('mn-on');
      }
    }
  }

  function showCityTab() {
    ensureCityVisible(true);
  }

  btnScreen.onclick = () => {
    mode = 'screen';
    btnScreen.style.background = '#28a745';
    btnScreen.style.color = '#fff';
    btnScreen.style.fontWeight = 'bold';
    btnScreen.style.border = 'none';
    checkScreen.style.display = 'inline';

    btnGhost.style.background = '#262238';
    btnGhost.style.color = '#aaa';
    btnGhost.style.fontWeight = 'normal';
    btnGhost.style.border = '1px solid #3d3759';
    checkGhost.style.display = 'none';

    st.textContent = `Экран: ${getMineLabel(currentMineId)}`;
    st.style.color = '#66ccff';

    setGhostVisuals(false);
    // Проверка на ошибку сервера "mine_closed"
    const closedEl = document.querySelector('.mn-board, .mn-panel, [role="dialog"], body');
    const isClosedText = (closedEl && closedEl.textContent && closedEl.textContent.includes('mine_closed')) ||
                         (document.body && document.body.textContent && document.body.textContent.includes('mine_closed'));
    if (G.__lgMineError === 'mine_closed' || isClosedText) {
      st.textContent = `❌ ${getMineLabel(currentMineId)} закрыта сервером`;
      st.style.color = '#ff4444';
      return;
    }

    if (!isTargetMineOpen(currentMineId)) {
      tryOpenMineOnScreen(currentMineId);
    }

    if (isPaused) {
      startBot();
    }
  };

  btnGhost.onclick = () => {
    mode = 'ghost';
    btnGhost.style.background = '#9b59b6';
    btnGhost.style.color = '#fff';
    btnGhost.style.fontWeight = 'bold';
    btnGhost.style.border = 'none';
    checkGhost.style.display = 'inline';

    btnScreen.style.background = '#262238';
    btnScreen.style.color = '#aaa';
    btnScreen.style.fontWeight = 'normal';
    btnScreen.style.border = '1px solid #3d3759';
    checkScreen.style.display = 'none';

    st.textContent = `Город: ${getMineLabel(currentMineId)}`;
    st.style.color = '#e056fd';

    // Проверка на ошибку сервера "mine_closed"
    const closedEl = document.querySelector('.mn-board, .mn-panel, [role="dialog"], body');
    const isClosedText = (closedEl && closedEl.textContent && closedEl.textContent.includes('mine_closed')) ||
                         (document.body && document.body.textContent && document.body.textContent.includes('mine_closed'));
    if (G.__lgMineError === 'mine_closed' || isClosedText) {
      st.textContent = `❌ ${getMineLabel(currentMineId)} закрыта сервером`;
      st.style.color = '#ff4444';
      return;
    }

    setGhostVisuals(true);

    if (!isTargetMineOpen(currentMineId)) {
      tryOpenMineOnScreen(currentMineId);
    }
    ensureCityVisible(true);

    if (isPaused) {
      startBot();
    }
  };

  // Переключение шахты: чистый переход без искусственных циклов копки
  mineSelect.onchange = async () => {
    const oldMineId = currentMineId;
    currentSid = null;
    G.__lgMineError = null;
    currentMineId = mineSelect.value;
    localStorage.setItem('lg_bot_mine', currentMineId);
    blacklist.clear();
    lastTargetKey = null;
    activeMiningRockIndex = -1;
    lastMiningConfirmedTime = 0;
    lastKnownMiningProgress = null;

    const label = getMineLabel(currentMineId);
    st.textContent = `Шахта: ${label}`;
    st.style.color = '#ffd700';

    await stopMiningInGame(oldMineId);

    // ВСЕГДА открываем новую выбранную шахту: и на экране, и в фоне города (ghost)
    tryOpenMineOnScreen(currentMineId, true);

    if (mode === 'ghost') {
      setGhostVisuals(true);
      ensureCityVisible(false);
    }
  };

  btnRefreshMines.onclick = async () => {
    btnRefreshMines.textContent = '⏳...';
    await scanMines();
    btnRefreshMines.textContent = '🔄 Обновить';
  };

  const btnCustomMine = w.querySelector('#b-custom-mine');
  if (btnCustomMine) {
    btnCustomMine.onclick = () => {
      const customId = prompt('Введите ID шахты или округа для пробы (например: d04, d02, d06, tower, tree, d16, volcano):', '');
      if (customId && customId.trim()) {
        const cleanId = customId.trim().toLowerCase();
        let existing = availableMines.find(m => m.id === cleanId);
        if (!existing) {
          existing = { id: cleanId, name: `Округ ${cleanId}`, danger: 1, test: true };
          availableMines.push(existing);
          updateMineSelectorUI();
        }
        mineSelect.value = cleanId;
        mineSelect.onchange();
      }
    };
  }

  function getMineLabel(id) {
    if (!id || id === 'home') return 'Домашняя ★';
    if (id.startsWith('home.')) return `Домашняя (Этаж ${id.split('.')[1]}) ★`;
    const m = availableMines.find(item => item.id === id);
    if (!m) return id;
    const stars = '★'.repeat(Math.max(1, Math.min(5, m.danger || 1)));
    return `${m.name} ${stars}`;
  }

  function updateMineSelectorUI() {
    // Не перерисовываем список, если пользователь держит его открытым на телефоне
    if (document.activeElement === mineSelect) return;

    const existingVal = mineSelect.value || currentMineId;
    const baseMines = availableMines.filter(m => !m.test);
    const testMines = availableMines.filter(m => m.test);

    let newHtml = baseMines.map(m => {
      const stars = '★'.repeat(Math.max(1, Math.min(5, m.danger || 1)));
      const busyTag = m.busy ? ' (копаем тут)' : '';
      return `<option value="${m.id}">${m.name} ${stars}${busyTag}</option>`;
    }).join('');

    if (testMines.length) {
      newHtml += `<optgroup label="── Соседние округа (на пробу) ──">` + testMines.map(m => {
        const stars = '★'.repeat(Math.max(1, Math.min(5, m.danger || 1)));
        return `<option value="${m.id}">${m.name} ${stars} [тест]</option>`;
      }).join('') + `</optgroup>`;
    }

    if (mineSelect.innerHTML !== newHtml) {
      mineSelect.innerHTML = newHtml;
    }

    const baseId = String(currentMineId || 'home').split('.')[0];
    if (availableMines.some(m => m.id === currentMineId)) {
      mineSelect.value = currentMineId;
    } else if (availableMines.some(m => m.id === baseId)) {
      mineSelect.value = baseId;
    } else if (availableMines.some(m => m.id === existingVal)) {
      mineSelect.value = existingVal;
    } else {
      currentMineId = availableMines[0]?.id || 'home';
      mineSelect.value = currentMineId;
    }
  }

  // Сканирование доступных шахт с гарантией строго одного пункта Рябиненень
  async function scanMines() {
    let found = [
      { id: 'home', name: 'Домашняя', danger: 1, busy: false },
      { id: 'ryabinen', name: 'Рябиненень', danger: 1, busy: false }
    ];

    function addOrUpdate(newMine) {
      if (!newMine || !newMine.id) return;
      const isRyab = (id, nm) => {
        const sid = String(id || '').toLowerCase();
        const snm = String(nm || '').toLowerCase();
        return sid.includes('ryabin') || snm.includes('рябин');
      };

      const isThisRyab = isRyab(newMine.id, newMine.name);

      // Ищем совпадение: либо по точному id, либо если обе относятся к Рябини
      const existing = found.find(f => {
        if (f.id === newMine.id) return true;
        if (isThisRyab && isRyab(f.id, f.name)) return true;
        if (f.name && newMine.name && f.name.toLowerCase().trim() === newMine.name.toLowerCase().trim()) return true;
        return false;
      });

      if (existing) {
        // Если у найденной шахты реальный серверный id, обновляем id
        if (newMine.id && newMine.id !== 'ryabinen') {
          if (currentMineId === existing.id || currentMineId === 'ryabinen') {
            currentMineId = newMine.id;
            localStorage.setItem('lg_bot_mine', currentMineId);
          }
          existing.id = newMine.id;
        }
        if (newMine.name) existing.name = newMine.name;
        if (newMine.danger) existing.danger = newMine.danger;
        if (newMine.busy != null) existing.busy = newMine.busy;
        existing.test = false; // официальная открытая шахта
      } else {
        found.push({
          id: newMine.id,
          name: newMine.name || newMine.id,
          danger: newMine.danger || 1,
          busy: !!newMine.busy,
          test: false
        });
      }
    }

    // 1. Имена городов из /api/city/towns для справочника названий
    const townNames = new Map();
    try {
      if (G.__lgMineCurrent && G.__lgMineCurrent.includes('.')) {
        addOrUpdate({
          id: G.__lgMineCurrent,
          name: `Домашняя (Этаж ${G.__lgMineCurrent.split('.')[1]})`,
          danger: 1,
          busy: false
        });
      }
      const tRes = await gameApi('/api/city/towns');
      if (tRes && Array.isArray(tRes.list)) {
        for (const t of tRes.list) {
          if (t && t.id) townNames.set(t.id, t.name || t.town || t.id);
        }
      }
    } catch (e) {}

    // 2. Официальные открытые шахты из /api/me (герой -> mn)
    try {
      const res = await gameApi('/api/me');
      if (res && res.hero && res.hero.mn) {
        for (const [id, m] of Object.entries(res.hero.mn)) {
          addOrUpdate({
            id: id,
            name: (m && m.nm) || townNames.get(id) || (id === 'home' ? 'Домашняя' : id),
            danger: (m && m.dg) || 1,
            busy: !!(m && m.i >= 0)
          });
        }
      }
    } catch (e) {}

    // 3. Из DOM: вкладки доски "Итоги шахт" ([data-bt])
    const tabs = document.querySelectorAll('[data-bt]');
    tabs.forEach(tab => {
      const id = tab.dataset.bt;
      if (id) {
        const text = tab.textContent.replace(/[★\s]+/g, ' ').trim() || id;
        addOrUpdate({ id, name: text, danger: 1, busy: false });
      }
    });

    // 4. Из DOM: диалог "Шахты наших городов" ([data-go])
    const goBtns = document.querySelectorAll('[data-go]');
    goBtns.forEach(btn => {
      const id = btn.dataset.go;
      if (id) {
        const title = btn.querySelector('b')?.textContent?.replace(/[★\s]+/g, ' ').trim() || id;
        addOrUpdate({ id, name: title, danger: 1, busy: false });
      }
    });

    // 5. Обязательно гарантируем Домашнюю первой
    if (!found.some(m => m.id === 'home')) {
      found.unshift({ id: 'home', name: 'Домашняя', danger: 1, busy: false });
    }

    // Сохраняем тестовые соседние округа для быстрой пробы
    for (const nm of NEIGHBOR_MINES) {
      if (!found.some(f => f.id === nm.id)) {
        found.push({ ...nm });
      }
    }

    availableMines = found;
    updateMineSelectorUI();
  }

  // Проверка: действительно ли открыта именно требуемая шахта targetId
  function isTargetMineOpen(targetId) {
    const target = targetId || currentMineId || 'home';
    const world = document.querySelector('.mn-world');
    if (!world) return false;

    // Во время активной поездки на лифте не дергаем open повторно
    if (isRidingLift) return true;

    // Первые 8 сек после спуска/перехода сцена еще инициализируется
    if (Date.now() - lastFloorTransitionTime < 8000) {
      return true;
    }

    const targetBase = String(target).split('.')[0];

    // 1. По текущему состоянию st.id и st.n движка игры
    const st = (G.__lgMineS && G.__lgMineS.st) || G.__lgMineState;
    if (st && st.id) {
      const stBase = String(st.id).split('.')[0];
      if (stBase !== targetBase) return false;
      if (st.n != null) {
        const deep = st.deep || st.n || 1;
        const currentFloorKey = st.n >= deep ? stBase : `${stBase}.${st.n}`;
        if (target !== currentFloorKey) {
          return false;
        }
      }
    }

    // 2. По перехваченному ID вызова LGMine.open
    if (G.__lgMineCurrent) {
      if (G.__lgMineCurrent !== target) return false;
    }

    return true;
  }

  function tryOpenMineOnScreen(mineId, force = false) {
    let targetMine = mineId || currentMineId || 'home';
    // Если передан псевдоним 'ryabinen', сопоставляем с реальным ID шахты из списка
    const mObj = availableMines.find(m => m.id === targetMine || (m.name && m.name.toLowerCase().includes(targetMine.toLowerCase())));
    if (mObj && mObj.id && mObj.id !== 'ryabinen') {
      targetMine = mObj.id;
    }

    const world = document.querySelector('.mn-world');
    // 🛡️ Защита от мигания экрана: если сцена шахты уже в DOM и мы находимся на этом ключе шахты
    // или еще длится переход между этажами, не разрушаем DOM повторными вызовами open (если не force)
    if (!force && world && (G.__lgMineCurrent === targetMine || isRidingLift || Date.now() - lastFloorTransitionTime < 6000)) {
      return true;
    }

    const ctx = G.__lgMineContext || getMineContext();
    if (G.LGMine && ctx) {
      try {
        G.LGMine.open(ctx, targetMine);
        return true;
      } catch (e) {
      }
    }

    if (G.__lgCityOpenMine) {
      try {
        G.__lgCityOpenMine(targetMine);
        return true;
      } catch (e) {}
    }

    const goBtn = document.querySelector(`[data-go="${targetMine}"]`);
    if (goBtn) {
      goBtn.click();
      return true;
    }

    // Если код шахты еще не загружен на страницу, пробуем нажать кнопку в городе
    if (!G.LGMine) {
      const enterBtn = document.querySelector('[data-b="mine"]') ||
        Array.from(document.querySelectorAll('button, .btn, .cy-entry, [data-go]'))
          .find(b => b.textContent.trim() === 'Шахта' || b.textContent.includes('Шахта'));
      if (enterBtn) {
        enterBtn.click();
        setTimeout(() => {
          if (targetMine && targetMine !== 'home') {
            tryOpenMineOnScreen(targetMine);
          }
          if (mode === 'ghost') {
            setGhostVisuals(true);
            ensureCityVisible(false);
          }
        }, 500);
        return true;
      }
    }
    return false;
  }

  async function stopMiningInGame(targetMineId) {
    const mineId = targetMineId || currentMineId || 'home';
    const stopBtn = document.querySelector('[data-mn-stop]') ||
      Array.from(document.querySelectorAll('button, .btn')).find(b => {
        const t = (b.textContent || '').trim();
        return t === 'Не копать' || t.includes('Не копать') || t.includes('Перестать копать');
      });
    if (stopBtn) {
      try { stopBtn.click(); } catch (e) {}
    }
    try {
      await gameApi('/api/mineStop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ c: mineId, sid: currentSid })
      });
    } catch (e) {}
    await new Promise(r => setTimeout(r, 150));
    return true;
  }

  function startBot() {
    if (!isPaused) return;
    isPaused = false;
    btn.textContent = 'Пауза';
    btn.style.background = '#28a745';
    dot.style.background = '#00ff66';
    if (bSquareDot) {
      bSquareDot.style.background = '#00ff66';
      bSquareDot.style.boxShadow = '0 0 5px #00ff66';
    }
    st.textContent = 'Работает';
    st.style.color = '#66ccff';
    clearTimeout(timer);
    initBackgroundAudioKeepAlive();
    initBackgroundWorker();
    setTimeout(() => { try { scanMyDrillsServerWide(); } catch (e) {} }, 3500);
    if (mode === 'ghost') {
      setGhostVisuals(true);
      ensureCityVisible(false);
    }
    resolveBackgroundWalking();
    startLiveProgressTicker();
    step();
  }

  function pauseBot() {
    if (isPaused) return;
    isPaused = true;
    btn.textContent = 'Старт';
    btn.style.background = '#007bff';
    dot.style.background = '#ffc107';
    if (bSquareDot) {
      bSquareDot.style.background = '#ffc107';
      bSquareDot.style.boxShadow = '0 0 5px #ffc107';
    }
    st.textContent = 'На паузе';
    st.style.color = '#ffc107';
    if (pMineWrap) pMineWrap.style.display = 'none';
    stopMiningInGame();
  }

  btn.onclick = () => {
    if (isPaused) {
      startBot();
    } else {
      pauseBot();
    }
  };

  window.__mineBotStop = () => {
    window.__mineBotRunning = false;
    broadcastAbortRequested = true;
    clearTimeout(timer);
    if (liveProgressTimer) clearInterval(liveProgressTimer);
    clearInterval(refreshMinesTimer);
    if (aucPollTimer) clearInterval(aucPollTimer);
    if (titleBlinkInterval) clearInterval(titleBlinkInterval);
    if (bgWorker) {
      try {
        bgWorker.postMessage('stop');
        bgWorker.terminate();
      } catch (e) {}
      bgWorker = null;
    }
    if (bgAudioCtx) {
      try { bgAudioCtx.close(); } catch (e) {}
      bgAudioCtx = null;
    }
    document.title = originalDocumentTitle;
    hideAucTooltip();
    if (aucTooltip) aucTooltip.remove();
    setGhostVisuals(false);
    if (ghostBodyObserver) ghostBodyObserver.disconnect();
    document.body.classList.remove('lg-ghost-mode');
    document.getElementById('lg-ghost-mode-style')?.remove();
    document.getElementById('lg-mine-floating-container')?.remove();
    w.remove();
  };
  w.querySelector('#b-cls').onclick = window.__mineBotStop;

  function closeNewsModal() {
    const wrap = document.querySelector('.mn-news-w');
    const modal = document.querySelector('.mn-news, [aria-label*="тебя не было"], [role="dialog"].mn-news');
    if (wrap || modal) {
      const root = wrap || modal;
      const okBtn = root.querySelector('[data-ok], [data-x], button.primary, button');
      if (okBtn) {
        try { okBtn.click(); } catch (e) {}
      }
      if (wrap && wrap.parentNode) {
        try { wrap.remove(); } catch (e) {}
      } else if (modal && modal.parentNode && modal.parentNode !== document.body) {
        try { modal.remove(); } catch (e) {}
      }
      try {
        const C = (G.__lgMineS && G.__lgMineS.st && G.__lgMineS.st.id) || currentMineId || 'home';
        window.X?.api?.('/api/mineSeen', { c: C });
      } catch (e) {}
    }
    if (G.__lgMineS) {
      G.__lgMineS.newsShown = true;
    }
  }

  // Регулярное фоновое закрытие модалки «Пока тебя не было», если она появилась
  setInterval(() => {
    if (G.__mineBotRunning && !isPaused) {
      closeNewsModal();
    }
  }, 1000);

  function checkTipError() {
    const tip = document.querySelector('.mn-tip');
    if (tip && !tip.hidden && tip.textContent) {
      const txt = tip.textContent.toLowerCase();
      if (
        txt.includes('не подойти') ||
        txt.includes('не пройти') ||
        txt.includes('недоступен') ||
        txt.includes('твёрд') ||
        txt.includes('тверд') ||
        txt.includes('обойти') ||
        txt.includes('расчисти') ||
        txt.includes('раскопать') ||
        txt.includes('не разбить') ||
        txt.includes('далеко') ||
        txt.includes('препятств')
      ) {
        return tip.textContent.trim();
      }
    }
    return null;
  }

  // ⏳ Проверка исчерпания суточного лимита копания (10 часов / me.cap, сброс в 05:00 МСК)
  function isDailyMiningCapReached() {
    try {
      const currentDay = getMineGamingDay();

      // 1. Проверяем объект me (наиболее точный и первичный источник данных игры)
      const me = G.__lgMineMe || (G.__lgMineS && G.__lgMineS.me);
      if (me) {
        const cap = Number(me.cap);
        const day = Number(me.day);
        if (cap > 0 && !isNaN(day)) {
          if (day >= cap) {
            cachedDailyMiningCapReached = true;
            cachedDailyMiningCapDay = currentDay;
            try { localStorage.setItem('lg_mining_cap_day', currentDay); } catch (e) {}
            return true;
          } else {
            // Если сервер вернул day < cap (например, новые сутки наступили в 05:00), сбрасываем кэш
            cachedDailyMiningCapReached = false;
            cachedDailyMiningCapDay = '';
            try { localStorage.removeItem('lg_mining_cap_day'); } catch (e) {}
            return false;
          }
        }
      }

      // 2. Проверка всплывающей подсказки (tip) игры
      const tipEl = document.querySelector('.mn-tip');
      if (tipEl && !tipEl.hidden && tipEl.textContent) {
        const tipTxt = tipEl.textContent.toLowerCase();
        if (tipTxt.includes('на сегодня хватит') || tipTxt.includes('новые сутки') || tipTxt.includes('сброс суток')) {
          cachedDailyMiningCapReached = true;
          cachedDailyMiningCapDay = currentDay;
          try { localStorage.setItem('lg_mining_cap_day', currentDay); } catch (e) {}
          return true;
        }
      }

      // 3. Проверка виджета "Сегодня: X из Y" в панели шахты (.mn-wg-day)
      const dayWg = document.querySelector('.mn-wg-day');
      if (dayWg && dayWg.textContent) {
        const txt = dayWg.textContent;
        const barI = dayWg.querySelector('i');
        if (barI && barI.style && barI.style.width) {
          const wNum = parseFloat(barI.style.width);
          if (!isNaN(wNum) && wNum >= 100) {
            cachedDailyMiningCapReached = true;
            cachedDailyMiningCapDay = currentDay;
            try { localStorage.setItem('lg_mining_cap_day', currentDay); } catch (e) {}
            return true;
          }
        }
        const m = txt.match(/(\d+)\s*ч.*?из\s*(\d+)\s*ч/i);
        if (m && m[1] && m[2]) {
          const curH = parseInt(m[1], 10);
          const maxH = parseInt(m[2], 10);
          if (curH >= maxH && maxH > 0) {
            cachedDailyMiningCapReached = true;
            cachedDailyMiningCapDay = currentDay;
            try { localStorage.setItem('lg_mining_cap_day', currentDay); } catch (e) {}
            return true;
          }
        }
      }

      // 4. Проверка сохранённого кэша текущих игровых суток (до 05:00 МСК)
      if (cachedDailyMiningCapReached && cachedDailyMiningCapDay === currentDay) {
        return true;
      }
      const savedCapDay = localStorage.getItem('lg_mining_cap_day');
      if (savedCapDay && savedCapDay === currentDay) {
        cachedDailyMiningCapReached = true;
        cachedDailyMiningCapDay = currentDay;
        return true;
      }
    } catch (e) {}
    return false;
  }

  function isMining() {
    try {
      const st = G.__lgMineState || (G.__lgMineS && G.__lgMineS.st);
      const isScreen = Boolean(document.querySelector('.mn') || G.__lgMineS);

      // 0. Если герой явно анимирован маханием киркой (.mn-heroes .mn-h.me.dig) — мы 100% копаем!
      const digHero = document.querySelector('.mn-heroes .mn-h.me.dig, .mn .mn-heroes .mn-h.me.dig');
      if (digHero) {
        lastMiningConfirmedTime = Date.now();
        return true;
      }

      // 1. Проверяем собственную полоску над камнем (.mn-bar.me)
      const myBar = document.querySelector('.mn-bar.me, .mn .mn-bar.me');
      if (myBar) {
        lastMiningConfirmedTime = Date.now();
        return true;
      }

      // 2. Проверяем карточку активной добычи / плашку внизу (.mn-nowl или .m2-card.m2-now.on)
      const nowlEl = document.querySelector('.mn-nowl');
      if (nowlEl && !nowlEl.hidden) {
        lastMiningConfirmedTime = Date.now();
        return true;
      }
      const activeCard = document.querySelector('.m2-card.m2-now.on, .m2-now.on');
      if (activeCard) {
        lastMiningConfirmedTime = Date.now();
        return true;
      }

      // 3. Проверяем список копателей у камней в st.dg
      if (st && st.dg) {
        const myLogin = (G.ME?.user?.login || G.__lgMineMe?.name || getMyNick() || '').toLowerCase();
        if (myLogin) {
          for (const [idx, list] of Object.entries(st.dg)) {
            if (Array.isArray(list) && list.some(l => String(l).toLowerCase() === myLogin)) {
              if (!st.dug || st.dug[parseInt(idx, 10)] !== '1') {
                lastMiningConfirmedTime = Date.now();
                return true;
              }
            }
          }
        }
      }

      // 4. Живой объект движка игры S.myI
      if (G.__lgMineS && typeof G.__lgMineS.myI === 'number' && G.__lgMineS.myI >= 0) {
        const isWalking = Boolean(G.__lgMineS.path && G.__lgMineS.path.length > 0);
        const isDug = Boolean(st && st.dug && st.dug[G.__lgMineS.myI] === '1');
        if (!isWalking && !isDug && !G.__lgMineS.left) {
          lastMiningConfirmedTime = Date.now();
          return true;
        }
      }

      // 5. Кнопка остановки добычи внутри видимой плашки добычи
      const stopBtn = document.querySelector('.mn-nowl:not([hidden]) [data-mn-stop]');
      if (stopBtn) {
        lastMiningConfirmedTime = Date.now();
        return true;
      }

      // 6. Только для Ghost-режима (когда экрана шахты нет): проверка снимка героя
      if (!isScreen) {
        const myMe = G.__lgMineMe;
        if (myMe && typeof myMe.i === 'number' && myMe.i >= 0) {
          const meFloor = myMe.f || myMe.at;
          const isOldFloorSnap = (st && st.n != null && meFloor != null && meFloor < st.n);
          if (!isOldFloorSnap && (!st || !st.dug || st.dug[myMe.i] !== '1')) {
            lastMiningConfirmedTime = Date.now();
            return true;
          }
        }
        const cId = currentMineId || 'home';
        const heroMn = G.ME?.hero?.mn;
        if (heroMn && heroMn[cId] && typeof heroMn[cId].i === 'number' && heroMn[cId].i >= 0) {
          const mnSnap = heroMn[cId];
          const mnFloor = mnSnap.f || mnSnap.at;
          const isOldFloorSnap = (st && st.n != null && mnFloor != null && mnFloor < st.n);
          if (!isOldFloorSnap && (!st || !st.dug || st.dug[heroMn[cId].i] !== '1')) {
            lastMiningConfirmedTime = Date.now();
            return true;
          }
        }
      }

      // 7. Анти-мерцание (Hysteresis / Grace Period):
      // Если герой копал менее 1.5 секунд назад, и на экране герой НЕ стоит в явном бездействии
      if (lastMiningConfirmedTime > 0 && Date.now() - lastMiningConfirmedTime < 1500) {
        const isHeroVisiblyIdle = isScreen && !digHero && !myBar && (!nowlEl || nowlEl.hidden) && (!G.__lgMineS?.path || G.__lgMineS.path.length === 0);
        if (!isHeroVisiblyIdle) {
          const isTargetDug = activeMiningRockIndex >= 0 && st?.dug && st.dug[activeMiningRockIndex] === '1';
          if (!isTargetDug) {
            return true;
          }
        }
      }
    } catch (e) {}

    return false;
  }
  G.__mineIsMining = isMining;
  G.__mineGetActiveMiningRockIndex = getActiveMiningRockIndex;
  G.__mineIsDailyMiningCapReached = isDailyMiningCapReached;
  G.__mineGetGamingDay = getMineGamingDay;
  G.__mineClickTarget = clickTarget;
  G.__mineIsRatingMode = () => isRatingMode;
  G.__mineSetRatingMode = (val) => {
    isRatingMode = !!val;
    localStorage.setItem('lg_bot_rating_mode', isRatingMode ? '1' : '0');
    if (isRatingMode) {
      oreFirst = false;
      localStorage.setItem('lg_bot_ore_first', '0');
      if (typeof checkOreFirst !== 'undefined' && checkOreFirst) {
        checkOreFirst.checked = false;
      }
      lastTargetKey = null;
    }
  };
  G.__mineGetCandidateHpProgress = getCandidateHpProgress;

  function clickTarget(candidate) {
    if (!candidate) return false;
    // ⏳ Если суточный лимит 10ч исчерпан — блокируем любые клики по камням
    if (isDailyMiningCapReached()) {
      lastTargetKey = null;
      activeMiningRockIndex = -1;
      if (st) {
        st.textContent = '⏳ Лимит 10ч исчерпан (Сброс в 05:00) · Буры и лифт активны';
        st.style.color = '#ffd700';
      }
      return false;
    }
    // 🛡️ Защита: убиваем 30-секундный таймер mineStop в движке игры S.left
    if (G.__lgMineS) {
      G.__lgMineS.left = null;
    }
    let idx = -1;
    if (candidate.rockId != null) {
      const parsed = parseInt(candidate.rockId, 10);
      if (!isNaN(parsed) && parsed >= 0) {
        idx = parsed;
      }
    }
    let group = candidate.group;
    if (!group && idx >= 0) {
      group = document.querySelector(`.mn-world g[data-i="${idx}"], svg.mn-bg g[data-i="${idx}"], g[data-i="${idx}"]`);
    }
    const bar = candidate.bar;

    // ВАЖНО: Никакого scrollIntoView на мобильных! На телефоне он вызывает дергание всего окна («колбасит»).
    if (group) {
      const target = group.querySelector('path') || group;
      const r = target.getBoundingClientRect();
      let cx = r.left + r.width / 2;
      let cy = r.top + r.height / 2;
      // Если в фоне или Ghost-режиме getBoundingClientRect вернул нули, рассчитываем точные координаты по сетке (Шахта 2.0 и 3.0)
      if ((!cx && !cy) && idx >= 0) {
        const isV3 = !!(G.__lgMineS?.F?.v3 || G.__lgMineState?.fl?.v3);
        const m3info = G.__lgMineS?.F || G.__lgMineState?.fl;
        const getXY = (isV3 && G.LGWorldLib?.mine3 && m3info) ? G.LGWorldLib.mine3(m3info).cellXY :
                      ((G.LGCraftWorld && G.LGCraftWorld.cellXY) ||
                      (G.LGWorldLib && G.LGWorldLib.mine && G.LGWorldLib.mine.cellXY));
        if (getXY) {
          const pt = getXY(idx);
          if (pt) {
            cx = pt.x;
            cy = pt.y;
          }
        }
      }
      const opts = { bubbles: true, cancelable: true, view: G, clientX: cx, clientY: cy, pointerId: 1 };
      target.dispatchEvent(new PointerEvent('pointerdown', opts));
      target.dispatchEvent(new PointerEvent('pointerup', opts));
      target.dispatchEvent(new MouseEvent('click', opts));

      const tipErr = checkTipError();
      if (tipErr) {
        if (candidate.rockId != null) {
          blacklist.set(String(candidate.rockId), Date.now() + 30000);
        }
        lastTargetKey = null;
        if (st) {
          st.textContent = 'Путь закрыт, меняем...';
          st.style.color = '#ff9900';
        }
        return false;
      }
      return true;
    }
    if (bar) {
      const r = bar.getBoundingClientRect();
      const x = r.left + r.width / 2;
      const y = r.top + r.height / 2;
      const opts = { bubbles: true, cancelable: true, view: G, clientX: x, clientY: y, pointerId: 1 };
      bar.dispatchEvent(new PointerEvent('pointerdown', opts));
      bar.dispatchEvent(new PointerEvent('pointerup', opts));
      bar.dispatchEvent(new MouseEvent('click', opts));

      const tipErr = checkTipError();
      if (tipErr) {
        if (candidate.rockId != null) {
          blacklist.set(String(candidate.rockId), Date.now() + 30000);
        }
        lastTargetKey = null;
        if (st) {
          st.textContent = 'Путь закрыт, меняем...';
          st.style.color = '#ff9900';
        }
        return false;
      }
      return true;
    }
    return false;
  }

  function getCandidateRocks() {
    const now = Date.now();
    for (const [k, exp] of blacklist.entries()) {
      if (now > exp) blacklist.delete(k);
    }
    const bars = Array.from(document.querySelectorAll('.mn-bar'));
    const candidates = [];
    const allGroups = Array.from(document.querySelectorAll('.mn-world g[data-i], svg.mn-bg g[data-i], g[data-i]'));
    const digCats = Array.from(document.querySelectorAll('.mn-heroes .mn-h.dig'));
    const candidateIndices = new Set();

    // Синхронизация своего камня из движка шахты S.myI
    const liveMyI = (G.__lgMineS && typeof G.__lgMineS.myI === 'number') ? G.__lgMineS.myI : -1;

    for (const bar of bars) {
      const bx = parseFloat(bar.style.left) || 0;
      const by = parseFloat(bar.style.top) || 0;
      const isMe = bar.classList.contains('me');

      // Точный расчет индекса клетки по сетке шахты (без искажений SVG bbox)
      let iNum = getCellIndexFromBarPosition(bx, by);
      if (isMe && liveMyI >= 0) {
        iNum = liveMyI;
      }
      if (iNum >= 0) {
        candidateIndices.add(iNum);
      }

      // Поиск SVG группы по точному data-i (Шахта 2.0 и Шахта 3.0)
      let matchedG = (iNum >= 0) ? document.querySelector(`.mn-world g[data-i="${iNum}"], svg.mn-bg g[data-i="${iNum}"], g[data-i="${iNum}"]`) : null;
      if (!matchedG) {
        matchedG = allGroups.find(g => g.dataset.i === String(iNum)) || null;
      }

      const rockId = (iNum >= 0) ? String(iNum) : (matchedG ? matchedG.dataset.i : `${bx}_${by}`);

      // 100% точный подсчёт игроков:
      // 1) Из серверного состояния st.bl (если перехвачено в памяти)
      let count = 0;
      if (G.__lgMineState && G.__lgMineState.bl && iNum >= 0 && G.__lgMineState.bl[iNum]) {
        count = G.__lgMineState.bl[iNum][3] || 0;
      } else {
        // 2) Из DOM: игра выводит плашку +X только если игроков больше 4 (+1 значит 4 кота на экране + 1 = 5 чел).
        // Если плашки нет, но есть полоска .mn-bar, считаем видимых котиков-копателей вокруг камня.
        const bTag = bar.querySelector('b');
        if (bTag) {
          const extra = parseInt(bTag.textContent.replace('+', ''), 10) || 0;
          count = 4 + extra;
        } else {
          let nearbyCats = 0;
          for (const cat of digCats) {
            const m = cat.style.transform.match(/translate\(([\d.-]+)px,\s*([\d.-]+)px\)/);
            if (m) {
              const hx = parseFloat(m[1]), hy = parseFloat(m[2]);
              if (Math.hypot(hx - (bx + 44), hy - (by + 70)) < 110) {
                nearbyCats++;
              }
            }
          }
          count = Math.max(1, nearbyCats);
        }
      }

      const isFar = matchedG ? matchedG.classList.contains('far') : false;

      // isMe подтверждается ТОЛЬКО реальными доказательствами (класс me, liveMyI или st.dg)
      if (!isMe && liveMyI >= 0 && iNum === liveMyI) {
        isMe = true;
      }
      if (!isMe && iNum >= 0 && G.__lgMineState?.dg?.[iNum]) {
        const myLogin = (G.ME?.user?.login || G.__lgMineMe?.name || getMyNick() || '').toLowerCase();
        if (myLogin && Array.isArray(G.__lgMineState.dg[iNum]) && G.__lgMineState.dg[iNum].some(l => String(l).toLowerCase() === myLogin)) {
          isMe = true;
        }
      }

      candidates.push({ bar, group: matchedG, rockId, count, isFar, isMe, inBlacklist: blacklist.has(rockId) });
    }

    // 2. Добавляем доступные камни первой линии напрямую из серверной памяти (st.front / S.st.front):
    // Это исключает любую зависимость от задержек отрисовки SVG в фоне или Ghost-режиме!
    const frontArray = (G.__lgMineState && G.__lgMineState.front) ||
                       (G.__lgMineS && G.__lgMineS.st && G.__lgMineS.st.front) ||
                       [];
    const frontSet = new Set(frontArray);

    // Сначала проходим по фронту из памяти (100% гарантия обнаружения руды даже при задержках SVG в фоне):
    for (const iNum of frontSet) {
      if (isNaN(iNum) || candidateIndices.has(iNum)) continue;
      const rockId = String(iNum);
      if (blacklist.has(rockId)) continue;
      if (!isRockPathReachable(iNum)) continue; // 🛡️ Исключаем физически недостижимые камни через сталагмиты/стены

      let matchedG = document.querySelector(`svg.mn-bg g[data-i="${iNum}"]`) || allGroups.find(g => g.dataset.i === rockId) || null;
      const isOre = isValuableRock(iNum, matchedG);
      if ((oreFirst && isOre) || candidates.length === 0) {
        candidateIndices.add(iNum);
        candidates.push({
          bar: null,
          group: matchedG,
          rockId,
          count: 0,
          isFar: false,
          isMe: false,
          inBlacklist: false
        });
      }
    }

    // Резервная проверка SVG групп на случай, если память st.front еще не заполнилась
    if (allGroups.length > 0) {
      for (const g of allGroups) {
        const iNum = +g.dataset.i;
        if (isNaN(iNum) || candidateIndices.has(iNum)) continue;
        const rockId = String(iNum);
        if (blacklist.has(rockId)) continue;
        if (g.classList.contains('far')) continue;
        if (!isRockPathReachable(iNum)) continue; // 🛡️ Исключаем физически недостижимые камни через сталагмиты/стены

        const isOpen = g.classList.contains('open') || (frontSet.size > 0 ? frontSet.has(iNum) : true);
        if (!isOpen) continue;

        const isOre = isValuableRock(iNum, g);
        if ((oreFirst && isOre) || candidates.length === 0) {
          candidateIndices.add(iNum);
          candidates.push({
            bar: null,
            group: g,
            rockId,
            count: 0,
            isFar: false,
            isMe: false,
            inBlacklist: false
          });
        }
      }
    }

    return candidates;
  }

  // Режим экрана шахты
  function stepScreen() {
    // 0. Своевременно закрываем всплывашку «Пока тебя не было»
    closeNewsModal();

    if (isRidingLift || pendingLiftRide) return;

    // ⏳ Проверка исчерпания суточного лимита копания (10 часов / me.cap, сброс в 05:00 МСК)
    if (isDailyMiningCapReached()) {
      if (isMining()) {
        const oreName = getCurrentMiningRockName();
        const oreLabel = oreName ? `: ${oreName}` : '';
        if (st) {
          st.textContent = `⏳ Лимит 10ч исчерпан · Докапываем${oreLabel}...`;
          st.style.color = '#ffd700';
        }
      } else {
        lastTargetKey = null;
        activeMiningRockIndex = -1;
        if (pMineWrap) pMineWrap.style.display = 'none';
        if (st) {
          st.textContent = '⏳ Лимит 10ч исчерпан (Сброс в 05:00) · Буры и лифт активны';
          st.style.color = '#ffd700';
        }
      }
      return;
    }

    // 🛡️ Сторожевой таймер (Watchdog) против зависания героя:
    // Если на экране герой физически не машет киркой (.dig), нет полоски (.mn-bar.me),
    // нет плашки (.mn-nowl), и герой не идёт (!S.path)
    const digEl = document.querySelector('.mn-heroes .mn-h.me.dig, .mn .mn-heroes .mn-h.me.dig');
    const myBarEl = document.querySelector('.mn-bar.me, .mn .mn-bar.me');
    const nowlEl = document.querySelector('.mn-nowl:not([hidden])');
    const isWalking = Boolean(G.__lgMineS?.path && G.__lgMineS.path.length > 0);
    const hasLiveDigProof = Boolean(digEl || myBarEl || nowlEl);

    if (!hasLiveDigProof && !isWalking) {
      if (!idleHeroDetectedTime) {
        idleHeroDetectedTime = Date.now();
      } else if (Date.now() - idleHeroDetectedTime > 2000) {
        // Герой стоит без дела уже более 2 секунд — блокируем зависшую цель в черный список и сбрасываем состояние
        if (lastTargetKey) {
          blacklist.set(lastTargetKey, Date.now() + 30000);
        }
        activeMiningRockIndex = -1;
        lastTargetKey = null;
        lastMiningConfirmedTime = 0;
        lastKnownMiningProgress = null;
        if (G.__lgMineS && G.__lgMineS.myI >= 0) {
          G.__lgMineS.myI = -1;
        }
        idleHeroDetectedTime = 0;
      }
    } else {
      idleHeroDetectedTime = 0;
    }

    if (G.__lgMineS) {
      if (G.__lgMineS.left && (typeof isMining === 'function' && isMining())) {
        G.__lgMineS.left = null;
      }
    }

    // Проверяем, не вывела ли игра сообщение об ошибке открытия шахты
    const errPanel = document.querySelector('.mn-panel .empty, .mn-err');
    if (errPanel && errPanel.textContent && errPanel.textContent.includes('не открылась')) {
      const errTxt = errPanel.textContent.trim();
      st.textContent = `[${getMineLabel(currentMineId)}] ${errTxt}`;
      st.style.color = '#ff6666';

      const retryBtn = document.querySelector('[data-mn-retry]');
      if (retryBtn && Date.now() - lastMineOpenAttempt > 5000) {
        lastMineOpenAttempt = Date.now();
        try { retryBtn.click(); } catch (e) {}
      }
      return;
    }

    // Проверка на ошибку сервера "mine_closed"
    const closedEl = document.querySelector('.mn-board, .mn-panel, [role="dialog"], body');
    const isClosedText = (closedEl && closedEl.textContent && closedEl.textContent.includes('mine_closed')) ||
                         (document.body && document.body.textContent && document.body.textContent.includes('mine_closed'));
    if (G.__lgMineError === 'mine_closed' || isClosedText) {
      st.textContent = `❌ ${getMineLabel(currentMineId)} закрыта сервером`;
      st.style.color = '#ff4444';
      return;
    }

    if (!isTargetMineOpen(currentMineId)) {
      if (Date.now() - lastMineOpenAttempt > 4000) {
        lastMineOpenAttempt = Date.now();
        const ok = tryOpenMineOnScreen(currentMineId);
        if (ok) {
          st.textContent = `Входим в ${getMineLabel(currentMineId)}...`;
          st.style.color = '#ffd700';
        }
      }
      return;
    }

    closeNewsModal();

    // Проверка зависшего пути ходьбы в фоне (Watchdog)
    if (G.__lgMineS && G.__lgMineS.path && G.__lgMineS.path.length && G.__lgMineS.then) {
      resolveBackgroundWalking();
    }

    const mining = isMining();
    if (!mining) {
      if (Date.now() - lastMiningConfirmedTime > 2500) {
        if (activeMiningRockIndex >= 0) {
          // Мы только что завершили копать камень — запускаем обновление руды через 500 мс
          scheduleOreRefreshAfterDig(500);
        }
        activeMiningRockIndex = -1;
        lastKnownMiningProgress = null;
        if (pMineWrap) pMineWrap.style.display = 'none';
      }
    }
    const candidates = getCandidateRocks();

    // Если НЕ копаем и шли к камню, проверяем сообщения о недоступности пути
    if (!mining && lastTargetKey) {
      const tipErr = checkTipError();
      if (tipErr) {
        blacklist.set(lastTargetKey, Date.now() + 30000);
        lastTargetKey = null;
        st.textContent = 'Путь закрыт, меняем...';
        st.style.color = '#ff9900';
        return;
      }
    }

    let myCount = 0;
    let myCandidate = null;
    if (mining) {
      for (const c of candidates) {
        if (c.isMe) {
          myCount = c.count;
          myCandidate = c;
          if (c.rockId != null) {
            const idx = parseInt(c.rockId, 10);
            if (!isNaN(idx) && idx >= 0) {
              activeMiningRockIndex = idx;
            }
          }
        }
      }

      // Синхронизация активного камня из getActiveMiningRockIndex()
      const liveIdx = getActiveMiningRockIndex();
      if (liveIdx >= 0) {
        activeMiningRockIndex = liveIdx;
      }
      if (activeMiningRockIndex >= 0 && G.__lgMineState?.bl?.[activeMiningRockIndex]) {
        const blk = G.__lgMineState.bl[activeMiningRockIndex];
        if (blk && blk[3]) myCount = blk[3];
      }
    } else {
      if (Date.now() - lastMiningConfirmedTime > 2500) {
        activeMiningRockIndex = -1;
        lastKnownMiningProgress = null;
      }
    }

    const valid = candidates.filter(c => !c.isFar && !c.inBlacklist && (c.isMe || isRockPathReachable(c.rockId)));

    // Функция стабильной сортировки кандидатов с гистерезисом (предотвращает метания между одинаковыми камнями)
    const sortCandidates = (arr) => {
      arr.sort((a, b) => {
        // 1. По числу людей (больше толпа — выше приоритет)
        if (b.count !== a.count) return b.count - a.count;
        // 2. Стабильность (гистерезис): отдаём абсолютное предпочтение камню, который мы уже копаем или к которому идём
        const aIsCur = a.isMe || a.rockId === String(activeMiningRockIndex) || a.rockId === lastTargetKey;
        const bIsCur = b.isMe || b.rockId === String(activeMiningRockIndex) || b.rockId === lastTargetKey;
        if (aIsCur && !bIsCur) return -1;
        if (!aIsCur && bIsCur) return 1;
        // 3. Детерминированный порядок по номеру клетки (чтобы массив не прыгал при перерисовках)
        return (+a.rockId || 0) - (+b.rockId || 0);
      });
    };

    // 🏆 Сортировка для режима «Рейтинг»:
    // Главная цель — максимум набранных очков и разрушенных камней/руд в час (+1 в рейтинг).
    // 1) Соответствие выбранному диапазону игроков (например 2-5 чел)
    // 2) Скорость разрушения (наименьшее ETA / наименьший % HP добивания): чем быстрее блок ломается, тем выше приоритет!
    // 3) Близость к игроку (расстояние): меньше времени на ходьбу — быстрее добыча!
    const sortRatingCandidates = (arr) => {
      arr.sort((a, b) => {
        // 1. Стабильность (гистерезис): удерживаем начатый камень или цель, к которой уже идём
        const aIsCur = a.isMe || a.rockId === String(activeMiningRockIndex) || a.rockId === lastTargetKey;
        const bIsCur = b.isMe || b.rockId === String(activeMiningRockIndex) || b.rockId === lastTargetKey;
        if (aIsCur && !bIsCur) return -1;
        if (!aIsCur && bIsCur) return 1;

        // 2. Соответствие выбранному диапазону игроков (приоритет целям с нужным числом копателей):
        const countA = a.count || 0;
        const countB = b.count || 0;
        const [minC, maxC] = parseRatingCrowdRange(ratingCrowdRange);
        const inRangeA = countA >= minC && (maxC <= 0 || countA <= maxC);
        const inRangeB = countB >= minC && (maxC <= 0 || countB <= maxC);
        if (inRangeA && !inRangeB) return -1;
        if (!inRangeA && inRangeB) return 1;

        // 3. Быстро разрушаемые породы (Обычный камень/базальт/уголь/железо быстрее редких жил золота):
        const catA = getRockCategory(a.rockId, a.group);
        const catB = getRockCategory(b.rockId, b.group);
        const isRareA = (catA === 'rare' || catA === 'chest' || catA === 'gold');
        const isRareB = (catB === 'rare' || catB === 'chest' || catB === 'gold');
        if (!isRareA && isRareB) return -1;
        if (isRareA && !isRareB) return 1;

        // 4. ГЛАВНЫЙ КРИТЕРИЙ: Скорость добивания (наименьшее время ETA или наименьший % HP):
        const progA = getCandidateHpProgress(a);
        const progB = getCandidateHpProgress(b);

        // Если есть расчёт времени в секундах у обоих:
        if (progA.etaSec > 0 && progB.etaSec > 0) {
          if (Math.abs(progA.etaSec - progB.etaSec) >= 10) {
            return progA.etaSec - progB.etaSec; // Меньше секунд до разрушения -> ПЕРВЫМ!
          }
        }

        // Если время не рассчитано или одинаково — сортируем по оставшемуся % HP (добивание):
        if (Math.abs(progA.remPct - progB.remPct) >= 5) {
          return progA.remPct - progB.remPct; // Меньше % HP -> быстрее сломается!
        }

        // 5. Близость к игроку (расстояние): меньше времени на ходьбу
        const distA = getRockDistance(a.rockId);
        const distB = getRockDistance(b.rockId);
        if (distA >= 0 && distB >= 0 && Math.abs(distA - distB) > 50) {
          return distA - distB;
        }

        // 6. Детерминированный порядок по номеру клетки
        return (+a.rockId || 0) - (+b.rockId || 0);
      });
    };

    let best = null;
    if (isRatingMode) {
      const [minCrowd, maxCrowd] = parseRatingCrowdRange(ratingCrowdRange);

      // 🏆 В режиме «Рейтинг» фильтруем кандидатов:
      // 1) Исключаем умирающие камни (< 25% HP при наличии людей), чтобы не бежать напрасно
      // 2) ФИЛЬТР ПО КОЛИЧЕСТВУ ИГРОКОВ: отбираем только камни и руды с нужным числом копателей (например 2-5 чел),
      // полностью исключая гигантские толпы по 50-70+ человек и пустые камни, если настроен лимит!
      const safeRatingCandidates = valid.filter(c => {
        if (c.isMe || c.rockId === String(activeMiningRockIndex) || c.rockId === lastTargetKey) return true;
        const hp = getCandidateHpProgress(c);
        if (c.count >= 2 && hp.remPct < 25) return false;
        const count = c.count || 0;
        if (count < minCrowd) return false;
        if (maxCrowd > 0 && count > maxCrowd) return false;
        return true;
      });

      // Если в заданном диапазоне игроков (например 2-5) есть кандидаты — берем их!
      // Если ни одного такого камня нет на экране, берем любые камни, но строго исключаем экстремальные зерги (> 20 чел)
      let ratingPool = safeRatingCandidates;
      if (ratingPool.length === 0) {
        ratingPool = valid.filter(c => {
          if (c.isMe || c.rockId === String(activeMiningRockIndex) || c.rockId === lastTargetKey) return true;
          const hp = getCandidateHpProgress(c);
          if (c.count >= 2 && hp.remPct < 25) return false;
          if (c.count > 20) return false;
          return true;
        });
        if (ratingPool.length === 0) ratingPool = valid;
      }

      sortRatingCandidates(ratingPool);
      best = ratingPool[0];
    } else if (oreFirst) {
      // 1. Исключаем кандидатов, чьи категории выключены пользователем
      const activeCandidates = valid.filter(c => {
        const cat = getRockCategory(c.rockId, c.group);
        const item = customPriorities.find(p => p.id === cat);
        return !item || item.enabled;
      });

      // 2. Раскладываем кандидатов по корзинам приоритетов
      const buckets = new Map();
      for (const p of customPriorities) {
        if (p.enabled) buckets.set(p.id, []);
      }

      for (const c of activeCandidates) {
        const cat = getRockCategory(c.rockId, c.group);
        if (buckets.has(cat)) {
          buckets.get(cat).push(c);
        } else if (buckets.has('other_ore')) {
          buckets.get('other_ore').push(c);
        } else if (buckets.has('rock')) {
          buckets.get('rock').push(c);
        }
      }

      // 3. Выбираем лучшего кандидата по строгому порядку списка приоритетов (сверху вниз)
      for (const p of customPriorities) {
        if (!p.enabled) continue;
        const groupList = buckets.get(p.id);
        if (groupList && groupList.length > 0) {
          sortCandidates(groupList);
          best = groupList[0];
          break;
        }
      }
    } else {
      sortCandidates(valid);
      best = valid[0];
    }

    // Защита от смены цели во время ходьбы: если уже идём к камню (< 7.5 сек), цель не разбита и доступна,
    // удерживаем её, если только на линии не открылась руда более высокого приоритета!
    if (!mining && lastTargetKey) {
      const walkElapsed = Date.now() - targetAssignedTime;
      const isTargetDug = G.__lgMineState?.dug && G.__lgMineState.dug[parseInt(lastTargetKey, 10)] === '1';
      if (!isTargetDug && !blacklist.has(lastTargetKey) && walkElapsed < 7500) {
        const lockedCand = valid.find(c => c.rockId === lastTargetKey);
        if (lockedCand) {
          if (isRatingMode) {
            const lockedCat = getRockCategory(lockedCand.rockId, lockedCand.group);
            const isPureLocked = (lockedCat === 'rock' && !isValuableRock(lockedCand.rockId, lockedCand.group));
            if (isPureLocked) {
              best = lockedCand;
            }
          } else {
            const lockedTier = getRockPriorityTier(lockedCand.rockId, lockedCand.group);
            const bestTier = best ? getRockPriorityTier(best.rockId, best.group) : 0;
            if (bestTier <= lockedTier) {
              best = lockedCand;
            }
          }
        }
      }
    }

    const currentTier = getRockPriorityTier(activeMiningRockIndex, myCandidate?.group);
    const bestTier = best ? getRockPriorityTier(best.rockId, best.group) : 0;
    const currentIsOre = currentTier > 0;

    // Защита от метаний туда-сюда:
    const switchElapsed = Date.now() - lastRockSwitchTime;
    const currentProg = (mining && activeMiningRockIndex >= 0) ? getRockMiningProgress(activeMiningRockIndex, myCandidate) : null;
    const currentPct = currentProg ? currentProg.pct : 0;

    const bestIsOre = bestTier > 0;
    const effectiveMyCount = Math.max(1, myCount);
    // 🛡️ Порог толпы: минимум в 1.5 раза больше игроков, чем на текущей цели (и минимум на 1 игрока больше)
    const crowdThreshold = Math.max(effectiveMyCount + 1, Math.ceil(effectiveMyCount * 1.5));
    const has15xCrowd = best && (best.count >= crowdThreshold);

    // 1) Переключение на руду более высокого приоритета (по списку customPriorities):
    // Разрешено, если текущий камень ещё не добит и прошел кулдаун переключения.
    // Если лучшая цель — сундук (и его приоритет выше текущего камня по настройкам игрока),
    // порог отказа от текущего камня увеличен до 80%, а кулдаун сокращен до 2.5 сек, чтобы не упустить клад!
    const bestCat = best ? getRockCategory(best.rockId, best.group) : '';
    const isBestChest = bestCat === 'chest';
    const maxSwitchPct = (isBestChest && bestTier > currentTier) ? 80 : 70;
    const minSwitchElapsed = (isBestChest && bestTier > currentTier) ? 2500 : 4000;
    const shouldSwitchToHigherOre = oreFirst && mining && (bestTier > currentTier) && currentPct < maxSwitchPct && switchElapsed > minSwitchElapsed;

    // 2) Переключение на руду с толпой (в 1.5 раза больше игроков):
    // Разрешено, если цель — руда, её ранг не ниже текущего (bestTier >= currentTier) или мы копали камень (!currentIsOre),
    // на ней минимум в 1.5 раза больше копателей, текущий блок не добит (< 70%) и прошел кулдаун (> 4 сек)
    const shouldSwitchToOreCrowd = oreFirst && mining && bestIsOre && (bestTier >= currentTier || !currentIsOre) && has15xCrowd && currentPct < 70 && switchElapsed > 4000;

    // 3) Переключение между обычными камнями по толпе:
    const canSwitchToRockCrowd = mining && !currentIsOre && !bestIsOre && has15xCrowd && currentPct <= 35 && switchElapsed > 6000;

    // 4) Чистый режим толпы (если выключен Приоритет руды):
    const shouldSwitchPureCrowd = !oreFirst && mining && has15xCrowd && currentPct < 70 && switchElapsed > 4000;

    // Защита: мы никогда не переключаемся на толпу/руду, если цель - это ТОТ ЖЕ САМЫЙ камень, который мы уже копаем!
    const isTargetSameAsCurrent = (activeMiningRockIndex >= 0 && best && best.rockId === String(activeMiningRockIndex));
    let shouldSwitchToCrowd = mining && best && !best.isMe && !isTargetSameAsCurrent &&
      (shouldSwitchToHigherOre || shouldSwitchToOreCrowd || canSwitchToRockCrowd || shouldSwitchPureCrowd);

    if (isRatingMode && mining) {
      // 🏆 В режиме «Рейтинг» действует ЖЕЛЕЗНОЕ ПРАВИЛО:
      // НИ В КОЕМ СЛУЧАЕ НЕ БРОСАТЬ НАЧАТЫЙ КАМЕНЬ ДО ЕГО ПОЛНОГО РАЗРУШЕНИЯ!
      // Любое переключение mid-dig обнуляет весь нанесенный урон и очко в топ!
      shouldSwitchToCrowd = false;
    }

    const prefix = mode === 'ghost' ? '🏙️ ' : '';
    if (mining && !shouldSwitchToCrowd) {
      lastTargetKey = (activeMiningRockIndex >= 0) ? String(activeMiningRockIndex) : null;
      if (Date.now() - lastMineStatePoll > 8000) {
        refreshMineStateFromServer();
      }
      const oreName = getCurrentMiningRockName(myCandidate);
      const oreLabel = oreName ? `: ${oreName}` : '';
      const prog = getRockMiningProgress(activeMiningRockIndex, myCandidate);
      const progLabel = prog ? ` · ост. ${prog.remPct}% (${prog.etaText})` : '';

      if (prog && pMineWrap && pMinePct && pMineTime && pMineBar) {
        pMineWrap.style.display = 'block';
        pMinePct.textContent = `${prog.remPct}%`;
        pMineTime.textContent = prog.etaText;
        pMineBar.style.width = `${prog.remPct}%`;
        const hpC = prog.remPct > 50
          ? (prog.remPct > 75 ? '#5fd35f' : 'linear-gradient(90deg, #ffd23f, #5fd35f)')
          : (prog.remPct > 20 ? 'linear-gradient(90deg, #ff903e, #ffd23f)' : '#ff4d3d');
        pMineBar.style.background = hpC;
        pMinePct.style.color = prog.remPct > 50 ? '#5fd35f' : (prog.remPct > 20 ? '#ffd23f' : '#ff4d3d');
      } else if (pMineWrap) {
        pMineWrap.style.display = 'none';
      }

      if (isRatingMode) {
        st.textContent = `${prefix}🏆 [Рейтинг] Добиваем${oreLabel} (${myCount || 1} чел)${progLabel} ⛏️`;
        st.style.color = '#ffd700';
      } else {
        st.textContent = `${prefix}Копаем${oreLabel} (${myCount || 1} чел)${progLabel} ⛏️`;
        st.style.color = '#00ff66';
      }
    } else {
      if (pMineWrap) pMineWrap.style.display = 'none';
      if (best) {
        const targetKey = best.rockId;
        // 🛡️ Защита от спама кликами: если мы УЖЕ копаем этот целевой камень (или стоим на нём) — НЕ кликаем повторно!
        if (mining && activeMiningRockIndex >= 0 && targetKey === String(activeMiningRockIndex)) {
          return;
        }
        const targetOreName = formatRockName(targetKey);
        const targetOreLabel = targetOreName ? `: ${targetOreName}` : '';
        if (shouldSwitchToCrowd) {
          lastRockSwitchTime = Date.now();
          const switchReason = shouldSwitchToHigherOre
            ? `Найдена руда${targetOreLabel}`
            : (shouldSwitchToOreCrowd ? `Толпа на руде${targetOreLabel}` : `Рывок на толпу${targetOreLabel}`);
          st.textContent = `${prefix}${switchReason} (${best.count} чел)!`;
          st.style.color = '#ffd700';
          // В Mine 2.0 прямой клик по новому камню сам перенаправляет героя без stopMiningInGame()
        }

        let shouldClick = true;
        if (lastTargetKey === targetKey) {
          const elapsed = Date.now() - targetAssignedTime;
          const tipErr = checkTipError();
          if (tipErr) {
            blacklist.set(targetKey, Date.now() + 30000);
            lastTargetKey = null;
            st.textContent = 'Путь закрыт, меняем...';
            st.style.color = '#ff9900';
            return;
          }
          if (elapsed > 25000) {
            // Реальный таймаут (25 сек) — если за 25 сек так и не начали копать, временно в черный список
            blacklist.set(targetKey, Date.now() + 30000);
            lastTargetKey = null;
            st.textContent = 'Таймаут пути, меняем...';
            st.style.color = '#ff9900';
            return;
          }
          // Если прошло больше 6 секунд, а персонаж всё ещё не начал копать (клик сорвался или сетевой лаг) — повторяем клик!
          if (elapsed > 6000) {
            shouldClick = true;
            targetAssignedTime = Date.now() - 2500;
          } else {
            // Персонаж идёт к камню (< 6 сек), даём спокойно дойти без спама кликами
            shouldClick = false;
          }
        } else {
          lastTargetKey = targetKey;
          targetAssignedTime = Date.now();
        }

        // 100% защита от случайного повторного клика по камню, который мы уже копаем
        if (mining && (best.isMe || (activeMiningRockIndex >= 0 && targetKey === String(activeMiningRockIndex)))) {
          shouldClick = false;
        }

        if (!shouldSwitchToCrowd) {
          if (isRatingMode) {
            const candHp = getCandidateHpProgress(best);
            const hpLabel = candHp.remPct < 100 ? `, ост. ${candHp.remPct}%` : '';
            st.textContent = `${prefix}🏆 [Рейтинг] Идем${targetOreLabel} (${best.count} чел${hpLabel})...`;
            st.style.color = '#ffd700';
          } else {
            st.textContent = `${prefix}Идем${targetOreLabel} (${best.count} чел)...`;
            st.style.color = '#66ccff';
          }
        }
        if (shouldClick) {
          clickTarget(best);
        }
      } else {
        const S = G.__lgMineS;
        const currentFloor = (S && S.st && S.st.n) || 1;
        const deep = (S && S.st && (S.st.deep || S.st.n)) || currentFloor;
        if (deep > currentFloor && autoLiftBestFloor && !isRidingLift) {
          st.textContent = `🛗 Этаж ${currentFloor} выкопан! Спускаемся на этаж ${deep}...`;
          st.style.color = '#38bdf8';
          executeLiftRide(deep, 1, 100);
          return;
        }
        st.textContent = `${prefix}Ищем свободный камень...`;
        st.style.color = '#ffd700';
      }
    }
  }



  function getMyLoginsSet() {
    const set = new Set();
    const addCandidate = (val) => {
      if (!val) return;
      if (typeof val === 'string') {
        const trimmed = val.replace(/^@/, '').trim().toLowerCase();
        if (trimmed) set.add(trimmed);
      }
    };

    // 1. Из нативных контекстов движка игры (LGMine / LGCity / LGAuc / X.login())
    const ctxs = [G.__lgMineContext, G.__lgCityContext, G.__lgAucContext, G.__lgQuestContext, window.X];
    for (const ctx of ctxs) {
      if (ctx && typeof ctx.login === 'function') {
        try { addCandidate(ctx.login()); } catch (e) {}
      }
    }

    // 2. Из DOM профиля игрока в правом верхнем углу сайта
    const whoEl = document.querySelector('#whob, .who, [title*="профиль"], [title*="Твой профиль"]');
    if (whoEl && whoEl.textContent) {
      addCandidate(whoEl.textContent);
    }

    // 3. Из глобальных объектов состояния
    addCandidate(G.ME?.user?.login);
    addCandidate(G.ME?.user?.name);
    addCandidate(G.ME?.hero?.n);
    addCandidate(G.ME?.hero?.name);
    addCandidate(G.__lgMineMe?.login);
    addCandidate(G.__lgMineMe?.n);
    addCandidate(G.__lgMineMe?.name);
    addCandidate(G.__lgMineMe?.user?.name);
    addCandidate(G.__lgHero?.name);
    addCandidate(G.__lgHero?.n);

    // 4. Через getMyNick()
    if (typeof getMyNick === 'function') {
      addCandidate(getMyNick());
    }

    return set;
  }

  function isMyFundedDrill(d, loginsSet) {
    if (!d || !Array.isArray(d)) return null;
    const drillKey = String(d[0]);
    if (fundedDrills.has(drillKey)) {
      return { coins: 100, key: drillKey, res: d[11], lv: d[4], stg: d[3] };
    }
    const byList = Array.isArray(d[12]) ? d[12] : [];
    for (const entry of byList) {
      if (Array.isArray(entry) && entry[0]) {
        const l = String(entry[0]).replace(/^@/, '').trim().toLowerCase();
        if (loginsSet && loginsSet.has(l)) {
          return { coins: +entry[1] || 100, key: drillKey, res: d[11], lv: d[4], stg: d[3] };
        }
        if (loginsSet && Array.from(loginsSet).some(my => my === l || (my.length >= 4 && l.includes(my)) || (l.length >= 4 && my.includes(l)))) {
          return { coins: +entry[1] || 100, key: drillKey, res: d[11], lv: d[4], stg: d[3] };
        }
      }
    }
    return null;
  }

  async function scanMyDrillsServerWide(forceScanOnly = false) {
    if (isScanningDrills) return;
    const st = (G.__lgMineS && G.__lgMineS.st) || G.__lgMineState;
    if (!st) {
      showToast('⚠️ Шахта еще не загружена');
      return;
    }

    let logins = getMyLoginsSet();
    if (logins.size === 0) {
      try {
        const meRes = await gameApi('/api/me');
        if (meRes) {
          G.ME = meRes;
          if (meRes.user?.login) logins.add(String(meRes.user.login).replace(/^@/, '').trim().toLowerCase());
          if (meRes.user?.name) logins.add(String(meRes.user.name).replace(/^@/, '').trim().toLowerCase());
          if (meRes.hero?.n) logins.add(String(meRes.hero.n).replace(/^@/, '').trim().toLowerCase());
        }
      } catch (e) {}
    }

    const currentFloor = st.n || 1;
    const deep = st.deep || currentFloor;
    const baseId = (currentMineId || 'home').split('.')[0];

    isScanningDrills = true;
    lastDrillsScanTime = Date.now();

    try {
      let myCount = 0;
      let myCoins = 0;
      const drillKeysFound = new Set();

      for (let f = 1; f <= deep; f++) {
        let drillsForFloor = [];
        if (f === currentFloor) {
          drillsForFloor = (st && Array.isArray(st.drills)) ? st.drills : [];
        } else {
          const roomKey = f >= deep ? baseId : `${baseId}.${f}`;
          try {
            const res = await gameApi(`/api/mine/state?c=${encodeURIComponent(roomKey)}`);
            if (res && res.ok && res.st && Array.isArray(res.st.drills)) {
              drillsForFloor = res.st.drills;
            }
          } catch (e) {}
          await new Promise(r => setTimeout(r, 200));
        }

        for (const d of drillsForFloor) {
          const myInfo = isMyFundedDrill(d, logins);
          if (myInfo && !drillKeysFound.has(myInfo.key)) {
            drillKeysFound.add(myInfo.key);
            myCount++;
            myCoins += myInfo.coins;
            fundedDrills.add(String(myInfo.key));
          } else if (!isPaused && !forceScanOnly && isDrillBuildAllFloors && !myInfo && Array.isArray(d)) {
            // Взнос в буровые на других этажах через API — СТРОГО ТОЛЬКО КОГДА ПЕРСОНАЖ В ШАХТЕ!
            if (!isPlayerInMine()) continue; // СТЕЛС: Из Города или в Ghost-режиме буровые не трогаем!
            const [k, , , stg, lv, c, need, , , , , res] = d;
            const drillKey = String(k);
            if ((stg === 'site' || stg === 'work') && (+lv || 0) < 3 && !fundedDrills.has(drillKey)) {
              const left = Math.max(0, (+need || 0) - (+c || 0));
              if (left > 0) {
                let currentWalletCoins = getMyCoins();
                const fundAmount = Math.min(100, left);
                // Оставляем обязательный защитный резерв 200 монет в кошельке игрока
                if (currentWalletCoins - fundAmount >= 200) {
                  try {
                    const resDrill = await gameApi('/api/mineDrill', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        c: baseId,
                        k: drillKey,
                        coins: fundAmount
                      })
                    });
                    if (resDrill && resDrill.ok) {
                      fundedDrills.add(drillKey);
                      drillKeysFound.add(drillKey);
                      myCount++;
                      myCoins += fundAmount;
                      myFundedDrillsCount++;
                      myFundedDrillsTotalCoins += fundAmount;
                      saveFundedDrillsState();
                      showToast(`🌐 Автобур: этаж ${f} — взнос ${fundAmount}м в буровую (${res || 'руда'})!`);
                      updateDrillsUI();
                      refreshMyCoinsFromApi(true);
                      await new Promise(r => setTimeout(r, 1200)); // Пауза для естественности
                    } else {
                      console.warn(`[Буры] Взнос на этаже ${f} отклонен:`, resDrill && resDrill.error);
                    }
                  } catch (e) {
                    console.warn(`[Буры] Ошибка отправки взноса на этаже ${f}:`, e);
                  }
                }
              }
            }
          }
        }
      }

      myFundedDrillsCount = Math.max(myFundedDrillsCount, myCount);
      myFundedDrillsTotalCoins = Math.max(myFundedDrillsTotalCoins, myCoins);
      saveFundedDrillsState();
      updateDrillsUI();
      showToast(`🏗️ Найдено буровых с вашим взносом: ${myFundedDrillsCount} шт. (${myFundedDrillsTotalCoins} монет)`);
    } catch (err) {
      console.warn('[Шахта] Ошибка сканирования буров:', err);
    } finally {
      isScanningDrills = false;
    }
  }

  // ==========================================
  // 🛗 Шахта 3.0: Авто-лифт на самый людный этаж (+30%)
  // ==========================================
  async function checkAndSelectBestFloor() {
    if (isPaused || !autoLiftBestFloor || isCheckingLiftFloors || isRidingLift) return;
    const S = G.__lgMineS;
    if (!S || !S.st) return;

    const currentFloor = S.st.n || 1;
    const deep = S.st.deep || currentFloor;
    if (deep <= 1) return; // Только 1 этаж открыт, переезжать некуда

    isCheckingLiftFloors = true;
    lastLiftCheckTime = Date.now();

    try {
      const baseId = (currentMineId || 'home').split('.')[0];
      const floorStats = [];

      for (let f = 1; f <= deep; f++) {
        const roomKey = f >= deep ? baseId : `${baseId}.${f}`;
        if (f === currentFloor) {
          const working = S.hw != null ? S.hw : (S.st.working || 0);
          const total = S.here != null ? Math.max(S.here, working) : (working + (S.nOthers || 0));
          floorStats.push({ floor: f, working, total, count: Math.max(total, working), key: roomKey });
        } else {
          try {
            const res = await gameApi(`/api/mine/state?c=${encodeURIComponent(roomKey)}`);
            if (res && res.ok) {
              const st = res.st || {};
              const working = res.hw != null ? +res.hw : (st.working || 0);
              const total = res.here != null ? Math.max(+res.here, working) : (working + (res.n || 0));
              floorStats.push({ floor: f, working, total, count: Math.max(total, working), key: roomKey });
            }
          } catch (e) {
            // Игнорируем сетевую ошибку отдельного этажа
          }
          await new Promise(r => setTimeout(r, 200));
        }
      }

      if (!floorStats.length) return;

      const curStat = floorStats.find(x => x.floor === currentFloor);
      const curCount = curStat ? curStat.count : 0;

      // 🛗 Приоритет нового этажа: если открыт новый более глубокий этаж (deep > currentFloor)
      const deepStat = floorStats.find(x => x.floor === deep);
      if (deep > currentFloor && deepStat) {
        pendingLiftRide = null;
        await executeLiftRide(deep, deepStat.count || 1, 100);
        return;
      }

      // Сортируем: наибольшее число людей -> активных копателей -> глубина
      floorStats.sort((a, b) => (b.count - a.count) || (b.working - a.working) || (b.floor - a.floor));
      const best = floorStats[0];

      if (!best || best.floor === currentFloor) return;

      let shouldSwitch = false;
      let percentDiff = 0;

      if (curCount === 0) {
        if (best.count > 0) {
          shouldSwitch = true;
          percentDiff = 100;
        }
      } else {
        percentDiff = Math.round(((best.count - curCount) / curCount) * 100);
        if (percentDiff >= 30) {
          shouldSwitch = true;
        }
      }

      if (shouldSwitch) {
        if (isRatingMode && isMining()) {
          // 🏆 В режиме «Рейтинг» не прерываем копание начатого камня!
          // Запоминаем этаж и переедем на лифте сразу, как только текущий камень разрушится:
          pendingLiftRide = { floor: best.floor, count: best.count, percent: percentDiff };
          showToast(`🏆 Лифт: этаж ${best.floor} (+${percentDiff}%) ждет окончания текущего камня`);
          return;
        }
        // Переезжаем СРАЗУ на более людный этаж, не дожидаясь окончания текущей руды/камня!
        // На этаже с толпой скорость добычи и профит несравнимо выше.
        pendingLiftRide = null;
        await executeLiftRide(best.floor, best.count, percentDiff);
      }
    } catch (err) {
      console.warn('[Шахта] Ошибка авто-проверки этажей лифта:', err);
    } finally {
      isCheckingLiftFloors = false;
    }
  }

  async function executeLiftRide(targetFloor, peopleCount, percentDiff) {
    if (isRidingLift) return;
    isRidingLift = true;
    pendingLiftRide = null;
    activeMiningRockIndex = -1;
    lastTargetKey = null;
    lastMiningConfirmedTime = 0;
    lastKnownMiningProgress = null;

    try {
      const S = G.__lgMineS;
      const baseId = (currentMineId || 'home').split('.')[0];
      const currentFloor = (S && S.st && S.st.n) || 1;
      const deep = (S && S.st && (S.st.deep || S.st.n)) || 1;

      // Если мы уже на этом этаже — переезд не требуется
      if (currentFloor === targetFloor) {
        isRidingLift = false;
        return;
      }

      showToast(`🛗 Лифт: едем на этаж ${targetFloor} (+${percentDiff}%, ${peopleCount} чел.)…`);

      // 0. Принудительно прекращаем копание текущей руды/камня
      try {
        await stopMiningInGame(currentMineId);
      } catch (e) {}

      // 1. Вызов API перехода на этаж через контекст игры (X.api) или fallback
      let rideSuccess = false;
      try {
        const ctx = G.__lgMineContext;
        if (ctx && typeof ctx.api === 'function') {
          const r = await ctx.api('/api/mineRide', { c: baseId, n: targetFloor });
          if (r && r.ok) {
            rideSuccess = true;
          } else if (r && r.error) {
            showToast(`Лифт: ${r.error}`);
          }
        }
      } catch (e) {}

      if (!rideSuccess) {
        try {
          const res = await gameApi('/api/mineRide', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ c: baseId, n: targetFloor })
          });
          if (res && res.ok) {
            rideSuccess = true;
          }
        } catch (e) {}
      }

      if (rideSuccess) {
        // 🛡️ Защита от follow3: лифт успешно отправлен на targetFloor
        if (G.__lgMineS) {
          G.__lgMineS.rideAt = Date.now();
          if (G.__lgMineS.me) G.__lgMineS.me.at = targetFloor;
        }
        if (G.__lgMineMe) G.__lgMineMe.at = targetFloor;
        if (G.__lgMineState) G.__lgMineState.n = targetFloor;

        const targetKey = targetFloor >= deep ? baseId : `${baseId}.${targetFloor}`;
        // В коде игры (mine.js): f >= deep ? 300 : 2500 мс на анимацию движения лифта
        const delay = targetFloor >= deep ? 400 : 2600;

        await new Promise(r => setTimeout(r, delay));

        currentMineId = targetKey;
        G.__lgMineCurrent = targetKey;
        lastFloorTransitionTime = Date.now();
        lastMineOpenAttempt = Date.now();

        // Открываем сцену нового этажа на экране (force = true)
        tryOpenMineOnScreen(targetKey, true);
        try { updateMineSelectorUI(); } catch (e) {}

        if (G.__lgMineS) {
          G.__lgMineS.rideAt = Date.now();
          if (G.__lgMineS.me) G.__lgMineS.me.at = targetFloor;
          G.__lgMineS.newsShown = true;
        }

        // Даем браузеру 1.5с на отрисовку нового этажа
        await new Promise(r => setTimeout(r, 1500));
        isRidingLift = false;

        closeNewsModal();
        setTimeout(closeNewsModal, 300);
        setTimeout(closeNewsModal, 1000);

        if (G.__mineBotRunning && !isPaused) {
          clearTimeout(timer);
          timer = setTimeout(step, 100);
        }
      } else {
        isRidingLift = false;
      }
    } catch (e) {
      isRidingLift = false;
    }
  }

  function checkAutoLiftStep() {
    if (isPaused || !autoLiftBestFloor || isRidingLift) return;

    if (pendingLiftRide && !isMining()) {
      const { floor, count, percent } = pendingLiftRide;
      pendingLiftRide = null;
      executeLiftRide(floor, count, percent);
      return;
    }

    // 🛗 Проверка авто-спуска: если открыт новый этаж (deep > currentFloor)
    const S = G.__lgMineS;
    if (S && S.st) {
      const currentFloor = S.st.n || 1;
      const deep = S.st.deep || currentFloor;
      if (deep > currentFloor && !isRidingLift) {
        if (!isMining()) {
          executeLiftRide(deep, 1, 100);
          return;
        }
      }
    }

    if (Date.now() - lastLiftCheckTime > 120000) {
      checkAndSelectBestFloor();
    }
  }

  async function step() {
    if (!G.__mineBotRunning) return;
    closeNewsModal();

    // 🔒 Защита: если персонаж находится в тюрьме канала
    if (G.ME?.jail) {
      st.textContent = `🔒 В тюрьме канала (${G.ME.jail.reason || 'штрафной изолятор'})`;
      st.style.color = '#f59e0b';
      timer = setTimeout(step, 10000);
      return;
    }

    // ⚔️ Защита: если персонаж находится в походе с отрядом, майнер стоит на паузе
    if (questAutoPausedMiner) {
      st.textContent = '⚔️ В походе с отрядом (шахта на авто-паузе)';
      st.style.color = '#f43f5e';
      timer = setTimeout(step, 3000);
      return;
    }

    if (isRidingLift) return;

    if (!isPaused && autoLiftBestFloor) {
      checkAutoLiftStep();
      if (isRidingLift) return;
    }

    lastStepExecutionTime = Date.now();
    try {
      if (!isPaused) {
        if (mode === 'ghost') {
          // Проверка на ошибку сервера "mine_closed"
          const closedEl = document.querySelector('.mn-board .empty, .mn-panel .empty');
          const isClosedText = (closedEl && closedEl.textContent && closedEl.textContent.includes('mine_closed')) ||
                               (G.__lgMineError === 'mine_closed');
          if (isClosedText) {
            st.textContent = `❌ ${getMineLabel(currentMineId)} закрыта сервером`;
            st.style.color = '#ff4444';
            return;
          }

          if (!isTargetMineOpen(currentMineId)) {
            if (Date.now() - lastMineOpenAttempt > 5000 && Date.now() - lastFloorTransitionTime > 3500) {
              lastMineOpenAttempt = Date.now();
              tryOpenMineOnScreen(currentMineId);
            }
          }
          setGhostVisuals(true);
          ensureCityVisible(false);
          stepScreen();
        } else {
          if (!isTargetMineOpen(currentMineId)) {
            if (Date.now() - lastMineOpenAttempt > 5000 && Date.now() - lastFloorTransitionTime > 3500) {
              lastMineOpenAttempt = Date.now();
              tryOpenMineOnScreen(currentMineId);
            }
          }
          setGhostVisuals(false);
          stepScreen();
        }
        updateStatsUI();
        if (Date.now() - lastDrillCheckTime > 1000) {
          lastDrillCheckTime = Date.now();
          if (isDrillBuild) processDrillAutoFund();
        }
        if (autoClaimQuests) {
          tryAutoClaimMineQuests();
        }
        if (autoLiftBestFloor) {
          checkAutoLiftStep();
        }
        const drillScanInterval = isDrillBuildAllFloors ? 120000 : 300000;
        if (Date.now() - lastDrillsScanTime > drillScanInterval) {
          scanMyDrillsServerWide();
        }
      }
    } catch (err) {
    } finally {
      clearTimeout(timer);
      if (G.__mineBotRunning) {
        timer = setTimeout(step, 1800);
      }
    }
  }

  // При нажатии "В город" на экране шахты не обрываем сессию, а мягко переключаем в Фон (Город) и открываем Город
  document.addEventListener('click', (e) => {
    const exitBtn = e.target.closest && e.target.closest('[data-mn-exit]');
    if (exitBtn && G.__mineBotRunning) {
      e.preventDefault();
      e.stopPropagation();
      btnGhost.click();
      ensureCityVisible(true);
    }
  }, true);

  // =========================================================================
  // 🎁 АВТО-ОТПРАВКА «МОНЕТЫ ДНЯ» ДРУЗЬЯМ (Friendcoin 29.10)
  // =========================================================================
  async function autoSendFriendCoins() {
    try {
      const today = new Date().toDateString();
      const lastSent = localStorage.getItem('lg_last_friendcoin_date');
      if (lastSent === today) return;
      if (!G.ME || !G.ME.user) return;

      const rid = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
      const res = await gameApi('/api/friends/coin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ all: 1, rid })
      });
      if (res && res.ok) {
        localStorage.setItem('lg_last_friendcoin_date', today);
        const count = res.n || (res.to ? res.to.length : 0);
        if (count > 0) {
          console.log(`[Friendcoin] 🎁 Монета дня автоматически отправлена ${count} друзьям!`);
        }
      }
    } catch (e) {}
  }

  // Запуск
  applyActiveTab();
  scanMines();
  startAuctionPolling();
  refreshMinesTimer = setInterval(scanMines, 30000);
  updateStatsUI();
  initBackgroundAudioKeepAlive();
  initBackgroundWorker();
  setTimeout(autoSendFriendCoins, 4000);
  setInterval(autoSendFriendCoins, 3600000);
  if (!isPaused) step();
  if (typeof scanPlayers === 'function') scanPlayers(true);
  if (typeof initQuestModule === 'function') initQuestModule();
})();
