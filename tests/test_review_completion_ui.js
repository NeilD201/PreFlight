const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];}
 append(...items){this.children.push(...items);}
 replaceChildren(){this.children=[];}
 addEventListener(){}
}
const box=new Element('div');let retries=0;
const ai={status:'completed',coverage:{validated_actions:2,requested_actions:3},
 rejected_items:[{group_id:'R001',message:'Unverified quotation.'}],result:{summary:'Saved review',limitations:[]}};
const ctx=vm.createContext({app:{project:{ai_review:ai},snapshot:{},config:{key_configured:true}},
 $:()=>box,node:(tag,cls,text)=>new Element(tag,text),badge:text=>new Element('span',text),list(){},renderCircuitAudit(){},renderInvestigation(){},
 aiRetryButton(){retries++;return new Element('button','Retry AI review');},openSettings(){},aiFailureHint(){return '';}});
const source=fs.readFileSync('web/app.js','utf8');
vm.runInContext(source.slice(source.indexOf('function renderAI()'),source.indexOf('function aiFailureHint(')),ctx);
vm.runInContext('renderAI()',ctx);
const flatten=e=>[e,...e.children.flatMap(flatten)];
assert.equal(retries,0,'Completed partial reviews must not prompt a full retry');
assert(flatten(box).some(e=>e.textContent.includes('remain unresolved')));
assert(flatten(box).some(e=>e.tag==='details'),'Unresolved diagnostics stay available');
ai.status='failed';ai.message='Provider timed out';vm.runInContext('renderAI()',ctx);
assert.equal(retries,1,'A failed request still offers retry');
ctx.app.snapshot.job={kind:'review',status:'running'};vm.runInContext('renderAI()',ctx);
assert.equal(retries,1,'An active review must not offer a second request');
console.log('Completed, partial, failed and running review states passed');
