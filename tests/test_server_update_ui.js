// A server restart under an open tab must surface a one-click refresh
// instead of letting the tab silently run stale interface code.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
function makeElement(id) {
  return {id, tag: id, hidden: false, value: '', textContent: '', children: [], listeners: {},
    append(...items) { this.children.push(...items); },
    prepend(...items) { this.children.unshift(...items); },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    setAttribute() {}, remove() {}, querySelector() { return null; }};
}
const elements = new Map();
const bodyEl = makeElement('body');
function all(node) { return [node, ...node.children.flatMap(all)]; }
let reloaded = false;
const context = vm.createContext({console,
  document: {getElementById: id => elements.get(id) || all(bodyEl).find(e => e.id === id) || null,
    createElement: tag => makeElement(tag),
    body: bodyEl},
  window: {}, localStorage: {getItem: () => null, setItem: () => {}},
  location: {reload() { reloaded = true; }}});
vm.runInContext(fs.readFileSync('web/app.js', 'utf8').replace('initialize().catch(showError);', ''), context);
vm.runInContext('app.serverId = "abc123";', context);
(async () => {
  const same = vm.runInContext('configIsNewServer({server_id: "abc123"})', context);
  assert.equal(same, false, 'same server id is not an update');
  const changed = vm.runInContext('configIsNewServer({server_id: "def456"})', context);
  assert.equal(changed, true, 'changed server id means the server restarted');
  assert.equal(vm.runInContext('configIsNewServer({})', context), false, 'missing id never triggers');
  assert.equal(vm.runInContext('configIsNewServer(null)', context), false, 'missing config never triggers');
  vm.runInContext('app.serverId = null', context);
  assert.equal(vm.runInContext('configIsNewServer({server_id: "def456"})', context), false, 'no baseline never triggers');

  vm.runInContext('showServerUpdateBanner()', context);
  const bars = vm.runInContext(`document.body.children.filter(e => e.id === 'server-update-banner')`, context);
  assert.equal(bars.length, 1, 'update banner is shown once');
  vm.runInContext('showServerUpdateBanner()', context);
  assert.equal(vm.runInContext(`document.body.children.filter(e => e.id === 'server-update-banner').length`, context), 1, 'banner is not duplicated');
  const bar = bars[0];
  assert.ok(bar.children.some(e => e.tag === 'span' && e.textContent.includes('Refresh')), 'banner explains the refresh');
  const go = bar.children.find(e => e.tag === 'button' && e.textContent === 'Refresh now');
  assert.ok(go, 'banner offers a one-click refresh');
  go.listeners.click();
  assert.equal(reloaded, true, 'refresh button reloads the tab');
  console.log('server update banner: passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
