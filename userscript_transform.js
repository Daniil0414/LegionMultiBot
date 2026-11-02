'use strict';
// Syntax-aware adaptation of the supported Legion userscript. No page API patches.
const acorn = require('./vendor/acorn.js');
const crypto = require('node:crypto');

const flags = new Set(['__botApiWrapped', '__botHooked', '__botMineHooked',
  '__cityHooked', '__hooked', '__isBotFallback', '__protectedF']);
const roots = new Set(['G', 'window', 'unsafeWindow', 'globalThis']);

function transform(source) {
  const tree = acorn.parse(source, { ecmaVersion: 'latest', sourceType: 'script' });
  const name = '_' + crypto.randomBytes(12).toString('hex');
  const stats = { globals: 0, objectFlags: 0, uiAccesses: 0 };
  const propertyName = node => node.computed ?
    (node.property.type === 'Literal' && typeof node.property.value === 'string' ? node.property.value : '') :
    node.property.name;
  const isDocument = node => node.type === 'Identifier' && node.name === 'document' ||
    node.type === 'MemberExpression' && node.object.type === 'Identifier' &&
      roots.has(node.object.name) && propertyName(node) === 'document';
  const isContainer = node => node.type === 'MemberExpression' && isDocument(node.object) &&
    ['body', 'head', 'documentElement'].includes(propertyName(node)) ||
    node.type === 'LogicalExpression' && isContainer(node.left) && isContainer(node.right);
  function emit(node) {
    if (node.type === 'Literal' && node.value === 'lg-ghost-mode') return `${name}.ghostClass`;
    if (node.type === 'CallExpression' && node.callee.type === 'MemberExpression') {
      const method = propertyName(node.callee), object = node.callee.object;
      const args = () => node.arguments.map(emit).join(',');
      if (isDocument(object) && ['getElementById', 'querySelector', 'querySelectorAll'].includes(method)) {
        stats.uiAccesses++;
        return `${name}.query(${JSON.stringify(method)},${args()})`;
      }
      if (isContainer(object) && method === 'appendChild' && node.arguments.length === 1) {
        stats.uiAccesses++;
        return `${name}.append(${emit(object)},${args()})`;
      }
      if (isContainer(object) && method === 'contains' && node.arguments.length === 1) {
        return `${name}.contains(${emit(object)},${args()})`;
      }
    }
    if (node.type === 'MemberExpression') {
      const key = propertyName(node);
      if (isDocument(node.object) && ['title', 'activeElement'].includes(key)) return `${name}.${key}`;
      const global = node.object.type === 'Identifier' && roots.has(node.object.name) && /^__(mine|lg)\w+$/.test(key);
      if (global || flags.has(key)) {
        stats[global ? 'globals' : 'objectFlags']++;
        return `${name}.state(${emit(node.object)})${node.optional ? '?.' : '.'}${key}`;
      }
      if (node.object.type === 'Identifier' && roots.has(node.object.name) && key === 'localStorage') {
        return `${name}.storage`;
      }
    }
    if (node.type === 'ObjectExpression') {
      const privateProps = node.properties.filter(p => p.type === 'Property' && !p.computed &&
        flags.has(p.key.name || p.key.value));
      if (privateProps.length) {
        if (privateProps.some(p => p.kind !== 'init' || p.method || p.value.type !== 'Literal')) {
          throw new Error('Unsupported private flag initializer; userscript adaptation blocked');
        }
        stats.objectFlags += privateProps.length;
        const publicProps = node.properties.filter(p => !privateProps.includes(p));
        return `${name}.init({${publicProps.map(emit).join(',')}},{${privateProps.map(emit).join(',')}})`;
      }
    }
    const children = [];
    for (const value of Object.values(node)) {
      for (const child of (Array.isArray(value) ? value : [value])) {
        if (child && typeof child.type === 'string' && Number.isInteger(child.start)) children.push(child);
      }
    }
    children.sort((a, b) => a.start - b.start || b.end - a.end);
    let result = '', position = node.start;
    for (const child of children) {
      if (child.start < position) continue; // Shorthand properties share a key/value range.
      result += source.slice(position, child.start) + emit(child);
      position = child.end;
    }
    return result + source.slice(position, node.end);
  }
  const adapted = emit(tree);
  if (!stats.globals) throw new Error('Supported bot private state was not found; adaptation blocked');
  const code = `(function(${name}, localStorage) {\n${adapted}\n}).call(window, privacy, privacy.storage);`;
  acorn.parse(code, { ecmaVersion: 'latest' });
  return { code, stats };
}

module.exports = { transform };
if (require.main === module) {
  let source = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', chunk => { source += chunk; });
  process.stdin.on('end', () => {
    try { process.stdout.write(JSON.stringify(transform(source))); }
    catch (error) { process.stderr.write(String(error.message)); process.exitCode = 1; }
  });
}
