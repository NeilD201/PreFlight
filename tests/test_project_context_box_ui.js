// Project context box: textarea with the exact placeholder, server prefill,
// save posts description + revision, and typing survives a re-render.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
function makeElement(tag) {
  return {tag, children: [], listeners: {}, disabled: false, value: '', textContent: '',
    rows: 0, maxLength: 0, placeholder: '',
    append(...i) { this.children.push(...i); },
    addEventListener(t, f) { this.listeners[t] = f; },
    setAttribute() {}, focus() { this.focused = true; },
    setSelectionRange() {}};
}
function descendants(root) { return [root, ...root.children.flatMap(descendants)]; }
const calls = [];
const context = vm.createContext({console, calls,
  document: {createElement: tag => makeElement(tag)},
  app: {snapshot: {report_id: 'r1'},
    project: {workspace: {design_context: {description: '', revision: 3}}}},
  api: async (path, data) => {
    calls.push({path, data});
    if (path === '/api/demo-projects') {
      return {demos: [{name: 'Demo', preset: 'Preset text'}]};
    }
    if (path === '/api/project-context' && !('description' in data)) {
      return {description: '', context_revision: 7};
    }
    if (path === '/api/project-context') {
      return {description: data.description, context_revision: 8};
    }
    return {};
  },
  node: (tag, cls, text) => {
    const e = makeElement(tag);
    if (cls) e.cls = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }});
vm.runInContext(fs.readFileSync('web/product-context.js', 'utf8'), context);
(async () => {
  const box = await vm.runInContext(`(async()=>{
    const c=node('div');await renderProjectContextBox(c,'r1');return c;})()`, context);
  const areas = descendants(box).filter(e => e.tag === 'textarea');
  assert.equal(areas.length, 1, 'one context textarea renders');
  assert.match(areas[0].placeholder,
    /Describe what your project does, how its controls should behave/,
    'the exact judge-facing placeholder is used');
  assert.equal(areas[0].value, '', 'empty server text leaves a blank box');
  const preset = descendants(box).find(e => e.tag === 'button' && /preset/i.test(e.textContent || ''));
  assert.ok(preset, 'a demo preset loader is offered when nothing is saved');
  await preset.listeners.click();
  assert.equal(areas[0].value, 'Preset text', 'preset fills the box for editing');
  const save = descendants(box).find(e => e.tag === 'button');
  assert.ok(save && save.textContent === 'Save context', 'explicit save control exists');
  areas[0].value = 'Edited context';
  areas[0].listeners.input();
  const box2 = await vm.runInContext(`(async()=>{
    const c=node('div');await renderProjectContextBox(c,'r1');return c;})()`, context);
  assert.equal(descendants(box2).filter(e => e.tag === 'textarea')[0].value,
    'Edited context', 'unsaved typing survives a re-render');
  await save.listeners.click();
  const post = calls.find(c => c.path === '/api/project-context' && 'description' in c.data);
  assert.ok(post, 'save posts the description');
  assert.equal(post.data.description, 'Edited context');
  assert.equal(post.data.context_revision, 7, 'save carries the loaded revision');
  console.log('project context box: passed');
})().catch(error => { console.error(error); process.exit(1); });
