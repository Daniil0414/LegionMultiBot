'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const os = require('os');
const { dataDirectory } = require('../app_paths');

test('source manager uses the external development state directory', () => {
  const local = path.join(os.tmpdir(), 'legion-path-test-local');
  assert.equal(dataDirectory({ env: { LOCALAPPDATA: local } }), path.join(local, 'LegionMultiBot', 'Development'));
});

test('source manager rejects a data override inside the checkout', () => {
  const appDir = path.join(os.tmpdir(), 'legion-path-test-repo');
  assert.throws(() => dataDirectory({ appDir, env: { LEGION_DATA_DIR: path.join(appDir, 'private') } }), /outside the source repository/);
});
