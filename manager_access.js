'use strict';
const { timingSafeEqual } = require('crypto');

function allowManagerRequest(req, port, token) {
  const hosts = [`127.0.0.1:${port}`, `localhost:${port}`];
  if (!hosts.includes(req.headers.host)) return false;
  const origin = req.headers.origin;
  if (origin && !hosts.some(host => origin === `http://${host}`)) return false;
  const site = req.headers['sec-fetch-site'];
  if (site && site !== 'same-origin' && site !== 'none') return false;
  if (req.method === 'OPTIONS') return false;
  const pathname = new URL(req.url, `http://127.0.0.1:${port}`).pathname;
  if (!pathname.startsWith('/api/')) return req.method === 'GET' || req.method === 'HEAD';
  // Images cannot add a custom header; only the manager's own origin can load them.
  if (pathname.startsWith('/api/avatar/') && req.method === 'GET' && site === 'same-origin') return true;
  const supplied = Buffer.from(req.headers['x-manager-token'] || '');
  const expected = Buffer.from(token);
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

module.exports = { allowManagerRequest };
