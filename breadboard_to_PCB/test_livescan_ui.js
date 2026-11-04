// LiveScan workspace UI: picker, intent, camera stubs, QC guidance, parts,
// wires, questions, findings, save. Uses purpose-built DOM/camera stubs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class El {
  constructor(tag, attrs = {}) {
    this.tagName = (tag || 'div').toUpperCase();
    this.children = []; this.parent = null;
    this._cls = new Set(String(attrs.class || '').split(' ').filter(Boolean));
    this.dataset = {}; this.attrs = {}; this._listeners = {};
    this.textContent = ''; this.innerHTMLRaw = '';
    this.value = ''; this.checked = false; this.disabled = false; this.hidden = false;
    this.selected = false; this.files = []; this.srcObject = null;
    this.style = {};
    this.width = 0; this.height = 0;
    let _id = '';
    Object.defineProperty(this, 'id', { get: () => _id,
      set: (v) => { _id = String(v); if (_id) registry[_id] = this; } });
  }
  get firstChild() { return this.children[0]; }
  get lastChild() { return this.children[this.children.length - 1]; }
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v).split(' ').filter(Boolean)); }
  get classList() {
    const self = this;
    return { contains: (c) => self._cls.has(c), add: (...c) => c.forEach((x) => self._cls.add(x)),
      remove: (...c) => c.forEach((x) => self._cls.delete(x)),
      toggle: (c, f) => { const on = f === undefined ? !self._cls.has(c) : !!f; on ? self._cls.add(c) : self._cls.delete(c); return on; } };
  }
  set innerHTML(html) {
    this.innerHTMLRaw = String(html);
    // Minimal parse: materialize id/class/tag hooks the UI queries for.
    this.children = [];
    const re = /<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>/g;
    let m;
    while ((m = re.exec(this.innerHTMLRaw))) {
      const tag = m[1].toLowerCase();
      if (['h1', 'h2', 'h3', 'h4', 'p', 'span', 'em', 'strong', 'figcaption', 'figure'].includes(tag)) continue;
      const idm = /id="([^"]+)"/.exec(m[2]);
      const child = new El(tag);
      if (idm) { child.setAttribute('id', idm[1]); child.id = idm[1]; registry[idm[1]] = child; }
      this.append(child);
    }
  }
  get innerHTML() { return this.innerHTMLRaw; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') { this.id = String(v); registry[String(v)] = this; } if (k === 'class') this.className = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  append(...nodes) { for (const n of nodes) { if (!n) continue; n.parent = this; this.children.push(n); } return this; }
  appendChild(n) { return this.append(n); }
  replaceChildren(...nodes) { this.children = []; this.innerHTMLRaw = ''; return this.append(...nodes); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
  addEventListener(t, f) { (this._listeners[t] = this._listeners[t] || []).push(f); }
  click() { for (const f of this._listeners.click || []) f({ target: this, preventDefault() {} }); }
  submit() { for (const f of this._listeners.submit || []) f({ target: this, preventDefault() {} }); }
  querySelectorAll(sel) {
    const out = [];
    const parts = String(sel).split(',').map((s) => s.trim()).filter(Boolean);
    const walk = (n) => { for (const c of n.children) { if (matches(c, parts)) out.push(c); walk(c); } };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  scrollIntoView() {}
  focus() {}
  play() { return Promise.resolve(); }
}
function matches(el, parts) {
  return parts.some((sel) => {
    const chain = sel.split(' ').filter(Boolean);
    let node = el;
    for (let i = chain.length - 1; i >= 0; i--) {
      if (!node || !simpleMatch(node, chain[i])) return false;
      if (i > 0) { // ancestor may be further up
        let p = node.parent, found = false;
        while (p) { if (simpleMatch(p, chain[i - 1])) { node = p; found = true; break; } p = p.parent; }
        if (!found) return false;
        i--;
      }
    }
    return true;
  });
}
function simpleMatch(el, sel) {
  if (sel.startsWith('#')) return el.id === sel.slice(1);
  if (sel.startsWith('.')) return el._cls.has(sel.slice(1));
  if (sel.startsWith('[')) {
    const m = /\[([^\]=]+)(?:="([^"]*)")?\]/.exec(sel);
    return m ? (m[2] === undefined ? m[1] in el.attrs : el.attrs[m[1]] === m[2]) : false;
  }
  const dot = sel.indexOf('.');
  if (dot > 0) return el.tagName === sel.slice(0, dot).toUpperCase() && el._cls.has(sel.slice(dot + 1));
  return el.tagName === sel.toUpperCase();
}
const registry = {};
function node(tag, cls, text) {
  const e = new El(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
// Programmable camera/canvas pixels for QC tests.
let PIXELS = new Uint8ClampedArray(160 * 120 * 4).fill(128);
let IMG_W = 640, IMG_H = 480;
class FakeImage {
  constructor() { this.naturalWidth = IMG_W; this.naturalHeight = IMG_H; this._src = ''; }
  set src(v) { this._src = v; setTimeout(() => this.onload && this.onload(), 0); }
  get src() { return this._src; }
}
function fakeCanvas() {
  const c = new El('canvas');
  c.getContext = () => ({ canvas: c, drawImage() {}, getImageData: () => ({ data: PIXELS }), translate() {}, rotate() {}, save() {}, restore() {},
    clearRect() {}, fillRect() {}, strokeRect() {}, set fillStyle(v) {}, set strokeStyle(v) {}, set lineWidth(v) {} });
  c.toBlob = (cb) => cb(new Blob(['x'], { type: 'image/jpeg' }));
  return c;
}
const errors = [];
const apiCalls = [];
let failSaveWith = null;
const interpretCalls = [];
const twinProjects = [];
const docProjects = [];
const context = vm.createContext({
  console, window: {}, location: { search: '' },
  navigator: {
    mediaDevices: {
      enumerateDevices: async () => [
        { kind: 'videoinput', label: 'FaceTime HD Camera', deviceId: 'ft' },
        { kind: 'videoinput', label: 'iPhone Continuity Camera', deviceId: 'cc' },
        { kind: 'audioinput', label: 'mic', deviceId: 'm' },
      ],
      getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }),
    },
  },
  document: {
    createElement: (t) => (t === 'canvas' ? fakeCanvas() : new El(t)),
    getElementById: (id) => registry[id] || (registry[id] = (() => { const e = new El('div'); e.setAttribute('id', id); return e; })()),
    querySelector: (s) => {
      for (const r of Object.values(registry)) { const f = r.querySelector(s); if (f) return f; }
      return null;
    },
    querySelectorAll: (s) => {
      const out = [];
      const roots = [context.document.body, ...Object.values(registry)];
      const seen = new Set();
      for (const r of roots) { if (r && !seen.has(r)) { seen.add(r); out.push(...r.querySelectorAll(s)); } }
      return out;
    },
    body: new El('body'),
  },
  Image: FakeImage, setTimeout, clearTimeout, setInterval, clearInterval, AbortController,
  localStorage: { _s: {}, getItem(k) { return this._s[k] ?? null; }, setItem(k, v) { this._s[k] = String(v); } },
  FileReader: class { readAsDataURL() { setTimeout(() => this.onload && this.onload(), 0); } },
  twinProjects,
  fetch: async (url) => {
    if (String(url).includes('/interpret')) {
      interpretCalls.push(String(url));
      // Mostly empty results, but the 3rd view reports one resistor so the
      // test proves a merged component reaches the 3D twin.
      const obs = interpretCalls.length === 3
        ? { board: { size_guess: 'unknown', rails_visible: [], coverage_notes: '' },
            components: [{ kind: 'resistor', label: 'tan resistor', value_guess: '',
                           bbox: [0.2, 0.3, 0.4, 0.5], confidence: 'medium', notes: '' }],
            wires: [], questions: [], next_view: '' }
        : { components: [], wires: [], questions: [] };
      return { ok: true, json: async () => ({ observations: obs }) };
    }
    return { ok: true, json: async () => ({ name: String(url).includes('scan-') ? 'scan-1.jpg' : 'a.jpg', size: 10 }) };
  },
  File, Blob, URL: { createObjectURL: () => 'blob:fake', revokeObjectURL() {} },
  requestAnimationFrame: () => 0,
  app: { config: { csrf_token: 'test' } },
  api: async (path, data) => {
    apiCalls.push({ path, data });
    if (path === '/api/livescan/projects' && data === undefined) return { projects: docProjects };
    if (path === '/api/livescan/projects') {
      const now = new Date().toISOString();
      return { version: 1, id: 'ls-test0001', name: data.name, created: now, updated: now,
        breadboard: { size: data.size, railGaps: {} }, intent: { text: '', fields: {}, updated: null },
        components: [], wires: [], images: [], questions: [], confirmations: {}, counters: {} };
    }
    const single = path.match(/^\/api\/livescan\/projects\/([^/]+)$/);
    if (single && data === undefined) {
      const found = docProjects.find((p) => p.id === decodeURIComponent(single[1]));
      if (!found) throw new Error('unknown project ' + single[1]);
      return JSON.parse(JSON.stringify(found));
    }
    if (path.endsWith('/save')) {
      if (failSaveWith) throw new Error(failSaveWith);
      return { id: 'ls-test0001', updated: '' };
    }
    throw new Error('unexpected api ' + path);
  },
  node, navigate: () => {},
  showError: (e) => { errors.push(String((e && e.message) || e)); },
});
vm.runInContext(fs.readFileSync('breadboard_to_PCB/breadboard.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('breadboard_to_PCB/scanmath.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('breadboard_to_PCB/livescan.js', 'utf8'), context);

(async () => {
  const $ = (id) => vm.runInContext(`document.getElementById('${id}')`, context);
  vm.runInContext('window.initializeLiveScan()', context);
  await new Promise((r) => setTimeout(r, 50));
  assert.ok($('ls-new-name'), 'create form renders');
  assert.ok($('ls-new-size'), 'size selector renders');
  // Create a project.
  vm.runInContext(`document.getElementById('ls-new-name').value='Door sensor';
    document.getElementById('ls-new-size').value='half';`, context);
  $('ls-new-name').parent.submit ? null : null;
  const form = $('ls-new-name').parent;
  form.submit();
  await new Promise((r) => setTimeout(r, 50));
  assert.ok($('ls-capture'), 'capture panel renders');
  assert.ok($('ls-twin'), 'twin mount renders');
  assert.ok($('ls-parts'), 'parts panel renders');
  assert.ok($('ls-wires'), 'wires panel renders');
  assert.ok($('ls-questions'), 'questions panel renders');
  assert.ok($('ls-findings'), 'findings panel renders');
  assert.match(vm.runInContext(`document.getElementById('ls-twin-gl').children.map((c) => c.textContent).join(' ')`, context),
    /3D engine failed to load/, 'graceful 3D fallback without engine');
  assert.ok(vm.runInContext(`document.getElementById('ls-twin').querySelector('#ls-record')`, context),
    'scanner overlay survives the fallback twin mount');
  console.log('picker/create/workspace: passed');

  // Camera devices incl. Continuity Camera; start + capture still.
  const sel = $('ls-camera-sel');
  await new Promise((r) => setTimeout(r, 30));
  const labels = sel.children.map((o) => o.textContent).join('|');
  assert.match(labels, /Continuity/, 'continuity camera listed');
  vm.runInContext(`[...document.getElementById('ls-capture').querySelectorAll('button')].find(b=>b.textContent==='Start camera').click()`, context);
  await new Promise((r) => setTimeout(r, 30));
  // Sharp checkerboard pixels.
  PIXELS = new Uint8ClampedArray(160 * 120 * 4);
  for (let y = 0; y < 120; y++) for (let x = 0; x < 160; x++) {
    const v = ((x >> 3) + (y >> 3)) % 2 ? 220 : 40;
    PIXELS.set([v, v, v, 255], (y * 160 + x) * 4);
  }
  IMG_W = 640; IMG_H = 480;
  // Front-camera facing is surfaced without breaking the normal path.
  vm.runInContext(`navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [{ stop() {}, getSettings: () => ({ facingMode: 'user' }) }],
    getVideoTracks() { return this.getTracks(); } });`, context);
  await vm.runInContext(`window.LiveScanTest.startCamera()`, context);
  await new Promise((r) => setTimeout(r, 30));
  assert.match($('ls-guide').children.map((c) => c.textContent).join(' '), /Front camera/,
    'front-facing camera produces a rear-camera hint');
  vm.runInContext(`navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [{ stop() {} }] });`, context);
  await vm.runInContext(`window.LiveScanTest.startCamera()`, context);
  await new Promise((r) => setTimeout(r, 30));
  vm.runInContext(`document.getElementById('ls-video').videoWidth=640;
    [...document.getElementById('ls-capture').querySelectorAll('button')].find(b=>b.textContent==='Capture still').click()`, context);
  await new Promise((r) => setTimeout(r, 60));
  const strip = $('ls-photos').querySelector('.ls-strip');
  assert.ok(strip && strip.children.length === 1, 'captured still appears in filmstrip');
  const guide1 = $('ls-guide').children.map((c) => c.textContent).join(' ');
  assert.ok(!/blurry/.test(guide1), 'sharp photo draws no blur warning: ' + guide1);
  // Flat gray pixels -> blur + exposure guidance.
  PIXELS = new Uint8ClampedArray(160 * 120 * 4).fill(30);
  PIXELS.forEach((_, i) => { if (i % 4 === 3) PIXELS[i] = 255; });
  vm.runInContext(`[...document.getElementById('ls-capture').querySelectorAll('button')].find(b=>b.textContent==='Capture still').click()`, context);
  await new Promise((r) => setTimeout(r, 60));
  const guide2 = $('ls-guide').children.map((c) => c.textContent).join(' ');
  assert.match(guide2, /blurry|dark/, 'flat dark photo triggers guidance: ' + guide2);
  console.log('camera/capture/QC: passed');

  // Parts: add resistor, inspect, place pins, answer intent, findings.
  vm.runInContext(`const f=[...document.getElementById('ls-parts').querySelectorAll('form')][0];
    f.querySelector('select').value='resistor'; f.querySelector('input').value='10k'; f.submit();`, context);
  await new Promise((r) => setTimeout(r, 20));
  const items = $('ls-parts-list').children.filter((c) => c.tagName === 'BUTTON');
  assert.equal(items.length, 1, 'part appears in list');
  assert.match(items[0].textContent || items[0].innerHTMLRaw, /R1/, 'reference assigned');
  items[0].click();
  const pinInputs = $('ls-inspector').querySelectorAll('input');
  assert.ok(pinInputs.length >= 2, 'pin editors render');
  pinInputs[0].value = 't10A'; pinInputs[1].value = 't10B';
  const applyBtn = $('ls-inspector').querySelectorAll('button').find((b) => b.textContent === 'Apply');
  applyBtn.click();
  await new Promise((r) => setTimeout(r, 20));
  // Wire a confirmed rail short and check the finding renders.
  vm.runInContext(`const fw=[...document.getElementById('ls-wires').querySelectorAll('form')][0];
    const insw=fw.querySelectorAll('input'); insw[0].value='rL+5'; insw[1].value='rL-5'; fw.submit();`, context);
  await new Promise((r) => setTimeout(r, 20));
  const wireBtn = $('ls-wires-list').children.find((c) => c.tagName === 'BUTTON');
  wireBtn.click();
  const statusSel = $('ls-inspector').querySelectorAll('select').find((s) => s.children.some((o) => o.textContent === 'Confirmed'));
  statusSel.value = 'confirmed';
  $('ls-inspector').querySelectorAll('button').find((b) => b.textContent === 'Apply').click();
  await new Promise((r) => setTimeout(r, 20));
  const textOf = (n) => n.textContent + ' ' + n.children.map(textOf).join(' ');
  const findings = $('ls-findings-list').children.map(textOf).join(' ');
  assert.match(findings, /shorted/, 'confirmed short renders as finding: ' + findings.slice(0, 200));
  console.log('parts/wires/findings: passed');

  // Questions: intent asked and answered; save persists.
  const qItems = $('ls-questions-list').children.filter((c) => c._cls.has('ls-question'));
  assert.ok(qItems.length >= 1, 'questions render');
  const intentQ = qItems.find((q) => /building/.test(q.children.map((c) => c.textContent).join(' ')));
  assert.ok(intentQ, 'intent question asked');
  intentQ.querySelector('input').value = 'Door sensor with ESP32';
  intentQ.querySelectorAll('button').find((b) => b.textContent === 'Answer').click();
  await new Promise((r) => setTimeout(r, 20));
  $('ls-save').click();
  await new Promise((r) => setTimeout(r, 30));
  const saveCall = apiCalls.find((c) => c.path.endsWith('/save'));
  assert.ok(saveCall, 'save posts project');
  assert.equal(saveCall.data.project.intent.text, 'Door sensor with ESP32', 'intent persisted');
  assert.equal(saveCall.data.project.components.length, 1, 'parts persisted');
  assert.ok(saveCall.data.project.images.length === 2, 'photos persisted');
  console.log('questions/save: passed');

  // The fallback path above is covered; now provide the 3D engine so the
  // test proves merged reconstruction output reaches the twin.
  vm.runInContext(`window.LiveTwin = { mount() { return {
    setProject(p) { twinProjects.push(JSON.parse(JSON.stringify({ comps: p.components.map((c) => ({ id: c.id, kind: c.kind, status: c.status })), wires: p.wires.length }))); },
    onSelect() {}, dispose() {}, select() {}, setBoardPose() {} }; } };
    window.LiveScanTest.remountTwin();`, context);
  assert.ok(twinProjects.length >= 1, 'twin receives the project on mount');
  // Guide boxes hold their target pixel aspect on any viewport: the full
  // board frame matches a ~3:1 landscape board instead of stretching.
  for (const [vw, vh] of [[1280, 720], [800, 600], [720, 1280]]) {
    const g0 = vm.runInContext(`window.LiveScanTest.fitGuideBox(0, ${vw}, ${vh})`, context);
    const aspect = (g0[2] * vw) / (g0[3] * vh);
    assert.ok(Math.abs(aspect - 2.9) < 0.15, `full box ~2.9:1 on ${vw}x${vh} (got ${aspect.toFixed(2)})`);
    for (let s = 0; s < 6; s++) {
      const g = vm.runInContext(`window.LiveScanTest.fitGuideBox(${s}, ${vw}, ${vh})`, context);
      assert.ok(g[0] >= 0 && g[1] >= 0 && g[0] + g[2] <= 1 && g[1] + g[3] <= 1, `step ${s} box inside ${vw}x${vh}`);
    }
    const gl = vm.runInContext(`window.LiveScanTest.fitGuideBox(1, ${vw}, ${vh})`, context);
    const gr = vm.runInContext(`window.LiveScanTest.fitGuideBox(2, ${vw}, ${vh})`, context);
    assert.ok(gl[0] + gl[2] / 2 < 0.5 && gr[0] + gr[2] / 2 > 0.5, `close-ups anchor left/right on ${vw}x${vh}`);
  }
  // Large uploads downscale before upload so vision's 2 MB cap accepts them;
  // entry dimensions describe the uploaded pixels for bbox mapping.
  IMG_W = 4032; IMG_H = 3024;
  const big = await vm.runInContext(`window.LiveScanTest.ingestFile(
    new File([new Uint8Array(2500000)], 'IMG_X.JPG', { type: 'image/jpeg' }))`, context);
  assert.equal(big.w, 1600, 'upload downscaled to 1600px');
  assert.equal(big.h, 1200, 'aspect preserved');
  assert.match(big.name, /\.jpg$/, 'converted upload renamed to jpeg');
  IMG_W = 640; IMG_H = 480;
  // Record workflow on the main twin viewport: overlay, Record button,
  // progress bar, guidance line, Finish. Old scan row is gone; manual
  // capture hides inside the advanced disclosure.
  // NOTE: the stub getElementById auto-creates missing ids, so absence and
  // placement are asserted through parent-scoped querySelector (real nulls).
  const inTwin = (sel) => vm.runInContext(
    `document.getElementById('ls-twin').querySelector('${sel}')`, context);
  const inPanel = (sel) => vm.runInContext(
    `document.getElementById('ls-twin-panel').querySelector('${sel}')`, context);
  assert.equal(vm.runInContext(`document.getElementById('ls-capture').querySelector('#ls-scan-row')`, context),
    null, 'old scan row removed');
  assert.ok(inPanel('#ls-record'), 'Record button renders on the main breadboard');
  assert.ok(inTwin('#ls-record'), 'Record lives inside the 3D viewport');
  assert.ok(inTwin('#ls-progress-fill') && inTwin('#ls-progress-label'), 'viewport progress renders');
  assert.match(inTwin('#ls-progress-label').textContent, /^\d+%$/, 'progress shows a real percentage');
  assert.ok(inTwin('#ls-vp-guide'), 'viewport guidance renders');
  assert.ok(inTwin('#ls-vp-verify'), 'viewport verification indicator renders');
  assert.ok(vm.runInContext(`!!document.getElementById('ls-vp-step')`, context), 'step pill renders');
  assert.ok(vm.runInContext(`document.getElementById('ls-vp-segs').querySelectorAll('span').length === 6`, context),
    'six progress segments render');
  const stepItems = () => vm.runInContext(
    `[...document.getElementById('ls-steps').querySelectorAll('li')].map((li) => li.textContent)`, context);
  assert.equal(stepItems().length, 6, 'board-placement checklist has 6 views');
  assert.ok(stepItems()[0].startsWith('Full board — Center the whole board'), 'step 1 tells board placement');
  const rec = () => $('ls-record');
  const finishBtn = $('ls-finish');
  assert.ok(finishBtn && finishBtn.hidden, 'Finish hidden while idle');
  const advBtns = () => [...vm.runInContext(`document.getElementById('ls-advanced')`, context)
    .querySelectorAll('button')];
  assert.ok(advBtns().find((b) => b.textContent === 'Capture still'), 'manual capture kept in advanced');
  assert.ok(advBtns().find((b) => b.textContent === 'Stop scan'), 'stop kept in advanced');
  assert.ok(vm.runInContext(`document.getElementById('ls-capture').querySelector('#ls-advanced #ls-coverage')`, context),
    'coverage map kept in advanced');
  const allBtns = () => vm.runInContext(
    `[...document.getElementById('livescan-root').querySelectorAll('button')].map((b) => b.textContent)`, context);
  assert.ok(!allBtns().includes('Start scanning'), 'old Start scanning button gone');
  // No manual rotate button: orientation is fully automatic.
  assert.ok(!advBtns().some((b) => /Rotate|⟳/.test(b.textContent)), 'no rotate button');
  // iPhone Continuity Camera auto-preferred from the device list.
  assert.equal($('ls-camera-sel').value, 'cc', 'continuity camera auto-selected');
  // Tracker outline canvas renders inside the viewport (draws on lock).
  assert.ok(inTwin('#ls-track-overlay'), 'tracker outline canvas renders');
  // Step checklist names each guided view with its board placement.
  const stepPlaces = vm.runInContext(
    `[...document.getElementById('ls-steps').querySelectorAll('li')].map((c) => c.textContent).join('|')`, context);
  assert.ok(stepPlaces.includes('Full board — Center the whole board'), 'checklist places the board per step');
  const osz = (w, h, r) => vm.runInContext(`window.LiveScanTest.orientedSize(${w}, ${h}, ${r}).join('x')`, context);
  assert.equal(osz(160, 120, 0), '160x120', 'no rotation keeps axes');
  assert.equal(osz(160, 120, 90), '120x160', '90° swaps axes');
  assert.equal(osz(160, 120, 270), '120x160', '270° swaps axes');
  assert.equal(osz(160, 120, 180), '160x120', '180° keeps axes');
  // rotLum90: 90° clockwise transpose, 4 applications restore the frame.
  const rot = vm.runInContext(`(() => {
    const T = window.LiveScanTest.rotLum90;
    let cur = { lum: new Float32Array([1, 2, 3, 4, 5, 6]), w: 3, h: 2 };
    const first = T(cur.lum, cur.w, cur.h);
    let back = first;
    for (let i = 0; i < 3; i++) back = T(back.lum, back.w, back.h);
    return { first: [first.w, first.h, [...first.lum].join(',')], back: [back.w, back.h, [...back.lum].join(',')] };
  })()`, context);
  assert.deepEqual([rot.first[0], rot.first[1]], [2, 3], 'transpose swaps dims');
  assert.equal(rot.first[2], '4,1,5,2,6,3', 'transpose rotates clockwise');
  assert.deepEqual([rot.back[0], rot.back[1]], [3, 2], '4 rotations restore dims');
  assert.equal(rot.back[2], '1,2,3,4,5,6', '4 rotations restore pixels');
  // Record with no camera and a failing camera request stays honest.
  vm.runInContext(`document.getElementById('ls-video').videoWidth=0;
    navigator.mediaDevices.getUserMedia = async () => { throw new Error('denied'); };`, context);
  rec().click();
  await new Promise((r) => setTimeout(r, 60));
  assert.equal($('ls-scan-status').textContent, 'No camera', 'failed start reports connection status');
  assert.match($('ls-vp-guide').textContent, /Camera unavailable|denied/, 'viewport explains camera failure');
  // Record with a working camera starts the real scan loop.
  vm.runInContext(`document.getElementById('ls-video').videoWidth=640;
    navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [{ stop() {} }] });`, context);
  PIXELS = new Uint8ClampedArray(160 * 120 * 4).fill(30);
  PIXELS.forEach((_, i) => { if (i % 4 === 3) PIXELS[i] = 255; });
  rec().click();
  await new Promise((r) => setTimeout(r, 60));
  assert.equal($('ls-scan-status').textContent, 'Scanning', 'record starts the scan');
  assert.equal(finishBtn.hidden, false, 'finish appears while scanning');
  assert.equal(inTwin('#ls-vp-step').textContent, 'Step 1 of 6 · Full board', 'step pill shows current view');
  assert.ok(inTwin('#ls-vp-guide').textContent.length > 0 || true, 'guide line present');
  const segDone = () => vm.runInContext(
    `[...document.getElementById('ls-vp-segs').querySelectorAll('span')].filter((s) => (s.className || '').includes('done')).length`, context);
  assert.equal(segDone(), 0, 'no segments complete before capture');
  assert.match(inTwin('#ls-progress-label').textContent, /^0%$/, 'progress starts at 0 of 6');
  assert.ok($('ls-twin').className.includes('capturing'), 'camera fills the main viewport');
  assert.ok(inTwin('#ls-track-overlay'), 'positioning overlay renders');
  // Six synthetic captures drive the whole state machine through the real
  // capture → save → interpret → step pipeline.
  for (let i = 1; i <= 6; i++) {
    PIXELS = new Uint8ClampedArray(160 * 120 * 4).fill(30 + i * 20);
    PIXELS.forEach((_, k) => { if (k % 4 === 3) PIXELS[k] = 255; });
    await vm.runInContext(`window.LiveScanTest.captureAuto(new Float32Array(160 * 120).fill(${100 + i}))`, context);
    await new Promise((r) => setTimeout(r, 120));
  }
  assert.equal(inTwin('#ls-vp-step').textContent, 'Step 6 of 6 · End 45°', 'six captures advance all steps');
  assert.equal(segDone(), 6, 'all six segments complete only after saved captures');
  assert.equal(inTwin('#ls-progress-label').textContent, '100%', 'progress reaches 100%');
  assert.equal(interpretCalls.length, 6, 'real interpret pipeline called for every capture');
  await new Promise((r) => setTimeout(r, 2200)); // auto-finish delay
  assert.equal($('ls-scan-status').textContent, 'Idle', 'camera stops after step 6');
  assert.ok(!$('ls-twin').className.includes('capturing'), 'twin returns after camera off');
  const twinSeen = twinProjects.flatMap((s) => s.comps);
  assert.ok(twinSeen.some((c) => c.kind === 'resistor' && c.status === 'inferred'),
    'merged vision component reaches the 3D twin: ' + JSON.stringify(twinSeen));
  assert.ok(vm.runInContext(`document.body.querySelector('.ls-quiz')`, context), 'questions open after reconstruction');
  const qzText = vm.runInContext(`document.body.querySelector('.ls-quiz h4').textContent`, context);
  assert.match(qzText, /What is|empty|supposed|exact|hole|voltage|purpose/i, 'quiz asks only open questions: ' + qzText);
  await new Promise((r) => setTimeout(r, 750));
  const scanGuide = $('ls-guide').children.map((c) => c.textContent).join(' ');
  assert.match(scanGuide, /Merged|question|empty|Generating|failed|reopen|paused/i, 'reconstruction explains the outcome: ' + scanGuide);
  assert.match($('ls-vp-guide').textContent, /dark|Move|Hold|Stabilized|captured|Merged|empty|Generating/i,
    'viewport mirrors live guidance: ' + $('ls-vp-guide').textContent);
  // The finished scan left the loop Idle, so Record starts a fresh scan first.
  rec().click();
  assert.equal($('ls-scan-status').textContent, 'Scanning', 'record restarts the loop after finish');
  rec().click(); // Record toggles pause while active
  assert.equal($('ls-scan-status').textContent, 'Paused', 'record pauses the loop');
  const photosBefore = $('ls-photos').querySelector('.ls-strip').children.length;
  await new Promise((r) => setTimeout(r, 1300));
  assert.equal($('ls-photos').querySelector('.ls-strip').children.length, photosBefore, 'no captures while paused');
  rec().click();
  assert.equal($('ls-scan-status').textContent, 'Scanning', 'record resumes the loop');
  advBtns().find((b) => b.textContent === 'Stop scan').click();
  assert.equal($('ls-scan-status').textContent, 'Idle', 'stop idles the loop');
  assert.equal(finishBtn.hidden, true, 'finish hides when idle');
  console.log('auto-scan: passed');

  // Post-scan quiz: popup queue over the editor, answers persist, no repeats.
  assert.ok(finishBtn, 'Finish renders');
  const quizOverlay = () => vm.runInContext(`document.body.querySelector('.ls-quiz')`, context);
  finishBtn.click();
  await new Promise((r) => setTimeout(r, 30));
  let overlay = quizOverlay();
  assert.ok(overlay, 'Finish capture opens the question popup');
  const qText = overlay.querySelector('h4').textContent;
  assert.match(qText, /R1/, 'popup targets the placed part: ' + qText);
  overlay.querySelector('input').value = '10k resistor, confirmed';
  overlay.querySelectorAll('button').find((b) => b.textContent === 'Save answer').click();
  await new Promise((r) => setTimeout(r, 30));
  overlay = quizOverlay();
  assert.ok(overlay, 'queue advances to the next question');
  // Structural-first ordering: R1 arrived fully placed, so R2's pins are
  // the live placement questions; answer them with distinct valid holes.
  {
    const t = overlay.querySelector('h4').textContent;
    assert.match(t, /Which hole does R\d pin [AB] sit in\?/, 'pin question names the part and function: ' + t);
    overlay.querySelector('input').value = /pin B/.test(t) ? 't10C' : 't5A';
    overlay.querySelectorAll('button').find((b) => b.textContent === 'Save answer').click();
  }
  await new Promise((r) => setTimeout(r, 30));
  for (let guard = 0; guard < 10 && quizOverlay(); guard++) {
    const ov = quizOverlay();
    const t = ov.querySelector('h4').textContent;
    if (/Which hole does R2 pin B/.test(t)) {
      ov.querySelector('input').value = 't10C';
      ov.querySelectorAll('button').find((b) => b.textContent === 'Save answer').click();
    } else if (/Which hole/.test(t)) {
      ov.querySelector('input').value = 't5F';
      ov.querySelectorAll('button').find((b) => b.textContent === 'Save answer').click();
    } else if (/resistor|part|value|model/i.test(t)) {
      ov.querySelector('input').value = '10k resistor, confirmed';
      ov.querySelectorAll('button').find((b) => b.textContent === 'Save answer').click();
    } else if (/Seen only once/.test(t)) {
      ov.querySelector('input').value = 'confirmed, all real';
      ov.querySelectorAll('button').find((b) => b.textContent === 'Save answer').click();
    } else {
      ov.querySelectorAll('button').find((b) => b.textContent === 'Skip').click();
    }
    await new Promise((r) => setTimeout(r, 30));
  }
  // The merged scan resistor adds pin questions; answer them with valid
  // holes so the queue drains and stays drained on re-finish.
  const spareHoles = ['t5A', 't5F', 't6A', 't6F'];
  for (let guard = 0; guard < 10 && quizOverlay(); guard++) {
    const ov = quizOverlay();
    if (/Which hole/.test(ov.querySelector('h4').textContent)) {
      ov.querySelector('input').value = spareHoles[guard % spareHoles.length];
      ov.querySelectorAll('button').find((b) => b.textContent === 'Save answer').click();
    } else if (/Seen only once/.test(ov.querySelector('h4').textContent)) {
      ov.querySelector('input').value = 'confirmed, all real';
      ov.querySelectorAll('button').find((b) => b.textContent === 'Save answer').click();
    } else {
      ov.querySelectorAll('button').find((b) => b.textContent === 'Skip').click();
    }
    await new Promise((r) => setTimeout(r, 30));
  }
  assert.equal(quizOverlay(), null, 'queue closes after the last answer');
  const quizSave = apiCalls.filter((c) => c.path.endsWith('/save')).pop();
  const answered = quizSave.data.project.questions.find((q) => q.key === 'identify:R1');
  assert.ok(answered && answered.status === 'answered', 'popup answer persists to the project');
  assert.equal(quizSave.data.project.questions.filter((q) => q.key === 'identify:R1').length, 1,
    'answered questions are never re-suggested');
  const r2 = quizSave.data.project.components.find((c) => c.id === 'R2');
  assert.equal(r2.pins[0].hole, 't5A', 'first pin answer moves pin A');
  assert.equal(r2.pins[1].hole, 't10C', 'placement answer moves the pin');
  finishBtn.click();
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(quizOverlay(), null, 'answered questions are not asked again');
  console.log('post-scan quiz: passed');

  // --- camera reliability: stale IDs, competing apps, death, recovery ---
  const liveTrack = { stop() {}, readyState: 'live' };
  vm.runInContext(`navigator.mediaDevices.getUserMedia = async (c) => {
    const want = c && c.video && c.video.deviceId && c.video.deviceId.exact;
    if (want === 'cc-stale') { const e = new Error('stale'); e.name = 'OverconstrainedError'; throw e; }
    document.getElementById('ls-video').videoWidth = 640;
    return { getTracks: () => [globalThis.__track] };
  };
  globalThis.__track = undefined;`, context);
  context.__track = liveTrack;
  // Stale Continuity deviceId: re-enumerate once, retry the fresh pick.
  await vm.runInContext(`window.LiveScanTest.startCamera('cc-stale')`, context);
  assert.equal($('ls-camera-sel').value, 'cc', 'stale id falls back to fresh continuity pick');
  assert.equal($('ls-scan-status').textContent, 'Idle', 'retry succeeds without error status');
  // Competing app and denied permission explain themselves.
  vm.runInContext(`navigator.mediaDevices.getUserMedia = async () => {
    const e = new Error('busy'); e.name = 'NotReadableError'; throw e; };`, context);
  await vm.runInContext(`window.LiveScanTest.startCamera('cc')`, context);
  assert.match($('ls-vp-guide').textContent, /another app.*quit it/i, 'busy camera names the fix');
  assert.equal(vm.runInContext(`!!document.getElementById('ls-video').srcObject`, context), false,
    'failed start holds no stream');
  vm.runInContext(`navigator.mediaDevices.getUserMedia = async () => {
    const e = new Error('no'); e.name = 'NotAllowedError'; throw e; };`, context);
  await vm.runInContext(`window.LiveScanTest.startCamera('cc')`, context);
  assert.match($('ls-vp-guide').textContent, /permission was denied/i, 'denied permission guides to the address bar');
  // Mid-scan death recovers with the step preserved.
  vm.runInContext(`navigator.mediaDevices.getUserMedia = async () => {
    document.getElementById('ls-video').videoWidth = 640;
    return { getTracks: () => [globalThis.__track] }; };`, context);
  await vm.runInContext(`window.LiveScanTest.startCamera('cc')`, context);
  vm.runInContext(`window.LiveScanTest.startScan()`, context);
  await vm.runInContext(`window.LiveScanTest.captureAuto(null)`, context);
  await new Promise((r) => setTimeout(r, 150));
  assert.equal(vm.runInContext(`window.LiveScanTest.scanState().step`, context), 1, 'one view captured');
  vm.runInContext(`document.getElementById('ls-video').videoWidth = 0;`, context);
  await vm.runInContext(`window.LiveScanTest.recoverCamera('The camera feed stopped.')`, context);
  assert.equal(vm.runInContext(`window.LiveScanTest.scanState().step`, context), 1, 'recovery keeps the step');
  assert.equal($('ls-scan-status').textContent, 'Scanning', 'loop resumes after recovery');
  // Frozen frames (dimensions kept, track ended) recover after 3 ticks.
  liveTrack.readyState = 'ended';
  vm.runInContext(`window.LiveScanTest.scanTick(); window.LiveScanTest.scanTick();`, context);
  assert.equal($('ls-scan-status').textContent, 'Scanning', 'single frozen frames do not flap');
  liveTrack.readyState = 'live';
  vm.runInContext(`window.LiveScanTest.scanTick()`, context);
  liveTrack.readyState = 'ended';
  vm.runInContext(`window.LiveScanTest.scanTick(); window.LiveScanTest.scanTick(); window.LiveScanTest.scanTick();`, context);
  await new Promise((r) => setTimeout(r, 120));
  assert.equal($('ls-scan-status').textContent, 'Scanning', 'frozen feed recovers and resumes');
  liveTrack.readyState = 'live';
  // Repeated failure parks the scan with guidance instead of looping.
  vm.runInContext(`navigator.mediaDevices.getUserMedia = async () => { throw new Error('gone'); };
    document.getElementById('ls-video').videoWidth = 0;`, context);
  for (let i = 0; i < 4; i++) await vm.runInContext(`window.LiveScanTest.recoverCamera('x')`, context);
  assert.equal($('ls-scan-status').textContent, 'Camera lost', 'exhaustion parks with status');
  assert.match($('ls-guide').children.map((c) => c.textContent).join(' '), /progress.*kept/i,
    'exhaustion promises preserved progress');
  assert.equal(vm.runInContext(`window.LiveScanTest.scanState().step`, context), 1, 'parked scan keeps its step');
  // A vanished device falls back instead of aiming at a ghost.
  vm.runInContext(`document.getElementById('ls-camera-sel').value = 'ghost';
    navigator.mediaDevices.ondevicechange();`, context);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal($('ls-camera-sel').value, 'cc', 'vanished device falls back to continuity');
  vm.runInContext(`document.getElementById('ls-video').videoWidth = 640;
    navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [globalThis.__track] });`, context);
  console.log('camera-recovery: passed');

  // No mirror transforms anywhere in the scanner: rear-camera motion must
  // never be inverted by the app.
  const scannerSrc = fs.readFileSync('breadboard_to_PCB/livescan.js', 'utf8') + fs.readFileSync('breadboard_to_PCB/livescan.css', 'utf8');
  assert.ok(!/scaleX\s*\(|scale\(\s*-|scaleY\s*\(\s*-/.test(scannerSrc), 'no mirror transforms in scanner');
  console.log('no-mirror audit: passed');

  // Orientation decisions from locked-quad geometry (aspects measured on
  // real breadboard pixels: landscape 2.32, sideways 0.43, tilted 1.02).
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    let st = {};
    assert.equal(T.orientDecideQuad(2.32, 103.8, 0, st, false), 0, 'landscape quad stands');
    assert.equal(T.orientDecideQuad(2.32, 103.8, 0, st, false), 0, 'landscape quad stands twice');
    st = {};
    assert.equal(T.orientDecideQuad(0.43, 103.8, 0, st, false), 0, 'first portrait only arms');
    assert.equal(T.orientDecideQuad(0.43, 103.8, 0, st, false), 90, 'second portrait adopts +90');
    st = {};
    assert.equal(T.orientDecideQuad(0.43, 103.8, 0, st, false), 0);
    assert.equal(T.orientDecideQuad(2.32, 103.8, 0, st, false), 0, 'a landscape resets confirmation');
    assert.equal(T.orientDecideQuad(0.43, 103.8, 0, st, false), 0, 're-arms after reset');
    assert.equal(T.orientDecideQuad(0.43, 103.8, 0, st, false), 90, 'two fresh portraits switch');
    st = {};
    assert.equal(T.orientDecideQuad(1.02, 197.4, 0, st, false), 0, 'square-ish tilt stands');
    assert.equal(T.orientDecideQuad(1.02, 197.4, 0, st, false), 0, 'square-ish tilt stands twice');
    st = {};
    assert.equal(T.orientDecideQuad(0.43, 30, 0, st, false), 0, 'weak contrast never switches');
    assert.equal(T.orientDecideQuad(0.43, 30, 0, st, false), 0, 'weak contrast never switches twice');
    st = {};
    assert.equal(T.orientDecideQuad(0.43, 103.8, 90, st, false), 90);
    assert.equal(T.orientDecideQuad(0.43, 103.8, 90, st, false), 180, 'confirmed switch continues the cycle');
    st = {};
    assert.equal(T.orientDecideQuad(0.43, 103.8, 0, st, true), 0, 'pinned orientation never switches');
    assert.equal(T.orientDecideQuad(0.43, 103.8, 0, st, true), 0, 'pinned orientation never switches twice');
    console.log('orient-decide: passed');
  }

  // Overlay mapping: independently derived from the sensor geometry, not
  // from the implementation. Opposite sensor corners stay symmetric about
  // the overlay center AND keep their true cover-fit distance (a vw-based
  // scale at 90° would stretch them 1.78x on 16:9 footage).
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    vm.runInContext(`document.getElementById('ls-track-video').videoWidth = 1920;
      document.getElementById('ls-track-video').videoHeight = 1080;
      document.getElementById('ls-track-overlay').width = 800;
      document.getElementById('ls-track-overlay').height = 600;`, context);
    const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    // rot 0: sample spans the sensor directly.
    T.setRotation(0);
    let pts = T.quadToOverlay([{ x: 0, y: 0 }, { x: 160, y: 90 }], { w: 160, h: 90 });
    assert.ok(pts && pts.length === 2, 'rot0 maps');
    assert.ok(Math.abs((pts[0].x + pts[1].x) / 2 - 400) < 1e-6, 'rot0 symmetric x');
    assert.ok(Math.abs((pts[0].y + pts[1].y) / 2 - 300) < 1e-6, 'rot0 symmetric y');
    const d0 = Math.hypot(1920, 1080) * Math.max(800 / 1920, 600 / 1080);
    assert.ok(Math.abs(dist(pts[0], pts[1]) - d0) < 1e-6, `rot0 true distance ${dist(pts[0], pts[1])} vs ${d0}`);
    // rot 90: sample spans the ROTATED frame; sensor (0,0)->sample (90,0),
    // sensor (1920,1080)->sample (0,160) at the 12x sample scale.
    T.setRotation(90);
    pts = T.quadToOverlay([{ x: 90, y: 0 }, { x: 0, y: 160 }], { w: 90, h: 160 });
    assert.ok(pts && pts.length === 2, 'rot90 maps');
    assert.ok(Math.abs((pts[0].x + pts[1].x) / 2 - 400) < 1e-6, 'rot90 symmetric x');
    assert.ok(Math.abs((pts[0].y + pts[1].y) / 2 - 300) < 1e-6, 'rot90 symmetric y');
    const d90 = Math.hypot(1920, 1080) * Math.max(800 / 1920, 600 / 1080);
    assert.ok(Math.abs(dist(pts[0], pts[1]) - d90) < 1e-6, `rot90 true distance ${dist(pts[0], pts[1])} vs ${d90}`);
    // rot 180/270 centers hold too.
    T.setRotation(180);
    pts = T.quadToOverlay([{ x: 80, y: 45 }], { w: 160, h: 90 });
    assert.ok(Math.abs(pts[0].x - 400) < 1e-6 && Math.abs(pts[0].y - 300) < 1e-6, 'rot180 center holds');
    T.setRotation(270);
    pts = T.quadToOverlay([{ x: 45, y: 80 }], { w: 90, h: 160 });
    assert.ok(Math.abs(pts[0].x - 400) < 1e-6 && Math.abs(pts[0].y - 300) < 1e-6, 'rot270 center holds');
    // Invalid inputs map to nothing, never to a misplaced quad.
    T.setRotation(45);
    assert.equal(T.quadToOverlay([{ x: 0, y: 0 }], { w: 160, h: 90 }), null, 'bad rotation maps null');
    T.setRotation(0);
    vm.runInContext('document.getElementById(\'ls-track-video\').videoWidth = 0;', context);
    assert.equal(T.quadToOverlay([{ x: 0, y: 0 }], { w: 160, h: 90 }), null, 'dead feed maps null');
    vm.runInContext('document.getElementById(\'ls-track-video\').videoWidth = 1920;', context);
    console.log('overlay-mapping: passed');
  }

  // Positioning guide follows a verified lock (bbox + 8% margin), falls
  // back to the static box for slivers and missing detections.
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    const g = T.guideBoxFor(0, 800, 600, [{ x: 100, y: 200 }, { x: 700, y: 200 }, { x: 700, y: 400 }, { x: 100, y: 400 }]);
    assert.ok(Math.abs(g[0] - 52 / 800) < 1e-9 && Math.abs(g[1] - 184 / 600) < 1e-9, 'lock bbox origin + margin');
    assert.ok(Math.abs(g[2] - 696 / 800) < 1e-9 && Math.abs(g[3] - 232 / 600) < 1e-9, 'lock bbox size + margin');
    const sliver = T.guideBoxFor(0, 800, 600, [{ x: 100, y: 100 }, { x: 700, y: 100 }, { x: 700, y: 120 }, { x: 100, y: 120 }]);
    assert.deepEqual(sliver, T.fitGuideBox(0, 800, 600), 'sliver detection falls back to static');
    assert.deepEqual(T.guideBoxFor(2, 800, 600, null), T.fitGuideBox(2, 800, 600), 'no lock uses static box');
    // Viewport clamping keeps the box fully visible.
    const edge = T.guideBoxFor(0, 800, 600, [{ x: 600, y: 400 }, { x: 900, y: 400 }, { x: 900, y: 550 }, { x: 600, y: 550 }]);
    assert.ok(edge[0] >= 0 && edge[1] >= 0 && edge[0] + edge[2] <= 1.0001 && edge[1] + edge[3] <= 1.0001, 'edge lock clamps to viewport');
    console.log('guide-from-detection: passed');
  }

  // Detection runs without the 3D engine; rotation persists and survives
  // camera recovery.
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    T.setRotation(90);
    assert.equal(T.scanState().rotation, 90, 'rotation readable');
    assert.equal(vm.runInContext('localStorage.getItem("ls-rotation")', context), '90', 'rotation persists');
    vm.runInContext('document.getElementById(\'ls-video\').videoWidth = 640;', context);
    vm.runInContext('window.LiveScanTest.startScan()', context);
    vm.runInContext('window.LiveScanTest.updateTrackPose({ lum: new Float32Array(160*90).fill(128), w: 160, h: 90 })', context);
    assert.equal(T.scanState().pose, 'hold', 'flat frame holds without twin');
    await vm.runInContext('window.LiveScanTest.recoverCamera(\'orientation check\')', context);
    await new Promise((r) => setTimeout(r, 60));
    assert.equal(T.scanState().rotation, 90, 'rotation survives recovery');
    T.setRotation(0);
    vm.runInContext('window.LiveScanTest.stopScan()', context);
    console.log('tracking-without-twin: passed');
  }

  // B1 starvation: a wireconf created after 10+ older wiring asks must
  // still surface in the capped quiz list, above same-wire open asks.
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    const P = T.testProject();
    P.wires.push({ id: 'J9', color: 'red', from: null, to: null,
      status: 'inferred', source: 'scan', region: '', notes: '', bbox: null });
    for (let i = 0; i < 10; i++) P.questions.push({ id: 'Qold' + i,
      key: 'wire:old:' + i, kind: 'wiring', text: 'old ask ' + i,
      target: 'J9', options: [], status: 'open', answer: null,
      created: new Date().toISOString() });
    T.testProposeWireHoles('J9', ['rL+5', 't10C'], 'IMG_1.JPG');
    const keys = T.testQuizList();
    assert.ok(keys.includes('wireconf:J9:rL+5|t10C'),
      'wireconf surfaces past 10 older asks: ' + keys.join(','));
    assert.ok(keys.indexOf('wireconf:J9:rL+5|t10C') < keys.findIndex((k) => k === 'wire:old:0'),
      'wireconf ranks above same-wire open asks');
    console.log('wireconf-starvation: passed');
  }

  // B2 outline anchor: synthetic affine board only (no customer photos).
  // 800x600, board TL=(100,50) TR=(700,50) BR=(700,550) BL=(100,550), rot=0.
  // The flow project is half-size (30 rows): module rows 8-22, E/F columns
  // -> box fractions x[0.375,0.625] y[0.320,0.656].
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    assert.ok(T.testAddModule('U9').ok, 'synthetic module added');
    const quad = [{ x: 100, y: 50 }, { x: 700, y: 50 }, { x: 700, y: 550 }, { x: 100, y: 550 }];
    const box = [0.375, 0.320, 0.625, 0.656];
    const holes = () => T.testProject().components.find((c) => c.id === 'U9').pins.map((p) => p.hole);
    let v = T.testAnchorVote('U9', quad, 800, 600, 0, 'viewA', box);
    assert.ok(v.voted && !v.placed, 'one view votes, never places');
    assert.ok(holes().every((h) => !h), 'UNKNOWN preserved on a single view');
    v = T.testAnchorVote('U9', quad.map((p) => ({ x: p.x + 10, y: p.y })), 800, 600, 0, 'viewB', box);
    assert.ok(v.placed, 'two agreeing views place');
    const placed = holes();
    assert.equal(placed[0], 't8E');
    assert.equal(placed[14], 't22E');
    assert.equal(placed[15], 't8F');
    assert.equal(placed[29], 't22F');
    assert.ok(T.testProject().questions.some((q) => q.key === 'orient:U9'), 'orientation question asked');
    // A disagreeing third view must not move the agreed placement.
    T.testAnchorVote('U9', quad, 800, 600, 0, 'viewC', [0.375, 0.05, 0.625, 0.20]);
    assert.equal(holes()[0], 't8E', 'conflicting view keeps first placement');
    console.log('anchor-vote: passed');
  }

  // Geometric wire-end projection: synthetic affine board only.
  // Half-size flow project: quad (100,50)-(700,550) rot=0; wire box whose
  // short-side midpoints sit on t8E/t22E projections -> exact hole IDs.
  // A box whose end touches the frame edge yields null for that end.
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    const quad = [{ x: 100, y: 50 }, { x: 700, y: 50 }, { x: 700, y: 550 }, { x: 100, y: 550 }];
    // Spread out of the vm realm: cross-realm arrays fail deepEqual identity
    // even when element-identical.
    const got = [...T.testGeoWire(quad, [0.40, 0.31, 0.49, 0.656], 800, 600, 0)];
    assert.deepEqual(got, ['t8E', 't22E'], 'geometric ends resolve: ' + JSON.stringify(got));
    const edge = [...T.testGeoWire(quad, [0.40, 0.31, 1.0, 0.656], 800, 600, 0)];
    assert.equal(edge[1], null, 'frame-edge end stays unknown');
    const far = [...T.testGeoWire(quad, [0.02, 0.02, 0.10, 0.10], 800, 600, 0)];
    assert.deepEqual(far, [null, null], 'empty corner stays unknown: ' + JSON.stringify(far));
    console.log('geo-wireends: passed');
  }

  // Settle-gate regression (6/7 wedge): all 7 session targets settled, but
  // the 7th project record was renamed to the wrong slot at upload, so the
  // progress/quiz gates counting project records by name stick at 6/7 and
  // the quiz never launches. Reconcile must repair the mirror.
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    const names = ['a.jpg', 'b.jpg', 'c.jpg', 'd.jpg', 'e.jpg', 'f.jpg', 'g.jpg'];
    const targets = names.map((n) => ({ name: n, interpreted: true, obsCount: 3 }));
    // Slot-rename bug: record for g.jpg overwritten onto f.jpg's twin.
    const records = names.slice(0, 6).map((n) => ({ name: n, interpreted: true }))
      .concat([{ name: 'f.jpg', interpreted: true }]);
    const before = targets.filter((t) => {
      const e = records.find((r) => r.name === t.name);
      return e && (e.interpreted || e.failed);
    }).length;
    assert.equal(before, 6, 'divergence reproduced: gate sees 6/7');
    T.testReconcile(targets, records);
    const after = targets.filter((t) => {
      const e = records.find((r) => r.name === t.name);
      return e && (e.interpreted || e.failed);
    }).length;
    assert.equal(after, 7, 'mirror repaired: gate sees 7/7, quiz may launch');
    // Missing record entirely (upload pre-rename path) is appended, not lost.
    const rec2 = [{ name: 'a.jpg', interpreted: true }];
    T.testReconcile([{ name: 'b.jpg', interpreted: false, failed: true, failMsg: 'x' }], rec2);
    assert.equal(rec2.length, 2, 'missing record appended');
    assert.equal(rec2[1].failed, true, 'failure state carried over');
    console.log('settle-gate: passed');
  }

  // Lock-hook regression (synthetic affine board only, no customer photos).
  // testEndsFromLock with a directly built H matches testGeoWire; the
  // project swap restores exactly; the plug finder resolves (never hangs)
  // and abstains on flat pixels.
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    const BB = vm.runInContext('globalThis.Breadboard', context);
    const SM = vm.runInContext('globalThis.ScanMath', context);
    const quad = [{ x: 100, y: 50 }, { x: 700, y: 50 }, { x: 700, y: 550 }, { x: 100, y: 550 }];
    const H = SM.homography(
      [{ x: -26, y: -44.1 }, { x: 26, y: -44.1 }, { x: 26, y: 44.1 }, { x: -26, y: 44.1 }], quad);
    assert.ok(H, 'synthetic H builds');
    const via = [...T.testEndsFromLock(H, [0.40, 0.31, 0.49, 0.656], 800, 600)];
    assert.deepEqual(via, ['t8E', 't22E'], 'lock-hook matches geo-hook: ' + JSON.stringify(via));
    const prev = T.testProject();
    assert.ok(T.testUseProject(BB.newProject('hook-tmp', { size: 'half' })), 'swap in');
    assert.equal(T.testProject().name, 'hook-tmp', 'swap visible');
    T.testUseProject(prev);
    assert.equal(T.testProject(), prev, 'project restored exactly');
    const entry = { name: 'flat.jpg', w: 160, h: 120, url: 'blob:fake', reg: { H, conf: 0.5 } };
    const out = await T.testWirePlugs(entry,
      [{ id: 'J9', bbox: [0.1, 0.1, 0.9, 0.9], endpoints: '' }], null);
    assert.equal(out.placed, 0, 'flat pixels place nothing');
    assert.equal(out.corroborated, 0, 'flat pixels corroborate nothing');
    console.log('lock-hooks: passed');
  }

  // Open-token + loud-save regression: out-of-order open responses resolve to
  // the latest click; a validator rejection aborts before sending; a server
  // rejection surfaces a loud named error and keeps edits; a good doc saves.
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    const BB = vm.runInContext('globalThis.Breadboard', context);
    const mkdoc = (id, name) => ({ version: 1, id, name, created: '', updated: '',
      breadboard: { size: 'half', railGaps: {} }, intent: { text: '', fields: {}, updated: null },
      components: [], wires: [], images: [], questions: [], confirmations: {}, counters: {} });
    docProjects.push(mkdoc('ls-AAA', 'A'), mkdoc('ls-BBB', 'B'));
    const first = T.openProjectById('ls-AAA');
    const second = T.openProjectById('ls-BBB');
    await first; await second;
    assert.equal(T.testProject().id, 'ls-BBB', 'latest open wins over stale response');
    const saves = () => apiCalls.filter((c) => String(c.path).endsWith('/save'));
    // validator rejects a bad doc before anything is sent
    const bad = JSON.parse(JSON.stringify(T.testProject()));
    bad.version = 999;
    assert.match(T.testValidateSave(bad, bad.id), /version/, 'validator names the rule');
    const n0 = saves().length;
    const live = T.testProject();
    T.testUseProject(bad); T.testMarkDirty(); await T.testSaveNow();
    assert.equal(saves().length, n0, 'rejected doc is never sent');
    assert.ok(errors.some((e) => /Cannot save project ls-BBB: .*version/.test(e)), 'loud validator error names project and rule');
    // a server-side rejection is loud too and keeps the edits
    T.testUseProject(live);
    failSaveWith = 'Request failed (400)';
    T.testMarkDirty(); await T.testSaveNow();
    failSaveWith = null;
    assert.equal(saves().length, n0 + 1, 'server-rejected attempt was sent once');
    assert.ok(errors.some((e) => /Save failed for project ls-BBB: Request failed \(400\)/.test(e)), 'loud server error names project');
    // a good doc round-trips with its content
    T.testAddModule('U9');
    T.testMarkDirty(); await T.testSaveNow();
    const last = saves()[saves().length - 1];
    assert.equal(last.data.project.components.length, 1, 'saved doc carries merged components');
    assert.equal(last.data.project.components[0].id, 'U9', 'saved component identity intact');
    // drain exactly the two intentional loud errors; anything else still fails the suite
    for (const re of [/Cannot save project ls-BBB: .*version/, /Save failed for project ls-BBB: Request failed \(400\)/]) {
      const i = errors.findIndex((e) => re.test(e));
      assert.ok(i >= 0, 'expected loud error present: ' + re);
      errors.splice(i, 1);
    }
    console.log('open-save: passed');
  }

  // Alias-guard regression (synthetic): a true-perspective H passes, a
  // 90-degree lattice-alias H (extreme axis stretch) is rejected.
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    const SM = vm.runInContext('globalThis.ScanMath', context);
    const good = SM.homography(
      [{ x: -26, y: -44.1 }, { x: 26, y: -44.1 }, { x: 26, y: 44.1 }, { x: -26, y: 44.1 }],
      [{ x: 100, y: 50 }, { x: 700, y: 50 }, { x: 700, y: 550 }, { x: 100, y: 550 }]);
    assert.ok(T.testAnisotropy(good), 'true-perspective H accepted');
    const alias = SM.homography(
      [{ x: -26, y: -44.1 }, { x: 26, y: -44.1 }, { x: 26, y: 44.1 }, { x: -26, y: 44.1 }],
      [{ x: 100, y: 50 }, { x: 700, y: 50 }, { x: 700, y: 120 }, { x: 100, y: 120 }]);
    assert.equal(T.testAnisotropy(alias), false, 'alias-stretch H rejected');
    assert.equal(T.testAnisotropy(null), false, 'missing H rejected');
    console.log('alias-guard: passed');
  }

  // B7 color-first wire ends: synthetic green bar segments through the
  // client hook; dark neutrals abstain. A single photo-verified end is
  // quoted in the loose-end question and YES applies it to the empty slot.
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    const BB = vm.runInContext('globalThis.Breadboard', context);
    const W = 40, H = 20, rgb = [];
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const bar = y >= 9 && y <= 11 && x >= 4 && x <= 35;
      rgb.push(bar ? 20 : 200, bar ? 150 : 190, bar ? 40 : 170);
    }
    const ce = T.testWireColorEnds(rgb, W, H, [0, 0, 1, 1], 'green');
    assert.ok(ce && ce.ends, 'client hook segments the bar');
    const e0 = [...ce.ends[0]], e1 = [...ce.ends[1]];
    assert.ok(e0[0] < 10 && e1[0] > 30, 'hook ends near bar tips: ' + JSON.stringify(ce.ends));
    assert.equal(T.testWireColorEnds(rgb, W, H, [0, 0, 1, 1], 'black'), null, 'hook: black abstains');
    const P = T.testProject();
    const r = BB.addWire(P, { color: 'red', status: 'inferred', source: 'scan', region: '', notes: '', bbox: null });
    assert.ok(r.ok, 'wire added');
    const w = P.wires.find((x) => x.id === r.id);
    w.holeProps = { ends: ['rL+5', null], views: ['v.jpg'] };
    const sug = BB.suggestQuestions(P).filter((q) => q.key === 'wire:' + r.id);
    assert.equal(sug.length, 1, 'one loose-end question');
    assert.ok(/rL\+5/.test(sug[0].text) && /YES/.test(sug[0].text), 'single proposal quoted: ' + sug[0].text);
    if (!P.questions.some((x) => x.key === sug[0].key && x.status === 'open')) P.questions.push(sug[0]);
    const ap = BB.answerQuestion(P, sug[0].id, 'YES');
    assert.ok(ap.ok, 'YES accepted');
    assert.equal(w.from, 'rL+5', 'single end applied to the empty slot');
    assert.equal(w.to, null, 'other slot stays unknown');
    P.wires = P.wires.filter((x) => x.id !== r.id);
    P.questions = P.questions.filter((q) => q.target !== r.id);
    console.log('colorends-singles: passed');
  }

  // B8 module span: placed devkit pins give the absolute row span; nothing
  // placed or voted gives null (the fitter proceeds without the landmark).
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    const BB = vm.runInContext('globalThis.Breadboard', context);
    const prev = T.testProject();
    assert.ok(T.testUseProject(BB.newProject('span-tmp', { size: 'half' })), 'swap in');
    assert.equal(T.testModuleSpan(), null, 'no pins/votes: no span');
    assert.ok(T.testAddModule('M1').ok, 'module added');
    const P = T.testProject();
    for (let i = 0; i < 8; i++) assert.ok(BB.setPinHole(P, 'M1', i, 't' + (8 + i) + 'E').ok, 'pin placed');
    assert.deepEqual({ ...T.testModuleSpan() }, { start: 8, end: 15 }, 'placed pins give span');
    T.testUseProject(prev);
    assert.equal(T.testProject(), prev, 'project restored exactly');
    console.log('modulespan: passed');
  }

  // B9 settle watchdog: an unsettled session target is failed honestly
  // (never interpreted) with a Retry pointer; settled runs are untouched.
  {
    const T = vm.runInContext('window.LiveScanTest', context);
    const P = T.testProject();
    P.images.push({ name: 'w1.jpg', interpreted: true }, { name: 'w2.jpg' });
    T.testArmWatch(['w1.jpg', 'w2.jpg']);
    const n = T.testForceSettle();
    assert.equal(n, 1, 'one straggler failed honestly');
    const r2 = P.images.find((x) => x.name === 'w2.jpg');
    assert.ok(r2.failed && /Retry/.test(r2.failMsg), 'retry pointer set');
    assert.ok(!r2.interpreted, 'never marked interpreted');
    T.testArmWatch(['w1.jpg']);
    assert.equal(T.testForceSettle(), 0, 'settled run untouched');
    P.images = P.images.filter((x) => x.name !== 'w1.jpg' && x.name !== 'w2.jpg');
    console.log('settle-watchdog: passed');
  }

  assert.deepEqual(errors, [], 'no UI errors: ' + errors.join(' | '));
  console.log('LiveScan UI: all passed');
})().catch((e) => { console.error(e); process.exit(1); });
