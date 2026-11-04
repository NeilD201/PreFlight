// Bundled demo projects: the setup screen offers both judges' projects with
// no upload, and opening one links a disposable server-side working copy.
// Opening a demo must clear any previously loaded report, navigate to the
// workspace, refresh, and leave the setup form open on the run control.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
function makeElement(id) {
  const classList = {add() {}, remove() {}, toggle() {}, contains: () => false};
  function take(it) { if (!it || typeof it !== 'object') return;
    const op = it._parent;
    if (op && op.children) { const j = op.children.indexOf(it); if (j >= 0) op.children.splice(j, 1); }
    it._parent = el; }
  function link(items) { for (const it of items) take(it); return items; }
  const el = {id, tag: id, className: '', hidden: false, value: '', textContent: '', open: false,
    children: [], disabled: false, listeners: {}, qs: {}, classList, style: {}, dataset: {},
    append(...items) { this.children.push(...link(items)); },
    prepend(...items) { this.children.unshift(...link(items)); },
    replaceChildren(...items) { this.children = link(items); },
    before(...items) { const p = this._parent; const i = p ? p.children.indexOf(this) : -1;
      if (i < 0) return;
      for (const it of items) { if (!it || typeof it !== 'object') continue;
        const op = it._parent;
        if (op && op.children) { const j = op.children.indexOf(it); if (j >= 0) op.children.splice(j, 1); }
        it._parent = p; }
      p.children.splice(i, 0, ...items.filter(it => it && typeof it === 'object')); },
    insertBefore(node, ref) { const i = ref ? this.children.indexOf(ref) : -1;
      take(node);
      if (i < 0) this.children.push(node); else this.children.splice(i, 0, node); return node; },
    setAttribute() {}, removeAttribute() {}, focus() {}, click() {},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    querySelector(sel) { if (!this.qs[sel]) this.qs[sel] = makeElement(id + '-q:' + sel); return this.qs[sel]; },
    querySelectorAll() { return []; }};
  return el;
}
function descendants(root) { return [root, ...root.children.flatMap(descendants)]; }
const elements = new Map();
const errors = [];
const calls = [];
const context = vm.createContext({console, errors, calls,
  document: {getElementById: id => { if (!elements.has(id)) elements.set(id, makeElement(id)); return elements.get(id); },
    createElement: tag => makeElement(tag),
    createElementNS: (ns, tag) => makeElement(tag),
    createTextNode: text => ({textContent: String(text), children: []}),
    body: makeElement('body'),
    querySelector: sel => makeElement('qs:' + sel), querySelectorAll: () => []},
  window: {}, localStorage: {getItem: () => null, setItem: () => {}},
  location: {reload() {}}, confirm: () => true,
  matchMedia: () => ({matches: false, addEventListener() {}}),
  requestAnimationFrame: () => 0, cancelAnimationFrame() {},
  setInterval: () => 0, clearInterval() {}, setTimeout: () => 0, clearTimeout() {},
  URLSearchParams, URL});
// Same classic-script order as web/index.html.
for (const f of ['workflow.js', 'command.js', 'design.js', 'file_fixes.js', 'fix_design.js', 'firmware.js', 'manufacturing.js', 'release.js', 'locations.js', 'overview.js', 'product-context.js', 'editor-connections.js', 'usage.js', 'app.js']) {
  vm.runInContext(fs.readFileSync('web/' + f, 'utf8').replace('initialize().catch(showError);', ''), context, {filename: f});
}
vm.runInContext(`
  app.busy=false; app.adding=false; app.config={};
  workflow.comparison=null; workflow.revisionBase=null; workflow.inputReport=null;
  maybeReviewSavedProject=()=>false; maybeAskProductQuestions=()=>false; maybeShowReviewAttention=()=>{};
  renderUsage=()=>{};
  showError=(e)=>{errors.push(String((e&&e.message)||e));};
  api=async(path,data)=>{calls.push({path,data});return {};};
`, context);
function resetApiCalls() { calls.length = 0; errors.length = 0; }
(async () => {
  const box = vm.runInContext('node("div")', context);
  await vm.runInContext('loadDemoProjects({tag:"div",children:[],append(...i){this.children.push(...i);}})', context);
  // With the stub api returning {}, no demos render and nothing throws.
  const demoBox = await vm.runInContext(`(async()=>{
    const b=node('div');b.id='demo-projects';
    api=async(path,data)=>{calls.push({path,data});
      if(path==='/api/demo-projects')return {demos:[
        {name:'ModularKeyboard wrong',directory:'/repo/demo-projects/ModularKeyboard wrong',files:['falsch.kicad_pro','falsch.kicad_pcb','falsch.kicad_sch']},
        {name:'ModularKeyboard correct',directory:'/repo/demo-projects/ModularKeyboard correct',files:['c.kicad_pro','c.kicad_pcb','c.kicad_sch']}]};
      if(path==='/api/demo-open')return {path:'/work/ModularKeyboard wrong',name:data.name,schematic:'falsch.kicad_sch',pcb:'falsch.kicad_pcb',sheets:['falsch.kicad_sch'],files:{}};
      if(path==='/api/state')return {revision:0,report_id:null,job:null};
      if(path==='/api/config')return {csrf_token:'t',linked_files:{path:'/work/ModularKeyboard wrong',name:'ModularKeyboard wrong'}};
      if(path==='/api/project')return null;
      return {};};
    await loadDemoProjects(b);return b;})()`, context);
  const buttons = descendants(demoBox).filter(e => e.tag === 'button');
  assert.equal(buttons.length, 2, 'both bundled demos render as buttons');
  assert.ok(buttons[0].textContent.includes('ModularKeyboard Wrong'), 'faulty demo shows the polished display name');
  assert.ok(buttons[1].textContent.includes('ModularKeyboard Correct'), 'correct demo shows the polished display name');
  await buttons[0].listeners.click();
  const opened = calls.find(c => c.path === '/api/demo-open');
  assert.ok(opened, 'opening a demo uses the working-copy endpoint');
  assert.equal(opened.data.name, 'ModularKeyboard wrong', 'demo name is sent, never a client path');
  assert.ok(!calls.some(c => c.path === 'tryAutoLink'), 'no direct folder linking for demos');
  const linkedPath = vm.runInContext(`(app.config.linked_files||{}).path`, context);
  assert.equal(linkedPath, '/work/ModularKeyboard wrong', 'linked working copy is stored in app config');
  assert.equal(elements.get('project-path').value, '/work/ModularKeyboard wrong', 'setup form shows the connected copy');

  // Workspace hero with files connected but no check run yet: shows the
  // connected board, never the generic "start with a project" screen. The
  // setup form must be open so the run control the hero names is visible —
  // even when an earlier results view left the disclosure collapsed.
  elements.get('setup-disclosure').open = false;
  vm.runInContext(`app.project=null;app.snapshot={};renderProject()`, context);
  assert.equal(elements.get('project-title').textContent, 'ModularKeyboard Wrong', 'hero names the connected demo');
  assert.equal(vm.runInContext(`displayProjectName('falsch')`, context), 'ModularKeyboard Wrong', 'saved falsch reports display under the demo name');
  assert.equal(vm.runInContext(`displayProjectName('Some Other Board')`, context), 'Some Other Board', 'unrelated projects keep their names');
  assert.ok(elements.get('project-subtitle').textContent.includes('Connected: /work/ModularKeyboard wrong'), 'hero confirms the connection');
  assert.equal(elements.get('empty-state').hidden, true, 'connect onboarding hides once files are connected');
  assert.equal(elements.get('setup-disclosure').open, true, 'setup form opens when no report is loaded');
  assert.equal(elements.get('results').hidden, true, 'no results panel before the first check');

  // The hero must name the actual run control. The button in the setup form
  // is labelled in web/index.html; the subtitle has to use those words.
  const runLabel = fs.readFileSync('web/index.html', 'utf8').match(/id="run-button"[^>]*>([^<]*)/)[1];
  assert.ok(runLabel.length > 3, 'run button has a real label');
  assert.ok(elements.get('project-subtitle').textContent.includes(runLabel),
    `hero names the real run control (${runLabel}), not a missing "Run the check" button`);

  // initializeOverview files the setup sections under results (Project tab).
  // With no loaded report they must be pulled back into the visible
  // workspace ahead of the job panel — opening them is not enough when
  // their container is hidden.
  await vm.runInContext(`(async()=>{
    const ws=document.getElementById('workspace-view');
    ws.replaceChildren(document.getElementById('page-heading'),
      document.getElementById('source-panel'),document.getElementById('job-panel'),
      document.getElementById('empty-state'),document.getElementById('results'));
    initializeSimpleSetup();initializeOverview();
    app.project=null;app.snapshot={};renderProject();
  })()`, context);
  assert.ok(!descendants(elements.get('results')).some(e => e.id === 'setup-disclosure'), 'setup form is not stranded inside hidden results');
  assert.ok(!descendants(elements.get('results')).some(e => e.id === 'source-panel'), 'advanced setup is not stranded inside hidden results');
  const wsKids = elements.get('workspace-view').children.map(e => e.id);
  assert.ok(wsKids.indexOf('setup-disclosure') !== -1 && wsKids.indexOf('setup-disclosure') < wsKids.indexOf('job-panel'), 'setup form sits in the visible workspace ahead of the job panel');

  // Nothing connected and nothing checked: the generic onboarding stays.
  vm.runInContext(`app.config={};renderProject()`, context);
  assert.equal(elements.get('project-title').textContent, 'Review a KiCad design', 'generic hero without a connection');
  assert.equal(elements.get('empty-state').hidden, false, 'generic onboarding shows only when truly empty');

  // A click while a check runs must explain the wait instead of dying silent.
  resetApiCalls();
  vm.runInContext('app.busy=true', context);
  await buttons[0].listeners.click();
  assert.ok(errors.some(m => /already running/.test(m)), 'busy demo click explains the wait');
  assert.ok(!calls.some(c => c.path === '/api/demo-open'), 'busy demo click sends no open request');
  vm.runInContext('app.busy=false', context);

  // Opening a demo clears a previously loaded report, navigates to the
  // workspace and refreshes, so stale results never linger under new files.
  resetApiCalls();
  await vm.runInContext(`(async()=>{
    app.project={stale:'old results'}; app.view='history'; app.revision=7;
    app.config={csrf_token:'t',linked_files:null};
    api=async(path,data)=>{calls.push({path,data});
      if(path==='/api/demo-open')return {path:'/work/ModularKeyboard correct',name:data.name,schematic:'c.kicad_sch',pcb:'c.kicad_pcb',sheets:['c.kicad_sch'],files:{}};
      if(path==='/api/state')return {revision:7,report_id:null,job:null};
      if(path==='/api/config')return {csrf_token:'t',linked_files:{path:'/work/ModularKeyboard correct',name:'ModularKeyboard correct'}};
      if(path==='/api/project')return null;
      return {};};
  })()`, context);
  await buttons[1].listeners.click();
  assert.equal(vm.runInContext('app.project', context), null, 'previous report is cleared on demo switch');
  assert.equal(vm.runInContext('app.view', context), 'workspace', 'demo open lands on the workspace');
  assert.ok(calls.some(c => c.path === '/api/state'), 'demo open refreshes after linking');
  assert.ok(calls.some(c => c.path === '/api/project'), 'demo open refetches the (now empty) project');
  assert.ok(elements.get('project-subtitle').textContent.includes('Connected: /work/ModularKeyboard correct'), 'hero follows the newly opened demo');
  assert.equal(errors.length, 0, 'clean demo switch raises no errors, got: ' + errors.join(' | '));

  // + New project flow: opening a demo after addProject must leave the
  // add-project state and render, or the workspace stays blank because
  // renderProject returns early on app.adding.
  resetApiCalls();
  vm.runInContext('app.adding=true', context);
  await buttons[1].listeners.click();
  assert.equal(vm.runInContext('app.adding', context), false, 'setup-section demo open leaves the add-project flow');
  assert.equal(vm.runInContext('app.view', context), 'workspace', 'demo open lands on the workspace');
  assert.ok(calls.some(c => c.path === '/api/state'), 'demo open refreshes after linking');
  assert.equal(errors.length, 0, 'demo open after + New project raises no errors, got: ' + errors.join(' | '));

  // History card path: same add-project trap on the Projects entry point.
  resetApiCalls();
  await vm.runInContext(`(async()=>{
    api=async(path,data)=>{calls.push({path,data});
      if(String(path).startsWith('/api/library'))return {projects:[],grouped:true,total:0,limit:30,offset:0,items:[]};
      if(path==='/api/demo-projects')return {demos:[{name:'ModularKeyboard wrong',directory:'/repo/wrong',files:['falsch.kicad_pro']}]};
      if(path==='/api/state')return {revision:7,report_id:null,job:null};
      if(path==='/api/config')return {csrf_token:'t',linked_files:{path:'/work/ModularKeyboard wrong',name:'ModularKeyboard wrong'}};
      if(path==='/api/demo-open')return {path:'/work/ModularKeyboard wrong',name:'ModularKeyboard wrong',schematic:'falsch.kicad_sch',pcb:'falsch.kicad_pcb',sheets:['falsch.kicad_sch'],files:{}};
      if(path==='/api/project')return null;
      return {};};
    await loadLibrary();
  })()`, context);
  const openBoard = descendants(elements.get('history-list')).filter(e => e.tag === 'button' && (e.textContent || '').includes('Open board'))[0];
  assert.ok(openBoard, 'history demo card keeps its open control');
  vm.runInContext('app.adding=true', context);
  await openBoard.listeners.click();
  assert.equal(vm.runInContext('app.adding', context), false, 'history demo open leaves the add-project flow');
  assert.equal(vm.runInContext('app.view', context), 'workspace', 'history demo open lands on the workspace');
  assert.equal(errors.length, 0, 'history demo open raises no errors, got: ' + errors.join(' | '));

  // After a board check the library card shows the saved preview; demos
  // without a run keep the honest placeholder.
  resetApiCalls();
  await vm.runInContext(`(async()=>{
    api=async(path,data)=>{calls.push({path,data});
      if(String(path).startsWith('/api/library'))return {projects:[{name:'ModularKeyboard wrong',runs:1}],grouped:true,total:1,limit:30,offset:0,items:[
        {id:'R1',project:'ModularKeyboard wrong',name:'ModularKeyboard wrong',created:'2026-01-01T00:00:00',components:5,ai_status:'completed',has_preview:true}]};
      if(path==='/api/demo-projects')return {demos:[
        {name:'ModularKeyboard wrong',directory:'/repo/wrong',files:['falsch.kicad_pro']},
        {name:'Unreviewed demo',directory:'/repo/other',files:['o.kicad_pro']}]};
      return {};};
    await loadLibrary();
  })()`, context);
  const historyCards = descendants(elements.get('history-list'));
  // The run card uses 'saved PCB preview'; only a demo card uses 'saved board
  // preview', so the alt text proves the demo card itself gained the image.
  const demoPreview = historyCards.filter(e => e.tag === 'img' && String(e.alt || '').includes('saved board preview'));
  assert.equal(demoPreview.length, 1, 'exactly the reviewed demo gains a preview');
  assert.ok(demoPreview[0].src.includes('/reports/R1/board-front.svg'), 'preview points at the saved report render');
  assert.ok(!historyCards.some(e => e.tag === 'img' && String(e.alt || '').includes('Unreviewed demo')), 'demos without a run gain no preview image');
  const placeholders = historyCards.filter(e => e.tag === 'span' && e.className === 'preview-unavailable' && !e.hidden);
  assert.ok(placeholders.length >= 1, 'demos without a run keep the honest placeholder');

  console.log('demo projects UI: passed');
})().catch(error => { console.error(error); process.exit(1); });
