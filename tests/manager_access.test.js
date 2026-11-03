'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { allowManagerRequest } = require('../manager_access');
const token = 'a'.repeat(43);
function request(headers = {}, url = '/api/profiles', method = 'GET') {
  return { headers: { host: '127.0.0.1:54321', ...headers }, url, method };
}
test('local API requires a per-run token', () => {
  assert.equal(allowManagerRequest(request(), 54321, token), false);
  assert.equal(allowManagerRequest(request({ 'x-manager-token': token }), 54321, token), true);
  assert.equal(allowManagerRequest(request({ 'x-manager-token': 'wrong' }), 54321, token), false);
});
test('a game origin cannot read or operate the manager even with a token', () => {
  for (const headers of [{ origin: 'https://play.multidonate.ru' },
                         { 'sec-fetch-site': 'cross-site' },
                         { host: 'rebind.invalid:54321' }]) {
    assert.equal(allowManagerRequest(request({ ...headers, 'x-manager-token': token }), 54321, token), false);
  }
  assert.equal(allowManagerRequest(request({}, '/api/status', 'OPTIONS'), 54321, token), false);
});
test('own static assets and avatars work without exposing API data', () => {
  assert.equal(allowManagerRequest(request({ 'sec-fetch-site': 'same-origin' }, '/app.js'), 54321, token), true);
  assert.equal(allowManagerRequest(request({ 'sec-fetch-site': 'same-origin' }, '/api/avatar/Profile'), 54321, token), true);
  assert.equal(allowManagerRequest(request({ 'sec-fetch-site': 'cross-site' }, '/api/avatar/Profile'), 54321, token), false);
});
