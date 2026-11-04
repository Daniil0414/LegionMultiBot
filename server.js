const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn, spawnSync } = require('child_process');
const { randomBytes } = require('crypto');
const { allowManagerRequest } = require('./manager_access');
const { launchSecure, secureRequest, closeSecure } = require('./secure_launch');
const url = require('url');
const { dataDirectory } = require('./app_paths');

const PORT = Number(process.env.LEGION_MANAGER_PORT || 0);
const MANAGER_TOKEN = randomBytes(32).toString('base64url');
const CONFIG_FILE = path.join(dataDirectory(), 'launcher_config.json');
const PUBLIC_DIR = path.join(__dirname, 'public');
const AUTO_CLOSE = process.argv?.includes('--open') || false;
const managerSessions = new Set();
let managerCloseTimer;
let shuttingDown = false;

async function shutdownManager() {
  if (shuttingDown) return;
  shuttingDown = true;
  clearTimeout(managerCloseTimer);
  server.close();
  for (const session of managerSessions) session.end();
  try {
    await closeSecure();
  } finally {
    server.closeAllConnections?.();
    process.exit(0);
  }
}

// Find Chrome Executable
function getChromeExe() {
  const candidates = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    path.join(process.env.LOCALAPPDATA || '', 'Google\\Chrome\\Application\\chrome.exe'),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return 'chrome.exe';
}

const CHROME_EXE = getChromeExe();

// Load or default config
function loadConfig() {
  const defaults = {
    url: 'https://play.multidonate.ru/c/zakvielchannel/',
    delay: 1500,
    antiThrottling: true,
    muteAudio: true,
    layout: 'grid',
    selectedScreen: 0,
    pageSize: 6,
    currentPage: 1,
    autoFlipSeconds: 0, // 0 = off
    selectedProfiles: []
  };
  try {
    if (fs.existsSync(CONFIG_FILE)) {
      const data = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
      return { ...defaults, ...data };
    }
  } catch (e) {
    console.error('Error loading config:', e);
  }
  return defaults;
}

function saveConfig(cfg) {
  try {
    fs.mkdirSync(path.dirname(CONFIG_FILE), { recursive: true });
    fs.writeFileSync(CONFIG_FILE, JSON.stringify(cfg, null, 2), 'utf8');
  } catch (e) {
    console.error('Error saving config:', e);
  }
}

// Read Chrome profiles from Local State
function getChromeProfiles() {
  const localAppData = process.env.LOCALAPPDATA || '';
  const localStatePath = path.join(localAppData, 'Google', 'Chrome', 'User Data', 'Local State');
  if (!fs.existsSync(localStatePath)) {
    return [];
  }

  try {
    const raw = fs.readFileSync(localStatePath, 'utf8');
    const data = JSON.parse(raw);
    const infoCache = (data.profile && data.profile.info_cache) || {};

    const profiles = Object.keys(infoCache).map(key => {
      const p = infoCache[key];
      const profileDir = path.join(localAppData, 'Google', 'Chrome', 'User Data', key);
      const picPath = path.join(profileDir, 'Google Profile Picture.png');
      const hasPic = fs.existsSync(picPath);

      return {
        key,
        name: p.name || key,
        gaiaName: p.gaia_name || p.gaia_given_name || '',
        email: p.user_name || '',
        hasPic,
        activeTime: p.active_time || 0,
        isDefaultProfile: key === 'Default'
      };
    });

    // Sort: Default first, then by activeTime desc or name
    profiles.sort((a, b) => {
      if (a.isDefaultProfile) return -1;
      if (b.isDefaultProfile) return 1;
      return (b.activeTime || 0) - (a.activeTime || 0);
    });

    return profiles;
  } catch (e) {
    console.error('Failed to read Chrome profiles:', e);
    return [];
  }
}

// Detect Screens via PowerShell
let cachedScreens = null;
function getScreens() {
  if (cachedScreens) return cachedScreens;
  try {
    const psScript = `
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Screen]::AllScreens | ForEach-Object {
  Write-Output "$($_.DeviceName);$($_.Bounds.X);$($_.Bounds.Y);$($_.Bounds.Width);$($_.Bounds.Height);$($_.Primary)"
}
`;
    const res = spawnSync('powershell', ['-NoProfile', '-Command', psScript], { encoding: 'utf8', timeout: 5000 });
    if (res.stdout) {
      const lines = res.stdout.trim().split(/\r?\n/).filter(Boolean);
      const screens = lines.map((l, idx) => {
        const parts = l.split(';');
        return {
          index: idx,
          device: parts[0] || `DISPLAY${idx+1}`,
          x: parseInt(parts[1], 10) || 0,
          y: parseInt(parts[2], 10) || 0,
          width: parseInt(parts[3], 10) || 1920,
          height: parseInt(parts[4], 10) || 1080,
          primary: parts[5] === 'True'
        };
      });
      if (screens.length > 0) {
        cachedScreens = screens;
        return screens;
      }
    }
  } catch (e) {
    console.error('Failed to detect screens:', e);
  }

  return [{
    index: 0,
    device: 'Primary Display',
    x: 0,
    y: 0,
    width: 1920,
    height: 1080,
    primary: true
  }];
}

// Launching State
let launchState = {
  running: false,
  total: 0,
  current: 0,
  currentProfile: '',
  log: []
};

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// Calculate 3x2 Grid Coordinates for a specific slot on a screen
function calculateSlotPosition(slotIdx, screen, cols = 3, rows = 2) {
  const usableH = Math.max(400, screen.height - 40); // reserve taskbar
  const cellW = Math.floor(screen.width / cols);
  const cellH = Math.floor(usableH / rows);

  const col = slotIdx % cols;
  const row = Math.floor(slotIdx / cols);

  const x = screen.x + (col * cellW);
  const y = screen.y + (row * cellH);

  return { x, y, width: cellW, height: cellH };
}

// Launch a single profile in a given window slot
async function launchInSlot(key, slotIdx, screen, options = {}) {
  const pos = calculateSlotPosition(slotIdx, screen, options.cols || 3, options.rows || 2);
  const targetUrl = options.url || 'https://play.multidonate.ru/c/zakvielchannel/';

  const args = [
    '--new-window',
    `--window-position=${pos.x},${pos.y}`,
    `--window-size=${pos.width},${pos.height}`
  ];

  if (options.antiThrottling !== false) {
    args.push(
      '--disable-background-timer-throttling',
      '--disable-backgrounding-occluded-windows',
      '--disable-renderer-backgrounding',
      '--disable-features=CalculateNativeWinOcclusion'
    );
  }

  if (options.muteAudio) {
    args.push('--mute-audio');
  }


  try {
    const profileIndex = getChromeProfiles().filter(p => !p.isDefaultProfile).findIndex(p => p.key === key);
    await launchSecure(key, profileIndex >= 0 ? profileIndex : slotIdx, targetUrl, args);
    return true;
  } catch (e) {
    console.error(`Error launching ${key} in slot ${slotIdx}:`, e);
    return false;
  }
}

// Launch an entire page (e.g. 6 profiles in 3x2 grid)
async function launchPageBatch(profiles, options = {}) {
  if (launchState.running) {
    throw new Error('Уже выполняется запуск окон!');
  }

  launchState.running = true;
  launchState.total = profiles.length;
  launchState.current = 0;
  launchState.log = [];

  const screens = getScreens();
  const screenIdx = Math.max(0, Math.min(options.selectedScreen || 0, screens.length - 1));
  const screen = screens[screenIdx] || screens[0];

  const cols = options.cols || 3;
  const rows = options.rows || 2;
  const delay = options.delay !== undefined ? options.delay : 1200;

  try {
    for (let i = 0; i < profiles.length; i++) {
      const key = profiles[i];
      launchState.current = i + 1;
      launchState.currentProfile = key;

      const success = await launchInSlot(key, i, screen, {
        cols,
        rows,
        url: options.url,
        antiThrottling: options.antiThrottling,
        muteAudio: options.muteAudio
      });

      if (success) {
        launchState.log.push(`[${i+1}/${profiles.length}] Слот #${i+1} (${key}) запущен`);
      } else {
        launchState.log.push(`[${i+1}/${profiles.length}] Ошибка слота #${i+1} (${key})`);
      }

      if (i < profiles.length - 1 && delay > 0) {
        await sleep(delay);
      }
    }
  } finally {
    launchState.running = false;
    launchState.currentProfile = '';
  }
}

// Launch all profiles (staggered), keeping the specified current page on top
async function launchAllPagedBatch(allBotProfiles, currentPageIdx, pageSize, options = {}) {
  if (launchState.running) {
    throw new Error('Уже выполняется запуск окон!');
  }

  launchState.running = true;
  launchState.total = allBotProfiles.length;
  launchState.current = 0;
  launchState.log = [];

  const screens = getScreens();
  const screenIdx = Math.max(0, Math.min(options.selectedScreen || 0, screens.length - 1));
  const screen = screens[screenIdx] || screens[0];

  const cols = 3;
  const rows = 2;
  const delay = options.delay !== undefined ? options.delay : 1200;

  try {
    // 1. First launch the profiles of other pages (so they run in background)
    const startIdx = currentPageIdx * pageSize;
    const endIdx = startIdx + pageSize;
    const currentPageProfiles = allBotProfiles.slice(startIdx, endIdx);
    const otherProfiles = allBotProfiles.filter((_, idx) => idx < startIdx || idx >= endIdx);

    // Launch background ones first
    for (let i = 0; i < otherProfiles.length; i++) {
      const key = otherProfiles[i];
      launchState.current = i + 1;
      launchState.currentProfile = key;

      const slotIdx = i % (cols * rows);
      const success = await launchInSlot(key, slotIdx, screen, {
        cols,
        rows,
        url: options.url,
        antiThrottling: options.antiThrottling,
        muteAudio: options.muteAudio
      });
      launchState.log.push(`[Фон ${i+1}/${otherProfiles.length}] ${success ? "Запущен" : "VPN: запуск заблокирован"}: ${key}`);

      if (delay > 0) await sleep(delay);
    }

    // 2. Finally launch the current page profiles so they are on the TOP and visible
    for (let i = 0; i < currentPageProfiles.length; i++) {
      const key = currentPageProfiles[i];
      launchState.current = otherProfiles.length + i + 1;
      launchState.currentProfile = key;

      const success = await launchInSlot(key, i, screen, {
        cols,
        rows,
        url: options.url,
        antiThrottling: options.antiThrottling,
        muteAudio: options.muteAudio
      });
      launchState.log.push(`[Активная Стр #${currentPageIdx+1} Слот ${i+1}] ${success ? "Запущен" : "VPN: запуск заблокирован"}: ${key}`);

      if (i < currentPageProfiles.length - 1 && delay > 0) {
        await sleep(delay);
      }
    }
  } finally {
    launchState.running = false;
    launchState.currentProfile = '';
  }
}

// Fallback SVG avatar generator
function generateSvgAvatar(name, key) {
  const colors = [
    '#2563eb', '#7c3aed', '#db2777', '#ea580c', '#16a34a',
    '#0891b2', '#4f46e5', '#9333ea', '#c026d3', '#059669'
  ];
  let hash = 0;
  for (let i = 0; i < (key || name).length; i++) {
    hash = ((hash << 5) - hash) + (key || name).charCodeAt(i);
    hash |= 0;
  }
  const color = colors[Math.abs(hash) % colors.length];
  const letter = (name ? name.trim()[0] : 'P').toUpperCase();

  return `<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">
    <circle cx="48" cy="48" r="48" fill="${color}" />
    <text x="48" y="58" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif" font-size="38" font-weight="bold" fill="#ffffff" text-anchor="middle">${letter}</text>
  </svg>`;
}

// Parse body helper
function parseBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (e) {
        reject(e);
      }
    });
    req.on('error', reject);
  });
}

// HTTP Server
const server = http.createServer(async (req, res) => {
  const parsedUrl = url.parse(req.url, true);
  const pathname = parsedUrl.pathname;

  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Cross-Origin-Resource-Policy', 'same-origin');
  res.setHeader('Content-Security-Policy', "frame-ancestors 'none'");
  if (!allowManagerRequest(req, server.address().port, MANAGER_TOKEN)) {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    return res.end('Not Found');
  }

  // API Endpoints
  if (pathname === '/api/ui-session' && req.method === 'GET') {
    clearTimeout(managerCloseTimer);
    managerSessions.add(res);
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'X-Accel-Buffering': 'no' });
    res.write(': connected\n\n');
    res.once('close', () => {
      managerSessions.delete(res);
      // Give a page reload time to reconnect; other open tabs keep the manager alive.
      if (AUTO_CLOSE && !shuttingDown && managerSessions.size === 0) {
        managerCloseTimer = setTimeout(shutdownManager, 2000);
      }
    });
    return;
  }

  if (pathname === '/api/profiles') {
    const profiles = getChromeProfiles();
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    return res.end(JSON.stringify(profiles));
  }

  if (pathname === '/api/screens') {
    const screens = getScreens();
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    return res.end(JSON.stringify(screens));
  }

  if (pathname === '/api/config') {
    if (req.method === 'POST') {
      try {
        const body = await parseBody(req);
        saveConfig(body);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        return res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        return res.end(JSON.stringify({ error: e.message }));
      }
    } else {
      const cfg = loadConfig();
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      return res.end(JSON.stringify(cfg));
    }
  }

  if (pathname.startsWith('/api/avatar/')) {
    const key = decodeURIComponent(pathname.replace('/api/avatar/', ''));
    const localAppData = process.env.LOCALAPPDATA || '';
    const picPath = path.join(localAppData, 'Google', 'Chrome', 'User Data', key, 'Google Profile Picture.png');

    if (fs.existsSync(picPath)) {
      try {
        const img = fs.readFileSync(picPath);
        res.writeHead(200, { 'Content-Type': 'image/png', 'Cache-Control': 'public, max-age=3600' });
        return res.end(img);
      } catch (e) {}
    }

    const svg = generateSvgAvatar(key, key);
    res.writeHead(200, { 'Content-Type': 'image/svg+xml; charset=utf-8' });
    return res.end(svg);
  }

  // Launch a specific page (e.g. 6 profiles in 3x2 grid)
  if (pathname === '/api/launch-page' && req.method === 'POST') {
    try {
      const body = await parseBody(req);
      const profiles = body.profiles || [];
      if (!profiles.length) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        return res.end(JSON.stringify({ error: 'В этой странице нет профилей' }));
      }

      // Save config updates
      const cfg = loadConfig();
      saveConfig({
        ...cfg,
        url: body.url || cfg.url,
        delay: body.delay !== undefined ? body.delay : cfg.delay,
        antiThrottling: body.antiThrottling !== undefined ? body.antiThrottling : cfg.antiThrottling,
        muteAudio: body.muteAudio !== undefined ? body.muteAudio : cfg.muteAudio,
        selectedScreen: body.selectedScreen !== undefined ? body.selectedScreen : cfg.selectedScreen,
        currentPage: body.currentPage || cfg.currentPage
      });

      // Start page launch
      launchPageBatch(profiles, {
        cols: 3,
        rows: 2,
        selectedScreen: body.selectedScreen,
        url: body.url,
        antiThrottling: body.antiThrottling,
        muteAudio: body.muteAudio,
        delay: body.delay
      }).catch(e => {
        console.error('Launch page error:', e);
      });

      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ ok: true, count: profiles.length }));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ error: e.message }));
    }
  }

  // Launch all bots (background + current page in 3x2 grid)
  if (pathname === '/api/launch-all-paged' && req.method === 'POST') {
    try {
      const body = await parseBody(req);
      const allProfiles = body.profiles || [];
      const currentPageIdx = (body.currentPage || 1) - 1;
      const pageSize = body.pageSize || 6;

      launchAllPagedBatch(allProfiles, currentPageIdx, pageSize, {
        selectedScreen: body.selectedScreen,
        url: body.url,
        antiThrottling: body.antiThrottling,
        muteAudio: body.muteAudio,
        delay: body.delay
      }).catch(e => {
        console.error('Launch all paged error:', e);
      });

      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ ok: true, total: allProfiles.length }));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ error: e.message }));
    }
  }

  // Launch a single profile in a specific slot
  if (pathname === '/api/launch-slot' && req.method === 'POST') {
    try {
      const body = await parseBody(req);
      const key = body.key;
      const slotIdx = body.slotIdx || 0;
      const screens = getScreens();
      const screenIdx = Math.max(0, Math.min(body.selectedScreen || 0, screens.length - 1));
      const screen = screens[screenIdx] || screens[0];

      if (!await launchInSlot(key, slotIdx, screen, body)) throw new Error('VPN не подключен; запуск заблокирован');
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ ok: true, key, slotIdx }));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ error: e.message }));
    }
  }

  if (pathname === '/api/userscript') {
    try {
      let request;
      if (req.method === 'GET') request = { action: 'userscript-status' };
      else if (req.method === 'POST' || req.method === 'DELETE') {
        const body = await parseBody(req);
        request = { action: req.method === 'POST' ? 'userscript-install' : 'userscript-disable',
          url: body.script_url, script_id: body.script_id };
      }
      else {
        res.writeHead(405, { 'Content-Type': 'application/json', Allow: 'GET, POST, DELETE' });
        return res.end(JSON.stringify({ error: 'Method not allowed' }));
      }
      const result = await secureRequest(request);
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      return res.end(JSON.stringify(result.userscript));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ error: e.message }));
    }
  }

  if (pathname === '/api/status') {
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    return res.end(JSON.stringify(launchState));
  }

  // Static File Serving
  const asset = { '/': 'index.html', '/index.html': 'index.html',
                  '/app.js': 'app.js', '/style.css': 'style.css' }[pathname];
  if (!asset) {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    return res.end('Not Found');
  }
  const filePath = path.join(PUBLIC_DIR, asset);

  const ext = path.extname(filePath).toLowerCase();
  const mimeTypes = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png': 'image/png',
    '.svg': 'image/svg+xml'
  };

  try {
    const data = fs.readFileSync(filePath);
    res.writeHead(200, { 'Content-Type': mimeTypes[ext] || 'text/plain' });
    res.end(data);
  } catch (e) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Not Found');
  }
});

if (typeof process.once === 'function') {
  process.once('SIGINT', shutdownManager);
  process.once('SIGTERM', shutdownManager);
}

server.listen(PORT, '127.0.0.1', () => {
  const entryUrl = `http://127.0.0.1:${server.address().port}/#${MANAGER_TOKEN}`;
  console.log(`=======================================================`);
  console.log(`🚀 Legion Auto-Miner Profile Manager запущен!`);
  console.log(`🌐 Веб-интерфейс: ${entryUrl}`);
  console.log(`=======================================================`);
  if (process.argv.includes('--open')) {
    const browser = spawn(CHROME_EXE, [entryUrl], { detached: true, stdio: 'ignore', windowsHide: true });
    browser.on('error', () => console.error('Откройте адрес веб-интерфейса из этой консоли.'));
    browser.unref();
  }
});
