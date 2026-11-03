// Legion Multi-Bot Pager (6 Окон на страницу)
const managerToken = location.hash.slice(1) || sessionStorage.getItem('manager-access') || '';
if (location.hash) {
  sessionStorage.setItem('manager-access', managerToken);
  history.replaceState(null, '', location.pathname + location.search);
}
function managerFetch(input, init = {}) {
  const headers = new Headers(init.headers || {});
  headers.set('X-Manager-Token', managerToken);
  return fetch(input, { ...init, headers });
}
let managerSession;
async function watchManagerSession() {
  const session = new AbortController();
  managerSession?.abort();
  managerSession = session;
  try {
    const response = await managerFetch('/api/ui-session', { signal: session.signal });
    if (!response.ok) return;
    const reader = response.body.getReader();
    while (!(await reader.read()).done) { /* Keep this tab's manager session open. */ }
  } catch { /* Closing the tab or manager ends the connection. */ }
  finally { if (managerSession === session) managerSession = null; }
}
window.addEventListener('pagehide', () => managerSession?.abort());
window.addEventListener('pageshow', event => { if (event.persisted) watchManagerSession(); });
let allProfiles = [];
let botProfiles = []; // Profiles excluding 'Default'
let screens = [];
let currentPage = 1;
const PAGE_SIZE = 6;
let totalPages = 1;
let autoFlipIntervalSec = 0;
let autoFlipTimer = null;
let autoFlipCountdownSec = 0;
let countdownTimer = null;
let currentViewMode = 'slots'; // 'slots' or 'all'

const DEFAULT_URL = 'https://play.multidonate.ru/c/zakvielchannel/';

// DOM Elements
const totalBotsCountEl = document.getElementById('totalBotsCount');
const totalPagesCountEl = document.getElementById('totalPagesCount');
const scriptStateEl = document.getElementById('scriptState');
let userscriptConfig = {};

const btnPrevPage = document.getElementById('btnPrevPage');
const btnNextPage = document.getElementById('btnNextPage');
const pagePillsContainer = document.getElementById('pagePillsContainer');
const pageIndicatorText = document.getElementById('pageIndicatorText');

const autoFlipButtons = document.querySelectorAll('[data-flip]');
const autoFlipCountdown = document.getElementById('autoFlipCountdown');

const targetUrlInput = document.getElementById('targetUrl');
const btnResetUrl = document.getElementById('btnResetUrl');
const scriptUrlInput = document.getElementById('scriptUrl');
const scriptSelect = document.getElementById('scriptSelect');
const btnAddScript = document.getElementById('btnAddScript');
const scriptStatus = document.getElementById('scriptStatus');
const btnInstallScript = document.getElementById('btnInstallScript');
const btnDisableScript = document.getElementById('btnDisableScript');

const screenSelect = document.getElementById('screenSelect');
const delaySlider = document.getElementById('delaySlider');
const delayVal = document.getElementById('delayVal');
const chkAntiThrottling = document.getElementById('chkAntiThrottling');
const chkMuteAudio = document.getElementById('chkMuteAudio');

const btnLaunchCurrentPage = document.getElementById('btnLaunchCurrentPage');
const btnLaunchCurrentPageText = document.getElementById('btnLaunchCurrentPageText');
const btnLaunchAllPaged = document.getElementById('btnLaunchAllPaged');

const slotsGrid = document.getElementById('slotsGrid');
const allProfilesContainer = document.getElementById('allProfilesContainer');
const allProfilesGrid = document.getElementById('allProfilesGrid');
const btnViewSlots = document.getElementById('btnViewSlots');
const btnViewAllList = document.getElementById('btnViewAllList');
const gridTitle = document.getElementById('gridTitle');

// Modal Elements
const launchModal = document.getElementById('launchModal');
const progressBarFill = document.getElementById('progressBarFill');
const progressStep = document.getElementById('progressStep');
const progressProfileName = document.getElementById('progressProfileName');
const progressLogs = document.getElementById('progressLogs');
const btnCloseModal = document.getElementById('btnCloseModal');
const toast = document.getElementById('toast');

function showToast(msg, duration = 3000) {
  toast.textContent = msg;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), duration);
}

// Slot position description
const SLOT_NAMES = [
  '#1 ВЕРХ-ЛЕВО',
  '#2 ВЕРХ-ЦЕНТР',
  '#3 ВЕРХ-ПРАВО',
  '#4 НИЗ-ЛЕВО',
  '#5 НИЗ-ЦЕНТР',
  '#6 НИЗ-ПРАВО'
];

// Initialize
async function init() {
  watchManagerSession();
  try {
    const [profilesRes, screensRes, configRes] = await Promise.all([
      managerFetch('/api/profiles').then(r => r.json()),
      managerFetch('/api/screens').then(r => r.json()),
      managerFetch('/api/config').then(r => r.json())
    ]);

    allProfiles = profilesRes || [];
    screens = screensRes || [];

    // Filter bots (separate Default / personal profile)
    botProfiles = allProfiles.filter(p => !p.isDefaultProfile);
    totalPages = Math.max(1, Math.ceil(botProfiles.length / PAGE_SIZE));

    // Populate screens
    renderScreenSelect(screens, configRes ? configRes.selectedScreen : 0);

    // Apply config
    if (configRes) {
      targetUrlInput.value = configRes.url || DEFAULT_URL;
      delaySlider.value = configRes.delay || 1200;
      delayVal.textContent = (configRes.delay / 1000).toFixed(1) + ' сек';
      chkAntiThrottling.checked = configRes.antiThrottling !== false;
      chkMuteAudio.checked = configRes.muteAudio !== false;
      if (configRes.currentPage && configRes.currentPage <= totalPages) {
        currentPage = configRes.currentPage;
      }
    }

    await refreshUserscript(true);
    updateHeaderStats();
    renderPagePills();
    renderCurrentPage();
    setupKeyboardNavigation();
  } catch (err) {
    console.error('Init error:', err);
    slotsGrid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: #f43f5e; padding: 40px;">Ошибка: ${err.message}</div>`;
  }
}

// Screen dropdown
function renderScreenSelect(screenList, selectedIdx = 0) {
  screenSelect.innerHTML = '';
  screenList.forEach((s, idx) => {
    const opt = document.createElement('option');
    opt.value = idx;
    opt.textContent = `Экран ${idx + 1}: ${s.width}x${s.height}${s.primary ? ' [Основной]' : ''}`;
    if (idx === selectedIdx) opt.selected = true;
    screenSelect.appendChild(opt);
  });
}

function updateHeaderStats() {
  totalBotsCountEl.textContent = botProfiles.length;
  totalPagesCountEl.textContent = totalPages;
  scriptStateEl.textContent = userscriptConfig.installed ? `Включено: ${userscriptConfig.enabled_count}` : 'Отключены';
}

// Render Page Pills: [ Стр 1 (1–6) ] [ Стр 2 (7–12) ] ...
function renderPagePills() {
  pagePillsContainer.innerHTML = '';
  for (let p = 1; p <= totalPages; p++) {
    const startIdx = (p - 1) * PAGE_SIZE + 1;
    const endIdx = Math.min(p * PAGE_SIZE, botProfiles.length);

    const btn = document.createElement('button');
    btn.className = `btn-page-pill ${p === currentPage ? 'active' : ''}`;
    btn.innerHTML = `
      <span>📄 Стр ${p}</span>
      <span class="pill-sub">(${startIdx}–${endIdx})</span>
    `;
    btn.addEventListener('click', () => {
      setPage(p);
    });
    pagePillsContainer.appendChild(btn);
  }

  btnPrevPage.disabled = currentPage <= 1;
  btnNextPage.disabled = currentPage >= totalPages;

  const start = (currentPage - 1) * PAGE_SIZE + 1;
  const end = Math.min(currentPage * PAGE_SIZE, botProfiles.length);
  pageIndicatorText.textContent = `Страница ${currentPage} из ${totalPages} (Боты ${start}–${end})`;
  btnLaunchCurrentPageText.textContent = `ЗАПУСТИТЬ СТРАНИЦУ ${currentPage} (6 ОКОН 3x2)`;
}

// Switch Page
function setPage(pageNum, autoTrigger = false) {
  if (pageNum < 1 || pageNum > totalPages) return;
  currentPage = pageNum;
  renderPagePills();
  renderCurrentPage();
  saveSettings();

  if (autoTrigger) {
    launchCurrentPage(true); // silent focus
  }
}

btnPrevPage.addEventListener('click', () => setPage(currentPage - 1));
btnNextPage.addEventListener('click', () => setPage(currentPage + 1));

// Render 6 Slots for Current Page
function renderCurrentPage() {
  const startIdx = (currentPage - 1) * PAGE_SIZE;
  const pageProfiles = botProfiles.slice(startIdx, startIdx + PAGE_SIZE);

  gridTitle.textContent = `Сетка окон Страницы #${currentPage} (Слоты 1–6)`;
  slotsGrid.innerHTML = '';

  const curScreenIdx = parseInt(screenSelect.value, 10) || 0;
  const screen = screens[curScreenIdx] || screens[0] || { x: 0, y: 0, width: 1920, height: 1080 };
  const cellW = Math.floor(screen.width / 3);
  const cellH = Math.floor(Math.max(400, screen.height - 40) / 2);

  for (let slot = 0; slot < PAGE_SIZE; slot++) {
    const profile = pageProfiles[slot];
    const col = slot % 3;
    const row = Math.floor(slot / 3);
    const winX = screen.x + (col * cellW);
    const winY = screen.y + (row * cellH);
    const coordsStr = `X: ${winX}, Y: ${winY} • ${cellW}x${cellH}`;

    const card = document.createElement('div');

    if (!profile) {
      // Empty Slot
      card.className = 'slot-card empty-slot';
      card.innerHTML = `
        <div class="slot-top-bar">
          <span class="slot-index-badge">${SLOT_NAMES[slot]}</span>
          <span class="slot-coords">${coordsStr}</span>
        </div>
        <div style="text-align: center; margin-top: 20px;">
          <span>Слот свободен</span>
        </div>
      `;
      slotsGrid.appendChild(card);
      continue;
    }

    card.className = 'slot-card';

    const scriptBadge = userscriptBadge();

    card.innerHTML = `
      <div class="slot-top-bar">
        <span class="slot-index-badge">${SLOT_NAMES[slot]}</span>
        <span class="slot-coords" title="Позиция окна">${coordsStr}</span>
      </div>

      <div class="slot-profile-content">
        <div class="avatar-wrapper">
          <img class="profile-avatar" src="/api/avatar/${encodeURIComponent(profile.key)}" alt="${profile.name}" loading="lazy">
        </div>
        <div class="slot-card-details">
          <div class="profile-name" title="${profile.name}">${profile.name}</div>
          <div class="profile-gaia" title="${profile.gaiaName}">${profile.gaiaName || profile.name}</div>
          <div class="profile-email" title="${profile.email}">${profile.email || profile.key}</div>
        </div>
      </div>

      <div class="slot-badges-row">
        ${scriptBadge}
        <span class="badge" style="background: rgba(255,255,255,0.06); color: #94a3b8;">${profile.key}</span>
      </div>

      <div class="slot-footer">
        <select class="slot-change-select" data-slot="${slot}" title="Поменять бота в этом слоте">
          ${botProfiles.map(bp => `<option value="${bp.key}" ${bp.key === profile.key ? 'selected' : ''}>${bp.gaiaName || bp.name}</option>`).join('')}
        </select>
        <button class="btn-slot-launch" data-key="${profile.key}" data-slot="${slot}">Открыть ↗</button>
      </div>
    `;

    // Swap profile in slot
    const selectEl = card.querySelector('.slot-change-select');
    selectEl.addEventListener('change', (e) => {
      const newKey = e.target.value;
      swapProfileToSlot(startIdx + slot, newKey);
    });

    // Launch single slot
    const btnLaunchSlot = card.querySelector('.btn-slot-launch');
    btnLaunchSlot.addEventListener('click', () => {
      launchSingleSlot(profile.key, slot);
    });

    slotsGrid.appendChild(card);
  }
}

// Swap profile in array
function swapProfileToSlot(targetIndex, newKey) {
  const currentIdx = botProfiles.findIndex(p => p.key === newKey);
  if (currentIdx !== -1 && targetIndex < botProfiles.length) {
    const temp = botProfiles[targetIndex];
    botProfiles[targetIndex] = botProfiles[currentIdx];
    botProfiles[currentIdx] = temp;
    renderCurrentPage();
    showToast(`Профиль перемещен в слот #${(targetIndex % PAGE_SIZE) + 1}`);
  }
}

// Launch single slot
async function launchSingleSlot(key, slotIdx) {
  try {
    showToast(`Запуск слота #${slotIdx + 1} (${key})...`);
    const res = await managerFetch('/api/launch-slot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key,
        slotIdx,
        selectedScreen: parseInt(screenSelect.value, 10) || 0,
        url: targetUrlInput.value || DEFAULT_URL,
        antiThrottling: chkAntiThrottling.checked,
        muteAudio: chkMuteAudio.checked
      })
    }).then(r => r.json());

    if (res.ok) {
      showToast(`Слот #${slotIdx + 1} открыт!`);
      refreshUserscript();
    } else {
      showToast(`Ошибка: ${res.error}`);
    }
  } catch (e) {
    showToast(`Ошибка: ${e.message}`);
  }
}

// Launch Current Page (6 windows in 3x2 grid)
async function launchCurrentPage(silent = false) {
  const startIdx = (currentPage - 1) * PAGE_SIZE;
  const pageProfiles = botProfiles.slice(startIdx, startIdx + PAGE_SIZE).map(p => p.key);

  if (pageProfiles.length === 0) {
    showToast('На этой странице нет профилей');
    return;
  }

  if (!silent) {
    launchModal.classList.add('open');
    progressBarFill.style.width = '0%';
    progressStep.textContent = `0 из ${pageProfiles.length}`;
    progressProfileName.textContent = `Запуск Страницы #${currentPage}...`;
    progressLogs.innerHTML = `<div>Подготовка 6 окон (сетка 3x2) для Страницы #${currentPage}...</div>`;
  }

  try {
    const res = await managerFetch('/api/launch-page', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        profiles: pageProfiles,
        currentPage,
        selectedScreen: parseInt(screenSelect.value, 10) || 0,
        url: targetUrlInput.value || DEFAULT_URL,
        delay: parseInt(delaySlider.value, 10),
        antiThrottling: chkAntiThrottling.checked,
        muteAudio: chkMuteAudio.checked
      })
    }).then(r => r.json());

    if (!res.ok) {
      if (!silent) progressProfileName.textContent = `Ошибка: ${res.error}`;
      return;
    }

    if (!silent) {
      startStatusPolling(pageProfiles.length);
    } else {
      showToast(`Страница #${currentPage} выведена на экран!`);
    }
  } catch (e) {
    if (!silent) progressProfileName.textContent = `Ошибка: ${e.message}`;
  }
}

btnLaunchCurrentPage.addEventListener('click', () => launchCurrentPage(false));

// Launch ALL profiles paged (farm mode)
btnLaunchAllPaged.addEventListener('click', async () => {
  const allKeys = botProfiles.map(p => p.key);
  if (!confirm(`Запустить ВСЕХ ${allKeys.length} ботов?\nОни начнут копать в фоне, а на экране откроется сетка 3x2 для Страницы #${currentPage}.`)) {
    return;
  }

  launchModal.classList.add('open');
  progressBarFill.style.width = '0%';
  progressStep.textContent = `0 из ${allKeys.length}`;
  progressProfileName.textContent = `Массовый запуск всей фермы (${allKeys.length} ботов)...`;
  progressLogs.innerHTML = `<div>Запуск всех страниц в фоне + позиционирование Страницы #${currentPage}...</div>`;

  try {
    const res = await managerFetch('/api/launch-all-paged', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        profiles: allKeys,
        currentPage,
        pageSize: PAGE_SIZE,
        selectedScreen: parseInt(screenSelect.value, 10) || 0,
        url: targetUrlInput.value || DEFAULT_URL,
        delay: parseInt(delaySlider.value, 10),
        antiThrottling: chkAntiThrottling.checked,
        muteAudio: chkMuteAudio.checked
      })
    }).then(r => r.json());

    if (!res.ok) {
      progressProfileName.textContent = `Ошибка: ${res.error}`;
      return;
    }

    startStatusPolling(allKeys.length);
  } catch (e) {
    progressProfileName.textContent = `Ошибка: ${e.message}`;
  }
});

// Status Polling
let pollingTimer = null;
function startStatusPolling(total) {
  if (pollingTimer) clearInterval(pollingTimer);

  pollingTimer = setInterval(async () => {
    try {
      const state = await managerFetch('/api/status').then(r => r.json());
      const cur = state.current || 0;
      const pct = total > 0 ? Math.round((cur / total) * 100) : 0;

      progressBarFill.style.width = `${pct}%`;
      progressStep.textContent = `${cur} из ${total}`;
      if (state.currentProfile) {
        progressProfileName.textContent = `Запускается: ${state.currentProfile}`;
      }

      if (state.log && state.log.length > 0) {
        progressLogs.innerHTML = state.log.map(l => `<div>${l}</div>`).join('');
        progressLogs.scrollTop = progressLogs.scrollHeight;
      }

      if (!state.running && cur >= total) {
        clearInterval(pollingTimer);
        pollingTimer = null;
        progressProfileName.textContent = '🎉 Все окна успешно открыты и спозиционированы!';
        refreshUserscript();
        showToast('Окна разложены по экрану!');
      }
    } catch (e) {
      console.error('Polling error:', e);
    }
  }, 400);
}

// Auto Flip Feature
autoFlipButtons.forEach(btn => {
  btn.addEventListener('click', () => {
    autoFlipButtons.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    const sec = parseInt(btn.dataset.flip, 10);
    setAutoFlip(sec);
  });
});

function setAutoFlip(sec) {
  autoFlipIntervalSec = sec;
  if (autoFlipTimer) clearInterval(autoFlipTimer);
  if (countdownTimer) clearInterval(countdownTimer);

  if (sec <= 0) {
    autoFlipCountdown.style.display = 'none';
    showToast('Авто-листание выключено');
    return;
  }

  autoFlipCountdown.style.display = 'inline-block';
  autoFlipCountdownSec = sec;
  autoFlipCountdown.textContent = `⏱️ ${autoFlipCountdownSec}с`;
  showToast(`Авто-листание включено: каждые ${sec} сек`);

  countdownTimer = setInterval(() => {
    autoFlipCountdownSec--;
    if (autoFlipCountdownSec <= 0) {
      autoFlipCountdownSec = sec;
    }
    autoFlipCountdown.textContent = `⏱️ ${autoFlipCountdownSec}с`;
  }, 1000);

  autoFlipTimer = setInterval(() => {
    let next = currentPage + 1;
    if (next > totalPages) next = 1;
    setPage(next, true); // flips and focuses the 6 windows!
  }, sec * 1000);
}

// Keyboard shortcuts (Left/Right arrows, 1..4)
function setupKeyboardNavigation() {
  window.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      setPage(currentPage - 1);
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      setPage(currentPage + 1);
    } else if (e.key >= '1' && e.key <= '9') {
      const num = parseInt(e.key, 10);
      if (num <= totalPages) {
        e.preventDefault();
        setPage(num);
      }
    }
  });
}

// View toggle: Slots vs All Profiles
btnViewSlots.addEventListener('click', () => {
  btnViewSlots.classList.add('active');
  btnViewAllList.classList.remove('active');
  slotsGrid.style.display = 'grid';
  allProfilesContainer.style.display = 'none';
  currentViewMode = 'slots';
});

btnViewAllList.addEventListener('click', () => {
  btnViewAllList.classList.add('active');
  btnViewSlots.classList.remove('active');
  slotsGrid.style.display = 'none';
  allProfilesContainer.style.display = 'block';
  currentViewMode = 'all';
  renderAllProfilesList();
});

function renderAllProfilesList() {
  allProfilesGrid.innerHTML = '';
  allProfiles.forEach(p => {
    const card = document.createElement('div');
    card.className = 'slot-card';
    card.innerHTML = `
      <div class="slot-profile-content">
        <div class="avatar-wrapper">
          <img class="profile-avatar" src="/api/avatar/${encodeURIComponent(p.key)}" alt="${p.name}">
        </div>
        <div class="slot-card-details">
          <div class="profile-name">${p.name} ${p.isDefaultProfile ? '⭐' : ''}</div>
          <div class="profile-gaia">${p.gaiaName || p.name}</div>
          <div class="profile-email">${p.email || p.key}</div>
        </div>
      </div>
      <div class="slot-badges-row">
        ${userscriptBadge()}
        <span class="badge" style="background: rgba(255,255,255,0.06); color: #94a3b8;">${p.key}</span>
      </div>
    `;
    allProfilesGrid.appendChild(card);
  });
}

// Save settings to backend
function saveSettings() {
  managerFetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      url: targetUrlInput.value || DEFAULT_URL,
      delay: parseInt(delaySlider.value, 10),
      antiThrottling: chkAntiThrottling.checked,
      muteAudio: chkMuteAudio.checked,
      selectedScreen: parseInt(screenSelect.value, 10) || 0,
      currentPage
    })
  }).catch(() => {});
}

// Reset URL
btnResetUrl.addEventListener('click', () => {
  targetUrlInput.value = DEFAULT_URL;
  saveSettings();
  showToast('Ссылка сброшена на ZakvielChannel');
});

function userscriptBadge() {
  return userscriptConfig.installed
    ? '<span class="badge badge-tm-ok">🤖 Скрипт подключён</span>'
    : '<span class="badge badge-tm-no">Скрипт отключён</span>';
}

function showScriptSelection(fillUrl = true) {
  const scripts = userscriptConfig.scripts || [];
  const selected = scripts.find(item => item.id === scriptSelect.value);
  if (fillUrl) scriptUrlInput.value = selected ? selected.script_url : (scripts.length ? '' : 'https://github.com/Daniil0414/LegionAutoMiner/blob/main/mine-bot.user.js');
  const updated = selected && selected.last_updated ? new Date(selected.last_updated).toLocaleString('ru-RU') : '';
  scriptStatus.textContent = selected
    ? `${selected.name} ${selected.version} · ${selected.installed ? 'Включён' : 'Отключён'} · Обновлён: ${updated}.`
    : 'Новый скрипт: вставьте ссылку на файл GitHub или Raw и нажмите «Добавить».';
  scriptStatus.textContent += ` Включено скриптов для всех профилей: ${userscriptConfig.enabled_count || 0}.`;
  for (const item of scripts.filter(item => item.installed && item.last_error)) {
    scriptStatus.textContent += ` ${item.name}: используется сохранённая версия. Ошибка обновления: ${item.last_error}.`;
  }
  scriptStatus.classList.toggle('script-error', Boolean(userscriptConfig.last_error));
  btnDisableScript.disabled = !selected || !selected.installed;
  btnInstallScript.textContent = selected ? 'Установить / обновить выбранный' : 'Добавить';
}

function displayUserscript(config, fillUrl = false) {
  const previous = scriptSelect.value;
  userscriptConfig = config;
  const scripts = config.scripts || [];
  scriptSelect.replaceChildren(new Option('＋ Новый скрипт', ''));
  for (const [index, item] of scripts.entries()) {
    scriptSelect.add(new Option(`${index + 1}. ${item.installed ? '●' : '○'} ${item.name} ${item.version}`, item.id));
  }
  scriptSelect.value = config.selected_id || previous || (fillUrl && scripts.length ? scripts[0].id : '');
  showScriptSelection(fillUrl);
  updateHeaderStats();
  renderCurrentPage();
  if (currentViewMode === 'all') renderAllProfilesList();
}

async function refreshUserscript(fillUrl = false) {
  try {
    const response = await managerFetch('/api/userscript');
    const config = await response.json();
    if (!response.ok) throw new Error(config.error || 'Не удалось загрузить настройки скрипта');
    displayUserscript(config, fillUrl);
  } catch (error) {
    scriptStatus.textContent = error.message;
    scriptStatus.classList.add('script-error');
  }
}

async function changeUserscript(disable = false) {
  const id = scriptSelect.value;
  if (disable && !id) return;
  const body = { script_id: id || undefined, ...(disable ? {} : { script_url: scriptUrlInput.value.trim() }) };
  for (const element of [btnInstallScript, btnDisableScript, btnAddScript, scriptSelect, scriptUrlInput]) element.disabled = true;
  scriptStatus.textContent = disable ? 'Отключение выбранного скрипта...' : 'Загрузка с GitHub и применение ко всем окнам...';
  try {
    const response = await managerFetch('/api/userscript', {
      method: disable ? 'DELETE' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const config = await response.json();
    if (!response.ok) throw new Error(config.error || 'Не удалось изменить скрипт');
    displayUserscript(config, true);
    showToast(disable ? 'Выбранный скрипт отключён во всех окнах' : 'Скрипт установлен для всех профилей');
  } catch (error) {
    scriptStatus.textContent = error.message;
    scriptStatus.classList.add('script-error');
  } finally {
    for (const element of [btnInstallScript, btnAddScript, scriptSelect, scriptUrlInput]) element.disabled = false;
    const selected = (userscriptConfig.scripts || []).find(item => item.id === scriptSelect.value);
    btnDisableScript.disabled = !selected || !selected.installed;
  }
}
btnInstallScript.addEventListener('click', () => changeUserscript());
btnDisableScript.addEventListener('click', () => changeUserscript(true));
scriptSelect.addEventListener('change', () => showScriptSelection());
btnAddScript.addEventListener('click', () => {
  scriptSelect.value = '';
  showScriptSelection();
  scriptUrlInput.focus();
});

// Slider delay change
delaySlider.addEventListener('input', () => {
  const val = parseInt(delaySlider.value, 10);
  delayVal.textContent = (val / 1000).toFixed(1) + ' сек';
  saveSettings();
});

screenSelect.addEventListener('change', () => {
  renderCurrentPage();
  saveSettings();
});
chkAntiThrottling.addEventListener('change', saveSettings);
chkMuteAudio.addEventListener('change', saveSettings);
targetUrlInput.addEventListener('change', saveSettings);

btnCloseModal.addEventListener('click', () => {
  launchModal.classList.remove('open');
});

window.addEventListener('DOMContentLoaded', init);
