const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const ctx=vm.createContext({app:{project:{explanation_profile:{mode:'plain'}}}});
vm.runInContext(fs.readFileSync('web/overview.js','utf8'),ctx);
const explain=text=>vm.runInContext(`explainIssueText(${JSON.stringify(text)})`,ctx);
assert.match(explain('Connect U8 exposed pad to GND.'),/exposed pad \(metal pad underneath the chip\)/);
assert.match(explain('U2 needs a pull-up resistor.'),/holds a signal high/);
assert.equal(explain('Keep "clearance" and [via] and Net-(via-Pad1) unchanged.'),'Keep "clearance" and [via] and Net-(via-Pad1) unchanged.');
assert.equal(explain('Check clearance (gap between copper features).'),'Check clearance (gap between copper features).');
const text=explain('U3 via is 0.25 mm; clearance is 0.15 mm.');
assert.match(text,/U3/);assert.match(text,/0.25 mm/);assert.match(text,/0.15 mm/);
vm.runInContext("app.project.explanation_profile.mode='technical'",ctx);
assert.equal(explain('Check clearance and via.'),'Check clearance and via.');
console.log('Adaptive issue language: terms explained, exact references and quoted evidence preserved.');
// Saved findings change presentation without erasing the original conclusion.
vm.runInContext("app.project.explanation_profile.mode='plain'",ctx);
ctx.example={summary:'Original technical explanation, 3.3 V.',pin_checks:[{reference:'U42',pin:'8',decision:'needs_context'}],disposition:'investigate'};
assert.match(vm.runInContext("issueObservation({},example)",ctx),/answer is still needed for U42/);
assert.equal(ctx.example.summary,'Original technical explanation, 3.3 V.');
vm.runInContext("app.project.explanation_profile.mode='technical'",ctx);
assert.equal(vm.runInContext("issueObservation({},example)",ctx),ctx.example.summary);
// Live feedback re-renders the selected issue once; repeated responses do not.
let refreshes=0;
ctx.$=()=>({});ctx.renderOverviewIssues=()=>refreshes++;ctx.app.tab='connections';
vm.runInContext("applyExplanationProfile({mode:'plain',detail:'brief'});applyExplanationProfile({mode:'plain',detail:'brief'})",ctx);
assert.equal(refreshes,1);
// Explain respects the profile instead of forcing every user into beginner prose.
vm.runInContext(fs.readFileSync('web/design.js','utf8'),ctx);
vm.runInContext("app.project.explanation_profile={mode:'technical',detail:'brief'}",ctx);
assert.match(vm.runInContext('stepExplanationRequest()',ctx),/precise engineering terms/);
vm.runInContext("app.project.explanation_profile={mode:'plain',detail:'detailed'}",ctx);
assert.match(vm.runInContext('stepExplanationRequest()',ctx),/everyday language/);
assert.match(vm.runInContext('stepExplanationRequest()',ctx),/step by step/);
vm.runInContext(fs.readFileSync('web/release.js','utf8'),ctx);
vm.runInContext("app.project.explanation_profile={mode:'plain',experience_level:'technical'}",ctx);
assert.equal(vm.runInContext('engineeringExperience()',ctx),true);
console.log('Experience, wording, answer length and immediate feedback rendering passed.');
