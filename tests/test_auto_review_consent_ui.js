const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class Element {
  constructor(tag, text = '') { this.tag = tag; this.text = text; this.children = []; this.listeners = {}; this.open = false; this.removed = false; }
  append(...items) { this.children.push(...items); }
  setAttribute() {}
  addEventListener(type, fn) { this.listeners[type] = fn; }
  querySelector(sel) { return all(this).find(e => e.tag === sel) || null; }
  showModal() { this.open = true; document.openDialogs.push(this); }
  close() { this.open = false; const i = document.openDialogs.indexOf(this); if (i >= 0)document.openDialogs.splice(i, 1); if (this.listeners.close)this.listeners.close(); }
  remove() { this.removed = true; }
}
function all(e) { return [e, ...e.children.flatMap(all)]; }
function texts(e) { return all(e).map(n => n.text).join(' '); }
function buttons(e) { return all(e).filter(n => n.tag === 'button'); }
const document = { body: new Element('body'), openDialogs: [], querySelector(sel) { return sel === 'dialog[open]' ? this.openDialogs[0] || null : null; } };
const store = {};
let calls = [], release = null, refreshed = 0;
function project(aiStatus, revision = 1) {
  return {
    ai_review: { status: aiStatus }, review_inputs: {},
    workspace: { design_context: { revision }, source_status: { status: 'current' } },
    design_context: { revision }, provenance: { archive: 'test' }, issues: [],
  };
}
const context = vm.createContext({
  node: (tag, cls, text = '') => new Element(tag, text),
  document, sessionStorage: { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } },
  $: () => new Element('div'),
  api: (path) => { calls.push(path); return new Promise(resolve => { release = resolve; }); },
  refresh: async () => { refreshed++; }, clearError() {}, setBusy() {}, showError(e) { throw e; },
  app: { busy: false, pending: false, view: 'workspace', adding: false, tab: 'overview', snapshot: { report_id: 'r1', job: null },
         config: { key_configured: true, model: 'm', keychain_loading: false }, project: project('stale') },
});
vm.runInContext(fs.readFileSync('web/design.js', 'utf8'), context);
vm.runInContext('this.__t = {maybeReviewSavedProject, retryAIReview};', context);
const t = context.__t, app = context.app;
(async () => {
  // 1. Stale review: asks once with cost stated, fires nothing by itself.
  assert.equal(t.maybeReviewSavedProject(), true);
  assert.equal(calls.length, 0, 'no paid request without a press');
  assert.equal(document.openDialogs.length, 1, 'consent dialog shown');
  let flat = texts(document.openDialogs[0]);
  assert.ok(flat.includes('Run the Vector review?'), 'explicit question: ' + flat);
  assert.ok(flat.includes('counts against your existing request limits'), 'cost stated: ' + flat);
  const run = buttons(document.openDialogs[0]).find(b => b.text === 'Run Vector review');
  const browse = buttons(document.openDialogs[0]).find(b => b.text === 'Continue browsing');
  assert.ok(run && browse, 'Run/Skip choices present');
  // 2. Explicit Run press fires exactly one paid request.
  run.listeners.click(); release({}); await new Promise(r => setTimeout(r, 20));
  assert.deepEqual(calls, ['/api/review-ai'], 'one paid request after explicit Run');
  assert.ok(refreshed >= 1, 'project refreshed after run');
  // 3. Dismissal asks never again and fires nothing, even across polls.
  calls = []; delete store['preflight-auto-review:' + JSON.stringify(['test', [], 2, 'm'])];
  app.project = project('stale', 2);
  assert.equal(t.maybeReviewSavedProject(), true);
  assert.equal(calls.length, 0);
  buttons(document.openDialogs[0]).find(b => b.text === 'Continue browsing').listeners.click();
  assert.equal(document.openDialogs.length, 0, 'dialog dismissed');
  assert.equal(t.maybeReviewSavedProject(), false, 'no re-ask after Skip');
  assert.equal(calls.length, 0, 'still no paid request');
  // 4. Failed analysis still offers an explicit retry and never auto-fires.
  app.project = project('failed', 3);
  assert.equal(t.maybeReviewSavedProject(), true);
  assert.equal(calls.length, 0, 'failed status never auto-fires');
  flat = texts(document.openDialogs[0]);
  assert.ok(flat.includes('Retry analysis') && flat.includes('Continue browsing'), 'explicit retry offered: ' + flat);
  console.log('Auto-review consent: ask-once with cost, explicit Run fires once, Skip respected, failures explicit passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
