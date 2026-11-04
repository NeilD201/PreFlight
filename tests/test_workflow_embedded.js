// The embedded Workflow tab must render live pipeline data with exactly the
// DOM ids shipped in index.html: unknown ids fail instead of being stubbed.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class El {
  constructor(tag) { this.tag = tag; this.children = []; this._text = ''; this.dataset = {}; this.listeners = {}; this.classList = { add() {}, remove() {}, toggle() {} }; this.hidden = false; }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); if (v === '') this.children = []; }
  set innerHTML(v) { this.children = []; }
  setAttribute() {}
  append(...c) { this.children.push(...c); }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  get text() { return [this._text, ...this.children.map(c => c.text)].join(' '); }
}
const html = fs.readFileSync('web/index.html', 'utf8');
const panel = html.slice(html.indexOf('id="workflow-live"'), html.indexOf('id="revision-progress"'));
const ids = [...panel.matchAll(/id="([a-z-]+)"/g)].map(m => m[1]);
for (const need of ['graph', 'inspector', 'play', 'rail-stats', 'scrub', 'src-banner', 'tick', 'zin', 'zout', 'zreset']) {
  assert.ok(ids.includes(need), `Workflow panel ships #${need}`);
}
const LIVE = {
  status: 'completed', model: 'meta/muse-spark-1.3-contributor',
  pipeline: {
    status: 'limited', elapsed_seconds: 1, product_hypothesis: 'h', limitations: [], product_model: {},
    steps: [], tasks: [], coverage_proof: { by_status: { supported: 1 }, by_origin: { scope: 1 }, by_mode: {} },
    ledger: { claims: [{ id: 'H1', origin: 'scope', statement: 's', scope: { components: ['U1'], nets: [], pins: [] }, evidence: [], assumptions: [], agent_sureness: 50, status: 'supported', verification: 'v', open_questions: [] }], events: [] },
    replay: [{ seq: 1, kind: 'hypothesized', actor: 'scope', title: 't', detail: 'd' }]
  },
  result: { actions: [], questions: [], summary: 's', limitations: [] }, usage: {}, request_usage: {}
};
(async () => {
  const registry = {};
  for (const id of ids) registry[id] = new El(id);
  const context = vm.createContext({
    window: {}, location: { search: '' }, matchMedia: () => ({ matches: false }),
    requestAnimationFrame: () => {}, setInterval: () => 0,
    URLSearchParams, document: {
      createElementNS: (ns, tag) => new El(tag),
      getElementById: id => {
        assert.ok(registry[id], `renderer needs #${id}, missing from embedded panel`);
        return registry[id];
      },
      activeElement: null, hidden: false
    },
    fetch: async () => ({ json: async () => JSON.parse(JSON.stringify(LIVE)) })
  });
  vm.runInContext(fs.readFileSync('web/command.js', 'utf8'), context);
  await new Promise(r => setTimeout(r, 150));
  assert.match(registry['src-banner'].textContent, /Live/, 'embedded view labels live data');
  assert.ok(registry['graph'].children.length > 0, 'embedded graph renders nodes');
  assert.equal(typeof context.window.refreshCommandCenter, 'function', 'refresh hook exposed');
  console.log('Workflow embedded render: passed');
})().catch(e => { console.error(e); process.exit(1); });
