// Counts, visible rows and labels must describe the same saved classifications.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const groups=[
  {id:'pending',priority:'review'}, {id:'accepted',priority:'review'},
  {id:'library',priority:'review'}, {id:'order',priority:'review'}
];
const assignments={
  pending:{section:'connections',attention:true,status:'context'},
  accepted:{section:'notes',attention:false,status:'accepted'},
  library:{section:'notes',attention:false,status:'reference'},
  order:{section:'manufacturing',attention:true,status:'review'}
};
const ctx=vm.createContext({app:{project:{review:{groups},review_sections:{assignments},action_plan:{actions:[]}}},console});
vm.runInContext(fs.readFileSync('web/overview.js','utf8'),ctx);
vm.runInContext(fs.readFileSync('web/file_fixes.js','utf8'),ctx);
vm.runInContext("overviewUI.section='connections';overviewUI.showAll=false",ctx);
assert.deepEqual(Array.from(vm.runInContext('overviewActions().map(x=>x.group.id)',ctx)),['pending']);
assert.equal(vm.runInContext('findingLabel(app.project.review.groups[0])',ctx),'Analysis incomplete');
assert.equal(vm.runInContext('fileFixEntry(app.project.review.groups[0])',ctx),null,'Unfinished analysis is not a repair plan');
assert.equal(vm.runInContext('findingLabel(app.project.review.groups[1],{pin_checks:[{decision:"keep"}]})',ctx),'Connections match supplied requirements');
assert.equal(vm.runInContext('findingLabel(app.project.review.groups[2])',ctx),'Reference note');
assert.equal(vm.runInContext('fileFixEntry(app.project.review.groups[1])',ctx),null);
assert.equal(vm.runInContext('fileFixEntry(app.project.review.groups[2])',ctx),null);
assert.equal(vm.runInContext('fileFixEntry(app.project.review.groups[3],{action_kind:"order_setting"})',ctx),null);
// A high raw priority must not bypass the shared attention decision.
vm.runInContext("app.project.review_sections.assignments.pending.attention=false",ctx);
assert.equal(vm.runInContext('overviewActions().length',ctx),0);
console.log('Unified review: matching filters, accepted/reference labels, and no misleading fix buttons passed');
vm.runInContext(`app.project.review.groups.push({id:'placement',priority:'review'},{id:'signal',priority:'review'});
app.project.review_sections.assignments.placement={section:'placement',attention:true};
app.project.review_sections.assignments.signal={section:'traces',attention:true};
overviewUI.section='board';`,ctx);
assert.deepEqual(Array.from(vm.runInContext('overviewActions().map(x=>x.group.id)',ctx)),['placement','signal']);
assert.equal(vm.runInContext("reviewSection('traces')",ctx),'board');
assert.equal(vm.runInContext("reviewSection('placement')",ctx),'board');
assert.equal(vm.runInContext("reviewSection('manufacturing')",ctx),'manufacturing');
console.log('Combined PCB workspace includes placement and signal findings, preserving manufacturing');
assert.deepEqual(Array.from(vm.runInContext("issueSteps(['m','','  Connect pin 1 to GND.  ',null])",ctx)),['Connect pin 1 to GND.']);
assert.deepEqual(Array.from(vm.runInContext("issueSteps('m')",ctx)),[]);
