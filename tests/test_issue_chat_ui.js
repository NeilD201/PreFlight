const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
 constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.listeners={};}
 append(...x){this.children.push(...x);}addEventListener(k,f){this.listeners[k]=f;}
 focus(){}scrollIntoView(){}
}
const project={workspace:{design_context:{revision:1},conversation_history:[
 {channel:'general',report_id:'r1',question:'General question',answer:'General answer'},
 {channel:'issue:R001',report_id:'r1',question:'First issue question',answer:'First answer',status:'ai'},
 {channel:'issue:R002',report_id:'r1',question:'Second issue question',answer:'Second answer'},
 {channel:'issue:R001',report_id:'old',question:'Old revision question',answer:'Old reply'}]},
 review:{groups:[{id:'R001',key:'one',title:'First issue',components:['U1']},{id:'R002',key:'two',title:'Second issue',components:['U2']}]},
 review_sections:{assignments:{R001:{section:'connections',attention:true},R002:{section:'manufacturing',attention:true}}}};
const calls=[];
const ctx=vm.createContext({console,app:{snapshot:{report_id:'r1'},project},node:(tag,cls,text)=>new Element(tag,text),
 navigate:x=>calls.push(['navigate',x]),selectTab:x=>calls.push(['tab',x]),openOverviewIssue:x=>calls.push(['issue',x]),
 $:()=>new Element('div'),issueTitle:g=>g.title,findingLabel:()=> 'Needs review',fileFixEntry:()=>null});
vm.runInContext(fs.readFileSync('web/design.js','utf8'),ctx);
vm.runInContext('renderStepChat=()=>{};api=()=>{throw Error("Opening chat must not call AI")};',ctx);
const src=fs.readFileSync('web/release.js','utf8');
vm.runInContext(src.slice(src.indexOf('function releaseIssueQueue('),src.indexOf('function renderRelease(')),ctx);
const all=e=>[e,...e.children.flatMap(all)];
const queue=vm.runInContext('releaseIssueQueue(app.project)',ctx);
all(queue).filter(e=>e.text==='Issue chat')[1].listeners.click();
assert.deepEqual(calls,[['navigate','workspace'],['tab','manufacturing'],['issue','R002']]);
assert.equal(vm.runInContext('designUI.chatGroup',ctx),'R002');
assert.equal(vm.runInContext("savedIssueMessages('R001').length",ctx),2);
assert.equal(vm.runInContext("savedIssueMessages('R001')[0].text",ctx),'First issue question');
assert.equal(vm.runInContext("chatConversationKey('R001',0)===chatConversationKey('R001',1)",ctx),true,'Steps share an issue conversation');
assert.equal(vm.runInContext("chatConversationKey('R001')===chatConversationKey('R002')",ctx),false);
console.log('Issue chat: correct navigation, no automatic spending, isolated saved history and shared steps passed');
// Saved decisions have an explicit result, independent of the model's prose.
const decisionBox=vm.runInContext(`(()=>{const box=node('div');appendChatDecision(box,{role:'assistant',decision_update:{status:'saved',message:'Saved: Use ABC123 for U42.',next_action:'No change needed.',files_changed:false}},null);return box;})()`,ctx);
assert.ok(all(decisionBox).some(e=>e.text==='Saved: Use ABC123 for U42.'));
assert.ok(all(decisionBox).some(e=>e.text==='No change needed.'));
assert.ok(all(decisionBox).some(e=>e.text==='Project context saved; KiCad files unchanged.'));
const proposalBox=vm.runInContext(`(()=>{const box=node('div');appendChatDecision(box,{role:'assistant',proposals:[{kind:'part',label:'Use ABC123 for U42'}]},value=>{confirmed=value});return box;})()`,ctx);
all(proposalBox).find(e=>e.tag==='button').listeners.click();
assert.equal(vm.runInContext('confirmed',ctx),'Confirm: Use ABC123 for U42');
