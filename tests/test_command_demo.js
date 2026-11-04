const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class El {
  constructor(tag) { this.tag = tag; this.children = []; this._text = ''; this.dataset = {}; this.listeners = {}; this.classList = { add() {}, remove() {}, toggle() {} }; }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); if (v === '') this.children = []; }
  set innerHTML(v) { this.children = []; }
  setAttribute() {}
  append(...c) { this.children.push(...c); }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  get text() { return [this._text, ...this.children.map(c => c.text)].join(' '); }
}
const MINIMAL = {
  status: 'completed', model: 'meta/muse-spark-1.3-contributor',
  pipeline: {
    status: 'limited', elapsed_seconds: 1, product_hypothesis: 'h', limitations: [], product_model: {},
    steps: [], tasks: [], coverage_proof: { by_status: { supported: 1 }, by_origin: { scope: 1 }, by_mode: {} },
    ledger: { claims: [{ id: 'H1', origin: 'scope', statement: 's', scope: { components: ['U1'], nets: [], pins: [] }, evidence: [], assumptions: [], agent_sureness: 50, status: 'supported', verification: 'v', open_questions: [] }], events: [] },
    replay: [{ seq: 1, kind: 'hypothesized', actor: 'scope', title: 't', detail: 'd' }]
  },
  result: { actions: [], questions: [], summary: 's', limitations: [] }, usage: {}, request_usage: {}
};
async function runCase(search, investigation) {
  const registry = {};
  const calls = [];
  const context = vm.createContext({
    window: {}, location: { search }, matchMedia: () => ({ matches: false }),
    requestAnimationFrame: () => {}, setInterval: () => 0,
    URLSearchParams, document: {
      createElementNS: (ns, tag) => new El(tag),
      getElementById: id => registry[id] || (registry[id] = new El(id)),
      activeElement: null
    },
    fetch: async url => {
      calls.push(String(url));
      if (String(url).includes('/api/investigation')) return { json: async () => investigation };
      return { json: async () => JSON.parse(JSON.stringify(MINIMAL)) };
    }
  });
  vm.runInContext(fs.readFileSync('web/command.js', 'utf8'), context);
  await new Promise(r => setTimeout(r, 150));
  const banner = registry['src-banner'] ? registry['src-banner'].textContent : '';
  return { calls, banner };
}
(async () => {
  // No live data and no ?demo=1: fixture must NOT load.
  let r = await runCase('', { status: 'empty' });
  assert.ok(!r.calls.some(u => u.includes('command-replay.json')), 'fixture fetched without ?demo=1');
  assert.match(r.banner, /No investigation recorded yet/);
  // Explicit ?demo=1: fixture loads and is labeled demo data.
  r = await runCase('?demo=1', { status: 'empty' });
  assert.ok(r.calls.some(u => u.includes('command-replay.json')), 'fixture not fetched with ?demo=1');
  assert.match(r.banner, /demo data/);
  // Live investigation: no fixture fetch.
  r = await runCase('', JSON.parse(JSON.stringify(MINIMAL)));
  assert.ok(!r.calls.some(u => u.includes('command-replay.json')), 'fixture fetched despite live data');
  assert.match(r.banner, /Live/);
  console.log('Command Center demo gating: fixture isolated from live view passed');
})().catch(e => { console.error(e); process.exit(1); });
