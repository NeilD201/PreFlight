const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
// boardSites must resolve named pads without schematic pins (tab, MP) on
// copper, and fall back to the component footprint instead of an empty view.
function stubElement() { return { append() {}, }; }
const context = vm.createContext({
  node: () => stubElement(),
  app: { snapshot: { report_id: 'r' } },
  overviewUI: {},
  schematicTargets: () => [],
});
vm.runInContext(fs.readFileSync('web/locations.js', 'utf8'), context);
const boardSites = vm.runInContext('boardSites', context);
const visual = {
  item_locations: {
    'uuid-tab4': { uuid: 'uuid-tab4', point: [10, 20], kind: 'pad', label: 'U3 pad 4', layers: ['F.Cu'] },
    'uuid-u9a': { uuid: 'uuid-u9a', point: [30, 40], kind: 'pad', label: 'U9 pad 1', layers: ['F.Cu'] },
  },
  pad_locations: { U3: { 4: ['uuid-tab4'] }, U9: { 1: ['uuid-u9a'] } },
};
const project = { workspace: { board_visual: visual } };
// 1. Named tab pad resolves precisely.
let sites = boardSites({ id: 'R1', components: ['U3'], chain_targets: [{ reference: 'U3', pin: '4' }], issue_ids: [] }, project);
assert.equal(sites.length, 1);
assert.equal(sites[0].key, 'U3:4');
assert.equal(sites[0].basis, 'Pad location');
// 2. Component with no pin targets falls back to its footprint.
sites = boardSites({ id: 'R2', components: ['U9'], issue_ids: [] }, project);
assert.equal(sites.length, 1);
assert.equal(sites[0].key, 'U9');
assert.equal(sites[0].basis, 'Component footprint');
// 3. Unknown component still yields nothing, not a fabricated marker.
sites = boardSites({ id: 'R3', components: ['UXX'], issue_ids: [] }, project);
assert.equal(sites.length, 0);
console.log('boardSites fallback: all passed');
