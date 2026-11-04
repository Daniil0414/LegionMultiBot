'use strict';
const os = require('os');
const path = require('path');

function dataDirectory({ appDir = __dirname, env = process.env, home = os.homedir() } = {}) {
  const local = env.LOCALAPPDATA || path.join(home, ...(process.platform === 'win32' ? ['AppData', 'Local'] : ['.local', 'share']));
  const directory = path.resolve(env.LEGION_DATA_DIR || path.join(local, 'LegionMultiBot', 'Development'));
  const relative = path.relative(path.resolve(appDir), directory);
  if (!relative || (!path.isAbsolute(relative) && relative !== '..' && !relative.startsWith(`..${path.sep}`))) {
    throw new Error('LEGION_DATA_DIR must be outside the source repository');
  }
  return directory;
}

module.exports = { dataDirectory };
