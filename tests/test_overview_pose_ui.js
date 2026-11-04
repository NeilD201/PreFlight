const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
// Render-surface regression: "Analysis & pin checks" must not show a
// beginner-suppressed "Still needed:" question verbatim, while ask-worthy
// questions (R011 chip identity) and expert views keep rendering.
function load(mode){
  const context=vm.createContext({console,app:{project:{explanation_profile:{mode}}}});
  vm.runInContext(fs.readFileSync('web/overview.js','utf8'),context);
  return context;
}
const r009='How will your code set U1 interrupts - open-drain shared, mirrored, polarity, or polling only with INT unused?';
const r009b='Will your software set both interrupt outputs to open-drain mode so they can share one wire?';
const r011='Which exact chip will you solder at U1 - the I2C MCP23017-E/ML?';
const reason=t=>`Shared INTA/INTB wire requires compatible output setting. For your use: Macropad telling host. Still needed: ${t}`;
const poseFor=(beginner,expert)=>({ask_beginner:beginner,ask_expert:expert,beginner_decision:'unknown'});
(function beginnerFiltersSuppressedKeepsAskWorthy(){
  const ctx=load('plain');
  const group={id:'R009',missing_context_pose:{[r009]:poseFor(false,true),[r011]:poseFor(true,true)}};
  const out=vm.runInContext(`filterStillNeeded(${JSON.stringify(reason(r009+'; '+r011))},suppressedQuestions(${JSON.stringify(group)},null))`,ctx);
  assert.ok(!out.includes('open-drain shared'),'suppressed R009 question removed for beginners');
  assert.ok(out.includes(r011),'ask-worthy R011 chip question kept for beginners');
  assert.ok(out.includes('compatible output setting'),'analysis prose kept');
})();
(function beginnerDropsLastSegmentCleanly(){
  const ctx=load('plain');
  const group={id:'R009',missing_context_pose:{[r009b]:poseFor(false,false)}};
  const out=vm.runInContext(`filterStillNeeded(${JSON.stringify(reason(r009b))},suppressedQuestions(${JSON.stringify(group)},null))`,ctx);
  assert.ok(!out.includes('Still needed'),'empty tail removed, no dangling marker');
  assert.ok(out.includes('Macropad telling host'),'head kept');
})();
(function expertKeepsAnchoredImplementation(){
  const ctx=load('technical');
  const group={id:'R009',missing_context_pose:{[r009]:poseFor(false,true)}};
  const out=vm.runInContext(`filterStillNeeded(${JSON.stringify(reason(r009))},suppressedQuestions(${JSON.stringify(group)},null))`,ctx);
  assert.ok(out.includes(r009),'experts keep anchored implementation questions');
})();
(function unannotatedReportsBehaveAsBefore(){
  const ctx=load('plain');
  const out=vm.runInContext(`filterStillNeeded(${JSON.stringify(reason(r009))},suppressedQuestions({},null))`,ctx);
  assert.equal(out,reason(r009),'no pose data means no filtering');
})();
console.log('Analysis prose filtering: beginner suppression, ask-worthy retention, expert view, unannotated passthrough passed');
