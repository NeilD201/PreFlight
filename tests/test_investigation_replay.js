// Coverage Proof and Investigation Replay render recorded pipeline data only.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
function makeNode(tag){
  return {tag,className:'',text:'',children:[],handlers:{},
    append(...kids){this.children.push(...kids);},
    replaceChildren(...kids){this.children=kids;},
    addEventListener(name,fn){this.handlers[name]=fn;},
    set textContent(value){this.text=value;}};
}
function esc(text){return String(text).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function serialize(node){
  const attrs=node.className?` class="${node.className}"`:'';
  const kids=node.children.map(serialize).join('');
  return `<${node.tag}${attrs}>${esc(node.text)}${kids}</${node.tag}>`;
}
const context=vm.createContext({console,document:{getElementById(){throw new Error('no page');},createElement:makeNode}});
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace('initialize().catch(showError);',''),context);
const pipeline={tasks:[],steps:[],limitations:['run caveat'],status:'limited',
  coverage_proof:{claims_total:2,by_status:{supported:1,unresolved:1},
    by_origin:{scope:2},examined_components:{items:['U1'],overflow:1},
    examined_nets:{items:[],overflow:0},
    unresolved:[{id:'H002',statement:'S <b>bold</b>',open_questions:['Q?']}],
    unresolved_overflow:0,
    verification:[{id:'H001',statement:'S1',status:'supported',final:'verified',final_reason:'All gates agree.',
      gate1:'pass',gate1_checks:[{name:'scope',result:'verified',reason:'Objects exist.'}],
      judge:'stands',judge_reason:'No error found.',reinvestigations:[{action:'evidence_added',detail:'Attached N:X.'}],
      gauge_sureness:99,open_questions:[]}],
    verification_overflow:0,gaps:{unexamined_claimable:['U9']},
    intent_questions:[{question:'What is it?'}],limitations:['proof caveat']},
  replay:[{seq:1,kind:'hypothesized',actor:'scope',title:'Hypothesis recorded',detail:'d1',claim_id:'H001',statement:'S1'},
    {seq:2,kind:'mystery',actor:'?',title:'mystery',detail:'d2',claim_id:null}]};
context.pipeline=pipeline;
vm.runInContext('var box=document.createElement("div");renderInvestigation(box,pipeline);box',context);
const html=serialize(vm.runInContext('box',context));
for(const text of ['Coverage proof','2 hypotheses tracked','supported: 1','unresolved: 1','scope: 2','U1','+1 more',
    'H002: unresolved','Q?','U9','What is it?','proof caveat','run caveat','Per-finding verification',
    'H001: verified · evidence pass · judge stands','scope: verified — Objects exist.',
    'Independent judge (stands): No error found.','evidence_added: Attached N:X.',
    'Gauge sureness: 99/100 (AI estimate, not proof)','Investigation replay',
    '1. Hypothesis recorded','H001: S1','Scope · coverage','2. mystery','Investigation limited'])assert.ok(html.includes(text),text);
assert.ok(html.includes('S &lt;b&gt;bold&lt;/b&gt;'),'statement text is escaped, never markup');
assert.ok(!html.includes('<b>bold</b>'),'no raw markup from record text');
// Component chips deep-link through the existing evidence focus.
vm.runInContext('var box2=document.createElement("div");renderInvestigation(box2,pipeline);box2',context);
const chips=[];
(function walk(node){if(node.tag==='button')chips.push(node);for(const kid of node.children)walk(kid);})(vm.runInContext('box2',context));
assert.equal(chips.length,1);
assert.equal(chips[0].text,'U1');
assert.equal(typeof chips[0].handlers.click,'function');
// Pipelines from older runs render exactly as before.
vm.runInContext('var box3=document.createElement("div");renderInvestigation(box3,{tasks:[],steps:[],limitations:[]});box3',context);
const legacy=serialize(vm.runInContext('box3',context));
assert.ok(legacy.includes('How Vector investigated this design'));
assert.ok(!legacy.includes('Coverage proof'));
assert.ok(!legacy.includes('Investigation replay'));
console.log('Coverage Proof and Investigation Replay render recorded data, escape text, and stay backward compatible: passed');
