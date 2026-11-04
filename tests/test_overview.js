// Test marker selection against native evidence, including mismatched ERC units.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({console});
vm.runInContext(fs.readFileSync('web/overview.js','utf8'),context);
context.failure={status:'failed',message:'This project exceeds the 300 kB AI evidence limit.',request_usage:{requests:0}};
assert.equal(vm.runInContext('reviewFailureNotice(failure).title',context),'Vector review was not sent');
assert.match(vm.runInContext('reviewFailureNotice(failure,true).message',context),/do not depend on AI/);
assert.match(vm.runInContext('reviewFailureNotice(failure).message',context),/300 kB/);
assert.equal(vm.runInContext("reviewFailureNotice({status:'failed',message:'Rate limit reached',request_usage:{requests:1}}).unsent",context),false);
vm.runInContext(`
  group={components:['U3'],issue_ids:['F0001']};
  visual={locations:{U3:[[-30.48,265.43]]},item_locations:{
    'real-pin':{point:[-15.24,265.43],reference:'U3',label:'U3 pin 7',kind:'pin',name:'GPIO1'}}};
  project={issues:[{source:'kicad_erc',items:[{uuid:'real-pin',pos:{x:-.1524,y:2.6543}}]}]};
  targets=schematicTargets(group,visual,project);
`,context);
assert.equal(vm.runInContext('targets[0].point[0]',context),-15.24);
assert.equal(vm.runInContext('targets[0].point[1]',context),265.43);
assert.equal(vm.runInContext('targets[0].kind',context),'pin');
assert.equal(vm.runInContext('targets[0].label',context),'U3 pin 7 · GPIO1');
vm.runInContext(`project.issues[0].items[0].uuid='missing';targets=schematicTargets(group,visual,project);`,context);
assert.equal(vm.runInContext('targets[0].kind',context),'symbol');
assert.equal(vm.runInContext('targets[0].point[0]',context),-30.48);
vm.runInContext(`
  project={issues:[{source:'kicad_drc',items:[
    {uuid:'front-pad',description:'Pad 1 of D1 on F.Cu',pos:{x:-18.68,y:-35}},
    {uuid:'back-pad',description:'Pad 2 of D1 on B.Cu',pos:{x:-19.32,y:-35}},
    {uuid:'front-pad',description:'Pad 1 of D1 on F.Cu',pos:{x:-18.68,y:-35}},
    {uuid:'no-location',description:'Unknown item'}]}]};
  targets=pcbTargets(group,project,'front');
`,context);
assert.equal(vm.runInContext('targets.length',context),1);
assert.equal(vm.runInContext('targets[0].point[0]',context),-18.68);
vm.runInContext(`targets=pcbTargets(group,project,'back');`,context);
assert.equal(vm.runInContext('targets.length',context),1);
assert.equal(vm.runInContext('targets[0].point[0]',context),-19.32);
assert.equal(vm.runInContext("findingLabel({priority:'note'})",context),'Reference');
console.log('Evidence marker selection: passed');

vm.runInContext(`
  chain={loads:[{reference:'U2',pin:'1'}],routes:[{steps:[{
    input_pins:[{reference:'U4',pin:'1'}],output_pins:[{reference:'U4',pin:'5'}],
    control_pins:[{reference:'U4',pin:'3'}]}]}],branches:[{
      from:{reference:'C1',pin:'1'},to:{reference:'C1',pin:'2'}}]};
  pins=routingPins(chain);
  visual={locations:{},item_locations:{},pins:{U4:{'3':{reference:'U4',pin:'3',kind:'pin',label:'U4 pin 3',point:[20.32,-30.48],name:'EN'}}}};
  targets=schematicTargets({components:['U4'],issue_ids:[],chain_targets:pins},visual,{issues:[]});
`,context);
assert.equal(vm.runInContext('pins.length',context),6);
assert.equal(vm.runInContext('targets[0].pin',context),'3');
assert.equal(vm.runInContext('targets[0].point[1]',context),-30.48);
console.log('Circuit path pin selection: passed');

vm.runInContext(`
  app={project:{circuit:{routing_chains:{chains:[{
    id:'chain',target_net:'LOGIC',loads:[{reference:'U2',pin:'1'}],branches:[],
    routes:[{steps:[{net:'BAT',pins:[{reference:'J4',pin:'2'}]},{input_net:'BAT',output_net:'LOGIC',input_pins:[{reference:'U4',pin:'1'}]}]},
      {steps:[{net:'RAW',pins:[{reference:'J1',pin:'2'}]},{input_net:'RAW',output_net:'BAT',input_pins:[{reference:'U8',pin:'1'}]},
      {input_net:'BAT',output_net:'LOGIC',input_pins:[{reference:'U4',pin:'1'}]}]}]}]}}}};
  overviewUI.chainId='chain';overviewUI.chainRoute=0;viewed=viewedRoutingChain();
`,context);
assert.deepEqual(Array.from(vm.runInContext('viewed.nets',context)),['LOGIC','BAT']);
assert(!vm.runInContext("viewed.components.includes('J1')",context));
console.log('Alternative path selection: passed');

// Filtering must leave the expensive preview and unsent question in place.
vm.runInContext(`
  function testNode(tag){return {tag,children:[],append(...items){this.children.push(...items);},setAttribute(){},addEventListener(){}};}
  node=testNode;
  issueBox={children:[],replaceChildren(){this.children=[];},append(...items){this.children.push(...items);},querySelector(){return {scrollTop:80};}};
  $=id=>id==='overview-issues'?issueBox:id==='overview-detail'?{replaceChildren(){throw new Error('Filter erased the current issue');}}:null;
  overviewActions=()=>[{group:{id:'R001',title:'First',priority:'review',components:['U1']}},{group:{id:'R002',title:'Second',priority:'review',components:['U2']}}];
  overviewUI.focus='R001';overviewUI.search='U2';
  renderOverviewIssues(true);
`,context);
assert.equal(vm.runInContext('issueBox.children.at(-1).children.length',context),1);
assert.equal(vm.runInContext('issueBox.children.at(-1).scrollTop',context),80);
console.log('Issue search preserves detail and list position: passed');
const simpleQuestion=vm.runInContext(`reviewInstruction('Is GPIO1 intended to be used as an externally driven temperature-conversion trigger?')`,context);
assert.match(simpleQuestion,/another device/);assert.match(simpleQuestion,/sending a signal/);
assert.doesNotMatch(simpleQuestion,/automatically|software|conversion trigger/,'A hardware trigger is not equivalent to software commands');
assert.equal(vm.runInContext("outcomeLabel({outcome:'verified_correction'})",context),'Verified correction ready');
assert.equal(vm.runInContext("outcomeLabel({outcome:'conditional'})",context),'Conditional correction');
assert.equal(vm.runInContext("outcomeLabel({outcome:'investigation_required'})",context),'Needs investigation');
assert.equal(vm.runInContext("outcomeLabel({outcome:'informational'})",context),'Informational');
console.log('Repair outlook labels: passed');
