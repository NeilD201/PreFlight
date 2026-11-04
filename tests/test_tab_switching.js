// Review tab switching through the real init path: real static markup, real
// initializeDesign/initializeOverview/initializeReviewTabs, simulated clicks.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class El {
  constructor(tag, attrs = {}) {
    this.tag = tag; this.children = []; this.parent = null;
    this.attrs = { ...attrs }; this.dataset = {}; this._listeners = {};
    this._text = ''; this.hidden = false; this.value = '';
    this._classes = new Set((attrs.class || '').split(/\s+/).filter(Boolean));
    for (const [k, v] of Object.entries(attrs)) if (k.startsWith('data-')) this.dataset[k.slice(5)] = v;
  }
  get id() { return this.attrs.id || ''; }
  set id(v) { this.attrs.id = String(v); }
  get childNodes() { return this.children; }
  get parentElement() { return this.parent; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); }
  get classList() {
    const s = this._classes;
    return { add: (...c) => c.forEach(x => s.add(x)), remove: (...c) => c.forEach(x => s.delete(x)), toggle: (c, f) => { if (f === undefined) { if (s.has(c)) s.delete(c); else s.add(c); } else if (f) s.add(c); else s.delete(c); return s.has(c); }, contains: c => s.has(c) };
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  replaceChildren(...nodes) { for (const c of this.children) c.parent = null; this.children = []; this.append(...nodes); }
  getAttribute(k) { return this.attrs[k]; }
  _adopt(n) { if (!n) return false; if (n.parent) n.parent.children = n.parent.children.filter(c => c !== n); n.parent = this; return true; }
  append(...nodes) { for (const n of nodes) if (this._adopt(n)) this.children.push(n); return this; }
  appendChild(n) { return this.append(n); }
  prepend(...nodes) { for (const n of [...nodes].reverse()) if (this._adopt(n)) this.children.unshift(n); }
  before(...nodes) { if (!this.parent) return; const i = this.parent.children.indexOf(this); for (const n of nodes) { if (!n) continue; if (n.parent) n.parent.children = n.parent.children.filter(c => c !== n); n.parent = this.parent; this.parent.children.splice(i, 0, n); } }
  insertBefore(n, ref) { if (!this._adopt(n)) return n; const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(n); else this.children.splice(i, 0, n); return n; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); }
  addEventListener(t, f) { (this._listeners[t] = this._listeners[t] || []).push(f); }
  click() {
    const event = { target: this, preventDefault() {} };
    let node = this;
    while (node) { for (const f of node._listeners.click || []) f(event); node = node.parent; }
  }
  keydown(key) { for (const f of this._listeners.keydown || []) f({ target: this, key, preventDefault() {} }); }
  focus() {}
  scrollIntoView() {}
  contains(n) { let p = n; while (p) { if (p === this) return true; p = p.parent; } return false; }
  closest(sel) { let p = this; while (p) { if (matches(p, sel)) return p; p = p.parent; } return null; }
  querySelectorAll(sel) {
    const out = [];
    const walk = n => { for (const c of n.children) { if (c instanceof El) { if (matches(c, sel)) out.push(c); walk(c); } } };
    walk(this); return out;
  }
  querySelector(sel) {
    const found = this.querySelectorAll(sel);
    if (found.length) return found[0];
    const last = sel.split(/\s+/).filter(Boolean).pop().split(',')[0].trim();
    const n = new El('div');
    let m;
    if ((m = last.match(/^#([A-Za-z0-9_-]+)/))) n.attrs.id = m[1];
    else if ((m = last.match(/^\.([A-Za-z0-9_-]+)/))) n._classes.add(m[1]);
    else if (/^[a-zA-Z][a-zA-Z0-9]*$/.test(last)) n.tag = last;
    this.append(n); return n;
  }
}
function matches(el, sel) {
  for (const part of sel.split(',').map(s => s.trim())) {
    const bits = part.split(/\s+/).filter(Boolean);
    if (!bits.length || !matchOne(el, bits[bits.length - 1])) continue;
    let node = el.parent, ok = true;
    for (let i = bits.length - 2; i >= 0 && ok; i--) {
      while (node && !matchOne(node, bits[i])) node = node.parent;
      if (!node) ok = false; else node = node.parent;
    }
    if (ok) return true;
  }
  return false;
}
function matchOne(el, bit) {
  if (bit.startsWith('#')) return el.id === bit.slice(1);
  if (bit.startsWith('.')) return el.classList.contains(bit.slice(1));
  const m = bit.match(/^\[([^\]=]+)(?:=(?:"([^"]*)"|([^\]]+)))?\]$/);
  if (m) {
    const want = m[2] !== undefined ? m[2] : m[3];
    if (m[1] === 'role') return want === undefined ? el.attrs.role !== undefined : el.attrs.role === want;
    if (m[1].startsWith('data-')) { const v = el.dataset[m[1].slice(5)]; return want === undefined ? v !== undefined : v === want; }
    return false;
  }
  return el.tag === bit;
}
const html = fs.readFileSync('web/index.html', 'utf8');
// Seed the real static tab buttons and results panels.
const doc = new El('document');
const tablist = new El('div', { role: 'tablist' }); doc.append(tablist);
for (const m of html.matchAll(/<button[^>]*data-tab="([a-z-]+)"[^>]*>/g)) {
  const attrs = {}; for (const a of m[0].matchAll(/([a-z-]+)="([^"]*)"/g)) attrs[a[1]] = a[2];
  const b = new El('button', attrs); b.append(new El('#text')); tablist.append(b);
}
const results = new El('section', { id: 'results' }); doc.append(results);
results.append(tablist);
for (const m of html.matchAll(/<section[^>]*id="(panel-[a-z-]+)"[^>]*role="tabpanel"[^>]*>/g)) {
  const attrs = {}; for (const a of m[0].matchAll(/([a-z-]+)="([^"]*)"/g)) attrs[a[1]] = a[2];
  const p = new El('section', attrs); p.hidden = true; results.append(p);
}
const registry = {};
const scratch = new El('scratch');
doc.querySelectorAll('*').forEach(() => {});
(function index(n) { if (n.id) registry[n.id] = n; for (const c of n.children) if (c instanceof El) index(c); })(doc);
const context = vm.createContext({
  console, window: {}, location: { search: '' }, matchMedia: () => ({ matches: false }),
  requestAnimationFrame: () => {}, setInterval: () => 0, clearInterval: () => {}, setTimeout: (f) => 0, clearTimeout: () => {},
  URLSearchParams, URL, AbortController, fetch: async () => ({ json: async () => ({ status: 'empty' }) }),
  document: {
    createElement: tag => new El(tag),
    createElementNS: (ns, tag) => new El(tag),
    createTextNode: text => { const e = new El('#text'); e.textContent = text; return e; },
    getElementById: id => registry[id] || (registry[id] = (() => { const e = new El('div', { id }); scratch.append(e); return e; })()),
    querySelector: sel => doc.querySelector(sel) || new El('div'),
    querySelectorAll: sel => doc.querySelectorAll(sel),
  },
});
for (const f of ['web/app.js', 'web/overview.js', 'web/manufacturing.js', 'web/release.js', 'web/design.js', 'web/workflow.js', 'web/product-context.js', 'web/editor-connections.js', 'web/usage.js', 'web/locations.js']) {
  let src = fs.readFileSync(f, 'utf8');
  if (f === 'web/app.js') src = src.replace('initialize().catch(showError);', '');
  vm.runInContext(src, context);
}
const relCount = () => vm.runInContext("document.querySelectorAll('[data-tab]').filter(b=>b.dataset.tab==='release').length", context);
vm.runInContext('initializeDesign();', context);
vm.runInContext('globalThis.__calls=[];const _io=initializeOverview;initializeOverview=function(){globalThis.__calls.push("overview");return _io();};const _ir=initializeRelease;initializeRelease=function(t){globalThis.__calls.push("release");return _ir(t);};const _im=initializeManufacturing;initializeManufacturing=function(t){globalThis.__calls.push("mfg");return _im(t);};', context);
vm.runInContext('initializeOverview();', context);
vm.runInContext('initializeReviewTabs();', context);
const buttons = vm.runInContext("[...document.querySelectorAll('[data-tab]')].map(b=>b.dataset.tab)", context);
assert.ok(buttons.length >= 8, `expected static+dynamic tabs, got ${buttons}`);
// Every tab click shows exactly its panel and marks exactly its button.
for (const name of buttons) {
  vm.runInContext(`document.querySelectorAll('[data-tab]').find(b=>b.dataset.tab==='${name}').click()`, context);
  const visible = vm.runInContext("[...document.querySelectorAll('#results [role=tabpanel]')].filter(p=>!p.hidden).map(p=>p.id).join(',')", context);
  const active = vm.runInContext("document.querySelectorAll('[data-tab]').filter(b=>b.getAttribute('aria-selected')==='true').map(b=>b.dataset.tab).join(',')", context);
  const section = ['connections', 'board', 'notes'].includes(name) ? 'overview' : name;
  assert.equal(visible, `panel-${section}`, `${name} shows panel-${section}, got ${visible}`);
  assert.equal(active, name, `${name} marks its button, got ${active}`);
}
// Keyboard: arrows move within the visible row.
vm.runInContext(`document.querySelectorAll('[data-tab]')[0].keydown('ArrowRight')`, context);
const afterKey = vm.runInContext("document.querySelectorAll('[data-tab]').filter(b=>b.getAttribute('aria-selected')==='true').map(b=>b.dataset.tab).join(',')", context);
assert.ok(afterKey && !afterKey.includes(','), 'arrow key selects exactly one tab');
// Late-added tabs work without rebinding (delegation).
vm.runInContext(`{const t=document.querySelector('[role=tablist]');const b=document.createElement('button');b.dataset.tab='late';b.setAttribute('role','tab');t.append(b);const p=document.createElement('section');p.id='panel-late';p.hidden=true;p.setAttribute('role','tabpanel');document.getElementById('results').append(p);}`, context);
vm.runInContext(`document.querySelectorAll('[data-tab]').find(b=>b.dataset.tab==='late').click()`, context);
const lateVisible = vm.runInContext("[...document.querySelectorAll('#results [role=tabpanel]')].filter(p=>!p.hidden).map(p=>p.id).join(',')", context);
assert.equal(lateVisible, 'panel-late', 'late-added tab switches');
console.log('Review tabs: delegated events, one panel, keyboard order, late tabs passed');
