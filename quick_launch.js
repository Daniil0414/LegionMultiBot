const fs = require('fs');
const path = require('path');
const { launchSecure } = require('./secure_launch');

const CHROME_EXE = fs.existsSync('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe')
  ? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
  : 'chrome.exe';

const TARGET_URL = 'https://play.multidonate.ru/c/zakvielchannel/';
const DELAY_MS = 1500;

const localAppData = process.env.LOCALAPPDATA || '';
const localStatePath = path.join(localAppData, 'Google', 'Chrome', 'User Data', 'Local State');

if (!fs.existsSync(localStatePath)) {
  console.error('Не найден файл Chrome Local State!');
  process.exit(1);
}

const data = JSON.parse(fs.readFileSync(localStatePath, 'utf8'));
const infoCache = (data.profile && data.profile.info_cache) || {};

// Exclude Default profile (personal)
const botKeys = Object.keys(infoCache).filter(k => k !== 'Default');

console.log('========================================================');
console.log(`🚀 БЫСТРЫЙ ЗАПУСК ВСЕХ БОТОВ LEGION (${botKeys.length} профилей)`);
console.log(`🎯 URL: ${TARGET_URL}`);
console.log(`⏱️ Задержка между окнами: ${DELAY_MS / 1000} сек`);
console.log('========================================================\n');

const sleep = ms => new Promise(res => setTimeout(res, ms));

(async () => {
  let launched = 0;
  for (let i = 0; i < botKeys.length; i++) {
    const key = botKeys[i];
    const name = infoCache[key].name || key;
    const gaia = infoCache[key].gaia_name || '';

    console.log(`[${i + 1}/${botKeys.length}] Запуск Chrome: ${name} (${gaia || key})...`);

    const args = [
      '--new-window',
      '--disable-background-timer-throttling',
      '--disable-backgrounding-occluded-windows',
      '--disable-renderer-backgrounding',
      '--disable-features=CalculateNativeWinOcclusion',
      '--mute-audio',
    ];

    try {
      await launchSecure(key, i, TARGET_URL, args);
      launched++;
    } catch (e) {
      console.error(`Ошибка при запуске ${key}:`, e.message);
    }

    if (i < botKeys.length - 1) {
      await sleep(DELAY_MS);
    }
  }

  console.log('\n========================================================');
  console.log(`Проверенных запусков: ${launched}/${botKeys.length}`);
  console.log('========================================================');
})();
