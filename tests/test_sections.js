// Section navigation retains access to notes and can open an issue from another view.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({console});
vm.runInContext(fs.readFileSync('web/overview.js','utf8'),context);
vm.runInContext(`
  app={project:{review:{groups:[
    {id:'R1',priority:'review',components:[]},{id:'R2',priority:'fix',components:[]},
    {id:'R3',priority:'note',components:[]},{id:'R4',priority:'review',components:[]}]},
    action_plan:{actions:[{group_id:'R4',disposition:'no_change'}]},
    review_sections:{assignments:{R1:{section:'notes'},R2:{section:'connections',attention:true},R3:{section:'traces'},R4:{section:'placement'}}}}};
  renderOverview=()=>{};renderOverviewIssues=()=>{};
`,context);
assert.deepEqual(Array.from(vm.runInContext('overviewActions().map(x=>x.group.id)',context)),['R2']);
vm.runInContext("chooseReviewSection('notes')",context);
assert.deepEqual(Array.from(vm.runInContext('overviewActions().map(x=>x.group.id)',context)),['R1']);
vm.runInContext("chooseReviewSection('traces')",context);
assert.equal(vm.runInContext('overviewActions().length',context),0);
vm.runInContext("openOverviewIssue('R3')",context);
assert.equal(vm.runInContext('overviewUI.showAll',context),true);
assert.equal(vm.runInContext('overviewActions()[0].group.id',context),'R3');
vm.runInContext("openOverviewIssue('R4')",context);
assert.equal(vm.runInContext('overviewUI.section',context),'board');
assert.equal(vm.runInContext('overviewUI.focus',context),'R4');
assert(vm.runInContext("overviewActions().map(x=>x.group.id).includes('R4')",context));
vm.runInContext("chooseReviewSection('connections')",context);
assert.equal(vm.runInContext('overviewUI.showAll',context),false);
assert.equal(vm.runInContext('overviewUI.view',context),'schematic');
console.log('Section filters, notes and cross-section issue navigation: passed');

vm.runInContext(`app.project.action_plan.actions=[
  {pin_checks:[{reference:'U4',pin:'5',net:'3V3',decision:'keep'}]},
  {pin_checks:[{reference:'U4',pin:'5',net:'3V3',decision:'needs_context'}]},
  {pin_checks:[{reference:'U4',pin:'5',net:'3V3',decision:'keep'}]}
];`,context);
assert.equal(vm.runInContext('comparedPins().length',context),1);
assert.equal(vm.runInContext('comparedPins()[0].decision',context),'needs_context');
console.log('Pin comparison summary deduplicates shared findings: passed');
assert.equal(vm.runInContext(`issueTitle({title:'Via dimensions versus saved limits'}, {manufacturer_check:{status:'option'},title:'Fill and cap vias in solder pads',advice_title:'Old small-via advice'})`,context),'Fill and cap vias in solder pads');
