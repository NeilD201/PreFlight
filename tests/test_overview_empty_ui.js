const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class Element {
  constructor(tag, text = '') { this.tag = tag; this.text = text; this.children = []; this.listeners = {}; this.attrs = {}; this.classes = new Set(); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  setAttribute(k, v) { this.attrs[k] = v; }
  get classList() { return { add: (...c) => c.forEach(x => this.classes.add(x)) }; }
}
function all(e) { return [e, ...e.children.flatMap(all)]; }
function texts(e) { return all(e).map(n => n.text); }
function clicks(e) { return all(e).filter(n => n.listeners.click); }
let chatOpened = 0;
function project(over = {}) {
  return Object.assign({
    review: { groups: [], coverage_incomplete: false },
    review_sections: { assignments: {}, coverage: {} },
    ai_review: { status: 'completed' },
    action_plan: { actions: [] },
    workspace: {}, selected_files: {}, issues: [],
  }, over);
}
const context = vm.createContext({
  node: (tag, cls, text = '') => { const e = new Element(tag, text); if (cls) cls.split(' ').forEach(c => e.classes.add(c)); return e; },
  $: () => new Element('div'),
  api: () => new Promise(() => {}),
  showError() {}, openChatHome() { chatOpened++; },
  selectTab() {}, navigate() {},
  app: { busy: false, tab: 'overview', snapshot: { report_id: 'r1' }, project: project() },
});
vm.runInContext(fs.readFileSync('web/overview.js', 'utf8'), context);
vm.runInContext(`this.__state = {reviewCompletionState, renderSectionEmpty, overviewUI};`, context);
const { reviewCompletionState, renderSectionEmpty, overviewUI } = context.__state;

function renderFor(proj, section = 'connections') {
  context.app.busy = !!proj.busy; context.app.project = proj.project;
  overviewUI.section = section; chatOpened = 0;
  const detail = new Element('article');
  vm.runInContext('renderSectionEmpty(this.__detail)', Object.assign(context, { __detail: detail }));
  return detail;
}
const clean = () => ({ project: project() });

// 1. Clean completed review -> success panel with working Ask Vector.
let detail = renderFor(clean());
let flat = texts(detail).join(' ');
assert.ok(flat.includes('No PCB errors detected!'), 'success heading');
assert.ok(flat.includes('PreFlight found no issues in the completed checks. Have questions or want to double-check something? Ask Vector Chat.'), 'success subtitle');
assert.ok(!flat.includes('needed for this view'), 'no global CLI warning on success');
const ask = clicks(detail).find(b => b.text === 'Ask Vector');
assert.ok(ask, 'Ask Vector action exists');
ask.listeners.click();
assert.equal(chatOpened, 1, 'Ask Vector opens existing Vector Chat with current project');
// Board section gets the same success state.
detail = renderFor(clean(), 'board');
assert.ok(texts(detail).join(' ').includes('No PCB errors detected!'), 'board success');

// 2. AI failed -> honest gaps, never the error-free claim.
detail = renderFor({ project: project({ ai_review: { status: 'failed', message: 'timeout' } }) });
flat = texts(detail).join(' ');
assert.ok(flat.includes('No findings, but some checks are incomplete'), 'gaps heading');
assert.ok(!flat.includes('No PCB errors detected!'), 'no success claim when Vector failed');
assert.ok(flat.includes('The Vector review failed for this version.'), 'scoped failure reason');

// 3. Skipped deterministic checks -> gaps, not success.
detail = renderFor({ project: project({ review: { groups: [], coverage_incomplete: true } }) });
flat = texts(detail).join(' ');
assert.ok(flat.includes('No findings, but some checks are incomplete'), 'coverage gaps heading');
assert.ok(flat.includes('Some checks were skipped or could not be verified'), 'coverage reason');
assert.ok(!flat.includes('No PCB errors detected!'), 'no success claim when checks skipped');

// 4. Running review -> progress, no findings claim at all.
detail = renderFor({ busy: true, project: project({ ai_review: { status: 'running' } }) });
flat = texts(detail).join(' ');
assert.ok(flat.includes('Review in progress'), 'running heading');
assert.ok(!flat.includes('No PCB errors detected!') && !flat.includes('No findings'), 'no verdict while running');

// 5. Findings elsewhere -> section keeps its per-section copy, not the success panel.
detail = renderFor({ project: project({ review: { groups: [{ id: 'R001', components: ['U1'], title: 't', why: 'w' }] }, review_sections: { assignments: { R001: { attention: true, section: 'board' } }, coverage: {} } }) });
flat = texts(detail).join(' ');
assert.ok(flat.includes('No wiring correction identified'), 'per-section copy preserved');
assert.ok(!flat.includes('No PCB errors detected!'), 'no success panel when other findings exist');

console.log('Overview empty states: success, gaps, running, per-section and Ask Vector wiring passed');
