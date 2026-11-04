const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class Element {
  constructor(tag, text = '') { this.tag = tag; this.text = text; this.children = []; this.listeners = {}; this.classes = new Set(); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  setAttribute() {}
  get classList() { return { add: (...c) => c.forEach(x => this.classes.add(x)) }; }
}
function all(e) { return [e, ...e.children.flatMap(all)]; }
function texts(e) { return all(e).map(n => n.text).join(' '); }
const descs = {
  R12: { short: 'the 10 k\u03a9 pull-down resistor (R12)', phrase: 'the 10 k\u03a9 pull-down resistor (R12) connected between U1 pin RESET and ground', endpoints: [] },
  C8: { short: 'the 100 nF capacitor (C8)', phrase: 'the 100 nF capacitor (C8) connected between +3V3 and ground', endpoints: [] },
  SW9: { short: 'the rotary encoder (SW9)', phrase: 'the rotary encoder (SW9) connected between U1 pins GPA5, GPA6, GPA7 and ground',
         endpoints: [{ net: 'Net-(U1-GPA5)', ground: false, others: [['U1', '22']] }, { net: 'GND', ground: true, others: [['U1', '11']] }] },
};
const context = vm.createContext({
  node: (tag, cls, text = '') => { const e = new Element(tag, text); if (cls) cls.split(' ').forEach(c => e.classes.add(c)); return e; },
  $: () => new Element('div'),
  api: () => new Promise(() => {}),
  showError() {}, openChatHome() {}, openIssueChat() {}, openStepChat() {}, openSharedContext() {},
  selectTab() {}, navigate() {}, renderChatHome() {}, loadIssueProof() {}, syncReviewNavigation() {},
  renderIssueViewer() {}, renderOverviewMap() {}, currentViewerGroup: () => ({}), fileFixEntry: () => null, coordinatedFixEntry: () => null,
  app: { busy: false, tab: 'overview', snapshot: { report_id: 'r1' },
         project: { review: { groups: [] }, review_sections: { assignments: {}, coverage: {} }, ai_review: { status: 'completed' },
                    action_plan: { actions: [] }, workspace: {}, selected_files: {}, issues: [], components: {},
                    component_descriptions: descs, explanation_profile: { mode: 'plain' } } },
});
vm.runInContext(fs.readFileSync('web/overview.js', 'utf8'), context);
vm.runInContext(fs.readFileSync('web/design.js', 'utf8'), context);
vm.runInContext('this.__t = {withComponentContext, issueTitle, explainIssueText, reviewInstruction, chatAnswerContent, schematicTargets};', context);
const t = context.__t;

// 1. Full phrase on first mention, short after; unknown refs untouched.
let out = t.withComponentContext('Check R12 and R12 again, plus Z99.');
assert.ok(out.includes('the 10 k\u03a9 pull-down resistor (R12) connected between U1 pin RESET and ground'), 'full phrase: ' + out);
assert.ok(out.includes('the 10 k\u03a9 pull-down resistor (R12) connected to') === false, 'no second full phrase');
assert.ok(out.includes('Z99'), 'unknown ref untouched');
// 2. Protected spans: quotes, parens, Net- identifiers, already-parenthesized refs.
out = t.withComponentContext('The "R12 net" and (R12) and Net-R12 stay, but R12 here changes.');
assert.equal((out.match(/the 10 k\u03a9 pull-down/g) || []).length, 1, 'exactly one enrichment: ' + out);
// 3. Titles use the short form and keep the reference in parentheses.
out = t.issueTitle({ id: 'R001', title: 'Footprint pads missing from the symbol: SW9', components: ['SW9'], priority: 'review' }, {});
assert.ok(out.includes('the rotary encoder (SW9)'), 'title short phrase: ' + out);
// 4. Beginner body text is enriched in both explanation modes.
out = t.explainIssueText('Check R12 for the fault.');
assert.ok(out.includes('(R12) connected between'), 'plain mode enriched: ' + out);
context.app.project.explanation_profile.mode = 'expert';
out = t.explainIssueText('Check C8 for the fault.');
assert.ok(out.includes('the 100 nF capacitor (C8)'), 'expert mode enriched: ' + out);
context.app.project.explanation_profile.mode = 'plain';
// 5. Questions inherit enrichment through reviewInstruction.
out = t.reviewInstruction('Is R12 the correct pull-down value?');
assert.ok(out.includes('(R12) connected between'), 'question enriched: ' + out);
// 6. Vector Chat answers: first mention full, citation links preserved.
const message = { text: 'Check R12, then recheck R12 and C8. See [datasheet](https://example.com/d.pdf).', citations: [] };
const para = t.chatAnswerContent(message);
const flat = texts(para);
assert.ok(flat.includes('the 10 k\u03a9 pull-down resistor (R12) connected between'), 'chat full phrase: ' + flat);
assert.ok(flat.includes('the 100 nF capacitor (C8) connected between'), 'chat second ref: ' + flat);
assert.ok(all(para).some(e => e.tag === 'a'), 'citation link node preserved');
// 7. Schematic viewer highlights the verified connected pin with the same reference.
const visual = { pins: { U1: { 22: { point: [3, 4], label: 'U1 pin 22' } } }, bounds: [0, 0, 10, 10], locations: {} };
const targets = t.schematicTargets({ id: 'R001', components: ['SW9'], issue_ids: [] }, visual, context.app.project);
assert.ok(targets.some(p => p.label.includes('connected to SW9') && p.label.includes('U1 pin 22')), 'connected pin highlighted: ' + JSON.stringify(targets));
assert.ok(!targets.some(p => /U1 pin 11/.test(p.label)), 'ground-net neighbours skipped');

console.log('Component context: phrases, titles, questions, chat and viewer highlight passed');
